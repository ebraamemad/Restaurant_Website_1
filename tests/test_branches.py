"""اختبارات Phase 1: الفروع، القناة، ساعات استقبال الطلبات.

كل حاجة هنا بتتأكد من قواعد العمل اللي اتفق عليها:

1. **كل طلب لازم يكون مربوط بفرع.** العمود `orders.branch_id` NOT NULL،
   والعميل اللي مش بيبعت فرع بينحسب له الفرع الافتراضي — يعني مفيش
   طريق يخلّي الطلب يتحفظ من غير فرع.
2. **الفرع المعطّل أو المجهول مرفوض.** العميل يستحيل يطلب منه.
3. **`system` و`whatsapp` طلب واحد في نفس الجدول.** الفرق في التحويل بس،
   والطلب لازم يكون اتحفظ في الداتابيز قبل أي رسالة واتساب.
4. **ساعات الاستقبال globals على مستوى المطعم**، وبتفهم الراحة اللي
   بتعدّي نص الليل (18:00 → 02:00).

المرجع: `app/models.py`, `app/utils.py`, `app/routers/public.py`,
`app/routers/admin.py`, `alembic/versions/c3d4e5f6a7b8_phase1_branches.py`.
"""

from __future__ import annotations

from datetime import time as dt_time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def menu(db):
    """منتج واحد متاح، عشان نقدر نبني طلب فعلي."""
    from app.models import Category, Product

    category = Category(
        name="أصناف", slug="p1-category", sort_order=0, is_active=True
    )
    db.add(category)
    db.flush()
    product = Product(
        category_id=category.id,
        name="منتج تجريبي",
        slug="p1-product",
        base_price=Decimal("50.00"),
        unit="piece",
        is_available=True,
        is_active=True,
        sort_order=0,
    )
    db.add(product)
    db.commit()
    return product


@pytest.fixture()
def default_branch(db):
    """الفرع اللي الـmigration بيعمله (المرجع الأساسي للاختبارات)."""
    from app.models import Branch

    branch = db.query(Branch).filter(Branch.is_default.is_(True)).one()
    return branch


@pytest.fixture()
def second_branch(db, default_branch):
    from app.models import Branch

    branch = Branch(
        name="فرع المعادي",
        address="شارع 9، المعادي",
        phone="01099887766",
        opening_hours="10:00 — 02:00",
        is_active=True,
        is_default=False,
    )
    db.add(branch)
    db.commit()
    return branch


def _payload(product_id: int, **overrides) -> dict:
    payload = {
        "customer_name": "عميل تجريبي",
        "customer_phone": "01012345678",
        "customer_address": "عنوان تجريبي",
        "items": [{"product_id": product_id, "quantity": 1}],
    }
    payload.update(overrides)
    return payload


def _csrf(client) -> str:
    client.get("/admin/login")
    return client.cookies.get("admin_csrf")


def _login_admin(client) -> None:
    client.post(
        "/admin/login",
        data={
            "username": "testadmin",
            "password": "correct-horse-battery",
            "csrf_token": _csrf(client),
        },
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# 1) قاعدة "كل طلب مربوط بفرع"
# ---------------------------------------------------------------------------


class TestEveryOrderHasABranch:
    def test_order_persists_with_the_chosen_branch(
        self, client: TestClient, db, menu, second_branch
    ) -> None:
        from app.models import Order

        response = client.post(
            "/orders", json=_payload(menu.id, branch_id=second_branch.id)
        )
        assert response.status_code in (200, 201), response.text
        assert response.json()["branch_id"] == second_branch.id

        order = db.query(Order).order_by(Order.id.desc()).first()
        assert order.branch_id == second_branch.id
        # المرجع لازم يتخزن كـFK فعلية مش مجرد رقم
        assert order.branch is not None
        assert order.branch.name == "فرع المعادي"

    def test_legacy_client_without_branch_lands_on_the_default(
        self, client: TestClient, db, menu, default_branch
    ) -> None:
        """عميل V1 بيبعت من غير فرع — لازم يتحوّل للفرع الافتراضي."""
        from app.models import Order

        response = client.post("/orders", json=_payload(menu.id))
        assert response.status_code in (200, 201), response.text
        assert response.json()["branch_id"] == default_branch.id
        assert db.query(Order).order_by(Order.id.desc()).first().branch_id == (
            default_branch.id
        )

    def test_branch_id_is_not_nullable_in_the_database(self) -> None:
        """الضمان الحقيقي في الداتابيز، مش في الكود بس."""
        from sqlalchemy import create_engine, text

        from tools.ensure_test_db import test_database_url

        engine = create_engine(test_database_url())
        try:
            with engine.connect() as conn:
                nullable = conn.execute(
                    text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name='orders' AND column_name='branch_id'"
                    )
                ).scalar()
            assert nullable == "NO"
        finally:
            engine.dispose()

    def test_unknown_branch_is_rejected(self, client: TestClient, menu) -> None:
        response = client.post("/orders", json=_payload(menu.id, branch_id=999999))
        assert response.status_code == 400
        assert "مش موجود" in response.json()["detail"]

    def test_inactive_branch_is_rejected(
        self, client: TestClient, db, menu, second_branch
    ) -> None:
        """العميل يستحيل يطلب من فرع معطّل، حتى لو بعت الـid يدوي."""
        from app.models import Order

        second_branch.is_active = False
        db.commit()

        response = client.post(
            "/orders", json=_payload(menu.id, branch_id=second_branch.id)
        )
        assert response.status_code == 400
        assert "مش متاح" in response.json()["detail"]
        assert db.query(Order).count() == 0, "مفيش طلب المفروض يتحفظ"

    def test_deleting_a_branch_with_orders_is_blocked(
        self, client: TestClient, db, menu, second_branch
    ) -> None:
        """الفرع اللي عليه طلبات ما بيتحذفش — عشان تاريخ الطلبات يفضل سليم."""
        from sqlalchemy.exc import IntegrityError

        from app.models import Order

        client.post("/orders", json=_payload(menu.id, branch_id=second_branch.id))
        assert db.query(Order).count() == 1

        db.delete(second_branch)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# 2) القناة: system vs WhatsApp
# ---------------------------------------------------------------------------


class TestOrderChannel:
    def test_whatsapp_is_the_default_and_returns_a_url(
        self, client: TestClient, db, menu
    ) -> None:
        from app.models import Order

        response = client.post("/orders", json=_payload(menu.id))
        assert response.status_code in (200, 201), response.text
        body = response.json()

        assert body["channel"] == "whatsapp"
        assert body["whatsapp_url"], "طلب واتساب لازم يرجّع رابط"

        order = db.query(Order).order_by(Order.id.desc()).first()
        assert order.channel == "whatsapp"
        # الرسالة بتتخزن كمان في الحالتين، زي V1 بالظبط
        assert order.whatsapp_message

    def test_system_order_is_saved_without_a_whatsapp_url(
        self, client: TestClient, db, menu
    ) -> None:
        from app.models import Order

        response = client.post("/orders", json=_payload(menu.id, channel="system"))
        assert response.status_code in (200, 201), response.text
        body = response.json()

        assert body["channel"] == "system"
        assert body["whatsapp_url"] is None, "طلب system ما بيبعتش لواتساب"

        order = db.query(Order).order_by(Order.id.desc()).first()
        assert order is not None, "الطلب لازم يكون اتحفظ في الداتابيز"
        assert order.channel == "system"
        # نفس الجدول، نفس الـstatus، مفيش جدول واتساب منفصل
        assert order.status == "pending"
        assert order.total > 0

    def test_both_channels_land_in_the_same_table(
        self, client: TestClient, db, menu
    ) -> None:
        from app.models import Order

        client.post("/orders", json=_payload(menu.id, channel="system"))
        client.post("/orders", json=_payload(menu.id, channel="whatsapp"))

        orders = db.query(Order).order_by(Order.id).all()
        assert len(orders) == 2
        assert {o.channel for o in orders} == {"system", "whatsapp"}
        assert len({o.id for o in orders}) == 2, "طلب منفصل لكل قناة"

    def test_the_order_is_saved_before_any_whatsapp_handoff(
        self, client: TestClient, db, menu
    ) -> None:
        """الطلب في الداتابيز *قبل* ما العميل يحوّل لواتساب.

        لو رجع رابط واتساب، يبقى لازم يكون فيه صف طلب من نفس الرقم.
        """
        from app.models import Order

        response = client.post("/orders", json=_payload(menu.id))
        body = response.json()
        assert body["whatsapp_url"]
        assert db.query(Order).filter(Order.id == body["order_id"]).count() == 1
    @pytest.mark.parametrize("channel", ["telegram", "email", "WHATSAPP", ""])
    def test_unknown_channels_are_rejected_by_the_schema(
        self, client: TestClient, menu, channel: str
    ) -> None:
        response = client.post(
            "/orders", json=_payload(menu.id, channel=channel)
        )
        assert response.status_code == 422

    def test_the_database_rejects_an_unknown_channel(
        self, client: TestClient, db, menu
    ) -> None:
        """الـcheck constraint في الداتابيز هو خط الدفاع الأخير.

        بنستخدم نفس الـsession عشان الصف يكون مرئي — الـfixture بيلف كل
        التغييرات في transaction واحد بيرجع في الآخر.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        response = client.post("/orders", json=_payload(menu.id))
        order_id = response.json()["order_id"]

        with pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE orders SET channel='telegram' WHERE id=:oid"),
                {"oid": order_id},
            )
        db.rollback()


# ---------------------------------------------------------------------------
# 3) ساعات استقبال الطلبات (globals، وبتفهم الراحة اللي بتعدّي نص الليل)
# ---------------------------------------------------------------------------


class TestOrderingHours:
    @pytest.mark.parametrize(
        ("open_at", "close_at", "now", "expected"),
        [
            # حالة يومية عادية
            ("10:00", "22:00", "12:00", True),
            ("10:00", "22:00", "09:59", False),
            ("10:00", "22:00", "22:00", False),
            # الراحة بتعدّي نص الليل: 18:00 → 02:00
            ("18:00", "02:00", "23:30", True),
            ("18:00", "02:00", "01:30", True),
            ("18:00", "02:00", "02:00", False),
            ("18:00", "02:00", "12:00", False),
            ("18:00", "02:00", "17:59", False),
            ("18:00", "02:00", "18:00", True),
            # وقت الفتح = وقت الإغلاق يعني 24 ساعة مفتوح
            ("00:00", "00:00", "03:15", True),
            # مفيش ساعات محددة = مفتوح طول الوقت
            (None, None, "03:15", True),
            ("10:00", None, "03:15", True),
            (None, "22:00", "03:15", True),
        ],
    )
    def test_window_logic(self, open_at, close_at, now, expected) -> None:
        from app.utils import is_within_ordering_hours

        open_time = dt_time.fromisoformat(open_at) if open_at else None
        close_time = dt_time.fromisoformat(close_at) if close_at else None
        assert is_within_ordering_hours(open_time, close_time, dt_time.fromisoformat(now)) is expected

    def test_a_closed_window_blocks_order_creation(
        self, client: TestClient, db, menu, monkeypatch
    ) -> None:
        from app.models import Order, RestaurantSettings
        from app.routers import public as public_router

        settings = db.query(RestaurantSettings).one_or_none()
        if settings is None:
            settings = RestaurantSettings()
            db.add(settings)
        settings.ordering_open_time = dt_time(10, 0)
        settings.ordering_close_time = dt_time(12, 0)
        db.commit()

        # نثبّت الوقت جوه النافذة المغلقة عشان الـtest يبقى deterministic
        monkeypatch.setattr(
            public_router, "is_within_ordering_hours", lambda o, c: False
        )

        response = client.post("/orders", json=_payload(menu.id))
        assert response.status_code == 403
        assert "الطلب عبر الموقع" in response.json()["detail"]
        assert db.query(Order).count() == 0, "مفيش طلب المفروض يتحفظ"

    def test_an_open_window_allows_order_creation(
        self, client: TestClient, menu
    ) -> None:
        response = client.post("/orders", json=_payload(menu.id))
        assert response.status_code in (200, 201), response.text

    def test_ordering_status_endpoint_reports_the_window(self, client: TestClient) -> None:
        body = client.get("/api/ordering-status").json()
        assert set(body) == {
            "is_open",
            "open_time",
            "close_time",
            "overnight",
            "message",
        }
        # مفيش ساعات متظبطة = مفتوح 24 ساعة، زي V1
        assert body["is_open"] is True
        assert body["open_time"] is None

    def test_overnight_window_is_flagged(self, client: TestClient, db) -> None:
        from app.models import RestaurantSettings

        settings = db.query(RestaurantSettings).one_or_none()
        if settings is None:
            settings = RestaurantSettings()
            db.add(settings)
        settings.ordering_open_time = dt_time(18, 0)
        settings.ordering_close_time = dt_time(2, 0)
        db.commit()

        body = client.get("/api/ordering-status").json()
        assert body["open_time"] == "18:00"
        assert body["close_time"] == "02:00"
        assert body["overnight"] is True
        assert "18:00" in body["message"]

    @pytest.mark.parametrize("raw", ["25:99", "abc", "10:00:30"])
    def test_malformed_hours_are_rejected(self, raw: str) -> None:
        from app.utils import parse_ordering_time

        with pytest.raises(ValueError):
            parse_ordering_time(raw, "الفتح")

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_empty_hours_mean_no_limit(self, raw) -> None:
        from app.utils import parse_ordering_time

        assert parse_ordering_time(raw, "الفتح") is None


# ---------------------------------------------------------------------------
# 4) الفرع الافتراضي — واحد بس
# ---------------------------------------------------------------------------


class TestSingleDefaultBranch:
    def test_the_migration_creates_exactly_one_default(self, db) -> None:
        from app.models import Branch

        defaults = db.query(Branch).filter(Branch.is_default.is_(True)).all()
        assert len(defaults) == 1
        assert defaults[0].name

    def test_the_database_rejects_two_defaults(
        self, client: TestClient, db, second_branch
    ) -> None:
        """الفهرس الجزئي `ix_branches_single_default` هو الضمان الحقيقي."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE branches SET is_default = TRUE WHERE id = :bid"),
                {"bid": second_branch.id},
            )
        db.rollback()

    def test_admin_can_move_the_default_flag(
        self, client: TestClient, db, admin_user, second_branch
    ) -> None:
        from app.models import Branch

        _login_admin(client)
        response = client.post(
            f"/admin/branches/{second_branch.id}/default",
            data={"csrf_token": _csrf(client)},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), response.text

        defaults = db.query(Branch).filter(Branch.is_default.is_(True)).all()
        assert [b.id for b in defaults] == [second_branch.id], "لازم يفضل واحد بس"

    def test_an_inactive_branch_cannot_become_default(
        self, client: TestClient, db, admin_user, second_branch
    ) -> None:
        from app.models import Branch

        second_branch.is_active = False
        db.commit()

        _login_admin(client)
        response = client.post(
            f"/admin/branches/{second_branch.id}/default",
            data={"csrf_token": _csrf(client)},
            follow_redirects=False,
        )
        assert response.status_code == 400

        old_default = db.query(Branch).filter(Branch.is_default.is_(True)).one()
        assert old_default.name != "فرع المعادي", "الافتراضي القديم ما يتغيرش"


# ---------------------------------------------------------------------------
# 5) واجهة العميل للفروع
# ---------------------------------------------------------------------------


class TestPublicBranchesApi:
    def test_active_branches_are_listed(self, client: TestClient, second_branch) -> None:
        response = client.get("/api/branches")
        assert response.status_code == 200
        names = {b["name"] for b in response.json()}
        assert "فرع المعادي" in names

    def test_inactive_branches_are_hidden(self, client: TestClient, db, second_branch) -> None:
        second_branch.is_active = False
        db.commit()

        body = client.get("/api/branches").json()
        assert "فرع المعادي" not in {b["name"] for b in body}

    def test_the_default_branch_comes_first(
        self, client: TestClient, db, second_branch
    ) -> None:
        from app.models import Branch

        body = client.get("/api/branches").json()
        default = db.query(Branch).filter(Branch.is_default.is_(True)).one()
        assert body[0]["id"] == default.id

    def test_internal_flags_are_not_exposed(self, client: TestClient) -> None:
        """العميل مش محتاج `is_active` ولا `is_default` في الـpayload."""
        body = client.get("/api/branches").json()
        assert "is_active" not in body[0]
        assert "is_default" not in body[0]


# ---------------------------------------------------------------------------
# 6) إدارة الفروع في لوحة التحكم
# ---------------------------------------------------------------------------


class TestAdminBranchManagement:
    def test_admin_can_create_a_branch(self, client: TestClient, db, admin_user) -> None:
        from app.models import Branch

        _login_admin(client)
        response = client.post(
            "/admin/branches",
            data={
                "name": "فرع مدينة نصر",
                "address": "شارع عباس العقاد",
                "phone": "01011223344",
                "opening_hours": "10:00 — 02:00",
                "is_active": "true",
                "csrf_token": _csrf(client),
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), response.text

        branch = db.query(Branch).filter(Branch.name == "فرع مدينة نصر").one()
        assert branch.is_active is True
        # الفرع الجديد مش بياخد الفلاج الافتراضي من غير طلب صريح
        assert branch.is_default is False

    def test_admin_can_deactivate_a_branch(
        self, client: TestClient, db, admin_user, second_branch
    ) -> None:
        _login_admin(client)
        response = client.post(
            "/admin/branches",
            data={
                "id": str(second_branch.id),
                "name": second_branch.name,
                "is_active": "false",
                "csrf_token": _csrf(client),
            },
            follow_redirects=False,
        )
        assert response.status_code in (302, 303), response.text
        assert db.get(type(second_branch), second_branch.id).is_active is False

    def test_the_branches_page_loads(
        self, client: TestClient, db, admin_user, second_branch
    ) -> None:
        _login_admin(client)
        response = client.get("/admin/branches")
        assert response.status_code == 200
        assert "فرع المعادي" in response.text

    def test_the_branches_page_needs_admin(self, client: TestClient) -> None:
        response = client.get("/admin/branches", follow_redirects=False)
        assert response.status_code in (302, 303, 401, 403)
        assert "/admin/login" in response.headers.get("location", "")

    def test_creating_a_branch_requires_csrf(self, client: TestClient, admin_user) -> None:
        _login_admin(client)
        response = client.post(
            "/admin/branches",
            data={"name": "فرع من غير توكن"},
            follow_redirects=False,
        )
        assert response.status_code in (401, 403), response.text


# ---------------------------------------------------------------------------
# 7) ساعات الاستقبال بتتخزن صح
# ---------------------------------------------------------------------------


class TestSettingsStoreOrderingHours:
    def _post_settings(self, client, **overrides) -> None:
        data = {
            "restaurant_name": "مطعم تجريبي",
            "whatsapp_number": "01012345678",
            "ordering_open_time": "",
            "ordering_close_time": "",
            "csrf_token": _csrf(client),
        }
        data.update(overrides)
        return client.post(
            "/admin/settings", data=data, follow_redirects=False
        )

    def test_overnight_hours_are_persisted(
        self, client: TestClient, db, admin_user
    ) -> None:
        from app.models import RestaurantSettings

        _login_admin(client)
        response = self._post_settings(
            client, ordering_open_time="18:00", ordering_close_time="02:00"
        )
        assert response.status_code in (302, 303), response.text

        settings = db.query(RestaurantSettings).one()
        assert settings.ordering_open_time == dt_time(18, 0)
        assert settings.ordering_close_time == dt_time(2, 0)

    def test_blank_hours_mean_always_open(
        self, client: TestClient, db, admin_user
    ) -> None:
        from app.models import RestaurantSettings

        _login_admin(client)
        self._post_settings(
            client, ordering_open_time="18:00", ordering_close_time="02:00"
        )
        self._post_settings(client)

        settings = db.query(RestaurantSettings).one()
        assert settings.ordering_open_time is None
        assert settings.ordering_close_time is None
        assert client.get("/api/ordering-status").json()["is_open"] is True

    def test_half_a_window_is_rejected(self, client: TestClient, admin_user) -> None:
        _login_admin(client)
        response = self._post_settings(client, ordering_open_time="18:00")
        assert response.status_code == 400

    def test_malformed_hours_are_rejected_not_a_500(
        self, client: TestClient, admin_user
    ) -> None:
        _login_admin(client)
        response = self._post_settings(
            client, ordering_open_time="99:99", ordering_close_time="02:00"
        )
        assert response.status_code == 400

    def test_hours_are_global_not_per_branch(
        self, client: TestClient, db, admin_user, second_branch
    ) -> None:
        """ساعات الاستقبال على مستوى المطعم — الفروع مالهاش ساعات خاصة."""
        from app.models import Branch, RestaurantSettings

        _login_admin(client)
        self._post_settings(
            client, ordering_open_time="09:00", ordering_close_time="23:00"
        )

        settings = db.query(RestaurantSettings).one()
        assert settings.ordering_open_time == dt_time(9, 0)

        # مفيش عمود ساعات استقبال على الـBranch — `opening_hours` نص عرض فقط
        branch_columns = {c.name for c in Branch.__table__.columns}
        assert "ordering_open_time" not in branch_columns
        assert "ordering_close_time" not in branch_columns
        assert "opening_hours" in branch_columns


# ---------------------------------------------------------------------------
# 8) صفحة تأكيد الطلب في الموقع
# ---------------------------------------------------------------------------


class TestSystemOrderConfirmationPage:
    def test_the_success_page_shows_the_order_id(
        self, client: TestClient, menu
    ) -> None:
        created = client.post("/orders", json=_payload(menu.id, channel="system")).json()
        order_id = created["order_id"]

        # Phase 2: الصفحة بتطلب `token` مش `order_id`. رقم الطلب بقى
        # معلومة عرض بعد ما التحقق ينجح، مش وسيلة تفويض — أي حد كان
        # يقدر يفتح `/order/success?order_id=N` لأي N.
        response = client.get(f"/order/success?token={created['tracking_token']}")
        assert response.status_code == 200
        assert str(order_id) in response.text

    def test_a_token_from_another_order_does_not_open_this_page(
        self, client: TestClient, menu
    ) -> None:
        created = client.post("/orders", json=_payload(menu.id, channel="system")).json()
        response = client.get(
            f"/order/success?token=wrong&order_id={created['order_id']}"
        )
        assert response.status_code == 404

    def test_an_unknown_order_is_a_404(self, client: TestClient) -> None:
        assert client.get("/order/success?token=nope").status_code == 404


# ---------------------------------------------------------------------------
# 9) الطلبات في لوحة التحكم
# ---------------------------------------------------------------------------


class TestAdminOrdersShowBranchAndChannel:
    def test_a_system_order_is_visible_in_the_admin_list(
        self, client: TestClient, admin_user, menu
    ) -> None:
        _login_admin(client)
        order_id = client.post(
            "/orders", json=_payload(menu.id, channel="system")
        ).json()["order_id"]

        response = client.get("/admin/orders")
        assert response.status_code == 200
        assert f">{order_id}<" in response.text
        assert "من الموقع" in response.text

    def test_a_whatsapp_order_shows_its_branch(
        self, client: TestClient, admin_user, menu, second_branch
    ) -> None:
        _login_admin(client)
        client.post(
            "/orders", json=_payload(menu.id, branch_id=second_branch.id)
        )

        response = client.get("/admin/orders")
        assert response.status_code == 200
        assert "فرع المعادي" in response.text
        assert "عبر واتساب" in response.text
