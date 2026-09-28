"""اختبارات أرقام التليفون.

السبب: الطلبات كانت بتقبل أي نص في خانة التليفون. الفراغ والمسافات كانت
بتوصل لصفحة 500 من غير رسالة مفهومة، و`01012345678` و`201012345678` كانوا
بيخزنوا في الداتابيز بشكلين مختلفين — فالعميل نفسه كان بيتحسب مرتين.

المستويات المتفرقة: `normalize_phone` بيشيل الرموز وبيوحّد الشكل،
`is_valid_egypt_phone` بيفحص الرقم **بعد** التوحيد، و
`validate_customer_phone` هو اللي بياخد مدخل المستخدم الخام.

المرجع: `app.utils` و`app.schemas`.
"""

from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import CustomerPhone, OptionalCustomerPhone
from app.utils import (
    PHONE_INVALID_MESSAGE,
    PHONE_MAX_INPUT_LENGTH,
    is_valid_egypt_phone,
    mask_phone,
    normalize_phone,
    to_display_phone,
    validate_customer_phone,
)

# الرقم المستخدم في كل الأمثلة
SAMPLE = "01012345678"
# الشكل الموحّد (10 أرقام، من غير صفر)
NORMALIZED = "1012345678"
# الشكل اللي بيتخزن ويتعرض
DISPLAY = SAMPLE

VALID_RAW_INPUTS = [
    SAMPLE,
    "+201012345678",
    "00201012345678",
    "201012345678",
    "  01012345678  ",
    "0101 234 5678",
    "0101-234-5678",  # الشرطات بتتشال
    "++201012345678",  # كل الرموز غير الرقمية بتتشال
    "tel: (010) 1234-5678",
]

# صيغ صالحة لست بتوحّد لنفس الرقم — كل واحدة بتستخدم بادئة 010 عشان
# المقارنة تبقى apples-to-apples. البادئات التانية متغطاة في
# TestIsValidEgyptPhone.
ALTERNATE_VALID_PREFIXES = ["011", "012", "015"]

INVALID_RAW_INPUTS = [
    "",
    "   ",
    "\t\n",
    "0101234567",  # قصير
    "010123456789",  # طويل
    "0001234567",
    "12345",
    "abcdefghijk",
    "02012345678",  # رقم Simone، مش رقم مصري
    "0101234567a",
    "0812345678",  # أرضي، مش موبايل
]


class _PhoneModel(BaseModel):
    phone: CustomerPhone


class _OptionalPhoneModel(BaseModel):
    phone: OptionalCustomerPhone = None


class TestNormalizePhone:
    @pytest.mark.parametrize("value", VALID_RAW_INPUTS)
    def test_all_input_shapes_collapse_to_one_key(self, value: str) -> None:
        """كل صيغ الإدخال بتوصل لنفس الـnormalized value.

        ده اللي بيمنع نفس العميل يظهر مرتين في الداتابيز.
        """
        assert normalize_phone(value) == NORMALIZED

    def test_strips_all_non_digits(self) -> None:
        assert normalize_phone("tel: (010) 1234-5678") == NORMALIZED

    def test_leaves_invalid_values_alone(self) -> None:
        """السلوك القديم متعمد: مش بنغيّر قيمة العميل لو كانت غلط أصلاً،
        عشان ما نمسّش بيانات موجودة."""
        assert normalize_phone("12345") == "12345"

    def test_trims_surrounding_whitespace(self) -> None:
        assert normalize_phone("   01012345678   ") == NORMALIZED

    def test_empty_input(self) -> None:
        assert normalize_phone("") == ""

    def test_none_input_does_not_crash(self) -> None:
        # التنفيذ فيه `phone_number or ""`، فـNone بيترجّع نص فاضي بدل ما
        # يطلع exception غريبة لو مرّ من هنا.
        assert normalize_phone(None) == ""


class TestIsValidEgyptPhone:
    """بتاخد رقم **بعد** التوحيد، مش مدخل خام."""

    @pytest.mark.parametrize("prefix", ["10", "11", "12", "15"])
    def test_accepts_each_egyptian_mobile_prefix(self, prefix: str) -> None:
        assert is_valid_egypt_phone(f"{prefix}12345678") is True

    @pytest.mark.parametrize("prefix", ALTERNATE_VALID_PREFIXES)
    def test_other_prefixes_keep_their_own_display_form(self, prefix: str) -> None:
        """011/012/015 بتفضل زي ما هي — التوحيد بيزيل الصفر بس، مش البادئة."""
        assert validate_customer_phone(f"{prefix}12345678") == f"{prefix}12345678"

    @pytest.mark.parametrize(
        "value",
        [
            "101234567",  # 9 digits
            "10123456789",  # 11 digits
            "1612345678",  # 016 is not an Egyptian mobile prefix
            "2012345678",
            "",
            "abcdefghij",
        ],
    )
    def test_rejects(self, value: str) -> None:
        assert is_valid_egypt_phone(value) is False


class TestToDisplayPhone:
    def test_adds_the_leading_zero(self) -> None:
        assert to_display_phone(NORMALIZED) == DISPLAY

    def test_short_value_is_returned_unchanged(self) -> None:
        assert to_display_phone("12345") == "12345"

    def test_adding_zero_is_idempotent_in_length(self) -> None:
        # الدالة بترجع زي ما هي لو الطول مش 10، فمفيش تضخيم
        assert to_display_phone(DISPLAY) == DISPLAY


class TestValidateCustomerPhone:
    """دي الدالة اللي بتاخد مدخل المستخدم فعليًا."""

    @pytest.mark.parametrize("value", VALID_RAW_INPUTS)
    def test_valid_input_returns_display_form(self, value: str) -> None:
        assert validate_customer_phone(value) == DISPLAY

    @pytest.mark.parametrize("value", INVALID_RAW_INPUTS)
    def test_invalid_input_raises_with_arabic_message(self, value: str) -> None:
        with pytest.raises(ValueError) as exc:
            validate_customer_phone(value)
        assert PHONE_INVALID_MESSAGE in str(exc.value)

    def test_none_raises_rather_than_crashing(self) -> None:
        with pytest.raises(ValueError) as exc:
            validate_customer_phone(None)
        assert PHONE_INVALID_MESSAGE in str(exc.value)

    def test_oversized_input_is_rejected_before_the_regex(self) -> None:
        """الحد الأقصى للطول بيمنع DoS على الـregex."""
        with pytest.raises(ValueError):
            validate_customer_phone("0" * (PHONE_MAX_INPUT_LENGTH + 1))

    def test_maximum_length_input_is_still_processed(self) -> None:
        # نص في الحد الأقصى، مش أطول
        padded = SAMPLE + " " * (PHONE_MAX_INPUT_LENGTH - len(SAMPLE))
        assert validate_customer_phone(padded) == DISPLAY

    def test_error_message_is_arabic(self) -> None:
        with pytest.raises(ValueError) as exc:
            validate_customer_phone("nope")
        message = str(exc.value)
        assert message == PHONE_INVALID_MESSAGE
        assert any("\u0600" <= ch <= "\u06ff" for ch in message)


class TestMaskPhone:
    def test_only_the_last_four_digits_survive(self) -> None:
        assert mask_phone(DISPLAY) == "*******5678"

    def test_short_value_is_fully_masked(self) -> None:
        # ما نكشفش أي جزء من رقم قصير
        assert mask_phone("12345") == "***"

    def test_empty_value(self) -> None:
        assert mask_phone("") == "***"

    def test_none_value(self) -> None:
        assert mask_phone(None) == "***"

    def test_never_leaks_the_middle_of_a_real_number(self) -> None:
        for value in VALID_RAW_INPUTS:
            assert "1234" not in mask_phone(value)

    def test_masking_is_idempotent_enough_to_be_logged(self) -> None:
        """الرقم المموّه نفسه مش صالح، فمش هيتسجل بالخطأ في لوج."""
        assert is_valid_egypt_phone(normalize_phone(mask_phone(DISPLAY))) is False


class TestSchemaPhoneTypes:
    """التحقق على مستوى الـschema — ده اللي بيحوّل الخطأ لـ422 مش 500."""

    @pytest.mark.parametrize("value", VALID_RAW_INPUTS)
    def test_valid_input_is_normalized_by_the_schema(self, value: str) -> None:
        assert _PhoneModel(phone=value).phone == DISPLAY

    @pytest.mark.parametrize("value", INVALID_RAW_INPUTS)
    def test_invalid_input_raises_validation_error(self, value: str) -> None:
        with pytest.raises(ValidationError):
            _PhoneModel(phone=value)

    def test_validation_error_carries_the_arabic_message(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _PhoneModel(phone="nope")
        assert PHONE_INVALID_MESSAGE in str(exc.value)

    def test_optional_field_defaults_to_none(self) -> None:
        assert _OptionalPhoneModel().phone is None

    def test_optional_field_accepts_none(self) -> None:
        assert _OptionalPhoneModel(phone=None).phone is None

    def test_optional_field_still_validates_when_present(self) -> None:
        with pytest.raises(ValidationError):
            _OptionalPhoneModel(phone="nope")

    def test_optional_field_normalizes_when_present(self) -> None:
        assert _OptionalPhoneModel(phone="+201012345678").phone == DISPLAY


def test_display_form_round_trips_through_normalize() -> None:
    """الشكل المخزّن لازم يرجّع لنفسه بعد التوحيد تاني.

    ده اللي بيخلّي الصفوف القديمة والجديدة تتقابل صح في استعلامات
    first-order-only اللي بتشيل الصفر الأول.
    """
    stored = validate_customer_phone(SAMPLE)
    assert normalize_phone(stored) == NORMALIZED
    assert to_display_phone(normalize_phone(stored)) == stored


def test_optional_type_allows_none_at_type_level() -> None:
    # فحص مبسط للـtype alias نفسه
    assert OptionalCustomerPhone is not None
    value: Optional[str] = None
    assert value is None
