"""أداة: تتحقق إن سلسلة الـmigrations شغالة من الصفر ومن الأول للآخر.

بتعمل downgrade كامل لـbase وبعدين upgrade كامل لـhead على داتابيز الاختبار،
وبتقرا إن السلسلة ماشية ومفيش عمود بيتزاد مرتين.

    $env:DATABASE_URL = python -m tools.test_db_url
    python -m tools.migration_roundtrip
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

DB_NAME_MARKER = "_test"


def main() -> int:
    # Same guard as the test suite: never run migrations against anything
    # whose name does not look like a test database.
    if DB_NAME_MARKER not in settings.database_url:
        print("REFUSING: database URL does not look like a test database", file=sys.stderr)
        return 1

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    print("downgrade -> base")
    command.downgrade(cfg, "base")
    print("upgrade   -> head")
    command.upgrade(cfg, "head")
    print("chain round-trip OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
