from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class OrderStatus(str, Enum):
    pending = "pending"
    preparing = "preparing"
    delivered = "delivered"
    cancelled = "cancelled"


class CategoryForm(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True
    is_featured: bool = False


class ProductForm(BaseModel):
    category_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)
    base_price: Decimal = Field(ge=0, decimal_places=2)
    unit: str = Field(default="piece", min_length=1, max_length=40)
    sort_order: int = Field(default=0, ge=0)
    is_available: bool = True
    is_active: bool = True


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
    customer_phone: str = Field(min_length=8, max_length=30)
    # العنوان اختياري عشان طلبات الاستلام من المطعم مش محتاجة عنوان.
    customer_address: Optional[str] = Field(default=None, min_length=5, max_length=350)
    notes: Optional[str] = Field(default=None, max_length=1000)
    items: List[CartItemIn] = Field(min_length=1)
    delivery_option_id: Optional[int] = Field(default=None, gt=0)
    delivery_zone_id: Optional[int] = Field(default=None, gt=0)
    coupon_code: Optional[str] = Field(default=None, max_length=50)
    pickup_time: Optional[datetime] = None


class OrderCreated(BaseModel):
    order_id: int
    status: str
    subtotal: Decimal
    discount_amount: Decimal
    delivery_fee: Decimal
    total: Decimal
    currency: str
    whatsapp_url: str


class DeliveryOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str]
    fee: Decimal
    estimated_time: Optional[str]
    is_pickup: bool


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
    is_featured: bool


class OrderPreviewIn(BaseModel):
    items: List[CartItemIn] = Field(min_length=1)
    delivery_option_id: Optional[int] = Field(default=None, gt=0)
    delivery_zone_id: Optional[int] = Field(default=None, gt=0)
    coupon_code: Optional[str] = Field(default=None, max_length=50)


class CouponValidateIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    items: List[CartItemIn] = Field(min_length=1)


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
