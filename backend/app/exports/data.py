from datetime import datetime, time, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased

from app.audit.models import SecurityEvent
from app.auth.models import Role, User
from app.billing.models import CalculationPeriod, PublicationAccrual
from app.catalog.models import Product
from app.content.models import Publication, PublicationHistory, VideoCard
from app.creators.models import (
    CreatorProfile,
    ProfileHistory,
    SocialAccount,
    SocialAccountHistory,
    SocialAccountStatus,
)
from app.exports.models import ExportType
from app.exports.schemas import DataExportFilters
from app.payouts.policy import MOSCOW
from app.readings.models import ViewReading, ViewReadingHistory
from app.support.models import SupportMessage, SupportTicket


MAX_EXPORT_ROWS = 10_000


class ExportTooLargeError(Exception):
    pass


def _utc_bounds(filters: DataExportFilters) -> tuple[datetime, datetime]:
    start = datetime.combine(filters.date_from, time.min, tzinfo=MOSCOW)
    end = datetime.combine(
        filters.date_to + timedelta(days=1), time.min, tzinfo=MOSCOW
    )
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _limited(db: Session, statement) -> list:
    rows = list(db.execute(statement.limit(MAX_EXPORT_ROWS + 1)))
    if len(rows) > MAX_EXPORT_ROWS:
        raise ExportTooLargeError
    return rows


def _product_values(card: VideoCard, product: Product | None) -> tuple[str, str]:
    brand = product.brand if product else card.reported_brand
    name = product.publication_name if product else card.reported_product_name
    return (brand.value if brand else "", name or "")


def _content_filters(filters, *, publication=Publication, card=VideoCard, product=Product):
    clauses = []
    if filters.blogger_id:
        clauses.append(card.blogger_id == filters.blogger_id)
    if filters.platform:
        clauses.append(publication.platform == filters.platform)
    if filters.product_id:
        clauses.append(card.product_id == filters.product_id)
    if filters.brand:
        clauses.append(
            or_(
                product.brand == filters.brand,
                and_(product.id.is_(None), card.reported_brand == filters.brand),
            )
        )
    return clauses


def _load_bloggers(db: Session, filters: DataExportFilters) -> list[dict]:
    start, end = _utc_bounds(filters)
    clauses = [User.role == Role.BLOGGER, User.created_at >= start, User.created_at < end]
    if filters.blogger_id:
        clauses.append(User.id == filters.blogger_id)
    if filters.status:
        clauses.append(User.status == filters.status)
    rows = _limited(
        db,
        select(User, CreatorProfile)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == User.id)
        .where(*clauses)
        .order_by(User.created_at, User.id),
    )
    return [
        {
            "blogger_id": user.id,
            "email": user.email,
            "full_name": profile.full_name if profile else None,
            "display_name": profile.display_name if profile else None,
            "phone": profile.phone if profile else None,
            "telegram": profile.telegram if profile else None,
            "city_country": profile.city_country if profile else None,
            "recipient_status": profile.recipient_status if profile else None,
            "account_status": user.status,
            "profile_status": profile.status if profile else None,
            "created_at": user.created_at,
        }
        for user, profile in rows
    ]


def _load_social_accounts(db: Session, filters: DataExportFilters) -> list[dict]:
    start, end = _utc_bounds(filters)
    clauses = [
        SocialAccount.created_at >= start,
        SocialAccount.created_at < end,
        SocialAccount.status == SocialAccountStatus.APPROVED,
        SocialAccount.deleted_at.is_(None),
    ]
    if filters.blogger_id:
        clauses.append(SocialAccount.user_id == filters.blogger_id)
    if filters.platform:
        clauses.append(SocialAccount.platform == filters.platform)
    if filters.status:
        clauses.append(SocialAccount.status == filters.status)
    rows = _limited(
        db,
        select(SocialAccount, CreatorProfile)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == SocialAccount.user_id)
        .where(*clauses)
        .order_by(SocialAccount.created_at, SocialAccount.id),
    )
    return [
        {
            "account_id": account.id,
            "blogger_id": account.user_id,
            "blogger": profile.display_name if profile else None,
            "platform": account.platform,
            "url": account.url,
            "follower_count": account.follower_count,
            "status": account.status,
            "updated_at": account.updated_at,
        }
        for account, profile in rows
    ]


def _publication_statement(filters: DataExportFilters):
    start, end = _utc_bounds(filters)
    clauses = [Publication.created_at >= start, Publication.created_at < end]
    clauses.extend(_content_filters(filters))
    if filters.status:
        clauses.append(Publication.status == filters.status)
    return (
        select(Publication, VideoCard, CreatorProfile, Product)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == VideoCard.blogger_id)
        .outerjoin(Product, Product.id == VideoCard.product_id)
        .where(*clauses)
        .order_by(Publication.created_at, Publication.id)
    )


def _load_publications(db: Session, filters: DataExportFilters) -> list[dict]:
    rows = _limited(db, _publication_statement(filters))
    result = []
    for publication, card, profile, product in rows:
        brand, product_name = _product_values(card, product)
        result.append(
            {
                "publication_id": publication.id,
                "blogger_id": card.blogger_id,
                "blogger": profile.display_name if profile else None,
                "video_card_id": card.id,
                "title": card.title,
                "brand": brand,
                "product": product_name,
                "platform": publication.platform,
                "url": publication.normalized_url,
                "external_id": publication.external_id,
                "status": publication.status,
                "availability": publication.availability,
                "submitted_at": publication.submitted_at,
                "reviewed_at": publication.reviewed_at,
            }
        )
    return result


def _load_view_readings(db: Session, filters: DataExportFilters) -> list[dict]:
    clauses = [
        ViewReading.reporting_period >= filters.date_from,
        ViewReading.reporting_period <= filters.date_to,
    ]
    clauses.extend(_content_filters(filters))
    if filters.status:
        clauses.append(ViewReading.status == filters.status)
    rows = _limited(
        db,
        select(ViewReading, Publication, VideoCard)
        .join(Publication, Publication.id == ViewReading.publication_id)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .outerjoin(Product, Product.id == VideoCard.product_id)
        .where(*clauses)
        .order_by(ViewReading.reporting_period, ViewReading.id),
    )
    return [
        {
            "reading_id": reading.id,
            "publication_id": publication.id,
            "blogger_id": card.blogger_id,
            "platform": publication.platform,
            "url": publication.normalized_url,
            "period": reading.reporting_period,
            "source": reading.source,
            "reported_value": reading.reported_value,
            "accepted_value": reading.accepted_value,
            "status": reading.status,
            "risk_flags": reading.risk_flags,
            "captured_at": reading.captured_at,
        }
        for reading, publication, card in rows
    ]


def _load_moderation_history(db: Session, filters: DataExportFilters) -> list[dict]:
    start, end = _utc_bounds(filters)
    sources = (
        ("profile", ProfileHistory, ProfileHistory.profile_id),
        ("social_account", SocialAccountHistory, SocialAccountHistory.social_account_id),
        ("publication", PublicationHistory, PublicationHistory.publication_id),
    )
    result = []
    for object_type, model, object_column in sources:
        rows = _limited(
            db,
            select(model)
            .where(model.created_at >= start, model.created_at < end)
            .order_by(model.created_at, model.id),
        )
        for (event,) in rows:
            row = {
                "event_id": event.id,
                "object_type": object_type,
                "object_id": getattr(event, object_column.key),
                "actor_user_id": event.actor_user_id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "reason": event.reason,
                "changes": getattr(event, "changes", {}),
                "created_at": event.created_at,
            }
            if not filters.status or event.to_status == filters.status:
                result.append(row)

    reading_rows = _limited(
        db,
        select(ViewReadingHistory)
        .where(ViewReadingHistory.created_at >= start, ViewReadingHistory.created_at < end)
        .order_by(ViewReadingHistory.created_at, ViewReadingHistory.id),
    )
    for (event,) in reading_rows:
        if filters.status and event.action != filters.status:
            continue
        result.append(
            {
                "event_id": event.id,
                "object_type": "view_reading",
                "object_id": event.reading_id,
                "actor_user_id": event.actor_user_id,
                "event_type": event.action,
                "from_status": None,
                "to_status": None,
                "reason": event.reason,
                "changes": {"old_value": event.old_value, "new_value": event.new_value},
                "created_at": event.created_at,
            }
        )
    result.sort(key=lambda row: (row["created_at"], str(row["event_id"])))
    if len(result) > MAX_EXPORT_ROWS:
        raise ExportTooLargeError
    return result


def _load_accruals(db: Session, filters: DataExportFilters) -> list[dict]:
    clauses = [
        CalculationPeriod.period >= filters.date_from,
        CalculationPeriod.period <= filters.date_to,
    ]
    clauses.extend(_content_filters(filters))
    rows = _limited(
        db,
        select(PublicationAccrual, CalculationPeriod, Publication, VideoCard, CreatorProfile, Product)
        .join(CalculationPeriod, CalculationPeriod.id == PublicationAccrual.period_id)
        .join(Publication, Publication.id == PublicationAccrual.publication_id)
        .join(VideoCard, VideoCard.id == Publication.video_card_id)
        .outerjoin(CreatorProfile, CreatorProfile.user_id == PublicationAccrual.blogger_id)
        .outerjoin(Product, Product.id == VideoCard.product_id)
        .where(*clauses)
        .order_by(CalculationPeriod.period, PublicationAccrual.id),
    )
    result = []
    for accrual, period, publication, card, profile, product in rows:
        brand, product_name = _product_values(card, product)
        result.append(
            {
                "accrual_id": accrual.id,
                "period": period.period,
                "blogger_id": accrual.blogger_id,
                "blogger": profile.display_name if profile else None,
                "publication_id": publication.id,
                "platform": publication.platform,
                "brand": brand,
                "product": product_name,
                "eligible_views": accrual.eligible_views,
                "rate_kopecks": accrual.rate_kopecks_per_view,
                "amount_kopecks": accrual.amount_kopecks,
                "adjustment_kopecks": accrual.adjustment_kopecks,
                "risk_flags": accrual.risk_flags,
            }
        )
    return result


def _load_support_tickets(db: Session, filters: DataExportFilters) -> list[dict]:
    start, end = _utc_bounds(filters)
    assignee = aliased(User)
    author = aliased(User)
    clauses = [SupportTicket.created_at >= start, SupportTicket.created_at < end]
    if filters.blogger_id:
        clauses.append(SupportTicket.blogger_id == filters.blogger_id)
    if filters.status:
        clauses.append(SupportTicket.status == filters.status)
    rows = _limited(
        db,
        select(
            SupportTicket,
            CreatorProfile,
            assignee.email,
            SupportMessage,
            author.email,
        )
        .outerjoin(CreatorProfile, CreatorProfile.user_id == SupportTicket.blogger_id)
        .outerjoin(assignee, assignee.id == SupportTicket.assigned_to_user_id)
        .outerjoin(SupportMessage, SupportMessage.ticket_id == SupportTicket.id)
        .outerjoin(author, author.id == SupportMessage.author_user_id)
        .where(*clauses)
        .order_by(
            SupportTicket.created_at,
            SupportTicket.id,
            SupportMessage.created_at,
            SupportMessage.id,
        ),
    )
    return [
        {
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "blogger_id": ticket.blogger_id,
            "blogger": profile.display_name if profile else None,
            "category": ticket.category,
            "subject": ticket.subject,
            "status": ticket.status,
            "assigned_to": assigned_to,
            "message_author": message_author,
            "message_body": message.body if message else None,
            "message_created_at": message.created_at if message else None,
            "created_at": ticket.created_at,
            "last_message_at": ticket.last_message_at,
            "resolved_at": ticket.resolved_at,
            "closed_at": ticket.closed_at,
        }
        for ticket, profile, assigned_to, message, message_author in rows
    ]


def _load_audit_log(db: Session, filters: DataExportFilters) -> list[dict]:
    start, end = _utc_bounds(filters)
    clauses = [SecurityEvent.occurred_at >= start, SecurityEvent.occurred_at < end]
    if filters.status:
        clauses.append(SecurityEvent.result == filters.status)
    rows = _limited(
        db,
        select(SecurityEvent)
        .where(*clauses)
        .order_by(SecurityEvent.occurred_at, SecurityEvent.id),
    )
    return [
        {
            "event_id": event.id,
            "occurred_at": event.occurred_at,
            "actor_user_id": event.actor_user_id,
            "actor_role": event.actor_role,
            "action": event.action,
            "result": event.result,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "request_id": event.request_id,
            "ip_address": event.ip_address,
            "metadata": event.event_metadata,
        }
        for (event,) in rows
    ]


LOADERS = {
    ExportType.BLOGGERS: _load_bloggers,
    ExportType.SOCIAL_ACCOUNTS: _load_social_accounts,
    ExportType.PUBLICATIONS: _load_publications,
    ExportType.VIEW_READINGS: _load_view_readings,
    ExportType.MODERATION_HISTORY: _load_moderation_history,
    ExportType.ACCRUALS: _load_accruals,
    ExportType.SUPPORT_TICKETS: _load_support_tickets,
    ExportType.AUDIT_LOG: _load_audit_log,
}


def load_data_export_rows(
    db: Session, *, export_type: ExportType, filters: DataExportFilters
) -> list[dict]:
    try:
        loader = LOADERS[export_type]
    except KeyError as error:
        raise ValueError("Unsupported data export type") from error
    return loader(db, filters)
