from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.services.auth import get_user_by_pat, get_user_by_session_token

__all__ = ["get_db", "get_current_user"]

_BEARER_PREFIX = "bearer "


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    # Programmatic clients (e.g. mcp_server/) that can't do the browser
    # cookie flow authenticate with a personal access token instead — see
    # POST /auth/tokens. Browser sessions keep using the cookie.
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith(_BEARER_PREFIX):
        user = get_user_by_pat(db, auth_header[len(_BEARER_PREFIX) :].strip())
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
        return user

    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    user = get_user_by_session_token(db, raw_token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    return user
