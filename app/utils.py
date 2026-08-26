from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple
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
    customer_name: str,
    customer_phone: str,
    customer_address: Optional[str],
    items: List[Tuple[str, int, Decimal, Optional[str]]],
    subtotal: Decimal,
    discount_amount: Decimal,
    delivery_fee: Decimal,
    delivery_name: Optional[str],
    total: Decimal,
    notes: Optional[str],
    pickup_time: Optional[datetime] = None,
    coupon_code: Optional[str] = None,
) -> str:
    lines = [
        f"طلب جديد من {settings.restaurant_name}",
        "",
        f"الاسم: {customer_name}",
        f"الهاتف: {customer_phone}",
    ]
    if customer_address:
        lines.append(f"العنوان: {customer_address}")
    if delivery_name:
        lines.append(f"طريقة الاستلام: {delivery_name}")
    if pickup_time is not None:
        lines.append(f"موعد الطلب: {ensure_aware(pickup_time).strftime('%Y-%m-%d %H:%M')}")
    lines.extend(["", "المنتجات:"])
    for name, quantity, line_total, item_note in items:
        lines.append(f"- {name} x {quantity} = {money(line_total)}")
        if item_note:
            lines.append(f"  ملاحظة: {item_note}")
    lines.append("")
    lines.append(f"الإجمالي الفرعي: {money(subtotal)} {settings.currency}")
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
