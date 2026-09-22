"""Fixtures for tests that call a route function directly (no FastAPI/HTTP
layer — Depends(...) defaults are just parameter annotations to Python, so
current_user/db can be passed straight in) against an in-memory SQLite
engine, the same style tests/services/conftest.py uses for service-layer
tests.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.db.base import Base


# JobPosting (needed by tests/api/test_applications.py) has Postgres-only
# JSONB columns — same shim tests/services/conftest.py registers for its own
# directory's tests, just scoped here too since this directory's tests
# don't share that conftest.
@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.SavedSearch.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()
