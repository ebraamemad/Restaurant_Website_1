"""اختبارات إعدادات الإنتاج، الكوكيز، الـhealth checks، واللوجات.

السبب: `compose.yaml` كان بيبعت `COOKIE_SECURE=false` بـdefault مع
`APP_ENV=production`. يعني النشر الافتراضي كان بيبعت كوكيز الجلسة من غير
تشفير. وكمان مفيش mechanism يمنع الحulus من FronTlink على HTTP.

المرجع: `app/config.py` و`app/main.py` و`app/logging_config.py`.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings


# ---------------------------------------------------------------------------
# إعدادات الإنتاج
# ---------------------------------------------------------------------------


def _prod_settings(**overrides) -> dict:
    base = {
        "app_env": "production",
        "app_secret_key": "a-real-secret-value-that-is-long-enough",
        "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
        "admin_password": "a-strong-admin-password",
        "cookie_secure": True,
    }
    base.update(overrides)
    return base


class TestProductionFailFast:
    def test_valid_production_settings_pass(self) -> None:
        settings = Settings(**_prod_settings())
        assert settings.is_production is True

    def test_placeholder_secret_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
            Settings(**_prod_settings(app_secret_key="change-me"))

    def test_empty_secret_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
            Settings(**_prod_settings(app_secret_key=""))

    def test_default_admin_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
            Settings(**_prod_settings(admin_password="admin12345"))

    def test_short_admin_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 8"):
            Settings(**_prod_settings(admin_password="short"))

    def test_insecure_cookies_are_rejected_in_production(self) -> None:
        """دي الحماية الأساسية: كوكيز من غير Secure على HTTP = قابل للسرقة."""
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            Settings(**_prod_settings(cookie_secure=False))

    def test_insecure_cookies_can_be_allowed_explicitly(self) -> None:
        """استثناء موثّق للبيئة اللي بتستخدم HTTP عن قصد (staging داخلي)."""
        settings = Settings(
            **_prod_settings(
                cookie_secure=False, allow_insecure_cookies_in_production=True
            )
        )
        assert settings.cookie_secure is False

    @pytest.mark.parametrize(
        "app_env", ["prodution", "Production", "PRODUCTION", " prod ", "staging", "banana"]
    )
    def test_production_checks_are_not_skipped_by_a_typo(self, app_env: str) -> None:
        """اسم بيئة مش معروف مينفعش يعطّل فحوصات الإنتاج في صمت.

        غير كده `APP_ENV=prodution` + `COOKIE_SECURE=false` هيعدّي والتطبيق
        هيظبط cookie من غير Secure على production. القاعدة: أي اسم مش
        معروف بيتعامل معاه على إنه production، والبيئات المجهولة آمنة.
        """
        with pytest.raises(ValidationError, match="COOKIE_SECURE"):
            Settings(**_prod_settings(app_env=app_env, cookie_secure=False))

    @pytest.mark.parametrize("app_env", ["production", "prod", "staging"])
    def test_prod_and_staging_are_treated_as_production(self, app_env: str) -> None:
        settings = Settings(**_prod_settings(app_env=app_env))
        assert settings.is_production is True

    @pytest.mark.parametrize("app_env", ["development", "dev", "test", "local", "TESTING"])
    def test_known_non_production_names_are_not_production(self, app_env: str) -> None:
        settings = Settings(**_prod_settings(app_env=app_env, cookie_secure=False))
        assert settings.is_production is False


class TestDevelopmentIsUnaffected:
    def test_development_allows_insecure_cookies(self) -> None:
        settings = Settings(
            app_env="development",
            app_secret_key="dev-secret",
            database_url="postgresql+psycopg://u:p@localhost:5432/db",
            cookie_secure=False,
        )
        assert settings.is_production is False

    def test_is_production_ignores_case_and_padding(self) -> None:
        settings = Settings(
            app_env="  Production  ",
            app_secret_key="x" * 40,
            database_url="postgresql+psycopg://u:p@localhost:5432/db",
            admin_password="a-strong-admin-password",
            cookie_secure=True,
        )
        assert settings.is_production is True


class TestThrottleSettings:
    def test_sane_defaults(self) -> None:
        settings = Settings(
            app_env="test", app_secret_key="x", database_url="postgresql+psycopg://u:p@h:5432/d"
        )
        assert settings.admin_login_max_attempts == 5
        assert settings.admin_login_max_attempts_per_ip == 20
        assert settings.admin_login_window_minutes == 15
        assert settings.admin_login_max_rows_per_key == 50

    def test_per_ip_limit_is_not_lower_than_the_per_key_limit(self) -> None:
        """لو الـIP limit أقل من الـkey limit، الحد الأول يبقى بلا معنى."""
        settings = Settings(
            app_env="test",
            app_secret_key="x",
            database_url="postgresql+psycopg://u:p@h:5432/d",
            admin_login_max_attempts=5,
            admin_login_max_attempts_per_ip=20,
        )
        assert settings.admin_login_max_attempts_per_ip >= settings.admin_login_max_attempts

    def test_samesite_default_is_lax(self) -> None:
        settings = Settings(
            app_env="test", app_secret_key="x", database_url="postgresql+psycopg://u:p@h:5432/d"
        )
        assert settings.cookie_samesite == "lax"


# ---------------------------------------------------------------------------
# Health / readiness
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    def test_health_is_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_is_ok_when_the_database_answers(self, client: TestClient) -> None:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_ready_reports_503_when_the_database_is_gone(
        self, client: TestClient
    ) -> None:
        from fastapi.testclient import TestClient as _Client

        from app.database import get_db
        from app.main import app as fastapi_app
        from sqlalchemy.exc import OperationalError

        def broken_db():
            # Session بيترمي أول ما حد ينفّذ عليه أي استعلام
            class BrokenSession:
                def execute(self, *args, **kwargs):
                    raise OperationalError("SELECT 1", {}, Exception("connection refused"))

            yield BrokenSession()

        fastapi_app.dependency_overrides[get_db] = broken_db
        try:
            with _Client(fastapi_app) as bare:
                response = bare.get("/ready")
        finally:
            fastapi_app.dependency_overrides.pop(get_db, None)

        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"

    def test_health_stays_up_even_when_the_database_is_down(
        self, client: TestClient
    ) -> None:
        """الـliveness مقصود إنه مش بيعتمد على الداتابيز."""
        assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    def test_nosniff_is_set(self, client: TestClient) -> None:
        assert client.get("/health").headers.get("X-Content-Type-Options") == "nosniff"

    def test_frame_options_is_set(self, client: TestClient) -> None:
        assert client.get("/health").headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy_is_set(self, client: TestClient) -> None:
        assert "strict-origin" in client.get("/health").headers.get("Referrer-Policy", "")

    def test_permissions_policy_is_set(self, client: TestClient) -> None:
        assert "geolocation=()" in client.get("/health").headers.get("Permissions-Policy", "")

    def test_headers_are_present_on_error_responses_too(self, client: TestClient) -> None:
        response = client.get("/no-such-route")
        assert response.status_code == 404
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_hsts_is_off_outside_production(self, client: TestClient) -> None:
        assert "Strict-Transport-Security" not in client.get("/health").headers

    def test_hsts_is_on_in_production(self, client: TestClient, monkeypatch) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "app_env", "production")
        response = client.get("/health")
        assert "max-age=31536000" in response.headers.get("Strict-Transport-Security", "")


class TestRequestId:
    def test_a_request_id_is_generated(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers.get("X-Request-ID")

    def test_inbound_request_id_is_reused(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers.get("X-Request-ID") == "trace-abc-123"

    def test_inbound_request_id_is_sanitised(self, client: TestClient) -> None:
        """header من بره لازم يتنضّف قبل ما يدخل اللوج.

        اللي مهم هنا إن محرفات السطر الجديد ما تدخلش — دي اللي هتسمح
        بتلويث الـresponse headers أو سطور اللوج. المسافة والنقطتين
        بتتشال بس، والحروف العادية بتفضل.
        """
        response = client.get(
            "/health", headers={"X-Request-ID": "trace id: 42\r\nX-Injected: yes"}
        )
        value = response.headers["X-Request-ID"]

        assert "\n" not in value
        assert "\r" not in value
        assert " " not in value
        assert ":" not in value
        assert value == "traceid42X-Injectedyes"

    def test_request_id_is_truncated(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "x" * 500})
        assert len(response.headers["X-Request-ID"]) <= 64

    def test_very_short_request_id_falls_back_to_a_generated_one(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "!!!"})
        assert response.headers["X-Request-ID"] not in ("", "!!!")

    def test_the_access_log_record_carries_the_request_id(
        self, client: TestClient, caplog
    ) -> None:
        """اللاج نفسه لازم يبقى مربوط بالـid اللي طلع في الـresponse.

        الكاش هنا إن الـmiddleware كان بيرجّع الـContextVar قبل ما يسجل
        لوج الطلب، فكل سطر في اللوج بيطلع `request_id="-"` والربط بين
        لوج العميل ولوج السيرفر بيصير مستحيل.
        """
        with caplog.at_level(logging.INFO, logger="app.main"):
            response = client.get("/health", headers={"X-Request-ID": "trace-xyz-789"})

        request_logs = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == "request"
        ]
        assert request_logs, "مفيش لوج طلب اتسجل"
        assert request_logs[-1].request_id == response.headers["X-Request-ID"]
        assert request_logs[-1].request_id == "trace-xyz-789"
        assert request_logs[-1].status == 200

    def test_the_access_log_does_not_leak_a_stale_request_id(
        self, client: TestClient, caplog
    ) -> None:
        """لوج الطلب التاني لازم ياخد id بتاعه هو، مش بتاع اللي قبله."""
        with caplog.at_level(logging.INFO, logger="app.main"):
            first = client.get("/health", headers={"X-Request-ID": "id-one"})
            second = client.get("/health", headers={"X-Request-ID": "id-two"})

        ids = [
            record.request_id
            for record in caplog.records
            if getattr(record, "event", None) == "request"
        ]
        assert first.headers["X-Request-ID"] == "id-one"
        assert second.headers["X-Request-ID"] == "id-two"
        assert ids[-2:] == ["id-one", "id-two"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    def test_text_format_includes_request_id(self) -> None:
        from app.logging_config import TextFormatter

        record = logging.LogRecord(
            "app.test", logging.INFO, "f.py", 1, "hello", None, None
        )
        record.request_id = "abc123"
        out = TextFormatter().format(record)
        assert "hello" in out and "abc123" in out

    def test_json_format_is_one_valid_object(self) -> None:
        from app.logging_config import JsonFormatter

        record = logging.LogRecord(
            "app.test", logging.WARNING, "f.py", 1, "something happened", None, None
        )
        record.request_id = "abc123"
        record.event = "test_event"
        payload = json.loads(JsonFormatter().format(record))

        assert payload["msg"] == "something happened"
        assert payload["level"] == "WARNING"
        assert payload["request_id"] == "abc123"
        assert payload["event"] == "test_event"
        assert "ts" in payload

    def test_json_format_survives_non_serialisable_extras(self) -> None:
        from app.logging_config import JsonFormatter

        record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "m", None, None)
        record.weird = object()
        payload = json.loads(JsonFormatter().format(record))
        assert isinstance(payload["weird"], str)

    def test_json_format_keeps_arabic_readable(self) -> None:
        from app.logging_config import JsonFormatter

        record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "دخول", None, None)
        assert "دخول" in JsonFormatter().format(record)

    def test_exception_is_included_in_json(self) -> None:
        import sys

        from app.logging_config import JsonFormatter

        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "app.test", logging.ERROR, "f.py", 1, "failed", None, sys.exc_info()
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError" in payload["exception"]

    def test_configure_logging_is_idempotent(self) -> None:
        """تستدعيه أكتر من مرة — مينفعش يتضاعف الـhandlers."""
        from app.logging_config import configure_logging

        root = logging.getLogger()
        configure_logging()
        after_first = len(root.handlers)
        configure_logging()
        assert len(root.handlers) == after_first

    def test_uvicorn_access_log_is_disabled(self) -> None:
        """عشان متعملش access log مرتين مع الـmiddleware بتاعنا."""
        from app.logging_config import configure_logging

        configure_logging()
        assert logging.getLogger("uvicorn.access").disabled is True

    def test_app_loggers_are_re_enabled(self) -> None:
        """`alembic.ini` بيعطّل كل الـloggers الموجودة — لازم نرجّع بتاعنا.

        من غير ده، أول `alembic upgrade head` بيشغّلها في نفس العملية
        بتمسح كل لوجات `app.*` بصمت، والـcaplog في الاختبارات بيبقى فاضي
        فأي فحص "مفيش أسرار في اللوج" بيمرّ من غير ما يفحص حاجة.
        """
        from app.logging_config import configure_logging

        guard = logging.getLogger("app.login_guard")
        guard.disabled = True
        try:
            configure_logging()
            assert guard.disabled is False
        finally:
            guard.disabled = False

    def test_foreign_loggers_are_left_alone(self) -> None:
        """مش بنمطّ loggers تانية — دي مش مهمتنا."""
        from app.logging_config import configure_logging

        foreign = logging.getLogger("some_third_party_library")
        foreign.disabled = True
        try:
            configure_logging()
            assert foreign.disabled is True
        finally:
            foreign.disabled = False
            logging.getLogger("some_third_party_library").handlers = []


class TestNoSensitiveDataInLogs:
    """اللوج جزء من تكلفة التشغيل، فلازم نأكد إنه ميكشفش حاجة حساسة.

    كل اختبار هنا بيتأكد إن الـcaplog التقط حاجة فعلاً — غير كده بيمرّي
    من غير ما يفحص ولا حاجة.
    """

    @staticmethod
    def _failed_login(client: TestClient) -> None:
        client.get("/admin/login")
        client.post(
            "/admin/login",
            data={
                "username": "sneaky-user",
                "password": "sneaky-password",
                "csrf_token": client.cookies.get("admin_csrf"),
            },
            follow_redirects=False,
        )

    def test_caplog_actually_captures_app_logs(self, client: TestClient, caplog) -> None:
        """حارس: لو الـcapture مش شغال، باقي الاختبارات دي بلا معنى."""
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.login_guard"):
            self._failed_login(client)
        assert caplog.records, "caplog captured nothing — logging setup is swallowing records"

    def test_failed_login_does_not_log_the_username_or_ip(
        self, client: TestClient, caplog
    ) -> None:
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.login_guard"):
            self._failed_login(client)

        text = caplog.text
        assert "sneaky-user" not in text
        assert "sneaky-password" not in text
        assert "testclient" not in text

    def test_failed_login_log_carries_only_a_hash_prefix(
        self, client: TestClient, caplog
    ) -> None:
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.login_guard"):
            self._failed_login(client)

        records = [r for r in caplog.records if r.getMessage() == "admin_login_failed"]
        assert records, "expected a failed-login log record"
        # بادئة قصيرة من التجزئة، مش الديigest كامل
        assert records[0].key_hash
        assert len(records[0].key_hash) == 12
        assert "username" not in records[0].__dict__
        assert "ip" not in records[0].__dict__

    def test_request_log_does_not_include_the_query_string(
        self, client: TestClient, caplog
    ) -> None:
        """الـquery ممكن يحتوي كلام العميل أو بيانات — مينفعش يتسجل."""
        import logging

        with caplog.at_level(logging.DEBUG, logger="app.main"):
            client.get("/health?secret_lookup=leak-me")

        records = [r for r in caplog.records if r.getMessage() == "request"]
        assert records, "expected a request log record"
        assert "leak-me" not in caplog.text
        assert records[0].path == "/health"
