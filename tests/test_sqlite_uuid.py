"""The SQLite test databases keep UUIDs as text (tests/conftest.py's
_uuid_as_text_on_sqlite). Without it a uuid whose hex reads as a number is
stored as a float and reading the row back fails — what broke the
saved-search alert test in CI on 2026-10-13."""

import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models as m
from app.db.base import Base


def test_numeric_looking_uuid_round_trips():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.User.__table__])
    numeric_looking = uuid.UUID("12345678-9012-4456-8890-12345678e901")
    with Session(engine) as session:
        session.add(m.User(id=numeric_looking, email="x@example.com"))
        session.commit()
        assert session.scalars(select(m.User.id)).one() == numeric_looking
