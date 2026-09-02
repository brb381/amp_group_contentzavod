import uuid
from secrets import token_urlsafe

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.audit.service import AuditAction, record_event
from app.auth.dependencies import CSRF_COOKIE, CurrentUser, auth_cookie_names, require_csrf
from app.auth.rate_limit import (
    RedisRateLimiter,
    clear_rate_limit,
    enforce_rate_limit,
    get_rate_limiter,
)
from app.auth.schemas import (
    EmailVerificationRequest,
    LoginRequest,
    PasswordResetConfirmationRequest,
    PasswordResetRequest,
    RegisterRequest,
    UserResponse,
)
from app.auth.security import create_access_token, decode_access_token, identity_hash
from app.auth.service import (
    confirm_email_verification,
    login,
    refresh,
    register_user,
    request_password_reset,
    request_email_verification,
    reset_password,
    revoke_session,
)
from app.config import Settings, get_settings
from app.database.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


def client_identity(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def set_auth_cookies(response: Response, *, access_token: str, refresh_token: str, settings: Settings) -> None:
    access_cookie, refresh_cookie = auth_cookie_names(settings)
    options = {"httponly": True, "secure": settings.cookie_secure, "samesite": "lax", "path": "/"}
    response.set_cookie(access_cookie, access_token, max_age=settings.access_token_ttl_minutes * 60, **options)
    response.set_cookie(refresh_cookie, refresh_token, max_age=settings.refresh_token_ttl_days * 86400, **options)
    response.set_cookie(
        CSRF_COOKIE,
        token_urlsafe(32),
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    return register_user(db, payload, context_from_request(request), settings)


@router.post("/login", response_model=UserResponse)
def login_user(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> UserResponse:
    email_key = f"rate-limit:login:email:{identity_hash(str(payload.email))}"
    ip_key = f"rate-limit:login:ip:{identity_hash(client_identity(request))}"
    enforce_rate_limit(limiter, key=email_key, limit=5, window_seconds=900, fail_closed=False)
    enforce_rate_limit(limiter, key=ip_key, limit=30, window_seconds=600, fail_closed=False)
    user, session, refresh_token = login(
        db,
        email=str(payload.email),
        password=payload.password,
        settings=settings,
        audit_context=context_from_request(request),
    )
    clear_rate_limit(limiter, email_key)
    access_token = create_access_token(user_id=user.id, session_id=session.id, settings=settings)
    set_auth_cookies(response, access_token=access_token, refresh_token=refresh_token, settings=settings)
    return user


@router.post("/email-verification-requests", status_code=status.HTTP_202_ACCEPTED)
def create_email_verification_request(
    request: Request,
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> None:
    request_email_verification(db, user, context_from_request(request), settings)


@router.post("/email-verifications", status_code=status.HTTP_204_NO_CONTENT)
def create_email_verification(
    payload: EmailVerificationRequest,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> None:
    confirm_email_verification(db, payload.token, context_from_request(request))


@router.post("/password-reset-requests", status_code=status.HTTP_202_ACCEPTED)
def create_password_reset_request(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> None:
    email_key = f"rate-limit:password-reset:email:{identity_hash(str(payload.email))}"
    ip_key = f"rate-limit:password-reset:ip:{identity_hash(client_identity(request))}"
    enforce_rate_limit(limiter, key=email_key, limit=3, window_seconds=3600, fail_closed=True)
    enforce_rate_limit(limiter, key=ip_key, limit=10, window_seconds=3600, fail_closed=True)
    request_password_reset(db, str(payload.email), context_from_request(request), settings)


@router.post("/password-resets", status_code=status.HTTP_204_NO_CONTENT)
def create_password_reset(
    payload: PasswordResetConfirmationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> None:
    token_key = f"rate-limit:password-reset-confirm:token:{identity_hash(payload.token)}"
    ip_key = f"rate-limit:password-reset-confirm:ip:{identity_hash(client_identity(request))}"
    enforce_rate_limit(limiter, key=token_key, limit=5, window_seconds=1800, fail_closed=True)
    enforce_rate_limit(limiter, key=ip_key, limit=10, window_seconds=3600, fail_closed=True)
    reset_password(
        db,
        token=payload.token,
        new_password=payload.new_password,
        audit_context=context_from_request(request),
    )
    access_cookie, refresh_cookie = auth_cookie_names(settings)
    response.delete_cookie(access_cookie, path="/")
    response.delete_cookie(refresh_cookie, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/refresh", response_model=UserResponse)
def refresh_tokens(
    response: Response,
    request: Request,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserResponse:
    _, refresh_cookie = auth_cookie_names(settings)
    refresh_token = request.cookies.get(refresh_cookie)
    if not refresh_token:
        from fastapi import HTTPException

        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    user, session, new_refresh_token = refresh(
        db,
        refresh_token=refresh_token,
        settings=settings,
        audit_context=context_from_request(request),
    )
    access_token = create_access_token(user_id=user.id, session_id=session.id, settings=settings)
    set_auth_cookies(response, access_token=access_token, refresh_token=new_refresh_token, settings=settings)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    user: CurrentUser,
    request: Request,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> None:
    _ = user
    access_cookie, refresh_cookie = auth_cookie_names(settings)
    access_token = request.cookies.get(access_cookie)
    if access_token:
        claims = decode_access_token(access_token, settings)
        revoke_session(db, session_id=uuid.UUID(claims["sid"]))
    record_event(
        db,
        context=context_from_request(request),
        action=AuditAction.LOGOUT_SUCCEEDED,
        actor_user_id=user.id,
        actor_role=user.role.value,
        object_type="refresh_session",
        object_id=getattr(request.state, "session_id", None),
    )
    response.delete_cookie(access_cookie, path="/")
    response.delete_cookie(refresh_cookie, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return user
