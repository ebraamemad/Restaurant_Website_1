"""اختبارات قناة واتساب — Phase 5.

## الترتيب

- **العقد والتنضيف** (`base`, `redact`) — السر **الشغلة الحرة** بيطلع.
- **الإعدادات** (`settings`) — الدمج، وفحص الـURL (SSRF)، والقناع.
- **المزوّد** (`evolution`) — كل الحالات اللي بترجّعها، مع
  `httpx.MockTransport` — **من غير أي اتصال حقيقي**.
- **الخدمة** (`service`) — الطابور، الإلغاء، الرفض، إعادة المحاولة.
- **التكامل** (`marketing`) — حملة `whatsapp` و`both` من الطرف للطرف.

## ليه المزوّد المزيف بدل Evolution حقيقي

مفيش Evolution شغال على جهاز الاختبار، ومفيش phones. الاختبار
اللي محتاج شبكة حقيقي بيبقى اختبار تكامل مش اختبار وحدة، وهو
بيكسر لما الشبكة بتتذبذب. `FakeProvider` بيختبر **منطق decisions**
بتايعنا، و`MockTransport` بيختبر **تحليل ردود المزوّد** — والاتنين
بيلمسوا السطح اللي مهم.

## ليه السر بيتفحص صريح

`redact` و`mask_secret` و`__repr__` — التلاتة مع بعض بتمنع
تسريب المفتاح. اختبار واحد بيلمسهم بالترتيب ده **مقصود**: لو
`t_a` بيحطّي المفتاح في الـoutcome و`t_b` بيحطّه في الـrepr،
و`t_c` بيحطّه في الإعدادات المعروضة، التلاتة من غير بعض، كل
اختبار يعدّي والسر بيطلع فعلاً. الاختبار المفرد بيلمسهم مع بعض
بيقول "مفيش مسار بيطلع السر".

## التحذير

`Evolution API` غير رسمي. مفيش حاجة في الاختبارات دي — ولا في
الكود — بتضمن إن رقم حقيقي مش هيتحظر. اللي بنختبره: إنه **ما
بنكسرشاش قاعدة**، وإحنا مش بنوعد بحاجة مش قادر نوفّرها.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app import marketing
from app.config import settings
from app.models import (
    Campaign,
    CampaignRecipient,
    Customer,
    PushSubscription,
    WhatsAppAttempt,
    WhatsAppConfigRow,
)
from app.whatsapp import base, redact, service, settings as wa_settings
from app.whatsapp.evolution import EvolutionProvider

SECRET = "super-secret-key-abc123"


# ============================================================
# الـfixtures
# ============================================================


@pytest.fixture()
def admin_session(client, db, admin_user):
    """جلسة أدمن + CSRF.

    نسخة من `test_marketing.py`. كاتب:
    تكرار فياخر ملف محجماعة، وإلا أخر
    إجراء يناسبها للمحنى.
    """
    from app.security import (
        AUTH_COOKIE_NAME,
        CSRF_COOKIE_NAME,
        create_access_token,
        create_csrf_token,
    )

    token = create_access_token(admin_user.username)
    csrf = create_csrf_token()
    client.cookies.set(AUTH_COOKIE_NAME, token)
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    client.headers["X-CSRF-Token"] = csrf
    return client


@pytest.fixture()
def env_keys(monkeypatch):
    """مفاتيح واتساب من الـenvironment، وبترجعها بعد كل اختبار."""
    monkeypatch.setattr(settings, "whatsapp_enabled", True)
    monkeypatch.setattr(settings, "evolution_base_url", "http://127.0.0.1:8080")
    monkeypatch.setattr(settings, "evolution_instance", "test-instance")
    monkeypatch.setattr(settings, "evolution_api_key", SECRET)
    monkeypatch.setattr(settings, "whatsapp_allow_private_hosts", True)
    monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "")
    return monkeypatch


@pytest.fixture()
def config_row(db):
    """صف الإعدادات المفعّل — **المبذور من الـmigration**، مش صف جديد.

    الاختيار ده مقصود: لو الـfixture يعمل `INSERT` بــ`id=1` كان
    هيقع على unique violation ويبخّي إن الـmigration مبذورة
    أصلاً. هنا بنكتب فوق الصف الموجود، فأي كسر في البذرة بيظهر
    كـ`NoneType` مش كـduplicate key.
    """
    row = db.get(WhatsAppConfigRow, 1)
    assert row is not None, "الـmigration لازم تبذّر صف الإعدادات"
    row.enabled = True
    row.sending_enabled = True
    row.base_url = "http://127.0.0.1:8080"
    row.instance = "test-instance"
    row.message_interval_ms = 0
    row.batch_size = 50
    row.max_retries = 2
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def ready_config(env_keys, config_row, db):
    return wa_settings.load_config(db)


@pytest.fixture()
def make_customer(db):
    counter = {"n": 0}

    def _make(*, phone=None, name="عميل", marketing_opt_out=False, subscribed=False):
        counter["n"] += 1
        phone = phone or f"0109{counter['n']:07d}"
        customer = Customer(
            phone=phone,
            name=name,
            source="order",
            marketing_opt_out=marketing_opt_out,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        if subscribed:
            db.add(
                PushSubscription(
                    endpoint=f"https://fcm.googleapis.com/fcm/send/t-{counter['n']}",
                    p256dh="k",
                    auth="a",
                    customer_id=customer.id,
                    status="active",
                )
            )
            db.commit()
        return customer

    return _make


def _make_campaign(db, *, channel="whatsapp", message="خصم 20%"):
    return marketing.create_campaign(
        db,
        name="حملة واتساب",
        title="خصم",
        message=message,
        audience_type="all",
        channel=channel,
    )


class FakeProvider(base.WhatsAppProvider):
    """مزوّد مزيف بيسجّل نداءاته.

    `outcomes` قائمة Outcomes بالتتابع، والنداء الأخير بيتكرر لو
    خلصت. ده بيخلّي اختبار "فشل ثم نجح" سطرين بدل كود معقّد.
    """

    name = "fake"

    def __init__(self, outcomes=None, *, raises=False):
        self._outcomes = list(outcomes or [base.SendOutcome(state=base.SendState.SENT)])
        self._raises = raises
        self.sent: list[base.WhatsAppMessage] = []
        self.connection_calls = 0

    async def test_connection(self) -> base.ConnectionStatus:
        self.connection_calls += 1
        return base.ConnectionStatus(
            state=base.ConnectionState.CONNECTED, message="ok", provider=self.name
        )

    async def send_text(self, message: base.WhatsAppMessage) -> base.SendOutcome:
        self.sent.append(message)
        if self._raises:
            raise RuntimeError("المزوّد رمي")
        if len(self._outcomes) > 1:
            return self._outcomes.pop(0)
        return self._outcomes[0]

    async def aclose(self) -> None:
        return None


async def _no_sleep(seconds: float) -> None:
    """بديل `asyncio.sleep` — الاختبارات بتستنى صفر."""
    return None


async def _run(db, campaign, provider, **kwargs):
    return await service.run_campaign(
        db, campaign, provider=provider, sleep=_no_sleep, **kwargs
    )


# ============================================================
# العقد والتنضيف
# ============================================================


class TestRedaction:
    def test_redact_removes_the_actual_key(self):
        assert SECRET not in redact.redact(f"apikey={SECRET} rejected")

    def test_redact_covers_a_bearer_header(self):
        """النمط العام بيمسح الأشكال الشائعة. الضمانة الحقيقية في
        `EvolutionProvider`: بيمرّر المفتاح نفسه في `extra`."""
        assert SECRET not in redact.redact(f"Authorization: Bearer {SECRET}")

    def test_mask_secret_keeps_only_the_tail(self):
        masked = redact.mask_secret(SECRET)
        assert SECRET not in masked
        assert masked.endswith("c123")
        assert SECRET[-4:] in masked

    def test_mask_secret_handles_short_values(self):
        masked = redact.mask_secret("abc")
        assert masked == "•" * 3
        assert "abc" not in masked

    def test_provider_repr_never_holds_the_key(self, ready_config):
        provider = EvolutionProvider(ready_config)
        rendered = repr(provider)
        assert SECRET not in rendered
        assert "Evolution" in rendered
        assert "127.0.0.1" in rendered

    def test_config_safe_dict_hides_the_key(self, ready_config):
        payload = ready_config.safe_dict()
        rendered = repr(payload)
        assert SECRET not in rendered
        assert payload["has_api_key"] is True
        assert payload["api_key_masked"]

    def test_safe_dict_is_the_only_display_path(self, ready_config):
        """مفيش أي مسار تاني بيرجّع السر. الفحص هنا على شكل
        الكائن نفسه مش على سلوك — لو حد زاد `api_key` للـdict،
        الاختبار بيفشل فوراً."""
        fields = set(ready_config.safe_dict())
        assert "api_key" not in fields


# ============================================================
# الإعدادات
# ============================================================


class TestSettings:
    def test_env_key_never_comes_from_the_database_row(self, env_keys, config_row, db):
        config = wa_settings.load_config(db)
        assert config.api_key == SECRET

    def test_config_row_has_no_key_column(self):
        columns = {c.name for c in WhatsAppConfigRow.__table__.columns}
        assert "api_key" not in columns

    def test_not_ready_without_the_env_switch(self, config_row, db, monkeypatch):
        monkeypatch.setattr(settings, "whatsapp_enabled", False)
        monkeypatch.setattr(settings, "evolution_api_key", SECRET)
        # Evolution محلي = استثناء معلن، مش افتراضي.
        monkeypatch.setattr(settings, "whatsapp_allow_private_hosts", True)
        config = wa_settings.load_config(db)
        assert config.ready is False
        with pytest.raises(base.WhatsAppConfigurationError):
            config.assert_sendable()

    def test_not_ready_without_a_key(self, env_keys, config_row, db, monkeypatch):
        monkeypatch.setattr(settings, "evolution_api_key", None)
        config = wa_settings.load_config(db)
        assert config.ready is False

    def test_not_ready_when_sending_switch_is_off(self, env_keys, config_row, db):
        config_row.sending_enabled = False
        db.commit()
        assert wa_settings.load_config(db).ready is False

    def test_ready_when_everything_is_in_place(self, ready_config):
        assert ready_config.ready is True

    def test_row_overrides_the_env_url(self, env_keys, config_row, db):
        config_row.base_url = "http://127.0.0.1:9999"
        db.commit()
        assert wa_settings.load_config(db).base_url.endswith(":9999")

    def test_pacing_is_clamped_not_trusted(self, env_keys, config_row, db):
        config_row.message_interval_ms = 10_000_000
        config_row.batch_size = 5_000
        config_row.max_retries = 99
        db.commit()
        config = wa_settings.load_config(db)
        assert config.message_interval_ms == wa_settings.MAX_INTERVAL_MS
        assert config.batch_size == wa_settings.MAX_BATCH_SIZE
        assert config.max_retries == wa_settings.MAX_RETRIES


class TestBaseUrlValidation:
    @pytest.mark.parametrize(
        "url",
        ["", "   ", "ftp://example.com", "not-a-url", "http://user:pass@example.com"],
    )
    def test_rejects_unusable_urls(self, url):
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url(url)

    def test_rejects_query_and_fragment(self):
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://example.com/?a=b")

    def test_rejects_private_hosts_when_locked(self):
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://127.0.0.1:8080", allow_private=False)

    def test_rejects_localhost_when_locked(self):
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://localhost:8080", allow_private=False)

    def test_allows_public_ip_when_locked(self):
        """IP عام حقيقي. الفحص هنا **من غير DNS** — اسم نطاق بيحتاج
        شبكة، والاختبار اللي بيعتمد على شبكة بيبقى اختبار تكامل."""
        assert (
            wa_settings.validate_base_url("https://93.184.216.34", allow_private=False)
            == "https://93.184.216.34"
        )

    def test_allowlist_entry_overrides_the_private_block(self, monkeypatch):
        """`WHATSAPP_ALLOWED_HOSTS` هو الاستثناء الموثّق للّ.internal
        — من غيره، السيرفر بيداوي Evolution جوه شبكة داخلية."""
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "127.0.0.1")
        assert (
            wa_settings.validate_base_url("http://127.0.0.1:8080", allow_private=False)
            == "http://127.0.0.1:8080"
        )

    def test_unresolvable_host_is_rejected_when_locked(self):
        """فشل الـDNS = "مش هنقدر نثبت إنه آمن" = رفض.

        `.invalid` نطاق ما بيحلّش أبداً حسب RFC 2606، فالرفض هنا
        حتمي ومش بيعتمد على حالة الشبكة.
        """
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://evo.invalid", allow_private=False)

    def test_strips_trailing_slash(self):
        """IP عام عشان الفحص يبقى حتمي ومن غير شبكة."""
        assert (
            wa_settings.validate_base_url("https://93.184.216.34/")
            == "https://93.184.216.34"
        )

    def test_rejects_unexpected_port(self):
        """المنفذ الغريب = service جوه الشبكة الداخلية."""
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("https://93.184.216.34:8443", allow_private=False)

    def test_accepts_explicit_https_port(self):
        assert (
            wa_settings.validate_base_url("https://93.184.216.34:443", allow_private=False)
            == "https://93.184.216.34:443"
        )

    def test_rejects_garbage_port(self):
        """منفذ مش رقم لازم يرمي، مش يتجاهل بصمت."""
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("https://93.184.216.34:abc", allow_private=False)

    @pytest.mark.parametrize(
        "url",
        [
            "http://10.0.0.5",
            "http://192.168.1.10:8080",
            "http://169.254.169.254",
            "http://[::1]:8080",
            "http://[fd00::1]",
            "http://0.0.0.0",
        ],
    )
    def test_rejects_private_reserved_and_metadata_ips(self, url):
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url(url, allow_private=False)

    def test_allow_private_does_not_open_the_whole_private_space(self):
        """الاستثناء للتطوير = loopback بس، مش كل الشبكة الداخلية.

        ده أهم سطر في الملف: `allow_private=True` كان بي.skip كل
        الفحص، فأي حد يقدر يوجّه السيرفر لـ`169.254.169.254`.
        """
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://169.254.169.254", allow_private=True)
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://10.0.0.5", allow_private=True)

    def test_allow_private_still_allows_loopback(self, monkeypatch):
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "")
        assert (
            wa_settings.validate_base_url("http://127.0.0.1:8080", allow_private=True)
            == "http://127.0.0.1:8080"
        )

    def test_allow_private_is_ignored_in_production(self, monkeypatch):
        """في production الاستثناء المحلي ما يعملش أصلاً."""
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "app_secret_key", "x" * 40)
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "")
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("https://127.0.0.1:8080", allow_private=True)

    def test_wildcard_in_allowlist_is_rejected(self, monkeypatch):
        """`*` كان هيفتح الشبكة كلها. مرفوض حتى لو اتقصد."""
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "*.example.com")
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("https://93.184.216.34", allow_private=False)

    def test_allowlist_blocks_everything_else(self, monkeypatch):
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "evo.example.com")
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("https://93.184.216.34", allow_private=False)

    def test_allowlist_entry_allows_internal_host(self, monkeypatch):
        """الاستثناء الموثّق: Evolution جوّه شبكة داخلية."""
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "evo.internal")
        assert (
            wa_settings.validate_base_url("http://evo.internal:8080", allow_private=False)
            == "http://evo.internal:8080"
        )

    def test_host_matching_ignores_case_and_trailing_dot(self, monkeypatch):
        monkeypatch.setattr(settings, "whatsapp_allowed_hosts", "evo.example.com")
        assert (
            wa_settings.validate_base_url("https://EVO.Example.COM./", allow_private=False)
            == "https://EVO.Example.COM."
        )

    def test_dot_local_and_dot_internal_are_rejected(self):
        for url in ("http://evo.local:8080", "http://printer.internal"):
            with pytest.raises(base.WhatsAppConfigurationError):
                wa_settings.validate_base_url(url, allow_private=True)

    def test_http_rejected_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "app_secret_key", "x" * 40)
        with pytest.raises(base.WhatsAppConfigurationError):
            wa_settings.validate_base_url("http://evo.example.com", allow_private=True)


class TestProviderSelection:
    def test_unknown_provider_raises_instead_of_falling_back(self, ready_config):
        broken = wa_settings.WhatsAppConfig(
            **{**ready_config.__dict__, "provider": "official-cloud"}
        )
        with pytest.raises(base.WhatsAppConfigurationError):
            service.build_provider(broken)

    def test_evolution_is_built_by_name(self, ready_config):
        assert isinstance(service.build_provider(ready_config), EvolutionProvider)


# ============================================================
# المزوّد
# ============================================================


def _provider_with(ready_config, handler) -> EvolutionProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return EvolutionProvider(ready_config, http_client=client)


class TestConnectionTest:
    def test_connected_when_the_instance_is_open(self, ready_config):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"name": "test-instance", "connectionState": "open"},
                ],
            )

        status = asyncio.run(_provider_with(ready_config, handler).test_connection())
        assert status.state is base.ConnectionState.CONNECTED
        assert status.ok is True

    def test_disconnected_when_the_instance_is_closed(self, ready_config):
        def handler(request):
            return httpx.Response(
                200,
                json=[{"name": "test-instance", "connectionState": "close"}],
            )

        status = asyncio.run(_provider_with(ready_config, handler).test_connection())
        assert status.state is base.ConnectionState.DISCONNECTED
        assert status.ok is False

    def test_missing_instance_is_not_an_error(self, ready_config):
        def handler(request):
            return httpx.Response(200, json=[{"name": "other"}])

        status = asyncio.run(_provider_with(ready_config, handler).test_connection())
        assert status.state is base.ConnectionState.DISCONNECTED

    def test_network_failure_becomes_error_not_exception(self, ready_config):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        status = asyncio.run(_provider_with(ready_config, handler).test_connection())
        assert status.state is base.ConnectionState.ERROR

    def test_401_message_never_leaks_the_key(self, ready_config):
        def handler(request):
            return httpx.Response(401, json={"message": f"invalid apikey {SECRET}"})

        status = asyncio.run(_provider_with(ready_config, handler).test_connection())
        assert status.state is base.ConnectionState.ERROR
        assert SECRET not in status.message

    def test_no_key_reports_configuration_not_error(self, ready_config, monkeypatch):
        empty = wa_settings.WhatsAppConfig(**{**ready_config.__dict__, "api_key": ""})
        status = asyncio.run(EvolutionProvider(empty).test_connection())
        assert status.state is base.ConnectionState.ERROR
        assert "EVOLUTION_API_KEY" in status.message


class TestSendOutcome:
    def _outcome(self, ready_config, response):
        def handler(request):
            return response

        return asyncio.run(
            _provider_with(ready_config, handler).send_text(
                base.WhatsAppMessage(to="201012345678", body="hi")
            )
        )

    def test_accepted_is_sent(self, ready_config):
        outcome = self._outcome(
            ready_config,
            httpx.Response(201, json={"key": {"id": "MSG1"}, "status": "PENDING"}),
        )
        assert outcome.state is base.SendState.SENT
        assert outcome.sent is True
        assert outcome.provider_message_id == "MSG1"
        assert outcome.retryable is False

    def test_429_is_skipped_and_retryable(self, ready_config):
        outcome = self._outcome(ready_config, httpx.Response(429, json={"message": "slow"}))
        assert outcome.state is base.SendState.SKIPPED
        assert outcome.retryable is True

    def test_500_is_skipped_and_retryable(self, ready_config):
        outcome = self._outcome(ready_config, httpx.Response(500, text="boom"))
        assert outcome.state is base.SendState.SKIPPED
        assert outcome.retryable is True

    def test_401_is_permanent(self, ready_config):
        """المفتاح غلط. 5 محاولات هنا = 5 محاولات فاشلة بنفس النتيجة."""
        outcome = self._outcome(ready_config, httpx.Response(401, json={"message": "no"}))
        assert outcome.state is base.SendState.FAILED
        assert outcome.retryable is False

    def test_404_is_permanent(self, ready_config):
        """`Evolution` بيرجّع 404 لما الرقم مش مسجّل على واتساب."""
        outcome = self._outcome(
            ready_config, httpx.Response(404, json={"message": "number not found"})
        )
        assert outcome.state is base.SendState.FAILED
        assert outcome.retryable is False

    def test_timeout_is_retryable_not_an_exception(self, ready_config):
        """انقطاع الشبكة **مش استثناء ومش فشل دائم**: المزوّد لو رماه، السطر
        بيمشي لفشل واضح بدل ما الحلقة كلها تموت."""
        def handler(request):
            raise httpx.ReadTimeout("timed out")

        provider = EvolutionProvider(
            ready_config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )
        outcome = asyncio.run(
            provider.send_text(base.WhatsAppMessage(to="201012345678", body="hi"))
        )
        assert outcome.state is base.SendState.SKIPPED
        assert outcome.retryable is True

    def test_response_body_is_redacted_before_return(self, ready_config):
        outcome = self._outcome(
            ready_config,
            httpx.Response(401, json={"message": f"bad key {SECRET}"}),
        )
        assert SECRET not in (outcome.error or "")
        assert SECRET not in (outcome.raw or "")

    def test_sends_the_dialable_number_in_the_payload(self, ready_config):
        seen = {}

        def handler(request):
            import json as _json

            seen.update(_json.loads(request.content))
            seen["_path"] = request.url.path
            return httpx.Response(201, json={"key": {"id": "X"}, "status": "PENDING"})

        asyncio.run(
            _provider_with(ready_config, handler).send_text(
                base.WhatsAppMessage(to="201012345678", body="hello")
            )
        )
        assert seen["number"] == "201012345678"
        assert seen["text"] == "hello"
        assert "/message/sendText/test-instance" in seen["_path"]

    def test_api_key_travels_in_the_header_not_the_body(self, ready_config):
        seen = {}

        def handler(request):
            seen["apikey"] = request.headers.get("apikey")
            return httpx.Response(201, json={"key": {"id": "X"}, "status": "PENDING"})

        asyncio.run(
            _provider_with(ready_config, handler).send_text(
                base.WhatsAppMessage(to="201012345678", body="hello")
            )
        )
        assert seen["apikey"] == SECRET


# ============================================================
# تطبيع الرقم
# ============================================================


class TestPhoneNormalization:
    @pytest.mark.parametrize(
        "raw",
        ["01012345678", "+201012345678", "00201012345678", "201012345678", "0101234567 8"],
    )
    def test_egyptian_formats_all_become_dialable(self, raw):
        assert service.to_dialable(raw) == "201012345678"

    @pytest.mark.parametrize("raw", ["", None, "12345", "0100", "0212345678", "abcdef"])
    def test_unusable_numbers_return_empty_not_a_guess(self, raw):
        """مفيش تخمين. رقم غلط = رسالة ما بتوصلش، والأدمن محتاج
        يشوف السبب."""
        assert service.to_dialable(raw) == ""


# ============================================================
# تجهيز الطابور
# ============================================================


class TestQueueing:
    def test_valid_customers_are_queued(self, db, make_customer, ready_config):
        make_customer()
        make_customer()
        campaign = _make_campaign(db)
        summary = service.enqueue_recipients(db, campaign, segments_params())

        assert summary.queued == 2
        rows = _rows(db, campaign.id)
        assert {r.status for r in rows} == {"pending"}
        assert {r.channel for r in rows} == {"whatsapp"}
        assert all(r.customer_phone.startswith("20") for r in rows)

    def test_opted_out_is_skipped_not_failed(self, db, make_customer, ready_config):
        """رفض الإذن مش فشل. الفرق بيغيّر التقرير وبيمنع
        مكالمة المزوّد بعميل رفض."""
        make_customer(marketing_opt_out=True)
        campaign = _make_campaign(db)
        summary = service.enqueue_recipients(db, campaign, segments_params())

        assert summary.opted_out == 1
        assert summary.queued == 0
        row = _rows(db, campaign.id)[0]
        assert row.status == "skipped"
        assert "رفض" in row.error

    def test_bad_phone_is_invalid(self, db, make_customer, ready_config):
        make_customer(phone="0212345678")
        campaign = _make_campaign(db)
        summary = service.enqueue_recipients(db, campaign, segments_params())

        assert summary.invalid == 1
        assert _rows(db, campaign.id)[0].status == "invalid"

    def test_one_row_per_customer(self, db, make_customer, ready_config):
        """واتساب بيفتكر في الرقم مش في الجهاز: عميل واحد = رسالة
        واحدة، حتى لو عنده جهازين."""
        make_customer(subscribed=True)
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        assert len(_rows(db, campaign.id)) == 1

    def test_enqueueing_twice_does_not_duplicate_rows(
        self, db, make_customer, ready_config
    ):
        """إعادةتشغيل الحملة، أو ضغطة على زر الإرسال.

        السطر المكرر معناه نفس الرقم مرتين عند المزوّد — ودهأسوء
        من إنه ناقص.
        """
        make_customer()
        campaign = _make_campaign(db)
        first = service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        second = service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        assert first.queued == 1
        assert second.queued == 0
        assert second.duplicate == 1
        assert len(_rows(db, campaign.id)) == 1

    def test_oversized_audience_is_refused_before_queueing(
        self, db, make_customer, ready_config, monkeypatch
    ):
        """الحد الأقصى بيرفض قبل أن يتكتب سطور.

        الرفض أخطر من بداءة حملة ملها أولوية قانونية عند
        المزوّد، وأخر من تخطيط الذاكرة كم خراج.
        """
        monkeypatch.setattr(marketing, "MAX_RECIPIENTS", 1)
        make_customer()
        make_customer()
        campaign = _make_campaign(db)

        with pytest.raises(marketing.CampaignError):
            service.enqueue_recipients(db, campaign, segments_params())

        assert _rows(db, campaign.id) == []

    def test_audience_params_are_not_mutated(self, db, make_customer, ready_config):
        """التجهيز لازم يتجاهل `respect_opt_out` عشان يبني التقرير،
        بس **مش يعدّله** — العميل اللي عدّله متوقع الإرسال."""
        make_customer(marketing_opt_out=True)
        campaign = _make_campaign(db)
        params = segments_params(respect_opt_out=False)
        service.enqueue_recipients(db, campaign, params)
        assert params.respect_opt_out is False


def segments_params(**overrides):
    from app.segments import AudienceParams

    base = AudienceParams(respect_opt_out=False, require_push_subscription=False)
    return base.evolve(**overrides) if overrides else base


def _rows(db, campaign_id) -> list[CampaignRecipient]:
    return list(
        db.execute(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id)
            .order_by(CampaignRecipient.id)
        )
        .scalars()
        .all()
    )


def _attempts(db, recipient_id) -> list[WhatsAppAttempt]:
    return list(
        db.execute(
            select(WhatsAppAttempt)
            .where(WhatsAppAttempt.recipient_id == recipient_id)
            .order_by(WhatsAppAttempt.attempt_number)
        )
        .scalars()
        .all()
    )


# ============================================================
# التنفيذ
# ============================================================


class TestRunCampaign:
    def test_sends_to_pending_rows_only(self, db, make_customer, ready_config):
        make_customer()
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider()
        result = asyncio.run(_run(db, campaign, provider))

        assert result.sent == 2
        assert len(provider.sent) == 2
        assert all(row.status == "sent" for row in _rows(db, campaign.id))

    def test_message_body_is_the_campaign_message(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db, message="عرض الجمعة ٢٠٪")
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider()
        asyncio.run(_run(db, campaign, provider))
        assert provider.sent[0].body == "عرض الجمعة ٢٠٪"

    def test_each_attempt_is_logged(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        asyncio.run(_run(db, campaign, FakeProvider()))
        row = _rows(db, campaign.id)[0]
        attempts = _attempts(db, row.id)

        assert len(attempts) == 1
        assert attempts[0].state == "sent"
        assert attempts[0].attempt_number == 1
        assert attempts[0].campaign_id == campaign.id

    def test_attempt_log_never_stores_the_message_body(self, db, make_customer, ready_config):
        """سجل المحاولات يقول "نجح؟" مش "إيه اللي اتبعت؟". تخزين
        النص معناه نسخة ثانية من رسالة العميل في جدول مش محمي."""
        make_customer()
        campaign = _make_campaign(db, message="سري جدا")
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        asyncio.run(_run(db, campaign, FakeProvider()))
        row = _rows(db, campaign.id)[0]
        raw = repr([vars(a) for a in _attempts(db, row.id)])
        assert "سري جدا" not in raw

    def test_opt_out_after_queueing_stops_before_the_provider(
        self, db, make_customer, ready_config
    ):
        """العميل رفض **بعد** ما اتحسب الجمهور. المزوّد ما بياخدش
        نداء عنه — مش بند نكلّمه برقم عميل رفض."""
        customer = make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        customer.marketing_opt_out = True
        db.commit()

        provider = FakeProvider()
        result = asyncio.run(_run(db, campaign, provider))

        assert provider.sent == []
        assert result.skipped == 1
        assert _rows(db, campaign.id)[0].status == "skipped"

    def test_retryable_failure_then_success(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider(
            [
                base.SendOutcome(
                    state=base.SendState.SKIPPED, error="429", retryable=True
                ),
                base.SendOutcome(state=base.SendState.SENT),
            ]
        )
        result = asyncio.run(_run(db, campaign, provider))

        assert result.sent == 1
        assert result.failed == 0
        row = _rows(db, campaign.id)[0]
        assert row.status == "sent"
        assert row.attempts == 2
        assert len(_attempts(db, row.id)) == 2

    def test_permanent_failure_is_not_retried(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider(
            [base.SendOutcome(state=base.SendState.FAILED, error="رقم غير مسجّل")]
        )
        result = asyncio.run(_run(db, campaign, provider))

        assert len(provider.sent) == 1
        assert result.failed == 1
        row = _rows(db, campaign.id)[0]
        assert row.status == "failed"
        assert row.attempts == 1

    def test_retries_stop_at_max_retries(self, db, make_customer, ready_config):
        """`max_retries=2` = 3 محاولات بالكل. تجاوزها معناه
        الدوسة على المزوّد برقم واحد رافض."""
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider(
            [base.SendOutcome(state=base.SendState.SKIPPED, error="timeout", retryable=True)]
        )
        result = asyncio.run(_run(db, campaign, provider))

        assert len(provider.sent) == ready_config.max_retries + 1
        row = _rows(db, campaign.id)[0]
        assert row.status == "failed"
        assert row.attempts == ready_config.max_retries + 1
        assert result.failed == 1

    def test_provider_exception_does_not_kill_the_campaign(self, db, make_customer, ready_config):
        """المزوّد متعاقد إنه ما يرميش. لو رمي، سطر واحد ميت
        والباقي عايش."""
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        result = asyncio.run(_run(db, campaign, FakeProvider(raises=True)))
        assert result.failed == 1
        assert _rows(db, campaign.id)[0].status == "failed"

    def test_cancellation_stops_the_run(self, db, make_customer, ready_config):
        """الأدمن دوس إيقاف من تاب تاني. بنوقف بين كل رسالتين."""
        customer = make_customer()
        for _ in range(3):
            make_customer()
        campaign = _make_campaign(db)
        campaign.status = marketing.STATUS_SENDING
        db.commit()
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        async def cancel_after_first(message):
            if len(provider.sent) == 0:
                db.refresh(campaign)
                campaign.status = marketing.STATUS_CANCELLED
                db.commit()
            return base.SendOutcome(state=base.SendState.SENT)

        provider = FakeProvider()
        provider.send_text = cancel_after_first
        result = asyncio.run(_run(db, campaign, provider))

        assert result.cancelled is True
        assert len(provider.sent) < 4

    def test_empty_body_stops_without_sending(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db, message="   ")
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider()
        result = asyncio.run(_run(db, campaign, provider))

        assert provider.sent == []
        assert "فاضي" in result.stopped_reason

    def test_pending_rows_survive_a_stopped_run(self, db, make_customer, ready_config):
        """لو الوقت خلص في النص، الباقي يفضل `pending` — مش
        `failed`._row معلّقة كـ`failed` معناها ضياع أرقام."""
        for _ in range(3):
            make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        provider = FakeProvider()
        asyncio.run(_run(db, campaign, provider, max_runtime_seconds=0))
        assert provider.sent == []
        assert all(row.status == "pending" for row in _rows(db, campaign.id))


# ============================================================
# التكامل مع الحملات
# ============================================================


class TestCampaignIntegration:
    def test_whatsapp_campaign_sends_and_counts(self, db, make_customer, ready_config):
        make_customer()
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        result = asyncio.run(_run(db, campaign, FakeProvider()))
        assert result.sent == 2

    def test_both_channels_get_their_own_rows(self, db, make_customer, ready_config, monkeypatch):
        """حملة `both` = سطر لكل قناة. عميل واحد = إشعار + رسالة،
        مش سطر واحد بحقلين."""
        make_customer(subscribed=True)
        make_customer(subscribed=False)
        campaign = _make_campaign(db, channel="both")
        marketing._run_whatsapp_channel(
            db, campaign, segments_params(), service, provider=FakeProvider()
        )
        db.commit()

        whatsapp_rows = [r for r in _rows(db, campaign.id) if r.channel == "whatsapp"]
        assert len(whatsapp_rows) == 2

    def test_push_and_whatsapp_are_counted_separately(self, db, make_customer, ready_config):
        make_customer(subscribed=True)
        make_customer(subscribed=False)
        campaign = _make_campaign(db, channel="both")
        marketing._run_whatsapp_channel(
            db, campaign, segments_params(), service, provider=FakeProvider()
        )
        db.commit()

        counts = service.counts_by_channel(db, campaign.id)
        assert counts["whatsapp"] == {"sent": 2}
        assert counts["push"] == {}

    def test_counts_by_channel_after_sending(self, db, make_customer, ready_config):
        make_customer()
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        asyncio.run(_run(db, campaign, FakeProvider()))

        counts = service.counts_by_channel(db, campaign.id)
        assert counts["whatsapp"] == {"sent": 2}
        assert counts["push"] == {}

    def test_scheduled_whatsapp_is_skipped_when_not_configured(self, db, make_customer):
        """الـtick الخلفي مابيبلّغش**: حملة واتساب من غير إعدادات
        بتتخطّى بصمت. الـerror هنا كان هيتكرر كل فتح صفحة."""
        make_customer()
        campaign = _make_campaign(db, channel="whatsapp")
        campaign.status = marketing.STATUS_SCHEDULED
        campaign.scheduled_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

        ran = marketing.run_due_campaigns(db)
        assert campaign.id not in [c.id for c in ran]

    def test_send_campaign_requires_ready_settings(self, db, make_customer, monkeypatch):
        monkeypatch.setattr(settings, "evolution_api_key", None)
        campaign = _make_campaign(db)
        with pytest.raises(marketing.ChannelNotReady):
            marketing.send_campaign(db, campaign)
        db.refresh(campaign)
        assert campaign.status == marketing.STATUS_DRAFT


# ============================================================
# الأرقام
# ============================================================


class TestPacing:
    def test_interval_is_applied_between_messages(self, db, make_customer, ready_config):
        make_customer()
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        waits = []

        async def record(seconds):
            waits.append(seconds)

        pacing = service.WhatsAppConfig(
            **{**ready_config.__dict__, "message_interval_ms": 1500}
        )
        asyncio.run(
            service.run_campaign(
                db, campaign, provider=FakeProvider(), config=pacing, sleep=record
            )
        )
        assert waits and all(w == 1.5 for w in waits if w)

    def test_zero_interval_does_not_sleep(self, db, make_customer, ready_config):
        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()

        waits = []

        async def record(seconds):
            waits.append(seconds)

        asyncio.run(
            service.run_campaign(
                db, campaign, provider=FakeProvider(), sleep=record
            )
        )
        assert all(w == 0 for w in waits)


# ============================================================
# الـHTTP: الراوتر نفسه
# ============================================================


class TestSettingsRouter:
    """الـrouter مش فيه منطق، بس هو **حدود أمان**.

    الاختبارات دي بتمسك حاجتين حقيقيتين: إن الصفحة محمية، وإن
    المفتاح مش بيلفّ من الـform. لو الـform فشل يقرا المفتاح من
    الـenvironment ويكتبه في الجدول، الحاجات دي مش هتفشل هنا —
    دي هتفشل في اختبار تاني أعمق.
    """

    def test_page_requires_login(self, client):
        response = client.get("/admin/marketing/whatsapp", follow_redirects=False)
        assert response.status_code == 303
        assert "/admin/login" in response.headers.get("Location", "")

    def test_save_requires_csrf(self, client, db, admin_user):
        from app.security import AUTH_COOKIE_NAME, create_access_token

        client.cookies.set(AUTH_COOKIE_NAME, create_access_token(admin_user.username))
        response = client.post("/admin/marketing/whatsapp", data={"enabled": "on"})
        assert response.status_code == 403

    def test_page_renders(self, admin_session, db):
        response = admin_session.get("/admin/marketing/whatsapp")
        assert response.status_code == 200
        body = response.text
        assert "Evolution" in body
        # المفتاح قيمته مش في الصفحة
        assert SECRET not in body

    def test_saving_never_stores_a_key(self, admin_session, db, env_keys):
        """`api_key` مش عمود. حتى لو حد بعت الحقل، مفيش مكان
        يتحفظ فيه."""
        response = admin_session.post(
            "/admin/marketing/whatsapp",
            data={
                "enabled": "on",
                "sending_enabled": "on",
                "base_url": "http://127.0.0.1:8080",
                "instance": "test-instance",
                "message_interval_ms": "1500",
                "batch_size": "30",
                "max_retries": "3",
                "api_key": SECRET,
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        row = db.get(WhatsAppConfigRow, 1)
        assert row.enabled is True
        assert row.sending_enabled is True
        assert row.message_interval_ms == 1500
        assert row.batch_size == 30
        assert row.max_retries == 3
        assert "api_key" not in {c.name for c in WhatsAppConfigRow.__table__.columns}

    def test_bad_url_is_refused_and_not_stored(self, admin_session, db):
        """رابط فيه كلمة سر = سر مكدّس في log وbackup. مرفوض قبل
        التخزين، والقيمة القديمة بتفضل زي ما هي."""
        before = db.get(WhatsAppConfigRow, 1)
        before_url = before.base_url if before else None

        response = admin_session.post(
            "/admin/marketing/whatsapp",
            data={
                "enabled": "on",
                "base_url": "https://user:pass@evo.example.com",
                "instance": "x",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "note=bad_url" in response.headers["Location"]

        db.expire_all()
        assert (db.get(WhatsAppConfigRow, 1).base_url if db.get(WhatsAppConfigRow, 1) else None) == before_url

    def test_pacing_values_are_clamped_on_save(self, admin_session, db, env_keys):
        """رقم غريب يتقصّ مش يرجع 422 — زرار الحفظ لازم يحفظ."""
        admin_session.post(
            "/admin/marketing/whatsapp",
            data={
                "enabled": "on",
                "sending_enabled": "on",
                "base_url": "http://127.0.0.1:8080",
                "instance": "i",
                "message_interval_ms": "999999999",
                "batch_size": "99999",
                "max_retries": "999",
            },
            follow_redirects=False,
        )
        db.expire_all()
        row = db.get(WhatsAppConfigRow, 1)
        assert row.message_interval_ms == wa_settings.MAX_INTERVAL_MS
        assert row.batch_size == wa_settings.MAX_BATCH_SIZE
        assert row.max_retries == wa_settings.MAX_RETRIES

    def test_connection_test_reports_failure_without_a_provider(
        self, admin_session, db, env_keys
    ):
        """من غير مزوّد شغال، الرد "مش شغال" — مش 500."""
        response = admin_session.post(
            "/admin/marketing/whatsapp/test-connection", follow_redirects=False
        )
        assert response.status_code == 303
        # إعدادات ناقصة تعطي خطأ إعداد، وليس مش متصل.
        assert "note=connection_error" in response.headers["Location"]

    def test_test_message_rejects_a_bad_number(self, admin_session, db, ready_config):
        response = admin_session.post(
            "/admin/marketing/whatsapp/send-test",
            data={"phone": "12345", "body": "hi"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "note=bad_number" in response.headers["Location"]


# ============================================================
# نتيجة آخر عملية: الرابط فيه رموز بس
# ============================================================


class TestResultState:
    """`note` و `state` رموز. نص المزوّد ما بيمشيش في الرابط.

    السبب عملي مش نظري: الرابط بيتكتب في access logs وفي browser
    history. ونص المزوّد نص حر — ممكن يبقى فيه مسطر أو فاصل.
    """

    def test_forged_note_is_ignored(self, admin_session, db):
        """حد يبعت رابط فيه `note=message_sent` — ما يظهرش banner.

        لو الرموز مقبولة كما هي، أي حد عنده الرابط يقول للمستخدم
        إن رسالة اتبعتت وهي عمرها ما اتبعتت.
        """
        body = admin_session.get(
            "/admin/marketing/whatsapp?note=message_sent&state=sent"
        ).text
        # نص الشريط نه محروء: من الخراشة بيان كتابة.
        assert "\u0627\u0644\u0631\u0633\u0627\u0644\u0629 \u0627\u062a\u0628\u0639\u062a\u062a" not in body

    def test_forged_state_is_ignored(self, admin_session, db):
        body = admin_session.get(
            "/admin/marketing/whatsapp?note=message_failed&state=<script>alert(1)</script>"
        ).text
        # القيمة تقول السكريبت: مما يتعدّل.
        assert ">alert(1)<" not in body
        assert "state=<script>" not in body

    def test_detail_parameter_is_not_accepted(self, admin_session, db):
        """`detail` كانت بتتبعت في الرابط قبل كده. دلوقتي بتتجاهل."""
        body = admin_session.get(
            "/admin/marketing/whatsapp?note=message_failed&detail=apikey=SECRET"
        ).text
        assert "apikey=" not in body
        assert "detail" not in body

    def test_known_code_renders_with_its_tone(self, admin_session, db):
        """الرمز الموقّع بيظهر، واللون بيوصف النتيجة.

        الخطر الأخضر بيجي لمص نجاح، والفشل متثالتيت.
        """
        from app.security import sign_result

        code, state = "message_failed", "failed"
        body = admin_session.get(
            "/admin/marketing/whatsapp?note=%s&state=%s&sig=%s"
            % (code, state, sign_result(code, state))
        ).text
        assert "\u0641\u0634\u0644\u062a" in body
        assert "text-red-800" in body

    def test_unsigned_code_is_ignored(self, admin_session, db):
        """نفس الرمز من غير توقيع = ما ينعرضش."""
        body = admin_session.get(
            "/admin/marketing/whatsapp?note=message_failed&state=failed"
        ).text
        # نص الشريط هو المؤشر: لو بادت.
        assert "\u0627\u0644\u0631\u0633\u0627\u0644\u0629 \u0645\u0627\u062a\u0628\u0639\u062a\u062a\u0634" not in body

    def test_provider_text_never_reaches_the_location(self, admin_session, db, monkeypatch):
        """رد المزوّد الحر ما يظهرش في `Location` ولا في الصفحة."""

        async def fake_check(db):
            from app.whatsapp.base import ConnectionStatus, ConnectionState

            return ConnectionStatus(
                state=ConnectionState.ERROR,
                message="boom&note=message_sent apikey=%s" % SECRET,
                provider="evolution",
            )

        monkeypatch.setattr(
            "app.routers.marketing_whatsapp.whatsapp_service.check_connection",
            fake_check,
        )
        response = admin_session.post(
            "/admin/marketing/whatsapp/test-connection", follow_redirects=False
        )
        assert response.status_code == 303
        location = response.headers["Location"]
        assert SECRET not in location
        assert "boom" not in location
        assert "apikey" not in location
        # رمز واحد بس، ومقروء.
        assert "note=connection_error" in location
        assert "&note=" not in location.replace("&note=", "&note=", 1)[len("&note="):]

        body = admin_session.get(location).text
        assert SECRET not in body
        assert "boom" not in body

    def test_disconnected_provider_has_its_own_code(self, admin_session, db, monkeypatch):
        """`disconnected` مش `error`: الـinstance موجودة بس مش متصلة."""

        async def fake_check(db):
            from app.whatsapp.base import ConnectionStatus, ConnectionState

            return ConnectionStatus(
                state=ConnectionState.DISCONNECTED,
                message="open the qr",
                provider="evolution",
            )

        monkeypatch.setattr(
            "app.routers.marketing_whatsapp.whatsapp_service.check_connection",
            fake_check,
        )
        location = admin_session.post(
            "/admin/marketing/whatsapp/test-connection", follow_redirects=False
        ).headers["Location"]
        assert "note=provider_unavailable" in location
        assert "state=disconnected" in location
        assert "المزوّد مش متصل" in admin_session.get(location).text

    def test_connected_provider_says_so(self, admin_session, db, monkeypatch):
        async def fake_check(db):
            from app.whatsapp.base import ConnectionStatus, ConnectionState

            return ConnectionStatus(
                state=ConnectionState.CONNECTED, message="ok", provider="evolution"
            )

        monkeypatch.setattr(
            "app.routers.marketing_whatsapp.whatsapp_service.check_connection",
            fake_check,
        )
        location = admin_session.post(
            "/admin/marketing/whatsapp/test-connection", follow_redirects=False
        ).headers["Location"]
        assert "note=connection_ok" in location
        assert "state=connected" in location

    def test_bad_number_is_checked_before_any_call(self, admin_session, db, ready_config):
        """رقم غلط = `bad_number`، مش `not_ready`.

        `send_test_message` بيرمي نفس الاستثناء في الحالتين، فالتحقق
        لازم يبقى قبله — وإلا الأدمن يظن إن رقمه غلط وهو الإعدادات
        هي اللي ناقصة.
        """
        called = []

        async def spy(db, to, body):
            called.append(to)
            raise AssertionError("must not reach the provider")

        location = admin_session.post(
            "/admin/marketing/whatsapp/send-test",
            data={"phone": "12345", "body": "hi"},
            follow_redirects=False,
        ).headers["Location"]
        assert "note=bad_number" in location
        assert called == []

    def test_unconfigured_provider_never_reports_sent(self, admin_session, db):
        """من غير إعدادات، النتيجة `not_ready` — مش "اتبعتت"."""
        location = admin_session.post(
            "/admin/marketing/whatsapp/send-test",
            data={"phone": "01000000000", "body": "hi"},
            follow_redirects=False,
        ).headers["Location"]
        assert "note=message_sent" not in location
        assert "note=not_ready" in location

    def test_phone_in_url_is_digits_only(self, admin_session, db, ready_config):
        """الرقم اللي بيرجع في الرابط أرقام بس — من غير حقن."""
        location = admin_session.post(
            "/admin/marketing/whatsapp/send-test",
            data={"phone": "01000000000&note=message_sent", "body": "hi"},
            follow_redirects=False,
        ).headers["Location"]
        assert location.count("note=") == 1


# ============================================================
# الخلاصة: مفيش مسار بيطلع السر
# ============================================================


# ============================================================
# صفحة الحملة: التقريع لازم يفرق القنات
# ============================================================


class TestBothChannelReporting:
    def test_detail_page_splits_push_from_whatsapp(
        self, admin_session, db, make_customer, ready_config
    ):
        """`both` = سطر لكل قناة. صفحة الحملة لازم تعرض اتنين.

        رقم مخلطت هنا يعني الأدمن بيشوف "2 اتبعتت" ويفتكر إن
        العملاء اتبعتت إليها بسانة، والحقيقيقة إن عميل واحد وصله
        إشعار بس.
        """
        make_customer(subscribed=True)
        make_customer(subscribed=False)
        campaign = _make_campaign(db, channel="both")
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        asyncio.run(_run(db, campaign, FakeProvider()))

        response = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}")
        assert response.status_code == 200
        body = response.text
        assert "Push" in body
        assert "WhatsApp" in body

    def test_send_confirmation_names_the_channel(
        self, admin_session, db, make_customer, ready_config
    ):
        """الرسالة التي بتظهر قبل الإرسال بتقول واتساب، مش "إشعار". الزرار الغلط ده الي بيكلّم 5000 عميل."""
        make_customer()
        campaign = _make_campaign(db)
        body = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}").text
        assert "WhatsApp للعميل" in body


class TestAttemptReporting:
    """سجلّ المحاولات على صفحة الحملة.

    الأدمن محتاج يعرف: مين، أنهي قناة، كام مرة اتحاول، وبقى إيه.
    رد المزوّد الخام (`raw`) ما يظهرش — قد يكون فيه حاجة ما لها
    لازمة، والسبب المقطّع في السطر كفاية.
    """

    def test_attempts_table_shows_recipient_channel_and_count(
        self, admin_session, db, make_customer, ready_config
    ):
        customer = make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        asyncio.run(_run(db, campaign, FakeProvider()))

        attempts = marketing.recipient_attempts(db, campaign.id)
        assert attempts, "no attempts were logged for a sent whatsapp campaign"
        assert attempts[0].state == "sent"
        assert attempts[0].attempt_number == 1
        assert attempts[0].recipient.attempts == 1
        assert attempts[0].http_status is None or attempts[0].http_status < 400
        assert attempts[0].recipient.customer_name == customer.name

        body = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}").text
        assert "\u0645\u062d\u0627\u0648\u0644\u0627\u062a \u0627\u0644\u0625\u0631\u0633\u0627\u0644" in body
        assert customer.name in body
        assert "WhatsApp" in body

    def test_failed_attempt_shows_state_and_http_status(
        self, admin_session, db, make_customer, ready_config
    ):
        """محاولة دائمة الفشل (رقم مرفوض) بتظهر بحالتها وكود المزوّد.

        5xx و 429 منفصلين: دول قابلين لإعادة المحاولة، فيتسجّلوا
        `skipped` والسطر يفضل `pending`. 4xx معناها "المزوّد مش هيقبل
        الرسالة دي"، فالفشل النهائي اللي الأدمن محتاج يشوفه.
        """

        def handler(request):
            return httpx.Response(400, json={"status": "invalid number"})

        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        asyncio.run(_run(db, campaign, _provider_with(ready_config, handler)))
        db.commit()

        attempts = marketing.recipient_attempts(db, campaign.id)
        assert [a.state for a in attempts] == ["failed"]
        assert attempts[0].http_status == 400
        # الفشل الدائم بيقفل الموضوع: مش معاد ولا مستني.
        assert marketing.recipient_breakdown(db, campaign.id)["failed"] == 1

        body = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}").text
        assert "\u0641\u0634\u0644\u062a" in body
        assert "400" in body

    def test_rate_limited_attempts_are_logged_then_the_row_fails(
        self, admin_session, db, make_customer, ready_config
    ):
        """429 = "مش دلوقتي"، فكل محاولة بتتسجّل `skipped`.

        ولما رصيد المحاولات يخلص السطر بيقفل `failed` — سيبه
        `pending` يعني `_due_rows` هتفلبّع عليه للأبد. السطرين
        موجودين عشان الأدمن يفرق: المحاولات بتقول "429"، والسطر
        بيقول "خلصت المحاولات".
        """

        def handler(request):
            # مسار الإرسال بينتهي باسم الـinstance، فالـmatch لازم
            # يكون في النص مش في الآخر.
            if "/sendText/" in request.url.path:
                return httpx.Response(429, json={"status": "rate limit"})
            return httpx.Response(200, json=[{"state": "CONNECTED"}])

        make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        result = asyncio.run(_run(db, campaign, _provider_with(ready_config, handler)))
        db.commit()

        attempts = marketing.recipient_attempts(db, campaign.id)
        assert len(attempts) >= 2, "expected retries after a 429"
        assert {a.state for a in attempts} == {"skipped"}
        assert all(a.http_status == 429 for a in attempts)
        breakdown = marketing.recipient_breakdown(db, campaign.id)
        assert breakdown["failed"] == 1 and breakdown["pending"] == 0

    def test_push_campaign_has_no_attempt_table(self, admin_session, db, make_customer):
        """`push` مش بيتسجّل في `whatsapp_attempts` — مفيش كارت محاولات
        فاضي في صفحة إشعار عادي."""
        make_customer(subscribed=True)
        campaign = _make_campaign(db, channel="push")

        body = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}").text
        assert "\u0645\u062d\u0627\u0648\u0644\u0627\u062a \u0627\u0644\u0625\u0631\u0633\u0627\u0644" not in body


class TestCampaignTotals:
    def test_detail_page_shows_pending_and_cancelled(
        self, admin_session, db, make_customer, ready_config
    ):
        """"اتبعت/فشل" مش كفاية: السطر اللي ما اتبعتش ليه حالة؟

        `pending` لسه مستني، `ملغاة` يعني الحملة اتوقفت قبل ما
        توصله. الرقمين مختلفين ومعنيين مختلفين.
        """
        for _ in range(3):
            make_customer()
        campaign = _make_campaign(db)
        service.enqueue_recipients(db, campaign, segments_params())
        db.commit()
        asyncio.run(_run(db, campaign, FakeProvider(), max_runtime_seconds=0))
        db.commit()

        body = admin_session.get(f"/admin/marketing/campaigns/{campaign.id}").text
        assert "\u0644\u0633\u0647 \u0645\u0633\u062a\u0646\u064a" in body
        assert "\u0645\u0644\u063a\u0627\u0629" in body


class TestNoSecretLeaks:
    """الفحص الشامل: المسار كله من القراءة للعرض."""

    def test_full_path_never_shows_the_key(self, db, ready_config, config_row):
        rendered = repr(
            {
                "config": ready_config.safe_dict(),
                "provider": repr(EvolutionProvider(ready_config)),
                "connection": asyncio.run(
                    service.check_connection(db)
                ).__dict__,
            }
        )
        assert SECRET not in rendered

    def test_failed_send_outcome_has_no_key(self, db, ready_config):
        def handler(request):
            return httpx.Response(401, json={"message": f"apikey {SECRET} invalid"})

        outcome = asyncio.run(
            _provider_with(ready_config, handler).send_text(
                base.WhatsAppMessage(to="201012345678", body="hi")
            )
        )
        assert SECRET not in (outcome.error or "")
        assert SECRET not in (outcome.raw or "")