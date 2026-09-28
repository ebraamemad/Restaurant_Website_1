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
    pending = "pending"
    preparing = "preparing"
    delivered = "delivered"
    cancelled = "cancelled"


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
    payment_method: Literal["cash", "ewallet", "card"] = "cash"
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
    delivery_type: str  # pickup | fixed | zone
    is_active: bool


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
