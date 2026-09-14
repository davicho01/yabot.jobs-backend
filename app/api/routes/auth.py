from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.schemas.auth import AuthResponse, MagicLinkRequest, MagicLinkVerifyRequest
from app.schemas.user import UserRead
from app.services import auth as auth_service
from app.services.email import send_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )


@router.post("/request-link", status_code=status.HTTP_202_ACCEPTED)
def request_magic_link(payload: MagicLinkRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    user = auth_service.get_or_create_user(db, payload.email)
    raw_token = auth_service.create_magic_link(db, user)
    link = f"{settings.frontend_base_url}/auth/callback?token={raw_token}"
    send_magic_link_email(user.email, link)
    return {"detail": "If that email is valid, a login link has been sent."}


@router.post("/verify", response_model=AuthResponse)
def verify_magic_link(
    payload: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthResponse:
    try:
        user = auth_service.redeem_magic_link(db, payload.token)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    session_token = auth_service.create_session(
        db, user, user_agent=request.headers.get("user-agent"), ip_address=request.client.host if request.client else None
    )
    _set_session_cookie(response, session_token)
    return AuthResponse(user=UserRead.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is not None:
        auth_service.revoke_session(db, raw_token)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
