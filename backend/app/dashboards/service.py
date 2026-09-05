import uuid
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import AccountStatus, Role, User
from app.billing.models import (
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
)
from app.billing.policy import accrual_amount
from app.catalog.models import Brand, Product
from app.content.models import (
    Publication,
    PublicationAvailability,
    PublicationHistory,
    PublicationStatus,
    VideoCard,
)
from app.creators.models import CreatorProfile, ProfileHistory, ProfileStatus
from app.dashboards.schemas import (
    AnalyticsBreakdownItem,
    AnalyticsMonthlyPoint,
    AnalyticsOverview,
    AnalyticsRankingItem,
    AnalyticsRiskSummary,
    CreatorAttentionSummary,
    CreatorCalculationSummary,
    CreatorContentSummary,
    CreatorDashboardResponse,
    CreatorFinanceSummary,
    CreatorVideoRankingItem,
    CreatorViewsSummary,
    PlatformCount,
    QueueCounter,
    StaffAnalyticsResponse,
    StaffDashboardResponse,
    StaffOverview,
)
from app.payouts.models import PayoutRequest, PayoutStatus, RecipientType
from app.platforms import Platform
from app.readings.models import ReadingStatus, ViewReading, YouTubeViewCollectionJob
from app.readings.policy import MOSCOW, manual_submission_open, reporting_period
from app.support.models import SupportStatus, SupportTicket
from app.youtube.models import YouTubeEnrichmentJob


def _month_shift(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _payable(value) -> int:
    return value.amount_kopecks + value.adjustment_kopecks


def _accepted_readings(
    db: Session, publication_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[ViewReading]]:
    if not publication_ids:
        return {}
    rows = db.scalars(
        select(ViewReading)
        .where(
            ViewReading.publication_id.in_(publication_ids),
            ViewReading.status == ReadingStatus.ACCEPTED,
            ViewReading.accepted_value.is_not(None),
        )
        .order_by(
            ViewReading.publication_id,
            ViewReading.reporting_period,
            ViewReading.captured_at,
            ViewReading.id,
        )
    )
    grouped: dict[uuid.UUID, list[ViewReading]] = defaultdict(list)
    for reading in rows:
        grouped[reading.publication_id].append(reading)
    return grouped


def _estimated_period_views(readings: list[ViewReading], period: date) -> int:
    before = [item for item in readings if item.reporting_period < period]
    current = [item for item in readings if item.reporting_period == period]
    if not current:
        return 0
    previous_value = before[-1].accepted_value if before else None
    if previous_value is None and len(current) > 1:
        previous_value = current[0].accepted_value
    current_value = current[-1].accepted_value
    return accrual_amount(previous_value, current_value, 1)[0]


def creator_dashboard(
    db: Session, *, actor: User, now: datetime
) -> CreatorDashboardResponse:
    period = reporting_period(now)
    cards = list(db.scalars(select(VideoCard).where(VideoCard.blogger_id == actor.id)))
    card_by_id = {card.id: card for card in cards}
    publications = list(
        db.scalars(
            select(Publication).where(
                Publication.video_card_id.in_(card_by_id) if card_by_id else False,
                Publication.deleted_at.is_(None),
            )
        )
    )
    active_publications = [
        item
        for item in publications
        if item.status == PublicationStatus.APPROVED
        and item.availability != PublicationAvailability.UNAVAILABLE
    ]
    active_card_ids = {item.video_card_id for item in active_publications}
    platform_counts = defaultdict(int)
    for item in active_publications:
        platform_counts[item.platform.value] += 1

    grouped_readings = _accepted_readings(db, {item.id for item in publications})
    total_by_publication = {
        publication_id: int(items[-1].accepted_value or 0)
        for publication_id, items in grouped_readings.items()
    }
    estimated_by_publication = {
        publication_id: _estimated_period_views(items, period)
        for publication_id, items in grouped_readings.items()
    }
    latest_capture = max(
        (items[-1].captured_at for items in grouped_readings.values()), default=None
    )

    calculation = db.scalar(
        select(CalculationPeriod).where(CalculationPeriod.period == period)
    )
    creator_total = None
    accruals: list[PublicationAccrual] = []
    if calculation:
        creator_total = db.scalar(
            select(CreatorPeriodTotal).where(
                CreatorPeriodTotal.period_id == calculation.id,
                CreatorPeriodTotal.blogger_id == actor.id,
            )
        )
        accruals = list(
            db.scalars(
                select(PublicationAccrual).where(
                    PublicationAccrual.period_id == calculation.id,
                    PublicationAccrual.blogger_id == actor.id,
                )
            )
        )
    if calculation and calculation.status in {
        CalculationPeriodStatus.PRELIMINARY,
        CalculationPeriodStatus.CONFIRMED,
    }:
        basis = calculation.status.value
        current_views = creator_total.eligible_views if creator_total else 0
        period_views_by_publication = {
            item.publication_id: item.eligible_views for item in accruals
        }
    else:
        basis = "estimated"
        current_views = sum(estimated_by_publication.values())
        period_views_by_publication = estimated_by_publication

    latest_preliminary = db.execute(
        select(CalculationPeriod, CreatorPeriodTotal)
        .join(CreatorPeriodTotal, CreatorPeriodTotal.period_id == CalculationPeriod.id)
        .where(
            CalculationPeriod.status == CalculationPeriodStatus.PRELIMINARY,
            CreatorPeriodTotal.blogger_id == actor.id,
        )
        .order_by(CalculationPeriod.period.desc())
        .limit(1)
    ).one_or_none()
    nearest_calculation = calculation or db.scalar(
        select(CalculationPeriod)
        .where(CalculationPeriod.period <= period)
        .order_by(CalculationPeriod.period.desc())
        .limit(1)
    )
    balance = db.get(CreatorBalance, actor.id)
    has_current_reading = {
        publication_id
        for publication_id, items in grouped_readings.items()
        if any(item.reporting_period == period for item in items)
    }
    missing_manual = sum(
        1
        for item in active_publications
        if item.platform != Platform.YOUTUBE and item.id not in has_current_reading
    )

    card_stats = defaultdict(
        lambda: {"publications": 0, "total": 0, "current": 0}
    )
    for item in publications:
        stats = card_stats[item.video_card_id]
        stats["publications"] += 1
        stats["total"] += total_by_publication.get(item.id, 0)
        stats["current"] += period_views_by_publication.get(item.id, 0)
    ranking = [
        CreatorVideoRankingItem(
            video_card_id=card.id,
            title=card.title,
            publications=card_stats[card.id]["publications"],
            total_views=card_stats[card.id]["total"],
            current_period_new_views=card_stats[card.id]["current"],
        )
        for card in cards
    ]
    ranking.sort(
        key=lambda item: (
            -item.total_views,
            -item.current_period_new_views,
            str(item.video_card_id),
        )
    )

    return CreatorDashboardResponse(
        period=period,
        generated_at=now,
        content=CreatorContentSummary(
            active_video_cards=len(active_card_ids),
            active_publications=len(active_publications),
            by_platform=[
                PlatformCount(platform=platform, publications=count)
                for platform, count in sorted(platform_counts.items())
            ],
        ),
        views=CreatorViewsSummary(
            total_views=sum(total_by_publication.values()),
            current_period_new_views=current_views,
            basis=basis,
            as_of=latest_capture,
        ),
        finance=CreatorFinanceSummary(
            preliminary_kopecks=(
                _payable(latest_preliminary[1]) if latest_preliminary else 0
            ),
            available_kopecks=balance.available_kopecks if balance else 0,
            reserved_kopecks=balance.reserved_kopecks if balance else 0,
            paid_kopecks=balance.paid_kopecks if balance else 0,
        ),
        calculation=CreatorCalculationSummary(
            period=nearest_calculation.period if nearest_calculation else period,
            status=(
                nearest_calculation.status.value
                if nearest_calculation
                else "not_started"
            ),
            calculated_at=(
                nearest_calculation.calculated_at if nearest_calculation else None
            ),
            confirmed_at=(
                nearest_calculation.confirmed_at if nearest_calculation else None
            ),
        ),
        attention=CreatorAttentionSummary(
            pending_publications=sum(
                item.status == PublicationStatus.PENDING_REVIEW
                for item in publications
            ),
            changes_required_publications=sum(
                item.status
                in {
                    PublicationStatus.CHANGES_REQUIRED,
                    PublicationStatus.RE_REVIEW_REQUIRED,
                }
                for item in publications
            ),
            missing_manual_readings=missing_manual,
            manual_submission_open=manual_submission_open(now),
        ),
        top_video_cards=ranking[:5],
    )


def _count(db: Session, model, *filters) -> int:
    return int(
        db.scalar(select(func.count()).select_from(model).where(*filters)) or 0
    )


def staff_dashboard(db: Session, *, now: datetime) -> StaffDashboardResponse:
    preliminary_amount = int(
        db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        CalculationPeriod.total_amount_kopecks
                        + CalculationPeriod.total_adjustment_kopecks
                    ),
                    0,
                )
            ).where(
                CalculationPeriod.status == CalculationPeriodStatus.PRELIMINARY
            )
        )
        or 0
    )
    available_balance = int(
        db.scalar(
            select(func.coalesce(func.sum(CreatorBalance.available_kopecks), 0))
        )
        or 0
    )
    payouts_in_progress = int(
        db.scalar(
            select(func.coalesce(func.sum(PayoutRequest.amount_kopecks), 0)).where(
                PayoutRequest.status.in_(
                    (
                        PayoutStatus.REQUESTED,
                        PayoutStatus.UNDER_REVIEW,
                        PayoutStatus.APPROVED,
                    )
                )
            )
        )
        or 0
    )
    repeated_profile_ids = select(ProfileHistory.profile_id).where(
        ProfileHistory.from_status == ProfileStatus.REJECTED.value,
        ProfileHistory.to_status == ProfileStatus.SUBMITTED.value,
    )
    corrected_publication_ids = select(PublicationHistory.publication_id).where(
        PublicationHistory.from_status.in_(
            (
                PublicationStatus.CHANGES_REQUIRED.value,
                PublicationStatus.REJECTED.value,
            )
        ),
        PublicationHistory.to_status == PublicationStatus.PENDING_REVIEW.value,
    )
    queue_specs = [
        ("new_profiles", CreatorProfile, (CreatorProfile.status == ProfileStatus.SUBMITTED, CreatorProfile.id.not_in(repeated_profile_ids)), "/staff/profiles?status=submitted&submission=new"),
        ("profiles_re_review", CreatorProfile, (CreatorProfile.status == ProfileStatus.SUBMITTED, CreatorProfile.id.in_(repeated_profile_ids)), "/staff/profiles?status=submitted&submission=repeated"),
        ("publications_pending", Publication, (Publication.status == PublicationStatus.PENDING_REVIEW, Publication.id.not_in(corrected_publication_ids)), "/staff/publications?status=pending_review&submission=new"),
        ("publications_re_submitted", Publication, (Publication.status == PublicationStatus.PENDING_REVIEW, Publication.id.in_(corrected_publication_ids)), "/staff/publications?status=pending_review&submission=repeated"),
        ("publications_changes_required", Publication, (Publication.status.in_((PublicationStatus.CHANGES_REQUIRED, PublicationStatus.RE_REVIEW_REQUIRED)),), "/staff/publications?status=changes_required"),
        ("manual_readings_pending", ViewReading, (ViewReading.status == ReadingStatus.PENDING,), "/staff/readings?status=pending"),
        ("calculations_preliminary", CalculationPeriod, (CalculationPeriod.status == CalculationPeriodStatus.PRELIMINARY,), "/staff/calculations?status=preliminary"),
        ("payouts_requested", PayoutRequest, (PayoutRequest.status == PayoutStatus.REQUESTED,), "/staff/payouts?status=requested"),
        ("payouts_approved", PayoutRequest, (PayoutRequest.status == PayoutStatus.APPROVED,), "/staff/payouts?status=approved"),
        ("unverified_requisites", PayoutRequest, (PayoutRequest.status.in_((PayoutStatus.REQUESTED, PayoutStatus.UNDER_REVIEW)), PayoutRequest.requisites_verified_at.is_(None)), "/staff/payouts?requisitesVerified=false"),
        ("overdue_receipts", PayoutRequest, (PayoutRequest.recipient_type == RecipientType.SELF_EMPLOYED, PayoutRequest.status == PayoutStatus.PAID, PayoutRequest.receipt_due_date < now.astimezone(MOSCOW).date(), PayoutRequest.receipt_received_on.is_(None)), "/staff/payouts?isReceiptOverdue=true"),
        ("new_support_tickets", SupportTicket, (SupportTicket.status == SupportStatus.NEW,), "/staff/support?status=new"),
        ("unavailable_publications", Publication, (Publication.availability == PublicationAvailability.UNAVAILABLE, Publication.deleted_at.is_(None)), "/staff/publications?availability=unavailable"),
        ("youtube_enrichment_errors", YouTubeEnrichmentJob, (YouTubeEnrichmentJob.state == "failed",), "/staff/integrations/youtube?type=enrichment&state=failed"),
        ("youtube_view_errors", YouTubeViewCollectionJob, (YouTubeViewCollectionJob.state == "failed",), "/staff/integrations/youtube?type=views&state=failed"),
    ]
    queues = [
        QueueCounter(
            code=code,
            count=_count(db, model, *filters),
            action_path=path,
        )
        for code, model, filters, path in queue_specs
    ]
    queues.insert(
        4,
        QueueCounter(
            code="suspicious_readings",
            count=_count(
                db,
                ViewReading,
                ViewReading.status == ReadingStatus.PENDING,
                ViewReading.risk_flags != [],
            ),
            action_path="/staff/readings?status=pending&isSuspicious=true",
        ),
    )
    return StaffDashboardResponse(
        generated_at=now,
        overview=StaffOverview(
            active_bloggers=_count(
                db,
                User,
                User.role == Role.BLOGGER,
                User.status == AccountStatus.ACTIVE,
                User.id.in_(
                    select(CreatorProfile.user_id).where(
                        CreatorProfile.status == ProfileStatus.APPROVED
                    )
                ),
            ),
            new_applications=_count(
                db,
                CreatorProfile,
                CreatorProfile.status == ProfileStatus.SUBMITTED,
                CreatorProfile.id.not_in(repeated_profile_ids),
            ),
            active_publications=_count(
                db,
                Publication,
                Publication.status == PublicationStatus.APPROVED,
                Publication.deleted_at.is_(None),
                Publication.availability != PublicationAvailability.UNAVAILABLE,
            ),
            preliminary_accrual_kopecks=preliminary_amount,
            available_balance_kopecks=available_balance,
            payouts_in_progress_kopecks=payouts_in_progress,
        ),
        queues=queues,
    )


def _dimension_item(
    key: str, label: str, rows: list[tuple]
) -> AnalyticsBreakdownItem:
    return AnalyticsBreakdownItem(
        key=key,
        label=label,
        views=sum(row[0].eligible_views for row in rows),
        accrual_kopecks=sum(_payable(row[0]) for row in rows),
        video_cards=len({row[3].id for row in rows}),
        publications=len({row[2].id for row in rows}),
    )


def staff_analytics(
    db: Session,
    *,
    now: datetime,
    period_from: date | None,
    period_to: date | None,
    blogger_id: uuid.UUID | None,
    brand: Brand | None,
    product_id: uuid.UUID | None,
    platform: Platform | None,
) -> StaffAnalyticsResponse:
    current = reporting_period(now)
    start = period_from or _month_shift(current, -11)
    end = period_to or current
    rows = list(
        db.execute(
            select(PublicationAccrual, CalculationPeriod, Publication, VideoCard, Product)
            .join(CalculationPeriod, CalculationPeriod.id == PublicationAccrual.period_id)
            .join(Publication, Publication.id == PublicationAccrual.publication_id)
            .join(VideoCard, VideoCard.id == Publication.video_card_id)
            .outerjoin(Product, Product.id == VideoCard.product_id)
            .where(
                CalculationPeriod.period >= start,
                CalculationPeriod.period <= end,
                CalculationPeriod.status.in_(
                    (
                        CalculationPeriodStatus.PRELIMINARY,
                        CalculationPeriodStatus.CONFIRMED,
                    )
                ),
            )
        ).all()
    )
    if blogger_id:
        rows = [row for row in rows if row[0].blogger_id == blogger_id]
    if platform:
        rows = [row for row in rows if row[2].platform == platform]
    if product_id:
        rows = [row for row in rows if row[3].product_id == product_id]
    if brand:
        rows = [
            row
            for row in rows
            if (row[4].brand if row[4] else row[3].reported_brand) == brand
        ]

    paid_filters = [
        PayoutRequest.status == PayoutStatus.PAID,
        PayoutRequest.paid_on >= start,
        PayoutRequest.paid_on < _month_shift(end, 1),
    ]
    if blogger_id:
        paid_filters.append(PayoutRequest.blogger_id == blogger_id)
    paid_rows = list(db.scalars(select(PayoutRequest).where(*paid_filters)))
    paid_by_month = defaultdict(int)
    for payout in paid_rows:
        paid_by_month[date(payout.paid_on.year, payout.paid_on.month, 1)] += (
            payout.amount_kopecks
        )

    by_month = defaultdict(list)
    by_brand = defaultdict(list)
    by_product = defaultdict(list)
    by_platform = defaultdict(list)
    by_blogger = defaultdict(list)
    by_publication = defaultdict(list)
    for row in rows:
        accrual, calc_period, publication, card, product = row
        by_month[calc_period.period].append(row)
        row_brand = product.brand if product else card.reported_brand
        row_product_key = (
            str(product.id)
            if product
            else f"reported:{card.reported_product_name or 'unknown'}"
        )
        row_product_label = (
            product.publication_name
            if product
            else (card.reported_product_name or "Unspecified")
        )
        by_brand[(row_brand.value if row_brand else "unknown")].append(row)
        by_product[(row_product_key, row_product_label)].append(row)
        by_platform[publication.platform.value].append(row)
        by_blogger[accrual.blogger_id].append(row)
        by_publication[publication.id].append(row)

    profiles = {
        profile.user_id: profile
        for profile in db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.user_id.in_(by_blogger) if by_blogger else False
            )
        )
    }
    monthly = []
    month = start
    while month <= end:
        month_rows = by_month[month]
        monthly.append(
            AnalyticsMonthlyPoint(
                period=month,
                views=sum(row[0].eligible_views for row in month_rows),
                accrual_kopecks=sum(_payable(row[0]) for row in month_rows),
                paid_kopecks=paid_by_month[month],
                bloggers=len({row[0].blogger_id for row in month_rows}),
                video_cards=len({row[3].id for row in month_rows}),
                publications=len({row[2].id for row in month_rows}),
            )
        )
        month = _month_shift(month, 1)

    def ranking(groups, labeler, identifier=lambda key: str(key)):
        items = [
            AnalyticsRankingItem(
                id=identifier(key),
                label=labeler(key, group) or identifier(key),
                views=sum(row[0].eligible_views for row in group),
                accrual_kopecks=sum(_payable(row[0]) for row in group),
            )
            for key, group in groups.items()
        ]
        return sorted(
            items,
            key=lambda item: (-item.views, -item.accrual_kopecks, item.id),
        )[:10]

    publication_ids = {row[2].id for row in rows}
    unavailable_filters = [
        Publication.availability == PublicationAvailability.UNAVAILABLE
    ]
    if platform:
        unavailable_filters.append(Publication.platform == platform)
    if publication_ids:
        unavailable_filters.append(Publication.id.in_(publication_ids))

    all_video_cards = {row[3].id for row in rows}
    total_accrual = sum(_payable(row[0]) for row in rows)
    return StaffAnalyticsResponse(
        period_from=start,
        period_to=end,
        generated_at=now,
        overview=AnalyticsOverview(
            views=sum(row[0].eligible_views for row in rows),
            accrual_kopecks=total_accrual,
            paid_kopecks=sum(item.amount_kopecks for item in paid_rows),
            video_cards=len(all_video_cards),
            publications=len({row[2].id for row in rows}),
            average_video_cost_kopecks=(
                total_accrual // len(all_video_cards) if all_video_cards else 0
            ),
        ),
        monthly=monthly,
        by_brand=[
            _dimension_item(key, key, group)
            for key, group in sorted(by_brand.items())
        ],
        by_product=[
            _dimension_item(key[0], key[1], group)
            for key, group in sorted(by_product.items())
        ],
        by_platform=[
            _dimension_item(key, key, group)
            for key, group in sorted(by_platform.items())
        ],
        top_bloggers=ranking(
            by_blogger,
            lambda key, _: (
                profiles[key].display_name or profiles[key].full_name
                if key in profiles
                else str(key)
            ),
        ),
        top_products=ranking(
            by_product,
            lambda key, _: key[1],
            identifier=lambda key: key[0],
        ),
        top_publications=ranking(
            by_publication,
            lambda _, group: group[0][2].external_title or group[0][3].title,
        ),
        risks=AnalyticsRiskSummary(
            suspicious_accruals=sum(bool(row[0].risk_flags) for row in rows),
            failed_enrichment_jobs=_count(
                db, YouTubeEnrichmentJob, YouTubeEnrichmentJob.state == "failed"
            ),
            failed_view_collection_jobs=_count(
                db,
                YouTubeViewCollectionJob,
                YouTubeViewCollectionJob.state == "failed",
            ),
            unavailable_publications=_count(
                db, Publication, *unavailable_filters
            ),
        ),
    )
