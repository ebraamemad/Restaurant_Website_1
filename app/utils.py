from __future__ import annotations

import re
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
    customer_address: str,
    items: List[Tuple[str, int, Decimal]],
    total: Decimal,
    notes: Optional[str],
) -> str:
    lines = [
        f"طلب جديد من {settings.restaurant_name}",
        "",
        f"الاسم: {customer_name}",
        f"الهاتف: {customer_phone}",
        f"العنوان: {customer_address}",
        "",
        "المنتجات:",
    ]
    for name, quantity, line_total in items:
        lines.append(f"- {name} x {quantity} = {money(line_total)}")
    lines.extend(["", f"الإجمالي: {money(total)} {settings.currency}"])
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
