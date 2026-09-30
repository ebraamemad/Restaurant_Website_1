"""قناة واتساب — طبقة المزوّد والقناة كلها في حزمة واحدة.

الحزمة دي **حدود الإرسال**. اللي برّا منها (منطق الحملات، الـrouters،
الـtemplates) بيعرف `app.marketing` و`app.whatsapp` بس، ومفيش سطر
فيه `httpx` ولا رابط مزوّد.

```
app/routers/marketing.py ─┐
                         ├─> app/whatsapp/service.py ─> WhatsAppProvider
app/marketing.py ─────────┘         (queue + retries)      (contract)
                                                          |
                            app/whatsapp/evolution.py <────┘
                            (the only file with HTTP)
```

## الملفات

- `base.py` — العقد والحالات والأخطاء. مفيش فيه I/O خالص.
- `evolution.py` — التنفيذ الوحيد اللي فيه سطور HTTP.
- `settings.py` — دمج الإعدادات، وفحص الـURL (SSRF)، والقناع.
- `redact.py` — تنضيف الردود قبل التخزين أو العرض.

`redact` **مش** مُصدَّر من `__init__` عن قصد: تصدير دالة اسمها
زي اسم الوحدة بيخفي `app.whatsapp.redact` ورا الدالة، وأي
`from app.whatsapp import redact` بعد كده هيلاقي دالة مش وحدة.
الوحدة تُستورد بمسارها الكامل.
- `service.py` — الطابور والإرسال والإلغاء وسجل المحاولات.

## التحذير اللي لازم يتقرأ قبل أي استخدام

`Evolution API` **مش API رسمي**. مفيش في الكود ده حاجة — ولا فاصل
رسايل، ولا batch، ولا احترام `429` — بتضمن إن رقمك مش هيتحظر.
اللي بنعمله: إرسال متباعد، احترام رفض الإذن، log كامل، وإلغاء
فوري. لو محتاج ضمان، استخدم مزوّد رسمي.

## الإرسال الفعلي

`service.run_campaign` **async**. من كود sync ناديه بـ
`service.run_campaign_sync` أو `await` مباشرة.
"""

from __future__ import annotations

from .base import (
    ConnectionState,
    ConnectionStatus,
    SendOutcome,
    SendState,
    WhatsAppConfigurationError,
    WhatsAppMessage,
    WhatsAppPermanentError,
    WhatsAppProvider,
    WhatsAppProviderError,
    WhatsAppTransientError,
)
from .redact import mask_secret
from .settings import WhatsAppConfig, load_config, validate_base_url

__all__ = [
    "ConnectionState",
    "ConnectionStatus",
    "SendOutcome",
    "SendState",
    "WhatsAppConfig",
    "WhatsAppConfigurationError",
    "WhatsAppMessage",
    "WhatsAppPermanentError",
    "WhatsAppProvider",
    "WhatsAppProviderError",
    "WhatsAppTransientError",
    "load_config",
    "mask_secret",
    "validate_base_url",
]