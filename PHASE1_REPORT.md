# تقرير Phase 1 — أساس الفروع وقنوات الطلب

> التاريخ: 2026-09-27
> الفرع: `v2-development` (لم يتم عمل commit)
> الحالة: **330 اختبار نجح كلها**، سلسلة الـmigrations (17 تهجير) بتتبني
> صح من الأول لآخر، ومفيش انحراف بين الموديلات والداتابيز.

Phase 1 كان هدفه يجهّز الـbackend والداتابيز والدومين موديل للشغل
متعدد الفروع، من غير ما يدخل في أي ميزة من Phase 2. الشغل كله على
`restaurant_store_test` — **قاعدة بيانات التطوير ما اتلمستش** (لسه
مختومة بـ`b5c8d3e4f5a7`، 19 جدول، من غير جدول `branches`).

---

## 1. النتيجة

| البند | قبل | بعد |
| --- | --- | --- |
| جدول الفروع | مفيش | `branches` + فرع افتراضي واحد |
| `orders.branch_id` | مفيش | NOT NULL + FK `RESTRICT` |
| قناة الطلب | مفيش | `system` / `whatsapp` + check constraint |
| ساعات الاستقبال | مفيش | globals على مستوى المطعم، بشيء يراعي الراحة اللي بتعدّي نص الليل |
| واجهة اختيار الفرع | مفيش | `/api/branches` + قائمة في صفحة الدفع |
| إدارة الفروع | مفيش | `/admin/branches` (إضافة/تعديل/تعطيل/افتراضي) |
| اختبارات | 269 | 330 |
| انحراف موديل/داتابيز | ولا واحد | ولا واحد |

توزيع الاختبارات:

| الملف | العدد | بيغطي |
| --- | --- | --- |
| `tests/test_phone_validation.py` | 87 | التطبيع والتحقق والقناع (من Phase 0) |
| `tests/test_branches.py` | **60** | الفروع، القناة، ساعات الاستقبال، لوحة التحكم |
| `tests/test_hardening.py` | 57 | fail-fast، headers، لوج، request id (من Phase 0) |
| `tests/test_login_guard.py` | 40 | throttle + route الأدمن (من Phase 0) |
| `tests/test_pages_smoke.py` | 26 | رندر الصفحات + 404 + أصول ثابتة (من Phase 0) |
| `tests/test_service_worker.py` | 22 | الـPWA (من Phase 0) |
| `tests/test_order_creation.py` | 18 | مسار الطلب + التسعير (من Phase 0) |
| `tests/test_migrations.py` | 18 | السلسلة + تطابق الموديل (+1 لتهجير Phase 1) |
| `tests/test_text_hygiene.py` | 2 | محارف من لغات غلط في النصوص (من Phase 0) |

---

## 2. قرار المعمارية: طلب واحد، قناتين

القرار الأهم في Phase 1: **`system` و`whatsapp` مش نظامين منفصلين.**
الاتنين بيخزنوا في نفس جدول `orders` وبيمشوا في نفس مسار التسعير
والتحقق بالظبط. الفرق الوحيد هو **التحويل بعد الحفظ**:

```text
POST /orders
  ├─ تحقق: ساعات الاستقبال → الفرع → المنتجات → التسعير
  ├─ commit:  الطلب بيتحفظ في PostgreSQL      ← مصدر الحقيقة
  └─ بعد الـcommit بس:
       channel=whatsapp → ترجع whatsapp_url (العميل يتحوّل)
       channel=system   → ترجع بدون رابط، والعميل يشوف /order/success
```

ليه الترتيب ده مهم: واتساب قناة **تواصل** مش مصدر حقيقة. لو اعتمدنا
عليه كمن مصدر، ضاع الطلب خلاص لو المحادثة اتقفلت أو العميل ما ردّش.

### channel كـ`String` + check constraint

مش `enum` حقيقي، عشان ملف الـmigration يفضل مقروء ومفهوم. القيد
مكتوب في الموديل كمان:

```python
CheckConstraint("channel IN ('system', 'whatsapp')", name="ck_orders_channel")
```

`alembic check` بيقول `No new upgrade operations detected`، يعني الموديل
والداتابيز متطابقين فعلاً.

`order.whatsapp_message` بيتخزن في الحالتين زي ما كان في V1، عشان يفضل
عندنا لقطة نصية من الرسالة اللي اتبعتت.

---

## 3. الفروع

### 3.1 الجدول

```text
id, name, address, phone, opening_hours,
is_active, is_default, created_at, updated_at
```

- **`opening_hours` نص حر، للعرض بس.** ساعات الاستقبال الحقيقية
  (`ordering_open_time` / `ordering_close_time`) على مستوى المطعم، حسب
  الموافقة. ما عملناش ساعات استقبال لكل فرع عشان ما نكررش سلوك واحد
  مرتين.
- **`is_default` زيادة عن الحد الأدنى المطلوب**، لكنه لازم: العميل اللي
  مش بيبعت فرع (يعني عملاء V1 القدامى) محتاج يرجع لفرع ما. وفهرس جزئي
  `ix_branches_single_default` بيمنع الاتنين مع بعض على مستوى الـdatabase،
  مش في الكود بس.

### 3.2 القاعدة الذهبية: مفيش طلب من غير فرع

العمود `orders.branch_id` **NOT NULL** في الداتابيز. في الـAPI
`branch_id` اختياري عشان ما نكسرش عملاء V1، بس السيرفر بيرجعله الفرع
الافتراضي قبل الحفظ. النتيجة: **مفيش أي طريق يخلّي طلب يتحفظ من غير
فرع** — لا من الـAPI ولا من SQL مباشر.

الفرع المجهول أو **المعطّل** مرفوض بـ400، حتى لو حد بعت الـid يدوي.
عميل شاف فرع مقفول في اختياره؟ مش المفروض يقدر يطلب منه.

`ON DELETE RESTRICT`: ممنوع نمسح فرع لسه فيه طلبات. تاريخ الطلبات
ما يضيعش — لو محتاجين نوقف فرع، بنعطّله.

### 3.3 الـAPI

| المسار | الوصف |
| --- | --- |
| `GET /api/branches` | الفروع المفعّلة بس، الافتراضي أول واحد |
| `GET /api/ordering-status` | مفتوح/مقفول + الساعات + هل الراحة بتعدّي نص الليل |
| `GET /admin/branches` | إدارة الفروع |
| `POST /admin/branches` | إضافة أو تعديل (حسب `id`) |
| `POST /admin/branches/{id}/default` | نقل الفلاج الافتراضي |

الـ`BranchOut` ما بيعرضش `is_active` ولا `is_default` للعميل — مفيش
سبب يعرض حالة داخلية في واجهة عامة.

الفرع الافتراضي مش ممكن يتعطّل أو يبقى هو الافتراضي وهو معطّل، عشان
مش يتقفل استقبال الطلبات على عملاء V1 من غير ما حد ياخد قرار واعي.

---

## 4. ساعات استقبال الطلبات

```text
ordering_open_time  Time nullable
ordering_close_time Time nullable
```

على مستوى المطعم كله. القواعد في `app/utils.py`:

| الحالة | السلوك |
| --- | --- |
| وقت واحد أو الاتنين فاضيين | مفتوح 24 ساعة (سلوك V1) |
| `open == close` | مفتوح 24 ساعة — مش "مقفول دايمًا" |
| `open < close` | نافذة يومية عادية (10:00 → 22:00) |
| `open > close` | **راحة بتعدّي نص الليل** (18:00 → 02:00) |

الراحة اللي بتعدّي نص الليل كانت الحالة اللي اتغطّت بـ13 حالة اختبار،
منهم `01:30` مفتوح و`02:00` مقفول و`12:00` مقفول.

الساعات دي **بتتحقق قبل أي شغل** في مسار الطلب: لو مقفول، 403 برسالة
عربية و**مفيش صف طلب بيتكتب**. العميل لسه يقدر يتصفح الموقع كله.

حماية المدخلات: `parse_ordering_time()` بترفض `25:99` و`10:00:30`
و`abc` بـ400 بدل استثناء غامض جوه SQLAlchemy، و`/admin/settings`
بيرفض نص نافذة واحدة من غير التانية (عشان ما يحصلش طلب مفتوح أو مقفول
على نصه).

`working_hours` في الإعدادات لسه **نص للعرض بس** زي ما كان — تغيير سلوكه
كان هيدخل في Phase 2.

### timezone

`is_within_ordering_hours()` بيقارن بـUTC دلوقتي. المطعم مصري، والوقت
الصح هو التوقيت المحلي. ده فخ معروف ومتوقع — حاطينه في "المتبقي" في
القسم 7 بدل ما نعمل timedelta hack دلوقتي ونبني عليه.

---

## 5. قائمة التحقق الأصلية

| المطلوب | الحالة | ملاحظة |
| --- | --- | --- |
| نموذج Branch بحد أدنى | ✅ | + `is_default` بحكم الداتابيز |
| كل طلب جديد لازم يكون ليه فرع | ✅ | NOT NULL + رجوع للافتراضي |
| `system` بيتسجل ويبان في لوحة التحكم | ✅ | `/admin/branches` + عمود في الطلبات |
| `whatsapp` بيشتغل زي الأول | ✅ | نفس المسار + `whatsapp_url` |
| طلب واحد مش نظامين | ✅ | نفس الجدول ونفس التسعير |
| شجرة فروع | ⛔ | طلب مستوي واحد على حده، مش شجرة |
| ساعات استقبال من غير تكرار | ✅ | globals على مستوى المطعم |
| قائمة اختيار الفرع في الـUI | ✅ | `checkout_form.html` |
| شجرة الفروع | ⛔ | مستوي |
| نظام workflow كامل | ⛔ | سيبنا `status` string زي ما هو |
| مسارات الأوامر اليدوية | ⛔ | سطح الأدمن موجود |
| التتبع/التقييم | ⛔ | Phase 2 |
| مناطق توصيل | ⛔ | ما ضفناش حاجة جديدة، الجداول الموجودة زي ما هي |
| ولاء/سمعة | ⛔ | Phase 2 |
| ثيم الأدمن | ⛔ | Phase 2 |
| مخزون | ⛔ | Phase 2 |

---

## 6. التحقق

```text
330 passed                                    (pytest، ترتيب عشوائي)
chain round-trip OK                           (downgrade → base → upgrade → head)
No new upgrade operations detected.           (alembic check)
no conflicts found in the chain               (tools.migration_conflicts)
النصوص نضيفة                                  (tools.text_hygiene --strict)
```

### سلامة البيانات عند الترحيل

اتعمل سكربت فحص مخصوص على داتابيز الاختبار: نزّلنا على الـrevision اللي
قبل Phase 1، زرعنا 5 طلبات V1 قديمة (من غير `branch_id` و من غير
`channel`)، وقارنّينا قبل وبعد:

- الـ5 طلبات **باقية زي ما هي** — مفيش صف اتغير أو اتمسح.
- اتعمل **فرع افتراضي واحد** بالاسم `الفرع الرئيسي`.
- `orders with null branch_id` = **0**.
- كل الطلبات القديمة بقت `channel='whatsapp'` (صح تاريخيًا، لأن V1 كله
  كان واتساب).
- الـdowngrade شال الجدول والأعمدة من غير ما يمسّ الطلبات.
- تشغيل الـupgrade تاني بعد الوصول للـhead = no-op.

### قاعدة التطوير

قراية بس، بعد كل الشغل:

```text
database        : restaurant_store
alembic_version : b5c8d3e4f5a7
table count     : 19
has branches?   : False
```

يعني مفيش أي تغيير على بيانات التطوير. الـmigration اتكتب واتأكد في
الاختبارات، بس **ما اتطبّقش على قاعدة التطوير** — دي قرار منفصل.

---

## 7. المتبقي لـPhase 2+

- **timezone لساعات الاستقبال** — لازم نقرّر: إعدادات timezone صريحة،
  ولا نخزّن `opened_at`/`closed_at` كـ`TIMESTAMPTZ` بتوقيت المطعم؟
  دلوقتي UTC والمطعم مصري، فده نقطة صغيرة تحت السطح لازم تتحل في Phase 2.
- **`working_hours`** لسه نص للعرض، مربوطش بالساعات الحقيقية.
- **الأسعار لسه global.** Phase 1 مقدّم فني لأسعار مختلفة لكل فرع،
  بس السعر حسب الفرع مقدمة Phase 2.
- **`is_default` بيتحدد من الأدمن بس.** مفيش auto-pick ذكي.
- **المناطق/العملاء/التقييمات** زي ما كانت.
- **`mask_phone()` لسه مش مستخدم** — الأدمن وتصدير CSV بيظهروا الرقم
  كامل. فجوة Phase 0 لسه مفتوحة.

## 8. ملفات Phase 1

| الملف | الدور |
| --- | --- |
| `app/models.py` | `Branch`، `branch_id`، `channel`، ساعات الاستقبال، الـcheck |
| `app/utils.py` | `is_within_ordering_hours`، `ordering_hours_message`، `parse_ordering_time` |
| `app/schemas.py` | `OrderChannel`، `BranchOut`، `OrderingStatusOut`، حقول الطلب |
| `app/routers/public.py` | `resolve_branch`، `/api/branches`، `/api/ordering-status`، `/order/success` |
| `app/routers/admin.py` | إدارة الفروع + ساعات الاستقبال + eager load للفرع |
| `alembic/versions/c3d4e5f6a7b8_phase1_branches.py` | الجدول، الـbackfill، القيود |
| `app/templates/admin/branches.html` | إدارة الفروع |
| `app/templates/public/order_success.html` | تأكيد طلب `system` |
| `app/templates/public/components/checkout_form.html` | اختيار الفرع والقناة |
| `app/static/js/cart.js` | إرسال `branch_id` + `channel` |
| `tests/test_branches.py` | 60 اختبار |
