from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import push
from app.database import get_db
from app.models import (
    Branch,
    Category,
    Coupon,
    Customer,
    Order,
    OrderItem,
    OrderItemAddon,
    OrderItemOption,
    PaymentMethod,
    Product,
    ProductOptionGroup,
    PushSubscription,
    RestaurantSettings,
)
from app.order_tracking import (
    build_tracked_order,
    find_order_by_token,
)
from app.ordering import compute_pricing, resolve_payment_method, validate_scheduled_time
from app.schemas import (
    AddonOut,
    BranchOut,
    OrderChannel,
    OrderCreate,
    OrderCreated,
    OrderStatus,
    OrderingStatusOut,
    PaymentStatus,
    ProductDetailOut,
)
from app.utils import (
    PHONE_INVALID_MESSAGE,
    build_search_variants,
    build_tracking_url,
    build_whatsapp_message,
    build_whatsapp_url,
    is_within_ordering_hours,
    new_tracking_credentials,
    normalize_phone,
    ordering_hours_message,
    parse_theme_colors,
)

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["theme_colors"] = parse_theme_colors

logger = logging.getLogger(__name__)


def get_settings(db: Session) -> RestaurantSettings:
    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def default_branch(db: Session) -> Optional[Branch]:
    """الفرع الافتراضي — اللي بيتستخدم لو العميل مابعتش فرع."""
    return db.scalar(
        select(Branch).where(Branch.is_default.is_(True)).order_by(Branch.id).limit(1)
    )


def resolve_branch(db: Session, branch_id: Optional[int]) -> Branch:
    """ترجّع فرع صالح للطلب ده، أو ترفع 400.

    - عميل بعت فرع: لازم يكون موجود **و** مفعّل. الفرع المعطّل مرفوض
      عشان ما ينفعش حد يطلب من فرع مقفول بالغلط.
    - عميل مابعتش فرع (عميل قديم): بنستخدم الفرع الافتراضي. العمود
      `orders.branch_id` NOT NULL في الداتابيز، فمفيش طلب بيتحفظ من غير
      فرع مهما كان العميل قديم.
    """
    if branch_id is not None:
        branch = db.get(Branch, branch_id)
        if branch is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "الفرع المختار مش موجود")
        if not branch.is_active:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "الفرع المختار مش متاح للطلبات حاليًا"
            )
        return branch
    branch = default_branch(db)
    if branch is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "مفيش فرع متاح للطلبات حاليًا")
    return branch


@router.get("/api/branches", response_model=List[BranchOut])
def list_branches(db: Session = Depends(get_db)) -> List[BranchOut]:
    """الفروع المتاحة للعميل.

    الفروع غير المفعّلة مش بتظهر خالص — العميل مش المفروض يشوف فرع
    مقفول أصلاً، وكمان `resolve_order` هيرفضه لو حد بعت الـid يدوي.
    """
    branches = db.scalars(
        select(Branch)
        .where(Branch.is_active.is_(True))
        .order_by(Branch.is_default.desc(), Branch.name)
    ).all()
    return [BranchOut.model_validate(branch) for branch in branches]


def resolve_requested_payment_method(db: Session, payload: OrderCreate) -> PaymentMethod:
    """تحدّد طريقة الدفع من الطلب، مع دعم الشكل القديم بتاع V1.

    - `payment_method_id` (الجديد): بيتحقق منه إنه موجود **و** مفعّل. رقم
      من المتصفح مشيثق منه، فلازم نرفض أي method معطّلة.
    - `payment_method` (قديم، نص زي "cash"): بيتحول لـrow عن طريق `code`
      عشان ماحدش من عملاء V1 يbreaking. لو مش موجودة أو معطّلة → 400
      صريح، مش fallback صامت لـcash: العميل اختار طريقة ولاقى إنه
      اتسجّلت في حاجة تانية، وده أسوأ من رفض الطلب.
    """
    if payload.payment_method_id is not None:
        return resolve_payment_method(db, payload.payment_method_id)

    legacy_code = (payload.payment_method or "cash").strip()
    method = db.scalar(select(PaymentMethod).where(PaymentMethod.code == legacy_code))
    if method is None or not method.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "طريقة الدفع المحددة غير متاحة"
        )
    return method


@router.get("/order/success", response_class=HTMLResponse)
def order_success(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """صفحة تأكيد الطلب (طلب `system`).

    بتطلب `token` مش `order_id`. الشكل القديم كان بياخد رقم الطلب في
    الـquery، وأي حد يخمّن الأرقام (12 طلب في داتابيز التطوير) كان
    بيفتح تأكيد أي طلب. رقم الطلب هنا **معلومة العرض** بس بعد ما
    التحقق بالهاش نجح — يعني مش وسيلة التفويض.

    التوكن غلط بيرجّع نفس 404 بتاع صفحة التتبّع.
    """
    order = find_order_by_token(db, token)
    if order is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "الطلب مش موجود أو الرابط غير صحيح"
        )
    return templates.TemplateResponse(
        "public/order_success.html",
        {
            "request": request,
            "order_id": order.id,
            "tracking_url": build_tracking_url(token),
            "settings": get_settings(db),
        },
    )


# ============================================================
# Phase 2 — تتبّع الطلب وسجل طلبات العميل
# ============================================================


@router.get("/track", response_class=HTMLResponse)
def track_order_page(
    request: Request,
    token: str = "",
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """صفحة تتبّع الطلب.

    التوكن جاي في query string عشان يبقى قابل للمشاركة (العميل يبعت
    اللينك لصاحبه). مش في path: path بتتسجّل في access logs السيرفر
    بشكل طبيعي، والـquery في الب-forms أقل. الخيار الحقيقي security-wise
    هو إن التتبع behind token 256-بت أساساً، فمكان التوكن في الـURL
    مش هو العامل الحاسم.

    طلب بـtoken مش صحيح بيرجّع **نفس** 404 بتاع طلب مش موجود: مفيش
    فايدة نخبر حد إن الـid ده موجود بس مش ملكه.
    """
    order = find_order_by_token(db, token)
    if order is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "الطلب مش موجود أو الرابط غير صحيح"
        )
    restaurant_settings = get_settings(db)
    return templates.TemplateResponse(
        "public/track_order.html",
        {
            "request": request,
            "token": token,
            "order": build_tracked_order(order, restaurant_settings.currency, db=db),
            "settings": restaurant_settings,
        },
    )


@router.get("/orders/history", response_class=HTMLResponse)
def order_history_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """صفحة سجل طلبات العميل.

    الصفحة **فاضية من ناحية السيرفر**: مفيش طلبات بتترجّع. العميل بيقرا
    التوكنات من `localStorage` ويطلب كل طلب لوحده.

    ليه: السجل keyed by token، والـtokens عند العميل. لو رجعنا كل
    الطلبات من السيرفر، هنكون محتاجين-account أو بريد — أي منهم
    بيكسر "بلا تسجيل دخول". ولو رجعناها بـtoken من الـURL، يبقى أي حد
    عنده توكن واحد بيشوف كل طلبات العميل.
    """
    return templates.TemplateResponse(
        "public/order_history.html",
        {"request": request, "settings": get_settings(db)},
    )


@router.get("/api/ordering-status", response_model=OrderingStatusOut)
def ordering_status(db: Session = Depends(get_db)) -> OrderingStatusOut:
    """هل استقبال الطلبات مفتوح دلوقتي؟

    الساعات دي globals على مستوى المطعم كله مش على مستوى الفرع. العميل
    يقدر يتفرج على الموقع وهو مقفول، بس مش يبعت طلب.
    """
    restaurant_settings = get_settings(db)
    open_time = restaurant_settings.ordering_open_time
    close_time = restaurant_settings.ordering_close_time
    is_open = is_within_ordering_hours(open_time, close_time)
    overnight = bool(
        open_time is not None
        and close_time is not None
        and open_time != close_time
        and open_time > close_time
    )
    return OrderingStatusOut(
        is_open=is_open,
        open_time=open_time.strftime("%H:%M") if open_time else None,
        close_time=close_time.strftime("%H:%M") if close_time else None,
        overnight=overnight,
        message=ordering_hours_message(open_time, close_time),
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    categories = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .options(selectinload(Category.products))
        .order_by(Category.sort_order, Category.name)
    ).all()
    products = db.scalars(
        select(Product)
        .where(Product.is_active.is_(True))
        .options(selectinload(Product.options), selectinload(Product.option_groups))
        .order_by(Product.sort_order, Product.name)
    ).all()
    return templates.TemplateResponse(
        "public/home.html",
        {"request": request, "settings": restaurant_settings, "categories": categories, "products": products},
    )


@router.get("/manifest.json")
def get_manifest(db: Session = Depends(get_db)) -> dict:
    """ملف PWA.

    `pwa_name` / `pwa_short_name` / `pwa_icon_url` حقول اختيارية في
    `restaurant_settings`: لو الأدمن سيبها فاضية، الـmanifest بيرجع
    لاسم المطعم زي V1 بالظبط. دي موجودة من قبل Phase 2 في داتابيز
    التطوير والـmigration بتتبناها.

    **الاسم لـ`short_name` بيتقصّ على 12 محرف** زي ما المتصفح بيتوقع
    (الحد في شرط قابلية التثبيت) — الاسم الحقيقي على الشاشة الرئيسية
    بيتم قطعه من المتصفح أصلاً، فبنقصّه إحساسًا متسق مع اللي
    المستخدم بيشوفه.
    """
    settings = get_settings(db)
    theme = parse_theme_colors(settings)

    # الأولوية: حقل PWA المخصّص، وبعدين `app_icon_url` (اللي كان
    # بيرجّع لـicons)، وبعدين الأيقونات الثابتة في `/static`.
    icon_url = settings.pwa_icon_url or settings.app_icon_url
    if icon_url:
        icons = [
            {"src": icon_url, "sizes": "192x192", "type": "image/png"},
            {"src": icon_url, "sizes": "512x512", "type": "image/png"},
        ]
    else:
        icons = [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ]

    short_name = (
        settings.pwa_short_name
        or settings.restaurant_name
        or "Restaurant"
    )[:12]

    return {
        "name": settings.pwa_name or settings.restaurant_name or "Restaurant Ordering",
        "short_name": short_name,
        "description": settings.description or "نظام طلب إلكتروني للمطعم",
        "start_url": "/",
        "scope": "/",
        "lang": "ar",
        "dir": "rtl",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": theme["primary"],
        "orientation": "portrait",
        "icons": icons,
    }


@router.get("/search", response_class=HTMLResponse)
def search_products(request: Request, q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    statement = select(Product).where(Product.is_active.is_(True))
    query = (q or "").strip()

    if query:
        terms = build_search_variants(query)
        if terms:
            conditions = [Product.name.ilike(f"%{term}%") for term in terms]
            statement = statement.where(or_(*conditions))
        else:
            statement = statement.where(Product.name.ilike(f"%{query}%"))
    else:
        statement = statement.order_by(Product.sort_order, Product.name)

    products = db.scalars(
        statement.options(selectinload(Product.options), selectinload(Product.option_groups)).order_by(
            Product.sort_order, Product.name
        )
    ).all()
    return templates.TemplateResponse(
        "public/partials/product_grid.html",
        {"request": request, "settings": restaurant_settings, "products": products},
    )


@router.get("/products/{product_slug}", response_class=HTMLResponse)
def product_detail(request: Request, product_slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    product = db.scalar(
        select(Product)
        .where(Product.slug == product_slug, Product.is_active.is_(True), Product.is_available.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.addons),
            selectinload(Product.options),
            selectinload(Product.option_groups).selectinload(ProductOptionGroup.options),
        )
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    active_addons = sorted(
        (a for a in product.addons if a.is_active), key=lambda a: (a.sort_order, a.name)
    )
    product_data = ProductDetailOut.model_validate(product).model_dump(mode="json")
    addon_data = [AddonOut.model_validate(a).model_dump(mode="json") for a in active_addons]

    # مجموعات الخيارات المتاحة فقط، مع خياراتها المتاحة ومرتبة.
    option_groups_data = []
    for group in sorted((g for g in product.option_groups if g.is_available), key=lambda g: (g.sort_order, g.name)):
        options = sorted((o for o in group.options if o.is_available), key=lambda o: (o.sort_order, o.name))
        option_groups_data.append(
            {
                "id": group.id,
                "name": group.name,
                "is_required": group.is_required,
                "allow_multiple": group.allow_multiple,
                "min_selections": group.min_selections,
                "max_selections": group.max_selections,
                "options": [
                    {"id": o.id, "name": o.name, "price_adjustment": float(o.price_adjustment)}
                    for o in options
                ],
            }
        )

    return templates.TemplateResponse(
        "public/product_detail.html",
        {
            "request": request,
            "settings": restaurant_settings,
            "product": product_data,
            "addons": addon_data,
            "option_groups": option_groups_data,
        },
    )


# ---------------------------------------------------------------------------
# Phase 3 — إشعارات الطلب
# ---------------------------------------------------------------------------
#
# ## ليه `_safe_*` helpers منفصلة
#
# قاعدة المرحلة دي: **فشل الإشعار ما يسقطش الطلب ولا تغيير الحالة.**
# دي دوال مساندة بتبلع أي استثناء وترجّع — مش بس استثناءات `push`:
# لو `record_status_change` أو `_link_*` رمى استثناءً، الطلب يفضل
# محفوظ والعميل ياخد رد 200 بتاعه. الخطأ بيروح للوج مش للعميل،
# لأن العميل مش يقدر يعمل حاجة بيه.
#
# **`push.send_and_commit` نفسها كمان ما بترميش** (داخلها `try/except`
# على كل مستوى)، بس دي طبقة تانية: لو `db.execute` جواها رفض، أو
# `select` رجّع استثناء، أو أي bug في `app.push` نفسه، الـwrapper
# بياكله.
#
# # ليه SAVEPOINT مش `db.rollback()`
#
# `_safe_link_customer_subscriptions` و`_safe_record_status` بيشتغلوا
# **قبل** `db.commit()` في `create_order` — يعني الطلب لسه في الـsession
# وغير محفوظ. `db.rollback()` هنا بيرمي **الطلب كله** مع الشغل اللي
# فشل: العميل بياخد 500، ومفيش طلب في الداتابيز، ومفيش رسالة خطأ
# مفهومة. ده أسوأ من إن سجل الحالة ما يتكتبش أصلاً.
#
# التعليق القديم كان بيقول "هنعمل rollback أو commit بعده على أي حال
# وهذا كفاية" — الفكرة إن الـrollback الآتي بيرمّي الأول. صحيح، بس
# الثمن إن الطلب نفسه بيضيع. ده بالظبط اللي الاختبار
# `test_order_is_created_when_everything_push_raises` بيمسكه.
#
# `db.begin_nested()` بيصدر `SAVEPOINT` في Postgres. الـrollback
# بيرجع **للنقطة دي بس** — الشغل اللي قبلها (الطلب) بيفضل متأثر.
# ده اللي بنحتاجه: "اللي فشل يتشال، اللي نجح يكمّل".


def _run_in_savepoint(db: Session, label: str, order_id: Optional[int], fn) -> None:
    """ينفّذ `fn` داخل SAVEPOINT. أي استثناء بتبتلع بس ما بترميش.

    `fn` بتاخد `db`. لو رمت، بنعمل rollback للـSAVEPOINT بس. لو
    الـSAVEPOINT نفسه فشل إنشاؤه (مفيش transaction مفتوحة مثلاً)،
    بن fall back على `db.rollback()` العادي — أحسن من إن الـhelper
    يبقى هو نفسه مصدر 500.
    """
    savepoint = None
    try:
        savepoint = db.begin_nested()
    except Exception:
        logger.exception(
            "savepoint_begin_failed",
            extra={"event": "savepoint_begin_failed", "label": label, "order_id": order_id},
        )
    try:
        fn()
    except Exception:
        logger.exception(
            label,
            extra={"event": label, "order_id": order_id},
        )
        try:
            if savepoint is not None:
                savepoint.rollback()
            else:
                db.rollback()
        except Exception:
            logger.exception("%s_rollback_failed", label)
            try:
                db.rollback()
            except Exception:
                logger.exception("%s_rollback_retry_failed", label)
        return
    if savepoint is not None:
        try:
            savepoint.commit()
        except Exception:
            logger.exception(
                "%s_savepoint_commit_failed",
                label,
                extra={"event": "%s_savepoint_commit_failed" % label, "order_id": order_id},
            )


def _safe_link_customer_subscriptions(
    db: Session, order: Order, push_endpoint: Optional[str] = None
) -> None:
    """يربط **اشتراك المتصفح اللي بعت الطلب** (إن وُجد) بالعميل الجديد.

    العميل ممكن يكون اشترك في الإشعارات **قبل** ما يعمل أول طلب (الزر
    على الصفحة الرئيسية). الاشتراكات دي بتفضل `customer_id = NULL` لحد
    ما يعمل طلب، وعندها بنربطها هنا.

    **SEC-1 — ليه الربط بالـendpoint مش "كل حاجة مجهولة":** قبل الإصلاح،
    الدالة كانت بتحدد كل صف عنده `customer_id IS NULL AND admin_id IS NULL`،
    يعني أول عميل يعمل طلب كان **بيستولي على كل اشتراك مجهول من أي متصفح
    تاني** — فجهازه هو اللي كان بيوصله إشعارات طلبات ناس تانية. دلوقتي
    الربط مربوط **بالـendpoint اللي المتصفح اللي بيعمل الطلب بعته صراحةً**،
    فبنحدّث صف واحد بالكتير، وبشرط إنه لسه مش مربوط بعميل ولا بأدمن.

    **مفيش `push_endpoint` = مفيش ربط:** الاشتراك المجهول يفضل مجهول لحد
    ما صاحبه يفتح صفحة التتبّع ويتأكّد (مسار `api.push_subscribe` بالـ
    token)، وده هو المسار الشرعي الوحيد.
    """
    from sqlalchemy import update

    if not push_endpoint or order.customer_id is None:
        return

    def _work() -> None:
        result = db.execute(
            update(PushSubscription)
            .where(
                PushSubscription.endpoint == push_endpoint,
                PushSubscription.customer_id.is_(None),
                PushSubscription.admin_id.is_(None),
            )
            .values(customer_id=order.customer_id)
        )
        if result.rowcount:
            db.flush()
            logger.info(
                "push_subscriptions_linked_to_customer",
                extra={
                    "event": "push_subscriptions_linked_to_customer",
                    "order_id": order.id,
                    "customer_id": order.customer_id,
                    "count": result.rowcount,
                },
            )

    _run_in_savepoint(db, "push_subscription_link_failed", order.id, _work)



def _safe_record_status(db: Session, order: Order, status_value: str) -> None:
    """يسجّل سطر الحالة الأول — ما بيرميش.

    **نفس منطق الـSAVEPOINT:** لو `record_status_change` فشل، بنشيل
    الشغل اللي فشل بس. الـorder نفسه لسه في الـsession ومحفوظ
    زي ما هو. `db.rollback()` هنا كان بيرمي الطلب معاه.
    """
    _run_in_savepoint(
        db,
        "status_history_write_failed",
        order.id,
        lambda: push.record_status_change(db, order, status_value),
    )


def _safe_notify_admins(db: Session, order: Order) -> None:
    """إشعار "طلب جديد" لكل اشتراكات الأدمن النشطة — ما بيرميش.

    **بعد** الـcommit. `send_and_commit` بيعمل commit مستقل، فأي تعديل
    على جدول الاشتراكات بيتحفظ لوحده.
    """
    try:
        if not push.is_configured():
            return
        subscriptions = push.active_admin_subscriptions(db)
        if not subscriptions:
            return
        summary = push.send_and_commit(
            db,
            subscriptions,
            payload=push.new_order_payload(order),
            audience=push.AUDIENCE_ADMIN,
            order_id=order.id,
        )
        if summary.sent or summary.failed:
            logger.info(
                "new_order_push_sent",
                extra={
                    "event": "new_order_push_sent",
                    "order_id": order.id,
                    "total": summary.total,
                    "sent": summary.sent,
                    "failed": summary.failed,
                    "invalid": summary.invalid,
                },
            )
    except Exception:
        logger.exception(
            "new_order_push_failed",
            extra={"event": "new_order_push_failed", "order_id": order.id},
        )
        try:
            db.rollback()
        except Exception:
            logger.exception("new_order_push_rollback_failed")


@router.post("/orders", response_model=OrderCreated)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)) -> OrderCreated:
    restaurant_settings = get_settings(db)

    # 0) ساعات استقبال الطلبات — عامة على مستوى المطعم كله. العميل
    #    يقدر يتفرج على الموقع وهو مقفول، بس مش يقدر يبعت طلب.
    if not is_within_ordering_hours(
        restaurant_settings.ordering_open_time,
        restaurant_settings.ordering_close_time,
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            ordering_hours_message(
                restaurant_settings.ordering_open_time,
                restaurant_settings.ordering_close_time,
            )
            or "الطلب عبر الموقع مغلق دلوقتي.",
        )

    # 0.b) الفرع. العميل بيبعت فرع، ولو مبعتش (عميل قديم) بنرجعله
    #      الفرع الافتراضي. الفرع المعطّل أو المجهول مرفوض.
    branch = resolve_branch(db, payload.branch_id)

    # 1) موعد الطلب المجدول (استلام/توصيل) لازم يكون في المستقبل.
    scheduled_time = validate_scheduled_time(payload.pickup_time)

    # 1.b) طريقة الدفع. العميل بيبعت `payment_method_id` (الطريقة الجديدة)،
    #      والعميل القديم بيبعت `payment_method` كنص. الأول بيتحوّل للتاني
    #      عن طريق `code` عشان ميكسرش أي عميل V1 لسه شغال.
    payment_method = resolve_requested_payment_method(db, payload)

    # 1.c) رمز التتبّع: 32 بايت عشوائي، هنخزّن الـhash بتاعه ونرجّع
    #      الـtoken نفسه للعميل مرة واحدة. الـtoken مش بيتخزّن في الداتابيز
    #      في أي مكان — لو الداتابيز اتسرّبت، مفيش روابط تتبّع شغّالة.
    tracking_token, tracking_hash = new_tracking_credentials()

    # 2) كل الحسابات تتعاد من Database — لا ثقة في أي سعر قادم من العميل.
    pricing = compute_pricing(
        db,
        payload.items,
        delivery_option_id=payload.delivery_option_id,
        delivery_zone_id=payload.delivery_zone_id,
        coupon_code=payload.coupon_code,
        customer_phone=payload.customer_phone,
    )

    # 3) العنوان مطلوب للتوصيل فقط — الاستلام من المطعم لا يحتاج عنوان.
    address = (payload.customer_address or "").strip() or None
    delivery_option = pricing.delivery_option
    if delivery_option is not None and delivery_option.delivery_type != "pickup" and not address:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "العنوان مطلوب لطلبات التوصيل")

    # 4) بناء الطلب مع حفظ لقطة الأسعار والتفصيلة الكاملة وقت الإنشاء.
    #
    #    لقطات الطقس/الدفع/التوصيل كلها بتتخزّن **وقت الطلب** عن قصد:
    #    لو الأدمن غيّر اسم طريقة الدفع أو اسم الفرع أو رسوم المنطقة بعد
    #    كده، الطلب القديم لازم يفضل يعرض اللي العميل شافه فعلاً، مش
    #    الحالة الحالية. من غير ده، تاريخ الفواتير بيتغير بأوامر لاحقة.
    order = Order(
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_address=address or "",
        notes=payload.notes,
        status=OrderStatus.pending.value,
        subtotal=pricing.subtotal,
        delivery_fee=pricing.delivery_fee,
        discount_amount=pricing.discount_amount,
        total=pricing.total,
        whatsapp_message="",
        branch_id=branch.id,
        branch_name_snapshot=branch.name,
        channel=payload.channel.value,
        delivery_option_id=delivery_option.id if delivery_option else None,
        delivery_zone_id=pricing.delivery_zone.id if pricing.delivery_zone else None,
        coupon_id=pricing.coupon.id if pricing.coupon else None,
        pickup_time=scheduled_time,
        # `payment_method` القديمة بتفضل فاضية دلوقتي: `payment_method_id`
        # بقى المصدر. بس بنخليها تنكتب عشان أي كود قديم لسه بيقراها.
        payment_method=payment_method.code,
        payment_method_id=payment_method.id,
        payment_method_code=payment_method.code,
        payment_method_label=payment_method.name,
        payment_instructions=payment_method.description,
        payment_status=PaymentStatus.pending.value,
        delivery_fee_pending=pricing.delivery_fee_pending,
        delivery_method_label=delivery_option.name if delivery_option else None,
        delivery_zone_label=pricing.delivery_zone.name if pricing.delivery_zone else None,
        delivery_estimated_time=delivery_option.estimated_time if delivery_option else None,
        delivery_type_snapshot=delivery_option.delivery_type if delivery_option else None,
    )
    order.order_tracking_hash = tracking_hash

    message_items: List[dict] = []
    for line in pricing.lines:
        order_item = OrderItem(
            product_id=line.product.id,
            product_name=line.display_name,
            unit_price=line.unit_price,
            quantity=line.quantity,
            line_total=line.line_total,
            item_note=line.item_note,
        )
        for addon in line.addons:
            order_item.addons.append(
                OrderItemAddon(addon_id=addon.id, name=addon.name, price=addon.price)
            )
        for option in line.options:
            order_item.options.append(
                OrderItemOption(
                    option_id=option.id,
                    group_name=option.group.name if option.group is not None else "",
                    name=option.name,
                    price_adjustment=option.price_adjustment,
                )
            )
        order.items.append(order_item)

        message_items.append(
            {
                "name": line.product.name,
                "quantity": line.quantity,
                "line_total": line.line_total,
                "options": [(o.name, o.price_adjustment) for o in line.options],
                "addons": [(a.name, a.price) for a in line.addons],
                "note": line.item_note,
            }
        )

    # 5) تجميع الطلبات تحت عميل واحد حسب الرقم الموحد.
    normalized_phone = normalize_phone(payload.customer_phone)
    customer = db.scalar(select(Customer).where(Customer.phone == normalized_phone)) if normalized_phone else None
    now = order.created_at or datetime.now(timezone.utc)
    if customer is None and normalized_phone:
        customer = Customer(phone=normalized_phone, name=payload.customer_name.strip(), first_order_at=now, last_order_at=now)
        db.add(customer)
        try:
            db.flush()
        except IntegrityError:
            db.expire_all()
            customer = db.scalar(select(Customer).where(Customer.phone == normalized_phone))
            if customer is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "العميل غير موجود")
    elif customer is not None:
        customer.name = payload.customer_name.strip() or customer.name
        customer.last_order_at = now
    order.customer_id = customer.id if customer else None

    # 6) منع race condition: التحقق من كوبون first-order-only مع قفل العهدة.
    if pricing.coupon is not None and pricing.coupon.first_order_only:
        # العميل لازم يكون اتسجل في الخطوة 5. التليفون بيتحقق في الـschema،
        # فالمفروض عمره ما يوصل هنا فاضي — لكن الحماية بتمنع أي 500 مستقبلي.
        if customer is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, PHONE_INVALID_MESSAGE)
        # قفل صف العميل عشان كوبون first-order-only ما يتسابّش مرتين
        db.scalar(select(Customer).where(Customer.id == customer.id).with_for_update())
        # قفل صف الكوبون نفس السبب
        db.scalar(select(Coupon).where(Coupon.id == pricing.coupon.id).with_for_update())
        from sqlalchemy import func as sa_func
        db_digits = sa_func.regexp_replace(
            sa_func.regexp_replace(
                sa_func.regexp_replace(Order.customer_phone, r"\D", "", "g"),
                r"^0", "",
            ),
            r"^20", "",
        )
        existing_order = db.scalar(
            select(Order.id).where(
                db_digits == normalized_phone,
                Order.status != "cancelled",
            )
        )
        if existing_order is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "هذا الكوبون للطلب الأول فقط — لديك طلب سابق")

    # 7) خصم استخدام الكوبون بعد نجاح الحساب.
    if pricing.coupon is not None:
        pricing.coupon.usage_count += 1
        db.add(pricing.coupon)

    # 8) حفظ الطلب أولًا لمعرفة رقمه، ثم بناء رسالة WhatsApp برقم الطلب.
    #    الـtracking token اتولّد **قبل** الكتابة (خط 4) وربط بالهاش، لأن
    #    الـtoken نفسه بيرجع للعميل مرة واحدة في الرد. لو اتولّد بعد commit
    #    كان فيه نافذة طلب اتحفظ من غير هاش — طلب غير قابل للتتبع للأبد.
    db.add(order)
    db.flush()

    # الرسالة بتتبني في الحالتين عشان تفضل لقطة مرجعية في السجل بتاع
    # الطلب، زي ما كانت في V1 بالظبط. الفرق الوحيد إن طلب `system` مش
    # بيرجّع رابط واتساب للعميل.
    message = build_whatsapp_message(
        restaurant_settings,
        order.id,
        payload.customer_name,
        payload.customer_phone,
        address,
        message_items,
        subtotal=pricing.subtotal,
        discount_amount=pricing.discount_amount,
        delivery_fee=pricing.delivery_fee,
        delivery_name=(f"{delivery_option.name} - {pricing.delivery_zone.name}" if delivery_option and pricing.delivery_zone else delivery_option.name if delivery_option else None),
        delivery_type=delivery_option.delivery_type if delivery_option else "fixed",
        total=pricing.total,
        notes=payload.notes,
        pickup_time=scheduled_time,
        coupon_code=pricing.coupon_code,
        # هنا بنبعت **الاسم** مش الـcode: رسالة واتساب بتتقري من موظف،
        # و"كاش" أوضح من "cash" ليه.
        payment_method=payment_method.name,
    )
    order.whatsapp_message = message

    # 9) التحويل لواتساب بيحصل **بعد** ما الطلب اتحفظ في الداتابيز. واتساب
    #    قناة تواصل مش مصدر حقيقة، فالطلب لازم يكون موجود قبل أي رسالة.
    is_whatsapp = payload.channel == OrderChannel.whatsapp

    # 10) سطر الحالة الأولى + ربط اشتراك متصفح الطلب **ذاته** بالعميل —
    #     قبل الـcommit عشان السطر والطلب يتحفظوا في معاملة واحدة.
    #
    #     `_safe_*` helpers دي **ما بيرميوش استثناءات أبداً**: فشلت هنا
    #     معناه الطلب اتسجل من غير سجل حالة أو من غير ربط اشتراك، ومش
    #     معناه إن العميل خسر طلبه. الربط نفسه بيشتغل بس مع الـ
    #     `push_endpoint` اللي المتصفح بعته (SEC-1 — مش كل الاشتراكات
    #     المجهولة).
    _safe_link_customer_subscriptions(db, order, payload.push_endpoint)
    _safe_record_status(db, order, order.status)

    db.commit()
    db.refresh(order)

    # 11) إشعار "طلب جديد" للأدمن — **بعد** الـcommit.
    #
    #     الترتيب ده مقصود: الإرسال بيبعت على الشبكة وبيعمل commit
    #     خاص بيه. لو الإرسال جا قبل الـcommit الأول، أي خطأ كان هيعمل
    #     rollback للطلب نفسه — والطلب **اتحفظ**.
    _safe_notify_admins(db, order)

    return OrderCreated(
        order_id=order.id,
        status=order.status,
        subtotal=order.subtotal,
        discount_amount=order.discount_amount,
        delivery_fee=order.delivery_fee,
        total=order.total,
        currency=restaurant_settings.currency,
        branch_id=order.branch_id,
        channel=order.channel,
        whatsapp_url=(
            build_whatsapp_url(restaurant_settings.whatsapp_number, message)
            if is_whatsapp
            else None
        ),
        tracking_token=tracking_token,
        tracking_url=build_tracking_url(tracking_token),
        payment_method_code=order.payment_method_code,
        payment_method_label=order.payment_method_label,
        payment_status=order.payment_status,
        delivery_fee_pending=order.delivery_fee_pending,
    )
