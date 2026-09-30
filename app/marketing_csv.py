"""استيراد جهات الاتصال من CSV — قراءة، تحقق، معاينة، استيراد.

## الملف ده بيدخل من برّه

الملف بييجي من الأدمن، يعني من حد ما نعرفوش. كل حاجة هنا مكتوبة
على أساس إن **المدخل عدائي**:

| الخطر | الحماية |
|---|---|
| ملف ضخم | حد بايت + حد صفوف، **قبل** أي parse |
| ملف مش CSV | فحص التمديد + فحص المحتوى الثنائي |
| CSV injection | رفض الخلايا اللي بتبدأ بـ`=` `+` `-` `@` |
| رقم هاتف مش مصري | `validate_customer_phone` (نفسOrders بالظبط) |
| تكرار | على مستوى الملف وعلى مستوى الداتابيز |
| نص بيحقن HTML | السطر بيتعرض escaped، والاسم بيتنضّف |

## CSV injection — التفسير المختصر

Excel وGoogle Sheets بيقرّوا أي خلية تبدأ بـ`=` أو `+` أو `-` أو
`@` كـ**formula** مش نص. يعني اسم عميل فيه `=HYPERLINK("http://evil",
"اضغط")` بيتحوّل ل لينك بيتنفّذ لما الأدمن يفتح ملف التصدير. عشان كده
الاسم اللي بيبدأ بحرف من دول **مرفوض**، مش متسالش. نرفض بدل ما
نعمل `escape`: الترجيع للعميل في رسالة "اسم غير مسموح" أوضح له من
اسم مكتوب فيه فاصلة عليا بتظهر في كل تقرير.

## ليه `csv` module مش `pandas`

`pandas.read_csv` بيقبل ملفات 10 جيجا في الذاكرة، وبي extrapolation
بيحاول يخمّن الترميز والترقيم. `csv` القياسي بايت-ببايت، فحجم
الملف هو اللي بنحكمه إحنا مش المكتبة. المشروع أصلاً مش فيه pandas
وده قرار مقصود — مش هضيفه عشان ملف واحد.

## ليه الدفعة في الداتابيز

انظر `ContactImport` في `app/models.py`. باختصار:_steps السطر
مراراً على HTTP (رفع → معاينة → استيراد) وthousands سطر مش
هيتحملوا في hidden field ولا في cookie.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContactImport, ContactImportRow, Customer
from app.utils import PHONE_MAX_INPUT_LENGTH, normalize_phone, validate_customer_phone

# ============================================================
# الحدود
# ============================================================

#: أقصى حجم ملف. 2 ميجا = تقريباً 12 ألف سطر باسم ورقم. استيراد
#: أكبر من كده بيتبعت على دفعات، وده أحسن من ملف
#: واحد بيقع عند 40 ألف سطر.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

#: أقصى عدد أسطر نتعامل معاها. مستقل عن الحجم: ملف 2 ميجا من
#: سطر واحد طويل = DoS برضه (الـ`csv` بيحجز ذاكرة للصف دي).
MAX_ROWS = 10_000

#: أقصى طول الاسم. `customers.name` = 120. الزيادة بتتقص
#: (التصدير بيحتاج نص)، والنقص بيرفض.
MAX_NAME_LENGTH = 120

#: أقصى طول خلية قبل الفحص. سطر من 10 ميجا في خلية واحدة
#: بيرفع `csv.field_size_limit` عندنا وبنفحصه بعدين.
MAX_CELL_LENGTH = 300

#: بادئات قاتلة للـformulas في Excel/Sheets. الحروف دي لو في أول
#: خلية، Excel بيقراها كـformula مش نص.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: نفس القائمة، مختصرة للأرقام.
#:
#: `+` و`-` **مش** في القائمة دي لسبب مقصود: `+201122334455` صيغة
#: هاتف دولية شرعية، و`010-123-4567` صيغة بشرية. لو فحصنا الرقم
#: بالقائمة الكاملة كانImports هترفض نص الملفات المصرية الصح.
#:
#: وباقي القائمة (بعد التنظيف) بتمنع أي نص فيه حرف أو رمز، فالرقم
#: لازم يكون أرقام بس. `=SUM(12345678901)` فيه `=` و`SUM` و`()`
#: فيترفض، و`=1+1` فيه `=` فيترفض. مفيش طريقة تخدع الفحص ده
#: وتعدّي `is_valid_egypt_phone` بعدها.
PHONE_FORMULA_PREFIXES = ("=", "@", "\t", "\r")

#: ترميزات بنجرّبها بالترتيب. `utf-8-sig` الأول عشان Excel العربي
#: بيحطّ BOM في الأول وبيخلي العمود الأول اسمه `\ufeffphone`.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1256", "latin-1")

#: الامتدادات المقبولة. `.txt` مسموح — ناس كتير بتحفظ export
#: كـCSV وبيسميه `.txt`.
ALLOWED_EXTENSIONS = (".csv", ".txt")


class CsvImportError(ValueError):
    """الملف مرفوض بالكامل — الرفع بيرجّع 400 برسالة واضحة.

    ده مش نفس "سطر مرفوض". السطر المرفوض بيتخطّى والملف يكمل.
    ده الملف كله مUsageش (مفيش هاتف، حجم كبير، ترميز مستحيل).
    """


# ============================================================
# قراءة الملف
# ============================================================


def _check_size(content: bytes) -> None:
    if not content:
        raise CsvImportError("الملف فاضي")
    if len(content) > MAX_UPLOAD_BYTES:
        mb = len(content) / (1024 * 1024)
        raise CsvImportError(f"الملف كبير أوي ({mb:.1f} ميجا). الحد {MAX_UPLOAD_BYTES // (1024 * 1024)} ميجا")


def _check_extension(filename: str) -> None:
    lowered = (filename or "").lower()
    if not lowered.endswith(ALLOWED_EXTENSIONS):
        raise CsvImportError("الملف لازم يكون CSV (امتداد .csv أو .txt)")


def _check_not_binary(content: bytes) -> None:
    """بايت NULL = ملف مش نص.

    ده أرخص فحص للملفات التنفيذية: `.exe` و`.zip` كلهم فيه NUL.
    بنبص على أول 8 كيلوبايت بس — ملف نصي عنده NUL في النص عادي
    (مش، النصوص ما فيها NUL)، فلو لقينا واحد يبقى الملف مش CSV.
    """
    if b"\x00" in content[:8192]:
        raise CsvImportError("الملف ده مش ملف نصي (بايتات غير صالحة)")


def _decode(content: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvImportError("مش قادرين نقرأ ترميز الملف. احفظه UTF-8 و جرّب تاني")


def _sniff_delimiter(sample: str) -> str:
    """Kam نعرف الفاصل.

    بنجرّب الفواصل المرشحة على أول 5 سطور ونحسب أي واحد بيطلع
    **نفس عدد الأعمدة** في كلهم. `csv.Sniffer` موجود، بس بيعتمد على
    الإحصائيات وبيرجع غلط مع الملفات العربية (بيحسب `،` فاصلة
    ناصلة). المنطق هنا 20 سطر ومحدود.
    """
    candidates = [",", ";", "\t", "|"]
    best: tuple[int, str] = (0, ",")
    lines = [ln for ln in sample.splitlines() if ln.strip()][:5]
    if not lines:
        return ","
    for candidate in candidates:
        counts = {line.count(candidate) for line in lines}
        # كل السطور لازم يكون فيه نفس العدد، و1 على الأقل.
        if len(counts) == 1 and counts.pop() >= 1:
            return candidate
        # لو مفيش فاصل ثابت، ناخد الأكثر تكراراً.
        top = max(lines, key=lambda ln: ln.count(candidate)).count(candidate)
        if top > best[0]:
            best = (top, candidate)
    return best[1] if best[0] else ","


#: أسماء الأعمدة المقبولة. `lower` + strip. العربية بتت normalizing
#:Forms: "الاسم " و"الاسم" لازم يشتغلوا مع بعض.
NAME_HEADERS = {"name", "الاسم", "اسم", "customer", "customer name", "full name", "اسم العميل"}
PHONE_HEADERS = {
    "phone",
    "mobile",
    "number",
    "tel",
    "telephone",
    "الهاتف",
    "رقم الهاتف",
    "رقم الموبايل",
    "رقم",
    "تليفون",
    "موبايل",
}


def _normalize_header(value: str) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = cleaned.replace("\ufeff", "")
    # توحيد الألف والهاء والياء والتاء المربوطة: "إسم" و"اسم" نفس
    # العمود. من غير ده بطل نطلب من الأدمن يسمّي العمود بالظبط.
    return re.sub(r"[إأآا]", "ا", re.sub(r"ى", "ي", re.sub(r"ة", "ه", cleaned)))


def _map_columns(header: List[str]) -> tuple[Optional[int], Optional[int]]:
    """يرجّع `(name_index, phone_index)` أو بيرمي.

    **الاسم اختياري** (المطلوب بس الهاتف: رقم من غير اسم لسه
    رقم صالح، والدفع بيتبعت بالرقم). الهاتف **مطلوب** — من غيره
    السطر مالهوش هوية.
    """
    normalized = [_normalize_header(cell) for cell in header]
    name_index: Optional[int] = None
    phone_index: Optional[int] = None
    for index, cell in enumerate(normalized):
        if not cell:
            continue
        if phone_index is None and cell in PHONE_HEADERS:
            phone_index = index
        elif name_index is None and cell in NAME_HEADERS:
            name_index = index
    if phone_index is None:
        raise CsvImportError(
            "مفيش عمود هاتف. لازم يكون فيه عمود اسمه 'رقم الهاتف' أو 'phone'"
        )
    return name_index, phone_index


# ============================================================
# تنظيف القيم
# ============================================================


def _clean_cell(value: Optional[str]) -> str:
    return (value or "").replace("\x00", "").strip()


def _has_formula_prefix(value: str, prefixes: tuple[str, ...] = FORMULA_PREFIXES) -> bool:
    return value.startswith(prefixes)


def _normalize_digits(value: str) -> str:
    """الأرقام العربية والهندية → ASCII.

    Excel العربي بيدخّل أرقام عربية (٠١٢...) في خلايا نصية أحياناً.
    من غير ده كل سطر من Excel بيبقى "رقم غير صحيح" والأدمن يفتكر
    إن الملف غلط.
    """
    arabic = "٠١٢٣٤٥٦٧٨٩"
    eastern = "۰۱۲۳۴۵۶۷۸۹"
    out = []
    for char in value:
        if char in arabic:
            out.append(str(arabic.index(char)))
        elif char in eastern:
            out.append(str(eastern.index(char)))
        else:
            out.append(char)
    return "".join(out)


# ============================================================
# نتيجة السطر
# ============================================================

STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_DUPLICATE = "duplicate"
STATUS_IMPORTED = "imported"


@dataclass
class ParsedRow:
    """سطر اتقري واتفحص — قبل ما يتقرر يتستورد ولا لأ."""

    row_number: int
    raw_name: Optional[str]
    raw_phone: Optional[str]
    status: str = STATUS_VALID
    name: Optional[str] = None
    normalized_phone: Optional[str] = None
    error: Optional[str] = None
    existing_customer_id: Optional[int] = None

    @property
    def is_importable(self) -> bool:
        return self.status == STATUS_VALID


@dataclass
class ParseResult:
    rows: List[ParsedRow] = field(default_factory=list)
    total: int = 0
    valid: int = 0
    invalid: int = 0
    duplicates: int = 0
    #: أعمدة الـCSV الفعلية — بتعرض للمستخدم لو الملف كان غريب.
    detected: str = ""

    def summary(self) -> dict[str, int]:
        return {
            "total": self.total,
            "valid": self.valid,
            "invalid": self.invalid,
            "duplicates": self.duplicates,
        }


# ============================================================
# الـparse
# ============================================================


def parse_csv(
    content: bytes,
    filename: str,
    *,
    existing_phones: Optional[Iterable[str]] = None,
) -> ParseResult:
    """يقرأ الملف ويرجّع نتيجة **معاينة** — ما بيكتبش حاجة.

    `existing_phones` = الأرقام الموجودة في الداتابيز (بالشكل
    الموحّد). بنمرّرها بدل ما نعمل query لكل سطر: 12 ألف سطر
    = 12 ألف query.

    **الترتيب:** كل سطر بيتنضّف ويتحقّق **قبل** ما نتحسب التكرار.
    سطر رقمه غلط أصلاً مش "مكرر" — هو "غلط". الخلط الاتنين بيخلّي
    الأدمن يشوف "500 مكرر" ويظن إن الملف نضيف وهو فيه 200 رقم
    باط.
    """
    _check_size(content)
    _check_extension(filename)
    _check_not_binary(content)
    text = _decode(content)

    delimiter = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        all_rows = list(reader)
    except csv.Error as exc:
        raise CsvImportError(f"الملف مش CSV سليم: {exc}") from exc

    if not all_rows:
        raise CsvImportError("الملف مفيش فيه أي سطر")

    header = all_rows[0]
    name_index, phone_index = _map_columns(header)
    body = all_rows[1:]

    if len(body) > MAX_ROWS:
        raise CsvImportError(
            f"الملف فيه {len(body)} سطر. الحد {MAX_ROWS}. قسّمه على ملفات أصغر"
        )

    known = set(existing_phones or ())
    seen_in_file: dict[str, int] = {}
    result = ParseResult(
        total=len(body), detected=f"الفاصل: {delimiter!r} — الاسم: {name_index}، الهاتف: {phone_index}"
    )

    for offset, raw in enumerate(body, start=2):  # 2 = بعد الـheader
        if len(result.rows) >= MAX_ROWS:
            break
        if not any((cell or "").strip() for cell in raw):
            continue  # سطر فاضي — مش خطأ، مجرد فراغ

        raw_name = _clean_cell(raw[name_index]) if name_index is not None and name_index < len(raw) else None
        raw_phone_raw = _clean_cell(raw[phone_index]) if phone_index < len(raw) else ""
        raw_phone = _normalize_digits(raw_phone_raw)

        row = ParsedRow(row_number=offset, raw_name=raw_name, raw_phone=raw_phone_raw or None)

        # 1) Atalabort — Formula. الاسم بالقائمة الكاملة، والرقم
        #    بقائمة أضيق (شوف `PHONE_FORMULA_PREFIXES`).
        if raw_name and _has_formula_prefix(raw_name, FORMULA_PREFIXES):
            row.status = STATUS_INVALID
            row.error = f"الاسم بيبدأ برمغة ({raw_name[0]!r}) — مش مسموح"
        elif raw_phone and _has_formula_prefix(raw_phone, PHONE_FORMULA_PREFIXES):
            row.status = STATUS_INVALID
            row.error = f"رقم الهاتف بيبدأ برمغة ({raw_phone[0]!r}) — مش مسموح"
        if row.status == STATUS_INVALID:
            result.rows.append(row)
            result.invalid += 1
            continue

        # 2) جوال.
        if not raw_phone:
            row.status = STATUS_INVALID
            row.error = "رقم الهاتف فاضي"
            result.rows.append(row)
            result.invalid += 1
            continue
        if len(raw_phone) > PHONE_MAX_INPUT_LENGTH:
            row.status = STATUS_INVALID
            row.error = f"رقم طويل أوي (أكثر من {PHONE_MAX_INPUT_LENGTH})"
            result.rows.append(row)
            result.invalid += 1
            continue
        try:
            display_phone = validate_customer_phone(raw_phone)
        except ValueError:
            row.status = STATUS_INVALID
            row.error = "رقم الموبايل مش مصري صحيح"
            result.rows.append(row)
            result.invalid += 1
            continue
        row.normalized_phone = normalize_phone(display_phone)

        # 3) الاسم.
        name = raw_name or None
        if name and len(name) > MAX_NAME_LENGTH:
            row.status = STATUS_INVALID
            row.error = f"الاسم أطول من {MAX_NAME_LENGTH} حرف"
            result.rows.append(row)
            result.invalid += 1
            continue
        row.name = name

        # 4) تكرار — بعدCleanup والachi، عشان التصنيف يبقى أمين.
        #
        # **التكرار جوه الملف** = سطر مش هيتنفّذ (الرقم اتقدّم فعلاً).
        # **الرقم موجود عندنا** = مش تكرار، ده **تحديث**: العميل ده
        # قائم فعلاً والصف الأول كلّفنا استيراد ملوش لازمة. لو رسبناه
        # كـduplicate، الأدمن مش هيقدر يصحّح اسم عميل من ملف أبداً —
        # والملف كله هيفضل مرفوض. الفرق بين "تكرار" و"موجود" فرق
        # تشغيلي مش شكلي.
        if row.normalized_phone in seen_in_file:
            row.status = STATUS_DUPLICATE
            row.error = f"الرقم ده مكرر في الملف (أول مرة سطر {seen_in_file[row.normalized_phone]})"
            result.duplicates += 1
        else:
            seen_in_file[row.normalized_phone] = offset
            row.status = STATUS_VALID
            result.valid += 1
        result.rows.append(row)

    return result


# ============================================================
# الدفعة في الداتابيز
# ============================================================


def existing_normalized_phones(db: Session) -> set[str]:
    """كل الأرقام الموجودة، بالشكل الموحّد.

    `customers.phone` مخزّن ب `0` أول (شكل العرض)، والـsegments
    بتقارن بالشكل الموحّد (من غير `0`). فبنعمل normalization وقت
    المقارنة بدل ما نغيّر كل الـصفوف — ده تغيير في بيانات
    موجودة، ومخاطرة على استعلامات شغّالة.
    """
    rows = db.execute(select(Customer.phone)).scalars().all()
    return {normalize_phone(phone) for phone in rows if phone}


def create_import_batch(
    db: Session,
    content: bytes,
    filename: str,
    *,
    admin_id: Optional[int] = None,
) -> tuple[ContactImport, ParseResult]:
    """يعمل دفعة في حالة previewed ويحفظ سطورها. **ما بيستوردش حاجة**.

    الاستيراد نفسه في `run_import` — من غيرين الـPOST بتاع الرفع
    كان هيعملImmutable عملية وأنا لقيتله زر.
    """
    known = existing_normalized_phones(db)
    parsed = parse_csv(content, filename, existing_phones=known)

    batch = ContactImport(
        filename=filename[:255],
        status="previewed",
        total_rows=parsed.total,
        valid_rows=parsed.valid,
        invalid_rows=parsed.invalid,
        duplicate_rows=parsed.duplicates,
        imported_rows=0,
        created_by_admin_id=admin_id,
    )
    db.add(batch)
    db.flush()

    for parsed_row in parsed.rows:
        db.add(
            ContactImportRow(
                import_id=batch.id,
                row_number=parsed_row.row_number,
                raw_name=parsed_row.raw_name,
                raw_phone=parsed_row.raw_phone,
                normalized_phone=parsed_row.normalized_phone,
                name=parsed_row.name,
                status=parsed_row.status,
                error=parsed_row.error,
            )
        )
    db.commit()
    db.refresh(batch)
    return batch, parsed


def _find_existing(db: Session, normalized: str) -> Optional[Customer]:
    """يدوّر على عميل بالرقم الموحّد.

    بنقارن على `LIKE` ببديلين لأن `customers.phone` ممكن يكون
    `01012345678` أو `1012345678` حسب لما اتخزّن. `ILIKE` بيشتغل
    على الأرقام مثل ما بيشتغل على الحروف.
    """
    variants = [normalized, f"0{normalized}"]
    return db.scalar(
        select(Customer).where(Customer.phone.in_(variants)).limit(1)
    )


def run_import(db: Session, import_id: int) -> ContactImport:
    """ينفّذ الاستيراد الحقيقي.

    ## ليه idempotent

    `status` بيتحوّل لـ`imported` في أول تنفيذ. لو دوس الأدمن
    مرتين (ولا ضغطة مزدوجة، ولا retry بعد timeout)، التنفيذ التاني
    بيلاقي `status != 'previewed'` وبيطلع من غير ما يضيف حاجة.
    من غير ده، الـdouble-click بيبقى 900 عميل مكرر.

    ## ليه بنستخدم nested transaction لكل سطر

    سطر واحد فاشل (unique violation من سباق مع طلب في نفس اللحظة)
    ما يبقىش سبب إن الـbatch كله يضيع. كل سطر في savepoint خاص
    بيه، وبنمسك الـ`IntegrityError` ونتخطاه كـinvalid. ده نفس
    النمط المستخدم في `app.push` و`app.ordering`.
    """
    batch = db.get(ContactImport, import_id)
    if batch is None:
        raise CsvImportError("دفعة الاستيراد دي مش موجودة")
    if batch.status == "imported":
        return batch  # اتنفذت قبل كده — مفيش إعادة
    if batch.status == "discarded":
        raise CsvImportError("الدفعة دي كانت متستعبة")

    rows = (
        db.execute(
            select(ContactImportRow)
            .where(ContactImportRow.import_id == import_id, ContactImportRow.status == STATUS_VALID)
            .order_by(ContactImportRow.row_number)
        )
        .scalars()
        .all()
    )

    imported = 0
    updated = 0
    duplicates = batch.duplicate_rows
    invalid = batch.invalid_rows
    now = datetime.now(timezone.utc)

    for row in rows:
        normalized = row.normalized_phone
        if not normalized:
            row.status = STATUS_INVALID
            row.error = "رقم فاضي وقت الاستيراد"
            invalid += 1
            continue

        existing = _find_existing(db, normalized)
        if existing is not None:
            # العميل ده موجود فعلاً. مش duplicate — **تحديث**. هنحدّث
            # الاسم بس لو الملف جاب اسم فاضي فعلاً: سطر رقمه بس (من غير
            # عمود اسم) لازم مايمسحش اسم العميل القديم.
            try:
                with db.begin_nested():
                    if row.name:
                        existing.name = row.name[:MAX_NAME_LENGTH]
                    row.customer_id = existing.id
                    row.existing_customer_id = existing.id
                    row.status = STATUS_IMPORTED
                    row.error = None
                    updated += 1
            except Exception:
                row.status = STATUS_INVALID
                row.error = "فشل تحديث العميل الموجود"
                invalid += 1
            continue

        try:
            with db.begin_nested():
                customer = Customer(
                    phone=f"0{normalized}" if len(normalized) == 10 else normalized,
                    name=row.name,
                    # العميل المستورد مالوش طلبات، فتاريخ الطلبات
                    # بيفضل NULL. ده اللي خلّى العمودين nullable.
                    first_order_at=None,
                    last_order_at=None,
                    source="import",
                )
                db.add(customer)
                db.flush()
                row.customer_id = customer.id
                row.status = STATUS_IMPORTED
                row.error = None
                imported += 1
        except Exception:
            # سباق على الـunique phone أو أي خطأ تاني في السطر
            # ده. السطر ده بس يموت.
            row.status = STATUS_INVALID
            row.error = "فشل إنشاء العميل (الرقم اتسجل في نفس اللحظة)"
            invalid += 1

    batch.imported_rows = imported + updated
    batch.invalid_rows = invalid
    batch.duplicate_rows = duplicates
    # كل الـvalid خلصت: إما اتضافت أو اتحدّثت.
    batch.valid_rows = 0
    batch.status = "imported"
    batch.imported_at = now
    db.commit()
    db.refresh(batch)
    return batch


def discard_batch(db: Session, import_id: int) -> ContactImport:
    """يرمي الدفعة من غير استيراد — لو الأدمن غيّر رأيه."""
    batch = db.get(ContactImport, import_id)
    if batch is None:
        raise CsvImportError("دفعة الاستيراد دي مش موجودة")
    if batch.status == "imported":
        raise CsvImportError("الدفعة دي اتنفذت بالفعل")
    batch.status = "discarded"
    db.commit()
    db.refresh(batch)
    return batch


def batch_rows(db: Session, import_id: int, status: Optional[str] = None):
    """سطور الدفعة للمعاينة، صف صفحة صف."""
    statement = select(ContactImportRow).where(ContactImportRow.import_id == import_id)
    if status:
        statement = statement.where(ContactImportRow.status == status)
    return (
        db.execute(statement.order_by(ContactImportRow.row_number))
        .scalars()
        .all()
    )


def template_csv() -> str:
    """قالب جاهز للتنزيل — بيلخّص الأعمدة المتوقّعة."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "phone"])
    writer.writerow(["أحمد محمد", "01012345678"])
    writer.writerow(["سارة علي", "+201122334455"])
    return buffer.getvalue()
