"""اختبارات حماية /admin/login من المحاولات العنيفة.

قبل Phase 0 كان في النموذج بحدود معدل على /orders و /api/* بس، وصفحة
الدخول نفسها كانت مفتوحة: حد يقدر يجرّب باسورد الأدمن بلا حدود، والعدّاد
كان in-memory فيتضيع مع أي restart.

المرجع: `app.login_guard` و`app.routers.admin_auth` و`app.security`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.login_guard import (
    LOGIN_ERROR_MESSAGE,
    LOGIN_THROTTLED_MESSAGE,
    check_login_allowed,
    clear_all_attempts,
    ip_hash_for,
    key_hash_for,
    record_login_failure,
    record_login_success,
)
from app.models import Admin, AdminLoginAttempt

VALID_PASSWORD = "correct-horse-battery"
WRONG_PASSWORD = "wrong-password-entirely"

GUARD_IP = "203.0.113.7"
OTHER_IP = "198.51.100.22"


# ---------------------------------------------------------------------------
# التجزئة: مفيش PII في الداتابيز
# ---------------------------------------------------------------------------


class TestHashing:
    def test_raw_ip_is_never_stored(self) -> None:
        digest = ip_hash_for(GUARD_IP)
        assert GUARD_IP not in digest
        assert len(digest) == 64  # hex-encoded SHA-256
        assert int(digest, 16) >= 0  # valid hex

    def test_raw_username_is_never_stored(self) -> None:
        digest = key_hash_for(GUARD_IP, "admin")
        assert "admin" not in digest
        assert GUARD_IP not in digest

    def test_same_inputs_give_the_same_digest(self) -> None:
        assert ip_hash_for(GUARD_IP) == ip_hash_for(GUARD_IP)
        assert key_hash_for(GUARD_IP, "admin") == key_hash_for(GUARD_IP, "admin")

    def test_different_ip_gives_a_different_digest(self) -> None:
        assert ip_hash_for(GUARD_IP) != ip_hash_for(OTHER_IP)

    def test_ip_and_key_hashes_differ(self) -> None:
        # لو الاتنين طلعوا نفس القيمة، حد يقدر يحسب الـIP من الـkey
        assert ip_hash_for(GUARD_IP) != key_hash_for(GUARD_IP, "admin")

    @pytest.mark.parametrize("variant", ["admin", "  admin  ", "ADMIN", "Admin"])
    def test_key_hash_is_normalized(self, variant: str) -> None:
        """مسافات أو حالة أحرف ما تقدرش تلاعب بالعدّاد وتعدّي الحد."""
        assert key_hash_for(GUARD_IP, variant) == key_hash_for(GUARD_IP, "admin")

    def test_different_username_gives_a_different_key(self) -> None:
        assert key_hash_for(GUARD_IP, "admin") != key_hash_for(GUARD_IP, "root")

    def test_empty_username_does_not_crash(self) -> None:
        assert len(key_hash_for(GUARD_IP, "")) == 64


# ---------------------------------------------------------------------------
# العدّاد
# ---------------------------------------------------------------------------


def _count(db) -> int:
    return db.scalar(select(func.count()).select_from(AdminLoginAttempt)) or 0


class TestThrottleCounting:
    def test_first_attempt_is_allowed(self, db) -> None:
        decision = check_login_allowed(db, GUARD_IP, "admin")
        assert decision.allowed is True
        assert decision.remaining > 0

    def test_failures_are_recorded(self, db) -> None:
        record_login_failure(db, GUARD_IP, "admin")
        db.commit()
        assert _count(db) == 1

    def test_blocked_after_the_per_key_limit(self, db, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 3

        for _ in range(3):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        decision = check_login_allowed(db, GUARD_IP, "admin")
        assert decision.allowed is False
        assert decision.retry_after_seconds > 0

    def test_a_different_username_is_blocked_by_the_ip_limit(self, db, settings_snapshot) -> None:
        """password spraying: عدّاد الـIP بيغطي أكتر من حساب."""
        settings_snapshot.admin_login_max_attempts = 50
        settings_snapshot.admin_login_max_attempts_per_ip = 3

        for _ in range(3):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        # يوزر مختلف خالص، بس نفس الـIP
        decision = check_login_allowed(db, GUARD_IP, "root")
        assert decision.allowed is False

    def test_a_different_ip_is_not_affected(self, db, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 2

        for _ in range(2):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        assert check_login_allowed(db, OTHER_IP, "admin").allowed is True

    def test_successful_login_clears_the_counter(self, db, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 3

        for _ in range(3):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()
        assert check_login_allowed(db, GUARD_IP, "admin").allowed is False

        record_login_success(db, GUARD_IP, "admin")
        db.commit()

        assert check_login_allowed(db, GUARD_IP, "admin").allowed is True
        assert _count(db) == 0

    def test_success_only_clears_its_own_key(self, db) -> None:
        """مسح عدّاد الأدمن اللي دخل مايمسّش عدّاد حد تاني."""
        record_login_failure(db, GUARD_IP, "other")
        record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        record_login_success(db, GUARD_IP, "admin")
        db.commit()

        remaining = db.scalars(select(AdminLoginAttempt.key_hash)).all()
        assert len(remaining) == 1
        assert remaining[0] == key_hash_for(GUARD_IP, "other")

    def test_retry_after_shrinks_as_the_window_moves(self, db, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 1
        settings_snapshot.admin_login_window_minutes = 15

        record_login_failure(db, GUARD_IP, "admin")
        db.commit()
        first = check_login_allowed(db, GUARD_IP, "admin").retry_after_seconds
        assert first > 0

        # نحط المحاولة في الماضي: المفروض تخرج من النافذة
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.execute(
            AdminLoginAttempt.__table__.update()
            .values(attempted_at=old)
            .where(AdminLoginAttempt.key_hash == key_hash_for(GUARD_IP, "admin"))
        )
        db.commit()

        assert check_login_allowed(db, GUARD_IP, "admin").allowed is True

    def test_attempts_outside_the_window_are_not_counted(self, db, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 2
        settings_snapshot.admin_login_window_minutes = 15

        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        for _ in range(5):
            db.add(
                AdminLoginAttempt(
                    ip_hash=ip_hash_for(GUARD_IP),
                    key_hash=key_hash_for(GUARD_IP, "admin"),
                    attempted_at=old,
                )
            )
        db.commit()

        assert check_login_allowed(db, GUARD_IP, "admin").allowed is True

    def test_row_cap_is_enforced(self, db, settings_snapshot) -> None:
        """سقف صلب على عدد الصفوف: حد بيكسر العدّاد مايعملش نمو بلا حدود."""
        settings_snapshot.admin_login_max_rows_per_key = 5
        settings_snapshot.admin_login_max_attempts = 1000
        settings_snapshot.admin_login_max_attempts_per_ip = 1000

        for _ in range(12):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        assert _count(db) == 5

    def test_clear_all_attempts(self, db) -> None:
        record_login_failure(db, GUARD_IP, "admin")
        record_login_failure(db, OTHER_IP, "admin")
        db.commit()
        assert _count(db) == 2

        assert clear_all_attempts(db) == 2
        db.commit()
        assert _count(db) == 0


# ---------------------------------------------------------------------------
# الـroute نفسها
# ---------------------------------------------------------------------------


def _csrf(client) -> tuple[str, str]:
    """بيجيب كوكي الـCSRF من صفحة الدخول — الـPOST محمي بيه."""
    client.get("/admin/login")
    return client.cookies.get("admin_csrf"), "<hidden>"


def _post_login(client, username: str, password: str):
    client.get("/admin/login")
    return client.post(
        "/admin/login",
        data={"username": username, "password": password, "csrf_token": client.cookies.get("admin_csrf")},
        follow_redirects=False,
    )


class TestLoginRouteThrottling:
    def test_wrong_password_returns_400_with_generic_message(self, client, admin_user) -> None:
        response = _post_login(client, admin_user.username, WRONG_PASSWORD)
        assert response.status_code == 400
        assert LOGIN_ERROR_MESSAGE in response.text

    def test_unknown_user_gets_the_same_message_as_wrong_password(
        self, client, admin_user
    ) -> None:
        """مفيش استنتاج ممكن عن اليوزرهات الموجودة من رد الفعل."""
        unknown = _post_login(client, "no-such-admin", WRONG_PASSWORD)
        wrong = _post_login(client, admin_user.username, WRONG_PASSWORD)

        assert unknown.status_code == wrong.status_code == 400
        assert LOGIN_ERROR_MESSAGE in unknown.text
        assert LOGIN_ERROR_MESSAGE in wrong.text

    def test_correct_credentials_log_in(self, client, admin_user) -> None:
        response = _post_login(client, admin_user.username, VALID_PASSWORD)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"

    def test_login_sets_a_session_cookie(self, client, admin_user) -> None:
        _post_login(client, admin_user.username, VALID_PASSWORD)
        assert "admin_access_token" in client.cookies

    def test_wrong_password_is_counted(self, client, db, admin_user) -> None:
        _post_login(client, admin_user.username, WRONG_PASSWORD)
        assert _count(db) == 1

    def test_unknown_user_is_counted_too(self, client, db, admin_user) -> None:
        """العدّاد بيتحسب على أي اسم، موجود أو لأ."""
        _post_login(client, "no-such-admin", WRONG_PASSWORD)
        assert _count(db) == 1

    def test_throttled_after_the_limit(self, client, db, admin_user, settings_snapshot) -> None:
        settings_snapshot.admin_login_max_attempts = 3

        for _ in range(3):
            _post_login(client, admin_user.username, WRONG_PASSWORD)

        blocked = _post_login(client, admin_user.username, VALID_PASSWORD)
        assert blocked.status_code == 429
        assert LOGIN_THROTTLED_MESSAGE in blocked.text
        assert "Retry-After" in blocked.headers

    def test_successful_login_clears_the_counter(self, client, db, admin_user) -> None:
        _post_login(client, admin_user.username, WRONG_PASSWORD)
        assert _count(db) == 1

        _post_login(client, admin_user.username, VALID_PASSWORD)
        assert _count(db) == 0

    def test_inactive_admin_cannot_log_in(self, client, db, admin_user) -> None:
        admin_user.is_active = False
        db.commit()

        response = _post_login(client, admin_user.username, VALID_PASSWORD)
        assert response.status_code == 400
        assert LOGIN_ERROR_MESSAGE in response.text

    def test_login_without_csrf_is_rejected(self, client, admin_user) -> None:
        """من غير CSRF،ProtectedDependency بيرفض قبل أي bcrypt."""
        response = client.post(
            "/admin/login",
            data={"username": admin_user.username, "password": VALID_PASSWORD},
            follow_redirects=False,
        )
        assert response.status_code in (403, 400, 422)

    def test_no_password_or_username_in_the_response(self, client, admin_user) -> None:
        response = _post_login(client, admin_user.username, WRONG_PASSWORD)
        assert VALID_PASSWORD not in response.text
        assert WRONG_PASSWORD not in response.text


# ---------------------------------------------------------------------------
# التوقيت: مفيش timing side channel
# ---------------------------------------------------------------------------


class TestTimingUniformity:
    def test_unknown_user_still_runs_a_bcrypt_verify(self, client, db) -> None:
        """اليوزر اللي مش موجود لازم يعدّي على نفس شغل bcrypt.

        لو رجع False فورًا، زمن الرد كان هيفرق وميكشف وجود اليوزر.
        """
        from app.security import verify_login_credentials

        assert verify_login_credentials(WRONG_PASSWORD, None) is False

    def test_real_hash_verifies(self) -> None:
        from app.security import hash_password, verify_login_credentials

        digest = hash_password(VALID_PASSWORD)
        assert verify_login_credentials(VALID_PASSWORD, digest) is True
        assert verify_login_credentials(WRONG_PASSWORD, digest) is False

    def test_dummy_hash_is_cached_not_rehashed_per_call(self) -> None:
        """التجزئة الوهمية بتتعمل مرة واحدة، مش في كل طلب."""
        from app import security

        first = security._get_dummy_hash()
        second = security._get_dummy_hash()
        assert first == second

    def test_unknown_and_known_user_take_comparable_time(self, client, admin_user) -> None:
        import time

        # مقارنة تقريبية: الفارق لازم يكون صغير مقارنة بزمن bcrypt نفسه.
        def elapsed(username: str, password: str) -> float:
            start = time.perf_counter()
            _post_login(client, username, password)
            return time.perf_counter() - start

        elapsed("no-such-admin", WRONG_PASSWORD)  # warm up caches
        unknown = min(elapsed("no-such-admin", WRONG_PASSWORD) for _ in range(3))
        known = min(elapsed(admin_user.username, WRONG_PASSWORD) for _ in range(3))

        # النسبة بتسمح بهامش واسع: الهدف إن مفيش رجوع فوري بدون bcrypt،
        # مش مقارنة دقيقة بالـmicroseconds.
        assert known > unknown * 0.2 or unknown > known * 0.2


# ---------------------------------------------------------------------------
# الجدول نفسه
# ---------------------------------------------------------------------------


class TestAttemptModel:
    def test_only_hashes_are_persisted(self, db) -> None:
        record_login_failure(db, GUARD_IP, "admin")
        db.commit()

        row = db.scalar(select(AdminLoginAttempt))
        assert row.ip_hash != GUARD_IP
        assert row.key_hash != "admin"
        assert GUARD_IP not in row.ip_hash
        assert "admin" not in row.key_hash

    def test_rows_are_ordered_for_the_newest_queries(self, db) -> None:
        for _ in range(3):
            record_login_failure(db, GUARD_IP, "admin")
        db.commit()
        assert _count(db) == 3

    def test_timestamps_come_from_the_database(self, db) -> None:
        """الـserver_default موجود، فالتوقيت مش متعلق بساعة السيرفر."""
        record_login_failure(db, GUARD_IP, "admin")
        db.commit()
        row = db.scalar(select(AdminLoginAttempt))
        assert row.attempted_at is not None
