# تقرير Reverse Engineering — مشروع تحويل Fruit Store إلى نظام مطعم

> هذا التقرير نتيجة قراءة وتحليل فقط. **لم يتم أي تعديل** على أي ملف من ملفات المشروع.
> sources: الكود الحالي + تاريخ git كاملاً (7 commits) بما فيه الملفات المحذوفة.

---

## 1. ملخص المشروع

- **التقنيات:** FastAPI 0.116.1 + SQLAlchemy 2.0 + Pydantic v2 + PostgreSQL (psycopg3) + Alembic + Jinja2 + Tailwind CDN + Alpine.js + HTMX (محمّلة لكن غير مستخدمة) + PWA (manifest + service worker). الواجهة عربية RTL.
- **الأصل:** نظام "Fresh Store" — موقع طلبات لمحل فاكهة/خضروات يبيع بالوزن (كيلو، بزيادات 0.25 كجم)، السلة في المتصفح، والطلب يُرسل عبر **واتساب** مع حفظه في قاعدة البيانات.
- **الوضع الحالي:** المشروع في **منتصف عملية تحويل** إلى نظام مطعم:
  - قاعدة البيانات تم تحويلها بالكامل تقريبًا (جداول جديدة: `addons`, `order_item_addons`, `product_options`, `coupons`, `delivery_options`, `restaurant_settings` + أعمدة جديدة في `orders` وتغيير `price` → `base_price`).
  - لكن **طبقة التطبيق (Schemas / Routers / Templates / JS) لم تكتمل** بعد هذا التغيير → المشروع حاليًا في حالة **مكسورة وظيفيًا** (تفاصيل قسم 14).
- **تاريخ git (7 commits):**
  1. `c8a083e` (08-18) Initial commit — مشروع Fresh Store كاملًا.
  2. `a0da4fe` (08-20) "تنظيم المشروع وتهيئته لكي يكون منيو لمطعم" — إلغاء `create_all()` والاعتماد على Alembic.
  3. `444b3f1` (08-20) "تغيير البيئة الي بيئة مناسبة لبايدانتك" — تعديل requirements.txt.
  4. `38d07a0` (08-21) "بدا التغيير" — التحويل الفعلي: نظام الإضافات (Addons) + صفحة تفاصيل المنتج + تعديل السلة + migration `0002_addons`.
  5. `6e12c75` (08-21) "تغيير glm" — تثبيت الكميات الصحيحة (int بدل Decimal) + migration `0003` لتحويل `quantity` إلى Numeric + إصلاحات.
  6. `ea49de7` (08-26) "تغيير ai agent" — تغيير models.py فقط: `price` → `base_price`، `unit` default "piece"، وإضافة models جديدة (ProductOption, Coupon, DeliveryOption, RestaurantSettings) وأعمدة في Order/OrderItemAddon **بدون أي تحديث لباقي الطبقات**.
  7. `4892030` (08-26) "تغغييرات بحذر" — حذف migrations القديمة الثلاثة وإنشاء migration موحّد `852de6ed0909_initial_restaurant_database` (autogenerate من الـmodels) + `test_db.py` + ربط قاعدة محلية اسمها `restaurant_store` في alembic.ini.
- الفرع المحلي متقدم بـ commit غير مدفوعَيْن عن `origin/main` (`ea49de7`, `4892030`).
- يوجد ملف غير متتبع `PROJECT_ARCHITECTURE.md` (هيكل فارغ أنشأه agent سابق).

---

## 2. المشروع الأصلي — Fruit Store (من commit `c8a083e`)

- **الهدف:** MVP لمحل يبيع فاكهة/خضروات/دواجن/بقالة (نص README الأصلي: "small fruit, vegetable, poultry, or grocery shop").
- **الجداول الأصلية (6):** `admins`, `categories`, `products`, `orders`, `order_items`, `store_settings`.
- **المنتجات الأصلية:** حقل `price` (سعر واحد) + `unit` افتراضي **"kg"** → البيع بالوزن.
- **الكميات:** عشرية بزيادات ¼ كجم — مثبتة من `cart.js` الأصلي (`increaseProductQty` يضيف 0.25) ومن `product_card.html` الأصلي (قائمة اختيار ¼، ½، ¾، 1، 1½، 2).
- **إنشاء الطلب الأصلي:** `POST /orders` يستقبل الاسم/الهاتف/العنوان/ملاحظات + items (product_id, quantity) → التحقق من توفر المنتجات → حساب الإجمالي → حفظ Order + OrderItems → بناء رسالة واتساب → إرجاع `whatsapp_url` → المتصفح يفتح wa.me. لا يوجد addons ولا خيارات.
- **السلة الأصلية:** localStorage بمفتاح "cart" + Alpine فقط (`storeCart()`). لا توجد صفحة تفاصيل منتج؛ الإضافة مباشرة من كارت المنتج.
- **لوحة الإدارة الأصلية:** login (JWT في cookie + bcrypt)، dashboard (إحصائيات يومية/أسبوعية/شهرية + أكثر المنتجات + أكثر العملاء)، CRUD للأقسام والمنتجات مع رفع صور، عرض الطلبات وتغيير حالتها، إعدادات المحل (اسم، واتساب، لوجو، بنر...).
- **البحث:** endpoint `GET /search` موجود بمنطق تطبيع عربي (ة↔ه، ي↔ى)، لكن واجهة البحث في `search_bar.html` كانت معطلة منذ البداية بنص "البحث غير متاح暂ًّا" (نص فيه حرف صيني — كان موجودًا في أول commit).
- **PWA:** manifest باسم "Fresh Store" + service worker يكاش الأصول الثابتة + شاشة "لا يوجد اتصال".
- **خلل أصلي معروف:** migration `0001_initial` أنشأ `order_items.quantity` من نوع INTEGER بينما الـModel كان `Numeric(10,3)` → الكميات العشرية كانت تُقرَّب صامتًا في PostgreSQL (موثق في نص migration `0003` المحذوف).

---

## 3. المشروع الحالي — Restaurant

ما تم فعليًا على مستوى الكود (وليس فقط قاعدة البيانات):

| الجزء | الوضع الحالي |
|---|---|
| الإضافات (Addons) | **مكتمل دورة كاملة:** admin يدير إضافات لكل منتج (`/admin/products/{id}/addons`)، صفحة تفاصيل المنتج تعرضها كاختيارات، السلة تحفظها، `POST /orders` يحسبها ويخزنها في `order_item_addons` |
| صفحة تفاصيل المنتج | **موجودة** (`GET /products/{slug}`) لكنها **مكسورة حاليًا** (انظر قسم 14) |
| الكميات | أصبحت **أعدادًا صحيحة فقط** (cart.js يجبر `Math.floor`، و`CartItemIn.quantity: int`) |
| قاعدة البيانات | محوّلة: `base_price` بدل `price`، جداول `product_options`, `coupons`, `delivery_options`, `restaurant_settings`، وأعمدة `delivery_option_id`, `coupon_code`, `pickup_time` في `orders` |
| Product Options / Coupons / Delivery / Pickup / RestaurantSettings | **موجودة في الـModels والـMigration فقط** — لا Schemas ولا Routes ولا Templates ولا JS تستخدمها |
| واتساب | ما زال هو آلية إتمام الطلب، وباني الرسالة ما زال يستخدم `StoreSettings.store_name` |

**خلاصة:** قاعدة البيانات سبقت الكود. آخر commit غيّر الـModels فقط، فصار الـModel في وادٍ وباقي التطبيق في وادٍ آخر.

---

## 4. هيكل المشروع

```text
F:\المشروع
├── alembic/
│   ├── env.py                      # يقرأ DATABASE_URL من settings (يتجاوز alembic.ini)
│   └── versions/
│       └── 852de6ed0909_initial_restaurant_database.py   # الـmigration الوحيد حاليًا
├── alembic.ini                     # فيه connection string محلية بالباسورد (committed!)
├── app/
│   ├── main.py                     # FastAPI app + mount static/uploads + تضمين 3 routers
│   ├── config.py                   # Settings من .env (app_name افتراضيًا "Fresh Store")
│   ├── database.py                 # engine + SessionLocal + get_db (create_all اتشال)
│   ├── models.py                   # 12 Model (محل التحويل)
│   ├── schemas.py                  # Pydantic (لم يحدَّث بعد تغيير base_price)
│   ├── deps.py                     # get_current_admin (JWT cookie)
│   ├── security.py                 # bcrypt + JWT (jose)
│   ├── utils.py                    # slugify, save_image, بناء رسالة/رابط واتساب
│   ├── seed_admin.py               # إنشاء Admin + StoreSettings
│   ├── routers/
│   │   ├── public.py               # /, /search, /products/{slug}, POST /orders
│   │   ├── admin_auth.py           # /admin/login, /admin/logout
│   │   └── admin.py                # /admin + الفئات/المنتجات/الإضافات/الطلبات/الإعدادات
│   ├── templates/
│   │   ├── public/                 # base, home, product_detail + components + partials
│   │   └── admin/                  # base, login, dashboard, categories, products, addons, orders, settings
│   ├── static/                     # css/app.css, js/cart.js, js/sw.js, manifest.json, icons
│   └── uploads/                    # صور مرفوعة (products/categories/logos/banners) — دليل استخدام الأدمن
├── .env                            # محلي (gitignored) — DB اسمها restaurant_store
├── .env.example
├── render.yaml                     # deploy على Render باسم fresh-store
├── requirements.txt
├── test_db.py                      # سكريبت اتصال بسيط (أُضيف في آخر commit)
├── README.md                       # ما زال يصف Fresh Store
└── PROJECT_ARCHITECTURE.md         # هيكل فارغ غير متتبع (untracked)
```

> **ملاحظة:** التبويب المفتوح `app/routers/checkout.py` في المحرر **لا يوجد له ملف على القرص** — لا في working tree ولا في git. غالبًا buffer غير محفوظ أو ملف حُذف.

---

## 5. تحليل قاعدة البيانات (الحالية — migration `852de6ed0909`)

12 جدولًا:

| الجدول | الوظيفة | أهم الأعمدة | PK | FK / قيود |
|---|---|---|---|---|
| `admins` | مستخدمو الإدارة | username, password_hash, full_name, is_active | id | username unique |
| `categories` | الأقسام | name, slug, image_url, sort_order, is_active, is_featured | id | slug unique |
| `products` | المنتجات | category_id, name, slug, **base_price**, unit (default بالـModel "piece"), is_available, is_active | id | FK category_id → categories.id (ondelete RESTRICT)؛ slug unique |
| `addons` | إضافات المنتج | product_id, name, price, is_active, sort_order | id | FK product_id → products.id (CASCADE) |
| `product_options` | خيارات المنتج (أحجام) — **غير مستخدمة بالكود** | product_id, name, price_adjustment, is_available | id | FK product_id → products.id (CASCADE) |
| `orders` | الطلبات | customer_name/phone/address, notes, status, total, whatsapp_message, **delivery_option_id**, **coupon_code**, **pickup_time** | id | FK delivery_option_id → delivery_options.id؛ FK coupon_code → **coupons.code** (وليس id) |
| `order_items` | أسطر الطلب | order_id, product_id, product_name, unit_price, **quantity Numeric(10,3)**, line_total | id | FK order_id → orders.id (CASCADE)؛ FK product_id → products.id (RESTRICT) |
| `order_item_addons` | إضافات سطر الطلب (snapshot: name+price) | order_item_id, **addon_id (NOT NULL)**, name, price | id | FK order_item_id → order_items.id (CASCADE)؛ FK addon_id → addons.id |
| `coupons` | كوبونات — **غير مستخدمة بالكود** | code (unique), discount_type, discount_value, minimum_order, start/end_date, usage_limit, usage_count, is_active | id | code unique |
| `delivery_options` | خيارات التوصيل — **غير مستخدمة بالكود** | name, fee, estimated_time, is_active | id | — |
| `restaurant_settings` | إعدادات المطعم — **غير مستخدمة بالكود** | restaurant_name, whatsapp_number, logo_url, banner_url, currency, facebook_url, tiktok_url, theme_colors, additional_info | id | — |
| `store_settings` | إعدادات المحل القديمة — **المستخدمة فعليًا** | store_name (default "Fresh Store"), whatsapp_number, logo_url, banner_url, currency | id | — |

---

## 6. العلاقات بين الجداول

```text
categories ──1:N──> products ──1:N──> addons ──1:N──> order_item_addons
                       │                                    ▲
                       ├──1:N──> product_options (معزولة)   │ snapshot
                       │                                    │
                       └──1:N──> order_items <──────────────┘
                                    ▲
                                    │ N:1
orders ──1:N──> order_items ────────┘
   │
   ├──N:1──> delivery_options (معزولة)
   └──N:1──> coupons عبر coupons.code (معزولة)

admins (معزول)          store_settings (معزول)          restaurant_settings (معزول)
```

- `Category.products` ↔ `Product.category` (back_populates)
- `Product.order_items` ↔ `OrderItem.product`؛ `Product.options`؛ `Product.addons` (cascade delete-orphan)
- `Addon.order_item_addons` ↔ `OrderItemAddon.addon`
- `Order.items` ↔ `OrderItem.order` (cascade delete-orphan)؛ `Order.delivery_option`؛ `Order.coupon`
- الحذف: حذف قسم ممنوع إن وُجدت منتجات (RESTRICT + تحقق في الكود)، حذف منتج له طلبات → IntegrityError يلتقطها admin.py ويعطّل المنتج بدلاً من حذفه.

---

## 7. الفرق بين النظام القديم والحالي

| الجزء | قبل التحويل | حاليًا | نوع التغيير |
|---|---|---|---|
| جدول `products` | `price`, `unit` default "kg" | `base_price`, `unit` default "piece" (بالـModel) | تم التعديل |
| جدول `order_items.quantity` | INTEGER في migration (Numeric بالـModel) | Numeric(10,3) في الـmigration والـModel | تم التعديل |
| جدول `addons` | غير موجود | موجود ويُستخدم | تمت الإضافة |
| جدول `order_item_addons` | غير موجود | موجود + عمود `addon_id` NOT NULL جديد | تمت الإضافة ثم التعديل |
| جدول `product_options` | غير موجود | موجود **بدون كود يستخدمه** | تمت الإضافة |
| جدول `coupons` | غير موجود | موجود **بدون كود يستخدمه** | تمت الإضافة |
| جدول `delivery_options` | غير موجود | موجود **بدون كود يستخدمه** | تمت الإضافة |
| جدول `restaurant_settings` | غير موجود | موجود **بدون كود يستخدمه** | تمت الإضافة |
| جدول `store_settings` | موجود ومستخدَم | ما زال موجودًا **وما زال هو المستخدَم** | لم يتغير |
| `orders` | بدون توصيل/كوبون | + delivery_option_id, coupon_code, pickup_time (غير مستخدمة بالكود) | تم التعديل |
| صفحة تفاصيل المنتج | غير موجودة | موجودة (مكسورة حاليًا) | تمت الإضافة |
| الكميات في السلة | عشرية (¼ كجم) | صحيحة فقط | تم التعديل |
| إدارية الإضافات | غير موجودة | موجودة وتعمل | تمت الإضافة |
| Search UI | معطل منذ البداية | ما زال معطلًا | لم يتغير |
| HTMX | محمّل وغير مستخدم | محمّل وغير مستخدم | لم يتغير |
| ملفات migration 0001/0002/0003 | موجودة | حُذفت واستُبدلت بـ `852de6ed0909` | تم الحذف + الإضافة |

---

## 8. التعديلات التي تمت على الكود (حسب الـcommits)

1. **`a0da4fe`:** `app/database.py` — حذف `Base.metadata.create_all()` (التحول لإدارة Alembic للمخطط). ملف مؤقت `query` (حُذف لاحقًا).
2. **`444b3f1`:** requirements.txt — تعديل تعليق نسخة بايثون (كان فيه بقايا مخرجات pip).
3. **`38d07a0` "بدا التغيير":**
   - `models.py`: إضافة `Addon` و`OrderItemAddon` والعلاقات.
   - `public.py`: endpoint تفاصيل المنتج + إعادة كتابة `create_order` لحساب الإضافات وتخزينها.
   - `admin.py`: صفحات حفظ/حذف الإضافات.
   - `schemas.py`: `AddonForm`, `AddonOut`, `ProductDetailOut`, `OrderItemAddonOut` + `addon_ids` في `CartItemIn` + حذف validator منع تكرار المنتجات.
   - `cart.js`: إعادة كتابة كاملة (lineId = منتج+إضافات، صور snapshot للإضافات).
   - `product_detail.html` جديد + تعديل `product_card.html` (رابط للتفاصيل، إخفاء قائمة الكسور) + تعديل السلة.
   - migration `0002_addons`.
4. **`6e12c75` "تغيير glm":**
   - `CartItemIn.quantity`: Decimal → int.
   - `cart.js`: الكميات صحيحة فقط (`Math.floor`).
   - migration `0003`: `order_items.quantity` Integer → Numeric(10,3).
   - إصلاح checkbox `is_active` في الإضافات (افتراضي False حتى يمكن التعطيل).
   - `main.py`: إنشاء مجلد uploads قبل الـmount.
   - `product_detail.html` اختُصرت من ~124 إلى 71 سطرًا.
5. **`ea49de7` "تغيير ai agent":** `models.py` فقط — `price`→`base_price`، unit default "piece"، إضافة ProductOption/Coupon/DeliveryOption/RestaurantSettings، توسيع Order وOrderItemAddon (+addon_id NOT NULL). **لم يُحدَّث أي شيء آخر → هنا حدث الكسر.**
6. **`4892030` "تغغييرات بحذر":** دمج تاريخ الـmigrations في ملف واحد autogenerate + `test_db.py` + URL محلية في alembic.ini.

---

## 9. بقايا النظام القديم (Fruit Store)

### مؤكد أنه من النظام القديم
| العنصر | المكان |
|---|---|
| Model + جدول `store_settings` (default "Fresh Store") — **المستخدَم فعليًا في كل الكود** | `app/models.py:199` |
| `get_settings()` و`get_or_create_settings()` يرجعان `StoreSettings` | `public.py:23`, `admin.py:51` |
| `build_whatsapp_message` يستخدم `settings.store_name` | `app/utils.py:54` |
| `seed_admin.py` ينشئ `StoreSettings` و"Store Admin" | `app/seed_admin.py` |
| `ProductForm.unit` و`SettingsForm.store_name` | `app/schemas.py:30,37` |
| default "kg" في `save_product` وفي قالب `admin/products.html` | `admin.py:163`, `products.html:10` |
| `app_name: str = "Fresh Store"` + تعليقات | `app/config.py:59` |
| `APP_NAME="Fresh Store"` | `.env`, `.env.example` |
| manifest.json: "Fresh Store"/"Fresh" | `app/static/manifest.json` |
| service worker cache: `fresh-store-cache-v1` | `app/static/js/sw.js:1` |
| service على Render اسمه `fresh-store` | `render.yaml:3` |
| README بالكامل يصف Fresh Store | `README.md` |
| fallback العنوان `{{ settings.store_name if settings else "Fresh Store" }}` | `public/base.html:22` |
| نص "إعدادات المحل" و"اسم المحل" | `admin/settings.html` |

### محتمل أنه من النظام القديم
- `order_items.quantity` من نوع Numeric(10,3): صُمم لأوزان الكيلو؛ واجهة المطعم الحالية تستخدم أعدادًا صحيحة فقط (بقية محتملة — لا تسبب خطأ).
- حقل `unit` أصلًا للموزونات؛ الـModel غيّر الافتراضي إلى "piece" لكن الطبقات الأخرى ما زالت "kg".

### لا توجد مشكلة فيه
- منطق البحث بالتطبيع العربي (عام).
- آلية واتساب (عامة).
- نظام المصادقة JWT/bcrypt (عام).

---

## 10. تحليل Products

```text
Model (Product: base_price, unit, options, addons)
  ↓  ⚠️ انفصال هنا
Schema (ProductForm/ProductDetailOut/ProductOut: ما زالوا يستخدمون price)
  ↓
Router (admin.save_product يضبط product.price — حقل غير موجود!)
  ↓
Template (product_card / product_detail / admin products تستخدم product.price)
  ↓
JavaScript (cart.js يستخدم product.price)
```

- **الإدارة:** إنشاء/تعديل/حذف منتج + صورة + إدارة إضافات لكل منتج. لا توجد إدارة خيارات (options) ولا كوبونات ولا delivery.
- **العرض العام:** كروت منتجات مجمعة حسب الأقسام + صفحة تفاصيل + بحث (endpoint جاهز وواجهة معطلة).
- حالة المنتج لها مفتاحان: `is_active` (ظاهر) و`is_available` (متوفر).

---

## 11. تحليل Cart

- **مكان الحالة:** localStorage بمفتاح `"cart"` فقط — لا session ولا cookie. كل سطر: `{lineId, id, name, price, quantity, addons[], addonIds[]}`.
- **دور `cart.js`:** وحدة `Cart` (قراءة/كتابة/دمج حسب lineId) + `storeCart()` (سلة الرئيسية: كمية، إضافة، حذف، إجمالي، إرسال الطلب) + `productDetail()` (صفحة التفاصيل: اختيار إضافات، كمية، إضافة للسلة ثم redirect إلى `/`).
- **Alpine.js:** محرك الواجهة بالكامل (x-data/x-model/x-for).
- **HTMX:** محمّل في `base.html` **ولا يوجد أي `hx-` في أي قالب** — غير مستخدم إطلاقًا.
- **نقل البيانات للـBackend:** `fetch("POST /orders")` بجسم JSON (customer + items بـ product_id/quantity/addon_ids) → الرد يحتوي `whatsapp_url` → تفريغ السلة → فتح واتساب.
- **الكمية:** صحيحة فقط، 1–999 (`Math.floor` + حدود). الكمية العشرية أزيلت في `6e12c75`.
- **الإضافات:** تُخزن snapshot (اسم+سعر) للعرض + قائمة IDs للإرسال. lineId يدمج المنتج مع الإضافات المرتبة، فنفس المنتج بإضافات مختلفة = سطور منفصلة.
- **Product Options:** لا وجود لها في السلة إطلاقًا.
- **ثغرة تصميمية معلومة:** الأسعار تُخزن snapshot في localStorage؛ لو تغيّر سعر المنتج تبقى السلة تعرض السعر القديم (الـBackend يعيد الحساب من عنده عند الطلب، فالمال محمي، لكن السعر المعروض للعميل قد يخالف).

---

## 12. تحليل Orders — دورة الطلب

```text
Product (+Addons من صفحة التفاصيل)
  ↓
Cart (localStorage: snapshot أسعار + addonIds)
  ↓
Checkout form (اسم/هاتف/عنوان/ملاحظات)
  ↓
POST /orders → OrderCreate (Pydantic)
  ↓
التحقق: المنتجات active+available / الإضافات active
  ↓
حساب: unit_price + addons × quantity → line_total → total   ⚠️ يستخدم product.price المكسور
  ↓
Order + OrderItems + OrderItemAddons (بدون addon_id! ⚠️)
  ↓
بناء رسالة واتساب + حفظها في orders.whatsapp_message
  ↓
إرجاع whatsapp_url → المتصفح يفتح wa.me
  ↓
Admin: تغيير الحالة pending → preparing → delivered / cancelled
```

- لا يوجد خصم كوبون ولا رسوم توصيل ولا pickup_time في الدورة الفعلية — الأعمدة موجودة فقط.
- حالة الطلب نصية مع enum في `schemas.OrderStatus` وتسميات عربية في `admin.py STATUS_LABELS`.

---

## 13. تحليل Admin — الوظائف الموجودة فعليًا

| الوظيفة | موجودة؟ | ملاحظات |
|---|---|---|
| Authentication | ✅ | JWT في cookie (httponly, samesite=lax) + bcrypt + صفحة login/logout |
| Dashboard | ✅ | عدادات يوم/أسبوع/شهر، مبيعات المُسلَّم، عدد العملاء، أعلى المنتجات/العملاء، آخر الطلبات |
| Categories | ✅ | CRUD + صورة + is_active/is_featured + منع حذف قسم فيه منتجات |
| Products | ✅ | CRUD + صورة + **مكسور حاليًا بسبب price/base_price** |
| Addons | ✅ | CRUD لكل منتج |
| Orders | ✅ | عرض + تصفية بالحالة (query param) + تغيير الحالة |
| Settings | ✅ | اسم/واتساب/هاتف/عملة/عنوان/مواعيد + لوجو/بنر — كلها على `store_settings` |
| Product Options | ❌ | لا UI ولا routes |
| Coupons | ❌ | لا UI ولا routes |
| Delivery | ❌ | لا UI ولا routes |
| Offers | ❌ | غير موجودة إطلاقًا |

---

## 14. المشاكل الحالية

### 🔴 حرجة (تكسر وظائف أساسية)

1. **`product.price` لم تعد موجودة (أصبحت `base_price`)** — `app/models.py:50` مقابل استخدامات في:
   - `app/routers/public.py:168,175,188` → `AttributeError` → **إنشاء أي طلب يفشل 500**.
   - `app/routers/admin.py:179` `product.price = price` → يسند خاصية غير مُعرَّفة فلا تُحفظ في قاعدة البيانات؛ للمنتج الجديد يعني `base_price` فارغ → **خطأ NOT NULL عند الحفظ** وحفظ المنتجات من الأدمن مكسور.
   - `public.py:111` `ProductDetailOut.model_validate(product)` → Schema يطلب `price` → **ValidationError → صفحة تفاصيل المنتج ترجع 500**.
   - `product_card.html:48,77` → السعر يظهر فارغًا وزر "أضف للسلة" يولّد JS غير صالح.
   - `admin/products.html:27` → حقل السعر في نموذج التعديل فارغ.
   - السبب: commit `ea49de7` غيّر الـModel وحده.
2. **`OrderItemAddon` يُنشأ بدون `addon_id`** — `public.py:181` بينما العمود NOT NULL في الـModel والـmigration → أي طلب فيه إضافات يفشل بـ IntegrityError حتى لو أُصلحت المشكلة الأولى. السبب: `addon_id` أُضيف في `ea49de7` بدون تحديث create_order.
3. **تعطّل سلسلة الأسعار كاملة:** العرض (templates) والحساب (public.py) والإدخال (admin) كلها تستخدم الاسم القديم.

### 🟠 مهمة

4. **جدولا إعدادات متوازيان:** `restaurant_settings` (جديد، لا يستخدمه أي كود) و`store_settings` (القديم، يستخدمه كل الكود). الاتجاه غير محسوم.
5. **إعادة كتابة تاريخ Alembic:** حُذفت 0001/0002/0003 وحلّ محلها `852de6ed0909`. أي قاعدة بيانات قديمة عليها `alembic_version = 0003_quantity_numeric` سيفشل معها `alembic upgrade head` (revision غير موجود). يعمل فقط على قاعدة جديدة أو بعد stamp يدوي.
6. **`app/routers/checkout.py` غير موجود** رغم وجود تبويب مفتوح به — قد توجد تغييرات غير محفوظة في المحرر ستضيع.
7. **مسارات نسبية تعتمد على cwd:** `UPLOADS_DIR = Path("app/uploads")` في `admin.py:23` و`Jinja2Templates(directory="app/templates")` — على Render/gunicorn من مجلد مختلف ستتكسر الرفع/القوالب (main.py وحده يستخدم مسارًا مطلقًا).
8. **FK الغريب `orders.coupon_code → coupons.code`:** ربط بمفتاح طبيعي بدل id؛ تعديل/حذف كود الكوبون مستقبلًا مشكلة.
9. **لا حماية CSRF** على فورمات الأدمن (POST forms).
10. **تعارض الكمية:** العمود Numeric(10,3) بينما السلة والـSchema int — مصدر لبس مستقبلي (هل نرجع للوزن أم لا؟).

### 🟡 متوسطة

11. Dashboard يحمّل **كل الطلبات** في الذاكرة لت computing الإحصائيات (`admin.py:62`) — يتدهور مع نمو البيانات.
12. HTMX محمّلة (~ عشرات KB) ولا تُستخدم.
13. لا pagination في الأدمن (منتجات/طلبات).
14. branding قديم في كل مكان: "Fresh Store" في config/manifest/sw/render/README/.env.
15. `unit` مختلف بين الطبقات: Model "piece"، Schema/الفورمات "kg".
16. نص `search_bar.html` فيه حرف صيني "暂" (كان موجودًا منذ أول commit) والبحث معطل رغم جاهزية الـendpoint.
17. `deps.py` يرمي HTTPException 303 بـLocation header — نمط غير معتاد (يعمل لكن يعيد 303 حتى لطلبات API).
18. credentials قاعدة البيانات المحلية مكتوبة في `alembic.ini` (committed في git).

### 🟢 بسيطة

19. `PROJECT_ARCHITECTURE.md` هيكل فارغ غير متتبع.
20. `.gitignore` ما زال يتجاهل ملف `query` المحذوف.
21. الفرع متقدم بـ2 commits عن origin/main لم يُدفعوا.
22. باراميتر `unit_price` في `build_whatsapp_message` غير مستخدم في النص.
23. عداد السلة السفلي يعرض عدد الأسطر لا مجموع الكميات.

---

## 15. مخاطر الـArchitecture (مناطق حساسة عند أي تعديل)

1. **`app/models.py`** — مصدر الحقيقة الوحيد للمخطط؛ أي إعادة تسمية/حذف حقل يجب أن يتزامن مع schemas + routers + templates + JS في نفس التغيير (درس `base_price`).
2. **`app/schemas.py`** — مرتبط بالاتجاهين: استقبال الطلبات (CartItemIn/OrderCreate) وإخراجها (ProductDetailOut). تعديل أي حقل يمس cart.js وpublic.py معًا.
3. **Orders** — `create_order` في `public.py` هي نقطة الحساب الوحيدة للأسعار؛ أي منطق خصم/توصيل جديد يجب أن يمر منها، وأي تغيير على OrderItem/OrderItemAddon يمس عرض `orders_table.html` وإحصائيات dashboard.
4. **Cart (localStorage)** — بنية السطر `{id, price, addonIds...}` مثبتة في cart.js وفي templates معًا؛ تغيير شكل البيانات يتطلب مسح سلة المستخدمين القدامى (تغيير CART_KEY أو ترحيل).
5. **Authentication** — cookie باسم `admin_access_token` وsecret من `.env`؛ تغيير السر أو اسم الـcookie يقطع جلسات الجميع. لا يوجد refresh/logout تلقائي.
6. **Routes** — `public.py` و`admin.py` يستخدمان مسارات نسبية للـtemplates والـuploads؛ أي نقل تشغيلي (working dir) يكسرهما.
7. **Alembic** — التاريخ أُسس من جديد عند `852de6ed0909`. أي تعديل مخطط يجب أن يكون migration جديدًا فوقه، **ولا** إعادة توليد من الصفر مرة أخرى (يضيع تاريخ أي قاعدة قائمة). `alembic/env.py` يتجاهل URL في ini ويأخذه من settings.
8. **علاقات الحذف:** حذف منتج مرتبط بطلبات مرفوض (RESTRICT) والكود يحوّله إلى تعطيل — الحفاظ على هذا السلوك ضروري لسلامة الطلبات التاريخية؛ `order_items` يخزن snapshot الاسم والسعر لهذا الغرض.

---

## 16. الأشياء غير المؤكدة

- **هل قاعدة البيانات المحلية الحالية جديدة؟** لا يمكن التحقق من قيمة `alembic_version` فيها بدون تشغيل استعلام (ممنوع في هذه المرحلة). إن كانت أُنشئت بعد `852de6ed0909` فهي متسقة؛ وإن كانت من قبل فالتاريخ منفصل.
- **محتوى تبويب `app/routers/checkout.py`** — الملف غير موجود على القرص؛ هل كان تجربة حذفها صاحب المشروع أم buffer لم يُحفظ؟ **غير مؤكد.**
- **سبب أسماء commits** ("تغيير glm"/"تغيير ai agent") — يبدو استخدام أدوات ذكاء اصطناعي مختلفة لكل مرحلة، لكن هذا استنتاج من الأسلوب وليس معلومة مؤكدة.
- **هل يوجد deployment فعلي على Render؟** `render.yaml` موجود لكنه ما زال باسم fresh-store؛ **غير مؤكد من الكود الحالي.**
- **الغرض المقصود من `addon_id` NOT NULL** في `order_item_addons` (تحليلات؟) — لم يُستخدم في أي كود؛ **سبب الإضافة غير مؤكد.**
- **هل كان المنتج "unit=piece" مقصودًا لكل المنتجات** أم فقط للمطعم؟ الكميات الصحيحة تدعم فكرة القطع، لكن وجود Numeric(10,3) يعاكسها.

---

## 17. خطة آمنة للتعديلات المستقبلية (بدون تكسير الـArchitecture)

**مبدأ عام:** المشروع بنيته سليمة (Routers → Schemas → Models → DB، وFrontend منفصل عبر templates+cart.js). المشكلة كلها في **عدم اكتمال تغيير واحد** (price→base_price + الإضافات الجديدة). الإصلاح = إكمال هذا التغيير باتساق، وليس إعادة هيكلة.

### المرحلة 0 — حماية (قبل أي تعديل)
1. التأكد من حفظ/إغلاق تبويب `checkout.py` الوهمي، و**backup لقاعدة البيانات المحلية** (`pg_dump`) + التأكد من قيم `alembic_version`.
2. تثبيت "عقد" التغيير: هل السعر الأساسي اسمه `price` أم `base_price`؟ الخياران:
   - **أ (موصى به):** إرجاع `base_price` → `price` في الـModel والـmigration (إعادة توليد migration من جديد لأن القاعدة على الأغلب جديدة) — لأنه يلم ملفًا واحدًا ويعيد توافق كل الطبقات فورًا بدون لمس 6 ملفات.
   - **ب:** إكمال الترحيل إلى `base_price` في كل الطبقات (schemas, public.py, admin.py, القوالب الثلاثة, cart.js) — صحيح معماريًا إذا كان المقصود لاحقًا عرض "السعر حسب الخيار".

### المرحلة 1 — إصلاح 🔴 (بالترتيب)
3. توحيد حقل السعر (حسب الخيار أعلاه) + تعديل `save_product` و`create_order` و`ProductDetailOut` والقوالب وcart.js معًا.
4. حسم `OrderItemAddon.addon_id`: إما ملؤه في `create_order` (الأفضل للتحليلات) أو جعله nullable؛ ثم تعديل migration accordingly.
5. اختبار يدوي شامل: الرئيسية → تفاصيل منتج → إضافة للسلة بإضافات → إرسال طلب → ظهوره في الأدمن → حفظ منتج/قسم/إضافة من الأدمن.

### المرحلة 2 — حسم الازدواج
6. قرار `store_settings` مقابل `restaurant_settings`: إما ترحيل الكود كله إلى RestaurantSettings (بيانات + كود + قوالب + utils + seed) وdrop store_settings، أو العكس (حذف restaurant_settings). لا يُترك الجدولان معًا.
7. قرار unit/الكميات: تثبيت "piece + كميات صحيحة" في كل الطبقات، أو إعادة دعم الوزن — لكن حسم واحد فقط.

### المرحلة 3 — تفعيل أو إزالة الجداول المعزولة
8. لكل من `product_options`, `coupons`, `delivery_options`, `pickup_time`: إما بناء الميزة كاملة (Schema → Admin UI → Public UI → دمج في create_order والحساب) أو حذف الجداول بـmigration. الجداول الميتة بدون كود = فخ لمن يعدل لاحقًا.

### المرحلة 4 — تجهيز للنشر
9. توحيد المسارات المطلقة (uploads/templates) بدل النسبية.
10. إزالة credentials من alembic.ini (env.py يقرأ من settings أصلًا).
11. تحديث branding (manifest, sw, render.yaml, README, .env) للاسم الجديد.

### قواعد دائمة لأي AI/مطور يعدل المشروع
- لا يُعدَّل `models.py` أبدًا وحده؛ أي تغيير حقل = جرد شامل لاستخداماته (grep).
- كل تغيير مخطط عبر `alembic revision --autogenerate` فوق `852de6ed0909` — لا دمج/حذف migrations قائمة بعد الآن.
- لا تغيير شكل بيانات السلة في localStorage بدون versioning للمفتاح.
- لا تغيير آلية المصادقة أو اسم الـcookie ضمن تعديلات الميزات.
- الأسعار تُحسب دائمًا في الـBackend (الوضع الحالي صحيح) — لا ثقة بأسعار localStorage.

---

## مصادرة الأدلة

- الكود الحالي بكامل طبقاته (models/schemas/routers/templates/static/alembic/config).
- `git log --stat` لكل الـ7 commits + diffs الكاملة للتحويلات.
- الملفات المحذوفة المسترجعة من التاريخ: `0001_initial.py`, `0002_addons.py`, `0003_order_item_quantity_numeric.py`, `models.py` و`cart.js` و`product_card.html` و`public.py` الأصلية.
