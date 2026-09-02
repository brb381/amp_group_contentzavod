import uuid
from datetime import date, datetime, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

import app.content.publication_service as publication_service
from app.audit.models import SecurityEvent
from app.audit.service import AuditAction
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now
from app.youtube_config import YouTubeWorkerSettings
from app.content.models import (
    Publication,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationStatus,
)
import app.content.moderation_service as moderation_service
from app.catalog.models import Brand, Product
from app.content.url_parser import PublicationURLInvalid, parse_publication_url
from app.creators.models import CreatorProfile, ProfileStatus, SocialAccount, SocialAccountStatus
from app.platforms import Platform
from app.contracts import YouTubeEnrichmentCommand, YouTubeViewCollectionCommand
from app.scheduling.youtube import dispatch_youtube_batch, dispatch_youtube_view_batch
from app.readings.models import ReadingStatus, ViewReading, YouTubeViewCollectionJob
from app.readings.policy import risk_flags
from app.youtube.client import YouTubeClientError
from app.youtube.models import ExternalProviderState, ExternalQuotaUsage, YouTubeEnrichmentJob
from app.youtube.schemas import YouTubeViewCountsResponse, YouTubeVideosResponse
from app.youtube.service import execute_youtube_enrichment
from app.youtube.views import execute_youtube_view_collection
from app.youtube.time import pacific_quota_date
from app.legal.models import AcceptanceMethod, LegalAcceptance, LegalDocument


PASSWORD = "correct-horse-battery-staple"


def create_blogger(client, email: str) -> User:
    with client.app.state.test_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.BLOGGER,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(user)
        db.flush()
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
                    request_id="publication-test-factory",
                    ip_hash="a" * 64,
                )
            )
        db.add(CreatorProfile(user_id=user.id, status=ProfileStatus.APPROVED))
        db.commit()
        return user


def create_social_account(
    client,
    user_id: uuid.UUID,
    *,
    platform: Platform = Platform.YOUTUBE,
    status: SocialAccountStatus = SocialAccountStatus.APPROVED,
) -> SocialAccount:
    with client.app.state.test_session() as db:
        account = SocialAccount(
            user_id=user_id,
            platform=platform,
            url=f"https://{platform.value}.example.test/{uuid.uuid4()}",
            status=status,
        )
        db.add(account)
        db.commit()
        return account


def create_moderator(client, email: str) -> User:
    with client.app.state.test_session() as db:
        moderator = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.MODERATOR,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(moderator)
        db.commit()
        return moderator


def create_manager(client, email: str) -> User:
    with client.app.state.test_session() as db:
        manager = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.MANAGER,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(manager)
        db.commit()
        return manager


def create_product(client, *, sku: str = "AMP-MOD-1") -> Product:
    with client.app.state.test_session() as db:
        product = Product(
            brand=Brand.AMP,
            model_name=f"Moderation model {sku}",
            publication_name=f"AMP Moderation model {sku}",
            sku=sku,
            normalized_name=f"moderation model {sku}".lower(),
            normalized_sku=sku.lower(),
            required_hashtags=["#amp"],
            marketplace_links=[],
            is_active=True,
        )
        db.add(product)
        db.commit()
        return product


def login(client, email: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def create_card(client, *, title: str = "Video card") -> dict:
    response = client.post(
        "/api/v1/me/video-cards",
        json={
            "title": title,
            "product": {"type": "unlisted", "brand": "AMP", "name": "Model X"},
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_publication(client, card_id: str, account_id: uuid.UUID, url: str) -> dict:
    response = client.post(
        f"/api/v1/me/video-cards/{card_id}/publications",
        json={"social_account_id": str(account_id), "url": url},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_url_parser_canonicalizes_identity_and_rejects_wrong_domain():
    watch = parse_publication_url(
        Platform.YOUTUBE,
        "https://www.youtube.com/watch?v=abc_123&utm_source=test",
    )
    short = parse_publication_url(Platform.YOUTUBE, "https://youtu.be/abc_123")

    assert watch == short
    assert watch.normalized_url == "https://youtube.com/shorts/abc_123"
    assert watch.external_id == "abc_123"
    assert watch.parse_status.value == "parsed"
    with pytest.raises(PublicationURLInvalid):
        parse_publication_url(Platform.YOUTUBE, "https://vk.com/clip1_2")
    with pytest.raises(PublicationURLInvalid):
        parse_publication_url(Platform.YOUTUBE, f"https://youtu.be/{'x' * 256}")


def test_unknown_valid_url_is_kept_for_manual_review():
    parsed = parse_publication_url(
        Platform.INSTAGRAM,
        "https://www.instagram.com/example/?utm_campaign=x&tab=videos",
    )

    assert parsed.external_id is None
    assert parsed.parse_status.value == "manual_review"
    assert parsed.normalized_url == "https://www.instagram.com/example?tab=videos"


def test_create_publication_normalizes_url_and_writes_history_and_audit(client):
    blogger = create_blogger(client, "publication-owner@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client)

    publication = create_publication(
        client,
        card["id"],
        account.id,
        "https://www.youtube.com/watch?v=video123&utm_source=campaign",
    )

    assert publication["platform"] == "youtube"
    assert publication["normalized_url"] == "https://youtube.com/shorts/video123"
    assert publication["external_id"] == "video123"
    assert publication["status"] == "draft"
    assert [item["event_type"] for item in publication["history"]] == ["publication_created"]
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count()).select_from(SecurityEvent).where(
                SecurityEvent.action == AuditAction.PUBLICATION_CREATED,
                SecurityEvent.object_id == uuid.UUID(publication["id"]),
            )
        ) == 1


def test_publication_requires_owned_approved_account_and_matching_domain(client):
    owner = create_blogger(client, "publication-account-owner@example.com")
    other = create_blogger(client, "publication-account-other@example.com")
    foreign_account = create_social_account(client, other.id)
    pending_account = create_social_account(
        client, owner.id, status=SocialAccountStatus.PENDING
    )
    approved_account = create_social_account(client, owner.id)
    login(client, owner.email)
    card = create_card(client)

    for account in (foreign_account, pending_account):
        response = client.post(
            f"/api/v1/me/video-cards/{card['id']}/publications",
            json={"social_account_id": str(account.id), "url": "https://youtu.be/new123"},
            headers=csrf_headers(client),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SOCIAL_ACCOUNT_NOT_APPROVED"

    mismatch = client.post(
        f"/api/v1/me/video-cards/{card['id']}/publications",
        json={"social_account_id": str(approved_account.id), "url": "https://vk.com/clip1_2"},
        headers=csrf_headers(client),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "PUBLICATION_URL_INVALID"


def test_duplicate_identity_is_rejected_atomically(client):
    blogger = create_blogger(client, "duplicate-publication@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    first_card = create_card(client, title="First")
    second_card = create_card(client, title="Second")
    first = create_publication(client, first_card["id"], account.id, "https://youtu.be/same123")

    duplicate = client.post(
        f"/api/v1/me/video-cards/{second_card['id']}/publications",
        json={
            "social_account_id": str(account.id),
            "url": "https://youtube.com/watch?v=same123&utm_source=other",
        },
        headers=csrf_headers(client),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PUBLICATION_URL_ALREADY_EXISTS"
    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(Publication)) == 1
        assert db.scalar(select(func.count()).select_from(PublicationHistory)) == 1
        assert db.get(Publication, uuid.UUID(first["id"])) is not None


def test_submit_locks_publication_and_card_product_and_updates_aggregate(client):
    blogger = create_blogger(client, "submit-publication@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/submit123")

    submitted = client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    edit = client.patch(
        f"/api/v1/me/publications/{publication['id']}",
        json={"url": "https://youtu.be/changed123"},
        headers=csrf_headers(client),
    )
    product_change = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"product": {"type": "unlisted", "brand": "AMP", "name": "Model Y"}},
        headers=csrf_headers(client),
    )
    card_detail = client.get(f"/api/v1/me/video-cards/{card['id']}")

    assert submitted.status_code == 201
    assert submitted.json()["status"] == "pending_review"
    assert [item["event_type"] for item in submitted.json()["history"]] == [
        "publication_submitted",
        "publication_created",
    ]
    assert edit.status_code == 409
    assert product_change.status_code == 409
    assert product_change.json()["error"]["code"] == "VIDEO_CARD_PRODUCT_LOCKED"
    assert card_detail.json()["status"] == "pending_review"
    assert card_detail.json()["publication_summary"] == {
        "total": 1,
        "approved": 0,
        "pending_review": 1,
    }


def test_draft_update_delete_and_owner_scope(client):
    owner = create_blogger(client, "draft-publication-owner@example.com")
    account = create_social_account(client, owner.id)
    login(client, owner.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/draft123")

    updated = client.patch(
        f"/api/v1/me/publications/{publication['id']}",
        json={"url": "https://youtu.be/draft456"},
        headers=csrf_headers(client),
    )
    assert updated.status_code == 200
    assert updated.json()["external_id"] == "draft456"

    other = create_blogger(client, "draft-publication-other@example.com")
    login(client, other.email)
    assert client.get(f"/api/v1/me/publications/{publication['id']}").status_code == 404

    login(client, owner.email)
    deleted = client.delete(
        f"/api/v1/me/publications/{publication['id']}", headers=csrf_headers(client)
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/me/publications/{publication['id']}").status_code == 404
    listing = client.get(f"/api/v1/me/video-cards/{card['id']}/publications")
    assert listing.json()["total_items"] == 0


def test_publication_and_history_roll_back_when_audit_fails(client, monkeypatch):
    blogger = create_blogger(client, "rollback-publication@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(publication_service, "record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            f"/api/v1/me/video-cards/{card['id']}/publications",
            json={"social_account_id": str(account.id), "url": "https://youtu.be/rollback123"},
            headers=csrf_headers(client),
        )

    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(Publication)) == 0
        assert db.scalar(select(func.count()).select_from(PublicationHistory)) == 0


def test_moderation_queue_is_protected_filtered_and_contains_context(client):
    blogger = create_blogger(client, "moderation-queue-blogger@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client, title="Queue card")
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/queue123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    assert client.get("/api/v1/moderation/publications").status_code == 403

    moderator = create_moderator(client, "moderation-queue-reviewer@example.com")
    login(client, moderator.email)
    queue = client.get(
        "/api/v1/moderation/publications?platform=youtube&parseStatus=parsed&pageSize=10"
    )
    detail = client.get(f"/api/v1/moderation/publications/{publication['id']}")

    assert queue.status_code == 200
    assert queue.json()["total_items"] == 1
    assert queue.json()["items"][0]["card_title"] == "Queue card"
    assert queue.json()["items"][0]["creator_email"] == blogger.email
    assert detail.status_code == 200
    assert detail.json()["social_account"]["id"] == str(account.id)
    assert detail.json()["card"]["publication_summary"]["pending_review"] == 1


def test_request_changes_allows_product_fix_and_resubmission(client):
    blogger = create_blogger(client, "changes-publication-blogger@example.com")
    account = create_social_account(client, blogger.id)
    product = create_product(client, sku="AMP-CHANGES")
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/changes123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )

    moderator = create_moderator(client, "changes-publication-reviewer@example.com")
    login(client, moderator.email)
    missing_reason = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "request_changes"},
        headers=csrf_headers(client),
    )
    requested = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "request_changes", "reason": "Correct the product model"},
        headers=csrf_headers(client),
    )
    assert missing_reason.status_code == 422
    assert requested.status_code == 200
    assert requested.json()["publication"]["status"] == "changes_required"
    assert requested.json()["publication"]["moderation_reason"] == "Correct the product model"

    login(client, blogger.email)
    fixed = client.patch(
        f"/api/v1/me/video-cards/{card['id']}",
        json={"product": {"type": "catalog", "product_id": str(product.id)}},
        headers=csrf_headers(client),
    )
    resubmitted = client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    assert fixed.status_code == 200
    assert fixed.json()["product_id"] == str(product.id)
    assert resubmitted.status_code == 201
    assert resubmitted.json()["moderation_reason"] is None

    login(client, moderator.email)
    approved = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve"},
        headers=csrf_headers(client),
    )
    assert approved.status_code == 200
    assert approved.json()["publication"]["status"] == "approved"
    assert approved.json()["card"]["status"] == "approved"


def test_approval_can_atomically_resolve_product_and_rejects_repeated_decision(client):
    blogger = create_blogger(client, "resolve-publication-blogger@example.com")
    account = create_social_account(client, blogger.id)
    product = create_product(client, sku="AMP-RESOLVE")
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/resolve123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )

    moderator = create_moderator(client, "resolve-publication-reviewer@example.com")
    login(client, moderator.email)
    unresolved = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve"},
        headers=csrf_headers(client),
    )
    invalid_negative_product = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={
            "decision": "reject",
            "reason": "Wrong video",
            "resolved_product_id": str(product.id),
        },
        headers=csrf_headers(client),
    )
    approved = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(product.id)},
        headers=csrf_headers(client),
    )
    repeated = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "reject", "reason": "Too late"},
        headers=csrf_headers(client),
    )

    assert unresolved.status_code == 409
    assert unresolved.json()["error"]["code"] == "VIDEO_CARD_PRODUCT_UNRESOLVED"
    assert invalid_negative_product.status_code == 422
    assert approved.status_code == 200
    assert approved.json()["card"]["product_id"] == str(product.id)
    assert approved.json()["history"][0]["changes"] == {
        "resolved_product_id": str(product.id)
    }
    with client.app.state.test_session() as db:
        baseline_job = db.scalar(
            select(YouTubeViewCollectionJob).where(
                YouTubeViewCollectionJob.publication_id
                == uuid.UUID(publication["id"])
            )
        )
        assert baseline_job is not None
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "INVALID_PUBLICATION_TRANSITION"


def test_approval_requires_available_link_and_still_approved_account(client):
    blogger = create_blogger(client, "approval-preconditions-blogger@example.com")
    account = create_social_account(client, blogger.id)
    product = create_product(client, sku="AMP-PRECONDITION")
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/precondition123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    with client.app.state.test_session() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        stored.availability = "unavailable"
        db.commit()

    moderator = create_moderator(client, "approval-preconditions-reviewer@example.com")
    login(client, moderator.email)
    unavailable = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(product.id)},
        headers=csrf_headers(client),
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "PUBLICATION_UNAVAILABLE"

    with client.app.state.test_session() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        stored.availability = "unknown"
        stored_account = db.get(SocialAccount, account.id)
        stored_account.status = SocialAccountStatus.PENDING
        db.commit()
    account_changed = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(product.id)},
        headers=csrf_headers(client),
    )
    assert account_changed.status_code == 409
    assert account_changed.json()["error"]["code"] == "PUBLICATION_ACCOUNT_NOT_APPROVED"


def test_approval_requires_creator_to_remain_active_and_approved(client):
    blogger = create_blogger(client, "inactive-approval-blogger@example.com")
    account = create_social_account(client, blogger.id)
    product = create_product(client, sku="AMP-INACTIVE")
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/inactive123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    with client.app.state.test_session() as db:
        stored_blogger = db.get(User, blogger.id)
        stored_blogger.status = AccountStatus.SUSPENDED
        db.commit()

    moderator = create_moderator(client, "inactive-approval-reviewer@example.com")
    login(client, moderator.email)
    approval = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(product.id)},
        headers=csrf_headers(client),
    )

    assert approval.status_code == 409
    assert approval.json()["error"]["code"] == "PUBLICATION_CREATOR_NOT_APPROVED"


def test_review_and_history_roll_back_when_audit_fails(client, monkeypatch):
    blogger = create_blogger(client, "review-rollback-blogger@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/reviewrollback")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    moderator = create_moderator(client, "review-rollback-reviewer@example.com")
    login(client, moderator.email)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(moderation_service, "record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            f"/api/v1/moderation/publications/{publication['id']}/reviews",
            json={"decision": "request_changes", "reason": "Correct the hashtags"},
            headers=csrf_headers(client),
        )

    with client.app.state.test_session() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        assert stored.status == PublicationStatus.PENDING_REVIEW
        assert db.scalar(
            select(func.count()).select_from(PublicationHistory).where(
                PublicationHistory.publication_id == stored.id
            )
        ) == 2


def test_moderator_cannot_replace_product_shared_with_an_approved_publication(client):
    blogger = create_blogger(client, "shared-product-blogger@example.com")
    account = create_social_account(client, blogger.id)
    first_product = create_product(client, sku="AMP-SHARED-1")
    second_product = create_product(client, sku="AMP-SHARED-2")
    login(client, blogger.email)
    card = create_card(client)
    first = create_publication(client, card["id"], account.id, "https://youtu.be/sharedone")
    second = create_publication(client, card["id"], account.id, "https://youtu.be/sharedtwo")
    for publication in (first, second):
        client.post(
            f"/api/v1/me/publications/{publication['id']}/submissions",
            headers=csrf_headers(client),
        )

    moderator = create_moderator(client, "shared-product-reviewer@example.com")
    login(client, moderator.email)
    first_approval = client.post(
        f"/api/v1/moderation/publications/{first['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(first_product.id)},
        headers=csrf_headers(client),
    )
    replacement = client.post(
        f"/api/v1/moderation/publications/{second['id']}/reviews",
        json={"decision": "approve", "resolved_product_id": str(second_product.id)},
        headers=csrf_headers(client),
    )

    assert first_approval.status_code == 200
    assert replacement.status_code == 409
    assert replacement.json()["error"]["code"] == "VIDEO_CARD_PRODUCT_ALREADY_RESOLVED"
    detail = client.get(f"/api/v1/moderation/publications/{second['id']}")
    assert detail.json()["card"]["product_id"] == str(first_product.id)
    assert detail.json()["publication"]["status"] == "pending_review"


def test_rejection_is_final_for_the_same_publication(client):
    blogger = create_blogger(client, "rejected-publication-blogger@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client)
    publication = create_publication(client, card["id"], account.id, "https://youtu.be/rejected123")
    client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )

    moderator = create_moderator(client, "rejected-publication-reviewer@example.com")
    login(client, moderator.email)
    rejected = client.post(
        f"/api/v1/moderation/publications/{publication['id']}/reviews",
        json={"decision": "reject", "reason": "The violation is inside the video"},
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 200
    assert rejected.json()["publication"]["status"] == "rejected"

    login(client, blogger.email)
    edit = client.patch(
        f"/api/v1/me/publications/{publication['id']}",
        json={"url": "https://youtu.be/rejectedfixed"},
        headers=csrf_headers(client),
    )
    resubmit = client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    delete = client.delete(
        f"/api/v1/me/publications/{publication['id']}",
        headers=csrf_headers(client),
    )
    assert edit.status_code == 409
    assert resubmit.status_code == 409
    assert delete.status_code == 409


class CapturingProducer:
    def __init__(self):
        self.messages = []

    def send_task(self, name, *, args, queue):
        self.messages.append({"name": name, "args": args, "queue": queue})


def _submitted_youtube_publication(client, suffix: str) -> dict:
    blogger = create_blogger(client, f"youtube-worker-{suffix}@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client, title=f"YouTube {suffix}")
    publication = create_publication(
        client,
        card["id"],
        account.id,
        f"https://youtu.be/video-{suffix}",
    )
    submitted = client.post(
        f"/api/v1/me/publications/{publication['id']}/submissions",
        headers=csrf_headers(client),
    )
    assert submitted.status_code == 201
    assert submitted.json()["enrichment_status"] == "pending"
    return submitted.json()


def test_scheduler_admits_one_youtube_batch_and_worker_applies_metadata(client):
    publication = _submitted_youtube_publication(client, "success")
    producer = CapturingProducer()
    session_factory = client.app.state.test_session

    assert dispatch_youtube_batch(
        session_factory=session_factory,
        task_producer=producer,
    ) is True
    assert dispatch_youtube_batch(
        session_factory=session_factory,
        task_producer=producer,
    ) is False
    assert len(producer.messages) == 1
    assert producer.messages[0]["queue"] == "youtube"
    command = YouTubeEnrichmentCommand.model_validate(producer.messages[0]["args"][0])

    class SuccessfulClient:
        def __init__(self):
            self.calls = 0

        def fetch_videos(self, video_ids):
            self.calls += 1
            assert video_ids == ["video-success"]
            return YouTubeVideosResponse.model_validate(
                {
                    "items": [
                        {
                            "id": "video-success",
                            "etag": "etag-1",
                            "snippet": {
                                "title": "Published title",
                                "channelId": "channel-1",
                                "channelTitle": "Creator channel",
                                "publishedAt": "2026-08-20T10:00:00Z",
                                "thumbnails": {
                                    "high": {"url": "https://img.example/video.jpg"}
                                },
                            },
                            "contentDetails": {"duration": "PT2M3S"},
                        }
                    ]
                }
            )

    worker_settings = YouTubeWorkerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        youtube_api_key=SecretStr("test-key"),
    )
    youtube_client = SuccessfulClient()
    execute_youtube_enrichment(
        command,
        worker_settings,
        session_factory,
        client=youtube_client,
    )
    execute_youtube_enrichment(
        command,
        worker_settings,
        session_factory,
        client=youtube_client,
    )
    assert youtube_client.calls == 1

    with session_factory() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        job = db.scalar(
            select(YouTubeEnrichmentJob).where(
                YouTubeEnrichmentJob.publication_id == stored.id
            )
        )
        usage = db.scalar(select(ExternalQuotaUsage))
        assert stored.enrichment_status == PublicationEnrichmentStatus.SUCCEEDED
        assert stored.external_title == "Published title"
        assert stored.external_duration_seconds == 123
        assert stored.availability.value == "available"
        assert job.state == "succeeded"
        assert usage.reserved_units == 1


def test_youtube_429_blocks_scheduler_without_worker_retry(client):
    publication = _submitted_youtube_publication(client, "limited")
    producer = CapturingProducer()
    session_factory = client.app.state.test_session
    assert dispatch_youtube_batch(
        session_factory=session_factory,
        task_producer=producer,
    ) is True
    command = YouTubeEnrichmentCommand.model_validate(producer.messages[0]["args"][0])

    class LimitedClient:
        def fetch_videos(self, video_ids):
            raise YouTubeClientError(429, "rateLimitExceeded", "120")

    worker_settings = YouTubeWorkerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        youtube_api_key=SecretStr("test-key"),
    )
    execute_youtube_enrichment(
        command,
        worker_settings,
        session_factory,
        client=LimitedClient(),
    )

    with session_factory() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        job = db.scalar(select(YouTubeEnrichmentJob))
        provider = db.get(ExternalProviderState, "youtube")
        assert stored.enrichment_status == PublicationEnrichmentStatus.RETRY_WAIT
        assert job.state == "retry_wait"
        assert provider.status == "blocked"
        assert provider.block_reason == "rateLimitExceeded"

    assert dispatch_youtube_batch(
        session_factory=session_factory,
        task_producer=producer,
    ) is False
    assert len(producer.messages) == 1


def test_scheduler_blocks_youtube_at_working_daily_limit(client):
    _submitted_youtube_publication(client, "daily-limit")
    producer = CapturingProducer()
    session_factory = client.app.state.test_session
    now = utc_now()
    with session_factory() as db:
        db.add(
            ExternalQuotaUsage(
                provider="youtube",
                quota_date=pacific_quota_date(now),
                reserved_units=9000,
                working_limit=9000,
            )
        )
        db.commit()

    assert dispatch_youtube_batch(
        now=now,
        session_factory=session_factory,
        task_producer=producer,
    ) is False
    assert producer.messages == []
    with session_factory() as db:
        provider = db.get(ExternalProviderState, "youtube")
        job = db.scalar(select(YouTubeEnrichmentJob))
        assert provider.status == "blocked"
        assert provider.block_reason == "working_limit_reached"
        assert job.state == "pending"


def test_manual_view_reading_can_be_edited_and_corrected(client, monkeypatch):
    blogger = create_blogger(client, "manual-reading@example.com")
    account = create_social_account(client, blogger.id, platform=Platform.VK)
    login(client, blogger.email)
    card = create_card(client, title="Manual views")
    publication = create_publication(
        client,
        card["id"],
        account.id,
        "https://vk.com/clip-123_456",
    )
    with client.app.state.test_session() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        stored.status = PublicationStatus.APPROVED
        db.commit()

    fixed_now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.readings.service.utc_now", lambda: fixed_now)
    created = client.post(
        f"/api/v1/me/publications/{publication['id']}/view-readings",
        json={"value": 120000},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    reading = created.json()
    assert reading["status"] == "pending"
    assert reading["reported_value"] == 120000
    duplicate = client.post(
        f"/api/v1/me/publications/{publication['id']}/view-readings",
        json={"value": 125000},
        headers=csrf_headers(client),
    )
    assert duplicate.status_code == 409
    edited = client.patch(
        f"/api/v1/me/view-readings/{reading['id']}",
        json={"value": 121000},
        headers=csrf_headers(client),
    )
    assert edited.status_code == 200
    assert edited.json()["reported_value"] == 121000

    moderator = create_moderator(client, "reading-moderator@example.com")
    login(client, moderator.email)
    corrected = client.post(
        f"/api/v1/moderation/view-readings/{reading['id']}/decisions",
        json={
            "decision": "correct",
            "accepted_value": 115000,
            "reason": "Verified against source",
        },
        headers=csrf_headers(client),
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "accepted"
    assert corrected.json()["reported_value"] == 121000
    assert corrected.json()["accepted_value"] == 115000

    manager = create_manager(client, "reading-manager@example.com")
    login(client, manager.email)
    recorrected = client.post(
        f"/api/v1/moderation/view-readings/{reading['id']}/corrections",
        json={"accepted_value": 114000, "reason": "Final manager correction"},
        headers=csrf_headers(client),
    )
    assert recorrected.status_code == 200, recorrected.text
    assert recorrected.json()["accepted_value"] == 114000


def test_youtube_view_scheduler_and_worker_store_daily_reading(client):
    blogger = create_blogger(client, "automatic-reading@example.com")
    account = create_social_account(client, blogger.id)
    login(client, blogger.email)
    card = create_card(client, title="Automatic views")
    publication = create_publication(
        client,
        card["id"],
        account.id,
        "https://youtu.be/automatic-views",
    )
    with client.app.state.test_session() as db:
        stored = db.get(Publication, uuid.UUID(publication["id"]))
        stored.status = PublicationStatus.APPROVED
        db.commit()

    producer = CapturingProducer()
    session_factory = client.app.state.test_session
    collection_time = datetime(2026, 8, 25, 20, 5, tzinfo=timezone.utc)
    assert dispatch_youtube_view_batch(
        now=collection_time,
        session_factory=session_factory,
        task_producer=producer,
    ) is True
    command = YouTubeViewCollectionCommand.model_validate(producer.messages[0]["args"][0])

    class ViewClient:
        def fetch_view_counts(self, video_ids):
            assert video_ids == ["automatic-views"]
            return YouTubeViewCountsResponse.model_validate(
                {
                    "items": [
                        {
                            "id": "automatic-views",
                            "statistics": {"viewCount": "987654"},
                        }
                    ]
                }
            )

    worker_settings = YouTubeWorkerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost:6379/0",
        youtube_api_key=SecretStr("test-key"),
    )
    execute_youtube_view_collection(
        command,
        worker_settings,
        session_factory,
        client=ViewClient(),
    )
    execute_youtube_view_collection(
        command,
        worker_settings,
        session_factory,
        client=ViewClient(),
    )

    with session_factory() as db:
        readings = list(db.scalars(select(ViewReading)))
        job = db.scalar(select(YouTubeViewCollectionJob))
        assert len(readings) == 1
        assert readings[0].reported_value == 987654
        assert readings[0].accepted_value == 987654
        assert readings[0].status == ReadingStatus.ACCEPTED
        assert job.state == "succeeded"
        assert risk_flags(
            db,
            publication_id=readings[0].publication_id,
            period=date(2026, 8, 1),
            value=900000,
            suspicious_growth_threshold=500000,
        ) == ["views_decreased"]
