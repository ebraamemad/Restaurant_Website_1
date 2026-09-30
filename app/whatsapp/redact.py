"""تنضيف الأسرار قبل أي تسجيل أو عرض.

## ليه الملف ده موجود لوحده

تسريب المفتاح بيحصل دايمًا من نفس الأماكن: رسالة خطأ من المزوّد،
`repr()` لكائن فيه إعدادات، traceback، أو `str()` على exception. كلها
"النية الحسنة" وكلها بتسرّب.

القاعدة هنا **بسيطة وواحدة**: أي نص بيخرج التطبيق — لوج، رد مزوّد،
رسالة خطأ، HTML — يعدّي على `redact()` الأول.

## ليه نمط بدل قائمة قيم

`redact()` بيبلّش بـ`sk-`، ومفتاح Evolution ممكن يكون `apikey:...` أو
base64 طويل. بدل ما نحط كل الأنماط المحتملة، بنمسح **أي** نص
طويل عشوائي Shape-وإيه. الحتمية أهم من الدقة: تسريب مفتاح بيحصل مرة
واحدة وبيكفّي.

`mask_secret` للمكانين اللي المفروض **يعرضوا** جزء من السر (شكل
`••••1234`) — عشان الأدمن يفرّق بين "مفيش مفتاح" و"مفتاح محفوظ".
"""

from __future__ import annotations

import re
from typing import Optional

#: أقل طولconsidered "سر محتمل". أقل من كده أرقام عادية أو IDs.
_MIN_SECRET_LEN = 8

#: الحروف اللي الـsecret بتبقى منها عملياً (base64/hex/url-safe).
_SECRET_SHAPES = (
    # hex
    re.compile(r"\b[0-9a-fA-F]{24,}\b"),
    # base64 / base64url — شرط واحد على الأقل من الحرفين المميّزين،
    # عشان ما نمسحش جملة إنجليزية طويلة فيها حروف عادية.
    re.compile(r"\b[A-Za-z0-9+/_-]{24,}={0,2}\b"),
    # JWT
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    # Bearer
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    # key=value شائعة
    re.compile(
        r"(?i)\b(api[_-]?key|apikey|token|secret|password|authorization)"
        r"\b\s*[:=]\s*\S{8,}"
    ),
)

_REDACTED = "[redacted]"


def redact(value: Optional[str], *, extra: tuple[str, ...] = ()) -> Optional[str]:
    """يرجّع نص آمن للتسجيل أو العرض.

    `extra` = قيم معروفة (المفتاح الحالي مثلاً) تُمسح حرفياً قبل
    النمط العام — عشان مفتاح قصير أو atypy ما يقعش في نمط.

    `None` بيفضل `None` مش نص فاضي: المستهلك بيفرق بين "مفيش" و"فاضي".
    """
    if value is None:
        return None
    out = value
    for secret in extra:
        if secret and len(secret) >= 4:
            out = out.replace(secret, _REDACTED)
    for pattern in _SECRET_SHAPES:
        out = pattern.sub(_REDACTED, out)
    if len(out) > _MIN_SECRET_LEN and _looks_like_secret(out):
        return _REDACTED
    return out


def _looks_like_secret(text: str) -> bool:
    """نص طويل مفيهوش حروف عربية ولا مسافات = غالبًا مفتاح.

    دهSci-fi guard أخير: لو الرد كان رسالة إنجليزية طويلة من
    المزوّد، هتعدّي. لو كان سطر مفتاح، هتتمسح.
    """
    if not text:
        return False
    if any("\u0600" <= ch <= "\u06ff" for ch in text):
        return False
    if " " in text.strip():
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/_=.:@\-]+", text.strip())) and len(text) >= 24


def mask_secret(value: Optional[str], *, keep: int = 4) -> str:
    """شكل العرض: `••••abcd` — آخر `keep` حرف بس.

    الأدمن محتاج يفرّق بين "مفيش مفتاح" و"مفيّfasela محفوظ"، بس
    **مش** محتاج القيمة. آخر 4 حروف كافية للعيان، ومش كافية
    للاستخدام.
    """
    if not value:
        return ""
    if len(value) <= keep:
        return "•" * len(value)
    return "•" * 8 + value[-keep:]