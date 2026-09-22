import itertools
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.api.routes.resumes import _resolve_resume
from app.db.base import Base

_counter = itertools.count()


@pytest.fixture
def db() -> Session:
    # Local db fixture (not tests/api/conftest.py's, which only has
    # SavedSearch) — _resolve_resume only needs Resume.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.Resume.__table__])
    with engine.begin() as conn:
        # Resume's one-main-per-user index is partial in Postgres
        # (postgresql_where=text("is_main")) — SQLite doesn't understand
        # that dialect-specific clause and silently drops it, so create_all
        # gives SQLite a *full* unique index on user_id instead, blocking a
        # user from having more than one resume at all. Drop it: these
        # tests need several resumes per user, and the one-main invariant
        # it enforces isn't what's under test here.
        conn.execute(text("DROP INDEX IF EXISTS uq_resumes_one_main_per_user"))
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


def _make_resume(db, user_id: uuid.UUID, *, is_main: bool = False) -> m.Resume:
    n = next(_counter)
    resume = m.Resume(
        user_id=user_id,
        filename=f"resume-{n}.pdf",
        content_type="application/pdf",
        storage_key=f"resumes/{user_id}/{n}",
        parsed_text=f"Resume text {n}",
        is_main=is_main,
    )
    db.add(resume)
    db.commit()
    return resume


def test_resolve_resume_with_no_id_falls_back_to_the_main_resume(db):
    user_id = uuid.uuid4()
    main = _make_resume(db, user_id, is_main=True)
    _make_resume(db, user_id, is_main=False)  # a second resume, not main

    resolved = _resolve_resume(db, user_id, None)

    assert resolved.id == main.id


def test_resolve_resume_with_an_id_picks_that_resume_even_when_not_main(db):
    user_id = uuid.uuid4()
    _make_resume(db, user_id, is_main=True)
    other = _make_resume(db, user_id, is_main=False)

    resolved = _resolve_resume(db, user_id, other.id)

    assert resolved.id == other.id


def test_resolve_resume_404s_for_a_resume_id_owned_by_someone_else(db):
    owner = uuid.uuid4()
    someone_else = uuid.uuid4()
    theirs = _make_resume(db, owner, is_main=True)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, someone_else, theirs.id)
    assert exc_info.value.status_code == 404


def test_resolve_resume_404s_for_an_unknown_resume_id(db):
    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, uuid.uuid4(), uuid.uuid4())
    assert exc_info.value.status_code == 404


def test_resolve_resume_with_no_id_and_no_main_resume_gives_the_original_422(db):
    # Unchanged from before resume_id existed — a user with no main resume
    # set still gets this specific, actionable error, not a generic 404.
    with pytest.raises(HTTPException) as exc_info:
        _resolve_resume(db, uuid.uuid4(), None)
    assert exc_info.value.status_code == 422
