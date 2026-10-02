from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# No pool_pre_ping: its SELECT 1 on every checkout was a full round trip to
# the database (another region) on every request. pool_recycle retires
# connections before idle timeouts can kill them instead; the tradeoff is
# that a connection broken some other way (a Cloud SQL restart) fails the
# one request that picks it up, rather than being silently replaced.
engine = create_engine(settings.database_url, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
