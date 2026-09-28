"""Test configuration and shared fixtures for the Phase 0 suite.

Two things matter here, and both are about not damaging anything:

1. **The suite refuses to run against anything that is not a test database.**
   The database name must contain `_test`. This runs at import time, before
   any application module is imported, so there is no window in which the app
   could hold a connection to real data.

2. **The schema is built from the migration chain, not from `create_all()`.**
   The dev database was originally created with `create_all()` and then
   stamped, which is exactly why a migration that added a column the models
   already declared (`orders.payment_method`) went unnoticed. Building the
   test schema from the chain means that class of bug fails the suite.

Run with:

    .venv\\Scripts\\python.exe -m pytest
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _resolve_test_database_url() -> str:
    """يطلع رابط داتابيز الاختبار، ويطلّع الـprocess لو مش داتابيز اختبار."""
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        if "_test" not in explicit:
            _refuse(f"TEST_DATABASE_URL points at a database that is not a test database: {_safe(explicit)}")
        return explicit

    # No TEST_DATABASE_URL: derive one from the configured DATABASE_URL by
    # swapping only the database name, so tests reuse the same credentials.
    from app.config import settings
    from sqlalchemy.engine import make_url

    derived = make_url(settings.database_url).set(database="restaurant_store_test")
    url = derived.render_as_string(hide_password=False)
    if "_test" not in url:
        _refuse("could not derive a test database URL from DATABASE_URL")
    return url


def _safe(url: str) -> str:
    """بيرجّع الرابط من غير الـpassword، عشان نقدر نطبعه في رسالة خطأ."""
    try:
        from sqlalchemy.engine import make_url

        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable url>"


def _refuse(message: str) -> None:
    sys.stderr.write(
        "\nRefusing to run the test suite.\n\n"
        f"  {message}\n\n"
        "The suite only ever touches a database whose name contains '_test'.\n"
        "Set TEST_DATABASE_URL, for example:\n"
        '  $env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/restaurant_store_test"\n'
    )
    raise SystemExit(1)


# Must happen before anything imports app.config, because pydantic-settings
# reads the environment at import time and the engine is created from it.
_TEST_DATABASE_URL = _resolve_test_database_url()
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
# Tests should not trip the production fail-fast validators, and cookies
# marked Secure would not be sent over the plain-HTTP TestClient.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _TEST_DATABASE_URL


@pytest.fixture(scope="session")
def db_schema() -> None:
    """يبني الـschema من سلسلة الـmigrations مرة واحدة لكل session."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(_TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")

    # `alembic.ini` بيعمل logging.config.fileConfig، ودي بتعطّل كل
    # الـloggers الموجودة ومنهم app.* — فبنظبط اللوج تاني بعد الـmigration.
    from app.logging_config import configure_logging

    configure_logging()


@pytest.fixture()
def db(db_schema):
    """Session واحدة لكل test، كل تغييراتها بتترجع في الآخر.

    Uses SQLAlchemy's `create_savepoint` join mode: the application code can
    call `commit()` freely (the login route does), and it turns into a
    savepoint release instead of a real commit, so the outer transaction still
    rolls back at the end of the test.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(_TEST_DATABASE_URL, pool_pre_ping=True)
    connection = engine.connect()
    transaction = connection.begin()
    TestingSession = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    """TestClient مربوط بداتابيز الاختبار."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    # The rate-limit middleware is bypassed outside production, and the login
    # throttle is tested directly against the database, so both start clean.
    from app.main import reset_rate_limits

    reset_rate_limits()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def admin_user(db):
    """أدمن مُفعّل بباسورد معروف، بيتنضّف مع الـrollback."""
    from app.models import Admin
    from app.security import hash_password

    admin = Admin(
        username="testadmin",
        password_hash=hash_password("correct-horse-battery"),
        full_name="Test Admin",
        is_active=True,
    )
    db.add(admin)
    db.commit()
    return admin


@pytest.fixture()
def settings_snapshot():
    """حفظ وإرجاع قيم settings عشان test يغيّرها ويرجّعها."""
    from app.config import settings

    keys = [
        "admin_login_max_attempts",
        "admin_login_max_attempts_per_ip",
        "admin_login_window_minutes",
        "admin_login_max_rows_per_key",
    ]
    original = {key: getattr(settings, key) for key in keys}
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)
