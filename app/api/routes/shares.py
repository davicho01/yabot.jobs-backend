"""Share attribution (PR-19, app.services.job_shares): recording a share
made signed in, and where a new user first came from."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.job_share import AttributionCreate, AttributionRead, JobShareCreate
from app.services import job_shares

router = APIRouter(tags=["shares"])


@router.post("/shares", status_code=status.HTTP_204_NO_CONTENT)
def create_share(
    payload: JobShareCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    """Sent after the share sheet or copy, best-effort: the link works
    without it, only the credit is lost."""
    try:
        job_shares.record_share(db, current_user, payload.id, payload.job_url_id, payload.method)
    except job_shares.ShareError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/me/attribution", response_model=AttributionRead)
def create_attribution(
    payload: AttributionCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> AttributionRead:
    """Sent right after sign-up; recorded once, for a new account only."""
    recorded = job_shares.record_attribution(
        db, current_user, datetime.now(timezone.utc), **payload.model_dump()
    )
    return AttributionRead(recorded=recorded)
