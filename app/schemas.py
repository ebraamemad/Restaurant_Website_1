from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.utils import PHONE_MAX_INPUT_LENGTH, validate_customer_phone

# رقم الموبايل المصري: بيتحقق من صحته على مستوى الـschema قبل أي منطق أعمال.
# ValidationError بتتحول لـ422 مع رسالة عربية واضحة بدل 500.
CustomerPhone = Annotated[
    str,
    Field(max_length=PHONE_MAX_INPUT_LENGTH),
    AfterValidator(validate_customer_phone),
]

# نسخة اختيارية لنسخ الـAPI اللي بتقبل الهاتف ممكن يكون فاضي
# (مثل معاينة الطلب قبل ما العميل يكتب هاتفه).
OptionalCustomerPhone = Annotated[
    Optional[str],
    Field(max_length=PHONE_MAX_INPUT_LENGTH),
    AfterValidator(lambda value: validate_customer_phone(value) if value is not None else None),
]


class OrderStatus(str, Enum):
    """حالات الطلب — المسار الكامل من الاستلام لحد التسليم.

    `delivered` موجودة من V1 وبيفضل مقبول ومقصود نحافظ عليها: في طلبات
    مسجّلة في الداتابيز بالحالة دي، وإسقاطها معناه بيانات ضايعة. Phase 2
    بتضيف `completed` كحالة نهائية جديدة، و`delivered` بتضل حالة نهائية
    برضو عشان السجل القديم يفضل مقروء.

    ترتيب القيم هنا هو ترتيب العرض في لوحة التحكم. `cancelled` و`completed`
    و`delivered` حالات نهائية: `allowed_next_statuses` بترفض أي انتقال
    منهم.
    """

    pending = "pending"
    confirmed = "confirmed"
    preparing = "preparing"
    ready = "ready"
    out_for_delivery = "out_for_delivery"
    completed = "completed"
    # legacy V1 — نهائية، ومقروءة في اللوحة
    delivered = "delivered"
    cancelled = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES


_TERMINAL_STATUSES = frozenset(
    {OrderStatus.completed, OrderStatus.delivered, OrderStatus.cancelled}
)

# المسار الطبيعي للطلب. `cancelled` مسموح من أي حالة غير نهائية (طلب
# بيلغي من أوله لآخره)، و`delivered` مسموح بعد `out_for_delivery` بس عشان
# تبقى توافقية مع V1 من غير ما نفتح انتقال غريب.
_STATUS_FLOW: dict[OrderStatus, tuple[OrderStatus, ...]] = {
    OrderStatus.pending: (
        OrderStatus.confirmed,
        OrderStatus.preparing,
        OrderStatus.cancelled,
    ),
    OrderStatus.confirmed: (
        OrderStatus.preparing,
        OrderStatus.cancelled,
    ),
    OrderStatus.preparing: (
        OrderStatus.ready,
        OrderStatus.completed,
        OrderStatus.cancelled,
    ),
    OrderStatus.ready: (
        OrderStatus.out_for_delivery,
        OrderStatus.completed,
        OrderStatus.cancelled,
    ),
    OrderStatus.out_for_delivery: (
        OrderStatus.completed,
        OrderStatus.delivered,
        OrderStatus.cancelled,
    ),
    OrderStatus.completed: (),
    OrderStatus.delivered: (),
    OrderStatus.cancelled: (),
}

# طلب اتسجل بحالة مش معروفة (زي `new` اللي لقيناه في داتابيز التطوير من
# شغل 2026-09-21) بيتعامل كـ`pending`: أحسن من إنه يفضل معطل في اللوحة.
_LEGACY_STATUS_ALIASES: dict[str, OrderStatus] = {
    "new": OrderStatus.pending,
}


def coerce_order_status(value: Optional[str]) -> OrderStatus:
    """يحوّل أي نص مخزّن لـ`OrderStatus`، أو بيرمي `ValueError`."""
    if isinstance(value, OrderStatus):
        return value
    raw = (value or "").strip().lower()
    aliased = _LEGACY_STATUS_ALIASES.get(raw, raw)
    try:
        return OrderStatus(aliased)
    except ValueError as exc:
        raise ValueError(f"حالة طلب غير معروفة: {value!r}") from exc


def allowed_next_statuses(current: OrderStatus) -> tuple[OrderStatus, ...]:
    """الحالات المسموحة والانتقال ليها من `current`."""
    if current.is_terminal:
        return ()
    return _STATUS_FLOW.get(current, (OrderStatus.cancelled,))


def is_transition_allowed(current: OrderStatus, target: OrderStatus) -> bool:
    if current == target:
        return True
    return target in allowed_next_statuses(current)


class PaymentStatus(str, Enum):
    """حالة الدفع — منفصلة عن حالة الطلب عن قصد.

    طلب ممكن يكون `preparing` والدفع لسه `pending` (كاش لسه ما اتدفعش)،
    وطلب تاني ممكن يكون `cancelled` والدفع لسه `pending` كمان. مفيش حالة
    `refunded` في المرحلة دي — الحالات الأربعة المطلوبة بس.
    """

    pending = "pending"
    confirmed = "confirmed"
    paid = "paid"
    failed = "failed"



class OrderChannel(str, Enum):
    """قناة الطلب — بتوصف إزاي الطلب بدأ، مش إيه نوعه.

    `system`: العميل عمل الطلب من الموقع، والطلب شايف في لوحة التحكم.
    `whatsapp`: العميل عمل الطلب من الموقع، وبعدين حوّلناه لواتساب.

    الحالتين بيخلّوا نفس صف الـ`Order` — مفيش طلبات واتساب في داتابيز
    منفصلة. الفرق في التحويل بس.
    """

    system = "system"
    whatsapp = "whatsapp"


class CategoryForm(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class ProductForm(BaseModel):
    category_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)
    base_price: Decimal = Field(ge=0, decimal_places=2)
    unit: str = Field(default="piece", min_length=1, max_length=40)
    sort_order: int = Field(default=0, ge=0)
    is_available: bool = True
    is_active: bool = True
    is_featured: bool = False
    is_offer: bool = False


class SettingsForm(BaseModel):
    restaurant_name: str = Field(min_length=2, max_length=160)
    whatsapp_number: str = Field(min_length=8, max_length=30)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    address: Optional[str] = Field(default=None, max_length=300)
    working_hours: Optional[str] = Field(default=None, max_length=250)
    currency: str = Field(default="EGP", min_length=1, max_length=10)
    facebook_url: Optional[str] = Field(default=None, max_length=500)
    tiktok_url: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=2000)
    additional_info: Optional[str] = Field(default=None, max_length=2000)


class AddonForm(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0, decimal_places=2)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0)


class CartItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=999)
    option_id: Optional[int] = Field(default=None, gt=0)  #legacy: خيار واحد
    option_ids: List[int] = Field(default=[])
    addon_ids: List[int] = Field(default=[])
    item_note: Optional[str] = Field(default=None, max_length=500)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: CustomerPhone
    # العنوان اختياري عشان طلبات الاستلام من المطعم مش محتاجة عنوان.
    customer_address: Optional[str] = Field(default=None, min_length=5, max_length=350)
    notes: Optional[str] = Field(default=None, max_length=1000)
    items: List[CartItemIn] = Field(min_length=1)
    delivery_option_id: Optional[int] = Field(default=None, gt=0)
    delivery_zone_id: Optional[int] = Field(default=None, gt=0)
    coupon_code: Optional[str] = Field(default=None, max_length=50)
    pickup_time: Optional[datetime] = None
    # `payment_method` القديم بيفضل مقبول عشان أي عميل V1 بيبعته مابيكسرش.
    # Phase 2 بتستخدم `payment_method_id` (FK حقيقي) والقديم بيتحوّل لـcode
    # في `app/routers/public.py`.
    payment_method: Optional[Literal["cash", "ewallet", "card"]] = None
    payment_method_id: Optional[int] = Field(default=None, gt=0)
    # الفرع: اختياري هنا على السكيما عشان مابكسرش أي عميل قديم بيبعت
    # طلب من غير فرع. لو مبعتش، السيرفر بيرجعله الفرع الافتراضي، والعمود
    # نفسه NOT NULL في الداتابيز، فمفيش طريقة يفضل فيها طلب من غير فرع.
    branch_id: Optional[int] = Field(default=None, gt=0)
    channel: OrderChannel = OrderChannel.whatsapp


class OrderCreated(BaseModel):
    order_id: int
    status: str
    subtotal: Decimal
    discount_amount: Decimal
    delivery_fee: Decimal
    total: Decimal
    currency: str
    branch_id: int
    channel: str
    # بيتقبل في طلبات `system` بس. طلبات `whatsapp` بترجّع الرابط.
    whatsapp_url: Optional[str] = None
    # Phase 2 — التتبّع والسجل. الحقلين دول بيشتغلوا مع بعض:
    # - `tracking_token`: بيرجع **مرة واحدة** هنا بس، والعميل بيحفظه في
    #   المتصفح. مبيتخزّنش في الداتابيز — الداتابيز شايفة الهاش بس.
    # - `tracking_url`: رابط جاهز للعميل يفتحه أو ينسخه. نفس الـtoken.
    # لو العميل فقد الـtoken، مفيش استرجاع: مفيش حساب ولا بريد ولا هاتف
    # بتبعت عليه، وده مقصود في تصميم "بلا تسجيل دخول".
    tracking_token: str
    tracking_url: str
    #Phase 2 — الدفع. لقطة الاسم وقت الطلب، مش الاسم الحالي في الجدول.
    payment_method_code: Optional[str] = None
    payment_method_label: Optional[str] = None
    payment_status: str
    # `call_center` بس: الرسوم لسه متحددةش والأدمن هيحدّدها. معناها إن
    # `delivery_fee` هنا مبدئي مش نهائي.
    delivery_fee_pending: bool = False


class BranchOut(BaseModel):
    """فرع ظاهر للعميل. الفروع غير المفعّلة مش بتظهر خالص."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    address: Optional[str]
    phone: Optional[str]
    opening_hours: Optional[str]


class BranchForm(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    address: Optional[str] = Field(default=None, max_length=300)
    phone: Optional[str] = Field(default=None, max_length=30)
    opening_hours: Optional[str] = Field(default=None, max_length=250)
    is_active: bool = True


class OrderingStatusOut(BaseModel):
    """حالة استقبال الطلبات دلوقتي — عشان العميل يقدر يتفرج/lمّا مقفول.

    الساعات دي globals على مستوى المطعم كله، مش على مستوى الفرع.
    """

    is_open: bool
    open_time: Optional[str] = None  # "HH:MM"
    close_time: Optional[str] = None  # "HH:MM"
    overnight: bool = False  # الراحة بتعدّي نص الليل (18:00 → 02:00)
    message: str = ""


class DeliveryOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    fee: Decimal
    estimated_time: Optional[str]
    # pickup | fixed | zone | call_center
    delivery_type: str
    is_active: bool


class PaymentMethodOut(BaseModel):
    """طريقة دفع مفعّلة Choosable للعميل.

    **مفيش رسوم على الدفع في المرحلة دي** — التحصيل بيتم على الطاولة أو
    عن طريق العميل مباشرة مع طريقة الدفع المختارة.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: Optional[str]
    # 'offline' (العميل بيدفع بنفسه) | 'online' (بوابة مستقبلية، غير مفعّلة بعد)
    kind: str


class DeliveryZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    fee: Decimal
    is_active: bool
    sort_order: int


class ProductOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_adjustment: Decimal
    is_available: bool
    sort_order: int


class OptionGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_required: bool
    allow_multiple: bool
    min_selections: int
    max_selections: Optional[int]
    sort_order: int
    options: List[ProductOptionOut] = []


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: Optional[str]
    image_url: Optional[str]
    is_active: bool


class OrderPreviewIn(BaseModel):
    items: List[CartItemIn] = Field(min_length=1)
    delivery_option_id: Optional[int] = Field(default=None, gt=0)
    delivery_zone_id: Optional[int] = Field(default=None, gt=0)
    coupon_code: Optional[str] = Field(default=None, max_length=50)
    customer_phone: OptionalCustomerPhone = None


class PaymentStatusUpdateIn(BaseModel):
    """تحديث حالة الدفع من الأدمن.

    مرجع العملية (`payment_reference`) اختياري و**حر**: في المرحلة دي
    مفيش بوابة دفع بتولّد أرقام مراجع، فالأدمن ممكن يكتب "رقم الإيصال
    1234" أو "اتحوّل على الرقم 100xxxxxxxx" بنفسه. الحقل موجود دلوقتي
    عشان لما البوابة تيجي تكون البيانات جاهزة من غير migration جديد،
    ولأنه بيساعد الأدمن لو عميل زعل من حالة دفع.

    **مفيش حقل card/CVV/PIN في الـpayload** — وده مقصود ومش هينضاف.
    رقم الكارت لو العميل بعته في الشات أو الـform هو بيانات مش
    لوجستية؛ تخزينها معناه قلبScope مخالفة PCI من غير أي فايدة.
    """

    payment_status: PaymentStatus
    payment_reference: Optional[str] = Field(default=None, max_length=120)
    # reason للإداري: بيظهر في صفحة الطلب كسجل داخلي
    note: Optional[str] = Field(default=None, max_length=500)


class AdminDeliveryFeeIn(BaseModel):
    """تحديد رسوم طلب `call_center` يدوياً من الأدمن."""

    # الرسوم بصيغة نص عشان الـform بيبعت string، والتحقق بيحصل هنا.
    # `ge=0` يمنع رسوم سالبة (خصم على التوصيل) — ده مش مفهوم تجارياً
    # ولا معناه أي حاجة في كاش.
    delivery_fee: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    # `delivery_fee_pending=False` بيترتب عليها تلقائياً: الأدمن حدّد
    # الرسوم، فمفيش حاجة معلّقة.
    note: Optional[str] = Field(default=None, max_length=500)


class CouponValidateIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    items: List[CartItemIn] = Field(min_length=1)
    customer_phone: OptionalCustomerPhone = None


class CouponValidateOut(BaseModel):
    valid: bool
    code: str
    discount_type: Optional[str] = None
    discount_value: Optional[Decimal] = None
    discount_amount: Decimal = Decimal("0.00")
    subtotal: Decimal = Decimal("0.00")
    message: str = ""


class OrderPreviewItemOut(BaseModel):
    product_id: int
    name: str
    unit_price: Decimal
    quantity: int
    addons_total: Decimal
    line_total: Decimal
    options: List[str] = []
    item_note: Optional[str] = None


class OrderPreviewOut(BaseModel):
    items: List[OrderPreviewItemOut]
    subtotal: Decimal
    discount_amount: Decimal
    delivery_fee: Decimal
    total: Decimal
    currency: str
    coupon_valid: Optional[bool] = None
    coupon_message: Optional[str] = None
    # `call_center`: الرسوم لسه متحددةش — `delivery_fee` مبدئي
    delivery_fee_pending: bool = False


class AddonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    is_active: bool
    sort_order: int


class ProductDetailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    base_price: Decimal
    unit: str
    description: Optional[str]
    image_url: Optional[str]
    is_available: bool
    is_active: bool
    is_featured: bool = False
    is_offer: bool = False
    category: Optional[CategoryOut] = None
    options: List[ProductOptionOut] = []
    option_groups: List[OptionGroupOut] = []
    addons: List[AddonOut] = []


class OrderItemAddonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    price: Decimal


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    base_price: Decimal
    unit: str
    description: Optional[str]
    image_url: Optional[str]
    is_available: bool
    is_active: bool
    is_featured: bool = False
    is_offer: bool = False
    category: Optional[CategoryOut] = None


class DashboardSummary(BaseModel):
    today_orders: int
    week_orders: int
    month_orders: int
    total_sales: Decimal
    customers_count: int


class CustomerSummary(BaseModel):
    id: int
    phone: str
    name: Optional[str]
    first_order_at: datetime
    last_order_at: datetime
    total_orders: int
    total_spent: Decimal


# ---------------------------------------------------------------------------
# Phase 2 — طلبات العميل وتتبّعها (بلا تسجيل دخول)
# ---------------------------------------------------------------------------


class OrderItemOut(BaseModel):
    """سطر طلب في صفحة التتبّع وسجل العميل.

    الأسعار المعروضة هي **لقطة وقت الطلب** (نفس القيم اللي اتسجلت في
    `order_items`)، مش أسعار اليوم. لو الأدمن عدّل سعر منتج بعدين،
    العميل بيفضل يشوف اللي دفعه فعلاً.
    """

    product_id: Optional[int] = None
    name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    options: List[str] = []
    addons: List[str] = []
    item_note: Optional[str] = None


class OrderStatusStepOut(BaseModel):
    """خطوة واحدة في خط زمن الحالة — عشان الواجهة ترسم شريط التقدم."""

    status: str
    label: str
    # true للخطوات اللي اتقعدت، false لللي لسه ما وصلتهاش
    reached: bool
    # وقت الوصول، None لو لسه ما وصلش
    at: Optional[datetime] = None


class TrackedOrderOut(BaseModel):
    """الطلب في صفحة التتبّع.

    **مفيش بريد ولا هاتف ولا عنوان هنا.** السبب مش الحصة: رمز التتبع
    256-بت وبيتحقق بـhash، فلو سابنا رقم الهاتف في نفس الرد، أي حد يقدر
    ياخد الرقم من DevTools ويجربه على `/api/orders/track` برضه. تحقّق
    الهاش بيبقى بلا قيمة لو الباب التاني مفتوح.
    """

    order_id: int
    status: str
    status_label: str
    # التواريخ بتتحوّل لـstring في الطبقة اللي بترجّع HTML
    created_at: datetime
    # `call_center`: الرسوم لسه متحددةش
    delivery_fee_pending: bool
    delivery_fee: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    total: Decimal
    currency: str
    items: List[OrderItemOut]
    steps: List[OrderStatusStepOut]
    # طلب `call_center` أو رسوم لسه ناقصة: الواجهة تعرض "هيحددها كول سنتر"
    delivery_fee_label: Optional[str] = None
    # ملاحظة الأدمن (بعض ملاحظات المفاتيح بتظهر للعميل)
    customer_note: Optional[str] = None
    # لو الطلب اتلغى: سبب الإلغاء يبقى معروف للعميل
    cancel_reason: Optional[str] = None


class OrderHistoryItemOut(BaseModel):
    """سطر مختصر في سجل طلبات العميل.

    الـtoken مش هنا: كل سطر في السجل هو عنصر مستقل في `localStorage`،
    وبيبقى قابل للتتبع بtoken بتاعه. مفيش سبب نخزّن token مرتين.
    """

    order_id: int
    status: str
    status_label: str
    created_at: datetime
    total: Decimal
    currency: str
    items_count: int
    # آخر اسم طرق في الطلب — عنصر واحد بس عشان السطر يبقى قصير
    items_preview: List[str] = []
    # رابط التتبّع الكامل (token جاي من localStorage وقت البناء)
    tracking_url: str


class ReorderIn(BaseModel):
    """طلب إعادة طلب.

    `token` مطلوب: إعادة الطلب بتعدي على نفس تحقق التتبع بالهاش. مفيش
    مسار "أعد الطلب برقم التليفون" — رقم التليفون مش سر، ومتاح في
    رسالة واتساب.

    **مفيش سطور في الطلب.** السيرفر بيبني السطور من `order_items` الطلب
    نفسه بعد ما يتأكد إن الـtoken بتاعه. العميل بيختار **ايهم** يرجّع
    بـ`line_indexes` (فه indexes حسب ترتيب `order_items.id`)، ولو
    سيبها فاضية معناها "الطلب كله".

    السبب إن الشكل القديم كان بياخد `items` من العميل: يبقى الـtoken
    مجرد تصريح بقراءة أي طلب، والسطور اللي في السلة مش بالضرورة من
    الطلب ده. وبكده `line_indexes` بقت اختيارية فعلاً — سيناريو
    "عايز 3 من 5" موجود، و"عايز حاجة من طلب تاني" مش موجود.
    """

    token: str = Field(min_length=1, max_length=200)
    line_indexes: List[int] = Field(default_factory=list)


class ReorderLine(BaseModel):
    """سطر طلب قابل لإعادة الطلب.

    أبسط ما يكون: id المنتج + الكمية. الخيارات والإضافات بالـid بتاعها
    و**بتتقارن مع الحالة الحالية** في `app/ordering.py` — لو منتج
    اتشال أو خيار بقى unavailable، السطر ده بيتخطى مع سبب، مش بنسجل
    طلب فيه حاجة مش متاحة.
    """

    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=999)
    option_ids: List[int] = Field(default_factory=list)
    addon_ids: List[int] = Field(default_factory=list)


class ReorderSkipped(BaseModel):
    """سطر اتخطّى في إعادة الطلب، مع السبب."""

    # `-1` لما السطر نفسه مش موجود في الطلب anymore (منتج اتشال من
    # `order_items` فعلياً). غير كده هو `product_id` بتاع السطر.
    product_id: int
    reason: str


class ReorderOut(BaseModel):
    """رد إعادة الطلب — سطور صالحة للسلة، مش إنشاء طلب.

    العميل بيراجع السلة وبيعدّل قبل ما يأكد. إعادة الطلب فيها قرار
    (هل لسه عايز نفس الحاجة؟)، والتكرار الآلي بيسبّط طلبات غلط.
    """

    lines: List[ReorderLine]
    # أسطر اتخطّت، كل واحد مع سبب. العميل يشوف ليه سطر مش موجود في
    # السلة الجديدة بدل ما السلة تطلع ناقصة من غير تفسير.
    skipped: List[ReorderSkipped] = Field(default_factory=list)
    subtotal: Decimal
    currency: str


