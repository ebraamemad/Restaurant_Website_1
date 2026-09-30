"""مزوّد Evolution API — التنفيذ الوحيد اللي فيه سطور HTTP.

## ليه كل HTTP في ملف واحد

الشرط كان صريح: منطق التسويق ما يعرفش Evolution. يعني:
- `service.py` (الـqueue، الإلغاء، المحاولات) فيه **صفر** ذكر لـ
  `evolution` أو `/message/sendText`.
- مفيش endpoint لـEvolution في أي router.
- `EvolutionProvider` هو الملف الوحيد اللي فيه
  `/message/sendText` و`/instance/connect`، وبس.

لو المزوّد اتبدّل، الملف ده هو اللي يتبدّل.

## ليه `httpx`

`httpx` async — الإرسال خلف `asyncio.sleep`، فاحنا بنستخدم
`await` أصلاً. الـtimeout **إجباري** في كل نداء: مزوّد معلّق لازم
يرجع error بعد N ثواني، مش يعلّق الحملة. `httpx.TimeoutException`
بيتحوّل لـ`WhatsAppTransientError` (يعني تُحاول تاني) — دي
السلوك الصح لطلب انقطع في النص.

## تصنيف الأخطاء

الحكم بيكون على **3** محاور بالترتيب:

1. استثناء شبكة (`Timeout`, `ConnectError`, `RemoteProtocolError`)
   → **مؤقت**. قطع في النص معناه الرسالة ممكن تكون اتبعتت.
2. `429` → **مؤقت**. المزوّد بيطلب بطء.
3. `5xx` → **مؤقت**. خطأ المزوّد.
4. `401/403` → **دائم**. المفتاح غلط — إعادة المحاولة هتفشل 5 مرات
   بنفس النتيجة. ده اللي بيحرق الـrate limit بتاع المزوّد.
5. `400/404/422` → **دائم**. غالبًا رقم مش على واتساب أو رسالة
   مرفوضة. (`Evolution` بيرجّع `404` للرقم غير المسجّل.)

## التحذير اللي لازم يتقري

Evolution API **مش تكامل رسمي**. مفيش أي إعداد — ولا أي فاصل
رسايل، ولا أي batch size — بيضمن إن رقمك مش هيتحظر. أي كلام
غير كده كذب.

اللي الكود ده بيعمله فعلاً: **paced sending + opt-out + logging +
إلغاء + retry محدود**. ده بيقلّل الحرق ويحترم الرفض — مش بيمنعه.
لو محتاج ضمان، استخدم مزوّد رسمي.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from .base import (
    ConnectionState,
    ConnectionStatus,
    SendOutcome,
    SendState,
    WhatsAppConfigurationError,
    WhatsAppMessage,
    WhatsAppPermanentError,
    WhatsAppProvider,
    WhatsAppTransientError,
)
from .redact import redact
from .settings import WhatsAppConfig, validate_base_url

logger = logging.getLogger(__name__)


class EvolutionProvider(WhatsAppProvider):
    """ تنفيذ Evolution v2.

    `http_client` بيتحقن عشان الاختبارات تستخدم `httpx.MockTransport`
    بدل network حقيقي — الـtest suite كله بيشتغل من غير اتصال.
    """

    name = "evolution"

    #: مهلة الاتصال بتتحدّد من الـconfig. لما نستبدل المزوّد،
    #: الـURL paths بتتغيّر وهنا بس.
    _SEND_PATH = "/message/sendText/{instance}"
    _FETCH_PATH = "/instance/fetchInstances"
    _CONNECT_PATH = "/instance/connect/{instance}"

    def __init__(
        self,
        config: WhatsAppConfig,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not config.base_url:
            raise WhatsAppConfigurationError("مفيش رابط المزوّد")
        self._config = config
        self._base_url = validate_base_url(
            config.base_url, allow_private=config.allow_private_hosts
        )
        self._headers = {
            "apikey": config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # `__repr__` من الـbase بيكفي — بس الـbase بيقول الاسم بس.
        # هنا بنتحقق: `repr` لازم **مايطبعش** الـbase_url مع الـkey.
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds, connect=config.timeout_seconds)
        )
        self._owns_client = http_client is None

    # ------------------------------------------------------------
    # الأسرار ما تطلع في ال-رِس
    # ------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<EvolutionProvider instance={self._config.instance!r} "
            f"url_host_set={self._host_only()}>"
        )

    def _safe(self, value: Optional[str]) -> Optional[str]:
        """تنضيف **بالمفتاح نفسه** مش بالنمط العام بس.

        `redact()` بيعرف الأشكال الشائعة (`apikey=...`، `Bearer ...`).
        بس المزوّد ممكن يرد برسالة ما على شكلها، أو يرجّع المفتاح
        جوه JSON من غير label. النمط مش ضمانة — تمرير القيمة
        المعروفة هي ضمانة.
        """
        return redact(value, extra=(self._config.api_key or "",))

    def _host_only(self) -> str:
        """اسم النطاق بس — من غير المسار ولا البورت ولا المفتاح."""
        from urllib.parse import urlsplit

        return urlsplit(self._base_url).hostname or "?"

    # ------------------------------------------------------------
    # الاتصال
    # ------------------------------------------------------------

    async def test_connection(self) -> ConnectionStatus:
        """بيفحص الـinstance موجودة ومتّصلة.

        **ما بيرميش أبدًا** — أي استثناء بيتحوّل لـ`ERROR` هنا
        بدل ما يطلع للـcaller ويقتل نص حملة.
        """
        if not self._config.api_key:
            return ConnectionStatus(
                state=ConnectionState.ERROR,
                message="مفيش مفتاح API. حطّه في EVOLUTION_API_KEY على السيرفر.",
                provider=self.name,
                instance=self._config.instance or None,
            )
        try:
            instances = await self._fetch_instances()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — عايزين النتيجة مش الرمي
            logger.warning(
                "whatsapp_connection_test_failed provider=evolution host=%s",
                self._host_only(),
                exc_info=True,
            )
            return ConnectionStatus(
                state=ConnectionState.ERROR,
                message=self._safe(str(exc)) or "فشل الاتصال بالمزوّد",
                provider=self.name,
                instance=self._config.instance or None,
            )

        match = next(
            (
                item
                for item in instances
                if isinstance(item, dict)
                and str(item.get("name") or "") == self._config.instance
            ),
            None,
        )
        if match is None:
            return ConnectionStatus(
                state=ConnectionState.DISCONNECTED,
                message=f"الـinstance '{self._config.instance}' مش موجودة عند المزوّد",
                provider=self.name,
                instance=self._config.instance,
            )
        state = str(match.get("connectionState") or match.get("status") or "").lower()
        if state in {"open", "connected", "online"}:
            return ConnectionStatus(
                state=ConnectionState.CONNECTED,
                message="متّصلة",
                provider=self.name,
                instance=self._config.instance,
            )
        return ConnectionStatus(
            state=ConnectionState.DISCONNECTED,
            message=f"الـinstance موجودة بس {state or 'غير متصلة'}. افتح الـQR من لوحة المزوّد.",
            provider=self.name,
            instance=self._config.instance,
        )

    async def _fetch_instances(self) -> list[dict]:
        response = await self._client.get(
            f"{self._base_url}{self._FETCH_PATH}", headers=self._headers
        )
        self._raise_for_status(response, context="fetchInstances")
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        # شكل `{ "instances": [...] }` — بتلقاه في بعض الفوركات.
        if isinstance(payload, dict):
            for key in ("instances", "data", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    # ------------------------------------------------------------
    # الإرسال
    # ------------------------------------------------------------

    async def send_text(self, message: WhatsAppMessage) -> SendOutcome:
        """يبعت رسالة وحدة. **ما بيرميش** — راجع `WhatsAppProvider`."""
        try:
            payload = {
                "number": message.to,
                "text": message.body,
            }
            if message.reference:
                payload["options"] = {"references": message.reference}
            response = await self._client.post(
                f"{self._base_url}{self._SEND_PATH.format(instance=self._config.instance)}",
                headers=self._headers,
                json=payload,
            )
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            # انقطع في النص — الرسالة ممكن تكون اتبعتت عند المزوّد
            # ومعرفنا الرد. دي بالظبط حالة الـ`SKIPPED` القابلة
            # لإعادة المحاولة: مش فشل نهائي (الشبكة هترجع)، ومش
            # نجاح مؤكد (الرسالة يمكن اتبعتت مرتين).
            logger.warning(
                "whatsapp_send_network_error instance=%s error=%s",
                self._config.instance,
                type(exc).__name__,
            )
            return SendOutcome(
                state=SendState.SKIPPED,
                provider_status="network_error",
                error=f"انقطاع شبكة ({type(exc).__name__})",
                retryable=True,
            )
        except httpx.HTTPError as exc:
            return SendOutcome(
                state=SendState.FAILED,
                error=self._safe(f"خطأ HTTP: {type(exc).__name__}"),
            )

        return self._outcome_from(response)

    def _outcome_from(self, response: httpx.Response) -> SendOutcome:
        """يرجّع `SendOutcome` من رد HTTP. **محاولة واحدة، حالة واحدة.**

        الدالة دي مقصودة إنها **ما ترميش**: كل المسارات بترجّع
        `SendOutcome`. الـ`retryable` حقل صريح في النتيجة، مش نص
        بيتبحث فيه — عشان `service.py` يقرّر إعادة المحاولة من
        حقل مش من مقارنة نصوص.
        """
        status_code = response.status_code
        body = _safe_json(response)
        provider_status = _provider_status(body)
        summary = _summarize(body)

        if status_code < 400:
            if provider_status in {"PENDING", "SENT", "DELIVERY_ACKNOWLEDGED", ""}:
                return SendOutcome(
                    state=SendState.SENT,
                    provider_message_id=_message_id(body),
                    provider_status=provider_status or "PENDING",
                    http_status=status_code,
                    raw=self._safe(summary),
                )
            return SendOutcome(
                state=SendState.FAILED,
                provider_message_id=_message_id(body),
                provider_status=provider_status,
                http_status=status_code,
                error=self._safe(summary) or "المزوّد رفض الرسالة",
                raw=self._safe(summary),
            )

        if status_code == 429:
            # مؤقت: المزوّد طلب إبطاء. `retryable=True` هي اللي بتخلي
            # `service.py` يعيد المحاولة بعد الفاصل.
            return SendOutcome(
                state=SendState.SKIPPED,
                provider_status=provider_status or "rate_limited",
                http_status=status_code,
                error="429 — المزوّد طلب إبطاء",
                raw=self._safe(summary),
                retryable=True,
            )
        if status_code >= 500:
            return SendOutcome(
                state=SendState.SKIPPED,
                provider_status=provider_status or "server_error",
                http_status=status_code,
                error=f"{status_code} — خطأ المزوّد",
                raw=self._safe(summary),
                retryable=True,
            )
        if status_code in (401, 403):
            # دائم: المفتاح غلط. 5 محاولات هنا = 5 محاولات فاشلة على
            # نفس المفتاح وبنفس النتيجة.
            return SendOutcome(
                state=SendState.FAILED,
                provider_status=provider_status or "unauthorized",
                http_status=status_code,
                error=self._safe(summary) or "المزوّد رفض المفتاح",
                raw=self._safe(summary),
            )
        return SendOutcome(
            state=SendState.FAILED,
            provider_status=provider_status or "rejected",
            http_status=status_code,
            error=self._safe(summary) or "الرقم أو الرسالة مرفوضة",
            raw=self._safe(summary),
        )

    def _raise_for_status(self, response: httpx.Response, *, context: str) -> None:
        """للاستعلامات غير لقناة الإرسال — بيرمي استثناء حقيقي."""
        if response.status_code < 400:
            return
        summary = self._safe(_summarize(_safe_json(response)) or response.text)
        if response.status_code in (429,) or response.status_code >= 500:
            raise WhatsAppTransientError(
                summary or f"{context}: {response.status_code}",
                retryable=True,
                status_code=response.status_code,
            )
        raise WhatsAppPermanentError(
            summary or f"{context}: {response.status_code}",
            retryable=False,
            status_code=response.status_code,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ============================================================
# قراءة الردود
# ============================================================


def _safe_json(response: httpx.Response) -> dict | list:
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, (dict, list)) else {}


def _provider_status(body: dict | list) -> str | None:
    if isinstance(body, dict):
        for key in ("status", "state"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value.upper()
        key_obj = body.get("key")
        if isinstance(key_obj, dict):
            remote = key_obj.get("remoteJid") or key_obj.get("id")
            if isinstance(remote, str) and remote:
                return "ACCEPTED"
    return None


def _message_id(body: dict | list) -> str | None:
    if not isinstance(body, dict):
        return None
    key = body.get("key")
    if isinstance(key, dict):
        message_id = key.get("id")
        if isinstance(message_id, str) and message_id:
            return message_id
    message_id = body.get("messageId")
    return message_id if isinstance(message_id, str) and message_id else None


def _summarize(body: dict | list) -> str | None:
    """نص قصير وmeaningful من رد المزوّد — للعرض في سجل المحاولات.

    **بينضّف قبل ما يرجع** (المزوّد ممكن يبعت الـkey في `response`).
    """
    if isinstance(body, dict):
        for key in ("message", "error", "response", "statusMessage", "reason"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
            if isinstance(value, (dict, list)):
                return str(value)[:300]
        if not body:
            return None
        return str(body)[:300]
    if isinstance(body, list) and not body:
        return None
    return str(body)[:300] if body else None