import pytest
import uuid
from sqlalchemy import func, select

import app.catalog.service as catalog_service
from app.audit.models import SecurityEvent
from app.audit.service import AuditAction
from app.audit.service import AuditContext
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now
from app.catalog.models import Product
from app.catalog.schemas import ProductCreateRequest
from app.errors import APIError


PASSWORD = "correct-horse-battery-staple"


def create_user(client, email: str, role: Role) -> User:
    with client.app.state.test_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(user)
        db.commit()
        return user


def login(client, email: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def product_payload(**overrides) -> dict:
    payload = {
        "brand": "AMP",
        "model_name": "Air Cleaner 100",
        "publication_name": "Очиститель воздуха AMP Air Cleaner 100",
        "sku": "AMP-AC-100",
        "required_hashtags": ["AMP", "#ЧистыйВоздух", "#amp"],
        "content_hint": "Покажите работу устройства в комнате.",
        "marketplace_links": [
            {"label": "Ozon", "url": "https://www.ozon.ru/product/example"}
        ],
    }
    payload.update(overrides)
    return payload


def create_product(client, **overrides) -> dict:
    response = client.post(
        "/api/v1/products",
        json=product_payload(**overrides),
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_requires_authentication_and_editor_for_writes(client):
    assert client.get("/api/v1/products").status_code == 401

    blogger = create_user(client, "catalog-blogger@example.com", Role.BLOGGER)
    login(client, blogger.email)
    response = client.post(
        "/api/v1/products", json=product_payload(), headers=csrf_headers(client)
    )
    assert response.status_code == 403


def test_editor_creates_normalized_product_and_audit_event(client):
    moderator = create_user(client, "catalog-moderator@example.com", Role.MODERATOR)
    login(client, moderator.email)

    response = client.post(
        "/api/v1/products",
        json=product_payload(model_name="  Air   Cleaner 100  ", sku="  AMP-AC-100  "),
        headers=csrf_headers(client),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["model_name"] == "Air Cleaner 100"
    assert body["sku"] == "AMP-AC-100"
    assert body["required_hashtags"] == ["#amp", "#чистыйвоздух"]
    assert body["marketplace_links"][0]["url"] == "https://www.ozon.ru/product/example"
    with client.app.state.test_session() as db:
        product = db.get(Product, uuid.UUID(body["id"]))
        assert product.normalized_name == "air cleaner 100"
        assert db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(
                SecurityEvent.action == AuditAction.PRODUCT_CREATED,
                SecurityEvent.object_id == product.id,
            )
        ) == 1


def test_database_uniqueness_rejects_normalized_name_and_sku_per_brand(client):
    admin = create_user(client, "catalog-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    create_product(client)

    duplicate_name = client.post(
        "/api/v1/products",
        json=product_payload(model_name="air cleaner 100", sku="ANOTHER-SKU"),
        headers=csrf_headers(client),
    )
    duplicate_sku = client.post(
        "/api/v1/products",
        json=product_payload(model_name="Another model", sku="amp-ac-100"),
        headers=csrf_headers(client),
    )
    other_brand = client.post(
        "/api/v1/products",
        json=product_payload(brand="AirTone"),
        headers=csrf_headers(client),
    )

    assert duplicate_name.status_code == 409
    assert duplicate_name.json()["error"]["code"] == "PRODUCT_NAME_ALREADY_EXISTS"
    assert duplicate_sku.status_code == 409
    assert duplicate_sku.json()["error"]["code"] == "PRODUCT_SKU_ALREADY_EXISTS"
    assert other_brand.status_code == 201


def test_list_is_paginated_searchable_and_hides_inactive_from_bloggers(client):
    admin = create_user(client, "catalog-list-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    active = create_product(client)
    hidden = create_product(
        client,
        model_name="Hidden Device",
        publication_name="Hidden publication name",
        sku="HIDDEN-1",
        is_active=False,
    )

    hidden_list = client.get("/api/v1/products?isActive=false&pageSize=1")
    assert hidden_list.status_code == 200
    assert hidden_list.json()["items"][0]["id"] == hidden["id"]
    search = client.get("/api/v1/products?search=cleaner&pageSize=1")
    assert search.status_code == 200
    assert search.json()["total_items"] == 1
    assert search.json()["items"][0]["id"] == active["id"]

    blogger = create_user(client, "catalog-reader@example.com", Role.BLOGGER)
    login(client, blogger.email)
    assert client.get("/api/v1/products?isActive=false").status_code == 403
    detail = client.get(f"/api/v1/products/{hidden['id']}")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_patch_updates_only_provided_fields_and_tracks_hide_restore(client):
    admin = create_user(client, "catalog-patch-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    product = create_product(client)

    hidden = client.patch(
        f"/api/v1/products/{product['id']}",
        json={"is_active": False},
        headers=csrf_headers(client),
    )
    restored = client.patch(
        f"/api/v1/products/{product['id']}",
        json={"is_active": True, "content_hint": None},
        headers=csrf_headers(client),
    )

    assert hidden.status_code == 200
    assert hidden.json()["publication_name"] == product["publication_name"]
    assert restored.status_code == 200
    assert restored.json()["is_active"] is True
    assert restored.json()["content_hint"] is None
    with client.app.state.test_session() as db:
        actions = list(
            db.scalars(
                select(SecurityEvent.action).where(
                    SecurityEvent.object_id == uuid.UUID(product["id"])
                )
            )
        )
        assert AuditAction.PRODUCT_HIDDEN in actions
        assert AuditAction.PRODUCT_RESTORED in actions


def test_noop_patch_does_not_write_false_audit_event(client):
    admin = create_user(client, "catalog-noop-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    product = create_product(client)

    response = client.patch(
        f"/api/v1/products/{product['id']}",
        json={"model_name": product["model_name"]},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(
                SecurityEvent.object_id == uuid.UUID(product["id"]),
                SecurityEvent.action == AuditAction.PRODUCT_UPDATED,
            )
        ) == 0


def test_product_and_audit_roll_back_together(client, monkeypatch):
    admin = create_user(client, "catalog-rollback-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    product = create_product(client)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(catalog_service, "record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.patch(
            f"/api/v1/products/{product['id']}",
            json={"sku": "CHANGED-SKU"},
            headers=csrf_headers(client),
        )

    with client.app.state.test_session() as db:
        stored = db.get(Product, uuid.UUID(product["id"]))
        assert stored.sku == product["sku"]


def test_patch_rejects_empty_body_and_null_required_fields(client):
    admin = create_user(client, "catalog-validation-admin@example.com", Role.ADMIN)
    login(client, admin.email)
    product = create_product(client)

    empty = client.patch(
        f"/api/v1/products/{product['id']}", json={}, headers=csrf_headers(client)
    )
    invalid_null = client.patch(
        f"/api/v1/products/{product['id']}",
        json={"sku": None},
        headers=csrf_headers(client),
    )

    assert empty.status_code == 422
    assert invalid_null.status_code == 422


def test_create_rejects_empty_hashtag_and_link_label(client):
    admin = create_user(client, "catalog-boundary-admin@example.com", Role.ADMIN)
    login(client, admin.email)

    empty_hashtag = client.post(
        "/api/v1/products",
        json=product_payload(required_hashtags=["#"]),
        headers=csrf_headers(client),
    )
    empty_label = client.post(
        "/api/v1/products",
        json=product_payload(
            marketplace_links=[{"label": "   ", "url": "https://example.com/product"}]
        ),
        headers=csrf_headers(client),
    )

    assert empty_hashtag.status_code == 422
    assert empty_label.status_code == 422


def test_stale_editor_cannot_create_product(client):
    moderator = create_user(client, "catalog-stale-editor@example.com", Role.MODERATOR)
    with client.app.state.test_session() as db:
        current = db.get(User, moderator.id)
        current.role = Role.BLOGGER
        db.commit()

    with client.app.state.test_session() as db:
        with pytest.raises(APIError) as error:
            catalog_service.create_product(
                db,
                actor=moderator,
                payload=ProductCreateRequest(**product_payload()),
                audit_context=AuditContext(
                    request_id="stale-catalog-test",
                    ip_address="127.0.0.1",
                    user_agent="pytest",
                ),
            )
        assert error.value.code == "CATALOG_PERMISSION_CHANGED"
