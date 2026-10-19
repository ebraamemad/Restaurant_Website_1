from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
from datetime import datetime, time as dt_time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from slugify import slugify

from app.models import RestaurantSettings

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_SIZE = 3 * 1024 * 1024

# كود الاتصال المصري من غير `+` — نفس قيمة `DIALABLE_PREFIX` في
# `app.whatsapp.service`. بنكرّرها هنا مش بنستوردها: حزمة `app.whatsapp`
# بتستورد `app.marketing` اللي بيستورد الملف ده، فاستيرادنا ليها هيعمل
# circular import من غير أي داعي.
EGYPT_DIALABLE_PREFIX = "20"

PAYMENT_METHOD_LABELS = {
    "cash": "كاش",
    "ewallet": "محفظة إلكترونية",
    "card": "فيزا",
}

# ---------------------------------------------------------------------------
# Phase 2: تتبّع الطلب بدون تسجيل دخول
# ---------------------------------------------------------------------------
# الفكرة كلها في سطرين: بنولّد token عشوائي 32 بايت، نرجعه للعميل **مرة
# واحدة** وقت إنشاء الطلب، ونخزّن SHA-256 بتاعه في الداتابيز. العميل
# بيحفظ الـtoken في المتصفح، ولما يفتح صفحة التتبع بنعمل lookup بالهاش.
#
# ليه بالظبط كده:
# 1. مفيش login في V2، فمفيش فايدة من search by phone: الرقم متاح في
#    نتائج محرك البحث وموجود في أخطاء الويب، والعميل غالباً بيدي رقم
#    تاني. البحث بالرقم كمان بيخلّي أي حد يعرف رقم تاني يقدر يشوف طلبه.
# 2. تخزين الهاش مش الـtoken: لو الداتابيز اتسرّبت، الـhashes مالهاش
#    فايدة عملية — الرموز نفسها مش موجودة. عكس لو خزّنا الـtoken مباشرة
#    كان أي dump = كل روابط التتبع شغّالة.
# 3. 32 بايت random مش قابل للتخمين: 2^256 احتمال.
#
# Constant-time compare مش مطلوب هنا: هنقارن SQL equality على hex digest
# ثابت الطول، مش في كود تفاضلي على سر.
TRACKING_TOKEN_BYTES = 32


def build_tracking_url(token: str) -> str:
    """رابط صفحة تتبّع الطلب للعميل.

    نسبي مش مطلق بالشكل ده: الجافاسكريبت في `cart.js` بيبنيه بنفسه من
    `location.origin`، والكود ده بيستخدمه كـfallback أو في رسالة واتساب
    على سيرفر مش ورا نفس الـdomain. أول ما Bearer/caching يبقى مطلوب
    (إحنا مش محتاجينه دلوقتي)، الشكل ده هو المكان الطبيعي يتغيّر فيه.
    """
    return f"/track?token={quote(token, safe='')}"


def generate_tracking_token() -> str:
    """token عشوائي آمن للتتبع (hex). يُرجع مرة واحدة للعميل ولا يُخزّن."""
    return secrets.token_hex(TRACKING_TOKEN_BYTES)


def hash_tracking_token(token: str) -> str:
    """SHA-256 hex للـtoken — هذا اللي بيتخزّن في `orders.order_tracking_hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_tracking_credentials() -> tuple[str, str]:
    """ترجع `(token, hash)`. الـtoken يروح للعميل، والـhash للداتابيز."""
    token = generate_tracking_token()
    return token, hash_tracking_token(token)


def make_slug(value: str, fallback: str = "item") -> str:
    slug = slugify(value, lowercase=True, separator="-")
    return slug or fallback


def money(value: Decimal) -> str:
    return f"{value:.2f}"


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", normalized)
    return " ".join(normalized.split())


def build_search_variants(value: str) -> List[str]:
    raw = (value or "").strip()
    if not raw:
        return []

    variants = {raw, normalize_search_text(raw)}
    cleaned = normalize_search_text(raw)
    if cleaned:
        variants.add(cleaned.replace(" ", ""))
        variants.add(cleaned.replace("ة", "ه"))
        variants.add(cleaned.replace("ي", "ى"))
        variants.add(cleaned.replace("ى", "ي"))

    return [variant for variant in variants if variant]


THEME_DEFAULTS: Dict[str, str] = {
    "primary": "#059669",
    "secondary": "#047857",
    "accent": "#f59e0b",
}
HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex_color(value: Optional[str]) -> Optional[str]:
    """يقبل #rgb أو #rrggbb ويعيدها بصيغة #rrggbb صغيرة، أو None لو غير صالحة."""
    color = (value or "").strip().lower()
    if not HEX_COLOR_RE.match(color):
        return None
    if len(color) == 4:
        color = "#" + "".join(ch * 2 for ch in color[1:])
    return color


def parse_theme_colors(settings: Optional[RestaurantSettings]) -> Dict[str, str]:
    """ألوان واجهة العميل من settings.theme_colors مع الوقوع على الافتراضي."""
    colors = dict(THEME_DEFAULTS)
    raw = (getattr(settings, "theme_colors", None) or "").strip() if settings is not None else ""
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in THEME_DEFAULTS:
                normalized = normalize_hex_color(data.get(key))
                if normalized is not None:
                    colors[key] = normalized
    return colors


async def save_image(upload: UploadFile, folder: Path) -> str:
    extension = ALLOWED_IMAGE_TYPES.get(upload.content_type or "")
    if extension is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only JPG, PNG, and WEBP images are allowed")

    data = await upload.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Image size must be 3MB or less")

    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    path = folder / filename
    path.write_bytes(data)
    return f"/uploads/{folder.name}/{filename}"


def build_whatsapp_message(
    settings: RestaurantSettings,
    order_id: Optional[int],
    customer_name: str,
    customer_phone: str,
    customer_address: Optional[str],
    items: List[dict],
    subtotal: Decimal,
    discount_amount: Decimal,
    delivery_fee: Decimal,
    delivery_name: Optional[str],
    delivery_type: str,
    total: Decimal,
    notes: Optional[str],
    pickup_time: Optional[datetime] = None,
    coupon_code: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> str:
    """يبني رسالة واتساب منظمة. كل عنصر في items هو dict بالحقول:
    name, quantity, line_total, options [(name, price_adjustment)], addons [(name, price)], note.
    """
    lines = [f"طلب جديد من {settings.restaurant_name}"]
    if order_id is not None:
        lines.append(f"رقم الطلب: #{order_id}")
    lines.extend(["", f"الاسم: {customer_name}", f"الهاتف: {customer_phone}"])
    if customer_address:
        lines.append(f"العنوان: {customer_address}")
    if delivery_name:
        lines.append(f"طريقة الاستلام: {delivery_name}")
    else:
        lines.append("طريقة الاستلام: " + ("استلام من المطعم" if delivery_type == "pickup" else "توصيل"))
    if pickup_time is not None:
        # العرض بالتوقيت المحلي للمطعم — نفس التوقيت اللي بيرجع من قاعدة البيانات.
        lines.append(f"موعد الطلب: {ensure_aware(pickup_time).astimezone().strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append("موعد الطلب: في أقرب وقت")
    if payment_method:
        lines.append(f"طريقة الدفع: {PAYMENT_METHOD_LABELS.get(payment_method, payment_method)}")

    lines.extend(["", "المنتجات:"])
    for item in items:
        lines.append(f"- {item['name']} × {item['quantity']} = {money(item['line_total'])} {settings.currency}")
        options = item.get("options") or []
        if options:
            opts = "، ".join(f"{name} (+{money(adj)})" for name, adj in options)
            lines.append(f"  الخيارات: {opts}")
        addons = item.get("addons") or []
        if addons:
            adds = "، ".join(f"{name} (+{money(price)})" for name, price in addons)
            lines.append(f"  الإضافات: {adds}")
        if item.get("note"):
            lines.append(f"  ملاحظة: {item['note']}")

    lines.extend(
        [
            "",
            f"الإجمالي الفرعي: {money(subtotal)} {settings.currency}",
        ]
    )
    if discount_amount > 0:
        suffix = f" (كوبون: {coupon_code})" if coupon_code else ""
        lines.append(f"الخصم: -{money(discount_amount)} {settings.currency}{suffix}")
    if delivery_fee > 0:
        lines.append(f"رسوم التوصيل: {money(delivery_fee)} {settings.currency}")
    lines.append(f"الإجمالي: {money(total)} {settings.currency}")
    if notes:
        lines.extend(["", f"ملاحظات: {notes}"])
    return "\n".join(lines)


def build_whatsapp_url(phone_number: str, message: str) -> str:
    """رابط click-to-chat لرقم **المطعم** (مش العميل).

    الشكل النهائي لازم يكون `wa.me/<E.164 من غير +>`. لو الرقم متخزن
    بالشكل المحلي (`01012345678`) وسِبناه زي ما هو، `wa.me` بيفتح شات
    **غير صالح** والعميل مايقدرش يبعت طلبه خالص — وهو ده شكل الرقم اللي
    نموذج الإعدادات (و`validate_customer_phone`) بيقبله عادي، فالحالة دي
    متجاهشة مش حالة نادرة.

    `+` / `00` زي ما همّا (أصل ده أصل رقم دولي مكتوب صراحة)، والباقي
    بنوحده بـ`normalize_phone` + كود مصر بس لو فعلاً موبايل مصري — أي
    رقم تاني بيروح زي ما هو من غير تخمين.
    """
    cleaned = (phone_number or "").strip()
    if not cleaned:
        return ""

    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return ""

    if cleaned.startswith("+"):
        normalized_phone = f"+{digits}"
    elif cleaned.startswith("00"):
        normalized_phone = f"+{digits[2:]}"
    else:
        national = normalize_phone(digits)
        if is_valid_egypt_phone(national):
            normalized_phone = f"{EGYPT_DIALABLE_PREFIX}{national}"
        else:
            normalized_phone = digits

    return f"https://wa.me/{quote(normalized_phone, safe='')}?text={quote(message, safe='')}"


def normalize_phone(phone_number: str) -> str:
    """توحيد الرقم للتجميع ومنع تكرار العميل.

    Egyptian numbers (local 01x or international +20/0020) both normalize to the
    same 10-digit national format without leading zero (e.g., '1012345678').
    This ensures consistent customer deduplication regardless of input format.

    ملاحظة: بترجع نص فاضي لو المدخل مالوش أرقام — المتصلين لازم يتأكدوا
    من صحة الرقم قبل ما يستخدموا النتيجة (راجع validate_customer_phone).
    """
    digits = re.sub(r"\D", "", phone_number or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("20") and len(digits) > 10:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


# بادئات الموبايل المصري بعد التوحيد (10 أرقام): 010 / 011 / 012 / 015
EGYPT_MOBILE_PREFIXES = frozenset({"10", "11", "12", "15"})

PHONE_INVALID_MESSAGE = "رقم الهاتف غير صحيح — من فضلك اكتب رقم موبايل مصري صحيح (مثال: 01012345678)"

# أطول نص مقبول قبل التنظيف: أطول صيغة مدعومة هي 0020 + 10 أرقام = 14 رقم،
# والباقي مسافات أو رموز. الحد ده بيمنه Denial of Service على الـregex.
PHONE_MAX_INPUT_LENGTH = 30


def is_valid_egypt_phone(normalized: str) -> bool:
    """هل الرقم بعد التوحيد رقم موبايل مصري صالح؟ (10 أرقام، بادئة معروفة)"""
    return len(normalized) == 10 and normalized.isdigit() and normalized[:2] in EGYPT_MOBILE_PREFIXES


def to_display_phone(normalized: str) -> str:
    """الشكل اللي بيتعرض للعميل والإدارة: 0 + الرقم الموحد (مثال: 01012345678).

    مهم: استعلامات الـfirst-order-only في SQL بتشيل الصفر الأول من
    orders.customer_phone، فتخزين الشكل ده بيفضل متوافق مع كل الصفوف القديمة.
    """
    return f"0{normalized}" if len(normalized) == 10 else normalized


def validate_customer_phone(raw: str) -> str:
    """تتحقق من رقم الموبايل المصري وترجعه في الشكل الموحد للعرض.

    بتقبل: 01012345678 / +201012345678 / 00201012345678 / 201012345678.

    بترمي ValueError برسالة عربية واضحة — وFastAPI بيحوّلها لـ422 بدل ما
    المدخل الغلط يوصل لوجيك الأعمال ويسبب 500.

    الأرقام اللي مش موبايل مصري (أرضي، أو أرقام ناقصة/غلط) بترفض هنا عمدًا،
    لأن رقم العميل لازم يقدر يستقبل رسالة واتساب.
    """
    if raw is None:
        raise ValueError(PHONE_INVALID_MESSAGE)
    if len(raw) > PHONE_MAX_INPUT_LENGTH:
        raise ValueError(PHONE_INVALID_MESSAGE)
    normalized = normalize_phone(raw)
    if not is_valid_egypt_phone(normalized):
        raise ValueError(PHONE_INVALID_MESSAGE)
    return to_display_phone(normalized)


def mask_phone(phone_number: Optional[str]) -> str:
    """تمويه الرقم قبل أي تسجيل في اللوجات — ما بنسجلش رقم عميل كامل."""
    digits = re.sub(r"\D", "", phone_number or "")
    if len(digits) < 6:
        return "***"
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


# ---------------------------------------------------------------------------
# ساعات استقبال الطلبات (globale على مستوى المطعم كله)
# ---------------------------------------------------------------------------


def is_within_ordering_hours(
    open_time: Optional[dt_time],
    close_time: Optional[dt_time],
    now: Optional[dt_time] = None,
) -> bool:
    """هل المطعم مفتوح لاستقبال الطلبات Online دلوقتي؟

    لو الوقتين مش متعددين (إحداهم NULL) بنعتبر المطعم مفتوح طول الوقت —
    ده الوضع الافتراضي اللي V1 بيشتغل بيه، ومفيش تغيير في السلوك
    الموجود.

    **الراحة اللي بتلفّ نص الليل:** لو وقت الإغلاق أبكر من وقت الفتح
    (مثلاً 18:00 → 02:00) بنعتبر الفترة دي ممتدة لحد اليوم اللي بعده.
    الحالة العادية (فتح وإغلاق في نفس اليوم) بتقارن الوقت بس.

    `open_time == close_time` معناها 24 ساعة مفتوح، مش "مقفول دايماً".
    """
    if open_time is None or close_time is None:
        return True
    current = now if now is not None else datetime.now(timezone.utc).time()
    if open_time == close_time:
        return True
    if open_time < close_time:
        return open_time <= current < close_time
    # راحة بتعدّي نص الليل، زي 18:00 → 02:00
    return current >= open_time or current < close_time


def ordering_hours_message(
    open_time: Optional[dt_time], close_time: Optional[dt_time]
) -> str:
    """رسالة عربية للعميل لما يكون الطلب مقفول."""
    if open_time is None or close_time is None:
        return ""
    return (
        f"الطلب عبر الموقع متاح من {open_time.strftime('%H:%M')} "
        f"إلى {close_time.strftime('%H:%M')} يوميًا."
    )


def parse_ordering_time(value: Optional[str], label: str) -> Optional[dt_time]:
    """حوّل قيمة `<input type="time">` لـ`time`.

    المتصفح بيبعت `HH:MM` أو نص فاضي. أي نص تاني — لو حد بيبعت request
    متعمد — بيرفع `ValueError` يتحوّل لـ400 في الراوتر، بدل ما يوقع في
    استثناء غامض جوّه SQLAlchemy وقت الحفظ.
    """
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        parsed = dt_time.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"وقت {label} لازم يكون بصيغة HH:MM") from exc
    if parsed.second or parsed.microsecond:
        raise ValueError(f"وقت {label} لازم يكون HH:MM بدون ثواني")
    return parsed.replace(second=0, microsecond=0)
