import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.routes.resumes import _get_job_posting, _resolve_resume
from app.models.auto_score_run import AutoScoreRun
from app.models.user import User
from app.schemas.auto_score import AutoScoreRunRead
from app.services.auto_score import latest_run, start_auto_score

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.post("/main/auto-score", response_model=AutoScoreRunRead, status_code=status.HTTP_202_ACCEPTED)
def start_main_auto_score(
    job_posting_id: uuid.UUID,
    resume_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoScoreRun:
    """Start a background score → tailor → score-tailored run (see
    app.services.auto_score). Poll GET below for its progress."""
    resume = _resolve_resume(db, current_user.id, resume_id)
    posting = _get_job_posting(db, job_posting_id)
    return start_auto_score(db, current_user, resume, posting)


@router.get("/main/auto-score", response_model=AutoScoreRunRead)
def get_main_auto_score(
    job_posting_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AutoScoreRun:
    run = latest_run(db, current_user.id, job_posting_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No auto score run for this job yet.")
    return run
