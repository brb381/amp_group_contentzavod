import logging
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import APIError


PRIVATE_NO_STORE_HEADERS = {"Cache-Control": "private, no-store"}
logger = logging.getLogger(__name__)


def _body(request: Request, *, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": getattr(request.state, "request_id", "unknown"),
    }


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(request, code=exc.code, message=exc.message, details=exc.details),
        headers={**exc.headers, **PRIVATE_NO_STORE_HEADERS},
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT"}.get(
        exc.status_code, "HTTP_ERROR"
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(request, code=code, message=message),
        headers=PRIVATE_NO_STORE_HEADERS,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {key: value for key, value in error.items() if key not in {"input", "url"}}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_body(
            request,
            code="VALIDATION_ERROR",
            message="Invalid request data",
            details={"errors": jsonable_encoder(errors)},
        ),
        headers=PRIVATE_NO_STORE_HEADERS,
    )


async def database_operational_error_handler(
    request: Request,
    exc: OperationalError,
) -> JSONResponse:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "55P03":
        code = "DATABASE_LOCK_TIMEOUT"
        message = "The operation is busy; retry shortly"
    elif sqlstate == "57014":
        code = "DATABASE_STATEMENT_TIMEOUT"
        message = "The database operation exceeded its time limit"
    else:
        code = "DATABASE_UNAVAILABLE"
        message = "The database is temporarily unavailable"
    return JSONResponse(
        status_code=503,
        content=_body(request, code=code, message=message),
        headers={
            "Retry-After": "1",
            **PRIVATE_NO_STORE_HEADERS,
        },
    )


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    logger.error(
        "Unhandled API error type=%s sqlstate=%s request_id=%s",
        type(exc).__name__,
        sqlstate,
        getattr(request.state, "request_id", "unknown"),
    )
    return JSONResponse(
        status_code=500,
        content=_body(
            request,
            code="INTERNAL_SERVER_ERROR",
            message="An internal server error occurred",
        ),
        headers=PRIVATE_NO_STORE_HEADERS,
    )
