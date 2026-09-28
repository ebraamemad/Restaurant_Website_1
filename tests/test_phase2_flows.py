"""اختبارات Phase 2: طرق الدفع، التتبّع، إعادة الطلب، `call_center`.

الفرق بين الملف ده وباقي الملفات: الباقي بيعمل assertions على response
واحدة. هنا بنمشي على **الرحلة كلها** بنفس العميل: ينشئ طلب، يلاقي
التوكن، يتتبعه، يعيد طلبه، والأدمن يحرّك الحالة. الغرض إن الـflows
اللي Phase 2 بنتها ما تفصلش عن بعضها.

المراجع: `app/order_tracking.py`, `app/ordering.py`,
`app/routers/api.py`, `app/routers/admin.py`,
`alembic/versions/d4e5f6a7b8c9_phase2_payments_tracking.py`.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response


# ---------------------------------------------------------------------------
# أدوات
# ---------------------------------------------------------------------------


def _login_admin(client: TestClient) -> None:
    """تسجيل دخول الأدمن على نفس الـclient، عشان الـsession يتبعت مع
    طلبات الـPOST اللي بتحتاج CSRF."""
    client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    client.post(
        "/admin/login",
        data={
            "username": "testadmin",
            "password": "correct-horse-battery",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )


@pytest.fixture()
def admin_client(client, admin_user):
    """`client` بعد ما يبقى داخل كـadmin.

    نفس الكوكي والـsession، عشان التحويل في نفس الاختبار بيبقى
    متسق: طلب `/orders` بـ`client` يوصل `/admin/orders/...` بـ
    `admin_client`.
    """
    _login_admin(client)
    return client


def admin_post(
    client: TestClient, url: str, data: dict, follow_redirects: bool = True
) -> Response:
    """POST للأدمن مع توكين CSRF.

    كل الـforms في `admin/*.html` بتحط `{{ csrf_field(request) }}`، فلو
    الـtest بيبعت من غيره بيرجّع 403. الـtest لازم يمشي بنفس آلية
    الـform الحقيقي، فالتوكين جزء من العقد مش تفصيل.
    """
    payload = dict(data)
    payload["csrf_token"] = client.cookies.get("admin_csrf")
    return client.post(url, data=payload, follow_redirects=follow_redirects)


def _order_payload(
    product_id: int,
    *,
    option_ids: list[int] | None = None,
    addon_ids: list[int] | None = None,
    delivery_option_id: int | None = None,
    channel: str = "system",
    payment_method_id: int | None = None,
) -> dict:
    payload: dict = {
        "customer_name": "عميل الاختبار",
        "customer_phone": "01012345678",
        "customer_address": "15 شارع النهضة، الدور التاني",
        "items": [
            {
                "product_id": product_id,
                "quantity": 2,
                "option_ids": option_ids or [],
                "addon_ids": addon_ids or [],
            }
        ],
        "channel": channel,
    }
    if delivery_option_id is not None:
        payload["delivery_option_id"] = delivery_option_id
    if payment_method_id is not None:
        payload["payment_method_id"] = payment_method_id
    return payload


def _cash_method_id(db) -> int:
    from app.models import PaymentMethod

    return db.query(PaymentMethod).filter_by(code="cash").one().id


@pytest.fixture()
def menu(db):
    """منتج بخيار وإضافة، لأن إعادة الطلب بتبني سطور من الخيارات.

    `product.options[0]` و`product.addons[0]` في الاختبارات اللي محتاجة
    سطر بخيار فعلي — إعادة الطلب بيرجّع الـids دي زي ما هي.
    """
    from app.models import Addon, Category, Product, ProductOption

    category = Category(
        name="مشروبات", slug="p2-category", sort_order=0, is_active=True
    )
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name="منتج تجريبي",
        slug="p2-product",
        base_price=Decimal("50.00"),
        unit="piece",
        is_available=True,
        is_active=True,
        sort_order=0,
    )
    db.add(product)
    db.flush()

    option = ProductOption(
        product_id=product.id,
        name="سكر أقل",
        price_adjustment=Decimal("5.00"),
        is_available=True,
    )
    addon = Addon(
        product_id=product.id, name="ليمون", price=Decimal("15.00"), is_active=True
    )
    db.add_all([option, addon])
    db.commit()
    return product


# ---------------------------------------------------------------------------
# 1) طرق الدفع: العميل بيشوف المفعّلة بس
# ---------------------------------------------------------------------------


class TestPaymentMethodListing:
    def test_only_active_methods_are_visible(self, client: TestClient, db) -> None:
        from app.models import PaymentMethod

        method = db.query(PaymentMethod).filter_by(code="ewallet").one()
        method.is_active = False
        db.commit()

        codes = [m["code"] for m in client.get("/api/payment-methods").json()]
        assert "cash" in codes
        assert "ewallet" not in codes

    def test_order_with_a_disabled_method_is_rejected(
        self, client: TestClient, menu, db
    ) -> None:
        """الـUI بيخفي الطريقة، بس حد ممكن يبعت الـid بإيده.

        التحقق في السيرفر هو اللي بيحمي: `resolve_payment_method` بيرفض
        الـmethod المعطّلة بـ400.
        """
        from app.models import PaymentMethod

        disabled = db.query(PaymentMethod).filter_by(code="ewallet").one()
        disabled.is_active = False
        db.commit()

        response = client.post(
            "/orders",
            json=_order_payload(menu.id, payment_method_id=disabled.id),
        )
        assert response.status_code == 400

    def test_order_records_the_payment_snapshot(
        self, client: TestClient, menu, db
    ) -> None:
        method_id = _cash_method_id(db)
        response = client.post(
            "/orders", json=_order_payload(menu.id, payment_method_id=method_id)
        )
        assert response.status_code == 200, response.text

        from app.models import Order

        order = db.query(Order).order_by(Order.id.desc()).first()
        assert order.payment_method_id == method_id
        assert order.payment_method_code == "cash"
        assert order.payment_method_label  # لقطة الاسم اتسجلت
        assert order.payment_status == "pending"


# ---------------------------------------------------------------------------
# 2) التتبّع: التوكن هو الهوية، مش رقم الطلب
# ---------------------------------------------------------------------------


class TestTrackingFlow:
    def test_the_token_is_returned_once_and_works(
        self, client: TestClient, menu
    ) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()

        token = created["tracking_token"]
        assert token and re.fullmatch(r"[0-9a-f]{64}", token)

        tracked = client.get(f"/api/orders/track?token={token}")
        assert tracked.status_code == 200
        assert tracked.json()["order_id"] == created["order_id"]

    def test_the_token_is_not_stored_in_the_clear(
        self, client: TestClient, menu, db
    ) -> None:
        """الداتابيز شايفة الهاش بس.

        معنى الكلام ده: لو الـDB اتسرّب، مفيش روابط تتبّع شغّالة من
        الـrows المسكوبة. الـtoken نفسه ما يتكتبش في أي عمود.
        """
        import hashlib

        from app.models import Order

        created = client.post("/orders", json=_order_payload(menu.id)).json()
        token = created["tracking_token"]

        order = db.query(Order).filter_by(id=created["order_id"]).one()
        assert order.order_tracking_hash == hashlib.sha256(token.encode()).hexdigest()
        assert order.order_tracking_hash != token

    def test_the_tracked_response_carries_no_pii(
        self, client: TestClient, menu
    ) -> None:
        """الصفحة public وبلا تسجيل دخول. الاسم/الهاتف/العنوان ما
        يظهروش في رد التتبّع."""
        created = client.post("/orders", json=_order_payload(menu.id)).json()

        body = client.get(
            f"/api/orders/track?token={created['tracking_token']}"
        ).text
        assert "01012345678" not in body
        assert "عميل الاختبار" not in body

    def test_an_older_order_cannot_be_opened_by_guessing(
        self, client: TestClient, menu
    ) -> None:
        client.post("/orders", json=_order_payload(menu.id))

        # رقم الطلب موجود ومتسرب (بيظهر في رسالة واتساب)، بس مش بيفتح.
        assert client.get("/api/orders/track?token=1").status_code == 404
        assert client.get(f"/api/orders/track?token={'0' * 64}").status_code == 404

    def test_the_tracking_page_renders(self, client: TestClient, menu) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()

        page = client.get(f"/track?token={created['tracking_token']}")
        assert page.status_code == 200
        assert "تم استلام الطلب" in page.text  # تسمية الحالة بالعربي


# ---------------------------------------------------------------------------
# 3) إعادة الطلب: السيرفر بيبني السطور من الطلب المرخّص
# ---------------------------------------------------------------------------


class TestReorderFlow:
    def test_reorder_returns_the_original_lines(
        self, client: TestClient, menu, db
    ) -> None:
        from app.models import Product

        product = db.get(Product, menu.id)
        option_id = product.options[0].id
        addon_id = product.addons[0].id

        created = client.post(
            "/orders",
            json=_order_payload(
                menu.id,
                option_ids=[option_id],
                addon_ids=[addon_id],
                payment_method_id=_cash_method_id(db),
            ),
        ).json()

        response = client.post(
            "/api/orders/reorder", json={"token": created["tracking_token"]}
        )
        assert response.status_code == 200, response.text

        data = response.json()
        assert len(data["lines"]) == 1
        line = data["lines"][0]
        assert line["product_id"] == menu.id
        assert line["quantity"] == 2
        # الخيارات والإضافات رجعت بالـid بتاعها من الطلب، مش من العميل.
        assert line["option_ids"] == [option_id]
        assert line["addon_ids"] == [addon_id]
        assert data["skipped"] == []

    def test_reorder_ignores_client_supplied_items(
        self, client: TestClient, menu
    ) -> None:
        """الـendpoint مابقاش بياخد سطور من العميل.

        لو كان لسه بياخد `items`، الـtoken كان تصريح بقراءة أي طلب.
        عميل يبعت سطر من منتج تاني بيبقى طلب لازم يتجاهل.
        """
        created = client.post("/orders", json=_order_payload(menu.id)).json()

        response = client.post(
            "/api/orders/reorder",
            json={
                "token": created["tracking_token"],
                "items": [{"product_id": 999999, "quantity": 1}],
            },
        )
        # `items` بقى مش معروف في الـschema — FastAPI بيطنّيه (extra
        # ignored) وبيبني السطر من الطلب نفسه.
        assert response.status_code == 200
        data = response.json()
        assert [line["product_id"] for line in data["lines"]] == [menu.id]

    def test_reorder_needs_the_token(self, client: TestClient, menu) -> None:
        client.post("/orders", json=_order_payload(menu.id))

        assert (
            client.post("/api/orders/reorder", json={"token": "wrong"}).status_code
            == 404
        )
        assert (
            client.post("/api/orders/reorder", json={"token": ""}).status_code == 422
        )


# ---------------------------------------------------------------------------
# 8) الـmanifest بيقرا حقول PWA
# ---------------------------------------------------------------------------


class TestHistoryPageScripts:
    """ترتيب الـscripts في صفحة السجل.

    زر "اطلب تاني" بيستدعي `window.restaurantCart.mergeFromReorder`،
    والـnamespace ده بيتصدّر من `cart.js` بس. لو الصفحة ما حمّلتهاش،
    الزر ميعملش حاجة والعميل يروح `/` وسلة فاضية. `defer` بينفّذ
    بالترتيب الظاهر، فترتيب الـscript tags جزء من العقد مش تفصيل.
    """

    def test_cart_js_is_loaded_before_the_history_script(
        self, client: TestClient
    ) -> None:
        html = client.get("/orders/history").text
        cart_at = html.index("/static/js/cart.js")
        history_at = html.index("/static/js/order_history.js")
        assert cart_at < history_at

    def test_both_scripts_are_deferred(self, client: TestClient) -> None:
        html = client.get("/orders/history").text
        for src in ("/static/js/cart.js", "/static/js/order_history.js"):
            tag = re.search(rf'<script[^>]*src="{re.escape(src)}"[^>]*>', html)
            assert tag, f"{src} مش محمّلة"
            assert "defer" in tag.group(0), f"{src} من غير defer"


class TestPwaManifest:

    @pytest.fixture()
    def restaurant_settings(self, db):
        """صف الإعدادات. `get_settings` بينشئه عند أول طلب، فبنعمله
        صريح عشان نعدّله."""
        from app.routers.public import get_settings

        settings = get_settings(db)
        db.commit()
        return settings

    def test_it_falls_back_to_the_restaurant_name(
        self, client: TestClient, db, restaurant_settings
    ) -> None:
        """الحقول اختيارية. لو فاضية، الـmanifest زي V1 بالظبط."""
        restaurant_settings.pwa_name = None
        restaurant_settings.pwa_short_name = None
        restaurant_settings.pwa_icon_url = None
        restaurant_settings.restaurant_name = "مطعم السعادة"
        restaurant_settings.app_icon_url = None
        db.commit()

        body = client.get("/manifest.json").json()
        assert body["name"] == "مطعم السعادة"
        assert body["short_name"] == "مطعم السعادة"

    def test_the_pwa_fields_win_when_set(
        self, client: TestClient, db, restaurant_settings
    ) -> None:
        restaurant_settings.restaurant_name = "مطعم السعادة"
        restaurant_settings.pwa_name = "طلب من مطعم السعادة"
        restaurant_settings.pwa_short_name = "مطعم السعادة"
        restaurant_settings.pwa_icon_url = "https://example.com/icon.png"
        db.commit()

        body = client.get("/manifest.json").json()
        assert body["name"] == "طلب من مطعم السعادة"
        assert body["icons"][0]["src"] == "https://example.com/icon.png"

    def test_the_short_name_is_capped_at_twelve_characters(
        self, client: TestClient, db, restaurant_settings
    ) -> None:
        """شرط قابلية التثبيت في المتصفح. الاسم الطويل على الشاشة
        الرئيسية بيتم قطعه من المتصفح، فنقصّه إحساسًا متسق."""
        restaurant_settings.pwa_short_name = "اسم طويل جدا للتطبيق"
        db.commit()

        assert len(client.get("/manifest.json").json()["short_name"]) == 12


# ---------------------------------------------------------------------------
# 4) call_center: رسوم معلّقة بتتحدد بعد التأكيد
# ---------------------------------------------------------------------------


@pytest.fixture()
def call_center_option(db):
    from app.models import DeliveryOption

    option = DeliveryOption(
        name="اتصال بالمطعم",
        delivery_type="call_center",
        fee=Decimal("0.00"),
        is_active=True,
    )
    db.add(option)
    db.commit()
    return option


class TestCallCenterFlow:
    def test_the_fee_starts_pending_at_zero(
        self, client: TestClient, menu, call_center_option, db
    ) -> None:
        response = client.post(
            "/orders",
            json=_order_payload(
                menu.id, delivery_option_id=call_center_option.id
            ),
        )
        assert response.status_code == 200, response.text

        from app.models import Order

        order = db.query(Order).order_by(Order.id.desc()).first()
        assert order.delivery_fee_pending is True
        assert order.delivery_fee == Decimal("0.00")
        # الإجمالي المبدئي = subtotal من غير رسوم.
        assert order.total == order.subtotal

    def test_the_preview_agrees(self, client: TestClient, menu, call_center_option) -> None:
        preview = client.post(
            "/api/orders/preview",
            json={
                "items": [{"product_id": menu.id, "quantity": 2}],
                "delivery_option_id": call_center_option.id,
            },
        )
        assert preview.status_code == 200
        body = preview.json()
        assert body["delivery_fee_pending"] is True
        assert Decimal(body["delivery_fee"]) == Decimal("0.00")
        assert Decimal(body["total"]) == Decimal(body["subtotal"])


# ---------------------------------------------------------------------------
# 5) انتقالات الحالة
# ---------------------------------------------------------------------------

HAPPY_PATH = ("confirmed", "preparing", "ready", "out_for_delivery", "completed")


class TestStatusTransitions:
    def test_a_normal_order_walks_the_happy_path(
        self, client: TestClient, admin_client, menu
    ) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        for status in HAPPY_PATH:
            response = admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": status})
            assert response.status_code == 200, f"{status}: {response.text}"

        tracked = client.get(
            f"/api/orders/track?token={created['tracking_token']}"
        ).json()
        assert tracked["status"] == "completed"

    def test_a_completed_order_cannot_go_back_to_preparing(
        self, client: TestClient, admin_client, menu
    ) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        for status in HAPPY_PATH:
            admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": status})

        response = admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": "preparing"})
        assert response.status_code == 400

    def test_cancellation_is_allowed_from_the_early_states(
        self, client: TestClient, admin_client, menu
    ) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": "preparing"})
        response = admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": "cancelled"})
        assert response.status_code == 200

    def test_a_terminal_order_offers_no_next_step(
        self, client: TestClient, admin_client, menu
    ) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": "cancelled"})
        page = admin_client.get(f"/admin/orders/{order_id}")
        assert page.status_code == 200
        assert "مفيش خطوات تالية" in page.text

    def test_the_order_detail_page_renders(self, client: TestClient, admin_client, menu) -> None:
        created = client.post("/orders", json=_order_payload(menu.id)).json()
        page = admin_client.get(f"/admin/orders/{created['order_id']}")
        assert page.status_code == 200
        assert "عميل الاختبار" in page.text
        assert "01012345678" in page.text


# ---------------------------------------------------------------------------
# 6) إدارة حالة الدفع من الأدمن
# ---------------------------------------------------------------------------


class TestAdminPaymentUpdates:
    def test_marking_paid_stamps_the_confirmation_time(
        self, client: TestClient, admin_client, menu, db
    ) -> None:
        from app.models import Order

        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        response = admin_post(admin_client, f"/admin/orders/{order_id}/payment", {"payment_status": "paid", "payment_reference": "TX-99"})
        assert response.status_code == 200, response.text

        order = db.query(Order).filter_by(id=order_id).one()
        assert order.payment_status == "paid"
        assert order.payment_reference == "TX-99"
        assert order.payment_confirmed_at is not None

    def test_the_confirmation_time_is_not_overwritten(
        self, client: TestClient, admin_client, menu, db
    ) -> None:
        """السؤال "امتى اتسجل الدفع؟" جواب واحد. لو الأدمن عدّل بعد كده،
        الوقت الأصلي هو المهم."""
        from app.models import Order

        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]

        admin_post(admin_client, f"/admin/orders/{order_id}/payment", {"payment_status": "paid"})
        first = db.query(Order).filter_by(id=order_id).one().payment_confirmed_at

        admin_post(admin_client, f"/admin/orders/{order_id}/payment", {"payment_status": "failed"})
        second = db.query(Order).filter_by(id=order_id).one().payment_confirmed_at
        assert first == second

    def test_payment_state_does_not_move_the_order_state(
        self, client: TestClient, admin_client, menu, db
    ) -> None:
        """حالة الدفع منفصلة عن حالة الطلب: طلب كاش لسه ما اتدفعش =
        `preparing` + `pending`. كل واحدة ليها endpoint ليها."""
        from app.models import Order

        created = client.post("/orders", json=_order_payload(menu.id)).json()
        order_id = created["order_id"]
        admin_post(admin_client, f"/admin/orders/{order_id}/status", {"status_value": "preparing"})

        admin_post(admin_client, f"/admin/orders/{order_id}/payment", {"payment_status": "pending"})
        order = db.query(Order).filter_by(id=order_id).one()
        assert order.status == "preparing"
        assert order.payment_status == "pending"


# ---------------------------------------------------------------------------
# 7) تحديد رسوم call_center من الأدمن
# ---------------------------------------------------------------------------


class TestAdminDeliveryFee:
    @staticmethod
    def _create(client, menu, call_center_option):
        response = client.post(
            "/orders",
            json=_order_payload(
                menu.id, delivery_option_id=call_center_option.id
            ),
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_settling_the_fee_recomputes_the_total(
        self, client: TestClient, admin_client, menu, call_center_option, db
    ) -> None:
        from app.models import Order

        created = self._create(client, menu, call_center_option)
        order_id = created["order_id"]

        response = admin_post(admin_client, f"/admin/orders/{order_id}/delivery-fee", {"delivery_fee": "40"})
        assert response.status_code == 200, response.text

        order = db.query(Order).filter_by(id=order_id).one()
        assert order.delivery_fee == Decimal("40.00")
        assert order.delivery_fee_pending is False
        # الإجمالي بيتحسب تاني: subtotal - discount + fee
        assert order.total == order.subtotal - order.discount_amount + Decimal("40.00")

    def test_the_customer_sees_the_settled_fee(
        self, client: TestClient, admin_client, menu, call_center_option
    ) -> None:
        created = self._create(client, menu, call_center_option)

        admin_post(
            admin_client,
            f"/admin/orders/{created['order_id']}/delivery-fee",
            {"delivery_fee": "40"},
        )

        tracked = client.get(
            f"/api/orders/track?token={created['tracking_token']}"
        ).json()
        assert tracked["delivery_fee_pending"] is False
        assert Decimal(tracked["delivery_fee"]) == Decimal("40.00")
        assert Decimal(tracked["total"]) == Decimal(tracked["subtotal"]) + Decimal("40.00")


# ---------------------------------------------------------------------------
# 9) الأدمن يقدر يضبط أسماء التطبيق من صفحة الإعدادات
# ---------------------------------------------------------------------------


class TestPwaSettingsForm:
    def _post_settings(self, admin_client, **overrides):
        data = {
            "restaurant_name": "مطعم تجريبي",
            "whatsapp_number": "01012345678",
            "ordering_open_time": "",
            "ordering_close_time": "",
        }
        data.update(overrides)
        return admin_post(
            admin_client, "/admin/settings", data, follow_redirects=False
        )

    def test_the_admin_can_set_the_pwa_names(
        self, admin_client, db
    ) -> None:
        from app.models import RestaurantSettings

        response = self._post_settings(
            admin_client,
            pwa_name="طلب من مطعم تجريبي",
            pwa_short_name="مطعم تجريبي",
        )
        assert response.status_code in (302, 303), response.text

        settings = db.query(RestaurantSettings).one()
        assert settings.pwa_name == "طلب من مطعم تجريبي"
        assert settings.pwa_short_name == "مطعم تجريبي"
        assert admin_client.get("/manifest.json").json()["name"] == "طلب من مطعم تجريبي"

    def test_blank_names_fall_back_to_the_restaurant_name(
        self, admin_client, db
    ) -> None:
        from app.models import RestaurantSettings

        self._post_settings(
            admin_client, pwa_name="  ", pwa_short_name=""
        )
        settings = db.query(RestaurantSettings).one()
        assert settings.pwa_name is None
        assert settings.pwa_short_name is None

    def test_a_long_short_name_is_rejected(self, admin_client) -> None:
        """المتصفح بيقصّ الاسم الطويل على الشاشة الرئيسية. نرفض
        بدل ما نخزّن اسم بيتقصّ ويبقى شكله غلط."""
        response = self._post_settings(admin_client, pwa_short_name="اسم طويل جدا للتطبيق")
        assert response.status_code == 400, response.text
        assert "12" in response.json()["detail"]
