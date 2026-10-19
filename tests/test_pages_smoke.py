"""اختبارات دخان للصفحات.

السبب: Phase 0 غيّر middleware في `app/main.py` (request logging + security
headers + `/sw.js`) وقوالب الـbase (Alpine محلي + تسجيل `/sw.js`). أي
كسر في الـmiddleware بيخلي كل الصفحات 500، والـtemplate بيخلي الصفحة
ترجع 200 وهي مكسورة جواها.

المرجع: `app/main.py` و`app/templates/`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PUBLIC_PAGES = [
    "/",
    "/search",
]

ADMIN_PAGES = [
    "/admin/login",
]


@pytest.fixture()
def restaurant(db):
    """إعدادات المطعم موجودة، عشان صفحات الـpublic متوقعش بيانات ناقصة."""
    from app.models import RestaurantSettings

    settings = RestaurantSettings(
        restaurant_name="مطعم الاختبار",
        whatsapp_number="201000000000",
        phone_number="01000000000",
        address="عنوان الاختبار",
        currency="EGP",
    )
    db.add(settings)
    db.commit()
    return settings


class TestPublicPagesRender:
    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_page_returns_200(self, client: TestClient, restaurant, path: str) -> None:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, f"{path} -> {response.status_code}"

    @pytest.mark.parametrize("path", PUBLIC_PAGES)
    def test_page_is_html_not_a_stack_trace(
        self, client: TestClient, restaurant, path: str
    ) -> None:
        response = client.get(path, follow_redirects=True)
        assert "text/html" in response.headers["content-type"]
        assert "Traceback" not in response.text

    def test_home_page_loads_local_alpine(self, client: TestClient, restaurant) -> None:
        body = client.get("/", follow_redirects=True).text
        assert "/static/js/alpine.min.js" in body

    def test_home_page_registers_the_root_service_worker(
        self, client: TestClient, restaurant
    ) -> None:
        body = client.get("/", follow_redirects=True).text
        assert "register('/sw.js')" in body


class TestProductPage:
    def test_missing_product_is_404_html(
        self, client: TestClient, restaurant
    ) -> None:
        response = client.get("/products/مش-موجود", follow_redirects=True)
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]

    def test_404_page_links_home(self, client: TestClient, restaurant) -> None:
        body = client.get("/products/مش-موجود").text
        assert 'href="/"' in body
        assert "404" in body


class TestNotFoundHandlerKeepsNonHtmlPathsJson:
    """صفحة 404HTML للعميل بس — مينفعش تتحوّل لـAPI أو ملفات ثابتة."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/nope",
            "/admin/nope",
            "/api",
            "/static/js/does-not-exist.js",
            "/uploads/nope.png",
        ],
    )
    def test_non_html_paths_stay_json(
        self, client: TestClient, restaurant, path: str
    ) -> None:
        response = client.get(path)
        assert response.status_code == 404
        assert "text/html" not in response.headers["content-type"]

    @pytest.mark.parametrize(
        "path", ["/openapi.json", "/manifest.json", "/health", "/ready", "/sw.js"]
    )
    def test_real_endpoints_are_not_turned_into_404_pages(
        self, client: TestClient, restaurant, path: str
    ) -> None:
        """الـhandler لازم ما يمسّش المسارات اللي موجودة فعلاً."""
        assert client.get(path).status_code == 200


class TestAdminPagesRender:
    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_login_page_renders(self, client: TestClient, path: str) -> None:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200

    def test_login_page_has_a_csrf_field(self, client: TestClient) -> None:
        body = client.get("/admin/login").text
        assert "csrf_token" in body

    def test_admin_redirects_to_login_when_signed_out(self, client: TestClient) -> None:
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code in (302, 303, 307)
        assert "/admin/login" in response.headers.get("location", "")

    def test_admin_page_has_the_csrf_cookie(self, client: TestClient) -> None:
        client.get("/admin/login")
        assert client.cookies.get("admin_csrf")


class TestStaticAssets:
    @pytest.mark.parametrize(
        "path",
        [
            "/static/css/app.css",
            "/static/js/cart.js",
            "/static/js/alpine.min.js",
        ],
    )
    def test_asset_is_served(self, client: TestClient, path: str) -> None:
        response = client.get(path)
        assert response.status_code == 200

    def test_alpine_asset_is_not_empty(self, client: TestClient) -> None:
        assert len(client.get("/static/js/alpine.min.js").content) > 10_000

    def test_push_js_asset_is_served(self, client: TestClient) -> None:
        response = client.get("/static/js/push.js")
        assert response.status_code == 200
        assert "enablePush" in response.text


class TestPushCustomerIntegration:
    """مسار الاشتراك التسويقي للعميل: من الواجهة لحد الزرار.

    الفجوة اللي كانت موجودة: `push.js` مكتوب في رأسه إنه «بيتحمّل في
    صفحات العميل وصفحة الأدمن»، بس **مكانه كان فاضي** — مفيش أي template
    بيحمله، يعني `enablePush()` ماكانتش موجودة أصلاً → العميل مايقدرش
    يشترك → حملات الـpush مالهاش جمهور. دي اختبارات بتطخّط الإصلاح
    عشان الفجوة ما ترجعش من غير ما الاختبارات تفشل.
    """

    @staticmethod
    def _track_token(client: TestClient, menu, phone: str) -> str:
        """طلب حقيقي → توكن تتبّع. كل اختبار برقم **مختلف** (هاتف العميل
        unique، والداتابيز مش بتتصفّر بين الاختبارات)."""
        from tests.test_order_creation import _order_payload

        created = client.post("/orders", json=_order_payload(phone, menu.id))
        assert created.status_code in (200, 201), created.text
        return created.json()["tracking_token"]

    def test_customer_base_loads_push_js(self, client: TestClient, restaurant) -> None:
        body = client.get("/", follow_redirects=True).text
        assert "/static/js/push.js" in body

    def test_track_page_renders_the_marketing_optin(
        self, client: TestClient, restaurant, menu
    ) -> None:
        token = self._track_token(client, menu, "01055500001")
        body = client.get("/track", params={"token": token}).text
        # الـbase (اللي بيحمل push.js) وكتلة الإشعارات نفسها موجودين.
        assert "/static/js/push.js" in body
        assert 'id="push-optin"' in body
        assert 'id="push-optin-btn"' in body

    def test_optin_card_is_hidden_until_javascript_approves(
        self, client: TestClient, restaurant, menu
    ) -> None:
        """الكارت مخفي افتراضياً — بيظهره الجافاسكريبت بس لما يتأكد إن
        المتصفح يدعم Push وإن الـVAPID عندنا مظبوط. غير كده الزرار يفضل
        متغطّى ومحدش يدوّس ويلاقي غاز."""
        import re

        token = self._track_token(client, menu, "01055500002")
        body = client.get("/track", params={"token": token}).text
        assert re.search(r'id="push-optin"[^>]*\shidden', body), (
            "كارت الإشعارات لازم يكون `hidden` افتراضي"
        )

    def test_permission_is_requested_only_from_a_click(
        self, client: TestClient, restaurant, menu
    ) -> None:
        """الصفحة نفسها ما تطلّبش `Notification.requestPermission`.

        المتصفح بيرجع `denied` **بشكل نهائي** لو الإذن اتسأل من غير
        ضغطة (بلا سبب معتبر)، وده بيقتل مسار الاشتراك كله. فالقاعدة هنا:
        أي نداء `requestPermission` لازم يكون جوه `enablePush()`،
        واللي بيتسمّى من زرار بس — مش من تحميل الصفحة.
        """
        token = self._track_token(client, menu, "01055500003")
        body = client.get("/track", params={"token": token}).text
        # الصفحة (HTML) ما بتطلّبش الإذن لوحدها.
        assert "Notification.requestPermission" not in body
        # بس اللي بينادي `enablePush(...)` هو مستمع ضغطة الزرار.
        assert "enablePush(" in body
        assert "addEventListener('click'" in body

    def test_optin_passes_the_tracking_token_so_the_subscription_is_not_an_orphan(
        self, client: TestClient, restaurant, menu
    ) -> None:
        """الاشتراك لازم يتربط بالعميل وقت الاشتراك نفسه.

        لو سجّلنا من غير `token`، السيرفر بيخزّن `customer_id = NULL`،
        و`marketing._recipient_rows_for` بتجيب اشتراكات العملاء **بس** —
        يعني العميل يفعّل الإشعارات ويفضل مستحيل يوصله أي عرض لحد ما
        يطلب تاني (ويا بخته لو عملها). فالصفحة لازم تمرّر توكن التتبّع
        اللي أصلًا موجود عندها في `#live-status[data-token]`.

        السيرفر نفسه مغطّى (`test_subscribe_with_token_binds_customer`)؛
        ده الاختبار اللي بيمنع سقوط الـwire بتاع العميل.
        """
        token = self._track_token(client, menu, "01055500004")
        body = client.get("/track", params={"token": token}).text

        # التوكن موجود في الصفحة فعلًا.
        assert f'data-token="{token}"' in body
        # الاتصالين بيستعملوه: قراءة الحالة (فنشوف subscribed صح)
        # والتسجيل نفسه (فترقعل `customer_id`).
        assert "getPushState(statusQuery)" in body
        assert "enablePush(tokenOptions)" in body
        # إلغاء الاشتراك كمان لازم يبعت نفس الـtoken — وإلا السيرفر
        # بيرفضه (الاشتراك مربوط بالعميل ده أصلًا).
        assert "disablePush(tokenOptions)" in body
        assert 'id="push-optin-off"' in body
