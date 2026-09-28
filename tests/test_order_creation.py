"""اختبارات مسار إنشاء الطلب وأرقام التليفون على مستوى الـHTTP.

السبب: `customer_phone` كان `str` عادي في الـschema، فـ:
- نص فاضي أو مسافات كان يعدّي الـvalidation ويفصل في منطق الأعمال بـ500.
- الأرقام بأشكال مختلفة كانت بتخزن زي ما هي، فالعميل نفسه بيتحسب مرتين.

القرار: `CustomerPhone` على مستوى الـschema، فالمشكلة بتتحول لـ422 برسالة
عربية بدل 500. المنطق في `app/routers/public.py` بقي دفاعي كمان: لو
`customer is None` بيرجع 400 بدل AttributeError.

المرجع: `app/schemas.py` و`app/routers/public.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.utils import PHONE_INVALID_MESSAGE

SAMPLE = "01012345678"
DISPLAY = SAMPLE


@pytest.fixture()
def menu(db):
    """منتج واحد متاح، عشان نقدر نبني طلب فعلي."""
    from app.models import Category, Product

    category = Category(
        name="أصناف",
        slug="test-category",
        sort_order=0,
        is_active=True,
    )
    db.add(category)
    db.flush()

    product = Product(
        category_id=category.id,
        name="منتج تجريبي",
        slug="test-product",
        base_price=Decimal("50.00"),
        unit="piece",
        is_available=True,
        is_active=True,
        sort_order=0,
    )
    db.add(product)
    db.commit()
    return product


def _order_payload(customer_phone: str, product_id: int) -> dict:
    return {
        "customer_name": "عميل تجريبي",
        "customer_phone": customer_phone,
        "customer_address": "عنوان تجريبي",
        "items": [{"product_id": product_id, "quantity": 1}],
    }


def _create_order(client: TestClient, payload: dict):
    return client.post("/orders", json=payload)


class TestOrderCreatePhoneValidation:
    @pytest.mark.parametrize("phone", [SAMPLE, "+201012345678", "00201012345678"])
    def test_valid_phones_are_accepted(
        self, client: TestClient, menu, phone: str
    ) -> None:
        response = _create_order(client, _order_payload(phone, menu.id))
        # 422 معناها الـschema رفضت الرقم، وده اللي بنختبره
        assert response.status_code != 422, response.text
        # إما 200 ولا 201 — المهم إنه مش 500
        assert response.status_code in (200, 201), response.text

    @pytest.mark.parametrize("phone", ["", "   ", "\t\n"])
    def test_blank_phone_is_rejected_not_a_500(
        self, client: TestClient, menu, phone: str
    ) -> None:
        """ده كان الـregression الأساسي: مسافات كانت بتطلع 500."""
        response = _create_order(client, _order_payload(phone, menu.id))
        assert response.status_code == 422
        assert "detail" in response.json()

    @pytest.mark.parametrize("phone", ["12345", "abcdefghijk", "02012345678"])
    def test_invalid_phone_is_rejected(
        self, client: TestClient, menu, phone: str
    ) -> None:
        response = _create_order(client, _order_payload(phone, menu.id))
        assert response.status_code == 422

    def test_oversized_phone_is_rejected(
        self, client: TestClient, menu
    ) -> None:
        response = _create_order(client, _order_payload("0" * 200, menu.id))
        assert response.status_code == 422

    def test_validation_message_is_arabic(
        self, client: TestClient, menu
    ) -> None:
        response = _create_order(client, _order_payload("12345", menu.id))
        assert PHONE_INVALID_MESSAGE in response.text

    def test_missing_phone_field_is_rejected(
        self, client: TestClient, menu
    ) -> None:
        payload = _order_payload(SAMPLE, menu.id)
        payload.pop("customer_phone")
        assert _create_order(client, payload).status_code == 422

    def test_no_order_is_created_for_an_invalid_phone(
        self, client: TestClient, db, menu
    ) -> None:
        from sqlalchemy import func, select

        from app.models import Order

        _create_order(client, _order_payload("   ", menu.id))
        assert db.scalar(select(func.count()).select_from(Order)) == 0


class TestOrderPreviewPhoneValidation:
    def test_preview_rejects_a_bad_phone(self, client: TestClient, menu) -> None:
        response = client.post(
            "/api/orders/preview",
            json={
                "customer_name": "عميل تجريبي",
                "customer_phone": "not-a-phone",
                "customer_address": "عنوان",
                "items": [{"product_id": menu.id, "quantity": 1}],
            },
        )
        assert response.status_code == 422

    def test_preview_accepts_a_good_phone(self, client: TestClient, menu) -> None:
        response = client.post(
            "/api/orders/preview",
            json={
                "customer_name": "عميل تجريبي",
                "customer_phone": SAMPLE,
                "customer_address": "عنوان",
                "items": [{"product_id": menu.id, "quantity": 1}],
            },
        )
        assert response.status_code != 422
        assert response.status_code != 500


class TestCouponValidatePhoneIsOptional:
    def test_missing_phone_is_fine(self, client: TestClient) -> None:
        """المعاينة بتحتاج الهاتف اختياري — الفحص لازم يسمح بـNone."""
        response = client.post("/api/coupons/validate", json={"code": "NOPE"})
        assert response.status_code != 500

    def test_unknown_coupon_does_not_500(self, client: TestClient) -> None:
        """الكوبون المجهول كان path ممكن يرمي exception."""
        response = client.post("/api/coupons/validate", json={"code": "NO-SUCH-CODE"})
        assert response.status_code in (400, 404, 422)


class TestNormalisedPhoneIsPersisted:
    def test_different_input_shapes_store_the_same_value(
        self, client: TestClient, db, menu
    ) -> None:
        from sqlalchemy import select

        from app.models import Order

        for variant in [SAMPLE, "+201012345678", "00201012345678", "  01012345678  "]:
            _create_order(client, _order_payload(variant, menu.id))

        stored = db.scalars(select(Order.customer_phone)).all()
        assert len(stored) == 4
        # كلهم اتخزنوا بنفس الشكل
        assert set(stored) == {DISPLAY}
