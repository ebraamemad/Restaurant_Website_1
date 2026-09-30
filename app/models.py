from __future__ import annotations

from datetime import datetime
from datetime import time as dt_time
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

#: `server_default` لـ Boolean. نستخدم `text("false")` مش `sa.false()`
#: عشان `false` كلمة SQL أصلاً، وdialect بيكتبها زي ما هي. أي حاجة
#: تانية هنا كانت هتكتب `"0"` على Postgres — اللي رفضها.
_BOOL_FALSE = text("false")
_BOOL_TRUE = text("true")


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="Admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Phase 3 — إشعارات "طلب جديد". كل حساب أدمن يقدر يعمل اشتراك Web Push
    # خاص بيه على جهازه (ديك توب، موبايل، PWA مثبّت).
    push_subscriptions: Mapped[List["PushSubscription"]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )


class AdminLoginAttempt(Base):
    """محاولات دخول فاشلة إلى لوحة الإدارة — أساس حماية الـbrute-force.

    بنخزن hash من الـIP ومن (IP + username) مش النص الخام، لسببين:
    - الجدول ما يكشفش مين حاول يدخل لو اتقرا أو اتسرق.
    - مفيش أي بيانات شخصية مخزنة، فقط قيم hash مشارة بالمفتاح السري.
    """

    __tablename__ = "admin_login_attempts"
    # الفحص بيصير مرتين في كل طلب: مرة على key_hash ومرة على ip_hash، وفي
    # الحالتين بيتفلتر على attempted_at. الـcomposite indexes دي بتخلّي
    # العدّاد index-only من غير ما يمسّ الجدول. لو فضلنا على single-column
    # بس، Postgres هيعمل bitmap scan لكل عدّاد.
    __table_args__ = (
        Index("ix_admin_login_attempts_key_time", "key_hash", "attempted_at"),
        Index("ix_admin_login_attempts_ip_time", "ip_hash", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Branch(Base):
    """فرع من فروع المطعم.

    المطعم بيكون عنده 1–3 فروع في الغالب. الموديل ده صغير على قد اللي
    محتاجينه دلوقتي: الاسم والعنوان والتليفون وساعات العمل وحالة
    التفعيل.

    قرار مقصود: `opening_hours` هنا **معلومة فقط**. ساعات استقبال الطلبات
    Online globale على مستوى المطعم كله ومش على مستوى الفرع، وده
    متظبط في `RestaurantSettings.ordering_open_time` / `ordering_close_time`.
    ساعات الفرع بتظهر للعميل بس (مثال: "الفتح"),
    ومفيش أي منطق في الـbusiness بيحكم على الوقت بتاعها.
    """

    __tablename__ = "branches"
    # "فرع افتراضي واحد بس" كضمان في الداتابيز مش مجرد اتفاق في الكود:
    # الـpartial unique index ده بيسمح بأي عدد من الفروع العادية، وبيسمح
    # بواحد بس يكون افتراضي. من غيره، طلبين متزامنين في اللوحة ممكن
    # يعملوا اتنين default.
    __table_args__ = (
        Index(
            "ix_branches_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(300))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    opening_hours: Mapped[Optional[str]] = mapped_column(String(250))  # معلومة فقط
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", index=True
    )
    # بتقبل طلب واحد بس، وبيتستخدم كـfallback للطلبات اللي مابتبعش فرع.
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    orders: Mapped[List["Order"]] = relationship(back_populates="branch")


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

    products: Mapped[List["Product"]] = relationship(back_populates="category", order_by="Product.sort_order")


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


class PaymentMethod(Base):
    """طريقة دفع يديرها الأدمن.

   Phase 2: `kind` للتوثيق فقط — `offline` يعني العميل بيدفع بنفسه عند
    الاستلام/الاتصال، و`online` محجوز لبوابة دفع مستقبلية (مش متنفذة في
    المرحلة دي). مفيش أي حقل هنا من نوع رقم كارت/PIN/بيانات بنكية — الحقول
    المتاحة الاسم والوصف فقط، والطلب بيخزّن `code` + لقطة الاسم.

    الاستبدال بـ`code` (مش id) في الطلبات: لو الأدمن غيّر الاسم أو عطّل
    الطريقة، الطلب القديم يفضل يقرا `code` بتاعه واللقطة تفضل تعرض الاسم
    وقت الطلب.
    """

    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(300))
    # 'offline' | 'online' — توثيق للعرض، مش منطق دفع.
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="offline", server_default="offline"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    orders: Mapped[List["Order"]] = relationship(back_populates="payment_method_ref")

    __table_args__ = (
        CheckConstraint("kind IN ('offline', 'online')", name="ck_payment_methods_kind"),
    )


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
    # كل طلب لازم يكون مربوط بفرع. RESTRICT مقصود: ممنوع نمسح فرع لسه
    # فيه طلبات، عشان تاريخ الطلبات ما يضيعش.
    branch_id: Mapped[int] = mapped_column(
        ForeignKey("branches.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # القناة: الطلب بيتعمل من الموقع بس (system) ولا من الموقع مع تحويل
    # لواتساب (whatsapp). دي بتوصف إزاي الطلب بدأ بس — الطلب نفسه ونوعه
    # واحد في الحالتين، ومفيش تكرار في منطق إنشاء الطلب.
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="whatsapp", server_default="whatsapp", index=True
    )
    delivery_option_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_options.id", ondelete="SET NULL"))
    delivery_zone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_zones.id", ondelete="SET NULL"), index=True)
    coupon_id: Mapped[Optional[int]] = mapped_column(ForeignKey("coupons.id", ondelete="SET NULL"), index=True)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    pickup_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ===== Phase 2: الدفع =====
    # `code` القديمة (V1) بتفضل موجودة ومش هنتلمسها — دي تاريخ.
    payment_method: Mapped[Optional[str]] = mapped_column(String(20))
    payment_method_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("payment_methods.id", ondelete="SET NULL"), index=True
    )
    payment_method_code: Mapped[Optional[str]] = mapped_column(String(20))
    # لقطة اسم/تعليمات طريقة الدفع وقت الطلب. لازم يبقى في الطلب مش بس في
    # الجدول: لو الأدمن غيّر الاسم أو طريقة الدفع، الطلب القديم يفضل يعرض
    # الاسم اللي العميل كان شايفه فعلاً لما طلب.
    payment_method_label: Mapped[Optional[str]] = mapped_column(String(120))
    payment_instructions: Mapped[Optional[str]] = mapped_column(String(300))
    payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    # مرجع العملية من بوابة الدفع مستقبلية. مش بنستخدمه في المرحلة دي.
    payment_reference: Mapped[Optional[str]] = mapped_column(String(120))
    payment_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ===== Phase 2: تتبع الطلب بدون تسجيل دخول =====
    # هاش مش رمز. الـtoken نفسه (32 بايت عشوائي) بيرجع للعميل مرة واحدة في
    # رد إنشاء الطلب، واللي بيتخزّن هنا هو SHA-256 بتاعه. يعني تسريب
    # الداتابيز ما بي leakingش أي رابط تتبع شغّال.
    order_tracking_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    # ===== Phase 2: رسوم التوصيل اليدوية (Mode C) =====
    # لازم يكون صريح إن الرسوم لسه متحددةش بدل ما نفترض صفر بصمت.
    delivery_fee_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # ===== Phase 2: لقطات التوصيل =====
    # الرسوم نفسها كانت بتتخزن من V1 (delivery_fee). الإضافي ده أسماء
    # وطريقة التسليم وقت الطلب، عشان الطلب القديم يفضل مقروء حتى لو
    # الأدمن عدّل المنطقة أو_method التوصيل بعد كده.
    delivery_method_label: Mapped[Optional[str]] = mapped_column(String(150))
    delivery_zone_label: Mapped[Optional[str]] = mapped_column(String(120))
    delivery_estimated_time: Mapped[Optional[str]] = mapped_column(String(50))
    delivery_type_snapshot: Mapped[Optional[str]] = mapped_column(String(20))
    branch_name_snapshot: Mapped[Optional[str]] = mapped_column(String(160))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[List["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    # Phase 3 — سجل تغيّرات الحالة. أول سطر (pending) بيتكتب مع إنشاء الطلب،
    # وبعده سطر لكل تغيير في اللوحة. `cascade` + `delete-orphan` مقصود:
    # السجل بتاع طلب مش موجود من غير الطلب.
    status_history: Mapped[List["OrderStatusHistory"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderStatusHistory.changed_at"
    )
    branch: Mapped["Branch"] = relationship(back_populates="orders")
    delivery_option: Mapped[Optional["DeliveryOption"]] = relationship(back_populates="orders")
    delivery_zone: Mapped[Optional["DeliveryZone"]] = relationship(back_populates="orders")
    coupon: Mapped[Optional["Coupon"]] = relationship(back_populates="orders")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="orders")
    payment_method_ref: Mapped[Optional["PaymentMethod"]] = relationship(back_populates="orders")
    push_links: Mapped[List["OrderPushSubscription"]] = relationship(back_populates="order")

    # القيود مكتوبة هنا كمان مش في الـmigration بس — أي حد يقرأ الموديل
    # يشوف القيم المسموحة من غير ما يفتح ملف الـmigration.
    __table_args__ = (
        CheckConstraint(
            "channel IN ('system', 'whatsapp')", name="ck_orders_channel"
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'preparing', 'ready', "
            "'out_for_delivery', 'completed', 'delivered', 'cancelled')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "payment_status IN ('pending', 'confirmed', 'paid', 'failed')",
            name="ck_orders_payment_status",
        ),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    item_note: Mapped[Optional[str]] = mapped_column(Text)  # ملاحظة خاصة بهذا السطر/المنتج
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Optional["Product"]] = relationship(back_populates="order_items")
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
    working_hours: Mapped[Optional[str]] = mapped_column(String(250))  # نص حر للعرض فقط
    # ساعات استقبال الطلبات Online —.globale على مستوى المطعم كله، مش
    # على مستوى الفرع. لو الاتنين NULL (الوضع الافتراضي) الطلب مفتوح
    # طول الوقت زي V1 بالظبط. لو اتحددوا، الراحة بتغطي نص الليل عادي
    # (مثلاً 18:00 → 02:00).
    ordering_open_time: Mapped[Optional[dt_time]] = mapped_column(Time)
    ordering_close_time: Mapped[Optional[dt_time]] = mapped_column(Time)
    currency: Mapped[str] = mapped_column(String(10), default="EGP")
    facebook_url: Mapped[Optional[str]] = mapped_column(String(500))
    tiktok_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text)  # وصف المطعم — يظهر في Hero والـFooter
    theme_colors: Mapped[Optional[str]] = mapped_column(String(500))  # JSON string for theme colors
    additional_info: Mapped[Optional[str]] = mapped_column(Text)  # Additional info displayed at bottom

    # ===== Phase 2: تسمية التطبيق (PWA) =====
    # لو فاضيين، الـmanifest بيرجع لاسم المطعم زي V1 بالظبط. الحقول دي
    # كانت موجودة في داتابيز التطوير من 2026-09-21 والـmigration بتتبناها
    # من غير ما تلمس الأعمدة الموجودة — فالعرض هنا لازم يطابق العرض
    # الموجود فعلاً (160/80) مش العرض اللي كنت متوقعه أنا.
    # سبب الاختيار: `alembic check` كان عايز يقصّ الأعمدة لـ120/40
    # (العرض الجديد أصغر)، والقصّ = تعديل على بيانات موجودة. الزيادة
    # مفيش فيها ضرر، والقصّ فيه ضرر.
    pwa_name: Mapped[Optional[str]] = mapped_column(String(160))
    pwa_short_name: Mapped[Optional[str]] = mapped_column(String(80))
    pwa_icon_url: Mapped[Optional[str]] = mapped_column(String(500))

    # ===== Phase 3: مفاتيح VAPID — محجوزة، مش مستخدمة في المرحلة دي =====
    # المرحلة دي بتوثّق مكانها بس. مفيش إرسال إشعارات في المرحلة دي.
    vapid_private_pem: Mapped[Optional[str]] = mapped_column(Text)
    vapid_public_pem: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Customer(Base):
    """عميل — قد يكون جاي من طلب، أو من استيراد CSV.

    ## `phone` مفتاح التوحيد

    العمود ده `unique`. كل المسارات اللي بتكتب عميل جديد
    (`app.ordering.create_order` للاستخدام العادي، `app.marketing_csv`
    للاستيراد) بتمرّ على `app.utils.normalize_phone` الأول، فـ`0101…`
    و`+20101…` و`0020101…` بتوصل لنفس الصف. ده اللي بيمنع إعداد
   Marketing Center من تقسيم عميل واحد لثلاثة.

    ## `first_order_at` / `last_order_at` بقت nullable

    كانت `NOT NULL` لأن كل عميل كان بيتبق من طلب. الاستيراد بيبق
    عميل **قبل** ما يطلب، فمفيش تاريخ طلبات ليه. الرجوع لـNOT NULL
    كان هيعني إما إنك نكسر الاستيراد، أو إننا نخزّن تاريخ وهمي
    (`created_at`) في عمود اسمه "أول طلب" — وده كدب بيظهر في تقرير
    التسويق كأنه اشترى. `NULL` هنا معناه "لسه ما طلبش" وده أصدق.

    ## `marketing_opt_out` مش زي إذن الإشعارات

    دي موافقة على **رسائل التسويق** تحديداً. إشعارات حالة الطلب
    (transactions) مش Promo، والمطعم ملزم يبعتاها عشان العميل يعرف
    طلبه وصل. لو خلطنا الاتنين، العميل اللي رفض عروض هيتعطل عنه
    إشعار "طلبك في الطريق" — وده ضرر للعميل مش حماية ليه.

    منفصل كمان عن صلاحية اشتراك Web Push: الاشتراك بيقول "المتصفح
    مستعد يستقبل"، و`marketing_opt_out` بيقول "العميل عايز promo
    بس". ممكن عميل يكون مشترك في push وعامد من العروض.
    """

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120))
    first_order_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_order_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ===== Phase 4 — التسويق =====
    # من إجا العميل ده؟ بيفرق في التقارير: عميل من طلب (`order`) عنده
    # تاريخ إنفاق حقيقي، وعميل من استيراد (`import`) لسه معروف برقم
    # واسم بس. ده بيبان في شاشة الاستيراد وكمان في مقاييس الحملة.
    #
    # `default="order"` مقصود: كل العملاء الموجودين من قبل جايين من
    # طلبات، والـbackfill في الـmigration بيحطّ `order` لكل صف قديم.
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="order", server_default="order", index=True
    )
    # رفض العروض. `False` = موافق (الافتراضي). `True` = رفض صريح.
    #
    # **ليه `False` مش "مش معروف":** في marketing push، الرقمنة بتوصل
    # لعميل ما طلبش حاجة مننا عمره — "مش عارف" = "ما أعرفش عايز
    # عروض، فماشي". ويبقى صريح في تسجيل موافقته.
    marketing_opt_out: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    # وقت الرفض. `NULL` معناها لسه موافق. بنحفظه عشان نعرف نرجّع
    # الموافقة لو العميل طلب، ونعرف إمتى طلبت (تاريخ دقيق لا لزوم له
    # في التقرير، بس مفيد للمراجعة).
    marketing_opt_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    orders: Mapped[List["Order"]] = relationship(back_populates="customer")
    push_subscriptions: Mapped[List["PushSubscription"]] = relationship(back_populates="customer")
    campaigns: Mapped[List["CampaignRecipient"]] = relationship(back_populates="customer")

    __table_args__ = (
        CheckConstraint("source IN ('order', 'import')", name="ck_customers_source"),
    )


class ContactImport(Base):
    """دفعة استيراد جهات اتصال من CSV — الـaudit بتاع العملية.

    ## ليه الـbatch مستقر ومش بيتبعت في الـURL

    الاستيراد عملية من 5 خطوات (رفع → فحص → معاينة → استيراد)،
    والصفوف thousands مينفعش تتحمل في hidden field ولا في session.
    فبنخزّن الدفعة في الداتابيز، والمعاينة بتقرأ منها بـ`import_id`
    عادي. ده كمان بيدي أثر: مين استورد إيه وإمتى، وحد كام سطر
    ضاع وليه.
    """

    __tablename__ = "contact_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    # اسم الملف الأصلي — للعرض فقط، مش بنفتحه تاني (الملف نفسه مش
    # بيتخزّن؛ الصفوف المستخرجة هي اللي بتتخزّن).
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # `previewed` = اترفع واتقري، `imported` = اتنفّذ، `discarded` =
    # الأدمن ستّه. الدفعة اللي ما اتنفذتش مينفعش تتنفذ تاني — عشان
    # ما يحصلش double import لو دوس زرار مرتين.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="previewed", server_default="previewed", index=True
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    imported_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # سبب الرفض لو الملف كله اترفض (حجم، ترميز، نوع ملف). بيتعرض
    # للأدمن في رسالة واضحة بدل 500.
    error: Mapped[Optional[str]] = mapped_column(String(300))

    rows: Mapped[List["ContactImportRow"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ContactImportRow.row_number"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('previewed', 'imported', 'discarded')",
            name="ck_contact_imports_status",
        ),
    )


class ContactImportRow(Base):
    """سطر واحد جوه دفعة استيراد — من فضله التحقق والمعالجة.

    ## ليه `raw_*` كمان

    الأدمن بيشوف **اللي في الملف** جنب **اللي عدّلناه**. من غير الـraw
    مش هعرف يفرق بين "الرقم كان غلط في الملف" و"النظام عدّله"،
    و"السطر رجع تاني بسبب تكرار" و"السطر مكرر في الملف نفسه".
    """

    __tablename__ = "contact_import_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(
        ForeignKey("contact_imports.id", ondelete="CASCADE"), index=True
    )
    # رقم السطر في الملف (2 = بعد الـheader). للتواصل مع الأدمن.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_name: Mapped[Optional[str]] = mapped_column(String(300))
    raw_phone: Mapped[Optional[str]] = mapped_column(String(120))
    # بعد `normalize_phone` — الشكل الموحّد اللي بيتخزّن في `customers`.
    normalized_phone: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120))
    # `valid` = جاهز للاستيراد، `invalid` = مرفوض (السبب في `error`)،
    # `duplicate` = الرقم موجود بالفعل، `imported` = اتنفذ.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="valid", server_default="valid", index=True
    )
    # سبب الرفض بالعربي — بيتعرض للأدمن في جدول المعاينة.
    error: Mapped[Optional[str]] = mapped_column(String(300))
    # العميل الموجود مسبقًا لو السطر طلع مكرر. بيخلّي "مكرر" informative
    # مش مجرد "skip".
    existing_customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    # بيتحطّ وقت الاستيراد بس — عشان نعرف مين اتضاف بالظبط.
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    batch: Mapped[ContactImport] = relationship(back_populates="rows")

    __table_args__ = (
        CheckConstraint(
            "status IN ('valid', 'invalid', 'duplicate', 'imported')",
            name="ck_contact_import_rows_status",
        ),
        Index("ix_contact_import_rows_import_status", "import_id", "status"),
    )


class Campaign(Base):
    """حملة تسويقية — الـheader بتاع الرسالة اللي بتتبعت.

    ## فصل `image_url` عن `link_url`

    `image_url` = صورة الأندوك (اللي بتظهر جوه الإشعار على الموبايل).
    `link_url` = الصفحة اللي الدوس على الإشعار بيفتحها.

    الاتنين `Optional` لأن من حق الأدمن يبعت نص بس. والوجهة بتتخزن
    **بعد** تحقق `app.schemas.MarketingLinkUrl` — مفيش `javascript:`
    ولا `data:` ولا رابط خارجي بيمرّي. (الـSW بيعمل تحقق تاني من
    ناحية العميل: الـserver مصدق بس المتصفح هو اللي بيفتح.)

    ## `audience_type` + `segment_key` مع بعض

    مقصود إن `segment_key` يبقى `NULL` لما `audience_type='all'`. ده
    بيدي شرط CHECK في الداتابيز، فالمستحيل تصنع حملة "الكل" ومعاها
    segment متعارض، ممنوع يجي بالبرمجة. `app.marketing.resolve_audience`
    بيفرض نفس القاعدة من ناحية الكود.

    ## الحالات

    ```
    draft ──send──> sending ──> completed | partially_completed | failed
      │                 │
      │                 └──cancel──> cancelled
      └──schedule──> scheduled ──(جرى الوقت)──> sending
    ```

    `partially_completed` مش cosmetic: معناها "بعتنا لبعض"، وده
    قرار تشغيلي بيحتاج يبقى باين في التقرير.
    """

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    # الاسم الداخلي بتاع الأدمن ("عرض الجمعة").
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # العنوان اللي بيظهر في الإشعار. منفصل عن `name` عشان الاسم
    # الداخلي ممكن يبقى "حملة تجريبية" والعنوان "خصم 20% 🎉".
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(600))
    link_url: Mapped[Optional[str]] = mapped_column(String(600))
    # `all` | `segment` | `imported`
    audience_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="all", server_default="all", index=True
    )
    # مفتاح من `app.segments.SEGMENTS`. NULL إلا لو `audience_type='segment'`.
    segment_key: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    # **معااملات الجمهور وقت الإنشاء** — `AudienceParams` متسلسلة كـJSON.
    #
    # ليه دي موجودة: `segments` بتبني الجمهور من معاملات
    # (`recently_active_days`, `inactive_days`, `high_spending_minimum`).
    # لو ما خزّناشها، `send_campaign` هيبني `AudienceParams()` من
    # الـdefaults — والأدمن شاف في المعاينة "آخر 7 أيام" (180 عميل)
    # وبعد ما ضغط إرسال طلع له "آخر 30 يوم" (520 عميل). ده أسوأ
    # نوع من الغلط: صامت، والأرقام في التقرير صح بس بتطابق شريحة غلط.
    #
    # فالمعاينة والإرسال بيبقىوا **نفس** الـparams بالظبط، من غير
    # احتمالات.
    #
    # `default={}` مش `None`: الحملة القديمة (قبل الـmigration) مالها
    # قيمة، و`parse_params()` بيرجّع الـdefaults لو مفيش. فالسطر
    # القديم بيشتغل زي ما كان بالظبط.
    audience_params: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    # `push` | `whatsapp` | `both`. التنفيذ في Phase 5. تسجيل القناة
    # في الحملة بحد ذاته مش معناه إن الرسالة اتبعتت — العكس كمان:
    # `channel='whatsapp'` مش دليل على الإرسال.
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="push", server_default="push", index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft", index=True
    )
    # `index=False` مع الـIndex المركّب في `__table_args__`: المجدول
    # بيسأل `status = 'scheduled' AND scheduled_at <= now` مرتّب
    # بـ`scheduled_at`، وفهرس العمودين هو اللي يخدم الاستعلام ده.
    # فهرس `scheduled_at` لوحده كان هيخلّي Postgres يرتّب كل
    # المجدولات عشان يطلّع القليلة المستحقة.
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(300))
    # العدادات دي **نتايج مش نوايا**: بتتحسب من صفوف `campaign_recipients`
    # وقت الإرسال، مش من تقدير قبل الإرسال. عشان كده التقرير بيقول
    # "اتبعت 412" مش "كان المفروض 412".
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    invalid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by_admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    recipients: Mapped[List["CampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("channel IN ('push', 'whatsapp', 'both')", name="ck_campaigns_channel"),
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'sending', 'completed', "
            "'partially_completed', 'cancelled', 'failed')",
            name="ck_campaigns_status",
        ),
        CheckConstraint(
            "audience_type IN ('all', 'segment', 'imported')",
            name="ck_campaigns_audience_type",
        ),
        # "الكل" ومعاها segment = بيانات متناقضة. القيد بيفصلها بدل ما
        # نسيب اللوجيك يكتشفها بعدين.
        CheckConstraint(
            "(audience_type = 'segment' AND segment_key IS NOT NULL) OR "
            "(audience_type <> 'segment' AND segment_key IS NULL)",
            name="ck_campaigns_audience_segment",
        ),
        # استعلام المجدول بالظبط: `status='scheduled' AND scheduled_at <= now`
        # مرتّبة. الفهرس المركّب هو اللي بيخلّي ده scan مش sort.
        Index("ix_campaigns_status_scheduled", "status", "scheduled_at"),
    )


class CampaignRecipient(Base):
    """سطر واحد في كل حملة: عميل واحد في حملة واحدة.

    ## ليه جدول استقبال منفصل عن الأرقام على الحملة

    العدادات على `Campaign` **محسوبة** من سطور الجدول ده:
    - "اشتراك باط من المزوّد" ← نقدر نعيد المحاولة مرة تانية.
    - "مين العميل ده فعلاً" → لو نفس العميل عنده جهازين، اتبعت
      مرتين، سطرين. ده صح: جهازين = رسالتين على devices تانية.
    - تقرير "did not deliver" بيحتاج سطر لكل جهاز.

    ## `channel` على السطر — ليه التكرار مقصود

        حملة `both` = **سطرين لكل عميل**: واحد `push` وواحد `whatsapp`.
        البديل (عمود `push_status` و`whatsapp_status` على سطر واحد)
        بيسبّب قاع في النظام: كل المسارات بتتفرّع على القناة،
        والعدّادات بتتضاعف، وسؤال "متى نعتبر السطر خلص؟" بيتكرر
        لكل قناة. السطر بيمثّل **(حملة، عميل، قناة)** بوحدها،
        ومعاها `status` واحد معناه واحد، والتقرير بيجمّع حسب
        القناة بتجميع بسيط بدل ما يحسب أعمدة مقترنة.

        تكرار العميل في الحالة دي مقصود: نفس الشخص بيتبعتله إشعار
        على الموبايل ورسالة واتساب — وده معنى `both`.

    ## `status='skipped'` موجود ليش

    الاشتراك ممكن يبقى مش `active` بين لحظة الـresolve ولحظة
    الإرسال (العميل رجع في الإذن، أو المزوّد بطل الـendpoint)،
    والعميل ممكن يكون سجّل نفسه `marketing_opt_out` بعد ما اتحسب
    الجمهور. ده مش فشل — العميل ما طلبش نوصّله. بنفرّق بين
    "مش دلوقتي" و"حاولنا وفشلنا" عشان التقرير يبقى أمين.
    """

    __tablename__ = "campaign_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="SET NULL")
    )
    # لقطة من وقت الحساب، عشان التقرير يبقى مقروء من غير ما نعمل join.
    customer_name: Mapped[Optional[str]] = mapped_column(String(120))
    customer_phone: Mapped[Optional[str]] = mapped_column(String(30))
    # القناة اللي السطر ده ليها: `push` | `whatsapp`.
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="push", server_default="push", index=True
    )
    # `pending` → `sent` | `failed` | `skipped` | `invalid`
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending", index=True
    )
    # سبب `failed`/`skipped` — نكوته مختصر، مش traceback.
    error: Mapped[Optional[str]] = mapped_column(String(300))
    # كود ردة المزوّد (201/410/500)، لو رجع.
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    # --- طابور واتساب --------------------------------------------
    #
    # `attempts` + `next_attempt_at` هما **الطابور نفسه**: مفيش جدول
    # jobs تاني. سطر `pending` وعنده `next_attempt_at` = مستني دوره.
    # سبب إن جدول تاني كان هيبقى مصدر حقيقة تاني للحالة، واللي
    # بيحصل مع جدولين هو بالظبط اللي بنحاول نتجنبه.
    #
    # `next_attempt_at` بيتقدّم بعد كل فشل مؤقت. `NULL` = مستني
    # أول محاولة فوراً.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # اسم المزوّد اللي actuاها الرسالة، و`provider_message_id` بتاعه.
    # بنخزّنهم **على السطر** مش بس في سجل المحاولات عشان التقرير
    # يقدر يجيبهم من غير join على جدول تالت.
    provider: Mapped[Optional[str]] = mapped_column(String(30))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(120))
    provider_status: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign] = relationship(back_populates="recipients")
    customer: Mapped[Optional["Customer"]] = relationship(back_populates="campaigns")
    attempts_log: Mapped[List["WhatsAppAttempt"]] = relationship(
        back_populates="recipient", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped', 'invalid')",
            name="ck_campaign_recipients_status",
        ),
        CheckConstraint(
            "channel IN ('push', 'whatsapp')",
            name="ck_campaign_recipients_channel",
        ),
        Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),
        # استعلام الـworker بالظبط: سطور واتساب مستنية دورها مرتّبة.
        # الفهرس المركّب هو اللي بيخلّي ده scan مش sort.
        Index(
            "ix_campaign_recipients_queue",
            "channel",
            "status",
            "next_attempt_at",
            postgresql_where=text("channel = 'whatsapp' AND status = 'pending'"),
        ),
    )


class WhatsAppAttempt(Base):
    """محاولة إرسال واحدة — سجل لكل نداء مزوّد، ناجح كان أو فاشل.

    ## ليه سجل مستقل عن `CampaignRecipient`

        السطر بيقول **النتيجة الأخيرة**. ده مش كفاية للتفتيش: لما
    رسالة فشلت 3 مرات وبعدين نجحت، "ليه اتأخرت؟" مش مبين من
    `attempts=3`. كل محاولة هنا صف، فالسبب بيتفحص فعلاً.

    والأهم: **الردود الخام بتتنضّف قبل ما تتخزّن**
    (`redact()`)، بس الحماية الحقيقية إن العمود ده
    `String(600)` محدود — ما فيش أثر ضخم في الداتابيز لو المزوّد
    رجّع صفحة HTML كاملة.

    **مش بنخزّن نص الرسالة.** لا الـbody ولا المفتاح. السطر بيقول
    "هل نجح الإرسال؟" من غير ما نخلّي سجل المحاولات قناة تسريب
    لبيانات العملاء.
    """

    __tablename__ = "whatsapp_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("campaign_recipients.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    # رقم المحاولة (1-based) — بيترجم لـ`CampaignRecipient.attempts`.
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    # `sent` | `failed` | `skipped` — نفس قيم `SendState`.
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_status: Mapped[Optional[str]] = mapped_column(String(60))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(120))
    http_status: Mapped[Optional[int]] = mapped_column(Integer)
    # السبب المختصر — **بعد التنضيف**. معروض للأدمن.
    error: Mapped[Optional[str]] = mapped_column(String(300))
    # رد المزوّد المختصر بعد التنضيف، للمعاينة.
    raw: Mapped[Optional[str]] = mapped_column(String(600))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    recipient: Mapped[CampaignRecipient] = relationship(back_populates="attempts_log")

    __table_args__ = (
        CheckConstraint(
            "state IN ('sent', 'failed', 'skipped')",
            name="ck_whatsapp_attempts_state",
        ),
        Index("ix_whatsapp_attempts_recipient", "recipient_id", "attempt_number"),
    )


class WhatsAppConfigRow(Base):
    """إعدادات واتساب القابلة للتعديل من اللوحة — **سطر واحد**.

    ## ليه سطر واحد

        مفيش مفهوم "أكتر من إعدادات" هنا — فيه instance واحدة. لو الجدول
    تاني فجأة، ده معناه إما multi-tenant أو multi-instance، وكل
    واحد فيهم تغيير في التصميم (والأمان). لحد ما ده يحصل، جدول
    من سطر واحد أبسط بكتير من جدول مع فلاتر.

    ## مفيش `api_key` هنا — وده مقصود

    السر في `.env` بس (`EVOLUTION_API_KEY`). لو حطّيناه هنا كان
    في `admin SELECT *` وbackup بياخدوا السر. **مفيش عمود
    مفتاح خالص في الجدول ده**، و`load_config` بيقرا السر من
    `app.config.settings` بس.

    اللي الجدول بيخزّنه: إعدادات **غير حسّاسة** — تشغيل، فاصل،
    دفعة، محاولات، ورابط المزوّد واسم الـinstance (الأدمن لازم
    يعدّلهم من غير SSH).

    القناة Master (`settings.whatsapp_enabled`) مش هنا عن قصد:
    تشغيل المزوّد كله تغيير في السيرفر، مش زرار في اللوحة. لو
    تقدر تشغّل المزوّد بزرار، فأي حد يعمل login يقدر يبعت
    رسايل لعملائك.
    """

    __tablename__ = "whatsapp_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    # القناة مقفولة افتراضياً. **double gate**: لازم `enabled` في
    # الجدول **و** `WHATSAPP_ENABLED` في البيئة. الاتنين مقفولين
    # افتراضياً.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_BOOL_FALSE
    )
    # الإرسال المسموح. منفصل عن `enabled` عشان تقدر تفحص الاتصال
    # وتختبر رسالة من غير ما تفتح قناة على كل العملاء.
    sending_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=_BOOL_FALSE
    )
    # رابط المزوّد واسم الـinstance — بيتoverride الـenvironment لو
    # اتكتبوا هنا. بيتفحصوا بـ`validate_base_url` قبل أي اتصال.
    base_url: Mapped[Optional[str]] = mapped_column(String(300))
    instance: Mapped[Optional[str]] = mapped_column(String(120))
    # --- إيقاع الإرسال --------------------------------------------
    #
    # `message_interval_ms` = الفاصل بين رسالتين. **مش ضمان** إن
    # المزوّد مش هيحظر — مفيش فاصلة بتضمن ده. ده بس بيقلّل
    # الضغط وبيحترم `429`.
    message_interval_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default="1000"
    )
    # حجم الدفعة: عدد الرسايل قبل commit. الدفعة الكبيرة = تأخير
    # commit = الإلغاء بيتأخر.
    batch_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, server_default="20"
    )
    # محاولات **إضافية** بعد الأولى (0 = محاولة واحدة وخلاص).
    # بتطبق على `whatsapp_attempts`، مش على `CampaignRecipient`.
    max_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    updated_by_admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )



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
    """طريقة استلام/توصيل واحدة يعرضها الأدمن للعميل.

    `delivery_type` بيحدد إزاي الرسوم بتتحسب على الـserver:
    - `pickup`: استلام من المطعم — الرسوم **لازم** تكون صفر. بيتفرض في
      `app/ordering.py` مش بس في الكود بتاع الأدمن، عشان مفيش طريق يعدّي
      عليه رسوم استلام.
    - `fixed`: رسوم ثابتة من العمود `fee`.
    - `zone`: رسوم المنطقة المختارة من جدول `delivery_zones`.
    - `call_center`: العميل يطلب من غير رسوم معروفة، والأدمن/كول سنتر
      يحدّد الرسوم بعدين. `orders.delivery_fee_pending` بيبقى true عشان
      الطلب يبقى واضح إنه ناقص، مش صفر بصمت.
    """
    __tablename__ = "delivery_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    estimated_time: Mapped[Optional[str]] = mapped_column(String(50))
    delivery_type: Mapped[str] = mapped_column(String(20), default="fixed")  # pickup | fixed | zone | call_center
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="delivery_option")

    __table_args__ = (
        CheckConstraint(
            "delivery_type IN ('pickup', 'fixed', 'zone', 'call_center')",
            name="ck_delivery_options_delivery_type",
        ),
    )


class DeliveryZone(Base):
    """منطقة توصيل برسومها.

    بتتربط بـ`orders.delivery_zone_id` بـ`ON DELETE SET NULL`، لكن **الحذف
    نفسه بيتعمل كإلغاء تفعيل مش DROP** (انظر `delete_delivery_zone` في
    `app/routers/admin.py`): الرسوم والاسم محفوظين في لقطات على الطلب نفسه،
    فحذف الصف بيسيب `delivery_zone_id = NULL` ويخلي العرض التاريخي ناقص.
    zone_id = NULL` بيكسر تاريخ الطلب. التلغيف بدل الحذف بيحافظ على
    السجل وبيخفي المنطقة من اختيار العميل الجديد.
    """

    __tablename__ = "delivery_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="delivery_zone")


# ---------------------------------------------------------------------------
# Phase 3 foundation — Web Push (NOT implemented in Phase 2)
# ---------------------------------------------------------------------------
# الموديلات دي موجودة عشان المرحلة الجاية (الإشعارات) تلاقي المخطط جاهز
# ومعلَن في الـmodels والـmigrations بدل ما يكون drift غامض. **مفيش أي
# كود في المرحلة دي بيقرأ أو يكتب الجداول دي**، ومفيش إرسال إشعارات.
# الجداول دي كانت موجودة في داتابيز التطوير من شغل يوم 2026-09-21 اللي
# ضاعت شيفرته — الـmigration الحالية بتتبنّاها مش بتعمل DROP.


class PushSubscription(Base):
    """اشتراك Web Push لجهاز/متصفح — عميل أو أدمن.

    ## ليه `endpoint` فريد

    نفس المتصفح بيرسل نفس الـendpoint في كل تسجيل. لو سمحنا بتكراره،
    هنجمع صفوف بتاعت نفس الجهاز ونبعت له الإشعار **مرتين**. عشان كده
    التسجيل بيعمل `upsert` بـON CONFLICT على الفهرس الفريد بدل INSERT.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # `unique=True, index=True` = فهرس فريد واحد اسمه
    # `ix_push_subscriptions_endpoint`.
    endpoint: Mapped[str] = mapped_column(String(600), nullable=False, unique=True, index=True)
    p256dh: Mapped[str] = mapped_column(String(300), nullable=False)
    auth: Mapped[str] = mapped_column(String(200), nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(400))
    device: Mapped[Optional[str]] = mapped_column(String(40))
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    # Phase 3 — مين صاحب الاشتراك. `admin_id` و`customer_id` بيت واحد في
    # نفس الوقت مش اتنين: اشتراك الأدمن مربوط بالحساب، واشتراك العميل
    # بالرقم. `customer_id` بيبقى NULL لاشتراك الأدمن والعكس.
    admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admins.id", ondelete="CASCADE"), index=True
    )

    # حالة الاشتراك نفسه. `active` هو الطبيعي. لما المزوّد يقولي إن الـendpoint
    # مبقاش صالح (404/410 من FCM أو Mozilla) بنحطّه `invalid` بدل ما نمسح
    # الصف: مسح الصف بيمسح تاريخ الإرسال المرتبط بيه، و`invalid` بيقول
    # "جرّبنا ده واشتغلش" من غير ما نكسر الـaudit.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active", index=True
    )
    # آخر مرة المزوّد رفض الـendpoint. بيتحطّ مرة واحدة وقت ما يتعلّم، مش
    # كل مرة — عشى جدول السجلات ميكبرش مع كل محاولة فاشلة.
    invalid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Optional["Customer"]] = relationship(back_populates="push_subscriptions")
    admin: Mapped[Optional["Admin"]] = relationship(back_populates="push_subscriptions")
    order_links: Mapped[List["OrderPushSubscription"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )
    notifications: Mapped[List["PushNotification"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )

    # الفهارس المركّبة دي هي اللي الاستعلامات الحقيقية بتشغّل عليها:
    # "كل اشتراكات العميل النشطة" (`app.push.subscriptions_for_customer`)
    # و"كل اشتراكات الأدمن النشطة" (`active_admin_subscriptions`). فهرس
    # العمود لوحده بيفلتر في الذاكرة بعد ما يجيب كل الصفوف — ده فارق
    # حقيقي لما يبقى عند العميل 5 أجهزة مسجّلة.
    #
    # مصرّح بيها بالاسم المطابق للـmigration عشان `alembic check` يقفل.
    __table_args__ = (
        Index("ix_push_subscriptions_customer_status", "customer_id", "status"),
        Index("ix_push_subscriptions_admin_status", "admin_id", "status"),
    )


class OrderStatusHistory(Base):
    """سجل تغيّرات حالة الطلب — Phase 3.

    ## ليه الجدول ده

    `orders.status` هي **الحالة الحالية** بس. من غير سجل، page التتبّع
    بتقدر تقول "الطلب دلوقتي preparing" بس مش **إمتى** اتغيّرت كل حالة —
    و`PHASE1`/`PHASE2`iyyinDeBoth said this explicitly: "مفيش جدول
    timestamps للحالات... دي نقطة التوسعة."

    السجل ده هو نقطة التوسعة دي. بيفيد في:
    - صفحة التتبّع: كل خطوة يظهر وقت فعلي مش وقت الطلب.
    - الـanalytics: زمن التحضير الحقيقي بدل تقدير.
    - دعم: العميل بيحتج "الطلب اتأكد الساعة كام".

    # الصف الأول

    الطلبات الموجودة قبل Phase 3 مالها سطر `pending` واحد وقت الإنشاء،
    من الـbackfill في الـmigration. ده **مُختلق**: احنا عارفين إن الطلب
    كان pending، ومش عارفين إمتى بالظبط. الحقل `synthetic` بيميز
    السطور دي عن السطور الحقيقية اللي اتسجلت بعد كده.
    """

    __tablename__ = "order_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # وقت التغيير. `server_default=now()` عشان الصف يتسجل من الداتابيز
    # مش من الكود — ساعت السيرفر هي المرجع في كل اللوجات.
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    # مين غيّر الحالة. NULL = العميل أو نظام تلقائي (إنشاء الطلب).
    # مينفعش نخليها NOT NULL: إنشاء الطلب مفيش أدمن بيشغّله.
    #
    # `index=False` مع `Index(...)` صريح في `__table_args__` — الفهرس
    # الحقيقي اسمه `ix_order_status_history_admin_id` (اللي migration
    # بيخلّقه). لو سبنا `index=True`، SQLAlchemy هيولّد
    # `ix_order_status_history_changed_by_admin_id` و`alembic check`
    # هيشوف فارق: فهرس الموديل مش موجود، وفهرس الداتابيز زائد.
    changed_by_admin_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admins.id", ondelete="SET NULL"), index=False
    )
    # سطر تليفون الأدمن وقت التغيير. denormalized عن قصد: لو الحساب
    # اتمسح بعدين، السجل يفضل يقول مين اللي كان شغال وقتها.
    changed_by_username: Mapped[Optional[str]] = mapped_column(String(80))
    note: Mapped[Optional[str]] = mapped_column(String(300))
    # True = صف مُختلق من الـbackfill، مش تغيّر حقيقي اتسجل.
    synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    order: Mapped["Order"] = relationship(back_populates="status_history")
    changed_by_admin: Mapped[Optional["Admin"]] = relationship(foreign_keys=[changed_by_admin_id])

    # الفهارس مصرّح بيها بالاسم المطابق للـmigration. قراءة خط
    # حالة طلب واحد زمنياً = index scan على `(order_id, changed_at)`،
    # مش sort على كل السطور. والاثنين دول اللي بيستعملهم
    # `app.order_tracking` فعلياً.
    __table_args__ = (
        Index("ix_order_status_history_admin_id", "changed_by_admin_id"),
        Index("ix_order_status_history_order_changed", "order_id", "changed_at"),
    )


class PushNotification(Base):
    """سجل إشعار اتبعت — Phase 3.

    ## الفرق بين السجل ده وبين الاشتراك

    `push_subscriptions` بيقول **مين مشترك**. `push_notifications` بيقول
    **إيه اللي اتبعت لمين، ونت إيه**. ده اللي بيخلّي:
    - العميل يشوف إشعاراته الأخيرة جوّه الموقع من غير ما يكون الـSW شغّال.
    - الأدمن يعرف طلب اتسجل عليه إشعار أصلاً ولا لأ (مفيد في support).
    - احنا نعرف هل القناة شغّالة أصلاً ولا المزوّد بيرفض.

    ## ليه مش بنخزّن الـpayload كامل

    بنخزّن `title` و`body` و`url` كأعمدة. الـpayload الموقّع نفسه مش
    بنخزّنه — هو بياناتmomentary، والسجل ده بيبقى أطول من عمر الإشعار.
    """

    __tablename__ = "push_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    # audience بيفرّق بين إشعار الأدمن وإشعار العميل. `order_status` و
    # `new_order` قيمتين بس في المرحلة دي.
    audience: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(300), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(300))
    # `sent` / `failed`. `sent` = المزوّد رد 200-299. `failed` = رد خطأ
    # أو استثناء. `skipped` = اشتراك مش active اتخطّى من غير محاولة.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="sent", index=True)
    # رقم HTTP اللي رجع من المزوّد، أو None لو مفيش رد. مفيش stack trace:
    # الـlog هو المكان الصح للتفاصيل.
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    subscription: Mapped[Optional["PushSubscription"]] = relationship(
        back_populates="notifications"
    )
    order: Mapped[Optional["Order"]] = relationship(foreign_keys=[order_id])


class OrderPushSubscription(Base):
    """ربط طلب باشتراك — عشان المرحلة الجاية تعرف تبعت لكل من طلبه.

    كـaudit trail للاستخدام: مين كان مطلوب إشعاره لطلب معيّن.
    """

    __tablename__ = "order_push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order: Mapped["Order"] = relationship(back_populates="push_links")
    subscription: Mapped["PushSubscription"] = relationship(back_populates="order_links")

