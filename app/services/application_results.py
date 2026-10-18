"""Keeping an application's "latest result" pointers in step with the results
that already exist for its job.

An application's latest_score_id / latest_tailored_resume_id /
latest_tailored_resume_score_id / latest_cover_letter_id are stamped when a
result is generated (see _stamp_application_pointer in
app.api.routes.resumes). A result generated while the user had no
application for the job — or an application removed and saved again later —
left the new application with none of them, so the applications list and the
job board showed no score for a job that had one.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job_application import UserJobApplication
from app.models.resume import CoverLetter, ResumeScore, TailoredResume, TailoredResumeScore


def link_latest_results(db: Session, application: UserJobApplication) -> None:
    """Point a just-created application at the user's newest score, tailored
    resume (and that version's newest score) and cover letter for its job,
    whichever exist. Leaves a pointer that's already set alone, so a caller
    stamping its own result right after keeps that one."""

    def newest(model, *where):
        return db.scalar(
            select(model.id)
            .where(model.user_id == application.user_id, *where)
            .order_by(model.created_at.desc())
            .limit(1)
        )

    job = application.job_posting_id
    if application.latest_score_id is None:
        application.latest_score_id = newest(ResumeScore, ResumeScore.job_posting_id == job)
    if application.latest_tailored_resume_id is None:
        application.latest_tailored_resume_id = newest(TailoredResume, TailoredResume.job_posting_id == job)
    if application.latest_tailored_resume_score_id is None and application.latest_tailored_resume_id is not None:
        application.latest_tailored_resume_score_id = newest(
            TailoredResumeScore, TailoredResumeScore.tailored_resume_id == application.latest_tailored_resume_id
        )
    if application.latest_cover_letter_id is None:
        application.latest_cover_letter_id = newest(CoverLetter, CoverLetter.job_posting_id == job)


def newest_scores_by_job(db: Session, user_id: uuid.UUID, job_posting_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """The overall score of `user_id`'s newest ResumeScore for each job, in one
    query — the fallback UserJobApplication.best_score uses when an
    application's latest_score_id was never set."""
    if not job_posting_ids:
        return {}
    rows = db.execute(
        select(ResumeScore.job_posting_id, ResumeScore.overall_score)
        .where(ResumeScore.user_id == user_id, ResumeScore.job_posting_id.in_(job_posting_ids))
        .order_by(ResumeScore.created_at.desc())
    ).all()
    newest: dict[uuid.UUID, int] = {}
    for job_posting_id, overall_score in rows:
        newest.setdefault(job_posting_id, overall_score)
    return newest


def fill_fallback_scores(db: Session, user_id: uuid.UUID, applications: list[UserJobApplication]) -> None:
    """Give every application without a latest score its job's newest score
    (as `fallback_score`, read by best_score), with one query for the lot
    instead of one per application."""
    missing = [a for a in applications if a.latest_score_id is None]
    scores = newest_scores_by_job(db, user_id, [a.job_posting_id for a in missing])
    for application in missing:
        application.fallback_score = scores.get(application.job_posting_id)
