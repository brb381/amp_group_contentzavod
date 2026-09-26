import argparse
import hashlib
from datetime import datetime, timedelta, timezone

from app.audit.service import AuditContext
from app.auth.models import Role
from app.auth.security import utc_now
from app.billing.models import (
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.catalog.models import Brand, Product
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationHistory,
    PublicationStatus,
    VideoCard,
)
from app.content.url_parser import parse_publication_url
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    ProfileStatus,
    RecipientStatus,
    SocialAccount,
    SocialAccountHistory,
    SocialAccountStatus,
)
from app.database.session import SessionLocal
from app.legal.models import LegalDocumentType
from app.legal.service import accept_registration_documents, get_current_documents
from app.lifecycle.models import ActivityKind, CreatorLifecycle
from app.payouts.models import PayoutDetails, PayoutRequest, PayoutStatus, RecipientType
from app.platforms import Platform
from app.program.models import ProgramSettings
from app.readings.models import ReadingSource, ReadingStatus, ViewReading, ViewReadingHistory
from app.readings.policy import reporting_period
from app.support.models import SupportCategory, SupportMessage, SupportStatus, SupportTicket

from app.cli.seed_demo import (
    DEFAULT_PASSWORD,
    ensure_legal_documents,
    stable_uuid,
    upsert_users,
)


PRODUCTS = (
    {
        "brand": Brand.AMP,
        "model_name": "AMP Studio One",
        "publication_name": "Набор для создания вертикального контента AMP Studio One",
        "sku": "AMP-STUDIO-ONE",
        "hashtags": ["#ampgroup", "#контент"],
        "hint": "Покажите сценарий использования и итоговый результат без постановочных обещаний.",
    },
    {
        "brand": Brand.AIRTONE,
        "model_name": "AirTone Pulse",
        "publication_name": "Беспроводные наушники AirTone Pulse",
        "sku": "AIRTONE-PULSE",
        "hashtags": ["#airtone", "#обзортехники"],
        "hint": "Расскажите о посадке, управлении и повседневном использовании.",
    },
    {
        "brand": Brand.CRIOLIGHT,
        "model_name": "CrioLight Care",
        "publication_name": "Устройство домашнего ухода CrioLight Care",
        "sku": "CRIOLIGHT-CARE",
        "hashtags": ["#criolight", "#уход"],
        "hint": "Покажите безопасный бытовой сценарий без медицинских заявлений.",
    },
)


PUBLICATIONS = (
    {
        "platform": Platform.YOUTUBE,
        "account_url": "https://www.youtube.com/@Samsung",
        "followers": 10_200_000,
        "url": "https://www.youtube.com/watch?v=ZyV8Xnvw_f0",
        "title": "Galaxy AI: Look familiar? Look closer",
        "author": "Samsung",
        "published_at": datetime(2026, 8, 19, tzinfo=timezone.utc),
        "duration": 30,
        "product_sku": "AIRTONE-PULSE",
        "card_title": "Короткий технологический обзор: динамичная подача",
        "description": "Реальный публичный YouTube-источник для проверки карточки, статистики и модерации.",
        "status": PublicationStatus.APPROVED,
        "baseline": 15_000_000,
        "baseline_source": ReadingSource.YOUTUBE_API,
    },
    {
        "platform": Platform.YOUTUBE,
        "account_url": "https://www.youtube.com/@Samsung",
        "followers": 10_200_000,
        "url": "https://www.youtube.com/watch?v=_1U0a2STwIw",
        "title": "Galaxy S26 Series: quick feature recap",
        "author": "Samsung",
        "published_at": datetime(2026, 2, 25, tzinfo=timezone.utc),
        "duration": 142,
        "product_sku": "AMP-STUDIO-ONE",
        "card_title": "Развернутый обзор продукта с демонстрацией функций",
        "description": "Реальный публичный YouTube-источник с несколькими продуктовыми сценами.",
        "status": PublicationStatus.APPROVED,
        "baseline": 34_800_000,
        "baseline_source": ReadingSource.YOUTUBE_API,
    },
    {
        "platform": Platform.TIKTOK,
        "thumbnail_url": "/assets/showcase-tiktok.jpg",
        "account_url": "https://www.tiktok.com/@scout2015",
        "followers": 1_500_000,
        "url": "https://www.tiktok.com/@scout2015/video/6718335390845095173",
        "title": "Scramble up your name and I will try to guess it",
        "author": "Scout & Suki",
        "published_at": datetime(2019, 7, 27, tzinfo=timezone.utc),
        "duration": 15,
        "product_sku": "CRIOLIGHT-CARE",
        "card_title": "TikTok: нативный вертикальный формат",
        "description": "Публичный пример из документации TikTok Embed API для проверки парсинга и сбора счетчика.",
        "status": PublicationStatus.APPROVED,
        "baseline": 150_000,
        "baseline_source": ReadingSource.TIKTOK_PUBLIC,
    },
    {
        "platform": Platform.VK,
        "thumbnail_url": "/assets/showcase-vk.jpg",
        "account_url": "https://vk.com/evrialgaming",
        "followers": 1_380,
        "url": "https://vk.com/video-119345534_456247325",
        "title": "Мой топ и обзор MMORPG 2025-2026",
        "author": "Evrial",
        "published_at": datetime(2025, 12, 1, tzinfo=timezone.utc),
        "duration": 900,
        "product_sku": "AMP-STUDIO-ONE",
        "card_title": "VK Видео: обзорный формат",
        "description": "Публичный VK-источник для проверки доступности и приблизительного счетчика просмотров.",
        "status": PublicationStatus.APPROVED,
        "baseline": 12_400,
        "baseline_source": ReadingSource.VK_PUBLIC,
    },
    {
        "platform": Platform.RUTUBE,
        "thumbnail_url": "/assets/showcase-rutube.jpg",
        "account_url": "https://rutube.ru/channel/23952456/",
        "followers": 1_100,
        "url": "https://rutube.ru/video/be034b80d968da98c0faf371c5090d73/",
        "title": "ТОП-8: лучшие фен-щётки для волос 2025 года",
        "author": "ТехРевизор",
        "published_at": datetime(2025, 6, 4, tzinfo=timezone.utc),
        "duration": 561,
        "product_sku": "AMP-STUDIO-ONE",
        "card_title": "RUTUBE: ручное показание по публикации",
        "description": "Реальный публичный RUTUBE-источник; просмотры ведутся вручную по правилам текущего контура.",
        "status": PublicationStatus.APPROVED,
        "baseline": 4_900,
        "current": 6_420,
        "baseline_source": ReadingSource.RUTUBE_PUBLIC,
    },
)


def product_snapshot(product: Product) -> dict:
    return {
        "brand": product.brand.value,
        "model_name": product.model_name,
        "publication_name": product.publication_name,
        "sku": product.sku,
        "required_hashtags": product.required_hashtags,
    }


def seed_products(db) -> dict[str, Product]:
    result = {}
    for item in PRODUCTS:
        product = Product(
            brand=item["brand"],
            model_name=item["model_name"],
            publication_name=item["publication_name"],
            sku=item["sku"],
            normalized_name=item["model_name"].casefold(),
            normalized_sku=item["sku"].casefold(),
            required_hashtags=item["hashtags"],
            content_hint=item["hint"],
            marketplace_links=[
                {"label": "Ozon", "url": "https://www.ozon.ru/"},
                {"label": "Wildberries", "url": "https://www.wildberries.ru/"},
            ],
            is_active=True,
        )
        db.add(product)
        db.flush()
        result[item["sku"]] = product
    return result


def seed_profile(db, users, now):
    blogger = users[Role.BLOGGER]
    documents = get_current_documents(db)
    accept_registration_documents(
        db,
        user=blogger,
        program_terms_document_id=documents[LegalDocumentType.PROGRAM_TERMS].id,
        personal_data_consent_document_id=documents[LegalDocumentType.PERSONAL_DATA_CONSENT].id,
        audit_context=AuditContext(
            request_id="cli:showcase-seed",
            ip_address="local",
            user_agent="seed-showcase",
        ),
    )
    profile = CreatorProfile(
        user_id=blogger.id,
        full_name="Мария Волкова (демо)",
        display_name="Мария | техника и lifestyle",
        phone="+7 900 000-00-01",
        telegram="@amp_showcase",
        city_country="Москва, Россия",
        content_topics="Техника, lifestyle, обзоры продуктов, короткие видео",
        recipient_status=RecipientStatus.SELF_EMPLOYED,
        status=ProfileStatus.APPROVED,
        submitted_at=now - timedelta(days=45),
        reviewed_at=now - timedelta(days=44),
    )
    db.add(profile)
    db.flush()
    db.add(
        ProfileHistory(
            profile_id=profile.id,
            actor_user_id=users[Role.MODERATOR].id,
            event_type="approved",
            from_status=ProfileStatus.IN_REVIEW.value,
            to_status=ProfileStatus.APPROVED.value,
            reason="Демонстрационный профиль проверен",
            changes={},
        )
    )
    db.add(
        CreatorLifecycle(
            blogger_id=blogger.id,
            last_activity_at=now,
            last_activity_kind=ActivityKind.PUBLICATION_CHANGED,
            activity_revision=1,
        )
    )
    return blogger


def seed_accounts(db, blogger, users):
    accounts = {}
    for spec in PUBLICATIONS:
        platform = spec["platform"]
        if platform in accounts:
            continue
        account = SocialAccount(
            user_id=blogger.id,
            platform=platform,
            url=spec["account_url"],
            follower_count=spec["followers"],
            status=SocialAccountStatus.APPROVED,
        )
        db.add(account)
        db.flush()
        db.add(
            SocialAccountHistory(
                social_account_id=account.id,
                actor_user_id=users[Role.MODERATOR].id,
                event_type="approved",
                from_status=SocialAccountStatus.PENDING.value,
                to_status=SocialAccountStatus.APPROVED.value,
                reason="Публичный демонстрационный источник",
            )
        )
        accounts[platform] = account
    return accounts


def add_reading(db, *, publication, period, value, source, key, captured_at, accepted, actor_id=None):
    reading = ViewReading(
        publication_id=publication.id,
        reporting_period=period,
        source=source,
        reported_value=value,
        accepted_value=value if accepted else None,
        status=ReadingStatus.ACCEPTED if accepted else ReadingStatus.PENDING,
        risk_flags=[] if source in {ReadingSource.YOUTUBE_API, ReadingSource.MANUAL} else ["approximate_public_counter"],
        idempotency_key=key,
        captured_at=captured_at,
        submitted_by_user_id=actor_id if source == ReadingSource.MANUAL else None,
        reviewed_by_user_id=actor_id if accepted and source == ReadingSource.MANUAL else None,
        reviewed_at=captured_at if accepted and source == ReadingSource.MANUAL else None,
        review_reason="Демонстрационный снимок публичного счетчика" if accepted else None,
    )
    db.add(reading)
    db.flush()
    db.add(
        ViewReadingHistory(
            reading_id=reading.id,
            action="auto_accepted" if accepted else "created",
            new_value=value,
            reason="Начальное showcase-показание",
            actor_user_id=actor_id,
        )
    )
    return reading


def seed_publications(db, blogger, users, products, accounts, now):
    current_period = reporting_period(now)
    previous_period = (current_period - timedelta(days=1)).replace(day=1)
    seeded = []
    for index, spec in enumerate(PUBLICATIONS, 1):
        product = products[spec["product_sku"]]
        card = VideoCard(
            blogger_id=blogger.id,
            title=spec["card_title"],
            description=spec["description"],
            product_id=product.id,
            product_snapshot=product_snapshot(product),
        )
        db.add(card)
        db.flush()
        parsed = parse_publication_url(spec["platform"], spec["url"])
        publication = Publication(
            video_card_id=card.id,
            social_account_id=accounts[spec["platform"]].id,
            platform=spec["platform"],
            submitted_url=spec["url"],
            normalized_url=parsed.normalized_url,
            external_id=parsed.external_id,
            status=spec["status"],
            parse_status=parsed.parse_status,
            availability=PublicationAvailability.AVAILABLE,
            enrichment_status=PublicationEnrichmentStatus.SUCCEEDED,
            external_title=spec["title"],
            external_author_name=spec["author"],
            external_published_at=spec["published_at"],
            external_duration_seconds=spec["duration"],
            external_thumbnail_url=spec.get("thumbnail_url") or (
                f"https://i.ytimg.com/vi/{parsed.external_id}/hqdefault.jpg"
                if spec["platform"] == Platform.YOUTUBE
                else None
            ),
            enriched_at=now - timedelta(days=7 - min(index, 6)),
            submitted_at=now - timedelta(days=8 - min(index, 6)),
            reviewed_at=now - timedelta(days=7 - min(index, 6)),
        )
        db.add(publication)
        db.flush()
        db.add(
            PublicationHistory(
                publication_id=publication.id,
                actor_user_id=users[Role.MODERATOR].id,
                event_type="approved",
                from_status=PublicationStatus.PENDING_REVIEW.value,
                to_status=PublicationStatus.APPROVED.value,
                reason="Публичный источник проверен для showcase-набора",
                changes={"source": "public_showcase", "platform": spec["platform"].value},
            )
        )
        baseline = add_reading(
            db,
            publication=publication,
            period=previous_period,
            value=spec["baseline"],
            source=spec["baseline_source"],
            key=f"showcase-baseline-{index}",
            captured_at=now - timedelta(days=35),
            accepted=True,
            actor_id=users[Role.MANAGER].id,
        )
        current = None
        if "current" in spec:
            current = add_reading(
                db,
                publication=publication,
                period=current_period,
                value=spec["current"],
                source=spec["baseline_source"],
                key=f"showcase-current-{index}",
                captured_at=now - timedelta(days=1),
                accepted=True,
                actor_id=users[Role.MANAGER].id,
            )
        seeded.append((publication, baseline, current))
    return seeded


def seed_finance(db, blogger, users, seeded, now):
    period = reporting_period(now)
    rate = RateVersion(
        rate_kopecks_per_view=5,
        effective_from_period=period,
        created_by_user_id=users[Role.ANALYST].id,
        reason="Showcase-ставка: 0,05 рубля за подтвержденный просмотр",
    )
    db.add(rate)
    db.flush()
    rutube_publication, previous, current = seeded[-1]
    eligible_views = current.accepted_value - previous.accepted_value
    amount = eligible_views * rate.rate_kopecks_per_view
    calculation = CalculationPeriod(
        period=period,
        status=CalculationPeriodStatus.PRELIMINARY,
        rate_version_id=rate.id,
        input_revision=1,
        calculated_at=now,
        total_views=eligible_views,
        total_amount_kopecks=amount,
    )
    db.add(calculation)
    db.flush()
    db.add(
        PublicationAccrual(
            period_id=calculation.id,
            publication_id=rutube_publication.id,
            blogger_id=blogger.id,
            previous_reading_id=previous.id,
            previous_value=previous.accepted_value,
            current_reading_id=current.id,
            current_value=current.accepted_value,
            eligible_views=eligible_views,
            rate_kopecks_per_view=rate.rate_kopecks_per_view,
            amount_kopecks=amount,
            adjustment_kopecks=0,
            risk_flags=[],
        )
    )
    db.add(
        CreatorPeriodTotal(
            period_id=calculation.id,
            blogger_id=blogger.id,
            eligible_views=eligible_views,
            amount_kopecks=amount,
            adjustment_kopecks=0,
            publication_count=1,
            risk_count=0,
        )
    )
    db.add(
        CreatorBalance(
            blogger_id=blogger.id,
            available_kopecks=184_500,
            reserved_kopecks=75_000,
            paid_kopecks=620_000,
        )
    )
    db.add(PayoutDetails(blogger_id=blogger.id, sbp_phone="+7 900 000-00-01", bank_name="Демо Банк"))
    db.add(
        PayoutRequest(
            request_number="AMP-SHOWCASE-0001",
            blogger_id=blogger.id,
            amount_kopecks=75_000,
            status=PayoutStatus.REQUESTED,
            recipient_full_name="Мария Волкова (демо)",
            recipient_display_name="Мария | техника и lifestyle",
            recipient_type=RecipientType.SELF_EMPLOYED,
            sbp_phone="+7 900 000-00-01",
            bank_name="Демо Банк",
            manager_comment="Showcase-заявка: проверить реквизиты и статус самозанятого",
        )
    )


def seed_support(db, blogger, users):
    ticket = SupportTicket(
        ticket_number="AMP-SHOWCASE-SUPPORT-1",
        blogger_id=blogger.id,
        category=SupportCategory.CONTENT,
        subject="Уточнение по статистике TikTok и VK",
        status=SupportStatus.IN_PROGRESS,
        assigned_to_user_id=users[Role.MANAGER].id,
        creation_idempotency_key=stable_uuid("showcase-support-create"),
        creation_payload_hash=hashlib.sha256(b"showcase-support-create").hexdigest(),
    )
    db.add(ticket)
    db.flush()
    messages = (
        (blogger.id, Role.BLOGGER.value, "Подскажите, почему счетчики TikTok и VK отмечены как приблизительные?"),
        (users[Role.MANAGER].id, Role.MANAGER.value, "Это публичные счетчики. Перед начислением показание проходит проверку менеджера."),
    )
    for index, (author_id, role, body) in enumerate(messages, 1):
        db.add(
            SupportMessage(
                ticket_id=ticket.id,
                author_user_id=author_id,
                author_role=role,
                body=body,
                idempotency_key=stable_uuid(f"showcase-support-message-{index}"),
                payload_hash=hashlib.sha256(body.encode()).hexdigest(),
            )
        )


def seed_program_settings(db, admin_id):
    settings = db.get(ProgramSettings, 1)
    settings.program_name = "AMP Content Factory"
    settings.main_text = "Рабочий кабинет контент-программы AMP Group"
    settings.manager_name = "Команда контент-программы"
    settings.manager_email = "content@ampgroup.pro"
    settings.manager_phone = "+7 900 000-00-00"
    settings.manager_telegram_url = "https://t.me/amp_group"
    settings.program_details = "Демонстрационная среда с публичными видеоссылками и зафиксированными снимками статистики."
    settings.service_signature = "AMP Group Content Team"
    settings.suspicious_growth_threshold = 500_000
    settings.random_review_percent = 10
    settings.rejection_reasons = [
        "Не указан рекламный характер публикации",
        "Публикация недоступна по ссылке",
        "Товар в ролике не соответствует карточке",
        "Требуется исправить описание или хештеги",
    ]
    settings.updated_by_user_id = admin_id


def seed_showcase(password: str) -> None:
    now = utc_now()
    with SessionLocal() as db:
        ensure_legal_documents(db)
        users = upsert_users(db, password)
        products = seed_products(db)
        blogger = seed_profile(db, users, now)
        accounts = seed_accounts(db, blogger, users)
        seeded = seed_publications(db, blogger, users, products, accounts, now)
        seed_finance(db, blogger, users, seeded, now)
        seed_support(db, blogger, users)
        seed_program_settings(db, users[Role.ADMIN].id)
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create realistic local showcase accounts and content")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()
    if len(args.password) < 12:
        parser.error("password must contain at least 12 characters")
    seed_showcase(args.password)
    print("Showcase accounts and public video data are ready")


if __name__ == "__main__":
    main()
