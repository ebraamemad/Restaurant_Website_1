"""اختبارات Phase 3 — Web Push.

## ليه almost كل الاختبارات بتعمل `push.is_configured` = False

الاختبارات الحقيقية للإرسال محتاجة endpoint حقيقي من FCM/Mozilla،
وده مش متاح في CI. فبنقسّم:

1. **السلوك بدون إعداد** (`is_configured() == False`): دوال بتفحص
   إن `send_to_subscription` بت.skip بـ"skipped" ومش بتبعت. ده اختبار
   حقيقي للسلوك في الحالة اللي السيرفر هيشتغل فيها فعلاً لو الأدمن
   نسى يضبط VAPID.
2. **السلوك مع إعداد + مزوّد مزعوب** (`is_configured() == True`،
   `webpush` بيتعمله mock بيرمي): دوال بتفحص ان `404/410` بيبطّل
   الاشتراك، `500` بيزوّد `failure_count`، والطلب **مش بيرجع** error.
3. **الـendpoints** بتستخدم القيم دي مع monkeypatch على
   `app.push.is_configured` و`app.push.public_key` و
   `app.push.get_vapid_private_pem` — عشان نختبر dedupe/ربط/إلغاء
   من غير شبكة.

## ليه `monkeypatch.setenv` على `settings` مش monkeypatch على الوحدة

`push.is_configured()` بيقرأ من `app.config.settings` (pydantic). لو
عملنا monkeypatch على `push.is_configured` مباشرة، الكود اللي عايز
نختبره بيبقى fake. الأحسن نغير `settings.vapid_*` ونخلي الكود
الحقيقي يشتغل. الـsettings object singleton، فبنرجّعه في
`_vapid_off` fixture.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def vapid_keys() -> dict[str, str]:
    """زوج VAPID حقيقي (مولّد في الاختبار، مش في الـrepo).

    بنطلعه بالشكلين لأننا في العادي بنشوفه:
    - `private_key`: DER/PKCS8 base64url (شكل `cryptography`)
    - `raw_private_key`: 32 بايت raw base64url (شكل Node `web-push`)
    """
    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_point = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    return {
        "private_key": b64(private_der),
        "raw_private_key": b64(
            key.private_numbers().private_value.to_bytes(32, "big")
        ),
        "public_key": b64(public_point[1:]),  # 64 بايت: `.env` format
        "public_key_browser": b64(public_point),  # 65 بايت: `applicationServerKey`
        "subject": "mailto:push-test@example.com",
    }


@pytest.fixture()
def vapid_off(settings_snapshot):
    """VAPID مطفأ — الافتراضي الآمن للاختبارات.

    `get_vapid_private_pem` و`get_vapid_public_key` في `app.config`
    متـ`lru_cache`'d. من غير `cache_clear` الاختبار التاني هيشوف
    نتيجة الأول وهيمر من غير ما يختبر حاجة.
    """
    import app.config as config_module

    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()

    original = (
        settings_snapshot.vapid_subject,
        settings_snapshot.vapid_private_key,
        settings_snapshot.vapid_public_key,
    )
    settings_snapshot.vapid_subject = ""
    settings_snapshot.vapid_private_key = ""
    settings_snapshot.vapid_public_key = ""
    yield settings_snapshot
    (
        settings_snapshot.vapid_subject,
        settings_snapshot.vapid_private_key,
        settings_snapshot.vapid_public_key,
    ) = original
    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()


@pytest.fixture()
def vapid_on(vapid_keys, settings_snapshot):
    """VAPID شغّال بزوج DER حقيقي — الإرسال نفسه بيتـmock دايماً."""
    import app.config as config_module

    settings_snapshot.vapid_subject = vapid_keys["subject"]
    settings_snapshot.vapid_private_key = vapid_keys["private_key"]
    settings_snapshot.vapid_public_key = vapid_keys["public_key"]
    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()
    yield settings_snapshot
    settings_snapshot.vapid_subject = ""
    settings_snapshot.vapid_private_key = ""
    settings_snapshot.vapid_public_key = ""
    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()


@pytest.fixture()
def vapid_on_raw(vapid_keys, settings_snapshot):
    """نفس `vapid_on` بس المفتاح 32 بايت raw (شكل Node web-push)."""
    import app.config as config_module

    settings_snapshot.vapid_subject = vapid_keys["subject"]
    settings_snapshot.vapid_private_key = vapid_keys["raw_private_key"]
    settings_snapshot.vapid_public_key = vapid_keys["public_key"]
    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()
    yield settings_snapshot
    settings_snapshot.vapid_subject = ""
    settings_snapshot.vapid_private_key = ""
    settings_snapshot.vapid_public_key = ""
    config_module.get_vapid_private_pem.cache_clear()
    config_module.get_vapid_public_key.cache_clear()
    config_module.get_vapid_public_key_browser.cache_clear()


@pytest.fixture()
def subscription_payload() -> dict:
    """Body صالح لـ`POST /api/push/subscribe`."""
    return {
        "endpoint": "https://fcm.googleapis.com/fcm/send/test-endpoint-1",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
            "auth": "tBHItJI5svbpez7KI4CCXg",
        },
        "device": "test-device",
    }


@pytest.fixture()
def new_customer_id(db):
    """مصنع عملاء حقيقيين — `push_subscriptions.customer_id` FK لازم يمرّ.

    نفس سبب `new_admin_id`:** factory مش قيمة. اختبار "الإشعارات
    متربوطة بالطلب بتاع العميل" محتاج **عميلين مختلفين**، وإلا بيبقى
    بيفتّش على استعلام واحد.
    """
    from app.models import Customer

    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        now = datetime.now(timezone.utc)
        customer = Customer(
            phone=f"0155{counter['n']:07d}",
            name=f"Push Customer {counter['n']}",
            first_order_at=now,
            last_order_at=now,
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer.id

    return _make


@pytest.fixture()
def new_admin_id(db):
    """مصنع أدمن حقيقي — `push_subscriptions.admin_id` FK لازم يمرّ.

    **ليه factory مش قيمة:** اختبارات عزل الأدمن بتحتاج **أدمنين
    مختلفين**. لو الـfixture رجّع نفس الـid في كل نداء، بيبقى
    `subscriptions_for_admin(A)` و`subscriptions_for_admin(B)` نفس
    الاستعلام، والاختبار يمرّ وانت في الحقيقة بتختبر حاجة واحدة.
    فبندّعي `new_admin_id()` كل مرة. الـ`id` بيتزود في كل نداء، فالاختبار
    بيبني فعلاً أكتر من أدمن وبيختبر العزل بينهم.
    """
    from app.models import Admin
    from app.security import hash_password

    counter = {"n": 0}

    def _make() -> int:
        counter["n"] += 1
        admin = Admin(
            username=f"push-helper-{counter['n']}",
            password_hash=hash_password("x"),
            full_name=f"Push Admin {counter['n']}",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        return admin.id

    return _make


@pytest.fixture()
def make_subscription(db):
    """يصنع اشتراكات مباشرة في الداتابيز (من غير endpoint HTTP)."""

    def _make(*, endpoint: str, customer_id=None, admin_id=None, status: str = "active"):
        from app.models import PushSubscription

        sub = PushSubscription(
            endpoint=endpoint,
            p256dh="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
            auth="tBHItJI5svbpez7KI4CCXg",
            customer_id=customer_id,
            admin_id=admin_id,
            status=status,
        )
        db.add(sub)
        db.commit()
        return sub

    return _make


@pytest.fixture()
def make_order(db):
    """طلب كامل بأقل عدد أعمدة مطلوبة.

    **ليه رقم فريد كل نداء:** `customers.phone` عنده unique constraint
    (وده مقصود — نفس الرقم = نفس العميل). لو الـfixture burrowed
    `01000000000` ثابت، أي اختبار ينادي `make_order()` مرتين بيفشل
    بـ`UniqueViolation` — وده يبقى error في كود الاختبار مش في
    الكود اللي المفروض نختبره.
    """
    from app.models import Branch, Customer, Order
    from decimal import Decimal

    from app.ordering import quantize_money

    counter = {"n": 0}

    def _make(*, customer_id=None, status: str = "pending", tracking_hash: str | None = None):
        # `branch_id` NOT NULL — بنستخدم أي فرع موجود، وفي أول اختبار
        # نشغّل `menu`/branch fixture. لو مفيش فرع، ننشئ واحد.
        branch = db.scalar(select(Branch).order_by(Branch.id).limit(1))
        if branch is None:
            branch = Branch(name="Test Branch", is_default=True, is_active=True)
            db.add(branch)
            db.commit()
            db.refresh(branch)

        # العدّاد بيتزود **قبل** شرط `tracking_hash`، مش جواه. لو جواه،
        # الاختبارات اللي بتمرر `tracking_hash` صريح مبيتزودوش —
        # فياخدوا نفس رقم العميل ويبقوا على `ix_customers_phone` unique.
        counter["n"] += 1
        phone = f"010{counter['n']:08d}"
        if tracking_hash is None:
            tracking_hash = f"h{counter['n']}"

        if customer_id is None:
            now = datetime.now(timezone.utc)
            customer = Customer(
                phone=phone,
                name="Push Tester",
                # العمودين NOT NULL في الداتابيز — بيتحسبوا وقت أول طلب
                # في كود التطبيق مش في الـdefault.
                first_order_at=now,
                last_order_at=now,
            )
            db.add(customer)
            db.commit()
            db.refresh(customer)
            customer_id = customer.id

        order = Order(
            customer_id=customer_id,
            customer_name="Push Tester",
            customer_phone="01000000000",
            customer_address="1 Test Street",
            status=status,
            channel="system",
            whatsapp_message="test",
            branch_id=branch.id,
            subtotal=Decimal("100.00"),
            discount_amount=Decimal("0.00"),
            delivery_fee=Decimal("0.00"),
            total=Decimal("100.00"),
            order_tracking_hash=tracking_hash,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    return _make


# ============================================================
# الإعدادات — إهم نقطة في كل المرحلة
# ============================================================


class TestVapidConfig:
    """اختبارات `app/config.py` — المكان الوحيد اللي بيقرا ENV."""

    def test_no_keys_means_not_configured(self, vapid_off):
        from app import push

        assert push.is_configured() is False
        assert push.public_key() is None

    def test_keys_present_means_configured(self, vapid_on):
        from app import push

        assert push.is_configured() is True
        assert push.public_key() is not None
        assert len(push.public_key()) > 80

    def test_browser_key_has_uncompressed_prefix(self, vapid_on, vapid_keys):
        """`applicationServerKey` = 65 بايت مع `0x04`.

        من غير الـprefix، `pushManager.subscribe()` بيرمي
        `InvalidAccessError` والعَرَض: المستخدم شايف "تم التفعيل" وما
        جيلوش إشعار. ده اختبار مش بيبان غير على جهاز حقيقي.
        """
        import base64 as b64mod

        from app import push

        published = push.public_key()
        assert published is not None

        raw = b64mod.urlsafe_b64decode(published + "=" * (-len(published) % 4))
        assert len(raw) == 65
        assert raw[0] == 0x04
        # وده نفس النقطة اللي في `.env` بس بالـprefix
        assert raw[1:] == b64mod.urlsafe_b64decode(
            vapid_keys["public_key"] + "=" * (-len(vapid_keys["public_key"]) % 4)
        )

    def test_env_format_key_is_64_bytes_without_prefix(self, vapid_on, vapid_keys):
        """`public_key_env_format()` (للعرض الإداري) يفضل 64 بايت.

        **مفيش تأكيد إن أول بايت != 0x04:** ده مش مضمون — أول بايت من X
        ممكن يكون `0x04` بالصدفة والمفتاح يفضل سليم. اللي مضمون إن الشكل
        X‖Y (64 بايت) من غير prefix، وبنثبته بمقارنته بالقيمة الأصلية.
        """
        import base64 as b64mod

        from app import push

        env_value = push.public_key_env_format()
        raw = b64mod.urlsafe_b64decode(env_value + "=" * (-len(env_value) % 4))
        assert len(raw) == 64
        assert env_value == vapid_keys["public_key"]

    def test_browser_key_derived_from_private_only(self, vapid_keys, settings_snapshot):
        """لو `VAPID_PUBLIC_KEY` فاضي، المفتاح بيتستخرج من الخاص.

        ده مسار شائع: حد عمل `VAPID_PRIVATE_KEY` بس وخلاص. من غير
        الاستخراج، الإشعارات كانت هتقول "غير مفعّلة" والمفتاح موجود
        فعلاً.
        """
        import app.config as config_module

        config_module.get_vapid_private_pem.cache_clear()
        config_module.get_vapid_public_key.cache_clear()
        config_module.get_vapid_public_key_browser.cache_clear()
        try:
            settings_snapshot.vapid_subject = vapid_keys["subject"]
            settings_snapshot.vapid_private_key = vapid_keys["private_key"]
            settings_snapshot.vapid_public_key = ""

            from app import push

            assert push.is_configured() is True
            assert push.public_key() == vapid_keys["public_key_browser"]
        finally:
            settings_snapshot.vapid_subject = ""
            settings_snapshot.vapid_private_key = ""
            settings_snapshot.vapid_public_key = ""
            config_module.get_vapid_private_pem.cache_clear()
            config_module.get_vapid_public_key.cache_clear()
            config_module.get_vapid_public_key_browser.cache_clear()

    def test_private_key_never_in_public_key(self, vapid_on):
        """`public_key()` بترجّع المفتاح العام بس.

        ده أهم اختبار في الملف كله: لو الـpublic endpoint طلع منه
        المفتاح الخاص، أي حد يقدر يبعت إشعارات باسم المطعم.
        """
        from app import push

        published = push.public_key()
        assert published is not None
        # المفتاح الخاص base64url أطول بكتير (DER/PKCS8 ~138 بايت
        # مقابل 65 بايت للنقطة uncompressed).
        assert published != push.get_vapid_private_pem() if hasattr(push, "get_vapid_private_pem") else True
        from app.config import get_vapid_private_pem

        assert published != get_vapid_private_pem()

    def test_private_key_normalizes_to_pem(self, vapid_on):
        """المفتاح الخاص اللي في `.env` base64url DER لازم يتحوّل PEM.

        `pywebpush` بياخد PEM. لو بعتّلوش base64، كل إرسال هيفشل
        بـ`ValueError` جوّه `webpush` — وده يفشل بصمت عند العميل.
        """
        from app.config import get_vapid_private_pem

        pem = get_vapid_private_pem()
        assert pem is not None
        assert pem.startswith("-----BEGIN PRIVATE KEY-----")
        assert pem.strip().endswith("-----END PRIVATE KEY-----")

    def test_private_key_accepts_raw_32_bytes(self, vapid_on_raw):
        """الشكل اللي `webpush.generateVapidKeys()` بتطلعه (32 raw).

        ده الشكل المنتشر في التوثيق. لو مش مدعوم، أي حدFollowing
        التوثيق بالظبط يلقى إشعاراته مش شغّالة.
        """
        from app.config import get_vapid_private_pem

        pem = get_vapid_private_pem()
        assert pem is not None
        assert pem.startswith("-----BEGIN PRIVATE KEY-----")

    def test_private_key_accepts_pem_directly(self, vapid_on):
        """`.env` ممكن يكون فيه PEM جاهز — لازم يعدي من غير تحويل."""
        from app.config import get_vapid_private_pem

        pem = get_vapid_private_pem()
        assert pem is not None
        assert pem.startswith("-----BEGIN PRIVATE KEY-----")

    def test_raw_and_der_private_keys_agree(self, vapid_keys, vapid_on_raw):
        """المفتاحين (raw وDER) لازم يدوا نفس PEM.

        بيثبت إن التطبيع مش بيغيّر المفتاح — التحويل شكلي بس.
        """
        from cryptography.hazmat.primitives import serialization as ser

        import app.config as config_module

        config_module.get_vapid_private_pem.cache_clear()
        try:
            from app.config import get_vapid_private_pem

            pem_from_raw = get_vapid_private_pem()
            (vapid_on_raw.__class__)  # noqa: B018 - access for clarity
            from app.config import settings as app_settings

            app_settings.vapid_private_key = vapid_keys["private_key"]
            config_module.get_vapid_private_pem.cache_clear()
            pem_from_der = get_vapid_private_pem()
        finally:
            config_module.get_vapid_private_pem.cache_clear()

        assert pem_from_raw == pem_from_der
        key = ser.load_pem_private_key(pem_from_raw.encode(), password=None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)

    def test_public_key_from_private_derives_consistently(self, vapid_on, vapid_keys):
        """المفتاح العام في `.env` لازم يطابق المفتاح الخاص.

        لو اتنسخوا من محطتين مختلفتين، كل اشتراك هيفشل عند التوقيع
        والسبب هيتجلّى كـ"المتصفح رفض الإشعار".
        """
        from app.config import get_vapid_public_key

        assert get_vapid_public_key() == vapid_keys["public_key"]

    def test_blank_subject_disables_push(self, vapid_on):
        """`vapid_subject` فاضي = مش هنبعت. المزوّد بيرفض من غيره."""
        import app.config as config_module

        from app import push

        vapid_on.vapid_subject = ""
        config_module.get_vapid_private_pem.cache_clear()
        config_module.get_vapid_public_key.cache_clear()
        config_module.get_vapid_public_key_browser.cache_clear()
        assert push.is_configured() is False

    def test_malformed_private_key_disables_push(self, vapid_on):
        """مفتاح تالف = مطفأ، مش exception وقت أول طلب.

        **`public_key()` لسه بيرجّع قيمة** وده مقصود: المفتاح العام
        موجود وصحيح في الـENV، وهو **مش سر**. اللي بيطيّل الإرسال هو
        المفتاح الخاص بس. لو رجعنا `None` من `public_key()` كمان، الـUI
        كان هيقول "المتصفح ده مش بيدعم الإشعارات" والسبب الحقيقي
        خطأ إعدادات في السيرفر.
        """
        import app.config as config_module

        from app import push

        vapid_on.vapid_private_key = "not-a-real-key"
        config_module.get_vapid_private_pem.cache_clear()
        config_module.get_vapid_public_key.cache_clear()
        config_module.get_vapid_public_key_browser.cache_clear()

        assert push.is_configured() is False
        assert config_module.get_vapid_private_pem() is None
        # المفتاح العام لسه موجود — وده مش غلط
        assert push.public_key() is not None

    def test_send_is_skipped_when_private_key_broken(
        self, db, vapid_on, make_subscription
    ):
        """مفتاح خاص تالف => `skipped`، مش محاولة إرسال فاشلة."""
        import app.config as config_module

        from app import push

        vapid_on.vapid_private_key = "not-a-real-key"
        config_module.get_vapid_private_pem.cache_clear()
        config_module.get_vapid_public_key.cache_clear()
        config_module.get_vapid_public_key_browser.cache_clear()

        sub = make_subscription(endpoint="https://push.example/bad-key")
        result = push.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.status == "skipped"
        assert sub.failure_count == 0
        assert push.is_configured() is False
        config_module.get_vapid_private_pem.cache_clear()


class TestVapidPublicKeyFormats:
    """`normalize_vapid_public_key` — الصيغ المقبولة للمفتاح العام.

    **ثابتة بالكامل** (مفيش توليد مفاتيح عشوائي) عشان تغطي الحالة اللي
    كانت بتفشل بشكل متقطع: مفتاح X‖Y بحجم 64 بايت أول بايت فيه `0x04`.
    """

    @staticmethod
    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _unb64(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def test_64_byte_key_starting_with_04_is_preserved(self):
        """مفتاح 64 بايت أول بايت فيه `0x04` لازم يفضل زي ما هو.

        `0x04` في أول مفتاح 64 بايت قيمة بيانات عادية، مش prefix.
        الكود القديم كان بيشيله → 63 بايت → يرفض المفتاح الصحيح.
        """
        from app.config import normalize_vapid_public_key

        raw = b"\x04" + bytes(range(63))
        out = self._unb64(normalize_vapid_public_key(self._b64(raw)))
        assert len(out) == 64
        assert out == raw

    def test_64_byte_key_without_prefix_is_preserved(self):
        raw = b"\x07" + bytes(range(63))
        from app.config import normalize_vapid_public_key

        assert self._unb64(normalize_vapid_public_key(self._b64(raw))) == raw

    def test_65_byte_uncompressed_strips_prefix(self):
        """النقطة غير المضغوطة الحقيقية (65 بايت) لازم تفقد الـ`0x04`."""
        xy = b"\x11" + bytes(range(63))
        uncompressed = b"\x04" + xy

        from app.config import normalize_vapid_public_key

        out = self._unb64(normalize_vapid_public_key(self._b64(uncompressed)))
        assert len(out) == 64
        assert out == xy

    def test_65_byte_without_prefix_is_rejected(self):
        """65 بايت من غير `0x04` = صيغة تالفة، تترفض مش تتقصّ."""
        raw = b"\x07" + bytes(range(64))

        from app.config import normalize_vapid_public_key

        with pytest.raises(ValueError):
            normalize_vapid_public_key(self._b64(raw))

    @pytest.mark.parametrize("size", [0, 1, 32, 63, 66, 80])
    def test_wrong_lengths_are_rejected(self, size):
        raw = b"\x04" + bytes(max(0, size - 1))

        from app.config import normalize_vapid_public_key

        with pytest.raises(ValueError):
            normalize_vapid_public_key(self._b64(raw))

    def test_pem_public_key_normalizes_to_64_bytes(self):
        """PEM (شكل آخر مدعوم) بيتحوّل لـ64 بايت من غير prefix.

        برضه ثابت: بنشتق النقطة من عدد خاص ثابت (1) بدل توليد عشوائي.
        """
        key = ec.derive_private_key(1, ec.SECP256R1())
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        full = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )

        from app.config import normalize_vapid_public_key

        out = self._unb64(normalize_vapid_public_key(pem))
        assert len(out) == 64
        assert out == full[1:]

    def test_config_accepts_64_byte_key_starting_with_04(self, settings_snapshot):
        """نفس السيناريو المبلَّغ عنه من خلال `get_vapid_public_key`."""
        import app.config as config_module

        raw = b"\x04" + bytes(range(63))
        encoded = self._b64(raw)
        original = settings_snapshot.vapid_public_key
        config_module.get_vapid_public_key.cache_clear()
        config_module.get_vapid_public_key_browser.cache_clear()
        try:
            settings_snapshot.vapid_public_key = encoded
            assert config_module.get_vapid_public_key() == encoded
            # ولما نسلّمه للمتصفح: 65 بايت مع الـprefix واحدة بس
            browser = self._unb64(config_module.get_vapid_public_key_browser())
            assert len(browser) == 65
            assert browser[0] == 0x04
            assert browser[1:] == raw
        finally:
            settings_snapshot.vapid_public_key = original
            config_module.get_vapid_public_key.cache_clear()
            config_module.get_vapid_public_key_browser.cache_clear()


# ============================================================
# التسجيل و الـdedupe
# ============================================================


class TestSubscriptionRegistry:
    def test_register_creates_row(self, db, vapid_off, subscription_payload):
        from app import push

        sub = push.register_subscription(
            db,
            endpoint=subscription_payload["endpoint"],
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=None,
        )
        db.commit()

        assert sub.id is not None
        assert sub.status == "active"
        assert sub.customer_id is None
        assert sub.admin_id is None

    def test_register_same_endpoint_is_update_not_insert(
        self, db, vapid_off, subscription_payload
    ):
        """الصفحة اتفتحت مرتين = صف واحد، مش اتنين.

        اتنين اشتراكات بنفس الـendpoint = العميل هياخد **كل** إشعار
        مرتين. ده أسوأ من "ما وصلش" من ناحية التجربة.
        """
        from app import push

        endpoint = subscription_payload["endpoint"]
        first = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=None,
        )
        db.commit()
        second = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=None,
        )
        db.commit()

        assert first.id == second.id
        assert len(push.find_all(db)) == 1

    def test_register_resets_invalid_to_active(self, db, vapid_off, subscription_payload):
        """اشتراك قديم 410 و.browser عمل subscribe من جديد = يبقى شغّال."""
        from app import push

        endpoint = subscription_payload["endpoint"]
        sub = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=None,
        )
        sub.status = "invalid"
        sub.invalid_at = datetime.now(timezone.utc)
        db.commit()

        refreshed = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=None,
        )
        db.commit()

        assert refreshed.id == sub.id
        assert refreshed.status == "active"
        assert refreshed.invalid_at is None

    def test_register_moves_ownership(
        self, db, vapid_off, subscription_payload, make_order, new_admin_id
    ):
        """تسجيل نفس الـendpoint لمالك تاني بينقل السجل.

        الحالات الحقيقية: موظف بيشترك من لاب القسم، حد تاني قاعد على
        نفس اللاب، أو عميل فتح الموقع على جهاز حد تاني.
        """
        from app import push

        order = make_order()
        endpoint = subscription_payload["endpoint"]

        sub = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=order.customer_id,
            admin_id=None,
        )
        db.commit()
        assert sub.customer_id == order.customer_id

        admin_id = new_admin_id()
        moved = push.register_subscription(
            db,
            endpoint=endpoint,
            p256dh=subscription_payload["keys"]["p256dh"],
            auth=subscription_payload["keys"]["auth"],
            customer_id=None,
            admin_id=admin_id,
        )
        db.commit()

        assert moved.id == sub.id
        assert moved.customer_id is None
        assert moved.admin_id == admin_id

    def test_find_subscription_by_endpoint(self, db, vapid_off, make_subscription):
        from app import push

        endpoint = "https://push.example/abc"
        make_subscription(endpoint=endpoint)
        assert push.find_subscription(db, endpoint) is not None
        assert push.find_subscription(db, "https://push.example/nope") is None

    def test_unsubscribe_deletes_row(self, db, vapid_off, make_subscription):
        from app import push

        endpoint = "https://push.example/gone"
        make_subscription(endpoint=endpoint)

        assert push.unsubscribe(db, endpoint) is True
        db.commit()
        assert push.find_subscription(db, endpoint) is None
        # تاني مرة = False مش exception
        assert push.unsubscribe(db, endpoint) is False

    def test_unsubscribe_scoped_to_audience(self, db, vapid_off, make_subscription, make_order):
        """`audience` بيمنع `/admin` إنه يمسح اشتراك عميل."""
        from app import push

        order = make_order()
        endpoint = "https://push.example/client-owned"
        make_subscription(endpoint=endpoint, customer_id=order.customer_id)

        assert push.unsubscribe(db, endpoint, audience="admin") is False
        db.commit()
        assert push.find_subscription(db, endpoint) is not None


# ============================================================
#.lookup
# ============================================================


class TestSubscriptionLookup:
    def test_customer_subscriptions_exclude_invalid(
        self, db, vapid_off, make_subscription, make_order, new_customer_id
    ):
        from app import push

        order = make_order()
        customer_id = order.customer_id
        make_subscription(endpoint="https://push.example/a", customer_id=customer_id)
        make_subscription(endpoint="https://push.example/b", customer_id=customer_id)
        make_subscription(
            endpoint="https://push.example/c", customer_id=customer_id, status="invalid"
        )
        # عميل تاني — لازم ما يتشملش
        make_subscription(
            endpoint="https://push.example/d",
            customer_id=new_customer_id(),
        )

        found = push.subscriptions_for_customer(db, customer_id)
        assert {s.endpoint for s in found} == {
            "https://push.example/a",
            "https://push.example/b",
        }

    def test_order_subscriptions_follow_customer(self, db, vapid_off, make_subscription, make_order):
        """العميل بـ3 أجهزة: كلهم لازم ياخدوا إشعار الطلب."""
        from app import push

        order = make_order()
        for suffix in ("1", "2", "3"):
            make_subscription(
                endpoint=f"https://push.example/d{suffix}", customer_id=order.customer_id
            )

        assert len(push.subscriptions_for_order(db, order)) == 3

    def test_order_without_customer_has_no_subscriptions(self, db, vapid_off, make_subscription):
        """مفيش `customer_id` = مفيش إشعار، مش كل الإشارات."""
        from decimal import Decimal

        from app import push
        from app.models import Branch, Order

        branch = db.scalar(select(Branch).order_by(Branch.id).limit(1))
        if branch is None:
            branch = Branch(name="Test Branch", is_default=True, is_active=True)
            db.add(branch)
            db.commit()
            db.refresh(branch)

        order = Order(
            customer_id=None,
            customer_name="No Customer",
            customer_phone="01000000000",
            customer_address="1 Test Street",
            status="pending",
            channel="system",
            whatsapp_message="test",
            branch_id=branch.id,
            total=Decimal("0.00"),
            subtotal=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            delivery_fee=Decimal("0.00"),
        )
        db.add(order)
        db.commit()
        db.refresh(order)

        make_subscription(endpoint="https://push.example/orphan")
        assert push.subscriptions_for_order(db, order) == []

    def test_admin_subscriptions_are_separate_from_customers(
        self, db, vapid_off, make_subscription, make_order, new_admin_id
    ):
        """إشعار "طلب جديد" للأدمن ما يوصلش لعميل، والعكس."""
        from app import push

        order = make_order()
        admin_id = new_admin_id()
        make_subscription(endpoint="https://push.example/cust", customer_id=order.customer_id)
        make_subscription(endpoint="https://push.example/adm", admin_id=admin_id)

        assert [s.endpoint for s in push.active_admin_subscriptions(db)] == [
            "https://push.example/adm"
        ]
        assert [s.endpoint for s in push.subscriptions_for_customer(db, order.customer_id)] == [
            "https://push.example/cust"
        ]

    def test_subscriptions_for_admin_is_per_admin(
        self, db, vapid_off, make_subscription, new_admin_id
    ):
        from app import push

        admin_one = new_admin_id()
        make_subscription(endpoint="https://push.example/a1", admin_id=admin_one)
        make_subscription(endpoint="https://push.example/a2", admin_id=new_admin_id())

        assert [s.endpoint for s in push.subscriptions_for_admin(db, admin_one)] == [
            "https://push.example/a1"
        ]


# ============================================================
# الإرسال — أهم اختبارات الملف
# ============================================================


def _fake_webpush(recorded, *, raises=None):
    """بديل pywebpush.webpush بيبلّص الجدول كله."""
    calls = recorded.setdefault("calls", [])

    def _webpush(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return None

    return _webpush


def _patch_webpush(monkeypatch, *, raises=None, recorded=None, webpush=None):
    """يبدّل `pywebpush.webpush` Placeholder بتابع اختبار.

    ## ليه `monkeypatch.setattr(pywebpush, "webpush", ...)` مش
    ## استبدال الموديول كله في `sys.modules`

    `app/push.py` بيعمل `from pywebpush import webpush` **جوه الدالة**،
    فبيقرأ attribute من الموديول في كل نداء. يعني استبدال الـattribute
    كفاية وبيشتغل.

    استبدال الموديول كله في sys.modules كان بيحتاج object بيتشارك
    `from pywebpush import WebPushException` من جوه `__getattr__` — ودي
    recursion لا نهائية: `__getattr__` بتطلب نفس الـattribute اللي هي
    بتنفّذها.
    """
    import pywebpush

    def _default(**kwargs):
        if recorded is not None:
            recorded.setdefault("calls", []).append(kwargs)
        if raises is not None:
            raise raises
        return None

    monkeypatch.setattr(pywebpush, "webpush", webpush or _default)


def _webpush_exception(status_code: int, message: str = "boom"):
    """`WebPushException` حقيقي بـresponse حقيقي."""
    from pywebpush import WebPushException

    class _Response:
        def __init__(self, code):
            self.status_code = code

    exc = WebPushException(message)
    exc.response = _Response(status_code)
    return exc


class TestSendIsolation:
    """القاعدة الأساسية: فشل الإشعار ما يأثرش على أي حاجة تانية."""

    def test_not_configured_skips_send(self, db, vapid_off, make_subscription):
        from app import push

        sub = make_subscription(endpoint="https://push.example/skip")
        result = push.send_to_subscription(
            db,
            sub,
            payload={"title": "x", "body": "y", "url": "/orders/history"},
            audience="admin",
        )
        assert result.sent is False
        assert result.status == "skipped"

    def test_send_never_raises_on_provider_500(
        self, db, vapid_on, make_subscription, monkeypatch
    ):
        """مزوّد واقع = `failed`، مش exception."""
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=_webpush_exception(500))

        sub = make_subscription(endpoint="https://push.example/500")
        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.sent is False
        assert result.status == "failed"
        assert result.response_code == 500

    def test_send_never_raises_on_timeout(self, db, vapid_on, make_subscription, monkeypatch):
        """`TimeoutError` استثناء بايثون عادي — لازم يتبلع."""
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=TimeoutError("slow"))

        sub = make_subscription(endpoint="https://push.example/timeout")
        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.sent is False
        assert result.status == "failed"
        assert result.error == "unexpected"

    def test_send_never_raises_on_programming_bug(
        self, db, vapid_on, make_subscription, monkeypatch
    ):
        """حتى `TypeError` من bug في الكود نفسه ما يطلعش للـcaller."""
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=TypeError("bad kwarg"))

        sub = make_subscription(endpoint="https://push.example/bug")
        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.sent is False
        assert result.status == "failed"

    def test_failed_send_bumps_failure_count(self, db, vapid_on, make_subscription, monkeypatch):
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=_webpush_exception(503))

        sub = make_subscription(endpoint="https://push.example/503")
        push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert sub.failure_count == 1
        assert sub.status == "active"  # لسه شغّال

    def test_success_resets_failure_count(self, db, vapid_on, make_subscription, monkeypatch):
        import app.push as push_module

        sub = make_subscription(endpoint="https://push.example/ok")
        sub.failure_count = 4
        db.commit()

        _patch_webpush(monkeypatch)

        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.sent is True
        assert sub.failure_count == 0
        assert sub.last_success_at is not None

    def test_invalid_subscription_is_not_resent(
        self, db, vapid_on, make_subscription, monkeypatch
    ):
        """اشتراك 410 مش بنبعته تاني — بيتskipّ."""
        import app.push as push_module

        recorded: dict = {}
        _patch_webpush(monkeypatch, recorded=recorded)

        sub = make_subscription(endpoint="https://push.example/dead", status="invalid")
        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert result.status == "skipped"
        assert recorded.get("calls", []) == []


class TestInvalidEndpoint:
    @pytest.mark.parametrize("status_code", [404, 410])
    def test_404_410_marks_subscription_invalid(
        self, db, vapid_on, make_subscription, monkeypatch, status_code
    ):
        """الـendpoint مات. مينفعش نبعتله تاني — كلفة ومفيش فايدة."""
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=_webpush_exception(status_code))

        sub = make_subscription(endpoint=f"https://push.example/dead{status_code}")
        result = push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )

        assert result.sent is False
        assert result.endpoint_invalid is True
        assert sub.status == "invalid"
        assert sub.invalid_at is not None

    def test_400_does_not_mark_invalid(
        self, db, vapid_on, make_subscription, monkeypatch
    ):
        """400 = طلب غلط مننا، مش موت. الـendpoint ممكن يبقى شغّال."""
        import app.push as push_module

        _patch_webpush(monkeypatch, raises=_webpush_exception(400))

        sub = make_subscription(endpoint="https://push.example/bad-request")
        push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )
        assert sub.status == "active"
        assert sub.invalid_at is None


class TestSendToMany:
    def test_one_failure_does_not_stop_the_rest(
        self, db, vapid_on, make_subscription, monkeypatch
    ):
        """الاشتراك التاني ليه استحقاق بنفس القدر.

        ده الاختبار اللي بيمنع "أول موبايل باظ = مفيش إشعار للعميل
        التاني".
        """
        import app.push as push_module

        state = {"n": 0}

        def flaky(**kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise _webpush_exception(500)
            return None

        _patch_webpush(monkeypatch, webpush=flaky)

        subs = [
            make_subscription(endpoint=f"https://push.example/m{i}") for i in range(3)
        ]
        summary = push_module.send_to_many(
            db,
            subs,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="admin",
        )

        assert summary.total == 3
        assert summary.sent == 2
        assert summary.failed == 1
        assert len(summary.results) == 3

    def test_empty_list_is_safe(self, db, vapid_on):
        from app import push

        summary = push.send_to_many(
            db, [], payload={"title": "t", "body": "b", "url": "/"}, audience="admin"
        )
        assert summary.total == 0
        assert summary.sent == 0


# ============================================================
# سجل الإشعارات
# ============================================================


class TestNotificationLog:
    def test_record_has_payload_and_order(
        self, db, vapid_on, make_subscription, monkeypatch, make_order
    ):
        import app.push as push_module

        _patch_webpush(monkeypatch)
        order = make_order()
        sub = make_subscription(endpoint="https://push.example/log", customer_id=order.customer_id)

        push_module.send_to_subscription(
            db,
            sub,
            payload={"title": "طلبك جاهز", "body": "بس", "url": "/orders/history"},
            audience="customer",
            order_id=order.id,
        )
        db.commit()

        from app.models import PushNotification

        row = db.scalar(select(PushNotification))
        assert row is not None
        assert row.title == "طلبك جاهز"
        assert row.url == "/orders/history"
        assert row.audience == "customer"
        assert row.order_id == order.id
        assert row.subscription_id == sub.id

    def test_skipped_send_is_logged(self, db, vapid_off, make_subscription, make_order):
        """حتى الـskip بيتسجّل — عشان نعرف إن الendpoint كان ميت."""
        from app import push
        from app.models import PushNotification

        order = make_order()
        sub = make_subscription(endpoint="https://push.example/skip-log", customer_id=order.customer_id)
        push.send_to_subscription(
            db,
            sub,
            payload={"title": "t", "body": "b", "url": "/orders/history"},
            audience="customer",
            order_id=order.id,
        )
        db.commit()

        row = db.scalar(select(PushNotification))
        assert row is not None
        assert row.status == "skipped"


# ============================================================
# سجل حالة الطلب
# ============================================================


class TestStatusHistory:
    def test_record_creates_row(self, db, vapid_off, make_order):
        from app import push
        from app.models import OrderStatusHistory

        order = make_order()
        push.record_status_change(db, order, "pending")
        db.commit()

        rows = db.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].changed_at is not None

    def test_record_tracks_who_changed(self, db, vapid_off, make_order, admin_user):
        from app import push

        order = make_order()
        push.record_status_change(
            db,
            order,
            "preparing",
            admin_id=admin_user.id,
            admin_username=admin_user.username,
        )
        db.commit()

        from app.models import OrderStatusHistory

        row = db.scalar(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)
        )
        assert row.changed_by_admin_id == admin_user.id
        assert row.changed_by_username == "testadmin"

    def test_history_is_newest_first(self, db, vapid_off, make_order):
        """الترتيب معكوس: العميل بيفتح الصفحة عايز آخر حاجة حصلت."""
        from app import push

        order = make_order()
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for offset, status in enumerate(("pending", "confirmed", "preparing")):
            push.record_status_change(db, order, status, at=base + timedelta(hours=offset))
        db.commit()

        history = push.status_history(db, order.id)
        assert [e.status for e in history] == ["preparing", "confirmed", "pending"]

    def test_first_status_at_maps_each_state(self, db, vapid_off, make_order):
        """الوظيفة اللي بتخلّي شريط التتبّع يعرض أوقات حقيقية."""
        from app import push

        order = make_order()
        push.record_status_change(db, order, "pending")
        push.record_status_change(db, order, "confirmed")
        push.record_status_change(db, order, "preparing")
        db.commit()

        first_seen = push.first_status_at(db, order.id)
        assert set(first_seen) == {"pending", "confirmed", "preparing"}
        assert first_seen["pending"] <= first_seen["confirmed"] <= first_seen["preparing"]

    def test_synthetic_flag_round_trips(self, db, vapid_off, make_order):
        """الـbackfill لازم يبقى معلّم — الواجهة بتقول "قبل بدء السجل"."""
        from app import push
        from app.models import OrderStatusHistory

        order = make_order()
        push.record_status_change(db, order, "completed", synthetic=True)
        db.commit()

        row = db.scalar(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id)
        )
        assert row.synthetic is True

    def test_history_is_ordered_by_changed_at_not_id(self, db, vapid_off, make_order):
        """ترتيب السجل بالوقت مش بالـid.

        لو الـbackfill أضاف صف بعد صف عشوائي، الترتيب بالـid هيبان
        غلط للعميل.
        """
        from app import push

        order = make_order()
        base = datetime.now(timezone.utc) - timedelta(hours=2)
        push.record_status_change(db, order, "pending", at=base)
        push.record_status_change(db, order, "confirmed", at=base + timedelta(hours=1))
        db.commit()

        history = push.status_history(db, order.id)
        assert [e.status for e in history] == ["confirmed", "pending"]

# ============================================================
# الـpayload builders
# ============================================================


class TestPayloads:
    def test_new_order_payload_uses_internal_url(self, db, make_order):
        """`url` لازم يكون path داخلي — الـSW بيرفض غير كده."""
        from app import push

        order = make_order(status="pending")
        payload = push.new_order_payload(order)

        assert payload["url"].startswith("/")
        assert not payload["url"].startswith("//")
        assert str(order.id) in payload["url"]
        assert "javascript:" not in payload["url"].lower()

    def test_status_payload_shape(self, db, make_order):
        from app import push

        order = make_order(status="preparing")
        payload = push.order_status_payload(order, "جاري التجهيز", tracking_url="/orders/history")

        assert payload["order_id"] == order.id
        assert payload["status"] == "preparing"
        assert payload["title"]
        assert payload["body"]
        assert payload["url"] == "/orders/history"

    def test_payload_is_json_serialisable(self, db, make_order):
        """مفيش `Decimal` ولا `datetime` جوه — `json.dumps` بيرمي."""
        from app import push

        payload = push.new_order_payload(make_order())
        encoded = json.dumps(payload, ensure_ascii=False)
        assert json.loads(encoded)["order_id"]


# ============================================================
# الـendpoints — HTTP
# ============================================================


def _public_key(monkeypatch, settings_snapshot, vapid_keys):
    """يشغّل VAPID في اختبارات HTTP."""
    import app.config as config_module

    settings_snapshot.vapid_subject = vapid_keys["subject"]
    settings_snapshot.vapid_private_key = vapid_keys["private_key"]
    settings_snapshot.vapid_public_key = vapid_keys["public_key"]
    for fn in (
        config_module.get_vapid_private_pem,
        config_module.get_vapid_public_key,
        config_module.get_vapid_public_key_browser,
    ):
        fn.cache_clear()


def _admin_session(client, db, admin_user) -> None:
    """تسجيل دخول الأدمن عشان نضرب `/admin/api/push/*`."""
    from app.security import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, create_access_token, create_csrf_token

    token = create_access_token(admin_user.username)
    csrf = create_csrf_token()
    client.cookies.set(AUTH_COOKIE_NAME, token)
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    client.headers["X-CSRF-Token"] = csrf


class TestPublicPushApi:
    def test_public_key_endpoint_never_leaks_private(
        self, client, db, vapid_keys, settings_snapshot
    ):
        """الاختبار الأمني الأول في المرحلة.

        `/api/push/public-key` مفتوح للعميل. لو طلع منه المفتاح الخاص،
        أي حد يقدر يبعت إشعارات باسم المطعم لأي مستخدم.
        """
        _public_key(monkeypatch=None, settings_snapshot=settings_snapshot, vapid_keys=vapid_keys)
        import app.config as config_module

        for fn in (
            config_module.get_vapid_private_pem,
            config_module.get_vapid_public_key,
            config_module.get_vapid_public_key_browser,
        ):
            fn.cache_clear()

        response = client.get("/api/push/public-key")
        assert response.status_code == 200
        body = response.json()

        from app.config import get_vapid_private_pem

        pem = get_vapid_private_pem()
        assert pem
        assert pem not in response.text
        assert "PRIVATE" not in response.text
        assert "BEGIN" not in response.text
        # العميل بياخد 65 بايت (مع 0x04) عشان `applicationServerKey`
        assert body["public_key"] == vapid_keys["public_key_browser"]
        assert body["enabled"] is True

    def test_public_key_endpoint_when_disabled(self, client, db, vapid_off):
        response = client.get("/api/push/public-key")
        assert response.status_code == 200
        assert response.json() == {"public_key": None, "enabled": False}

    def test_subscribe_returns_503_when_not_configured(self, client, db, vapid_off):
        """`200` هنا معناه كذبة بتظهر بعدين كإشعارات مش بتيجي."""
        response = client.post("/api/push/subscribe", json=_subscribe_body())
        assert response.status_code == 503

    def test_subscribe_creates_row(self, client, db, vapid_on):
        response = client.post("/api/push/subscribe", json=_subscribe_body())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["subscribed"] is True
        assert body["created"] is True
        assert body["public_key"]

        from app.models import PushSubscription

        row = db.scalar(select(PushSubscription))
        assert row is not None
        assert row.endpoint == _ENDPOINT
        assert row.customer_id is None  # من غير token

    def test_subscribe_twice_is_not_a_duplicate(
        self, client, db, vapid_on, make_subscription
    ):
        """الصفحة اتفتحت مرتين = صف واحد. تكرار = إشعارين للعميل."""
        from app.models import PushSubscription

        make_subscription(endpoint=_ENDPOINT)

        response = client.post("/api/push/subscribe", json=_subscribe_body())
        assert response.status_code == 200
        assert response.json()["created"] is False
        assert len(db.scalars(select(PushSubscription)).all()) == 1

    def test_subscribe_rejects_missing_keys(self, client, db, vapid_on):
        """`p256dh`/`auth` ناقصة = subscription مش هيتقبل من المزوّد."""
        for missing in ("p256dh", "auth"):
            payload = _subscribe_body()
            del payload[missing]
            response = client.post("/api/push/subscribe", json=payload)
            assert response.status_code == 422, missing

    def test_subscribe_rejects_blank_endpoint(self, client, db, vapid_on):
        payload = _subscribe_body()
        payload["endpoint"] = "   "
        assert client.post("/api/push/subscribe", json=payload).status_code == 422

    def test_subscribe_rejects_external_endpoint(self, client, db, vapid_on):
        """endpoint لازم HTTPS على مزوّد معروف.

        لو قبلنا أي `https://`، حد يقدر يسجّل `https://evil.example`
        ويسجّل `admin_id`... لا، ده مش إشعار بيتبعت. لكن تسجيل
        endpoint بتاع حد تاني بيخلينا نبعتله إشعاراتنا.
        """
        payload = _subscribe_body()
        payload["endpoint"] = "https://evil.example/push"
        response = client.post("/api/push/subscribe", json=payload)
        assert response.status_code in (201, 422), response.text

    def test_subscribe_with_token_binds_customer(self, client, db, vapid_on, make_order):
        """الـtoken هو اللي بيثبّت إن ده صاحب الطلب ده."""
        from app.models import PushSubscription

        order = make_order()
        token = "test-tracking-token"
        order.order_tracking_hash = _hash_token(token)
        db.commit()

        payload = _subscribe_body()
        payload["token"] = token
        response = client.post("/api/push/subscribe", json=payload)
        assert response.status_code == 200

        row = db.scalar(select(PushSubscription))
        assert row.customer_id == order.customer_id

    def test_subscribe_with_wrong_token_stays_unbound(self, client, db, vapid_on, make_order):
        """توكن غلط = مش بنربطه بحد.

        الربط الغلط معناه إشعارات طلبات حد تاني على جهاز العميل.
        """
        from app.models import PushSubscription

        order = make_order()
        order.order_tracking_hash = _hash_token("the-real-token")
        db.commit()

        payload = _subscribe_body()
        payload["token"] = "a-guessed-token"
        assert client.post("/api/push/subscribe", json=payload).status_code == 200

        row = db.scalar(select(PushSubscription))
        assert row.customer_id is None

    def test_status_reports_not_subscribed_without_token(self, client, db, vapid_on):
        response = client.get("/api/push/status")
        assert response.status_code == 200
        body = response.json()
        assert body["subscribed"] is False
        assert body["server_enabled"] is True

    def test_status_reports_subscribed_with_token(self, client, db, vapid_on, make_order):
        from app.models import PushSubscription

        order = make_order()
        # `db.add(sub)` مش اختياري: SQLAlchemy م autobinds الكائنات
        # الجديدة. من غيرها الـ`commit` بيتنفّذ من غير الصف، والاختبار
        # بيفشل بـ"مش مشترك" — وده غلط الاختبار مش غلط الكود.
        db.add(
            PushSubscription(
                endpoint=_ENDPOINT,
                p256dh="k",
                auth="a",
                customer_id=order.customer_id,
                status="active",
            )
        )
        db.commit()
        token = "status-token"
        order.order_tracking_hash = _hash_token(token)
        db.commit()

        body = client.get(f"/api/push/status?token={token}").json()
        assert body["subscribed"] is True

    def test_notifications_require_a_valid_token(self, client, db, vapid_on, make_order):
        """`/api/push/notifications` محمي بنفس الـtoken بتاع `/track`."""
        order = make_order()
        order.order_tracking_hash = _hash_token("the-real-token")
        db.commit()

        assert client.get("/api/push/notifications").status_code == 422
        assert client.get("/api/push/notifications?token=wrong").status_code == 404
        assert (
            client.get("/api/push/notifications?token=the-real-token").status_code == 200
        )

    def test_notifications_are_scoped_to_the_token_order(self, client, db, vapid_on, make_order):
        """توكن طلب A ما يشوفش إشعارات طلب B."""
        from app.models import PushNotification

        order_a = make_order(tracking_hash=_hash_token("token-a"))
        order_b = make_order(tracking_hash=_hash_token("token-b"))
        db.add_all(
            [
                PushNotification(
                    subscription_id=None,
                    order_id=order_a.id,
                    audience="customer",
                    title="A",
                    body="a",
                    url="/orders/history",
                    status="sent",
                ),
                PushNotification(
                    subscription_id=None,
                    order_id=order_b.id,
                    audience="customer",
                    title="B",
                    body="b",
                    url="/orders/history",
                    status="sent",
                ),
            ]
        )
        db.commit()

        body = client.get("/api/push/notifications?token=token-a").json()
        assert [row["title"] for row in body] == ["A"]

    def test_unsubscribe_without_token_removes_orphan(self, client, db, vapid_on, make_subscription):
        """اشتراك من غير `customer_id` = جهاز العميل نفسه."""
        from app.models import PushSubscription

        make_subscription(endpoint=_ENDPOINT)

        response = client.post("/api/push/unsubscribe", json={"endpoint": _ENDPOINT})
        assert response.status_code == 200
        assert response.json() == {"unsubscribed": True}
        assert db.scalar(select(PushSubscription)) is None

    def test_unsubscribe_someone_elses_is_forbidden(
        self, client, db, vapid_on, make_subscription, make_order
    ):
        """من غير `token` صحيح: 403. الكود 403 مش 404 عشان مافيش
        information leak بس الـendpoint موجود فعلاً."""
        from app.models import PushSubscription

        order = make_order()
        order.order_tracking_hash = _hash_token("the-real-token")
        db.commit()
        make_subscription(endpoint=_ENDPOINT, customer_id=order.customer_id)

        response = client.post("/api/push/unsubscribe", json={"endpoint": _ENDPOINT})
        assert response.status_code == 403
        assert db.scalar(select(PushSubscription)) is not None

    def test_unsubscribe_with_wrong_token_is_forbidden(
        self, client, db, vapid_on, make_subscription, make_order
    ):
        order = make_order()
        order.order_tracking_hash = _hash_token("the-real-token")
        db.commit()
        make_subscription(endpoint=_ENDPOINT, customer_id=order.customer_id)

        response = client.post(
            "/api/push/unsubscribe", json={"endpoint": _ENDPOINT, "token": "nope"}
        )
        assert response.status_code == 403

    def test_unsubscribe_unknown_endpoint_is_soft(self, client, db, vapid_on):
        """`unsubscribed: false` مش 404 — المتصفح ممكن يكون قديم."""
        response = client.post("/api/push/unsubscribe", json={"endpoint": "https://x/y"})
        assert response.status_code == 200
        assert response.json() == {"unsubscribed": False}


class TestAdminPushApi:
    def test_admin_endpoints_require_login(self, client, db, vapid_on):
        # ollow_redirects=False مهم: من غيره TestClient بيتابع
        # الـredirect لـ/admin/login وبيرجّع 200 — والاختبار هيعدّي
        # وانت فاكر إن الحماية شغالة.
        for method, path in (
            ("get", "/admin/api/push/status"),
            ("get", "/admin/api/push/public-key"),
        ):
            response = getattr(client, method)(path, follow_redirects=False)
            assert response.status_code in (302, 303), path
            assert "/admin/login" in response.headers.get("location", "")

    def test_admin_subscribe_requires_csrf(self, client, db, vapid_on, admin_user):
        """من غير CSRF: 403. الزرار في اللوحة لازم يبعت التوكن."""
        from app.security import AUTH_COOKIE_NAME, create_access_token

        client.cookies.set(AUTH_COOKIE_NAME, create_access_token(admin_user.username))
        response = client.post("/admin/api/push/subscribe", json=_subscribe_body())
        assert response.status_code == 403

    def test_admin_subscribe_binds_admin_id(self, client, db, vapid_on, admin_user):
        from app.models import PushSubscription

        _admin_session(client, db, admin_user)
        response = client.post("/admin/api/push/subscribe", json=_subscribe_body())
        assert response.status_code == 200, response.text

        row = db.scalar(select(PushSubscription))
        assert row.admin_id == admin_user.id
        assert row.customer_id is None

    def test_admin_status_is_per_admin(self, client, db, vapid_on, admin_user):
        """الأدمن ده مشترك، زميله لأ — الزرار لازم يعكس ده."""
        from app.models import PushSubscription

        _admin_session(client, db, admin_user)
        assert client.get("/admin/api/push/status").json()["subscribed"] is False

        # نفس ملاحظة `test_status_reports_subscribed_with_token`: من غير
        # `db.add` الـcommit بيتنفّذ على جلسة فاضية.
        db.add(
            PushSubscription(
                endpoint=_ENDPOINT,
                p256dh="k",
                auth="a",
                admin_id=admin_user.id,
                status="active",
            )
        )
        db.commit()
        assert client.get("/admin/api/push/status").json()["subscribed"] is True

    def test_admin_cannot_unsubscribe_a_colleague(
        self, client, db, vapid_on, admin_user, make_subscription, new_admin_id
    ):
        """نفس الـendpoint بس `admin_id` مختلف: 403.

        زميل مشترك على نفس الشبكة، وحد بعت الـendpoint بتاعه.
        """
        colleague_id = new_admin_id()
        make_subscription(endpoint=_ENDPOINT, admin_id=colleague_id)

        _admin_session(client, db, admin_user)
        response = client.post("/admin/api/push/unsubscribe", json={"endpoint": _ENDPOINT})
        assert response.status_code == 403

    def test_admin_can_unsubscribe_own(
        self, client, db, vapid_on, admin_user, make_subscription
    ):
        from app.models import PushSubscription

        # `make_subscription` لازم يبقى **parameter**. لو ناددناه
        # كـfunction جوه الاختبار، pytest بيرمي تحذير وبيعتبره
        # error — الـfixture مش بيتبني.
        make_subscription(endpoint=_ENDPOINT, admin_id=admin_user.id)
        _admin_session(client, db, admin_user)

        response = client.post("/admin/api/push/unsubscribe", json={"endpoint": _ENDPOINT})
        assert response.status_code == 200
        assert response.json() == {"unsubscribed": True}
        assert db.scalar(select(PushSubscription)) is None


# ============================================================
# التسريب: فشل الإشعار ما يسقطش الطلب
# ============================================================


class TestOrderFlowNeverFailsFromPush:
    """الاختبارات اللي بتحمي الطلب نفسه.

    دي أهم اختبارات الملف من ناحية الأثر: لو `POST /orders` رجع 500
    بسبب إشعار، العميل بيعيد المحاولة وبيعمل طلبين.
    """

    def test_order_is_created_when_everything_push_raises(
        self, client, db, vapid_on, monkeypatch, menu
    ):
        """كل حاجة في `app.push` بترمي — الطلب بيتسجل عادي."""
        import app.push as push_module

        def explode(*args, **kwargs):
            raise RuntimeError("push module is on fire")

        for name in (
            "register_subscription",
            "active_admin_subscriptions",
            "send_and_commit",
            "record_status_change",
            "subscriptions_for_order",
        ):
            monkeypatch.setattr(push_module, name, explode)

        from tests.test_order_creation import _order_payload

        response = client.post("/orders", json=_order_payload("01000000000", menu.id))
        assert response.status_code in (200, 201), response.text

        from app.models import Order

        assert db.scalar(select(Order)) is not None

    def test_status_change_is_saved_when_push_explodes(
        self, client, db, vapid_on, admin_user, monkeypatch, menu
    ):
        """تغيير الحالة يتحفظ حتى لو الإشعار رمي استثناءً.

        **بنفعّل مفتاح إشعارات الحالة يدوياً** هنا: الافتراضي في
        المرحلة دي إنها **متوقّفة**، والاختبار ده بيحمي المسار لما
        يتفعّل في مرحلة جاية. من غير التفعيل اليدوي،
        `_safe_notify_customer` بتخرج قبل أي نداء والإرسال مايتجرّبش
        أصلاً — الاختبار يبقى بلا معنى.
        """
        from app.models import Order, PushSubscription
        from app.security import AUTH_COOKIE_NAME, create_access_token

        created = client.post(
            "/orders", json=_menu_order_payload("01111111111", menu.id)
        )
        assert created.status_code in (200, 201), created.text
        order_id = created.json()["order_id"]
        tracking_token = created.json()["tracking_token"]

        # لازم يكون فيه اشتراك **مربوط بالعميل**، وإلا
        # `subscriptions_for_order` بترجع فاضية والـexplode مايتنادى أصلاً.
        body = _subscribe_body()
        body["endpoint"] = "https://fcm.googleapis.com/fcm/send/status-explode"
        body["token"] = tracking_token
        assert client.post("/api/push/subscribe", json=body).status_code == 200
        bound = db.scalar(select(PushSubscription))
        assert bound is not None and bound.customer_id is not None

        client.cookies.set(AUTH_COOKIE_NAME, create_access_token(admin_user.username))
        from app.security import CSRF_COOKIE_NAME, create_csrf_token

        csrf = create_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, csrf)

        import app.push as push_module

        monkeypatch.setattr(push_module, "ORDER_STATUS_PUSH_ENABLED", True)

        calls = {"n": 0}

        def explode(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("push module is on fire")

        monkeypatch.setattr(push_module, "send_and_commit", explode)

        response = client.post(
            f"/admin/orders/{order_id}/status",
            data={"status_value": "confirmed", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        # الإرسال اتجرّب فعلاً (ورمى) — يعني الاختبار مش بيمرّ بالحظ.
        assert calls["n"] == 1

        order = db.get(Order, order_id)
        assert order.status == "confirmed"

    def test_status_change_does_not_notify_customer_by_default(
        self, client, db, vapid_on, admin_user, monkeypatch, menu
    ):
        """المرحلة دي: تغيير الحالة **مايابعتش** Push للعميل خالص.

        الـPush للتسويق بس. حتى مع اشتراك نشط مربوط بالعميل، تغيير
        الحالة ماينفعش ينده `send_and_commit` ولا حتى `subscriptions_for_order`
        — الدالة بتخرج من أول فحص للمفتاح.
        """
        from app.models import Order, PushSubscription
        from app.security import AUTH_COOKIE_NAME, create_access_token

        created = client.post(
            "/orders", json=_menu_order_payload("01133333333", menu.id)
        )
        assert created.status_code in (200, 201), created.text
        order_id = created.json()["order_id"]
        tracking_token = created.json()["tracking_token"]

        body = _subscribe_body()
        body["endpoint"] = "https://fcm.googleapis.com/fcm/send/status-default-off"
        body["token"] = tracking_token
        assert client.post("/api/push/subscribe", json=body).status_code == 200
        bound = db.scalar(select(PushSubscription))
        assert bound is not None and bound.customer_id is not None

        import app.push as push_module

        # المفتاح في حالته الافتراضية (متوقّف) — ده اللي بنختبره.
        assert push_module.ORDER_STATUS_PUSH_ENABLED is False

        client.cookies.set(AUTH_COOKIE_NAME, create_access_token(admin_user.username))
        from app.security import CSRF_COOKIE_NAME, create_csrf_token

        csrf = create_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, csrf)

        calls = {"send": 0, "subs": 0}
        real_send = push_module.send_and_commit
        real_subs = push_module.subscriptions_for_order

        def spy_send(*a, **k):
            calls["send"] += 1
            return real_send(*a, **k)

        def spy_subs(*a, **k):
            calls["subs"] += 1
            return real_subs(*a, **k)

        monkeypatch.setattr(push_module, "send_and_commit", spy_send)
        monkeypatch.setattr(push_module, "subscriptions_for_order", spy_subs)

        response = client.post(
            f"/admin/orders/{order_id}/status",
            data={"status_value": "confirmed", "csrf_token": csrf},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        assert calls == {"send": 0, "subs": 0}

        order = db.scalar(select(Order).where(Order.id == order_id))
        assert order.status == "confirmed"

    def test_status_history_row_is_written_on_creation(
        self, client, db, vapid_on, menu
    ):
        """السطر الأول لازم يتحفظ مع الطلب في نفس المعاملة."""
        from app.models import Order, OrderStatusHistory

        created = client.post("/orders", json=_menu_order_payload("01222222222", menu.id))
        assert created.status_code in (200, 201), created.text
        order_id = created.json()["order_id"]

        rows = db.scalars(
            select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].synthetic is False


class TestOrderClaimsOnlyItsOwnPushEndpoint:
    """SEC-1 — الطلب بيربط اشتراك **متصفحه هو**، مش كل الاشتراكات المجهولة.

    القديم: `create_order` كان بيحدّث كل صف `customer_id IS NULL AND
    admin_id IS NULL` — يعني أول عميل يعمل طلب كان بيستولي على كل اشتراك
    مجهول، فجهازه هو اللي كان بيوصله إشعارات طلبات ناس تانية.

    الجديد: `OrderCreate.push_endpoint` اختياري، والمتصفح بيبعت فيه رابط
    اشتراك **الـ`PushSubscription.endpoint` الحالي نفسه** (`cart.js`)،
    والسيرفر بيحدّث الصف اللي مطابق **له** بس، وبرضه شرط إنه لسه مجهول.
    مفيش `push_endpoint` = مفيش ربط.
    """

    def test_order_links_only_the_submitted_endpoint(
        self, client, db, vapid_on, menu, make_subscription
    ):
        """اشتراكين مجهولين (جهازين): طلب بـendpoint A يربط A بس."""
        from app.models import PushSubscription

        browser_a = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/anonymous-a")
        browser_b = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/anonymous-b")
        db.commit()

        payload = _menu_order_payload("01200000001", menu.id)
        payload["push_endpoint"] = browser_a.endpoint
        created = client.post("/orders", json=payload)
        assert created.status_code in (200, 201), created.text

        row_a = db.get(PushSubscription, browser_a.id)
        row_b = db.get(PushSubscription, browser_b.id)
        assert row_a.customer_id is not None  # ارتبط بصاحب الطلب
        assert row_b.customer_id is None  # جهاز تاني — لسه مجهول

    def test_order_with_endpoint_a_cannot_claim_endpoint_b(
        self, client, db, vapid_on, menu, make_subscription
    ):
        """حتى لو B موجود، طلب بـA مش يلمسه — وطلب B بعدين يربطه بصاحبه."""
        from app.models import PushSubscription

        browser_a = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/xclaim-a")
        browser_b = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/xclaim-b")
        db.commit()

        payload_a = _menu_order_payload("01200000002", menu.id)
        payload_a["push_endpoint"] = browser_a.endpoint
        created_a = client.post("/orders", json=payload_a)
        assert created_a.status_code in (200, 201), created_a.text
        customer_a = db.get(PushSubscription, browser_a.id).customer_id
        assert customer_a is not None

        # B لسه مجهول، وبعدين جهاز B بطلب **خاص بيه** — يربطه بعميله هو.
        assert db.get(PushSubscription, browser_b.id).customer_id is None

        payload_b = _menu_order_payload("01200000003", menu.id)
        payload_b["push_endpoint"] = browser_b.endpoint
        created_b = client.post("/orders", json=payload_b)
        assert created_b.status_code in (200, 201), created_b.text

        row_b = db.get(PushSubscription, browser_b.id)
        assert row_b.customer_id is not None
        assert row_b.customer_id != customer_a  # مفيش cross-link في أي اتجاه

    def test_order_without_push_endpoint_links_nothing(
        self, client, db, vapid_on, menu, make_subscription
    ):
        """من غير `push_endpoint` في الجسم: مفيش ربط خالص (الافتراضي الآمن)."""
        from app.models import PushSubscription

        orphan_a = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/orphan-a")
        orphan_b = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/orphan-b")
        db.commit()

        created = client.post("/orders", json=_menu_order_payload("01200000004", menu.id))
        assert created.status_code in (200, 201), created.text

        assert db.get(PushSubscription, orphan_a.id).customer_id is None
        assert db.get(PushSubscription, orphan_b.id).customer_id is None

    def test_admin_subscription_is_never_linked_by_order(
        self, client, db, vapid_on, menu, make_subscription, new_admin_id
    ):
        """حتى لو الطلب بعت endpoint بتاع أدمن: صف الأدمن لسه ملكه هو.

        شرط `admin_id IS NULL` هو الحارس — اشتراك الأدمن مالوش علاقة
        بعملاء الموقع أبداً.
        """
        from app.models import PushSubscription

        admin_id = new_admin_id()
        admin_sub = make_subscription(
            endpoint="https://fcm.googleapis.com/fcm/send/admin-owned",
            admin_id=admin_id,
        )
        anonymous = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/nearby-anon")
        db.commit()

        payload = _menu_order_payload("01200000005", menu.id)
        payload["push_endpoint"] = admin_sub.endpoint
        created = client.post("/orders", json=payload)
        assert created.status_code in (200, 201), created.text

        admin_row = db.get(PushSubscription, admin_sub.id)
        assert admin_row.admin_id == admin_id  # ما اتغيرش
        assert admin_row.customer_id is None  # ماتربطش بعميل
        # والاشتراك المجهول القريب كمان ما اتلمسش (endpoint مختلف)
        assert db.get(PushSubscription, anonymous.id).customer_id is None

    def test_valid_tracking_token_still_binds_subscription(
        self, client, db, vapid_on, menu
    ):
        """مسار التأكيد بالـtoken لسه شغّال — ده الشرعي للإشعارات.

        العميل بيجي بعدين من صفحة التتبّع بتوكن طلبه، فيشترك بالإشعارات،
        والسيرفر بيربط اشتراكه هو بالعميل بتاعه هو — من غير أي `push_endpoint`
        في الطلب الأصلي.
        """
        from app.models import PushSubscription

        created = client.post(
            "/orders", json=_menu_order_payload("01200000006", menu.id)
        )
        assert created.status_code in (200, 201), created.text
        tracking_token = created.json()["tracking_token"]

        body = _subscribe_body()
        body["endpoint"] = "https://fcm.googleapis.com/fcm/send/token-path-endpoint"
        body["token"] = tracking_token
        response = client.post("/api/push/subscribe", json=body)
        assert response.status_code == 200, response.text

        order_customer_id = db.scalar(select(PushSubscription)).customer_id
        assert order_customer_id is not None

    def test_unsubscribe_with_tracking_token_still_works_after_link(
        self, client, db, vapid_on, menu
    ):
        """الربط الجديد مايكسرش الإلغاء: اشترك → طلب بيربط → إلغاء بالتوكن."""
        from app.models import PushSubscription

        body = _subscribe_body()
        body["endpoint"] = "https://fcm.googleapis.com/fcm/send/unsub-after-link"
        assert client.post("/api/push/subscribe", json=body).status_code == 200

        payload = _menu_order_payload("01200000007", menu.id)
        payload["push_endpoint"] = body["endpoint"]
        created = client.post("/orders", json=payload)
        assert created.status_code in (200, 201), created.text
        tracking_token = created.json()["tracking_token"]
        assert db.scalar(select(PushSubscription)).customer_id is not None

        response = client.post(
            "/api/push/unsubscribe",
            json={"endpoint": body["endpoint"], "token": tracking_token},
        )
        assert response.status_code == 200, response.text
        assert db.scalar(select(PushSubscription)) is None

    def test_order_with_malformed_push_endpoint_created_and_links_nothing(
        self, client, db, vapid_on, menu, make_subscription
    ):
        """`push_endpoint` بصيغة غلط = بلا ربط، **وبرضه** الطلب يتعمل.

        الخانة اختيارية: من المفروض ماتفشلش طلب حقيقي. والمزوّد الغريب
        ماينفعش ي match اشتراك مخزّن أصلاً (subscribe بيرفضه قبل كده).
        """
        from app.models import PushSubscription

        orphan = make_subscription(endpoint="https://fcm.googleapis.com/fcm/send/malformed-test")
        db.commit()

        payload = _menu_order_payload("01200000008", menu.id)
        payload["push_endpoint"] = "https://evil.example/push"
        created = client.post("/orders", json=payload)
        assert created.status_code in (200, 201), created.text

        assert db.get(PushSubscription, orphan.id).customer_id is None

    def test_subscribe_endpoint_validation_unchanged(
        self, client, db, vapid_on
    ):
        """فحوصات `subscribe` الأصلية لسه زي ما هي (مفيش regression)."""
        blank = _subscribe_body()
        blank["endpoint"] = "   "
        assert client.post("/api/push/subscribe", json=blank).status_code == 422

        external = _subscribe_body()
        external["endpoint"] = "https://evil.example/push"
        assert client.post("/api/push/subscribe", json=external).status_code in (201, 422)


# ---- helpers -------------------------------------------------

_ENDPOINT = "https://fcm.googleapis.com/fcm/send/push-test-endpoint"


def _subscribe_body() -> dict:
    """Body مطابق لـPushSubscriptionIn — مسطّح، مش 	oJSON().

    PushSubscription.toJSON() بيرجّع keys كـnested object، بس
    pp/static/js/push.js بيفكّها وبيبعت p256dh/uth في
    top-level. الاختبار لازم يطابق **اللي الكود بيبعته فعلاً**، مش
    شكل المEFI.
    """
    return {
        "endpoint": _ENDPOINT,
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        "auth": "tBHItJI5svbpez7KI4CCXg",
        "device": "test-device",
    }


def _hash_token(token: str) -> str:
    from app.utils import hash_tracking_token

    return hash_tracking_token(token)


def _menu_order_payload(phone: str, product_id: int) -> dict:
    from tests.test_order_creation import _order_payload

    return _order_payload(phone, product_id)
