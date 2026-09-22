"""Fixtures for tests that call a route function directly (no FastAPI/HTTP
layer — Depends(...) defaults are just parameter annotations to Python, so
current_user/db can be passed straight in) against an in-memory SQLite
engine, the same style tests/services/conftest.py uses for service-layer
tests.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.models as m
from app.db.base import Base


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[m.SavedSearch.__table__])
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()
