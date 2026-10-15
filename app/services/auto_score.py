"""Background "score → tailor → score tailored" pass over one (user, resume, job).

Started by POST /resumes/main/auto-score (the apply page's Auto score button)
and, later, by a pipeline that keeps evaluating jobs for a user — both just
call start_auto_score. The run itself happens on the scan worker
(see app.services.jobs.process_scan_message), so it keeps going after the
user leaves the page; its AutoScoreRun row is the state the page polls.

Rules:
- Quick-score the resume. Below TAILOR_THRESHOLD, stop.
- Otherwise tailor it and quick-score the tailored version.
- best = max(both scores). At READY_THRESHOLD or above, save the application
  (create it if missing) and mark it ready to apply. Below that, no
  application is created — one the user already had is kept, just not
  marked ready.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.resumes import quick_score_resume, quick_score_tailored_resume, tailor_resume
from app.models.auto_score_run import AutoScoreRun
from app.models.enums import AutoScoreStatus
from app.models.job_application import UserJobApplication
from app.models.job_posting import JobPosting
from app.models.resume import Resume, TailoredResume
from app.models.user import User
from app.services.job_queue import enqueue_auto_score
from app.services.llm_client import LlmError
from app.services.resume_llm import QUICK_SCORE_DEADLINE_SECONDS, TAILOR_DEADLINE_SECONDS

logger = logging.getLogger("app.auto_score")

TAILOR_THRESHOLD = 75
READY_THRESHOLD = 85

ACTIVE_STATUSES = {
    AutoScoreStatus.QUEUED,
    AutoScoreStatus.SCORING,
    AutoScoreStatus.TAILORING,
    AutoScoreStatus.SCORING_TAILORED,
}

# A run still in progress this long after it was started is given up on
# (failed), however many times its steps were re-run, so the user can start
# another. A normal run's three steps fit in their deadlines' 4 minutes.
_STALE_AFTER = timedelta(minutes=5)

# How long a status can sit unchanged before whoever was working on it is
# taken to have died (worker restarted/crashed mid-step, or the queue
# message was lost): the step's LLM deadline (resume_llm) plus room to save
# its result. Past this, the step is re-run.
_LEASES = {
    AutoScoreStatus.QUEUED: timedelta(seconds=60),
    AutoScoreStatus.SCORING: timedelta(seconds=QUICK_SCORE_DEADLINE_SECONDS + 30),
    AutoScoreStatus.TAILORING: timedelta(seconds=TAILOR_DEADLINE_SECONDS + 30),
    AutoScoreStatus.SCORING_TAILORED: timedelta(seconds=QUICK_SCORE_DEADLINE_SECONDS + 30),
}


def _older_than(moment: datetime, age: timedelta) -> bool:
    if moment.tzinfo is None:  # SQLite (tests) hands back naive UTC
        moment = moment.replace(tzinfo=timezone.utc)
    return moment < datetime.now(timezone.utc) - age


def _is_stale(run: AutoScoreRun) -> bool:
    return _older_than(run.created_at, _STALE_AFTER)


def _lease_expired(run: AutoScoreRun) -> bool:
    return _older_than(run.updated_at, _LEASES[run.status])


def latest_run(db: Session, user_id: uuid.UUID, job_posting_id: uuid.UUID) -> AutoScoreRun | None:
    """The newest run for this user+job. An in-progress one past _STALE_AFTER
    is marked failed; one nobody is working on any more (its lease ran out)
    is sent to the worker again, which picks it back up — this is what
    restarts a run whose worker died, since the page polls this."""
    run = db.scalar(
        select(AutoScoreRun)
        .where(AutoScoreRun.user_id == user_id, AutoScoreRun.job_posting_id == job_posting_id)
        .order_by(AutoScoreRun.created_at.desc())
    )
    if run is None or run.status not in ACTIVE_STATUSES:
        return run
    if _is_stale(run):
        run.status = AutoScoreStatus.FAILED
        run.error = "Timed out — try again."
        db.flush()
    elif _lease_expired(run):
        try:
            enqueue_auto_score(run.id)
            logger.warning("Auto score run %s stalled in %s; sent it to the worker again.", run.id, run.status)
        except Exception:
            logger.exception("Could not re-queue stalled auto score run %s", run.id)  # the next poll tries again
    return run


def start_auto_score(db: Session, user: User, resume: Resume, posting: JobPosting) -> AutoScoreRun:
    """Queue a run for the worker. 409 if one is already in progress for this job."""
    current = latest_run(db, user.id, posting.id)
    if current is not None and current.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Auto score is already running for this job.")

    run = AutoScoreRun(user_id=user.id, job_posting_id=posting.id, resume_id=resume.id, status=AutoScoreStatus.QUEUED)
    db.add(run)
    # Committed before publishing so the worker can always see the row.
    db.commit()
    try:
        enqueue_auto_score(run.id)
    except Exception as exc:
        logger.exception("Could not queue auto score run %s", run.id)
        run.status = AutoScoreStatus.FAILED
        run.error = "Could not start — try again."
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=run.error) from exc
    return run


def _set_status(db: Session, run: AutoScoreRun, new_status: AutoScoreStatus) -> None:
    run.status = new_status
    # Set by hand: re-running a step sets the status it already has, which
    # wouldn't trigger the column's onupdate — and this is the step's lease.
    run.updated_at = datetime.now(timezone.utc)
    db.commit()


def _stage_started_but_unfinished(run: AutoScoreRun) -> bool:
    """A stage's status is committed before its LLM call, so finding one set
    without its result means a delivery is making that call — or was, and
    died."""
    return (
        (run.status == AutoScoreStatus.SCORING and run.resume_score_id is None)
        or (run.status == AutoScoreStatus.TAILORING and run.tailored_resume_id is None)
        or (run.status == AutoScoreStatus.SCORING_TAILORED and run.tailored_score_id is None)
    )


def run_auto_score(db: Session, run_id: uuid.UUID) -> None:
    """Worker entry point. Commits after every stage so the page sees each
    one, and picks up after the last finished one, so it can be delivered
    any number of times:
    - a stage that started but didn't finish, still within its lease, is
      another delivery's — this one leaves it alone;
    - past its lease, that delivery died (worker restart/crash), so the
      stage is run again. That's the one case a stage can call the LLM a
      second time.
    The row is locked while deciding, so two deliveries can't both claim the
    same stage. Any error ends the run as failed instead of raising — a
    raised error would have the message redelivered into another LLM call.
    """
    run = db.get(AutoScoreRun, run_id, with_for_update=True)
    if run is None or run.status not in ACTIVE_STATUSES:
        db.rollback()
        return
    if _is_stale(run):
        _fail(db, run_id, "Timed out — try again.")
        return
    if _stage_started_but_unfinished(run):
        if not _lease_expired(run):
            db.rollback()
            logger.info("Auto score run %s is already being worked on (%s); skipping.", run_id, run.status)
            return
        logger.warning("Auto score run %s was interrupted in %s; running that step again.", run_id, run.status)

    user = db.get(User, run.user_id)
    resume = db.get(Resume, run.resume_id)
    posting = db.get(JobPosting, run.job_posting_id)

    try:
        if run.resume_score_id is None:
            _set_status(db, run, AutoScoreStatus.SCORING)
            score = quick_score_resume(db, user, resume, posting, create_application=False)
            run.resume_score_id = score.id
            run.resume_score = score.overall_score
            db.commit()

        if run.resume_score < TAILOR_THRESHOLD:
            run.best_score = run.resume_score
            _set_status(db, run, AutoScoreStatus.BELOW_THRESHOLD)
            return

        if run.tailored_resume_id is None:
            _set_status(db, run, AutoScoreStatus.TAILORING)
            tailored = tailor_resume(db, user, resume, posting, create_application=False)
            run.tailored_resume_id = tailored.id
            db.commit()

        if run.tailored_score_id is None:
            _set_status(db, run, AutoScoreStatus.SCORING_TAILORED)
            tailored = db.get(TailoredResume, run.tailored_resume_id)
            tailored_score = quick_score_tailored_resume(db, user, tailored, posting, create_application=False)
            run.tailored_score_id = tailored_score.id
            run.tailored_score = tailored_score.overall_score
            db.commit()

        run.best_score = max(run.resume_score, run.tailored_score)
        _finish(db, run)
    except (LlmError, HTTPException) as exc:
        _fail(db, run_id, exc.detail if isinstance(exc, HTTPException) else str(exc))
    except Exception:
        # Anything else (storage, DB, a bug) — still end the run rather than
        # raise, so the message isn't redelivered into another LLM call.
        logger.exception("Auto score run %s hit an unexpected error", run_id)
        _fail(db, run_id, "Something went wrong — try again.")


def _fail(db: Session, run_id: uuid.UUID, error: str) -> None:
    db.rollback()
    run = db.get(AutoScoreRun, run_id)
    run.status = AutoScoreStatus.FAILED
    run.error = error
    db.commit()
    logger.warning("Auto score run %s failed: %s", run_id, error)


def _finish(db: Session, run: AutoScoreRun) -> None:
    application = db.scalar(
        select(UserJobApplication).where(
            UserJobApplication.user_id == run.user_id, UserJobApplication.job_posting_id == run.job_posting_id
        )
    )
    if run.best_score >= READY_THRESHOLD:
        if application is None:
            application = UserJobApplication(user_id=run.user_id, job_posting_id=run.job_posting_id)
            db.add(application)
        application.latest_score_id = run.resume_score_id
        application.latest_tailored_resume_id = run.tailored_resume_id
        application.latest_tailored_resume_score_id = run.tailored_score_id
        application.selected_resume_id = application.selected_resume_id or run.resume_id
        application.ready_to_apply_at = datetime.now(timezone.utc)
        _set_status(db, run, AutoScoreStatus.READY)
        return

    if application is not None:
        application.ready_to_apply_at = None
    _set_status(db, run, AutoScoreStatus.NOT_READY)
