# تقرير Phase 0 — تثبيت V1

> التاريخ: 2026-09-27
> الفرع: `v2-development` (لم يتم عمل commit)
> الحالة: **269 اختبار نجح كلها**، سلسلة الـmigrations بتتبني صح من الأول
> لآخر، ومفيش انحراف بين الموديل والداتابيز.

Phase 0 كان هدفه يثبّت نسخة V1 ويقوّي إنتاجها، من غير أي شغل V2.
العمل كله على `restaurant_store_test` — **قاعدة بيانات التطوير ما اتلمستش**
(لسه مختومة بـ`b5c8d3e4f5a7`، 19 جدول، من غير `admin_login_attempts`).

---

## 1. النتيجة

| البند | قبل | بعد |
| --- | --- | --- |
| اختبارات | مفيش | 269 |
| بناء الداتابيز من الصفر | مكسور | شغال (upgrade → downgrade → upgrade) |
| انحراف موديل/داتابيز | موجود | ولا واحد |
| حماية تسجيل دخول الأدمن | مفيش | throttling على PostgreSQL |
| لوج | مفيش | JSON/text + request id |
| حماية الكوكيز في الإنتاج | ضعيفة | فاشل-فاست لو `COOKIE_SECURE=false` |

توزيع الاختبارات:

| الملف | العدد | بيغطي |
| --- | --- | --- |
| `tests/test_phone_validation.py` | 87 | التطبيع والتحقق والقناع |
| `tests/test_hardening.py` | 57 | fail-fast، headers، لوج، request id |
| `tests/test_login_guard.py` | 40 | throttle + route الأدمن |
| `tests/test_pages_smoke.py` | 26 | رندر الصفحات + 404 + أصول ثابتة |
| `tests/test_service_worker.py` | 22 | الـPWA |
| `tests/test_order_creation.py` | 18 | مسار الطلب + التسعير |
| `tests/test_migrations.py` | 17 | السلسلة + تطابق الموديل |
| `tests/test_text_hygiene.py` | 2 | محارف من لغات غلط في النصوص |

---

## 2. أهم إصلاحات

### 2.1 سلسلة الـmigrations كانت مكسورة

قاعدة التطوير اتعملت بـ`create_all` من الموديلات وبعدين اتـstamp، فسلسلة
التهجير **ما اشتغلتش من الأول لآخر ولا مرة**. أول نشر على بيئة جديدة
كان هيفشل. اتصلّح:

- `e7f2a1b3c4d5` كان بيعمل `add_column` لعمود موجود فعلاً.
- `c32c9e6af771` الـdowngrade بتاعه كان ناقص.
- `d44b83933e46` التوثيق بتاعه بيقول إنه شال `delivery_options.is_pickup`،
  وهو في الحقيقة ما شالش.
- `orders.payment_method` مفقود من الداتابيز مع إنه في الموديل.
- `ix_order_items_product_id` ناقص.
- تكرار في الـunique constraints على `customers.phone` و`delivery_zones`.

الإضافات الجديدة: `c1d2e3f4a5b6` (جدول محاولات الدخول) و
`b2c3d4e5f6a7` (مواءمة الموديل). الـhead الحالي `b2c3d4e5f6a7` من 16
تهجير.

**تحذير:** اتعدّل على تهجيرات تاريخية. ده آمن للبناء الجديد، بس لو فيه
بيئة اتطبّقت بالفعل لازم تتأكد إنها في نفس الحالة المتوقعة.

### 2.2 حماية تسجيل دخول الأدمن

كان مفتوح ضد Brute-force. دلوقتي (`app/login_guard.py`):

- 5 محاولات فاشلة / 15 دقيقة لكل `(IP + اسم المستخدم)`.
- 20 محاولة / 15 دقيقة لكل IP.
- **التخزين في PostgreSQL** مش في الذاكرة — عشان بيشتغل على أكتر من نسخة
  وبلا Redis.
- المتخزّن بصمة HMAC-SHA256 مش الـIP ولا اسم المستخدم.
- bcrypt بنفس التوقيت للمستخدم الغريب والمستخدم المعطّل، عشان متكشفش
  وجود الحساب من سرعة الرد.

### 2.3 bug في الـrequest id

الـmiddleware كان بيرجّع الـContextVar **قبل** ما يسجل لوج الطلب، فكل
سطر في اللوج بيطلع `request_id="-"` والربط بين لوج العميل ولوج السيرفر
كان مستحيل. اتصلّح في `app/main.py`، والاختبار
`test_the_access_log_record_carries_the_request_id` بيتأكد إن الكود
المكسور بيفشل.

### 2.4 صفحة 404 للعميل

أي لينك ميت أو صنف اتشال كان بيرجّع `application/json` للعميل. دلوقتي
بياخد صفحة HTML، والـAPI والملفات الثابتة فضلت JSON.

### 2.5 `alembic.ini` كان بيقفل اللوجات

`alembic upgrade head` بيقرأ `alembic.ini` وبيستدعي
`logging.config.fileConfig`، ودي بتعطّل **كل** الـloggers الموجودة
(`disable_existing_loggers=True`). النتيجة إن أول migration في نفس
العملية بتمسح كل لوجات `app.*` بصمت. ده كان بيخلي اختبار
"مفيش أسرار في اللوج" يعدّي وهو مبيفحصش حاجة.

اتصلّح بـ`_reenable_app_loggers()` في `app/logging_config.py`. وكمان
أضفت regression testين: واحد بيأكد إن لوجاتنا بترجع، وواحد بيأكد إننا
بنمسّش loggers تانية.

---

## 3. إصلاحات وترتيبات

- **Alpine.js 3.17.4** بقى محلي في `/static/js/` بدل CDN.
- **Service worker** بيتserve من root range (`/sw.js`) — كان بيتserve من
  `/static/js/` والـscope كان `/static/js/` فمبشتغلش على صفحات HTML.
- **`/ready`** جنب `/health`، والـcompose والـrender بيستخدموا `/ready`.
- **الكوكيز**: `COOKIE_SAMESITE` قابل للضبط، و`COOKIE_SECURE=true` في
  الإنتاج.
- **أرقام التليفون** بتتخزّن موحّدة `+20XXXXXXXXX` وبتتتحقق في الـschema.
- **تعليقو-historical migrations + `alembic check` نضافف في الاختبارات**،
  فالانحراف في المستقبل هيمسك في أول commit.

---

## 4. فجوات معروفة (متعمداً ما اتعملتش)

دي مسجّلة صريحة في `SECURITY.md` كمان:

1. **`mask_phone` مكتوبة بس مش مستخدمة في أي مكان.** الأرقام كاملة في
   اللوحة وفي تصدير CSV. تغيير ده بيغيّر شغل التاجر اليومي، فمحتاج قرار
   منك مش افتراض مني.
2. **تصدير CSV بيكتب أرقام كاملة** — أسهل ملف يتسرّب. التغيير سطر واحد
   في `app/routers/admin.py:926`.
3. **مفيش rate limiting على تصدير العملاء.**
4. **مفيش audit log لعمليات الأدمن** (مين غيّر إيه).
5. **`tools/test_db_url.py` بيطبع الـURL بكلمة السر.** مستخدم دلوقتي
   بس، وعايزه يتعدّل يحدّد الـمتغير من غير ما يطبع.

مفيش حد في كلهم ده مكشوف **من غير تسجيل دخول** — مصدر الأرقام غير
المحمي هو فورم الطلب العام، والعميل بيكتب رقمه هو.

---

## 5. إزاي تتحقق

```powershell
# 1. تجهيز داتابيز الاختبار (مرة واحدة)
python -m tools.ensure_test_db

# 2. تشغيل الاختبارات
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/restaurant_store_test"
.venv\Scripts\python.exe -m pytest -q

# 3. فحوصات إضافية
.venv\Scripts\python.exe -m alembic check
python -m tools.migration_conflicts
python -m tools.text_hygiene --strict
```

الـconftest **بيرفض** يشتغل لو اسم الداتابيز مش فيه `_test`، وبيطبع
كلمة السر مشفّرة في رسالة الرفض.

---

## 6. الخطوة الجاية

Phase 0 خلص. مفيش أي شغل V2 اتعمل — لا cache، لا services، لا
K8s، لا events. لو عايز تبدأ Phase 1، قولي الأول إيه أولويتك.
