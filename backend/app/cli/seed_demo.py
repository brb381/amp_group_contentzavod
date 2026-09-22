import argparse
import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.audit.service import AuditContext
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import hash_password, utc_now
from app.billing.models import CalculationPeriod, CalculationPeriodStatus, CreatorBalance, CreatorPeriodTotal, PublicationAccrual, RateVersion
from app.catalog.models import Brand
from app.content.models import Publication, PublicationAvailability, PublicationEnrichmentStatus, PublicationParseStatus, PublicationStatus, VideoCard
from app.creators.models import CreatorProfile, ProfileStatus, RecipientStatus, SocialAccount, SocialAccountStatus
from app.database.session import SessionLocal
from app.legal.models import LegalDocument, LegalDocumentType
from app.legal.service import accept_registration_documents, get_current_documents
from app.lifecycle.models import ActivityKind, CreatorLifecycle
from app.payouts.models import PayoutDetails, PayoutRequest, PayoutStatus, RecipientType
from app.platforms import Platform
from app.readings.models import ReadingSource, ReadingStatus, ViewReading
from app.readings.policy import reporting_period
from app.support.models import SupportCategory, SupportMessage, SupportStatus, SupportTicket


DEFAULT_PASSWORD = "AmpDemoRole!2026"
DEMO_USERS = {
    Role.BLOGGER: "demo-blogger@ampgroup.pro",
    Role.MODERATOR: "demo-moderator@ampgroup.pro",
    Role.MANAGER: "demo-manager@ampgroup.pro",
    Role.FINANCE: "demo-finance@ampgroup.pro",
    Role.ANALYST: "demo-analyst@ampgroup.pro",
    Role.ADMIN: "demo-admin@ampgroup.pro",
}


def stable_uuid(value):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"amp-demo:{value}")



def ensure_legal_documents(db):
    documents = {
        LegalDocumentType.PROGRAM_TERMS: ("Условия программы", "Условия участия в демонстрационной среде AMP."),
        LegalDocumentType.PERSONAL_DATA_CONSENT: ("Согласие на обработку данных", "Согласие для локальной демонстрационной среды AMP."),
        LegalDocumentType.PRIVACY_POLICY: ("Политика конфиденциальности", "Политика для локальной демонстрационной среды AMP."),
    }
    for document_type, (title, content) in documents.items():
        current = db.scalar(select(LegalDocument).where(
            LegalDocument.document_type == document_type,
            LegalDocument.is_current.is_(True),
        ))
        if current is not None:
            continue
        revision = (db.scalar(select(LegalDocument.revision).where(
            LegalDocument.document_type == document_type,
        ).order_by(LegalDocument.revision.desc()).limit(1)) or 0) + 1
        db.add(LegalDocument(
            document_type=document_type,
            version=f"demo-{revision}",
            revision=revision,
            title=title,
            content_markdown=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            is_current=True,
            requires_reacceptance=False,
            change_summary="Local demonstration document",
        ))
    db.flush()
def upsert_users(db, password):
    now = utc_now()
    users = {}
    for role, email in DEMO_USERS.items():
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            db.add(user)
        user.password_hash = hash_password(password)
        user.role = role
        user.status = AccountStatus.ACTIVE
        user.email_verified_at = user.email_verified_at or now
        user.status_changed_at = now
        user.status_reason = "Local demonstration account"
        user.status_before_block = None
        user.deleted_at = None
        db.flush()
        db.query(RefreshSession).filter(RefreshSession.user_id == user.id).delete()
        users[role] = user
    return users


def seed_creator(db, users):
    now = utc_now()
    blogger = users[Role.BLOGGER]
    documents = get_current_documents(db)
    accept_registration_documents(
        db,
        user=blogger,
        program_terms_document_id=documents[LegalDocumentType.PROGRAM_TERMS].id,
        personal_data_consent_document_id=documents[LegalDocumentType.PERSONAL_DATA_CONSENT].id,
        audit_context=AuditContext(
            request_id="cli:demo-seed",
            ip_address="local",
            user_agent="seed-demo",
        ),
    )
    if db.get(CreatorLifecycle, blogger.id) is None:
        db.add(CreatorLifecycle(
            blogger_id=blogger.id, last_activity_at=now,
            last_activity_kind=ActivityKind.ACCOUNT_CREATED, activity_revision=1,
        ))
    profile = db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == blogger.id))
    if profile is None:
        profile = CreatorProfile(user_id=blogger.id)
        db.add(profile)
    profile.full_name = "Анна Сергеевна Демидова"
    profile.display_name = "Anna AMP"
    profile.phone = "+7 999 000-11-22"
    profile.telegram = "@anna_amp_demo"
    profile.city_country = "Красноярск, Россия"
    profile.content_topics = "Красота, lifestyle, обзоры продуктов"
    profile.recipient_status = RecipientStatus.SELF_EMPLOYED
    profile.status = ProfileStatus.APPROVED
    profile.submitted_at = profile.submitted_at or now - timedelta(days=14)
    profile.reviewed_at = profile.reviewed_at or now - timedelta(days=13)

    account = db.scalar(select(SocialAccount).where(
        SocialAccount.user_id == blogger.id,
        SocialAccount.url == "https://vk.com/amp_demo_creator",
    ))
    if account is None:
        account = SocialAccount(user_id=blogger.id, platform=Platform.VK, url="https://vk.com/amp_demo_creator")
        db.add(account)
    account.follower_count = 42800
    account.status = SocialAccountStatus.APPROVED
    account.deleted_at = None
    db.flush()

    specs = (
        ("Утренний уход с AMP Air", "AMP Air", PublicationStatus.APPROVED),
        ("Тестируем AMP Glow", "AMP Glow", PublicationStatus.PENDING_REVIEW),
        ("Неделя с AMP Balance", "AMP Balance", PublicationStatus.CHANGES_REQUIRED),
    )
    publications = []
    for index, (title, product, status) in enumerate(specs, 1):
        card = db.scalar(select(VideoCard).where(VideoCard.blogger_id == blogger.id, VideoCard.title == title))
        if card is None:
            card = VideoCard(
                blogger_id=blogger.id, title=title,
                description="Демонстрационная карточка контента",
                reported_brand=Brand.AMP, reported_product_name=product,
            )
            db.add(card)
            db.flush()
        url = f"https://vk.com/clip-demo-{index:03d}"
        publication = db.scalar(select(Publication).where(Publication.normalized_url == url))
        if publication is None:
            publication = Publication(
                video_card_id=card.id, social_account_id=account.id, platform=Platform.VK,
                submitted_url=url, normalized_url=url, external_id=f"demo-{index:03d}",
                parse_status=PublicationParseStatus.PARSED,
            )
            db.add(publication)
        publication.status = status
        publication.availability = PublicationAvailability.AVAILABLE
        publication.enrichment_status = PublicationEnrichmentStatus.NOT_REQUESTED
        publication.external_title = title
        publication.external_author_name = "Anna AMP"
        publication.external_published_at = now - timedelta(days=11 - index)
        publication.submitted_at = publication.submitted_at or now - timedelta(days=11 - index)
        publication.reviewed_at = now - timedelta(days=9) if status == PublicationStatus.APPROVED else None
        publication.moderation_reason = "Добавьте маркировку рекламного материала" if status == PublicationStatus.CHANGES_REQUIRED else None
        db.flush()
        publications.append(publication)

    period = reporting_period(now)
    specs = (
        (publications[0], (period - timedelta(days=1)).replace(day=1), 18400, 18400, ReadingStatus.ACCEPTED, [], "baseline"),
        (publications[0], period, 26750, 26750, ReadingStatus.ACCEPTED, ["large_growth"], "current"),
        (publications[1], period, 9200, None, ReadingStatus.PENDING, [], "pending"),
    )
    readings = []
    for publication, reading_period, reported, accepted, status, flags, suffix in specs:
        key = f"amp-demo-{suffix}"
        reading = db.scalar(select(ViewReading).where(
            ViewReading.publication_id == publication.id, ViewReading.idempotency_key == key,
        ))
        if reading is None:
            reading = ViewReading(
                publication_id=publication.id, reporting_period=reading_period,
                source=ReadingSource.MANUAL, reported_value=reported, accepted_value=accepted,
                status=status, risk_flags=flags, idempotency_key=key,
                captured_at=now - timedelta(days=30 if suffix == "baseline" else 1),
                submitted_by_user_id=blogger.id,
                reviewed_by_user_id=users[Role.MANAGER].id if status == ReadingStatus.ACCEPTED else None,
                reviewed_at=now if status == ReadingStatus.ACCEPTED else None,
                review_reason="Demo dataset" if status == ReadingStatus.ACCEPTED else None,
            )
            db.add(reading)
        readings.append(reading)
    db.flush()
    return blogger, publications, readings


def seed_finance(db, users, blogger, publications, readings):
    now = utc_now()
    period = reporting_period(now)
    rate = db.scalar(select(RateVersion).where(RateVersion.effective_from_period == period))
    if rate is None:
        rate = RateVersion(
            rate_kopecks_per_view=5, effective_from_period=period,
            created_by_user_id=users[Role.ANALYST].id, reason="Demonstration rate",
        )
        db.add(rate)
        db.flush()
    calculation = db.scalar(select(CalculationPeriod).where(CalculationPeriod.period == period))
    if calculation is None:
        calculation = CalculationPeriod(
            period=period, status=CalculationPeriodStatus.PRELIMINARY,
            rate_version_id=rate.id, input_revision=1, calculated_at=now,
            total_views=8350, total_amount_kopecks=41750,
        )
        db.add(calculation)
        db.flush()
    if db.scalar(select(CreatorPeriodTotal).where(
        CreatorPeriodTotal.period_id == calculation.id, CreatorPeriodTotal.blogger_id == blogger.id,
    )) is None:
        db.add(CreatorPeriodTotal(
            period_id=calculation.id, blogger_id=blogger.id, eligible_views=8350,
            amount_kopecks=41750, adjustment_kopecks=0, publication_count=1, risk_count=1,
        ))
    if db.scalar(select(PublicationAccrual).where(
        PublicationAccrual.period_id == calculation.id,
        PublicationAccrual.publication_id == publications[0].id,
    )) is None:
        db.add(PublicationAccrual(
            period_id=calculation.id, publication_id=publications[0].id, blogger_id=blogger.id,
            previous_reading_id=readings[0].id, previous_value=18400,
            current_reading_id=readings[1].id, current_value=26750,
            eligible_views=8350, rate_kopecks_per_view=5, amount_kopecks=41750,
            adjustment_kopecks=0, risk_flags=["large_growth"],
        ))
    balance = db.get(CreatorBalance, blogger.id)
    if balance is None:
        balance = CreatorBalance(blogger_id=blogger.id)
        db.add(balance)
    balance.available_kopecks = 325000
    balance.reserved_kopecks = 125000
    balance.paid_kopecks = 780000

    if db.get(PayoutDetails, blogger.id) is None:
        db.add(PayoutDetails(blogger_id=blogger.id, sbp_phone="+7 999 000-11-22", bank_name="Демо Банк"))
    if db.scalar(select(PayoutRequest).where(PayoutRequest.request_number == "AMP-DEMO-0001")) is None:
        db.add(PayoutRequest(
            request_number="AMP-DEMO-0001", blogger_id=blogger.id, amount_kopecks=125000,
            status=PayoutStatus.REQUESTED, recipient_full_name="Анна Сергеевна Демидова",
            recipient_display_name="Анна Демидова", recipient_type=RecipientType.SELF_EMPLOYED,
            sbp_phone="+7 999 000-11-22", bank_name="Демо Банк", manager_comment="Проверить реквизиты",
        ))


def seed_support(db, users, blogger):
    if db.scalar(select(SupportTicket).where(SupportTicket.ticket_number == "AMP-DEMO-SUPPORT-1")):
        return
    ticket = SupportTicket(
        ticket_number="AMP-DEMO-SUPPORT-1", blogger_id=blogger.id,
        category=SupportCategory.PAYMENT, subject="Когда поступит выплата?",
        status=SupportStatus.IN_PROGRESS, assigned_to_user_id=users[Role.MANAGER].id,
        creation_idempotency_key=stable_uuid("support-create"),
        creation_payload_hash=hashlib.sha256(b"support-create").hexdigest(),
    )
    db.add(ticket)
    db.flush()
    db.add_all([
        SupportMessage(
            ticket_id=ticket.id, author_user_id=blogger.id, author_role=Role.BLOGGER.value,
            body="Подскажите, пожалуйста, статус моей выплаты.",
            idempotency_key=stable_uuid("support-message-blogger"),
            payload_hash=hashlib.sha256(b"support-message-blogger").hexdigest(),
        ),
        SupportMessage(
            ticket_id=ticket.id, author_user_id=users[Role.MANAGER].id, author_role=Role.MANAGER.value,
            body="Проверяем реквизиты, вернёмся с ответом сегодня.",
            idempotency_key=stable_uuid("support-message-manager"),
            payload_hash=hashlib.sha256(b"support-message-manager").hexdigest(),
        ),
    ])


def seed_demo(password):
    with SessionLocal() as db:
        ensure_legal_documents(db)
        users = upsert_users(db, password)
        blogger, publications, readings = seed_creator(db, users)
        seed_finance(db, users, blogger, publications, readings)
        seed_support(db, users, blogger)
        db.commit()


def main():
    parser = argparse.ArgumentParser(description="Create local demonstration accounts and data")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()
    if len(args.password) < 12:
        parser.error("password must contain at least 12 characters")
    seed_demo(args.password)
    print("Demo accounts and data are ready")


if __name__ == "__main__":
    main()