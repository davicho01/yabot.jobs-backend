from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_current_admin_user, get_current_user_optional, get_db
from app.core.rate_limit import RateLimitExceeded
from app.models.survey_response import SurveyResponse
from app.models.user import User
from app.schemas.survey import AdminSurveyListRead, AdminSurveyRead, SurveyRead, SurveySubmit
from app.services.survey import answer_counts, save_survey_answers

router = APIRouter(prefix="/survey", tags=["survey"])
# Separate router (not admin.py) to keep the survey feature in one file.
admin_router = APIRouter(
    prefix="/admin/survey", tags=["admin"], dependencies=[Depends(get_current_admin_user)]
)


@router.post("", response_model=SurveyRead, status_code=status.HTTP_201_CREATED)
def submit_survey(
    payload: SurveySubmit,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
) -> SurveyResponse:
    """Open to signed-out visitors. Called once per survey page; see
    save_survey_answers."""
    try:
        return save_survey_answers(db, current_user, payload)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


def _to_admin_survey(row: SurveyResponse) -> AdminSurveyRead:
    fields = {name: getattr(row, name) for name in AdminSurveyRead.model_fields if name != "user_email"}
    return AdminSurveyRead(**fields, user_email=row.user.email if row.user else None)


@admin_router.get("", response_model=AdminSurveyListRead)
def list_survey_responses(
    completed_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AdminSurveyListRead:
    """Survey answers, newest first, with per-question counts over everything."""
    stmt = select(SurveyResponse)
    if completed_only:
        stmt = stmt.where(SurveyResponse.completed.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    completed_count = (
        db.scalar(select(func.count()).select_from(SurveyResponse).where(SurveyResponse.completed.is_(True))) or 0
    )
    rows = db.scalars(
        stmt.options(joinedload(SurveyResponse.user))
        .order_by(SurveyResponse.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return AdminSurveyListRead(
        items=[_to_admin_survey(row) for row in rows],
        total=total,
        completed_count=completed_count,
        counts=answer_counts(db),
    )
