"""أداة: بتعمل داتابيز الاختبار من الصفر.

بتمسح الـschema كله وبتبنيه تاني من سلسلة الـmigrations. ده أحسن من
migration فوق migration، لأن لو عدّلنا migration قديم، الداتابيز تفضل
على الحالة القديمة فيبقى الفحص بيمرّ على schema مش زي اللي هينزل production.

    $env:DATABASE_URL = python -m tools.test_db_url
    python -m tools.rebuild_test_db
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings

DB_NAME_MARKER = "_test"


def main() -> int:
    if DB_NAME_MARKER not in settings.database_url:
        print("REFUSING: database URL does not look like a test database", file=sys.stderr)
        return 1

    engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    print("schema dropped and recreated")

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    print("built from migrations at head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
