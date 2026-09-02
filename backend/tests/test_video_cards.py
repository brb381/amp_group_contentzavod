import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import app.content.service as content_service
from app.audit.models import SecurityEvent
from app.audit.service import AuditAction
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now
from app.catalog.models import Brand, Product
from app.content.models import VideoCard
from app.creators.models import CreatorProfile, ProfileStatus
from app.legal.models import AcceptanceMethod, LegalAcceptance, LegalDocument


PASSWORD = "correct-horse-battery-staple"


def create_user(
    client,
    email: str,
    *,
    role: Role = Role.BLOGGER,
    account_status: AccountStatus = AccountStatus.ACTIVE,
    profile_status: ProfileStatus | None = ProfileStatus.APPROVED,
) -> User:
    with client.app.state.test_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            status=account_status,
            email_verified_at=utc_now(),
        )
        db.add(user)
        db.flush()
        if role == Role.BLOGGER:
            for document in db.scalars(
                select(LegalDocument).where(
                    LegalDocument.is_current.is_(True),
                    LegalDocument.requires_reacceptance.is_(True),
                )
            ):
                db.add(
                    LegalAcceptance(
                        user_id=user.id,
                        document_id=document.id,
                        method=AcceptanceMethod.REGISTRATION,
                        request_id="video-card-test-factory",
                        ip_hash="a" * 64,
                    )
                )
        if profile_status is not None:
            db.add(CreatorProfile(user_id=user.id, status=profile_status))
        db.commit()
        return user


def create_product(
    client,
    *,
    model_name: str = "Air Cleaner 100",
    sku: str = "AMP-AC-100",
    is_active: bool = True,
) -> Product:
    with client.app.state.test_session() as db:
        product = Product(
            brand=Brand.AMP,
            model_name=model_name,
            publication_name=f"Очиститель воздуха AMP {model_name}",
            sku=sku,
            normalized_name=model_name.lower(),
            normalized_sku=sku.lower(),
            required_hashtags=["#amp", "#чистыйвоздух"],
            marketplace_links=[],
            is_active=is_active,
        )
        db.add(product)
        db.commit()
        return product


def login(client, email: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def catalog_payload(product_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "title": "Обзор очистителя",
        "description": "Показываю работу устройства",
        "product": {"type": "catalog", "product_id": str(product_id)},
    }
    payload.update(overrides)
    return payload


def post_card(client, payload: dict) -> dict:
    response = client.post(
        "/api/v1/me/video-cards", json=payload, headers=csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_video_card_writes_require_approved_active_blogger_and_csrf(client):
    product = create_product(client)
    pending = create_user(
        client,
        "pending-card-writer@example.com",
        profile_status=ProfileStatus.SUBMITTED,
    )
    login(client, pending.email)

    no_csrf = client.post("/api/v1/me/video-cards", json=catalog_payload(product.id))
    denied = client.post(
        "/api/v1/me/video-cards",
        json=catalog_payload(product.id),
        headers=csrf_headers(client),
    )

    assert no_csrf.status_code == 403
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CREATOR_NOT_APPROVED"

    moderator = create_user(
        client,
        "moderator-card-writer@example.com",
        role=Role.MODERATOR,
        profile_status=None,
    )
    login(client, moderator.email)
    assert client.post(
        "/api/v1/me/video-cards",
        json=catalog_payload(product.id),
        headers=csrf_headers(client),
    ).status_code == 403


def test_catalog_card_stores_immutable_product_snapshot_and_audit(client):
    blogger = create_user(client, "catalog-card-owner@example.com")
    product = create_product(client)
    login(client, blogger.email)

    card = post_card(
        client,
        catalog_payload(
            product.id,
            title="  Обзор   очистителя  ",
            description="   ",
        ),
    )

    assert card["title"] == "Обзор очистителя"
    assert card["description"] is None
    assert card["product_id"] == str(product.id)
    assert card["product_snapshot"]["sku"] == "AMP-AC-100"
    assert card["reported_product"] is None
    assert card["is_product_resolved"] is True
    assert card["status"] == "draft"
    assert card["publication_summary"]["total"] == 0

    with client.app.state.test_session() as db:
        stored_product = db.get(Product, product.id)
        stored_product.sku = "CHANGED-LATER"
        db.commit()

    detail = client.get(f"/api/v1/me/video-cards/{card['id']}")
    assert detail.status_code == 200
    assert detail.json()["product_snapshot"]["sku"] == "AMP-AC-100"
    with client.app.state.test_session() as db:
        event = db.scalar(
            select(SecurityEvent).where(
                SecurityEvent.action == AuditAction.VIDEO_CARD_CREATED,
                SecurityEvent.object_id == uuid.UUID(card["id"]),
            )
        )
        assert event.event_metadata == {"product_source": "catalog"}


def test_unlisted_product_can_be_replaced_with_catalog_product(client):
    blogger = create_user(client, "unlisted-card-owner@example.com")
    product = create_product(client)
    login(client, blogger.email)
    card = post_card(
        client,
        {
            "title": "Новая модель",
            "product": {"type": "unlisted", "brand": "AMP", "name": "  Model X  "},
        },
    )

    assert card["product_id"] is None
    assert card["product_snapshot"] is None
    assert card["reported_product"] == {"brand": "AMP", "name": "Model X"}
    assert card["is_product_resolved"] is False

    updated = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"product": {"type": "catalog", "product_id": str(product.id)}},
        headers=csrf_headers(client),
    )
    assert updated.status_code == 200
    assert updated.json()["product_id"] == str(product.id)
    assert updated.json()["reported_product"] is None


def test_hidden_or_missing_product_is_not_available(client):
    blogger = create_user(client, "hidden-product-card@example.com")
    hidden_product = create_product(client, is_active=False)
    login(client, blogger.email)

    hidden = client.post(
        "/api/v1/me/video-cards",
        json=catalog_payload(hidden_product.id),
        headers=csrf_headers(client),
    )
    missing = client.post(
        "/api/v1/me/video-cards",
        json=catalog_payload(uuid.uuid4()),
        headers=csrf_headers(client),
    )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "PRODUCT_NOT_AVAILABLE"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PRODUCT_NOT_AVAILABLE"


def test_card_access_is_owner_scoped_and_suspended_blogger_can_read(client):
    owner = create_user(client, "video-card-owner@example.com")
    product = create_product(client)
    login(client, owner.email)
    card = post_card(client, catalog_payload(product.id))

    other = create_user(client, "other-video-card-owner@example.com")
    login(client, other.email)
    assert client.get(f"/api/v1/me/video-cards/{card['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"title": "Чужое изменение"},
        headers=csrf_headers(client),
    ).status_code == 404

    with client.app.state.test_session() as db:
        stored_owner = db.get(User, owner.id)
        stored_owner.status = AccountStatus.SUSPENDED
        db.commit()
    login(client, owner.email)
    assert client.get(f"/api/v1/me/video-cards/{card['id']}").status_code == 200
    denied_update = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"title": "Недоступное изменение"},
        headers=csrf_headers(client),
    )
    assert denied_update.status_code == 403
    assert denied_update.json()["error"]["code"] == "CONTENT_PERMISSION_CHANGED"


def test_patch_is_partial_and_noop_does_not_write_false_audit(client):
    blogger = create_user(client, "patch-video-card@example.com")
    product = create_product(client)
    login(client, blogger.email)
    card = post_card(client, catalog_payload(product.id))

    updated = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"description": "Новое описание"},
        headers=csrf_headers(client),
    )
    noop = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"description": "Новое описание"},
        headers=csrf_headers(client),
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == card["title"]
    assert noop.status_code == 200
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(
                SecurityEvent.object_id == uuid.UUID(card["id"]),
                SecurityEvent.action == AuditAction.VIDEO_CARD_UPDATED,
            )
        ) == 1


def test_video_card_list_is_paginated(client):
    blogger = create_user(client, "list-video-cards@example.com")
    product = create_product(client)
    login(client, blogger.email)
    post_card(client, catalog_payload(product.id, title="Первая карточка"))
    post_card(client, catalog_payload(product.id, title="Вторая карточка"))

    response = client.get("/api/v1/me/video-cards?page=1&pageSize=1")

    assert response.status_code == 200
    assert response.json()["page_size"] == 1
    assert response.json()["total_items"] == 2
    assert response.json()["total_pages"] == 2
    assert len(response.json()["items"]) == 1


def test_invalid_card_inputs_are_rejected_at_api_boundary(client):
    blogger = create_user(client, "invalid-video-card@example.com")
    product = create_product(client)
    login(client, blogger.email)

    empty_patch = client.patch(
        f"/api/v1/me/video-cards/{uuid.uuid4()}",
        json={},
        headers=csrf_headers(client),
    )
    null_product = client.post(
        "/api/v1/me/video-cards",
        json={"title": "Карточка", "product": None},
        headers=csrf_headers(client),
    )
    bad_variant = client.post(
        "/api/v1/me/video-cards",
        json={
            "title": "Карточка",
            "product": {"type": "catalog", "product_id": str(product.id), "name": "Лишнее"},
        },
        headers=csrf_headers(client),
    )

    assert empty_patch.status_code == 422
    assert null_product.status_code == 422
    assert bad_variant.status_code == 422


def test_database_rejects_card_without_any_product(client):
    blogger = create_user(client, "invalid-db-video-card@example.com")
    with client.app.state.test_session() as db:
        db.add(VideoCard(blogger_id=blogger.id, title="Invalid card"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    with client.app.state.test_session() as db:
        db.add(
            VideoCard(
                blogger_id=blogger.id,
                title="Invalid manual product",
                reported_brand=Brand.AMP,
                reported_product_name="   ",
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_video_card_and_audit_event_roll_back_together(client, monkeypatch):
    blogger = create_user(client, "rollback-video-card@example.com")
    product = create_product(client)
    login(client, blogger.email)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(content_service, "record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            "/api/v1/me/video-cards",
            json=catalog_payload(product.id),
            headers=csrf_headers(client),
        )

    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(VideoCard)) == 0
