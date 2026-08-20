from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    price: Decimal = Field(gt=0, decimal_places=2)
    unit: str = Field(default="kg", min_length=1, max_length=40)
    sort_order: int = Field(default=0, ge=0)
    is_available: bool = True
    is_active: bool = True


class SettingsForm(BaseModel):
    store_name: str = Field(min_length=2, max_length=160)
    whatsapp_number: str = Field(min_length=8, max_length=30)
    phone_number: Optional[str] = Field(default=None, max_length=30)
    address: Optional[str] = Field(default=None, max_length=300)
    working_hours: Optional[str] = Field(default=None, max_length=250)
    currency: str = Field(default="EGP", min_length=1, max_length=10)


class AddonForm(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0, decimal_places=2)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0)


class CartItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=999)
    addon_ids: List[int] = Field(default=[])


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=30)
    customer_address: str = Field(min_length=5, max_length=350)
    notes: Optional[str] = Field(default=None, max_length=1000)
    items: List[CartItemIn] = Field(min_length=1)


class OrderCreated(BaseModel):
    order_id: int
    total: Decimal
    whatsapp_url: str


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
    price: Decimal
    unit: str
    description: Optional[str]
    image_url: Optional[str]
    is_available: bool
    is_active: bool
    addons: List[AddonOut] = []


class OrderItemAddonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    price: Decimal


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: Decimal
    unit: str
    image_url: Optional[str]
    is_available: bool


class DashboardSummary(BaseModel):
    today_orders: int
    week_orders: int
    month_orders: int
    total_sales: Decimal
    customers_count: int
