"""Two PRs that each add a migration off the same parent merge cleanly in git
but leave two alembic heads, and `alembic upgrade head` then refuses to run —
failing the deploy's migrate job (2026-11-02, the survey and device-token
migrations). This catches that in CI/locally instead.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_have_a_single_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"Multiple alembic heads: {heads}. Point the newest migration's down_revision at the other head."
