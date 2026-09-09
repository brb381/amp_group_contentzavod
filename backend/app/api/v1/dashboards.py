import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.auth.dependencies import require_active_roles, require_roles
from app.auth.models import Role, User
from app.catalog.models import Brand
from app.clock import utc_now
from app.dashboards.schemas import (
    CreatorDashboardResponse,
    StaffAnalyticsResponse,
    StaffDashboardResponse,
)
from app.dashboards.service import creator_dashboard, staff_analytics, staff_dashboard
from app.database.session import get_db
from app.platforms import Platform


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    tags=["dashboards"], dependencies=[Depends(_set_private_no_store)]
)
Blogger = Annotated[User, Depends(require_roles(Role.BLOGGER))]
StaffDashboardViewer = Annotated[
    User,
    Depends(
        require_active_roles(
            Role.ADMIN,
            Role.MODERATOR,
            Role.MANAGER,
        )
    ),
]
AnalyticsViewer = Annotated[
    User,
    Depends(require_active_roles(Role.ADMIN, Role.ANALYST)),
]


@router.get("/me/dashboard", response_model=CreatorDashboardResponse)
def get_creator_dashboard(
    blogger: Blogger,
    db: Session = Depends(get_db, scope="function"),
) -> CreatorDashboardResponse:
    return creator_dashboard(db, actor=blogger, now=utc_now())


@router.get("/staff/dashboard", response_model=StaffDashboardResponse)
def get_staff_dashboard(
    _: StaffDashboardViewer,
    db: Session = Depends(get_db, scope="function"),
) -> StaffDashboardResponse:
    return staff_dashboard(db, now=utc_now())


@router.get("/staff/analytics", response_model=StaffAnalyticsResponse)
def get_staff_analytics(
    _: AnalyticsViewer,
    period_from: date | None = Query(default=None, alias="periodFrom"),
    period_to: date | None = Query(default=None, alias="periodTo"),
    blogger_id: uuid.UUID | None = Query(default=None, alias="bloggerId"),
    brand: Brand | None = Query(default=None),
    product_id: uuid.UUID | None = Query(default=None, alias="productId"),
    platform: Platform | None = Query(default=None),
    db: Session = Depends(get_db, scope="function"),
) -> StaffAnalyticsResponse:
    if (period_from and period_from.day != 1) or (
        period_to and period_to.day != 1
    ):
        raise APIError(
            422,
            "INVALID_ANALYTICS_PERIOD",
            "Analytics periods must start on the first day of a month",
        )
    if period_from and period_to and period_from > period_to:
        raise APIError(
            422,
            "INVALID_ANALYTICS_PERIOD",
            "periodFrom must not be after periodTo",
        )
    return staff_analytics(
        db,
        now=utc_now(),
        period_from=period_from,
        period_to=period_to,
        blogger_id=blogger_id,
        brand=brand,
        product_id=product_id,
        platform=platform,
    )
