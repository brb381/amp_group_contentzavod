import hashlib

import pytest
from sqlalchemy import func, select

from app.audit.models import SecurityEvent
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now
from app.legal.models import LegalAcceptance, LegalDocument, LegalDocumentType


PASSWORD = "correct-horse-battery-staple"


def _documents(client) -> dict:
    response = client.get("/api/v1/legal-documents/current")
    assert response.status_code == 200, response.text
    return response.json()


def _registration_payload(client, email: str) -> dict:
    documents = _documents(client)
    return {
        "email": email,
        "password": PASSWORD,
        "program_terms_document_id": documents["program_terms"]["id"],
        "program_terms_accepted": True,
        "personal_data_consent_document_id": documents["personal_data_consent"]["id"],
        "personal_data_consent_granted": True,
    }


def _register(client, email: str = "legal-blogger@example.com") -> User:
    response = client.post("/api/v1/auth/register", json=_registration_payload(client, email))
    assert response.status_code == 201, response.text
    with client.app.state.test_session.begin() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.status = AccountStatus.ACTIVE
        user.email_verified_at = utc_now()
        return user


def _create_admin(client, email: str = "legal-admin@example.com") -> User:
    with client.app.state.test_session.begin() as db:
        admin = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.ADMIN,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(admin)
        db.flush()
        return admin


def _login(client, email: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def _publish(client, *, document_type: str, version: str, requires_reacceptance: bool):
    return client.post(
        "/api/v1/admin/legal-documents",
        headers={"X-CSRF-Token": client.cookies.get("amp_csrf")},
        json={
            "document_type": document_type,
            "version": version,
            "title": f"Updated {document_type}",
            "content_markdown": f"# Updated\n\nFull legal content for {document_type} version {version}.",
            "requires_reacceptance": requires_reacceptance,
            "change_summary": "Test publication",
        },
    )


def test_registration_uses_server_documents_and_records_separate_acceptances(client):
    documents = _documents(client)
    assert set(documents) == {"program_terms", "personal_data_consent", "privacy_policy"}
    response = client.post(
        "/api/v1/auth/register",
        json=_registration_payload(client, "accepted-documents@example.com"),
    )
    assert response.status_code == 201
    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == "accepted-documents@example.com"))
        acceptances = list(
            db.scalars(select(LegalAcceptance).where(LegalAcceptance.user_id == user.id))
        )
        assert len(acceptances) == 2
        accepted_types = set(
            db.scalars(
                select(LegalDocument.document_type)
                .join(LegalAcceptance, LegalAcceptance.document_id == LegalDocument.id)
                .where(LegalAcceptance.user_id == user.id)
            )
        )
        assert accepted_types == {
            LegalDocumentType.PROGRAM_TERMS,
            LegalDocumentType.PERSONAL_DATA_CONSENT,
        }
        assert db.scalar(
            select(func.count()).select_from(SecurityEvent).where(
                SecurityEvent.action == "legal.document_accepted",
                SecurityEvent.actor_user_id == user.id,
            )
        ) == 2


def test_creator_can_page_acceptance_history_and_open_exact_document(client):
    blogger = _register(client, "legal-history@example.com")
    _login(client, blogger.email)
    history = client.get("/api/v1/me/legal-acceptances?page=1&pageSize=1")
    assert history.status_code == 200
    assert history.json()["total_items"] == 2
    assert history.json()["total_pages"] == 2
    item = history.json()["items"][0]
    document = client.get(f"/api/v1/legal-documents/{item['document_id']}")
    assert document.status_code == 200
    assert document.json()["version"] == item["document_version"]
    assert document.json()["content_sha256"] == item["document_content_sha256"]


def test_registration_rejects_stale_document_ids_atomically(client):
    payload = _registration_payload(client, "stale-legal@example.com")
    _create_admin(client)
    _login(client, "legal-admin@example.com")
    published = _publish(
        client,
        document_type="program_terms",
        version="2026-09",
        requires_reacceptance=True,
    )
    assert published.status_code == 201, published.text
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LEGAL_DOCUMENT_VERSION_OUTDATED"
    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.email == "stale-legal@example.com")) == 0


def test_material_change_blocks_only_participation_until_accepted(client):
    blogger = _register(client)
    _create_admin(client)
    _login(client, "legal-admin@example.com")
    published = _publish(
        client,
        document_type="program_terms",
        version="2026-10",
        requires_reacceptance=True,
    )
    assert published.status_code == 201, published.text

    csrf = _login(client, blogger.email)
    status_response = client.get("/api/v1/me/legal-status")
    assert status_response.status_code == 200
    assert status_response.json()["is_participation_allowed"] is False
    assert client.get("/api/v1/me/profile").status_code == 200
    blocked = client.put(
        "/api/v1/me/profile",
        headers=csrf,
        json={"full_name": "Legal User", "display_name": "legal-user"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LEGAL_ACCEPTANCE_REQUIRED"

    accepted = client.post(
        "/api/v1/me/legal-acceptances",
        headers=csrf,
        json={"document_id": published.json()["id"], "accepted": True},
    )
    assert accepted.status_code == 201, accepted.text
    assert client.get("/api/v1/me/legal-status").json()["is_participation_allowed"] is True
    assert client.put(
        "/api/v1/me/profile",
        headers=csrf,
        json={"full_name": "Legal User", "display_name": "legal-user"},
    ).status_code == 200


def test_minor_document_change_does_not_require_reacceptance(client):
    blogger = _register(client, "minor-change@example.com")
    _create_admin(client)
    _login(client, "legal-admin@example.com")
    response = _publish(
        client,
        document_type="program_terms",
        version="2026-08.1",
        requires_reacceptance=False,
    )
    assert response.status_code == 201, response.text
    _login(client, blogger.email)
    assert client.get("/api/v1/me/legal-status").json() == {
        "is_participation_allowed": True,
        "required_acceptances": [],
    }


def test_personal_data_consent_can_be_withdrawn_and_granted_again(client):
    blogger = _register(client, "withdrawal@example.com")
    _create_admin(client)
    _login(client, "legal-admin@example.com")
    current_consent = _publish(
        client,
        document_type="personal_data_consent",
        version="2026-08.1",
        requires_reacceptance=False,
    )
    assert current_consent.status_code == 201
    csrf = _login(client, blogger.email)
    assert client.post(
        "/api/v1/me/legal-acceptances",
        headers=csrf,
        json={"document_id": current_consent.json()["id"], "accepted": True},
    ).status_code == 201
    withdrawal = client.post(
        "/api/v1/me/personal-data-consent-withdrawals",
        headers=csrf,
    )
    assert withdrawal.status_code == 200, withdrawal.text
    assert withdrawal.json()["withdrawn_at"] is not None
    legal_status = client.get("/api/v1/me/legal-status").json()
    assert legal_status["is_participation_allowed"] is False
    required = next(
        item
        for item in legal_status["required_acceptances"]
        if item["document_type"] == "personal_data_consent"
    )
    granted = client.post(
        "/api/v1/me/legal-acceptances",
        headers=csrf,
        json={"document_id": required["document_id"], "accepted": True},
    )
    assert granted.status_code == 201, granted.text
    assert client.get("/api/v1/me/legal-status").json()["is_participation_allowed"] is True
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(LegalAcceptance)
            .where(LegalAcceptance.user_id == blogger.id)
        ) == 4
        assert db.scalar(
            select(func.count())
            .select_from(LegalAcceptance)
            .join(LegalDocument, LegalDocument.id == LegalAcceptance.document_id)
            .where(
                LegalAcceptance.user_id == blogger.id,
                LegalAcceptance.withdrawn_at.is_(None),
                LegalDocument.document_type == LegalDocumentType.PERSONAL_DATA_CONSENT,
            )
        ) == 1


def test_published_documents_and_acceptance_facts_are_immutable(client):
    blogger = _register(client, "immutable-legal@example.com")
    with pytest.raises(ValueError, match="immutable"):
        with client.app.state.test_session.begin() as db:
            document = db.scalar(
                select(LegalDocument).where(
                    LegalDocument.document_type == LegalDocumentType.PROGRAM_TERMS,
                    LegalDocument.is_current.is_(True),
                )
            )
            document.content_markdown = "Changed after publication"
    with pytest.raises(ValueError, match="immutable"):
        with client.app.state.test_session.begin() as db:
            acceptance = db.scalar(
                select(LegalAcceptance).where(LegalAcceptance.user_id == blogger.id)
            )
            acceptance.ip_hash = hashlib.sha256(b"rewritten").hexdigest()
