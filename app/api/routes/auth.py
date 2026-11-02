from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.rate_limit import RateLimitExceeded
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    GoogleSignInRequest,
    LoginCodeVerifyRequest,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    UserUpdate,
)
from app.schemas.user import UserRead
from app.services import auth as auth_service
from app.services.email import send_login_code_email, send_magic_link_email

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


def _start_session(
    db: Session, user: User, request: Request, response: Response, client: str | None
) -> AuthResponse:
    """Signs the user in: a session cookie for the web, or — for the mobile
    app (?client=mobile), which has no cookie jar to lean on — a bearer token
    (a personal access token, accepted by get_current_user_optional)."""
    if client == "mobile":
        _, raw_token = auth_service.create_personal_access_token(
            db, user, "mobile app", settings.mobile_token_ttl_days
        )
        return AuthResponse(user=UserRead.model_validate(user), token=raw_token)
    session_token = auth_service.create_session(
        db, user, user_agent=request.headers.get("user-agent"), ip_address=request.client.host if request.client else None
    )
    _set_session_cookie(response, session_token)
    return AuthResponse(user=UserRead.model_validate(user))


@router.post("/request-link", status_code=status.HTTP_202_ACCEPTED)
def request_magic_link(payload: MagicLinkRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    user = auth_service.get_or_create_user(db, payload.email)
    ip_address = request.client.host if request.client else None
    try:
        auth_service.enforce_magic_link_rate_limit(db, user, ip_address)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    raw_token = auth_service.create_magic_link(db, user, requested_ip=ip_address)
    link = f"{settings.frontend_base_url}/auth/callback?token={raw_token}"
    send_magic_link_email(user.email, link)
    return {"detail": "If that email is valid, a login link has been sent."}


@router.post("/request-code", status_code=status.HTTP_202_ACCEPTED)
def request_login_code(payload: MagicLinkRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    """Emails a six-digit sign-in code to type into POST /auth/verify-code —
    in place of a link, so signing in doesn't leave the page it started on
    (e.g. the Apply someone tapped). Same throttle as /request-link."""
    user = auth_service.get_or_create_user(db, payload.email)
    ip_address = request.client.host if request.client else None
    try:
        auth_service.enforce_magic_link_rate_limit(db, user, ip_address)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    code = auth_service.create_login_code(db, user, requested_ip=ip_address)
    send_login_code_email(user.email, code)
    return {"detail": "If that email is valid, a sign-in code has been sent."}


@router.post("/verify-code", response_model=AuthResponse)
def verify_login_code(
    payload: LoginCodeVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    client: str | None = None,
) -> AuthResponse:
    try:
        user = auth_service.verify_login_code(db, payload.email, payload.code)
    except auth_service.AuthError as exc:
        # Keeps the wrong-try count, which get_db would roll back with this error.
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _start_session(db, user, request, response, client)


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


@router.post("/google", response_model=AuthResponse)
def sign_in_with_google(
    payload: GoogleSignInRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    client: str | None = None,
) -> AuthResponse:
    if not settings.google_client_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Google sign-in isn't enabled.")
    try:
        user = auth_service.sign_in_with_google(db, payload.credential)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _start_session(db, user, request, response, client)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is not None:
        auth_service.revoke_session(db, raw_token)
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
def update_current_user(
    payload: UserUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserRead:
    # A genuine partial update: a field is only touched when the request
    # actually included it (exclude_unset), not whenever it happens to be
    # non-None — otherwise adding email_alerts_enabled here would make any
    # call that sets it (without also re-sending display_name) silently
    # wipe the name back to blank.
    fields = payload.model_dump(exclude_unset=True)
    if "display_name" in fields:
        display_name = payload.display_name.strip() if payload.display_name else None
        current_user.display_name = display_name or None
    if "email_alerts_enabled" in fields:
        current_user.email_alerts_enabled = payload.email_alerts_enabled
    db.flush()
    return UserRead.model_validate(current_user)
