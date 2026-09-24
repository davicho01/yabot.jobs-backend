from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.auth import get_user_by_pat, get_user_by_session_token

__all__ = ["get_db", "get_current_user", "get_current_user_optional", "get_current_admin_user"]

_BEARER_PREFIX = "bearer "


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    # Programmatic clients (e.g. mcp_server/) that can't do the browser
    # cookie flow authenticate with a personal access token instead — see
    # POST /auth/tokens. Browser sessions keep using the cookie.
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith(_BEARER_PREFIX):
        return get_user_by_pat(db, auth_header[len(_BEARER_PREFIX) :].strip())

    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None:
        return None

    return get_user_by_session_token(db, raw_token)


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    return user


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return current_user
