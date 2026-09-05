import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class PlatformCount(BaseModel):
    platform: str
    publications: int


class CreatorContentSummary(BaseModel):
    active_video_cards: int
    active_publications: int
    by_platform: list[PlatformCount]


class CreatorViewsSummary(BaseModel):
    total_views: int
    current_period_new_views: int
    basis: Literal["estimated", "preliminary", "confirmed"]
    as_of: datetime | None


class CreatorFinanceSummary(BaseModel):
    preliminary_kopecks: int
    available_kopecks: int
    reserved_kopecks: int
    paid_kopecks: int


class CreatorCalculationSummary(BaseModel):
    period: date
    status: Literal["not_started", "pending", "preliminary", "confirmed"]
    calculated_at: datetime | None
    confirmed_at: datetime | None


class CreatorAttentionSummary(BaseModel):
    pending_publications: int
    changes_required_publications: int
    missing_manual_readings: int
    manual_submission_open: bool


class CreatorVideoRankingItem(BaseModel):
    video_card_id: uuid.UUID
    title: str
    publications: int
    total_views: int
    current_period_new_views: int


class CreatorDashboardResponse(BaseModel):
    period: date
    generated_at: datetime
    content: CreatorContentSummary
    views: CreatorViewsSummary
    finance: CreatorFinanceSummary
    calculation: CreatorCalculationSummary
    attention: CreatorAttentionSummary
    top_video_cards: list[CreatorVideoRankingItem]


class QueueCounter(BaseModel):
    code: str
    count: int
    action_path: str


class StaffOverview(BaseModel):
    active_bloggers: int
    new_applications: int
    active_publications: int
    preliminary_accrual_kopecks: int
    available_balance_kopecks: int
    payouts_in_progress_kopecks: int


class StaffDashboardResponse(BaseModel):
    generated_at: datetime
    overview: StaffOverview
    queues: list[QueueCounter]


class AnalyticsOverview(BaseModel):
    views: int
    accrual_kopecks: int
    paid_kopecks: int
    video_cards: int
    publications: int
    average_video_cost_kopecks: int


class AnalyticsBreakdownItem(BaseModel):
    key: str
    label: str
    views: int
    accrual_kopecks: int
    video_cards: int
    publications: int


class AnalyticsRankingItem(BaseModel):
    id: str
    label: str
    views: int
    accrual_kopecks: int


class AnalyticsMonthlyPoint(BaseModel):
    period: date
    views: int
    accrual_kopecks: int
    paid_kopecks: int
    bloggers: int
    video_cards: int
    publications: int


class AnalyticsRiskSummary(BaseModel):
    suspicious_accruals: int
    failed_enrichment_jobs: int
    failed_view_collection_jobs: int
    unavailable_publications: int


class StaffAnalyticsResponse(BaseModel):
    period_from: date
    period_to: date
    generated_at: datetime
    overview: AnalyticsOverview
    monthly: list[AnalyticsMonthlyPoint]
    by_brand: list[AnalyticsBreakdownItem]
    by_product: list[AnalyticsBreakdownItem]
    by_platform: list[AnalyticsBreakdownItem]
    top_bloggers: list[AnalyticsRankingItem]
    top_products: list[AnalyticsRankingItem]
    top_publications: list[AnalyticsRankingItem]
    risks: AnalyticsRiskSummary
