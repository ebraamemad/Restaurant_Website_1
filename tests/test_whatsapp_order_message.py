"""رسالة واتساب بتاعة الطلب + رابط click-to-chat: الصيغة ورقم الوجهة.

## المكان

المسار كله محلّي وبيتمم من غير أي إرسال خارجي:

- `POST /orders` → `build_whatsapp_message` (نص بيتحفظ في
  `orders.whatsapp_message` وبيتسطّر لموظف المطعم في الرسالة)، و
- `build_whatsapp_url` (الرابط اللي بيحطّ العميل في محادثة المطعم).

## ليه الاختبارات دي أصلاً

قبلها، رسالة الطلب **ما كانتش عليها اختبار وحدة واحد** — بس فعلاً
`test_branches` بتتأكد إنها «مش فاضية»، وده كل حاجة. ورابط `wa.me`
كان ليه حالة خطأ صريحة: الرقم المحلي (`01012345678`) بيتعلّم من
نموذج الإعدادات عادي (و`validate_customer_phone` بيقبله)، بس كان
بيوصل لـ`wa.me/01012345678` — وده **مش رقم دولي**، فاللينك بيفتح
شات غير صالح والعميل مايقدرش يكمل طلبه.

## مبادئ

- مفيش شبكة خالص: دوال نقية بس، وطلب حقيقي واحد بس عبر `TestClient`.
- أرقام هواتف في الاختبارات دي **مختلفة عن باقي الملفات** —
  `customers.phone` unique والداتابيز مش بتتصفّر بين الاختبارات.
- مفيش أي سرّ أو رقم عميل حقيقي هنا.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

# أرقام مخصّصة للاختبارات دي بس — مش مكرّرة مع أي ملف تاني.
OWN_PHONES = ["01077700001", "01077700002", "01077700003", "01077700004"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def wa_settings():
    """صف إعدادات مطعم **من غير قاعدة بيانات**.

    `build_whatsapp_message` بيقرا بس `restaurant_name` و`currency` —
    نمرّرهم صراحةً عشان `default=` بيتطبّق وقت الـflush مش وقت
    البناء، وإحنا عايزين اختبار وحدة نقي من غير أي كتابة في الداتابيز.
    """
    from app.models import RestaurantSettings

    return RestaurantSettings(restaurant_name="مطعم الاختبار", currency="EGP")


def _item(**overrides) -> dict:
    item: dict = {
        "name": "برجر كلاسيك",
        "quantity": 2,
        "line_total": Decimal("150.00"),
        "options": [("شيبس محمّر", Decimal("10.00"))],
        "addons": [("جبنة شيدر", Decimal("15.00"))],
        "note": "بدون بصل",
    }
    item.update(overrides)
    return item


def _message(settings, **overrides) -> str:
    from app.utils import build_whatsapp_message

    kwargs: dict = dict(
        settings=settings,
        order_id=1042,
        customer_name="منى أحمد",
        customer_phone=OWN_PHONES[0],
        customer_address="شارع النيل، المعادي",
        items=[_item()],
        subtotal=Decimal("150.00"),
        discount_amount=Decimal("0.00"),
        delivery_fee=Decimal("0.00"),
        delivery_name=None,
        delivery_type="pickup",
        total=Decimal("150.00"),
        notes=None,
    )
    kwargs.update(overrides)
    return build_whatsapp_message(**kwargs)


# ---------------------------------------------------------------------------
# 1) صيغة رسالة الطلب
# ---------------------------------------------------------------------------


class TestBuildWhatsAppMessage:
    def test_header_carries_the_restaurant_and_the_order_number(self, wa_settings) -> None:
        message = _message(wa_settings)
        assert message.startswith("طلب جديد من مطعم الاختبار")
        assert "رقم الطلب: #1042" in message

    def test_order_number_line_disappears_when_there_is_no_order(self, wa_settings) -> None:
        message = _message(wa_settings, order_id=None)
        assert "رقم الطلب:" not in message

    def test_customer_block_lists_name_phone_and_address(self, wa_settings) -> None:
        message = _message(wa_settings)
        assert f"الاسم: منى أحمد" in message
        assert f"الهاتف: {OWN_PHONES[0]}" in message
        assert "العنوان: شارع النيل، المعادي" in message

    def test_address_line_is_skipped_when_the_customer_leaves_it_blank(
        self, wa_settings
    ) -> None:
        message = _message(wa_settings, customer_address=None)
        assert "العنوان:" not in message

    def test_items_carry_quantity_line_total_options_addons_and_note(
        self, wa_settings
    ) -> None:
        message = _message(wa_settings)
        assert "- برجر كلاسيك × 2 = 150.00 EGP" in message
        assert "الخيارات: شيبس محمّر (+10.00)" in message
        assert "الإضافات: جبنة شيدر (+15.00)" in message
        assert "ملاحظة: بدون بصل" in message

    def test_several_options_and_addons_are_joined_with_arabic_comma(
        self, wa_settings
    ) -> None:
        message = _message(
            wa_settings,
            items=[
                _item(
                    options=[("شيبس محمّر", Decimal("10.00")), ("بصل مكرمل", Decimal("5.00"))],
                    addons=[("جبنة شيدر", Decimal("15.00")), ("بيكون", Decimal("20.00"))],
                )
            ],
        )
        assert "الخيارات: شيبس محمّر (+10.00)، بصل مكرمل (+5.00)" in message
        assert "الإضافات: جبنة شيدر (+15.00)، بيكون (+20.00)" in message

    def test_item_without_options_addons_or_note_leaves_no_empty_lines(
        self, wa_settings
    ) -> None:
        message = _message(
            wa_settings,
            items=[{"name": "ماء", "quantity": 1, "line_total": Decimal("5.00")}],
        )
        assert "- ماء × 1 = 5.00 EGP" in message
        assert "الخيارات:" not in message
        assert "الإضافات:" not in message
        assert "ملاحظة:" not in message

    def test_totals_are_labeled_in_arabic_with_the_currency(self, wa_settings) -> None:
        message = _message(
            wa_settings,
            subtotal=Decimal("150.00"),
            discount_amount=Decimal("20.00"),
            delivery_fee=Decimal("25.00"),
            total=Decimal("155.00"),
            coupon_code="SAVE20",
        )
        assert "الإجمالي الفرعي: 150.00 EGP" in message
        assert "الخصم: -20.00 EGP (كوبون: SAVE20)" in message
        assert "رسوم التوصيل: 25.00 EGP" in message
        assert "الإجمالي: 155.00 EGP" in message

    def test_discount_without_a_coupon_code_has_no_parentheses(self, wa_settings) -> None:
        message = _message(
            wa_settings, discount_amount=Decimal("10.00"), coupon_code=None
        )
        assert "الخصم: -10.00 EGP" in message
        assert "كوبون" not in message

    def test_zero_discount_and_zero_delivery_lines_are_omitted(self, wa_settings) -> None:
        message = _message(
            wa_settings,
            discount_amount=Decimal("0.00"),
            delivery_fee=Decimal("0.00"),
        )
        assert "الخصم:" not in message
        assert "رسوم التوصيل:" not in message
        assert "الإجمالي: 150.00 EGP" in message

    def test_payment_method_is_shown_by_its_arabic_label(self, wa_settings) -> None:
        # هنا بنبعث **الاسم** مش الـcode: الموظف مايعرفش "cash".
        assert "طريقة الدفع: كاش" in _message(wa_settings, payment_method="cash")
        assert "طريقة الدفع: محفظة إلكترونية" in _message(
            wa_settings, payment_method="ewallet"
        )
        assert "طريقة الدفع: فيزا" in _message(wa_settings, payment_method="card")

    def test_unknown_payment_code_is_shown_as_is_instead_of_blank(
        self, wa_settings
    ) -> None:
        # مفيش كود ناقص في القاموس → نعرض الكود نفسه. الفراغ هنا معناه
        # «الموظف مايعرفش هيستلم إزاي» وهو أسوأ من إن يظهر الكود.
        assert "طريقة الدفع: bank" in _message(wa_settings, payment_method="bank")

    def test_no_payment_line_when_the_customer_chose_none(self, wa_settings) -> None:
        assert "طريقة الدفع:" not in _message(wa_settings, payment_method=None)

    def test_explicit_delivery_name_wins_over_the_delivery_type(self, wa_settings) -> None:
        message = _message(
            wa_settings, delivery_name="توصيل - المعادي", delivery_type="delivery"
        )
        assert "طريقة الاستلام: توصيل - المعادي" in message

    def test_fallback_labels_cover_pickup_and_delivery(self, wa_settings) -> None:
        pickup = _message(wa_settings, delivery_type="pickup")
        assert "طريقة الاستلام: استلام من المطعم" in pickup
        delivery = _message(wa_settings, delivery_type="delivery")
        assert "طريقة الاستلام: توصيل" in delivery

    def test_pickup_time_defaults_to_asap_and_formats_when_set(self, wa_settings) -> None:
        assert "موعد الطلب: في أقرب وقت" in _message(wa_settings, pickup_time=None)
        scheduled = _message(
            wa_settings, pickup_time=datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc)
        )
        assert re.search(
            r"موعد الطلب: \d{4}-\d{2}-\d{2} \d{2}:\d{2}", scheduled
        ), scheduled

    def test_order_notes_close_the_message(self, wa_settings) -> None:
        message = _message(wa_settings, notes="اتصلوا بي قبل التحضير")
        assert message.splitlines()[-1] == "ملاحظات: اتصلوا بي قبل التحضير"

    def test_blank_notes_leave_no_dangling_section(self, wa_settings) -> None:
        assert "ملاحظات:" not in _message(wa_settings, notes=None)

    def test_arabic_labels_survive_verbatim(self, wa_settings) -> None:
        """الرسالة بتتقري من موظف مصري بصريًّا — كل تسمية عربية لازم
        تفضل زي ما هي (أي تحويل ترميز أو تطبيع بيظهر فوراً)."""
        message = _message(wa_settings)
        for label in (
            "المنتجات:",
            "الإجمالي الفرعي:",
            "الإجمالي:",
            "طريقة الاستلام:",
            "موعد الطلب:",
        ):
            assert label in message, label

    def test_every_order_line_is_separated_by_a_real_newline(self, wa_settings) -> None:
        """لو حد حطّ `\n` ناقصة هيتلبّط نص الرسالة وسط السطور."""
        message = _message(wa_settings, notes="ملاحظة")
        lines = message.split("\n")
        assert len(lines) >= 10
        assert all("\r" not in line for line in lines)


# ---------------------------------------------------------------------------
# 2) رقم الوجهة في رابط click-to-chat
# ---------------------------------------------------------------------------


def _number(url: str) -> str:
    """جزء الرقم من `https://wa.me/<رقم>?text=...` — مفكوك الترميز.

    لازم نفكّ `quote(..., safe="")`: `+` بتوصل `%2B` في الـURL، والمقارنة
    لازم تكون على الرقم الفعلي اللي المتصفح بيحلّه — مش على شكل الـURL
    المضبوط.
    """
    prefix = "https://wa.me/"
    assert url.startswith(prefix), url
    rest = url[len(prefix) :]
    return unquote(rest.split("?", 1)[0])


class TestBuildWhatsAppUrl:
    def test_local_egyptian_number_is_internationalized(self) -> None:
        """الشكل المحلي (`01012345678`) **مش معادل دولي**.

        `wa.me` عايز E.164 من غير `+`؛ لينك `wa.me/010...` بيفتح شات
        غير صالح والعميل مايقدرش يبعت طلبه. الرقم ده بالظبط اللي
        نموذج الإعدادات بيقبله، فالتصحيح واجب مش تحسين.
        """
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("01012345678", "الرسالة")) == "201012345678"

    def test_already_international_number_is_left_alone(self) -> None:
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("201012345678", "hi")) == "201012345678"

    def test_explicit_plus_prefix_is_preserved(self) -> None:
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("+201012345678", "hi")) == "+201012345678"

    def test_double_zero_prefix_becomes_plus(self) -> None:
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("00201012345678", "hi")) == "+201012345678"

    def test_non_egyptian_numbers_are_never_guessed(self) -> None:
        """مفيش تخمين برة مصر: رقم عربي/أجنبي بيروح زي ما هو، وإلا
        كنا حوّلناه لرقم **غلط** ودّانا لعميل غلط."""
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("+966512345678", "hi")) == "+966512345678"
        assert _number(build_whatsapp_url("966512345678", "hi")) == "966512345678"

    def test_local_landline_is_left_alone(self) -> None:
        """أرضي (`02...`) مش موبايل، ومفيش أساس نتأكّد منه للكود الدولي —
        نسيبه زي ما هو بدل ما نخمّن ونغلط."""
        from app.utils import build_whatsapp_url

        assert _number(build_whatsapp_url("0212345678", "hi")) == "0212345678"

    def test_message_is_url_encoded_and_round_trips(self) -> None:
        from app.utils import build_whatsapp_url

        message = "طلب جديد من مطعم الاختبار\nالكمية: 2"
        url = build_whatsapp_url("201012345678", message)
        assert "?" in url
        query = url.split("?", 1)[1]
        assert query.startswith("text=")
        encoded = query[len("text=") :]
        # مفيش مسافات ولا أسطر خام في الـURL
        assert " " not in encoded and "\n" not in encoded
        assert "%0A" in encoded
        assert unquote(encoded) == message

    def test_arabic_in_both_number_and_message_never_breaks_the_url(self) -> None:
        from app.utils import build_whatsapp_url

        url = build_whatsapp_url("01012345678", "رقم الطلب: #1042 — شكرًا")
        number = _number(url)
        assert number == "201012345678"
        assert unquote(url.split("?", 1)[1][len("text=") :]) == "رقم الطلب: #1042 — شكرًا"

    def test_blank_digitless_or_none_number_yields_no_link(self) -> None:
        from app.utils import build_whatsapp_url

        assert build_whatsapp_url("", "hi") == ""
        assert build_whatsapp_url(None, "hi") == ""
        assert build_whatsapp_url("الخط ما ينفعش", "hi") == ""


# ---------------------------------------------------------------------------
# 3) نفس النتيجة من الطرف — عبر `POST /orders`
# ---------------------------------------------------------------------------


class TestOrderHandoffDestination:
    def _persist_local_number(self, db, number: str):
        """نضمن إن صف الإعدادات الوحيد هو اللي فيه رقم المطعم المحلي.

        متنحطّش صف جديد: `get_settings` بيرجّع **أول صف**، ولو الملفات
        التانية سايبة صف قديم من اختبار قبلها، هيرجّعه هو ويخلّي
        فرضيتنا عن رقم الوجهة غلط. فنعدّل الموجود أو ننشئ لو مفيش.
        """
        from sqlalchemy import select

        from app.models import RestaurantSettings

        row = db.scalar(select(RestaurantSettings))
        if row is None:
            row = RestaurantSettings(restaurant_name="مطعم الاختبار")
            db.add(row)
        row.whatsapp_number = number
        db.commit()
        return row

    def test_whatsapp_handoff_number_is_international_even_when_settings_store_local(
        self, client: TestClient, db, wa_settings, menu
    ) -> None:
        """المسار الكامل: إعدادات بالشكل المحلي → رابط تحويل دولي."""
        self._persist_local_number(db, "01012345678")

        from tests.test_branches import _payload

        payload = _payload(menu.id, customer_phone=OWN_PHONES[1])
        response = client.post("/orders", json=payload)
        assert response.status_code in (200, 201), response.text
        body = response.json()

        url = body.get("whatsapp_url") or ""
        assert url, "طلب واتساب لازم يرجّع رابط"
        assert _number(url) == "201012345678", url

    def test_system_order_still_has_no_whatsapp_url(
        self, client: TestClient, db, wa_settings, menu
    ) -> None:
        """طلب `system` ما بيحطّش العميل في واتساب خالص — مش تغيّر."""
        self._persist_local_number(db, "01012345678")

        from tests.test_branches import _payload

        payload = _payload(menu.id, customer_phone=OWN_PHONES[2], channel="system")
        response = client.post("/orders", json=payload)
        assert response.status_code in (200, 201), response.text
        assert response.json()["whatsapp_url"] is None

    def test_stored_order_message_is_the_arabic_order_text(
        self, client: TestClient, db, wa_settings, menu
    ) -> None:
        """الرسالة المحفوظة في السجل لازم تكون النص العربي الكامل، مش مسودة."""
        self._persist_local_number(db, "01012345678")

        from tests.test_branches import _payload

        payload = _payload(menu.id, customer_phone=OWN_PHONES[3])
        response = client.post("/orders", json=payload)
        assert response.status_code in (200, 201), response.text

        from sqlalchemy import select

        from app.models import Order

        order = db.scalar(select(Order).order_by(Order.id.desc()))
        stored = order.whatsapp_message or ""
        assert stored.startswith("طلب جديد من")
        assert "رقم الطلب: #" in stored
        assert f"الهاتف: {OWN_PHONES[3]}" in stored
        assert "المنتجات:" in stored
        assert "الإجمالي:" in stored
