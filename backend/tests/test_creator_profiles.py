from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now


PASSWORD = "correct-horse-battery-staple"
PROFILE = {
    "full_name": "Test Creator",
    "display_name": "Test Channel",
    "phone": "+7 999 123-45-67",
    "telegram": "@test_creator",
    "city_country": "Moscow, Russia",
    "content_topics": "Technology",
    "recipient_status": "self_employed",
}


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def register_and_login(client, email: str = "creator-profile@example.com") -> None:
    documents = client.get("/api/v1/legal-documents/current").json()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "program_terms_document_id": documents["program_terms"]["id"],
            "program_terms_accepted": True,
            "personal_data_consent_document_id": documents["personal_data_consent"]["id"],
            "personal_data_consent_granted": True,
        },
    )
    assert response.status_code == 201
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def verify_user(client, email: str) -> None:
    with client.app.state.test_session() as db:
        user = db.query(User).filter(User.email == email).one()
        user.email_verified_at = utc_now()
        user.status = AccountStatus.ACTIVE
        db.commit()


def create_moderator(client, email: str = "moderator@example.com") -> None:
    with client.app.state.test_session() as db:
        db.add(
            User(
                email=email,
                password_hash=hash_password(PASSWORD),
                role=Role.MODERATOR,
                status=AccountStatus.ACTIVE,
                email_verified_at=utc_now(),
            )
        )
        db.commit()


def test_profile_submission_requires_verified_email_and_complete_data(client):
    email = "creator-requirements@example.com"
    register_and_login(client, email)

    assert client.put("/api/v1/me/profile", json=PROFILE).status_code == 403
    assert client.put("/api/v1/me/profile", json=PROFILE, headers=csrf_headers(client)).status_code == 200
    assert client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "youtube", "url": "https://youtube.com/@test", "follower_count": 1200},
        headers=csrf_headers(client),
    ).status_code == 201

    unverified = client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client))
    assert unverified.status_code == 409
    assert unverified.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

    verify_user(client, email)
    submitted = client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client))
    assert submitted.status_code == 201
    assert submitted.json()["status"] == "submitted"

    duplicate = client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "PROFILE_NOT_SUBMITTABLE"


def test_edit_during_review_resets_profile_to_draft(client):
    email = "creator-edit@example.com"
    register_and_login(client, email)
    verify_user(client, email)
    client.put("/api/v1/me/profile", json=PROFILE, headers=csrf_headers(client))
    client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "vk", "url": "https://vk.com/test_creator", "follower_count": 400},
        headers=csrf_headers(client),
    )
    assert client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client)).status_code == 201

    changed = dict(PROFILE, display_name="Changed Channel")
    response = client.put("/api/v1/me/profile", json=changed, headers=csrf_headers(client))
    assert response.status_code == 200
    assert response.json()["status"] == "draft"
    assert response.json()["history"][0]["event_type"] == "profile_updated"

    assert client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client)).status_code == 201
    social_change = client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "youtube", "url": "https://youtube.com/@changed", "follower_count": 500},
        headers=csrf_headers(client),
    )
    assert social_change.status_code == 201
    assert client.get("/api/v1/me/profile").json()["profile"]["status"] == "draft"


def test_moderator_rejects_profile_and_history_is_preserved(client):
    creator_email = "creator-moderation@example.com"
    register_and_login(client, creator_email)
    verify_user(client, creator_email)
    profile_id = client.put(
        "/api/v1/me/profile", json=PROFILE, headers=csrf_headers(client)
    ).json()["id"]
    client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "tiktok", "url": "https://tiktok.com/@test_creator", "follower_count": 900},
        headers=csrf_headers(client),
    )
    client.post("/api/v1/me/profile-submissions", headers=csrf_headers(client))

    create_moderator(client)
    login = client.post("/api/v1/auth/login", json={"email": "moderator@example.com", "password": PASSWORD})
    assert login.status_code == 200

    queue = client.get("/api/v1/moderation/profiles?status=submitted&pageSize=10")
    assert queue.status_code == 200
    assert queue.json()["total_items"] == 1

    started = client.post(
        f"/api/v1/moderation/profiles/{profile_id}/reviews",
        json={"decision": "start_review"},
        headers=csrf_headers(client),
    )
    assert started.status_code == 200
    assert started.json()["status"] == "in_review"

    missing_reason = client.post(
        f"/api/v1/moderation/profiles/{profile_id}/reviews",
        json={"decision": "reject"},
        headers=csrf_headers(client),
    )
    assert missing_reason.status_code == 422

    rejected = client.post(
        f"/api/v1/moderation/profiles/{profile_id}/reviews",
        json={"decision": "reject", "reason": "Telegram contact is invalid"},
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["moderation_reason"] == "Telegram contact is invalid"
    assert [event["event_type"] for event in rejected.json()["history"][:2]] == [
        "profile_reject",
        "profile_start_review",
    ]


def test_social_account_moderation_history_and_soft_delete(client):
    creator_email = "social-moderation@example.com"
    register_and_login(client, creator_email)
    verify_user(client, creator_email)
    client.put("/api/v1/me/profile", json=PROFILE, headers=csrf_headers(client))

    created = client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "youtube", "url": "https://youtube.com/@moderation", "follower_count": 300},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201
    account_id = created.json()["id"]

    assert client.get("/api/v1/moderation/social-accounts?status=pending").status_code == 403

    create_moderator(client, "social-moderator@example.com")
    moderator_login = client.post(
        "/api/v1/auth/login", json={"email": "social-moderator@example.com", "password": PASSWORD}
    )
    assert moderator_login.status_code == 200

    queue = client.get("/api/v1/moderation/social-accounts?status=pending&pageSize=10")
    assert queue.status_code == 200
    assert queue.json()["total_items"] == 1
    assert queue.json()["items"][0]["creator_user_id"]
    assert queue.json()["items"][0]["creator_display_name"] == PROFILE["display_name"]

    no_reason = client.post(
        f"/api/v1/moderation/social-accounts/{account_id}/reviews",
        json={"decision": "reject"},
        headers=csrf_headers(client),
    )
    assert no_reason.status_code == 422

    rejected = client.post(
        f"/api/v1/moderation/social-accounts/{account_id}/reviews",
        json={"decision": "reject", "reason": "The account ownership is not confirmed"},
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 200
    assert rejected.json()["account"]["status"] == "rejected"
    assert rejected.json()["history"][0]["event_type"] == "social_account_reject"

    repeated = client.post(
        f"/api/v1/moderation/social-accounts/{account_id}/reviews",
        json={"decision": "approve"},
        headers=csrf_headers(client),
    )
    assert repeated.status_code == 409

    client.post("/api/v1/auth/login", json={"email": creator_email, "password": PASSWORD})
    updated = client.patch(
        f"/api/v1/me/social-accounts/{account_id}",
        json={"url": "https://youtube.com/@moderation-fixed"},
        headers=csrf_headers(client),
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "pending"

    client.post(
        "/api/v1/auth/login", json={"email": "social-moderator@example.com", "password": PASSWORD}
    )
    approved = client.post(
        f"/api/v1/moderation/social-accounts/{account_id}/reviews",
        json={"decision": "approve"},
        headers=csrf_headers(client),
    )
    assert approved.status_code == 200
    assert approved.json()["account"]["status"] == "approved"

    client.post("/api/v1/auth/login", json={"email": creator_email, "password": PASSWORD})
    deleted = client.delete(
        f"/api/v1/me/social-accounts/{account_id}", headers=csrf_headers(client)
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/me/social-accounts").json() == []

    restored = client.post(
        "/api/v1/me/social-accounts",
        json={"platform": "youtube", "url": "https://youtube.com/@moderation-fixed", "follower_count": 350},
        headers=csrf_headers(client),
    )
    assert restored.status_code == 201
    assert restored.json()["id"] == account_id
    assert restored.json()["status"] == "pending"

    client.post(
        "/api/v1/auth/login", json={"email": "social-moderator@example.com", "password": PASSWORD}
    )
    detail = client.get(f"/api/v1/moderation/social-accounts/{account_id}")
    assert detail.status_code == 200
    assert [event["event_type"] for event in detail.json()["history"][:3]] == [
        "social_account_restored",
        "social_account_deleted",
        "social_account_approve",
    ]
