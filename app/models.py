from __future__ import annotations

from datetime import datetime
from datetime import time as dt_time
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
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


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120), default="Admin")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(120))
    first_order_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_order_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    orders: Mapped[List["Order"]] = relationship(back_populates="customer")
    push_subscriptions: Mapped[List["PushSubscription"]] = relationship(back_populates="customer")


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
    """اشتراك Web Push لجهاز/متصفح عميل — أساس المرحلة الجاية فقط."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # `unique=True, index=True` = فهرس فريد واحد اسمه
    # `ix_push_subscriptions_endpoint`. الـendpoint هو مفتاح device
    # فعليًا: نفس المتصفح بيرسل نفس الـendpoint في كل تسجيل، ولو سمحنا
    # بتكراره هنجمع صفوف بتاعت نفس الجهاز ونبعت له الإشعار مرتين.
    # الفهرس الفريد بيفرض `upsert` بـON CONFLICT — واليوم مفيش
    # endpoints أصلاً، بس لازم الـconstraint يبقى موجود من دلوقتي قبل
    # ما الكود يتكتب.
    endpoint: Mapped[str] = mapped_column(String(600), nullable=False, unique=True, index=True)
    p256dh: Mapped[str] = mapped_column(String(300), nullable=False)
    auth: Mapped[str] = mapped_column(String(200), nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(400))
    device: Mapped[Optional[str]] = mapped_column(String(40))
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Optional["Customer"]] = relationship(back_populates="push_subscriptions")
    order_links: Mapped[List["OrderPushSubscription"]] = relationship(
        back_populates="subscription", cascade="all, delete-orphan"
    )


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

