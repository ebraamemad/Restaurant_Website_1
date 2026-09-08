from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="Admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    products: Mapped[List["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    base_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # صفر مسموح للمنتجات المسعرة بالخيارات
    unit: Mapped[str] = mapped_column(String(40), default="piece")
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_offer: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    category: Mapped[Category] = relationship(back_populates="products")
    order_items: Mapped[List["OrderItem"]] = relationship(back_populates="product")
    options: Mapped[List["ProductOption"]] = relationship(back_populates="product")
    option_groups: Mapped[List["ProductOptionGroup"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    addons: Mapped[List["Addon"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Addon(Base):
    __tablename__ = "addons"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product: Mapped[Product] = relationship(back_populates="addons")
    order_item_addons: Mapped[List["OrderItemAddon"]] = relationship(back_populates="addon")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(30))
    customer_address: Mapped[str] = mapped_column(String(350))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))  # إجمالي المنتجات قبل الخصم والرسوم
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))  # رسوم التوصيل المحفوظة وقت الطلب
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))  # خصم الكوبون المحفوظ وقت الطلب
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    whatsapp_message: Mapped[str] = mapped_column(Text)
    delivery_option_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_options.id", ondelete="SET NULL"))
    delivery_zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_zones.id", ondelete="SET NULL"), index=True)
    coupon_id: Mapped[Optional[int]] = mapped_column(ForeignKey("coupons.id", ondelete="SET NULL"), index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    pickup_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    payment_method: Mapped[Optional[str]] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[List["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    delivery_option: Mapped[Optional["DeliveryOption"]] = relationship(back_populates="orders")
    delivery_zone: Mapped[Optional["DeliveryZone"]] = relationship(back_populates="orders")
    coupon: Mapped[Optional["Coupon"]] = relationship(back_populates="orders")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="orders")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    product_name: Mapped[str] = mapped_column(String(160))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    item_note: Mapped[Optional[str]] = mapped_column(Text)  # ملاحظة خاصة بهذا السطر/المنتج
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items")
    addons: Mapped[List["OrderItemAddon"]] = relationship(back_populates="order_item", cascade="all, delete-orphan")
    options: Mapped[List["OrderItemOption"]] = relationship(back_populates="order_item", cascade="all, delete-orphan")


class OrderItemAddon(Base):
    __tablename__ = "order_item_addons"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"), index=True)
    addon_id: Mapped[Optional[int]] = mapped_column(ForeignKey("addons.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order_item: Mapped[OrderItem] = relationship(back_populates="addons")
    addon: Mapped[Optional["Addon"]] = relationship(back_populates="order_item_addons")


class OrderItemOption(Base):
    """لقطة الخيار المختار وقت الطلب — بتحفظ الاسم والسعر حتى لو الخيار اتعدل/اتحذف بعد كده."""

    __tablename__ = "order_item_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id", ondelete="CASCADE"), index=True)
    option_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product_options.id", ondelete="SET NULL"), index=True)
    group_name: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(120))
    price_adjustment: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order_item: Mapped[OrderItem] = relationship(back_populates="options")
    option: Mapped[Optional["ProductOption"]] = relationship(back_populates="order_item_options")


class RestaurantSettings(Base):
    __tablename__ = "restaurant_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    restaurant_name: Mapped[str] = mapped_column(String(160), default="Restaurant")
    whatsapp_number: Mapped[str] = mapped_column(String(30), default="201000000000")
    phone_number: Mapped[Optional[str]] = mapped_column(String(30))
    address: Mapped[Optional[str]] = mapped_column(String(300))
    logo_url: Mapped[Optional[str]] = mapped_column(String(500))
    banner_url: Mapped[Optional[str]] = mapped_column(String(500))
    app_icon_url: Mapped[Optional[str]] = mapped_column(String(500))
    working_hours: Mapped[Optional[str]] = mapped_column(String(250))
    currency: Mapped[str] = mapped_column(String(10), default="EGP")
    facebook_url: Mapped[Optional[str]] = mapped_column(String(500))
    tiktok_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)  # وصف المطعم — يظهر في Hero والـFooter
    theme_colors: Mapped[Optional[str]] = mapped_column(String(500))  # JSON string for theme colors
    additional_info: Mapped[Optional[str]] = mapped_column(Text)  # Additional info displayed at bottom
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120))
    first_order_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_order_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="customer")


class ProductOptionGroup(Base):
    """مجموعة خيارات/مقاسات للمنتج — مثال: مجموعة "الحجم" بخيارات صغير/وسط/كبير.

    إعدادات المجموعة بتتحكم في سلوك الاختيار عند العميل:
    - is_required: هل لازم العميل يختار من المجموعة دي؟
    - allow_multiple: هل مسموح اختيار أكتر من خيار واحد؟
    - min_selections / max_selections: حدود عدد الاختيارات.
    """

    __tablename__ = "product_option_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # مثال: "الحجم"
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    min_selections: Mapped[int] = mapped_column(Integer, default=0)
    max_selections: Mapped[Optional[int]] = mapped_column(Integer)  # NULL = بلا حد أقصى
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product: Mapped["Product"] = relationship(back_populates="option_groups")
    options: Mapped[List["ProductOption"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class ProductOption(Base):
    __tablename__ = "product_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("product_option_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # مثال: "صغير", "وسط", "كبير"
    price_adjustment: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))  # تعديل السعر
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product: Mapped["Product"] = relationship(back_populates="options")
    group: Mapped[Optional["ProductOptionGroup"]] = relationship(back_populates="options")
    order_item_options: Mapped[List["OrderItemOption"]] = relationship(back_populates="option")


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'percentage' or 'fixed'
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)  # نسبة مئوية أو مبلغ ثابت
    minimum_order: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usage_limit: Mapped[Optional[int]] = mapped_column(Integer)  # عدد الاستخدامات المسموح بها (NULL = غير محدود)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_order_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # صالح للطلب الأول فقط (حسب normalized phone)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="coupon")


class DeliveryOption(Base):
    __tablename__ = "delivery_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # مثال: "التوصيل", "الاستلام"
    description: Mapped[Optional[str]] = mapped_column(Text)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    estimated_time: Mapped[Optional[str]] = mapped_column(String(50))  # مثال: "30-45 دقيقة"
    is_pickup: Mapped[bool] = mapped_column(Boolean, default=False)  # True = استلام من المطعم (بدون عنوان/رسوم)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="delivery_option")


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="delivery_zone")

