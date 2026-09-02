import uuid
from hmac import compare_digest
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import decode_access_token, is_expired
from app.config import Settings, get_settings
from app.database.session import get_db

ACCESS_COOKIE = "__Host-amp_access"
REFRESH_COOKIE = "__Host-amp_refresh"
CSRF_COOKIE = "amp_csrf"


def auth_cookie_names(settings: Settings) -> tuple[str, str]:
    if settings.cookie_secure:
        return ACCESS_COOKIE, REFRESH_COOKIE
    return "amp_access", "amp_refresh"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> User:
    access_cookie, _ = auth_cookie_names(settings)
    access_token = request.cookies.get(access_cookie)
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")

    claims = decode_access_token(access_token, settings)
    try:
        user_id = uuid.UUID(claims["sub"])
        session_id = uuid.UUID(claims["sid"])
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from error

    session = db.get(RefreshSession, session_id)
    user = db.get(User, user_id)
    if (
        not session
        or session.user_id != user_id
        or session.revoked_at
        or is_expired(session.expires_at)
        or not user
        or user.status in {AccountStatus.BLOCKED, AccountStatus.DELETED}
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is no longer active")
    request.state.session_id = session_id
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency


def require_active_roles(*roles: Role) -> Callable:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles or user.status != AccountStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return dependency


def require_csrf(
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if not csrf_cookie or not csrf_header or not compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
