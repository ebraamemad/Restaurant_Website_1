# Restaurant Ordering System

نظام طلب إلكتروني لمطعم مبني بـ FastAPI. يعرض قائمة المنتجات مع الإضافات
(Addons)، ويسمح للزبون بتكوين سلة وإرسال الطلب عبر واتساب، مع لوحة إدارة
 كاملة لإدارة الأقسام والمنتجات والإضافات والطلبات والإعدادات.

هذا المشروع كان في الأصل نظامًا لمحل فاكهة (Fruit Store) ثم تم تحويله
تدريجيًا إلى نظام مطعم. راجع `PROJECT_ARCHITECTURE.md` لتفاصيل البنية.

## المتطلبات

- Python 3.13 (انظر `.python-version`)
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

ملف `.env` يحتوي على الأسرار المحلية ولا يُرفع إلى Git أبدًا. على الخادم
(Render أو VPS) اضبط نفس القيم كمتغيرات بيئة حقيقية —
`DATABASE_URL` من البيئة يتجاوز دائمًا ما في `.env`.

## لوحة الإدارة

```text
http://127.0.0.1:8000/admin/login
```

بيانات الدخول الافتراضية تأتي من `ADMIN_USERNAME` و`ADMIN_PASSWORD` في `.env`.

## واجهة الزبون

```text
http://127.0.0.1:8000/
```

## تهجير قاعدة البيانات (Migrations)

```bash
alembic upgrade head      # تطبيق كل التهجير
alembic revision --autogenerate -m "description"
```

> تاريخ التهجير أعيد تأسيسه عند `852de6ed0909` ثم أُضيفت
> `c7d9f2a41b36` لمواءمة نظام المطعم. أي قاعدة بيانات قديمة مختومة
> بـ revisions المحذوفة (مثل `0003_quantity_numeric`) تحتاج إعادة تهيئة.
