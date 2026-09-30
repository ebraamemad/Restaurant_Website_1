"""Marketing Center — Phase 4.

## ليه الراوتر ده منفصل عن `admin.py`

`admin.py` بقى 3 آلاف سطر، وفيه كل حاجة: الكتالوج، الطلبات، الإعدادات.
لو ضفت التسويق جوه، بقى ملف بيعمل كذا حاجة ومحدش يقدر يعدّله من غير ما
يكسر حاجة تانية. الفصل بيخلّي مراجعة التسويق مستقلة عن الباقي.

الـrouter بيستخدم نفس نسخة `APIRouter(prefix="/admin", dependencies=...)`
بنفس الـdependencies بالظبط: `get_current_admin` + `verify_admin_csrf`.
فمفيش فرق في الحماية — بس الفصل بيخلّي الـMarketing reviews مستقلة.

## نقطة مهمة: `run_due_campaigns` بيتنادى هنا

مش background task ولا cron. أول ما الأدمن يفتح `/admin/marketing/campaigns`
بنعمل tick: المجدولات اللي وقتها جه بتتنفذ. ده معناه:
- السيرفر مش محتاج scheduler شغال
- لو السيرفر اتقفل، أول ما حد يفتح الصفحة المجدولات بتتنفذ
- لو محدش فتح الصفحة، المجدولات مستنية — وده مقبول لحملة صغيرة

البديل (APScheduler/Celery) استُبعد عمداً: بيضيف dependency وprocess
وحدّث، ومفيش حملات ضخمة تستاهله في مرحلة واحدة.

## ليه `send_campaign` طلب POST صريح

POST `/send` بيبعت فعلياً لكل العملاء. CSRF محميه، بس accidentally-double-click
= حملة مرتين لنفس الناس. عشان كده:
- الزر بيطلب confirmation
- `send_campaign` بيرفضlde بـ`status != 'scheduled'` — مفيش إعادة إرسال
- الإرسال في request واحد، والصفحة بترجع بعد ما يخلص
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import marketing, marketing_csv, segments
from app.database import get_db
from app.deps import get_current_admin, verify_admin_csrf
from app.models import Campaign, ContactImport
from app.schemas import (
    CampaignCreateIn,
    CampaignSummaryOut,
    ImportBatchOut,
    ImportRowOut,
    _validate_internal_url,
)
from app.security import csrf_field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/marketing", dependencies=[Depends(get_current_admin), Depends(verify_admin_csrf)])
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field
templates.env.globals["segments"] = segments.list_segments

#: تسميات الحالات والقنوات. الـraw string في الـtemplate بيبان للمستخدم
#: (`partial` بدل "نجحت من غير ما فشل")، والـfilter بالـquery بيقبل
#: نفس القيم. المصدر واحد عشان ما يحصلش تناقض بين الشاشة والفلتر.
STATUS_LABELS = {
    marketing.STATUS_DRAFT: "مسودة",
    marketing.STATUS_SCHEDULED: "مجدولة",
    marketing.STATUS_SENDING: "بتتبعت",
    marketing.STATUS_COMPLETED: "تمت",
    marketing.STATUS_PARTIAL: "نجحت جزئياً",
    marketing.STATUS_FAILED: "فشلت",
    marketing.STATUS_CANCELLED: "ملغاة",
}
CHANNEL_LABELS = {
    marketing.CHANNEL_PUSH: "Push",
    marketing.CHANNEL_WHATSAPP: "WhatsApp (قريباً)",
    marketing.CHANNEL_BOTH: "Push + WhatsApp",
}
#: القنوات اللي تقدر تتنفذ **دلوقتي**. `whatsapp` بيظهر في الفلتر
#: (فيه حملات متبنية عليه) بس مش في dropdown الإنشاء.
EXECUTABLE_CHANNELS = {marketing.CHANNEL_PUSH}
AUDIENCE_LABELS = {
    "all": "كل العملاء",
    "segment": "شريحة",
    "imported": "عملاء مستوردين",
}
templates.env.globals["status_label"] = STATUS_LABELS.get
templates.env.globals["channel_label"] = CHANNEL_LABELS.get
templates.env.globals["audience_label"] = AUDIENCE_LABELS.get
#: القاموسين كاملين كـglobals: الفلتر في صفحة الحملات بيحتاج
#: `status_labels.items()` (مش `.get`)، والـdetails بيحتاج `.get`.
templates.env.globals["status_labels"] = STATUS_LABELS
templates.env.globals["channel_labels"] = CHANNEL_LABELS

#: ردود على `?error=` — بنترجم الكود لرسالة بدل ما نخزّن نص عربي في URL.
#: المسميات تروح للـtemplate من هنا بدل ما كل صفحة تاخد string literals.
ERROR_MESSAGES = {
    "unknown_segment": "الشرحة دي مش موجودة. من فضلك اختارها من القائمة.",
    "needs_segment": "اختر شرحة من القائمة قبل ما تحفظ الحملة.",
    "unknown_channel": "قناة الإرسال مش معروفة. المتاح: Push.",
    "bad_link": "رابط الحملة لازم يكون مسار داخلي يبدأ بـ/ أو رابط https.",
    "bad_image": "صورة الحملة لازم تكون مسار داخلي يبدأ بـ/ أو رابط https.",
    "bad_json": "الطلب مش مفهوم. جرّب تاني.",
    "schedule_past": "وقت الجدولة لازم يكون في المستقبل.لو عايز تبعت دلوقتي سيب الوقت فاضي.",
    "cancel_done": "الحملة اتلغت.",
    "not_editable": "الحملة دي اتبعت أو اتلغت خلاص — مش هتتعدّل تاني.",
    "import_missing": "ملف الاستيراد مش موجود. ارفعه تاني.",
    "import_gone": "ملف الاستيراد ده اتقفل أو اتستورد. ارفع ملف جديد.",
}

#: `ERROR_MESSAGES` كـglobal مش context per-page: كل الصفحات محتاجاه،
#: وتمريره في كل `TemplateResponse` معناه لو نسيت واحدة هتبقى
#: `UndefinedError` في production. كـglobal مفيش قيمة افتراضية.
templates.env.globals["error_messages"] = ERROR_MESSAGES


def redirect(path: str) -> RedirectResponse:
    """303 مش 302 — عشان المتصفح مايعيدش POST على refresh."""
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _with_error(path: str, code: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return redirect(f"{path}{sep}error={code}")


def _params(
    recently_active_days: Optional[int],
    inactive_days: Optional[int],
    high_spending_minimum: Optional[Decimal],
) -> segments.AudienceParams:
    """حوّل الـform لـ`AudienceParams` مع رمي رسالة مفهومة على خطأ.

    `parse_params` بيدي رسالة إنجليزية عنapyparsing، والأدمن مش بيفهم
    إنجليزي. بنلتقط `ValueError` ونترجمها لـ`bad_json` عشان ترجع
    للصفحة برسالة مفهومة بدل 500.
    """
    try:
        return segments.parse_params(
            recently_active_days=recently_active_days,
            inactive_days=inactive_days,
            high_spending_minimum=high_spending_minimum,
        )
    except ValueError:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/marketing?error=bad_json"})


# ============================================================
# لوحة التسويق — الـstats والشرائح
# ============================================================


@router.get("", response_class=HTMLResponse)
def dashboard(
    request: Request,
    recently_active_days: Optional[int] = None,
    inactive_days: Optional[int] = None,
    high_spending_minimum: Optional[Decimal] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    params = _params(recently_active_days, inactive_days, high_spending_minimum)
    breakdown = segments.segment_breakdown(db, params)
    total_stats = segments.audience_stats(db, "all", params=params)
    recent = db.scalars(
        select(Campaign).order_by(Campaign.created_at.desc()).limit(5)
    ).all()
    return templates.TemplateResponse(
        "admin/marketing/dashboard.html",
        {
            "request": request,
            "breakdown": breakdown,
            "total": total_stats,
            "recent": recent,
            "params": params,
            "error": request.query_params.get("error"),
        },
    )


# ============================================================
# المعاينة
# ============================================================


@router.post("/preview", response_class=HTMLResponse)
def preview(
    request: Request,
    audience_type: str = Form(...),
    segment_key: Optional[str] = Form(default=None),
    channel: str = Form(default="push"),
    recently_active_days: Optional[int] = Form(default=None),
    inactive_days: Optional[int] = Form(default=None),
    high_spending_minimum: Optional[Decimal] = Form(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """عاين الجمهور **قبل** ما تكتب رسالة أو تضغط إرسال.

    دي أهم شاشة في القسم: الأدمن بيشوف "477 عميل، 12 رفضوا، 9 من غير
    إشعار" و5 عينات بأسمائهم. من غير معاينة، زر الإرسال بيبقى رمي
    في الظلام — ومفيش way ترجّع.
    """
    try:
        marketing.validate_audience_fields(audience_type, segment_key)
        marketing.validate_channel(channel)
    except marketing.CampaignError as exc:
        return _preview_error(request, str(exc), db)

    params = _params(recently_active_days, inactive_days, high_spending_minimum)
    try:
        result = marketing.preview_audience(
            db, audience_type, segment_key, channel=channel, params=params
        )
    except segments.AudienceError:
        return _preview_error(request, ERROR_MESSAGES["unknown_segment"], db)
    return templates.TemplateResponse(
        "admin/marketing/preview.html",
        {
            "request": request,
            "result": result,
            "audience_type": audience_type,
            "segment_key": segment_key,
            "channel": channel,
            "params": params,
        },
    )


def _preview_error(request: Request, message: str, db: Session) -> HTMLResponse:
    return templates.TemplateResponse(
        "admin/marketing/preview.html",
        {
            "request": request,
            "result": None,
            "audience_type": request.query_params.get("audience_type", "all"),
            "segment_key": request.query_params.get("segment_key"),
            "channel": "push",
            "params": segments.AudienceParams(),
            "error_message": message,
        },
    )


# ============================================================
# الحملات
# ============================================================


@router.get("/campaigns", response_class=HTMLResponse)
def campaigns_page(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    # Tick كسول: المجدولات المستحقة بتتنفذ بمجرد فتح الصفحة.
    ran = marketing.run_due_campaigns(db)
    for campaign in ran:
        logger.info(
            "marketing lazy tick ran campaign id=%s name=%r sent=%s failed=%s",
            campaign.id,
            campaign.name,
            campaign.sent_count,
            campaign.failed_count,
        )

    filters = marketing.HistoryFilters(
        status=request.query_params.get("status") or None,
        channel=request.query_params.get("channel") or None,
        q=request.query_params.get("q") or None,
    )
    rows, total = marketing.campaign_history(db, filters)
    return templates.TemplateResponse(
        "admin/marketing/campaigns.html",
        {
            "request": request,
            "campaigns": rows,
            "total": total,
            "filters": filters,
            "ran": ran,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/campaigns")
def create_campaign(
    request: Request,
    title: str = Form(...),
    message: str = Form(...),
    name: str = Form(...),
    audience_type: str = Form(...),
    segment_key: Optional[str] = Form(default=None),
    channel: str = Form(default="push"),
    image_url: Optional[str] = Form(default=None),
    link_url: Optional[str] = Form(default=None),
    scheduled_at: Optional[str] = Form(default=None),
    recently_active_days: Optional[int] = Form(default=None),
    inactive_days: Optional[int] = Form(default=None),
    high_spending_minimum: Optional[Decimal] = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """احفظ الحملة. **ما بيبعتش** — الإرسال خطوة تانية مقصودة.

    فصل الإنشاء عن الإرسال مقصود: الأدمن بيكتب الرسالة، بيشوف
    المعاينة، بيقرا الكود مرة تانية، وبعدين يضغط "إرسال". لو
    الإنشاء بيبعت فوراً، مفيش لحظة مراجعة.
    """
    title, message, name = title.strip(), message.strip(), name.strip()
    if not (title and message and name):
        return _with_error("/admin/marketing/campaigns/new", "not_editable")

    # الـform بيوصل raw string؛ الفحص security بيتم قبل أي تخزين.
    # لو رجعنا للـschema كان الـForm مش هيمرّ بيها من غير TypedDict.
    for raw, field_name, key in (
        (image_url, "صورة الحملة", "bad_image"),
        (link_url, "رابط الحملة", "bad_link"),
    ):
        if not raw or not raw.strip():
            continue
        try:
            _validate_internal_url(raw, field_name)
        except ValueError:
            return _with_error("/admin/marketing/campaigns/new", key)

    try:
        marketing.validate_audience_fields(audience_type, segment_key)
        marketing.validate_channel(channel)
    except marketing.CampaignError as exc:
        code = "unknown_channel" if "قناة" in str(exc) else "needs_segment"
        return _with_error("/admin/marketing/campaigns/new", code)

    when: Optional[datetime] = None
    if scheduled_at and scheduled_at.strip():
        when = _parse_scheduled(scheduled_at)
        if when is None:
            return _with_error("/admin/marketing/campaigns/new", "bad_json")
        if when <= datetime.now(timezone.utc):
            return _with_error("/admin/marketing/campaigns/new", "schedule_past")

    try:
        campaign = marketing.create_campaign(
            db,
            name=name,
            title=title,
            message=message,
            audience_type=audience_type,
            segment_key=segment_key,
            channel=channel,
            image_url=image_url.strip() if image_url and image_url.strip() else None,
            link_url=link_url.strip() if link_url and link_url.strip() else None,
            scheduled_at=when,
            admin_id=getattr(request.state, "admin_id", None),
            # المعاملات بتتخزّن مع الحملة عشان الإرسال يطابق المعاينة
            # **بالظبط**. من غير السطر ده، الأدمن يظبط "آخر 7 أيام"
            # في المعاينة ويبعت لـ"آخر 30 يوم" من غير ما ياخد notice.
            params=_params(recently_active_days, inactive_days, high_spending_minimum),
        )
    except segments.AudienceError:
        return _with_error("/admin/marketing/campaigns/new", "unknown_segment")

    # جدولة → نرجع للقائمة (هتشتغل بـlazy tick). فورًا → صفحة الحملة.
    if when is not None:
        return redirect("/admin/marketing/campaigns")
    return redirect(f"/admin/marketing/campaigns/{campaign.id}")


def _parse_scheduled(raw: str) -> Optional[datetime]:
    """`<input type="datetime-local">` بيبعته `2026-01-05T14:30` — مفيش timezone.

    المتصفح بيبعته بتوقيت **العميل**، مش بتوقيت السيرفر. لو السيرفر
    في مصر والعميل في الخليج، بيفرق 3 ساعات. من غير explicit timezone
    مفيش طريق نعرف الإزاي. فبنعتبر إن الوقت **بتوقيت السيرفر**
    (naive → UTC)، وده_good_enough للحملة الداخلية، والـUX بيقول
    للأدمن إن ده بتوقيت السيرفر.
    """
    cleaned = raw.strip().replace(" ", "T")
    for text in (cleaned, cleaned[:16]):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


@router.post("/campaigns/{campaign_id}/send")
def send_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """ابدأ الإرسال فورًا.

    `send_campaign` بيرفض أي حالة غير `scheduled`، فالدوس مرتين
    أو refresh الصفحة مش هيضاعف الإرسال. وده **أقوى** من_confirmation
    في الـUI: حتى لو حد ضغط مرتين بسرعة، التاني بيفشل.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحملة مش موجودة")
    # `params` بتفضل `None` → `send_campaign` بيقرأ
    # `campaign.audience_params` — **نفس القيم اللي شافتها الأدمن في
    # المعاينة** (اتخزّنت وقت الإنشاء). بتمرير `params` من هنا كان
    # هنيحل محل القراءة دي ونرجع لنفس الفجوة اللي العمود اتضاف عشانها.
    try:
        marketing.send_campaign(db, campaign)
    except marketing.ChannelNotReady:
        return _with_error(f"/admin/marketing/campaigns/{campaign_id}", "unknown_channel")
    except marketing.CampaignError as exc:
        logger.warning("campaign send refused id=%s: %s", campaign_id, exc)
        return _with_error(f"/admin/marketing/campaigns/{campaign_id}", "not_editable")
    return redirect(f"/admin/marketing/campaigns/{campaign_id}")


@router.post("/campaigns/{campaign_id}/cancel")
def cancel_campaign(campaign_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحملة مش موجودة")
    try:
        marketing.cancel_campaign(db, campaign)
    except marketing.CampaignError:
        return _with_error(f"/admin/marketing/campaigns/{campaign_id}", "not_editable")
    return redirect(f"/admin/marketing/campaigns/{campaign_id}")


@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        "admin/marketing/campaign_form.html",
        {
            "request": request,
            "campaign": None,
            "error": request.query_params.get("error"),
        },
    )


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الحملة مش موجودة")
    breakdown = marketing.recipient_breakdown(db, campaign_id)
    failures = marketing.failed_recipients(db, campaign_id)
    return templates.TemplateResponse(
        "admin/marketing/campaign_detail.html",
        {
            "request": request,
            "campaign": campaign,
            "breakdown": breakdown,
            "failures": failures,
            "error": request.query_params.get("error"),
        },
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    filters = marketing.HistoryFilters(
        status=request.query_params.get("status") or None,
        channel=request.query_params.get("channel") or None,
        q=request.query_params.get("q") or None,
    )
    rows, total = marketing.campaign_history(db, filters)
    return templates.TemplateResponse(
        "admin/marketing/history.html",
        {"request": request, "campaigns": rows, "total": total, "filters": filters},
    )


@router.get("/api/campaigns", response_class=Response)
def campaigns_json(db: Session = Depends(get_db)) -> Response:
    """JSON للوحة/الاختبارات.

    `CampaignSummaryOut` بيشيل الحسابات عن المنطق —
    `success_rate` معرّفة كـpropertyمش column، فمش هتبان في
    `model_dump` لوحدها، وبنحسبها هنا.
    """
    rows, _total = marketing.campaign_history(db)
    payload = []
    for campaign in rows:
        item = CampaignSummaryOut.model_validate(campaign, from_attributes=True)
        data = item.model_dump()
        data["success_rate"] = item.success_rate
        payload.append(data)
    return _json(payload)


def _json(data, *, status_code: int = status.HTTP_200_OK) -> Response:
    from fastapi.responses import JSONResponse

    return JSONResponse(data, status_code=status_code)


# ============================================================
# الاستيراد
# ============================================================


@router.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request,
    import_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    batch = None
    rows: list[ImportRowOut] = []
    if import_id is not None:
        batch = db.get(ContactImport, import_id)
        if batch is None:
            return _with_error("/admin/marketing/import", "import_gone")
        rows = [
            ImportRowOut(
                row_number=r.row_number,
                raw_name=r.raw_name,
                raw_phone=r.raw_phone,
                name=r.name,
                normalized_phone=r.normalized_phone,
                status=r.status,
                error=r.error,
            )
            for r in marketing_csv.batch_rows(db, import_id)
        ]
    return templates.TemplateResponse(
        "admin/marketing/import.html",
        {
            "request": request,
            "batch": ImportBatchOut.from_model(batch) if batch else None,
            "rows": rows,
            "template": marketing_csv.template_csv(),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/import")
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """ارفع ملف و**اعمل preview**.

    مفيش import هنا — سطور الملف بتتخزّن كـ`ContactImportRow` بحالة
    `valid`/`invalid`/`duplicate`، والأدمن بيشوفها. الـimport بيحصل
    لما يضغط "تأكيد الاستيراد" → POST `/import/{id}/commit`.

    مقصود: الأدمن لازم يشوف "48 صحيح، 3 غلط" قبل ما
    الـcustomers يتضافوا.
    """
    content = await file.read()
    filename = file.filename or "import.csv"
    try:
        batch, _result = marketing_csv.create_import_batch(
            db, content, filename, admin_id=getattr(request.state, "admin_id", None)
        )
    except marketing_csv.CsvImportError as exc:
        logger.info("csv rejected filename=%r: %s", filename, exc)
        return _with_error("/admin/marketing/import", "import_missing")
    return redirect(f"/admin/marketing/import?import_id={batch.id}")


@router.post("/import/{import_id}/commit")
def commit_import(import_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        batch = marketing_csv.run_import(db, import_id)
    except marketing_csv.CsvImportError:
        return _with_error(f"/admin/marketing/import?import_id={import_id}", "import_gone")
    return redirect(f"/admin/marketing/import?import_id={batch.id}")


@router.post("/import/{import_id}/discard")
def discard_import(import_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        marketing_csv.discard_batch(db, import_id)
    except marketing_csv.CsvImportError:
        return _with_error(f"/admin/marketing/import?import_id={import_id}", "import_gone")
    return redirect("/admin/marketing/import")


@router.get("/import/template")
def import_template() -> Response:
    """قالب CSV. `no-store` مهم — المتصفح مش يعمل cache للملف."""
    return Response(
        content=marketing_csv.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="marketing-import-template.csv"',
            "Cache-Control": "no-store",
        },
    )
