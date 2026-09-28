# Restaurant Ordering System

نظام طلب إلكتروني لمطعم مبني بـ FastAPI. يعرض قائمة المنتجات مع الإضافات
(Addons)، ويسمح للزبون بتكوين سلة وإرسال الطلب عبر واتساب، مع لوحة إدارة
 كاملة لإدارة الأقسام والمنتجات والإضافات والطلبات والإعدادات.

هذا المشروع كان في الأصل نظامًا لمحل فاكهة (Fruit Store) ثم تم تحويله
تدريجيًا إلى نظام مطعم. راجع `PROJECT_ARCHITECTURE.md` لتفاصيل البنية.

## المتطلبات

- Python 3.13.5 (انظر `.python-version`)
- PostgreSQL 14+ (طُوّر على PostgreSQL 17)

## الإعداد

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
python -m app.seed_admin
uvicorn app.main:app --reload
```

## التشغيل باستخدام Docker

```bash
docker compose up -d --build
docker compose exec web python -m app.seed_admin
```

يفتح الموقع على `http://127.0.0.1:8000`. قبل التشغيل، انسخ `.env.example` إلى `.env` وغيّر الأسرار وقيم PostgreSQL. يتم تشغيل migrations تلقائياً عند بدء حاوية التطبيق، وتُحفظ الصور في volume باسم `restaurant_uploads`.

في بيئة VPS، استخدم `COOKIE_SECURE=true` بعد تفعيل HTTPS عبر reverse proxy، وشغّل `docker compose up -d --build`. يتم فتح المنفذ الداخلي 8000 ويمكن ربطه بـNginx أو Caddy.

لإيقاف الحاويات:

```bash
docker compose down
```

ملف `.env` يحتوي على الأسرار المحلية ولا يُرفع إلى Git أبدًا. على الخادم
(Render أو VPS) اضبط نفس القيم كمتغيرات بيئة حقيقية —
`DATABASE_URL` من البيئة يتجاوز دائمًا ما في `.env`.

## لوحة الإدارة

```text
http://127.0.0.1:8000/admin/login
```

بيانات الدخول الافتراضية تأتي من `ADMIN_USERNAME` و`ADMIN_PASSWORD` في `.env`.

**قبل أي نشر حقيقي** راجع `SECURITY.md` — التطبيق بيرفض يشتغل في وضع
الإنتاج لو `APP_SECRET_KEY` أو `ADMIN_PASSWORD` قصروا أو لو
`COOKIE_SECURE=false`.

## واجهة الزبون

```text
http://127.0.0.1:8000/
```

## الفروع وقنوات الطلب (Phase 1)

كل طلب لازم يكون مربوط بفرع. إدارة الفروع من `/admin/branches`، وساعات
استقبال الطلبات من `/admin/settings`.

- `GET /api/branches` — الفروع المفعّلة بس (الافتراضي بيجي الأول).
- `GET /api/ordering-status` — استقبال الطلبات مفتوح ولا مقفول دلوقتي.
- `POST /orders` بقبول `branch_id` و`channel`:
  - `branch_id` اختياري في الطلب — لو مبعوتش، السيرفر بيرجعله الفرع
    الافتراضي. العمود `orders.branch_id` NOT NULL، فمفيش طلب بيتحفظ
    من غير فرع. الفرع المجهول أو المعطّل مرفوض بـ400.
  - `channel` قيمته `system` أو `whatsapp`. الاتنين بيخزنوا **نفس**
    صف الـ`Order`؛ الفرق إن `whatsapp` بيرجّع رابط تحويل بعد ما
    الطلب يبقى متحفظ فعلاً في الداتابيز، و`system` بيفتح صفحة تأكيد
    `/order/success`. السعر والتحقق من المنتجات بيبقى واحد في الاتنين.

ساعات استقبال الطلبات على مستوى المطعم كله (`ordering_open_time` /
`ordering_close_time`)، مش على مستوى الفرع. سيبها فاضية = مفتوح 24
ساعة. لو وقت الإغلاق أبكر من وقت الفتح، النظام بيفهم إن الراحة بتعدّي
نص الليل (18:00 → 02:00). ملاحظة: `working_hours` لسه نص للعرض بس،
مش بيتحقّق منه.

## تهجير قاعدة البيانات (Migrations)

```bash
.venv\Scripts\python.exe -m alembic upgrade head      # تطبيق كل التهجير
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "description"
.venv\Scripts\python.exe -m alembic check              # هل الموديل بياخالف الداتابيز؟
```

الـhead الحالي هو `c3d4e5f6a7b8` و السلسلة 17 تهجير.

سلسلة التهجير أعيد تأسيسها عند `852de6ed0909` (البداية من الأول لآخر).
قاعدة بيانات موجودة بـrevision قديم محذوف (مثل `0003_quantity_numeric`)
محتاجة إعادة تهيئة، لأن السلسلة الجديدة مش متسلسلة معاها.

أدوات تشخيص للتهجير:

```bash
python -m tools.migration_report        # يطبع السلسلة
python -m tools.migration_conflicts     # تدوّر على تعارضات تكسر بناء جديد
python -m tools.schema_diff             # يقارن الموديل بالداتابيز
```

## الاختبارات

الاختبارات محتاجة **داتابيز اختبار منفصلة**، وبترفض تشتغل على أي
داتابيز اسمها مش فيه `_test` — عشان ما يمسحش بيانات حقيقية بالغلط.

```bash
copy .env.example .env
python -m tools.ensure_test_db          # ينشئ restaurant_store_test لو مش موجود
```

بعدين شغّل:

```bash
# PowerShell
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/restaurant_store_test"
.venv\Scripts\python.exe -m pytest -q
```

كل جلسة اختبار بتعمل ترحيل الداتابيز من الصفر (`alembic upgrade head`)
وبتعمل rollback بعد كل test. يعني السرعة بطيئة شوية (بسبب bcrypt)،
بس دي أول مرة بنقدر نتأكد إن السلسلة بتتبني صح من الأول لآخر.

الملف `pytest.ini` بيخلي الـcollection من مجلد `tests/` بس — فيه
سكربتات قديمة في root بتستخدم داتابيز التطوير.

فحوصات النصوص:

```bash
python -m tools.text_hygiene --strict
```

