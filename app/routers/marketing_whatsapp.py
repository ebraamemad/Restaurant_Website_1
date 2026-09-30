"""صفحة إعدادات واتساب — إعدادات، فحص اتصال، رسالة اختبار.

## ليه راوتر منفصل

ملف التسويق بيكمّل. الإعدادات دي فيها حاجة **مالهاش** علاقة
بالحملات: فحص اتصال المزوّد، رسالة اختبار لرقم واحد، وحفظ
مفاتيح. لو كانت في `marketing.py` كان الملف بيعمل كذا حاجة
ومحدش يقدر يعدّل من غير ما يمسّ حاجة تانية.

## ليه السر مش من الـform

`api_key` مالوش عمود في `whatsapp_config` أصلاً — بيتقرأ من
`EVOLUTION_API_KEY` في البيئة بس. يعني مفيش `<input type=password>`
بياخده، ومش بنحطه في الـURL بعد الحفظ. الحقل ده لو اتزاد في
الـDB كان معناه نسخ المفتاح في backup واحد على الأقل.

## ليه الفحص قبل الحفظ

`validate_base_url` بيرمي على رابط فيه مستخدم/كلمة سر، أو `http`
في production، أو نطاق داخلي والمفتاح مقفول. الخطأ بيرجع للصفحة
برسالة — أحسن من إنه يتحفظ بسري ويطلع 400 رسالة فاشلة بعدين على
أول حملة.

## ليه القفلين منفصلين

- `enabled`: القناة موجودة أصلاً (تشوف المعاينة والاختبار).
- `sending_enabled`: الأدمن أكّد إنه عايز يبعت.

اللي بيفصل بينهم هو زرار الإرسال في حملة فيها 5000 عميل.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_admin, verify_admin_csrf
from app.models import WhatsAppConfigRow
from app.routers.marketing import templates
from app.security import sign_result, verify_result_signature
from app.whatsapp import service as whatsapp_service
from app.whatsapp.base import (
    ConnectionState,
    SendState,
    WhatsAppConfigurationError,
)
from app.whatsapp.settings import (
    MAX_BATCH_SIZE,
    MAX_INTERVAL_MS,
    MAX_RETRIES,
    MIN_INTERVAL_MS,
    load_config,
    validate_base_url,
)

router = APIRouter(
    prefix="/admin/marketing/whatsapp",
    dependencies=[Depends(get_current_admin), Depends(verify_admin_csrf)],
)

#: نتيجة آخر عملية بترجع في الـquery-string بعد redirect (PRG).
#: القيم رموز قصيرة — **مفيش نص حر ولا سر** بين URL.
NOTES = {
    "saved": "اتحفظت.",
    "connection_ok": "الاتصال بالمزوّد شغال.",
    "provider_unavailable": "المزوّد مش متصل.",
    "connection_error": "الإعدادات ناقصة أو فيها خطأ.",
    "message_sent": "الرسالة اتبعتت.",
    "message_failed": "الرسالة ماتبعتتش.",
    "bad_url": "الرابط مش صالح.",
    "bad_number": "الرقم مش رقم مصري صحيح.",
    "not_ready": "الإعدادات ناقصة.",
}

#: لون الشريط حسب النتيجة. "نجح" و"فشل" مش نفس اللون — شريط
#: أخضر مع خبر فاشل هو أسوأ من مفيش شريط.
NOTE_TONES = {
    "saved": "ok",
    "connection_ok": "ok",
    "message_sent": "ok",
    "provider_unavailable": "warn",
    "not_ready": "warn",
    "bad_url": "bad",
    "bad_number": "bad",
    "connection_error": "bad",
    "message_failed": "bad",
}

#: نص كل حالة. القيم جاية من `SendState` و `ConnectionState`.
STATE_LABELS = {
    ConnectionState.CONNECTED.value: "\u0645\u062a\u0635\u0644",
    ConnectionState.DISCONNECTED.value: "\u0645\u0634 \u0645\u062a\u0635\u0644",
    ConnectionState.ERROR.value: "\u062e\u0637\u0623 \u0641\u064a \u0627\u0644\u0625\u0639\u062f\u0627\u062f",
    SendState.SENT.value: "\u0627\u062a\u0628\u0639\u062a\u062a",
    SendState.FAILED.value: "\u0641\u0634\u0644\u062a",
    SendState.SKIPPED.value: "\u0627\u062a\u062e\u0637\u0651\u062a",
}

#: المسموح في الـURL. أي رمز أو حالة بره القائمتين دول بتتجاهل،
#: فما فيش نص من المزوّد بيوصلنا عبر الرابط.
ALLOWED_STATES = frozenset(STATE_LABELS)

CONNECTION_LABELS = {
    ConnectionState.CONNECTED: "\u0645\u062a\u0635\u0644",
    ConnectionState.DISCONNECTED: "\u0645\u0634 \u0645\u062a\u0635\u0644",
    ConnectionState.ERROR: "\u062e\u0637\u0623",
}

SETTINGS_PATH = "/admin/marketing/whatsapp"


def _redirect(code: str, *, state: Optional[str] = None, phone: Optional[str] = None) -> RedirectResponse:
    """303 بعد POST — عشان refresh مايعيدش الحفظ ولا الإرسال.

    **الـURL فيه رموز بس، وموقّعة.** `note` من قاموس، `state` من
    قاموس، و`phone` أرقام. نص المزوّد — أي نص حر — ما بيمشيش من
    هنا، والسبب إن الرابط بيتكتب في access logs و browser history.

    `urlencode` مش اختياري: من غيرها `&` جوه النص بتكسر الـquery.

    والـ`sig` بيخلّي الرمز مش قابل للتزوير: حد يبعت رابط
    بإيده بـ`note=message_sent` ما يشوفش banner نجاح.
    """
    if code not in NOTES:
        code = "not_ready"
    if state not in ALLOWED_STATES:
        state = ""
    params = {"note": code, "sig": sign_result(code, state)}
    if state:
        params["state"] = state
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())[:20]
    if digits:
        params["phone"] = digits
    return RedirectResponse(f"{SETTINGS_PATH}?{urlencode(params)}", status_code=303)


def _row(db: Session) -> Optional[WhatsAppConfigRow]:
    return db.scalar(select(WhatsAppConfigRow).limit(1))


def _context(request: Request, db: Session, **overrides) -> dict:
    """سياق الصفحة.

    `load_config` بيرمي لو الـURL المكتوب في الجدول متلفّ
    (`http` في production مثلاً). ده **مش** 500 — نعرض القيم
    الخام مع الرسالة، عشان الأدمن يشوف culprit ويصلّحه بدل ما
    يطلّع صفحة خطأ وهو أصلاً بيوصلّح الإعدادات.

    **`note` و `state` بيتقرا من القواميس مش من الـquery مباشرة**:
    أي قيمة بره القاموس بتتجاهل. ده اللي بيمنع حد يبعت رابط
    `/admin/marketing/whatsapp?note=message_sent` للعين ويقول
    للمستخدم إن الرسالة اتبعتت.
    """
    raw = _row(db)
    try:
        config = load_config(db)
        problem = ""
    except WhatsAppConfigurationError as exc:
        config = None
        problem = str(exc)

    raw_note = request.query_params.get("note")
    raw_state = request.query_params.get("state") or ""
    signature = request.query_params.get("sig")
    # الرمز لازم يكون معروف **وموقّع**. التوقيع بيستبعد رابط
    # مزوّر من حد تاني بيقول "الرسالة اتبعتت".
    signed = verify_result_signature(raw_note or "", raw_state, signature)
    note = raw_note if signed and raw_note in NOTES else None
    state = raw_state if signed and raw_state in ALLOWED_STATES else None
    raw_phone = request.query_params.get("phone")
    phone_from_url = "".join(ch for ch in (raw_phone or "") if ch.isdigit())[:20]

    context = {
        "request": request,
        "config": config,
        "raw_row": raw,
        "problem": problem,
        "note": note,
        "note_text": NOTES.get(note, "") if note else "",
        "note_tone": NOTE_TONES.get(note, "warn") if note else "",
        "notes": NOTES,
        "state": state,
        "state_label": STATE_LABELS.get(state, "") if state else "",
        "phone": phone_from_url
        or (config.test_phone if config else "")
        or settings.whatsapp_test_phone
        or "",
        "message": settings.whatsapp_test_message or "",
        "connection": None,
        "connection_labels": CONNECTION_LABELS,
        "min_interval_ms": MIN_INTERVAL_MS,
        "max_interval_ms": MAX_INTERVAL_MS,
        "max_batch_size": MAX_BATCH_SIZE,
        "max_retries": MAX_RETRIES,
        "test_states": {
            SendState.SENT: "اتبعتت",
            SendState.FAILED: "فشلت",
            SendState.SKIPPED: "اتخطّت",
        },
    }
    context.update(overrides)
    return context


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        "admin/marketing/whatsapp_settings.html", _context(request, db)
    )


@router.post("")
def save_settings(
    enabled: bool = Form(default=False),
    sending_enabled: bool = Form(default=False),
    base_url: str = Form(default=""),
    instance: str = Form(default=""),
    message_interval_ms: int = Form(default=1000),
    batch_size: int = Form(default=20),
    max_retries: int = Form(default=2),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """احفظ الإعدادات. **مش** بيبعتش رسالة.

    مفيش `api_key` في الـsignature عن قصد — مفيش عمود ليه. لو حد
    بعت الحقل في الـform بنتجاهله: أي حاجة في الجدول بتتسخ في
    الـbackups، والمفتاح مفروض مايتخزّنش.
    """
    cleaned_url = (base_url or "").strip().rstrip("/")
    if cleaned_url:
        try:
            validate_base_url(
                cleaned_url, allow_private=bool(settings.whatsapp_allow_private_hosts)
            )
        except WhatsAppConfigurationError:
            return _redirect("bad_url")

    row = _row(db)
    if row is None:
        row = WhatsAppConfigRow(id=1)
        db.add(row)
    row.enabled = bool(enabled)
    row.sending_enabled = bool(sending_enabled)
    row.base_url = cleaned_url or None
    row.instance = (instance or "").strip() or None
    # `clamp` مش `reject`: الأدمن بيكتب `0` وهو قصده "بلا فاصل"،
    # والرقم الغريب يتقصّ بدل ما يرجع 422 على زر الحفظ.
    row.message_interval_ms = max(
        MIN_INTERVAL_MS, min(int(message_interval_ms or 0), MAX_INTERVAL_MS)
    )
    row.batch_size = max(1, min(int(batch_size or 1), MAX_BATCH_SIZE))
    row.max_retries = max(0, min(int(max_retries or 0), MAX_RETRIES))
    db.commit()
    return _redirect("saved")


@router.post("/test-connection")
def test_connection(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """افحص الاتصال بالمزوّد.

    `check_connection` **ما بيرميش** — رجّع حالة وخلاص. عشان كده
    مفيش `try/except` هنا: لو رمي يبقى ده bug مش حالة مستخدم.

    تلات حالات مختلفة ورمز لكل واحدة: متصل، مزوّد مش متصل
    (الـinstance موجودة بس جلسة الواتساب مقفولة)، وخطأ إعداد.
    ده اللي يخلي الأدمن يعرف: أعمل ايه بعدين.
    """
    status = asyncio.run(whatsapp_service.check_connection(db))
    if status.state is ConnectionState.CONNECTED:
        code = "connection_ok"
    elif status.state is ConnectionState.DISCONNECTED:
        code = "provider_unavailable"
    else:
        code = "connection_error"
    return _redirect(code, state=status.state.value)


@router.post("/send-test")
def send_test(
    phone: str = Form(default=""),
    body: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """رسالة واحدة لرقم واحد. **مفيش سطر في الـdatabase.**

    ليه رقم في الـform: الأدمن بيكتب رقمه الأول وبيبقى محفوظ في
    المتصفح بعدين. والـdefault من `WHATSAPP_TEST_PHONE` في البيئة
    عشان الرقم مش بيتكتب بالغلط في خانة الاسم وتبقى بتكتب على
    زبون حقيقي بالاسم.

    **الرقم بيتشيك قبل أي اتصال.** `send_test_message` بيرمي
    `WhatsAppConfigurationError` في الحالتين: رقم غلط، وإعدادات
    ناقصة. نخلطهم كانت هتخلي الأدمن يظن إن الرقم غلط وهو
    أصلاً مش مفعّل الإعدادات أصلاً.
    """
    target = (phone or "").strip() or (settings.whatsapp_test_phone or "").strip()
    if not target:
        return _redirect("not_ready")
    if not whatsapp_service.to_dialable(target):
        return _redirect("bad_number", phone=target)
    try:
        outcome = asyncio.run(
            whatsapp_service.send_test_message(
                db, target, body or settings.whatsapp_test_message or "رسالة اختبار"
            )
        )
    except WhatsAppConfigurationError:
        return _redirect("not_ready", phone=target)

    code = "message_sent" if outcome.state is SendState.SENT else "message_failed"
    return _redirect(code, phone=target, state=outcome.state.value)
