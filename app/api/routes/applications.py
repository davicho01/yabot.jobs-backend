import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db
from app.models.enums import ApplicationStatus
from app.models.job_application import UserJobApplication
from app.models.user import User
from app.schemas.application import ApplicationCreate, ApplicationRead, ApplicationUpdate
from app.services.jobs import find_existing_application, get_or_create_job_posting

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationRead])
def list_applications(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ApplicationRead]:
    applications = db.scalars(
        select(UserJobApplication)
        .where(UserJobApplication.user_id == current_user.id)
        .options(
            selectinload(UserJobApplication.job_posting),
            selectinload(UserJobApplication.latest_score),
            selectinload(UserJobApplication.latest_tailored_resume),
            selectinload(UserJobApplication.latest_cover_letter),
            selectinload(UserJobApplication.latest_tailored_resume_score),
        )
        .order_by(UserJobApplication.created_at.desc())
    ).all()
    return applications


@router.post("", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    posting, _url_row = get_or_create_job_posting(db, str(payload.url), current_user.id)

    # Also catches a cross-posted duplicate of a job already saved/applied to
    # under a different URL (see find_existing_application) — not just this
    # exact posting, which alone would only catch resubmitting the same URL.
    application = find_existing_application(db, current_user.id, posting.id)
    if application is None:
        application = UserJobApplication(
            user_id=current_user.id, job_posting=posting, status=ApplicationStatus.SAVED
        )
        db.add(application)
        db.flush()
    return application


@router.patch("/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    application = db.scalar(
        select(UserJobApplication).where(
            UserJobApplication.id == application_id, UserJobApplication.user_id == current_user.id
        )
    )
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")

    if payload.status is not None:
        if payload.status == ApplicationStatus.APPLIED and application.applied_at is None:
            application.applied_at = datetime.now(timezone.utc)
        application.status = payload.status
    if payload.notes is not None:
        application.notes = payload.notes
    if payload.is_archived is not None:
        application.is_archived = payload.is_archived

    db.flush()
    return application


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    application = db.scalar(
        select(UserJobApplication).where(
            UserJobApplication.id == application_id, UserJobApplication.user_id == current_user.id
        )
    )
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found.")
    db.delete(application)
