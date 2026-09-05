import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.billing.models import (
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.catalog.models import Brand
from app.clock import utc_now
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationParseStatus,
    PublicationStatus,
    VideoCard,
)
from app.creators.models import (
    CreatorProfile,
    ProfileStatus,
    SocialAccount,
    SocialAccountStatus,
)
from app.platforms import Platform
from app.readings.models import ReadingSource, ReadingStatus, ViewReading
from app.readings.policy import reporting_period


PASSWORD = "correct-horse-battery-staple"


def _previous_month(period):
    return (period - timedelta(days=1)).replace(day=1)


def _user(db, email: str, role: Role) -> User:
    user = User(
        email=email,
        password_hash=hash_password(PASSWORD),
        role=role,
        status=AccountStatus.ACTIVE,
        email_verified_at=utc_now(),
    )
    db.add(user)
    db.flush()
    return user


def _login(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text


def _seed_dashboard_data(client):
    period = reporting_period(utc_now())
    with client.app.state.test_session() as db:
        blogger = _user(db, "dashboard-blogger@example.com", Role.BLOGGER)
        analyst = _user(db, "dashboard-analyst@example.com", Role.ANALYST)
        db.add(
            CreatorProfile(
                user_id=blogger.id,
                display_name="Dashboard creator",
                status=ProfileStatus.APPROVED,
            )
        )
        account = SocialAccount(
            user_id=blogger.id,
            platform=Platform.VK,
            url=f"https://vk.example.test/{uuid.uuid4()}",
            status=SocialAccountStatus.APPROVED,
        )
        card = VideoCard(
            blogger_id=blogger.id,
            title="Air test",
            reported_brand=Brand.AMP,
            reported_product_name="AMP Air",
        )
        db.add_all([account, card])
        db.flush()
        publication = Publication(
            video_card_id=card.id,
            social_account_id=account.id,
            platform=Platform.VK,
            submitted_url=f"https://vk.com/clip-{uuid.uuid4()}",
            normalized_url=f"https://vk.com/clip-{uuid.uuid4()}",
            external_id=str(uuid.uuid4()),
            status=PublicationStatus.APPROVED,
            parse_status=PublicationParseStatus.PARSED,
            availability=PublicationAvailability.AVAILABLE,
            enrichment_status=PublicationEnrichmentStatus.NOT_REQUESTED,
        )
        db.add(publication)
        db.flush()
        previous = ViewReading(
            publication_id=publication.id,
            reporting_period=_previous_month(period),
            source=ReadingSource.MANUAL,
            reported_value=1000,
            accepted_value=1000,
            status=ReadingStatus.ACCEPTED,
            risk_flags=[],
            idempotency_key="dashboard-previous",
            captured_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        current = ViewReading(
            publication_id=publication.id,
            reporting_period=period,
            source=ReadingSource.MANUAL,
            reported_value=1500,
            accepted_value=1500,
            status=ReadingStatus.ACCEPTED,
            risk_flags=["large_growth"],
            idempotency_key="dashboard-current",
            captured_at=datetime.now(timezone.utc),
        )
        rate = RateVersion(
            rate_kopecks_per_view=5,
            effective_from_period=period,
            created_by_user_id=analyst.id,
        )
        db.add_all([previous, current, rate])
        db.flush()
        calculation = CalculationPeriod(
            period=period,
            status=CalculationPeriodStatus.PRELIMINARY,
            rate_version_id=rate.id,
            input_revision=1,
            calculated_at=datetime.now(timezone.utc),
            total_views=500,
            total_amount_kopecks=2500,
        )
        db.add(calculation)
        db.flush()
        db.add_all(
            [
                CreatorPeriodTotal(
                    period_id=calculation.id,
                    blogger_id=blogger.id,
                    eligible_views=500,
                    amount_kopecks=2500,
                    adjustment_kopecks=0,
                    publication_count=1,
                    risk_count=1,
                ),
                PublicationAccrual(
                    period_id=calculation.id,
                    publication_id=publication.id,
                    blogger_id=blogger.id,
                    previous_reading_id=previous.id,
                    previous_value=1000,
                    current_reading_id=current.id,
                    current_value=1500,
                    eligible_views=500,
                    rate_kopecks_per_view=5,
                    amount_kopecks=2500,
                    adjustment_kopecks=0,
                    risk_flags=["large_growth"],
                ),
                CreatorBalance(
                    blogger_id=blogger.id,
                    available_kopecks=200,
                    reserved_kopecks=100,
                    paid_kopecks=300,
                ),
            ]
        )
        db.commit()
    return period


def test_creator_dashboard_uses_readings_billing_and_balance(client):
    period = _seed_dashboard_data(client)
    _login(client, "dashboard-blogger@example.com")

    response = client.get("/api/v1/me/dashboard")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["period"] == period.isoformat()
    assert body["content"] == {
        "active_video_cards": 1,
        "active_publications": 1,
        "by_platform": [{"platform": "vk", "publications": 1}],
    }
    assert body["views"]["total_views"] == 1500
    assert body["views"]["current_period_new_views"] == 500
    assert body["views"]["basis"] == "preliminary"
    assert body["finance"] == {
        "preliminary_kopecks": 2500,
        "available_kopecks": 200,
        "reserved_kopecks": 100,
        "paid_kopecks": 300,
    }
    assert body["attention"]["missing_manual_readings"] == 0
    assert body["top_video_cards"][0]["total_views"] == 1500


def test_staff_dashboard_and_analytics_are_available_to_analyst(client):
    period = _seed_dashboard_data(client)
    _login(client, "dashboard-analyst@example.com")

    dashboard = client.get("/api/v1/staff/dashboard")
    analytics = client.get(
        "/api/v1/staff/analytics",
        params={"periodFrom": period.isoformat(), "periodTo": period.isoformat()},
    )

    assert dashboard.status_code == 200
    assert dashboard.json()["overview"]["active_bloggers"] == 1
    assert dashboard.json()["overview"]["active_publications"] == 1
    assert dashboard.json()["overview"]["preliminary_accrual_kopecks"] == 2500
    assert analytics.status_code == 200
    body = analytics.json()
    assert body["overview"]["views"] == 500
    assert body["overview"]["accrual_kopecks"] == 2500
    assert body["overview"]["average_video_cost_kopecks"] == 2500
    assert body["by_brand"][0]["key"] == "AMP"
    assert body["by_platform"][0]["key"] == "vk"
    assert body["top_bloggers"][0]["label"] == "Dashboard creator"
    assert body["risks"]["suspicious_accruals"] == 1


def test_blogger_cannot_read_staff_dashboard(client):
    _seed_dashboard_data(client)
    _login(client, "dashboard-blogger@example.com")

    assert client.get("/api/v1/staff/dashboard").status_code == 403
    assert client.get("/api/v1/staff/analytics").status_code == 403


def test_staff_analytics_validates_period_range(client):
    _seed_dashboard_data(client)
    _login(client, "dashboard-analyst@example.com")

    response = client.get(
        "/api/v1/staff/analytics?periodFrom=2026-09-02&periodTo=2026-08-01"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ANALYTICS_PERIOD"
