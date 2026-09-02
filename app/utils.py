from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
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
    is_pickup: bool,
    total: Decimal,
    notes: Optional[str],
    pickup_time: Optional[datetime] = None,
    coupon_code: Optional[str] = None,
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
        lines.append("طريقة الاستلام: " + ("استلام من المطعم" if is_pickup else "توصيل"))
    if pickup_time is not None:
        # العرض بالتوقيت المحلي للمطعم — نفس التوقيت اللي بيرجع من قاعدة البيانات.
        lines.append(f"موعد الطلب: {ensure_aware(pickup_time).astimezone().strftime('%Y-%m-%d %H:%M')}")
    else:
        lines.append("موعد الطلب: في أقرب وقت")

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
        normalized_phone = digits

    return f"https://wa.me/{quote(normalized_phone, safe='')}?text={quote(message, safe='')}"


def normalize_phone(phone_number: str) -> str:
    """توحيد الرقم للتجميع ومنع تكرار العميل، مع إبقاء الصفر المحلي كما هو."""
    digits = re.sub(r"\D", "", phone_number or "")
    if digits.startswith("00"):
        digits = digits[2:]
    return digits
