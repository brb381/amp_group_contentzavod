import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import OperationalError

from app.api.v1.auth import router as auth_router
from app.api.v1.creator_profiles import router as creator_profiles_router
from app.api.v1.dashboards import router as dashboards_router
from app.api.v1.social_account_moderation import router as social_account_moderation_router
from app.api.v1.security_events import router as security_events_router
from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.products import router as products_router
from app.api.v1.video_cards import router as video_cards_router
from app.api.v1.publications import router as publications_router
from app.api.v1.publication_moderation import router as publication_moderation_router
from app.api.v1.view_readings import router as view_readings_router
from app.api.v1.billing import router as billing_router
from app.api.v1.payouts import router as payouts_router
from app.api.v1.exports import router as exports_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.support import router as support_router
from app.api.v1.lifecycle import router as lifecycle_router
from app.api.v1.account_deletion import router as account_deletion_router
from app.api.v1.legal import router as legal_router
from app.api.errors import (
    api_error_handler,
    database_operational_error_handler,
    http_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from app.errors import APIError
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    configure_logging("api")
    app = FastAPI(title="AMP Content Factory API", version="0.1.0")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(creator_profiles_router, prefix="/api/v1")
    app.include_router(dashboards_router, prefix="/api/v1")
    app.include_router(social_account_moderation_router, prefix="/api/v1")
    app.include_router(security_events_router, prefix="/api/v1")
    app.include_router(admin_users_router, prefix="/api/v1")
    app.include_router(products_router, prefix="/api/v1")
    app.include_router(video_cards_router, prefix="/api/v1")
    app.include_router(publications_router, prefix="/api/v1")
    app.include_router(publication_moderation_router, prefix="/api/v1")
    app.include_router(view_readings_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(payouts_router, prefix="/api/v1")
    app.include_router(exports_router, prefix="/api/v1")
    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(support_router, prefix="/api/v1")
    app.include_router(lifecycle_router, prefix="/api/v1")
    app.include_router(account_deletion_router, prefix="/api/v1")
    app.include_router(legal_router, prefix="/api/v1")
    app.add_exception_handler(APIError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(OperationalError, database_operational_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
