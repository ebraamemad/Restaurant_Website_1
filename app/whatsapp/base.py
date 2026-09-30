"""عقد مزوّد واتساب — الطبقة اللي كل حاجة التانية بتتكلم معاها.

## ليه abstraction أصلاً

الطلب بيقول إن منطق التسويق يتكلّم مع المزوّد، مش مع Evolution.، وليس معناه نكسر البنية.
مangkalوش arquitectura — ده **السبب** إن `Evolution API` مش API رسمي:
هو تكامل غير رسمي، وممكن يتغيّر، وممكن نستبدله بمزوّد رسمي بعدين.
قاعدة Business logic بتعرف `WhatsAppProvider` وخلاص: لو المزوّد اتبدّل
من Evolution لـofficial Cloud API، الملف الوحيد اللي بيتغيّر هو
`evolution.py`، و`service.py` (الـqueue والإرسال والإلغاء) مش هتعرف
إن في تغيير حصل أصلاً.

```
Campaign → Audience → Sending Queue → WhatsAppProvider → (Evolution | Official | …)
                            ↑                    ↑
              مش بتعرف المزوّد          سطر HTTP واحد في ملف واحد بس
```

## ليه الأخطاء متقسّمة transient / permanent

المفهوم الحقيقي: **إعادة المحاولة على الدايم = إساءة**. مزوّد بيقول
"الرقم ده مش على واتساب" مش هينفع معاه 5 محاولات — النتيجة هتكون
نفس الفشل 5 مرات بس. على الضد كده "الاتصال اتقطع" أو "429" بتستاهل
محاولة تانية.

فالقسمة دي **بتقلّل الشغل الضائع**، مش بتزوده: كل subclass بيقول
صراحةً قصده إيه. المزوّد الجديد لازم يصنّف، ولو صنّف غلط
(permanent كـtransient) هيبقى بطيء بس مش مخطئ — والعكس (transient
الترشيح متحفّظ عمداً في اتجاه الإهام: ما تضيّع رسائل بسطًا.

## ليه `SendOutcome` مش `bool`

لأن الأدمن محتاج يعرف **ليه** فشل، وبيحتاج يفرق بين "الرقم غلط"
(يتصلح ببيانات العميل) و"الخدمة وقعت" (تستاهل إعادة). `bool` بيمسح
المعلومة دي. `status` هيبقى نص حر مش تعداد، عشان كل مزوّد بيرمّز
أخطاءه بنفسه.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================================
# الحالات
# ============================================================


class ConnectionState(str, Enum):
    """نتيجة اختبار الاتصال — التلات قيم اللي الـUI بيعرضهم."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class SendState(str, Enum):
    """نتيجة رسالة واحدة.

    `SKIPPED` مش `FAILED`: المزوّد قال "مش دلوقتي" (الinstance مقفولة،
    العميل مش متاح دلوقتي). ده مش خطأ في شغلنا، والخلط بينه وبين
    الفشل بيبعّد أرقام التقرير.
    """

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


# ============================================================
# الأخطاء
# ============================================================


class WhatsAppProviderError(Exception):
    """خطأ من المزوّد. الأساس لكل الباقي."""

    #: هل تستحق إعادة محاولة؟ الافتراضي **لا** — الترشيح متحفّظ.
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: Optional[int] = None,
        provider_status: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.provider_status = provider_status


class WhatsAppTransientError(WhatsAppProviderError):
    """فشل مؤقت — مهلة الاتصال، 429، 5xx، انقطاع اتصال.

    دول وحدهم اللي بياخدوا إعادة محاولة. `max_retries` بيحدّ الأرقام
    عشان ما ندخلش في loop لا نهائي.
    """

    retryable = True


class WhatsAppPermanentError(WhatsAppProviderError):
    """فشل نهائي — رقم مش موجود، محظور، رسالة مرفوضة.

    إعادة المحاولة هنا = استنزاف بلا فايدة. بيتسجّل فوراً كـ`failed`
    وخلاص.
    """

    retryable = False


class WhatsAppConfigurationError(WhatsAppProviderError):
    """الإعدادات ناقصة أو الـURL مرفوض — مش مشكلة في المزوّد.

    منفصل عن الباقي عشان الـUI يعرض "ظبّط الإعدادات" مش "فشل إرسال".
    """


# ============================================================
# القيم
# ============================================================


@dataclass(frozen=True)
class ConnectionStatus:
    """نتيجة اختبار الاتصال. **من غير أي سر.**

    `message` لازم يكون آمن للعرض: المزوّد بيبعت أحياناً الـURL أو
    الـkey في رسالة الخطأ، و`redact()` بتنضّفها قبل ما تتخزّن.
    """

    state: ConnectionState
    message: str = ""
    #: اسم المزوّد اللي اتجرّب — للعرض فقط.
    provider: str = ""
    instance: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.state is ConnectionState.CONNECTED


@dataclass(frozen=True)
class SendOutcome:
    """نتيجة رسالة واحدة لمرة واحدة.

    `raw` هو رد المزوّد **بعد التنضيف** — بيتخزّن في سجل المحاولات
    عشان الأدمن يشوف السبب الحقيقي. ممنوع أي سر يوصل هنا.
    """

    state: SendState
    provider_message_id: Optional[str] = None
    #: رمز المزوّد (`"PENDING"`, `"invalid_number"` …) — حر، مش تعداد.
    provider_status: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    raw: Optional[str] = None
    #: **هل الفشل مؤقت؟** ده اللي بيقرّر إعادة المحاولة في
    #: `service.py` — مش تحليل نص `error`. حقل صريح عشان نص
    #: الخطأ بيتغيّر مع كل نسخة مزوّد، والحقل مش.
    retryable: bool = False

    @property
    def sent(self) -> bool:
        return self.state is SendState.SENT

    @property
    def failed(self) -> bool:
        return self.state is SendState.FAILED

    @property
    def skipped(self) -> bool:
        return self.state is SendState.SKIPPED


@dataclass(frozen=True)
class WhatsAppMessage:
    """الرسالة الجاهزة للإرسال.

    `to` لازم يكون **موحّد** (10 أرقام، من غير `+` ولا صفر أول) —
    التطبيع بيحصل قبل الوصول هنا، في service.py.
    الرقم من الداتابيز مش من الـstring، فتنسيق شبه عشوائي مفيش
    معنى.
    """

    to: str
    body: str
    #: مفتاح تفرّد — المزوّد بيرجّعه مع الرد، وبيدفع تكرار الإرسال.
    reference: Optional[str] = None


# ============================================================
# العقد
# ============================================================


class WhatsAppProvider(abc.ABC):
    """كل مزوّد لازم يوفّر تلات حاجات: يتوصّل، يبعت، يقول اسمه.

    **مفيش** `settings` أو `db` في التوقيع عمداً: المزوّد لازم يبقى
    كائن HTTP نقي يتأسّل من إعدادات، عشان نقدر نعمل له test بـfake
    من غير database ومن غير network. حاجة تربطه بالداتابيز
    (`service.py`) برّا الشاشة دي.
    """

    #: اسم ثابت للعرض والتقارير. بيتخزّن مع كل محاولة عشان لو
    #: لو الأدمن غيّر المزوّد بعد كده، السجل القديم يفضل يقول إنه كان Evolution وقتها.
    #: اسم المزوّد مخزن مع كل محاكة.
    name: str = "abstract"

    @abc.abstractmethod
    async def test_connection(self) -> ConnectionStatus:
        """يفحص إن المزوّد شغال والinstance متاحة.

        لازم **ما يرميش** — أي استثناء بيتحوّل لـ`ERROR` في
        `service.py`. المزوّد المسجّل بيرمي استثناء = شيل encoding
        على الاتجاه المحافظ.
        """

    @abc.abstractmethod
    async def send_text(self, message: WhatsAppMessage) -> SendOutcome:
        """يبعت رسالة واحدة.

        لازم **ما يرميش**. أي استثناء = `FAILED` + سبب
        `provider_raised`. السطر ده مش تجميل: المزوّد بيرمي في نص
        حملة = 900 رسالة ضايعة لو الاستثناء طلع للـcaller.
        """

    async def aclose(self) -> None:
        """ينضّف الاتصال. للـoverride — الأساس مش بيعمل حاجة."""

    def __repr__(self) -> str:
        # **من غير الإعدادات.** `__repr__` بيروح للوج والـtraceback،
        # والـtraceback ده بيتسجّل. طباعة الـapi_key هنا تسريب.
        return f"<{type(self).__name__} name={self.name!r}>"