# V2 Readiness Audit — Restaurant Ordering System

| Field | Value |
| --- | --- |
| Repository | `F:\المشروع` |
| Branch | `v2-development` (identical to `main`) |
| HEAD | `df45f4a` — "Finalize V1" |
| Working tree | Clean at the time of writing; this document is the only change |
| Scope | Read-only audit of the existing V1 system. No code, schema, API, UI, or dependency was modified. |

This document records the state of the codebase as found, the concrete defects that
were confirmed by reading the code, and the migration risks that V2 work will hit.
Every claim below cites the file and line that supports it.

Related documents already in the repository (older, written during the reverse-engineering phase):

- `PROJECT_ARCHITECTURE.md`
- `FINAL_REPORT.md`
- `audit_report.md` and `admin/audit_report.md`
- `.kilo/plans/1787775462865-restaurant-reverse-engineering.md`

---

## 1. Executive summary

V1 is a coherent, well-factored monolith: one FastAPI application, one PostgreSQL
database, server-rendered Jinja2 pages, and a WhatsApp hand-off as the payment/fulfilment
channel. The core ordering domain is in better shape than most projects at this stage —
pricing is recomputed server-side from the database on every request
(`app/ordering.py:299`), option-group rules are enforced on the backend and not only in
the UI (`app/ordering.py:150`), and the admin area has a real CSRF implementation
(`app/deps.py:34`).

The risks are concentrated in four places, in priority order:

1. **A confirmed 500 on order creation.** A phone value of eight whitespace characters
   passes Pydantic validation and then crashes `POST /orders` with `AttributeError`
   when a `first_order_only` coupon is used. See §13.1.
2. **The service worker controls almost nothing.** It is served from `/static/js/sw.js`,
   so its default scope is `/static/js/`, not the manifest's declared `/`. Offline
   support is effectively absent and the precache list is dead weight. See §9.2.
3. **No protection on the admin login.** `POST /admin/login` accepts unlimited attempts;
   the in-memory rate limiter in `app/main.py:78` covers only three public paths. See §10.2.
4. **Production is effectively plain HTTP.** `compose.yaml:31` defaults `COOKIE_SECURE`
   to `false` while `APP_ENV` defaults to `production`, and the port is published
   directly with no TLS terminator. See §11.2.

Multi-branch support, the largest V2 feature, is also the largest schema change and has
no supporting structures at all in the current model. See §5 and §13.2.

Nothing found requires a rewrite. Every item below is either a contained fix or a
well-understood additive migration.

---

## 2. Architecture overview

### 2.1 Runtime shape

```
Browser (PWA)
   │  HTML pages, JSON API calls
   ▼
FastAPI (app/main.py)
   ├── /static   → app/static        (StaticFiles mount)
   ├── /uploads  → app/uploads       (StaticFiles mount)
   ├── middleware: rate_limit_middleware   (app/main.py:78)
   ├── middleware: admin_csrf_cookie       (app/main.py:98)
   └── routers
         ├── public      (app/routers/public.py)  — customer UI + POST /orders
         ├── api         (app/routers/api.py)     — customer JSON API
         ├── admin_auth  (app/routers/admin_auth.py) — /admin/login, /admin/logout
         └── admin       (app/routers/admin.py)   — protected admin area
   ▼
PostgreSQL 17 (SQLAlchemy 2.0 + Alembic)
```

Deployment process: `Dockerfile:21` → `alembic upgrade head` → `app.seed_admin` →
`gunicorn -k uvicorn.workers.UvicornWorker`.

### 2.2 Module responsibilities

| Module | Responsibility | Notes |
| --- | --- | --- |
| `app/main.py` | App assembly, static mounts, rate limiting, CSRF cookie issuance | 128 lines |
| `app/config.py` | Pydantic Settings, production secret validation | rejects `change-me` and `admin12345` in production |
| `app/database.py` | Engine, `SessionLocal`, `get_db` generator | `pool_pre_ping=True`, `expire_on_commit=False` |
| `app/models.py` | 15 declarative models | no `create_all`; Alembic owns the schema |
| `app/schemas.py` | 20+ Pydantic request/response models | single source of truth for input validation |
| `app/ordering.py` | Pricing engine — the only source of pricing truth | shared by preview, coupon validation, and order creation |
| `app/utils.py` | Slugs, money formatting, phone normalization, WhatsApp message/URL builders, image upload | Arabic search normalization lives here |
| `app/security.py` | bcrypt, JWT HS256, HMAC CSRF token | stateless, no server-side session store |
| `app/deps.py` | `get_current_admin`, `verify_admin_csrf` | |
| `app/routers/*` | HTTP layer | 4 routers, no service/repository layer |

### 2.3 Architectural characteristics

- **Monolith, layered by concern, not by strict layer discipline.** Routers call
  `db.scalar(select(...))` directly. There is no repository abstraction. This is
  consistent and readable; it is also the reason a "shared catalog across branches"
  change will touch many query sites (§13.2).
- **No business-logic duplication in pricing.** `compute_pricing` is the single
  implementation used by `POST /orders` (`app/routers/public.py:183`),
  `POST /api/orders/preview` (`app/routers/api.py:201`), and
  `POST /api/coupons/validate` indirectly. This is the strongest part of the design and
  should be preserved.
- **Templates are duplicated between routers.** `public.py:33` and `admin_auth.py:19`
  each build their own `Jinja2Templates`, and `public.py:34`, `admin.py:52-55` each
  register their own globals. Adding a Jinja global means editing up to three places.
- **Two near-identical settings helpers.** `public.get_settings`
  (`app/routers/public.py:37`) and `admin.get_or_create_settings` both create the
  singleton row on demand. `db.scalar(select(RestaurantSettings))` raises
  `MultipleResultsFound` if a second row ever appears; there is no unique constraint
  preventing that.

---

## 3. Request lifecycle and order flow

### 3.1 Customer order flow (end to end)

1. **Browse.** `GET /` (`app/routers/public.py:47`) loads active categories with
   `selectinload(Category.products)` and, separately, all active products. Home renders
   the featured slider, category rail, and product grid.
2. **Product detail.** `GET /products/{slug}` (`app/routers/public.py:125`) filters on
   `is_active` **and** `is_available`, and eager-loads category, addons, options, and
   option groups.
3. **Cart.** Purely client-side in `localStorage` under the key `cart`
   (`app/static/js/cart.js:2`). Line identity is
   `productId-optionIds.addonIds-note` (`cart.js:17`), so changing any option, addon, or
   note creates a separate line.
4. **Preview.** `POST /api/orders/preview` on every cart mutation
   (`cart.js:142`). A sequence counter discards out-of-order responses
   (`cart.js:139,152`). Totals shown to the user are always the server's numbers.
5. **Submit.** `POST /orders` with JSON (`cart.js:306`).
6. **Redirect.** On success the client clears the cart and navigates to
   `data.whatsapp_url` (`cart.js:335`).

### 3.2 `POST /orders` step by step

`app/routers/public.py:177`

| Step | Action | Line |
| --- | --- | --- |
| 1 | Validate scheduled time is in the future | 180 |
| 2 | Recompute all pricing from the database | 183 |
| 3 | Require address for non-pickup delivery | 193-196 |
| 4 | Build `Order`, `OrderItem`, `OrderItemAddon`, `OrderItemOption` snapshots | 199-251 |
| 5 | Upsert `Customer` by normalized phone, `flush`, catch `IntegrityError` | 253-270 |
| 6 | Re-check `first_order_only` under `SELECT … FOR UPDATE` row locks | 272-293 |
| 7 | Increment `Coupon.usage_count` | 295-298 |
| 8 | `add(order)` + `flush` to obtain the order id | 300-302 |
| 9 | Build the WhatsApp message, store it, `commit` | 304-325 |
| 10 | Return `OrderCreated` with the `wa.me` URL | 327-336 |

### 3.3 Semantics that follow from this flow

- **The order is committed before WhatsApp opens.** There is no confirmation step and no
  server-side acknowledgement from the restaurant. If the user closes the tab, the
  network drops, or they never press send in WhatsApp, a `pending` order already exists
  in the database. The restaurant sees an order the customer never sent. This is the
  single most important product-semantics issue in V1 (§13.3).
- **Order status has no "abandoned" state.** `OrderStatus`
  (`app/schemas.py:11`) is `pending | preparing | delivered | cancelled`. Nothing
  distinguishes "waiting for the customer to press send" from "the restaurant received
  this".
- **Status transitions are unvalidated and unaudited.** `POST /admin/orders/{id}/status`
  (`app/routers/admin.py:713`) accepts any enum value with no transition rules, no
  reason field, and no record of which admin changed it or when.
- **Coupon usage is consumed even for orders that are later cancelled.**
  `usage_count` is incremented at `public.py:297` in the same transaction; cancelling
  the order later does not decrement it.

---

## 4. Pricing and validation logic

`app/ordering.py` is the authoritative implementation.

### 4.1 What the server refuses to trust

Everything the client sends is re-derived. Client-supplied prices never reach the
database. `resolve_priced_lines` (`app/ordering.py:66`) re-reads products, options,
groups, and addons and raises `400` when:

- a product is missing, inactive, or unavailable (`ordering.py:80`);
- an option does not belong to the product (`ordering.py:131`);
- an option's group does not belong to the product (`ordering.py:137`);
- an addon does not belong to the product (`ordering.py:175`);
- a group is single-select and more than one option was chosen (`ordering.py:152`);
- `max_selections` is exceeded (`ordering.py:157`);
- `is_required` or `min_selections` is not met (`ordering.py:162-169`).

Duplicate addon ids within one line are de-duplicated before pricing
(`ordering.py:173`).

### 4.2 Money arithmetic

- `Decimal` throughout, `Numeric(10, 2)` in the database.
- `quantize_money` uses `ROUND_HALF_UP` (`ordering.py:34`).
- Unit price = `base_price` + sum of selected option adjustments, then
  addons are added to the unit price and the result is multiplied by quantity
  (`ordering.py:182-185`).
- `total = subtotal - discount + delivery_fee`, floored at zero
  (`ordering.py:326-328`).
- Percentage discounts are computed on the subtotal and are not capped at 100%; a
  coupon configured with `discount_value > 100` produces a total of `0.00` because of
  the floor rather than an error. Worth a guard.

### 4.3 Delivery resolution

`ordering.py:281-319` enforces the combination rules:

- zone requires a delivery option (`ordering.py:314`);
- zone is incompatible with `pickup` (`ordering.py:315`);
- a `zone`-type option requires a zone (`ordering.py:317`);
- fee comes from the zone when present, otherwise from the option
  (`ordering.py:319`).

### 4.4 Coupon validation

`validate_coupon` (`ordering.py:264`) and `coupon_error` (`ordering.py:214`) check, in
order: coupon exists, is active, is within `start_date`/`end_date`, has remaining
`usage_limit`, meets `minimum_order`, and — for `first_order_only` — has no prior
non-cancelled order for the normalized phone.

The `first_order_only` lookup is done in SQL with three nested `regexp_replace` calls
(`ordering.py:238-250`, mirrored at `public.py:279-291`) to bridge two different phone
storage formats: `Customer.phone` is normalized, `Order.customer_phone` is stored raw.

**Consequence:** the first-order-only path is PostgreSQL-specific. It cannot run on
SQLite, so any unit-test harness that wants to cover it needs a real Postgres instance.
The existing tests already do this (§12).

### 4.5 Race-condition handling

`public.py:272-293` re-validates the first-order coupon under
`SELECT … FOR UPDATE` on both the `Customer` and `Coupon` rows before incrementing
`usage_count`. `test_race.py` exercises this with threads and savepoints. This is a
genuinely careful piece of engineering and should be preserved through V2.

---

## 5. Database schema audit

15 models in `app/models.py`. All money columns are `Numeric(10, 2)`. All timestamps are
`DateTime(timezone=True)` with `server_default=func.now()`.

### 5.1 Table inventory

| Table | Purpose | Key constraints |
| --- | --- | --- |
| `admins` | Admin accounts | `username` unique + indexed; `password_hash`; `is_active` |
| `categories` | Menu sections | `slug` unique + indexed; `is_active`; `sort_order` |
| `products` | Menu items | `slug` unique; `category_id` FK `RESTRICT`; 4 boolean flags |
| `product_option_groups` | Option/size groups | `product_id` FK `CASCADE`; required/multiple/min/max |
| `product_options` | Individual options | `product_id` FK `CASCADE`; `group_id` FK `CASCADE`; `price_adjustment` |
| `addons` | Product extras | `product_id` FK `CASCADE`; `price` |
| `orders` | Order header | 7 indexed columns; 4 optional FKs; 4 money columns |
| `order_items` | Order lines | `product_id` FK `SET NULL`; name/price snapshotted |
| `order_item_options` | Chosen options snapshot | `option_id` FK `SET NULL`; group/name/price snapshotted |
| `order_item_addons` | Chosen addons snapshot | `addon_id` FK `SET NULL`; name/price snapshotted |
| `coupons` | Discounts | `code` unique; dates required; `usage_limit`/`usage_count`; `first_order_only` |
| `delivery_options` | Pickup/fixed/zone methods | `delivery_type`; `fee` |
| `delivery_zones` | Named delivery areas | `name` unique; `fee` |
| `customers` | Deduplicated customers | `phone` unique; `first_order_at`/`last_order_at` NOT NULL |
| `restaurant_settings` | Singleton config | no uniqueness constraint on the singleton |

### 5.2 Snapshot design — good

`order_items`, `order_item_options`, and `order_item_addons` all denormalize name and
price at order time, and their FKs to live catalog rows are `SET NULL`. Editing or
deleting a product never rewrites history. This is the correct call and it is already
done.

### 5.3 Gaps and risks

| # | Gap | Impact |
| --- | --- | --- |
| D1 | **No branch entity anywhere.** No `branches` table, no `branch_id` on `products`, `categories`, `coupons`, `delivery_zones`, `delivery_options`, or `orders`. | Multi-branch is a cross-cutting schema change (§13.2). |
| D2 | **No inventory or stock.** `products.is_available` is a single manual flag shared by all contexts. | Per-branch stock levels require new tables. |
| D3 | **`RestaurantSettings` is an unenforced singleton.** `db.scalar(select(RestaurantSettings))` will raise `MultipleResultsFound` if two rows exist, and there is nothing stopping a second insert. | Low probability, high blast radius. |
| D4 | **No push subscription table.** Nothing stores an endpoint for web push. | Required for V2 notifications. |
| D5 | **No order status history / audit table.** Status changes overwrite in place. | Required for accountability and for any dispute resolution. |
| D6 | **No order number separate from the integer id.** Customers and the restaurant communicate using the sequential `id`, which leaks total order volume. | Cosmetic/reputational. |
| D7 | **`Customer.first_order_at`/`last_order_at` are only updated on order creation**, never on cancellation. A cancelled first order still marks the customer as having ordered. | Interacts with `first_order_only` coupons. |
| D8 | **No `CHECK` constraints** on money columns being non-negative, on `quantity > 0`, or on `discount_type IN ('percentage','fixed')`. | Application-level validation only. |
| D9 | **No soft delete** on any entity. Deletes are hard, and `products.category_id` is `RESTRICT` so a category with products cannot be deleted. | Archive/history features need `deleted_at`. |
| D10 | **No timezone/locale column anywhere.** `working_hours` on `RestaurantSettings` is a free-text `String(250)`, not structured. | Cannot drive an "open now" check or scheduling validation (§13.4). |
| D11 | **`Order.payment_method` is a bare `String(20)`** while the Pydantic layer enforces `Literal["cash","ewallet","card"]`. | No DB-level integrity; adding a method needs a migration. |
| D12 | **No index on `orders.customer_phone`.** The `first_order_only` check does a computed `regexp_replace` scan over `orders` for every qualifying checkout. | Will degrade linearly with order volume. |
| D13 | **No full-text search index.** Search is `ilike '%term%'` (`public.py:107`). | Acceptable at current scale; degrades on large menus. |

### 5.4 Relationship hygiene

- `ondelete` choices are deliberate and mostly correct: `CASCADE` for catalog children,
  `SET NULL` for order history, `RESTRICT` for `products.category_id`.
- `Product.options` and `Product.addons` relationships lack `cascade`, but they are
  nullable FKs on the child side, so this is fine.
- `RestaurantSettings` has no relationships, which is correct for a singleton.

---

## 6. Migrations and schema history

15 Alembic revisions in `alembic/versions/`, forming a single unbroken linear chain.

```
852de6ed0909  initial_restaurant_database
  └─ c7d9f2a41b36  restaurant_alignment
       └─ 5f2d9a7c31e4  option_groups_item_notes_option_snapshots
            └─ 9b7e4c2d15a8  order_price_breakdown_pickup_flag
                 └─ c8f1a2b3d4e5  first_order_only
                      └─ d8e3c5b72a19  restaurant_settings_description
                           └─ a1b2c3d4e5f6  customers_and_delivery_zones
                                └─ 3a2b1c0d9e8f  coupons_first_order_only   (no-op marker)
                                     └─ 4d5e6f7a8b9c  normalize_customer_phone (data migration)
                                          └─ c32c9e6af771  app_icon_url_and_payment_method
                                               └─ e7f2a1b3c4d5  product_featured_and_offer
                                                    └─ d44b83933e46  delivery_type_to_options
                                                         └─ f1a2b3c4d5e6  orderitem_product_fk_to_set_null
                                                              └─ a1b2c3d4e5f7  make_order_items_product_id_nullable  ← HEAD
```

Observations:

- **Single head.** `a1b2c3d4e5f7`. No merge commits, no branches in history.
- **Two migrations address `first_order_only`.** `c8f1a2b3d4e5` adds the column;
  `3a2b1c0d9e8f` is an explicit no-op marker whose docstring says so
  (`3a2b1c0d9e8f:7-8`). Harmless, but confusing to read.
- **`f1a2b3c4d5e6` and `a1b2c3d4e5f7` overlap.** The head revision's name implies it
  makes `order_items.product_id` nullable, which the previous revision also appears to
  address. Existing databases may have applied both; a fresh `alembic upgrade head` must
  be tested against an empty database, not only against a migrated copy.
- **`4d5e6f7a8b9c` is a data migration** that rewrites `customers.phone`. It has a
  working `upgrade` and an empty `downgrade` (`4d5e6f7a8b9c:41-42`). The downgrade is
  not reversible, which is acceptable for a normalization pass but should be documented.
- **Alembic is applied at container start** (`Dockerfile:21`), not as a separate
  migration job. With more than one replica this races (§11.3).

---

## 7. API surface

### 7.1 Public, unauthenticated

| Method | Path | Purpose | Line |
| --- | --- | --- | --- |
| GET | `/health` | Liveness probe (no DB check) | `main.py:26` |
| GET | `/` | Home page | `public.py:47` |
| GET | `/manifest.json` | Dynamic PWA manifest from DB settings | `public.py:68` |
| GET | `/search?q=` | HTML product-grid partial | `public.py:98` |
| GET | `/products/{slug}` | Product detail page | `public.py:125` |
| POST | `/orders` | Create order, return `wa.me` URL | `public.py:177` |
| GET | `/api/categories` | Active categories | `api.py:46` |
| GET | `/api/categories/{slug}/products` | Products in a category | `api.py:57` |
| GET | `/api/products` | Products, optional `category` and `q` | `api.py:74` |
| GET | `/api/products/{slug}` | Product detail JSON | `api.py:105` |
| GET | `/api/delivery-options` | Active delivery options | `api.py:143` |
| GET | `/api/delivery-zones` | Active delivery zones | `api.py:154` |
| POST | `/api/orders/preview` | Price a cart without saving | `api.py:192` |
| POST | `/api/coupons/validate` | Validate a coupon against a cart | `api.py:165` |

### 7.2 Admin

`/admin/login` and `/admin/logout` in `admin_auth.py`; 29 routes in `admin.py` covering
dashboard, categories, products, addons, option groups, options, orders, coupons,
delivery options, delivery zones, customers, customer detail, customer CSV export, and
settings.

### 7.3 API consistency issue

`GET /api/products/{slug}` (`api.py:105-109`) filters on `is_active` only, while
`GET /products/{slug}` (`public.py:128-131`) filters on `is_active` **and**
`is_available`. The JSON API therefore returns full detail for a product the HTML page
treats as unavailable. Any V2 client built on the JSON API would show sellable products
that the ordering endpoint then rejects.

Related: `is_available` is not applied to `/api/products` (`api.py:80`),
`/api/categories/{slug}/products` (`api.py:67`), `GET /` (`public.py:58`), or
`GET /search` (`public.py:101`). This is a deliberate UI choice for the HTML pages —
`product_card.html:27` renders a "sold out" badge — so the fix is a scoping decision
rather than a blanket change. The featured slider (`featured_slider.html:2`) filters
`is_featured and is_active` but not `is_available`, so sold-out products can appear in
the hero.

---

## 8. Admin panel

### 8.1 Authentication

| Concern | Implementation | Assessment |
| --- | --- | --- |
| Password storage | bcrypt via passlib (`security.py:16`) | Correct |
| Session | Stateless JWT HS256 in the `admin_access_token` cookie | Acceptable for a single app |
| Cookie flags | `httponly=True`, `samesite="lax"`, `secure=settings.cookie_secure` | `cookie_secure` defaults to `true` in config but `false` in compose (§11.2) |
| Token lifetime | `access_token_expire_minutes = 60 * 24` = 24 hours | Long; no refresh or sliding renewal |
| Revocation | None. No denylist, no session table. | A stolen token is valid for up to 24 h. Deactivating the admin blocks new logins and existing requests (`deps.py:28`) but does not invalidate issued tokens — actually it does, because every request re-reads the admin row. Acceptable. |
| Per-request check | `get_current_admin` re-queries `Admin` and requires `is_active` (`deps.py:28`) | Good |
| Roles | None. Every admin is full access. | Blocks per-branch scoping (§13.2) |
| CSRF | Double-submit with an HMAC-signed token bound to `APP_SECRET_KEY` (`deps.py:34`, `security.py:45`) | Correct implementation |
| CSRF cookie issuance | `admin_csrf` middleware, `httponly=False` so JS can read it (`main.py:98`) | Required for the double-submit pattern |
| Login throttling | **None** | Finding S1 |
| Login response | Generic Arabic error, `400` | No user enumeration |

### 8.2 Bootstrap and credential rotation

`app/seed_admin.py` creates the admin **only if the username does not exist**. It never
updates `password_hash` for an existing row. Consequence: rotating `ADMIN_PASSWORD` in
`.env` on a running deployment has no effect. Password rotation today requires direct
database access. This is a real operational trap and is not documented anywhere.

`seed_admin` also creates the `RestaurantSettings` singleton on first run, using
`APP_NAME` as the restaurant name.

### 8.3 Admin surface

All admin routes carry `Depends(get_current_admin)` and `Depends(verify_admin_csrf)`
at the router level (`admin.py:49`), so protection cannot be forgotten per-route. The
`admin_auth` router carries only `verify_admin_csrf` (`admin_auth.py:17`), which is
correct — login needs CSRF but not a session.

Uploads (`admin.py:344, 408, 1036-1038`) go through `save_image` (`utils.py:102`):
UUID filenames, a 3-type allowlist by declared `content_type`, a 3 MB cap, written to
`app/uploads/{categories,products,logos,banners,icons}`.

Weaknesses: `content_type` is client-supplied and is not verified against magic bytes;
images are not re-encoded or resized; deleted images are never cleaned up; and the
`/uploads` mount serves whatever is on disk with the extension the client declared.
Acceptable for an authenticated admin-only feature, but it should not be exposed
publicly or extended to customer-uploaded content without a rewrite.

### 8.4 Customers and CSV

`GET /admin/customers/export` (`admin.py:898`) already produces a BOM-prefixed UTF-8 CSV
with an Arabic header row. The `Customer` view supports sorting, search, date range, and
a minimum-order filter, and the detail page recomputes totals excluding cancelled orders
(`admin.py:951-953`).

Note the inconsistency: `customer_detail` excludes cancelled orders from totals, but
`customer_rows` is used by the export, and the dashboard aggregates are computed
separately. V2 should define one canonical rule for whether cancelled orders count
toward revenue (§13.6).

---

## 9. PWA and frontend

### 9.1 Manifest

`GET /manifest.json` (`public.py:68`) is generated from `RestaurantSettings`: name,
description, `theme_color` from the admin-configured palette, and either the uploaded
`app_icon_url` or the bundled icons at 192 and 512.

`start_url` and `scope` are both `/`; `lang` is `ar`, `dir` is `rtl`, `display` is
`standalone`, `orientation` is `portrait`.

Gaps: no `id`, no `purpose: "any maskable"`, no 96/144/384 sizes, and no
`related_applications`. A second, static `app/static/manifest.json` also exists in the
repository and is shadowed by the dynamic route — dead weight and a source of confusion
for anyone editing assets.

### 9.2 Service worker — confirmed defect

`app/static/js/sw.js` is registered at `/static/js/sw.js` from both
`app/templates/public/base.html:28` and `app/templates/admin/base.html:44`.

A service worker served from `/static/js/sw.js` defaults to a scope of
`/static/js/`. The manifest declares `scope: "/"`. Therefore:

- the worker does **not** control `/`, `/search`, `/products/...`, or `/static/css/*`;
- the only in-scope request that its `fetch` handler can serve is `/static/js/cart.js`;
- the five precached assets (`tailwind.css`, `app.css`, `cart.js`, two icons) are stored
  but essentially unreachable by the controlled page.

The fix is a one-line change plus a cache-version bump: serve the worker from a
root-scoped path such as `/sw.js` (a route that returns the file with
`Service-Worker-Allowed: /`) or move it to the application root. Until then, the PWA is
installable but has no working offline behaviour.

Additional frontend observations:

- **Cache strategy is cache-first with no invalidation** for `style`/`script`/`image`
  destinations (`sw.js:22-50`). Combined with `caches.match(..., {ignoreSearch: true})`
  and a hand-maintained `CACHE_NAME = 'restaurant-cache-v2'`, a CSS change ships only
  when someone edits that string by hand.
- **Alpine.js is loaded from a third-party CDN at runtime** —
  `https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js`
  (`app/templates/public/base.html:19`). The version is a floating range, there is no
  subresource integrity attribute, and the precache list does not include it. An
  offline install would have no cart, no checkout, and no interactivity. Vendoring
  Alpine into `/static/js/` is a small, high-value change.
- **Google Fonts** is also a runtime CDN dependency (`public/base.html:17`).
- No `push`, `notificationclick`, or `notificationclose` handlers exist in `sw.js`.
  No `Notification.requestPermission()` call exists in any template. There is no
  subscription endpoint. **Push notifications are entirely unimplemented** even though
  VAPID keys are present in `.env` (§13.5).

### 9.3 Tailwind

`app/static/css/tailwind.css` is a committed build artifact. There is no
`package.json`, no `tailwind.config.js` build script wired into CI, and no
`npm run build`. `tailwind.config.js` and `tailwind.input.css` exist as sources.

Consequence: the admin's `theme_colors` setting cannot regenerate CSS. It works only
because `app.css` and template-level inline styles consume the palette exposed by
`theme_colors` (`public.py:34`, `admin.py:53`) and by the `theme-color` meta tag
(`public/base.html:7`). Any V2 feature that needs new utility classes requires a manual
Tailwind rebuild outside the repository's documented workflow.

### 9.4 Cart and checkout UX

- `Cart` is namespaced as a global object in `cart.js`; `productDetail()` is a second
  global factory. There is no module system and no bundler.
- Quantity is clamped to 1..999 in both the product grid and the cart, matching the
  server-side `le=999` bound (`schemas.py:60`).
- `refreshPreview` is fired on every cart mutation with no debounce. On a 15-line cart
  this is a request per click.
- Scheduled-time input is a native `datetime-local`, and the browser's local timezone is
  sent as UTC via `toISOString()` (`cart.js:301`). The server stores it as
  `DateTime(timezone=True)` and renders the WhatsApp message using server-local time
  (`utils.py:151`). If the server timezone differs from the customer's, the message
  shows a different time than the customer chose. There is no restaurant timezone
  setting (D10).
- The phone input (`checkout_form.html:66`) is `required` with no `type="tel"`, no
  `pattern`, and no `minlength`. `required` is satisfied by eight spaces, which is
  exactly the path to the 500 in §13.1.

---

## 10. Security

### 10.1 What is done well

- bcrypt password hashing; HS256 JWT with an expiry; `hmac.compare_digest` for both
  password-independent comparisons (`security.py:60`, `deps.py:47`).
- HMAC-signed CSRF tokens rather than random opaque tokens, so a token cannot be
  forged without `APP_SECRET_KEY`.
- Session fixation is not possible: the auth cookie is issued only after a successful
  credential check, and the CSRF cookie is re-minted when invalid.
- Production secret validation refuses to boot with `change-me` or `admin12345`, and
  enforces an 8-character minimum (`config.py:22-39`).
- Router-level dependencies make it structurally hard to add an unprotected admin route.
- Every price is recomputed server-side. Client-side price manipulation is impossible.
- Upload size and type are bounded server-side.

### 10.2 Confirmed findings

| ID | Finding | Evidence | Severity |
| --- | --- | --- | --- |
| S1 | **`POST /admin/login` has no rate limiting or lockout.** The rate-limit middleware covers only `/orders`, `/api/orders/preview`, `/api/coupons/validate` (`main.py:71-75`). Login is not in the list, so the login form can be brute-forced at unlimited rate. | `main.py:71`, `admin_auth.py:28` | High |
| S2 | **Production defaults to `COOKIE_SECURE=false` and publishes port 8000 directly.** `compose.yaml:26` sets `APP_ENV=production` while `compose.yaml:31` defaults `COOKIE_SECURE` to `false`, and `compose.yaml:32-33` publishes the container port with no TLS terminator. The auth cookie is then transmitted over plain HTTP. | `compose.yaml:26-33` | High |
| S3 | **Rate limiting is per-process and defeated by proxying.** Gunicorn runs a single worker (`Dockerfile:21`) so counters are coherent today, but any `--workers > 1` gives each process its own store, multiplying the effective limit. Separately, `UvicornWorker` does not enable proxy headers, so `request.client.host` (`main.py:53`) behind a reverse proxy or a published port is the proxy's address — collapsing all customers into one bucket. | `main.py:43-64`, `Dockerfile:21` | High |
| S4 | **`APP_SECRET_KEY` and `ADMIN_PASSWORD` rotation is not supported by the bootstrap script.** Existing rows are never updated. | `seed_admin.py:17-25` | Medium |
| S5 | **No security headers.** No `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, or `Content-Security-Policy` is set anywhere. A CSP is also not currently possible because of the CDN Alpine dependency. | absence across `app/main.py` | Medium |
| S6 | **Upload content type is trusted, not verified.** `save_image` branches on the client-declared `content_type` and writes that extension. | `utils.py:104-115` | Low |
| S7 | **The rate-limit store is never evicted for abandoned keys.** Pruning runs only when a key is re-requested (`main.py:62-63`). Bounded in practice by the fixed path set, so low risk. | `main.py:43` | Low |
| S8 | **No logging of any kind.** No `logging` import exists in `app/`. No failed-login events, no admin mutations, no order transitions, no request IDs. An incident leaves no trace beyond database rows. | absence | Medium |
| S9 | **No dependency vulnerability scanning or update automation.** All 14 production dependencies are pinned exactly, which is good, but nothing checks them. | `requirements.txt` | Low |
| S10 | **No CORS configuration**, which is correct — the API is same-origin only. Noted as a positive. | — | Info |

### 10.3 Secrets handling

`.env` is git-ignored (`.gitignore` "Environment files" block) and `.env.example`
contains placeholders only. `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT`
are present in the local `.env` but are referenced by **zero** lines of application code.
They are dead configuration and should either be wired up or removed to avoid implying
that push is ready.

---

## 11. Deployment and operations

### 11.1 What exists

- `Dockerfile`: `python:3.13.5-slim`, installs `requirements.txt`, copies `alembic.ini`,
  `alembic/`, `app/`, `.env.example`, creates `app/uploads`, exposes 8000.
- `compose.yaml`: `postgres:17-alpine` with a named volume and a `pg_isready`
  healthcheck; a `web` service gated on `condition: service_healthy`; a named volume for
  uploads; an HTTP healthcheck against `/health`.
- `render.yaml`: a Render blueprint running the same migrate-then-serve command with
  `COOKIE_SECURE=true`.

`.dockerignore` exists. Uploads are on a named volume so they survive container
replacement. Secrets are injected via environment with `:?` guards that fail fast.

### 11.2 Findings

| ID | Finding | Evidence |
| --- | --- | --- |
| O1 | **No reverse proxy or TLS.** The port is published directly. See S2. | `compose.yaml:32-33` |
| O2 | **Migrations run inside the container's start command.** Any replica or scale-out runs `alembic upgrade head` concurrently. Alembic has no advisory lock here, so concurrent upgrades can fail or corrupt. | `Dockerfile:21`, `render.yaml:9` |
| O3 | **`/health` does not check the database.** It returns `{"status": "ok"}` unconditionally, so the compose and Render healthchecks report healthy while Postgres is unreachable or migrations are pending. | `main.py:26-28` |
| O4 | **Single Gunicorn worker, no worker tuning.** No `--workers`, no `--timeout`, no `--access-logfile`, no `--forwarded-allow-ips`. One synchronous worker is the ceiling for a public-facing site. | `Dockerfile:21` |
| O5 | **No logging or log shipping.** See S8. | — |
| O6 | **No backup or restore procedure.** The Postgres volume is the only copy of the data. No `pg_dump` automation, no documented restore. | `compose.yaml:9-10` |
| O7 | **No resource limits and no `deploy` block.** No CPU/memory caps on either service. | `compose.yaml` |
| O8 | **Rollback is per-image, not per-migration.** Reverting the image re-runs `alembic upgrade head` forward; there is no `alembic downgrade` path in the start command. | `Dockerfile:21` |
| O9 | **Tailwind cannot be rebuilt in CI.** See §9.3. | absence of `package.json` |
| O10 | **No CI pipeline.** No workflow, no lint, no type-check, no test gate. | absence |

---

## 12. Testing and quality gates

### 12.1 What exists

12 top-level scripts, each a standalone `python` program, not a pytest suite:

| Script | Coverage |
| --- | --- |
| `test_backend.py` (42 KB) | Broad order/pricing/customer behaviour |
| `test_stage1.py` … `test_stage4.py`, `test_stage8.py` | Customer detail, options, first-order coupons, analytics, featured/offer flags |
| `test_race.py` | Threaded first-order-coupon race with savepoints |
| `test_payment.py` | Payment-method behaviour |
| `test_pwa.py` | Manifest, service worker, install prompt, icons |
| `test_pages_smoke.py` | Page-level smoke tests |
| `test_rate_limit.py` | Rate-limit function and middleware behaviour |
| `test_phase2.py` | Options validation |

Characteristics:

- They import `TestClient` from `fastapi.testclient` and run against the **development
  database**, cleaning up with manual transaction rollback and savepoints.
- They print human-readable Arabic/English output and `sys.exit(1)` on failure. There is
  no assertion framework, no fixtures, no parametrisation, no reporting.
- `requirements.txt` declares **no test dependencies at all** — no `pytest`, no `httpx`
  (which `TestClient` requires). `httpx` is present only in the local `.venv`, so a clean
  machine cannot run the suite as written.
- `test_pwa.py` asserts on manifest and service-worker content, so it will not catch the
  scope defect in §9.2 — it checks that a worker file exists, not that its scope covers
  the site.

### 12.2 Gaps

- No `pytest`, no fixtures, no database isolation strategy that does not rely on a live
  development database.
- No coverage measurement.
- No CI to run any of it.
- The PostgreSQL-only `regexp_replace` path (§4.4) means the test suite is coupled to a
  real Postgres instance and cannot be moved to SQLite.
- No test exists for the eight-whitespace phone crash (§13.1), for admin login throttling
  (S1), or for status-transition rules.

---

## 13. V2 feature readiness

Thirteen work areas were in scope for V2. Each is assessed below against the code as it
stands. Two decisions marked **BLOCKED** need a product answer before implementation can
start.

### 13.1 Confirmed defect: whitespace phone with a first-order coupon → HTTP 500

**Severity: High. Customer-visible. One-line fix.**

Reproduce: `POST /orders` with `"customer_phone": "        "` (eight spaces) and any
active `first_order_only` coupon.

Chain of events:

1. `OrderCreate.customer_phone` is `Field(min_length=8, max_length=30)`
   (`app/schemas.py:68`). Eight spaces satisfies `min_length=8`, so Pydantic accepts it.
   There is no digit or pattern constraint.
2. `normalize_phone("        ")` strips all non-digits and returns `""`
   (`app/utils.py:214-221`).
3. `public.py:254-255` — because `normalized_phone` is falsy, `customer` is never
   looked up and is never created, so it stays `None`. `order.customer_id` is set to
   `None` (`public.py:270`).
4. `public.py:273` — `pricing.coupon.first_order_only` is `True`, so the guard is entered.
5. `public.py:275` — `select(Customer).where(Customer.id == customer.id)` dereferences
   `customer.id` on `None`.

Result: `AttributeError`, unhandled, HTTP 500 with a generic body. No order is created,
but the customer sees an opaque failure and the coupon's `usage_count` is not consumed
(the transaction is rolled back).

The same crash is reachable with any phone input that is 8–30 characters long and
contains no digits — `"          "`, `"         a"`, `"!!!!!!!!"`.

Fix: validate the phone at the schema boundary (require a minimum digit count after
normalization, and reject inputs that normalize to empty), and guard the `customer is
None` case in the `first_order_only` block. Both are small; either alone stops the 500.

### 13.2 Multi-branch — **highest schema risk**

**Status: not started. Requires the largest change in V2.**

Nothing in the current schema is branch-aware (D1). The work is:

1. New `branches` table (name, slug, phone, address, timezone, `is_active`).
2. `branch_id` on `products`, `categories`, `delivery_zones`, `delivery_options`,
   `coupons`, `orders`, `restaurant_settings`, and `admins`.
3. A decision on catalog sharing: one shared catalog with per-branch availability and
   price overrides, or fully independent per-branch catalogs. The second is
   straightforward; the first needs a `product_branch_overrides` table.
4. All 14 public read queries in `app/routers/public.py` and `app/routers/api.py` must
   become branch-scoped, and every public page needs a branch selector that is either in
   the URL or resolved by geolocation.
5. `app/ordering.py` must verify that every cart line, the delivery option, the zone, and
   the coupon all belong to the same branch — otherwise a customer can assemble a cart
   across branches. This is a new invariant with no analogue in the current code.
6. `admin.py` needs branch scoping on 29 routes plus a role model, since a branch manager
   must not see another branch's orders (D-none: there are no roles today, §8.1).
7. `RestaurantSettings` must become per-branch, which means replacing an unenforced
   singleton with a proper one-row-per-branch table (D3).

Migration strategy: additive nullable `branch_id` columns, backfill every existing row
to a single default branch, then a non-null constraint. Do not attempt this in one
revision.

**BLOCKED — needs a decision:** is the catalog shared across branches with per-branch
availability, or is each branch's catalog independent? This changes the schema
substantially and cannot be deferred past the first migration.

### 13.3 Order confirmation semantics

**Status: current behaviour is "commit then redirect to WhatsApp".**

As analysed in §3.3, orders are committed before the customer sends anything, and there
is no way to distinguish an abandoned checkout from a real order. Options:

- **(a) Keep as-is** and add a "new"/"abandoned" marker plus an expiry sweep.
- **(b) Add an explicit confirm step** — the order is created in a `draft` state, the
  customer sends the WhatsApp message, and the restaurant confirms from the admin panel.
  Requires a new status and a way for the admin to reject.
- **(c) Confirm server-side** via a WhatsApp Business API callback, which removes the
  manual step entirely but introduces a paid provider and a webhook.

**BLOCKED — needs a decision:** option (a), (b), or (c). This determines whether V2 needs
a new order status, and it interacts with the existing `pending` orders in production:
whatever is chosen, existing rows need a one-time classification.

### 13.4 Ordering hours and scheduling

**Status: partially present, not enforced.**

- `RestaurantSettings.working_hours` is free text (`models.py:174`), so it cannot be
  evaluated.
- `pickup_time` exists on `Order` and is validated as "in the future"
  (`ordering.py:343-350`).
- There is no "is the restaurant open now" check anywhere, and no closing-time check, so
  a customer can schedule an order for 3 a.m.
- There is no restaurant timezone (D10), and the WhatsApp message renders the scheduled
  time in **server-local** time (`utils.py:151`), which can disagree with what the
  customer selected.

V2 needs a structured hours model (per weekday, open/close, optional break, per branch)
and a timezone per branch.

### 13.5 Push notifications

**Status: entirely unimplemented, despite configured keys.**

- `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` are in `.env` and used by
  nothing (§10.3).
- `pywebpush` is installed in `.venv` but is **not** in `requirements.txt`, so a
  production image built from the Dockerfile cannot send push.
- `sw.js` has no `push`, `notificationclick`, or `notificationclose` handler.
- No template calls `Notification.requestPermission()`.
- No `push_subscriptions` table (D4) and no subscribe endpoint.
- The service worker's broken scope (§9.2) must be fixed first, or notifications will not
  be delivered reliably regardless of the backend work.

Required: a `push_subscriptions` table, a subscribe/unsubscribe endpoint, `pywebpush` in
`requirements.txt`, VAPID settings in `app/config.py`, the three service-worker handlers,
and a consent flow. `notification_data` must also be added to `Settings` to carry
VAPID keys without editing `.env` by hand.

**Consent is a product and legal question**, not only a technical one — see §14.

### 13.6 CSV import and export

**Status: export exists; import does not.**

- Export: `GET /admin/customers/export` (`admin.py:898`) already emits a BOM-prefixed
  UTF-8 CSV with Arabic headers. It is well built.
- Import: nothing. No upload endpoint, no column mapping, no validation report, no
  transaction strategy.

V2 import needs a defined column contract, a dry-run validation pass that reports row
errors without writing, and a duplicate policy. The existing `Customer.phone` uniqueness
means duplicates must be resolved as update-or-skip or update-or-insert.

**BLOCKED — needs a decision:** on a duplicate phone in the import file, does the row
update the existing customer, skip, or fail the whole import? And do cancelled orders
count toward the exported "total spent" figures, or only delivered ones? The detail page
currently excludes cancelled orders (`admin.py:951`) but the export path and the
dashboard each compute their own aggregates.

### 13.7 Customer loyalty

**Status: partial foundation, no loyalty concept.**

What exists and is reusable:

- `Customer` already aggregates orders (`first_order_at`, `last_order_at`) and the admin
  has a customer list with order counts and spend (`admin.py:853`).
- `Coupon` already supports `first_order_only`, `usage_limit`, and date windows, which
  covers the simplest loyalty mechanic — "send a coupon to your best customers" —
  without any new schema.

What is missing: a points or tier table, per-customer balances, an award ledger, and
earned-redeemed separation. If loyalty means only "segment customers and send coupons",
the current schema is sufficient and the feature is mostly a reporting and CSV exercise.
If it means points, expect three new tables and a transaction on every order.

### 13.8 Order tracking for customers

**Status: not started.**

`OrderStatus` is `pending | preparing | delivered | cancelled` (`schemas.py:11`) and
there is no customer-facing order lookup at all. A tracking page needs:

- a public order-lookup endpoint, scoped so one customer cannot enumerate others'
  orders — a phone number plus a short code, not a sequential id (D6 makes the id
  guessable);
- a status the customer can see, with an ETA derived from the delivery option's
  `estimated_time` (`models.py:273`);
- the customer-safe subset of order data only.

The auth/authorization design is the substantive part of this feature, not the UI.

### 13.9 Admin theming

**Status: partially implemented, and constrained by the build setup.**

- `RestaurantSettings.theme_colors` stores a JSON string (`models.py:179`).
- `parse_theme_colors` (`utils.py:85`) validates hex values against
  `HEX_COLOR_RE` and falls back to `THEME_DEFAULTS` — solid input validation.
- Templates receive the palette through the `theme_colors` Jinja global
  (`public.py:34`, `admin.py:53`) and the `theme-color` meta tag (`public/base.html:7`).

Limitations: only three colors exist (`primary`, `secondary`, `accent`); there is no
dark mode; and because `tailwind.css` is a committed artifact with no build pipeline
(§9.3), new utility classes cannot be introduced by changing the theme. V2 theming
should be done with CSS custom properties in `app.css`, not with new Tailwind classes.

### 13.10 WhatsApp integration depth

**Status: `wa.me` deep links only.**

`build_whatsapp_url` (`utils.py:188`) builds a `https://wa.me/<digits>?text=<encoded>`
URL. This works with no provider account, no cost, and no credentials — a deliberate and
sensible V1 choice. It also means the restaurant must manually read and transcribe orders,
and the system gets no delivery receipts or read confirmations.

Escalating to the official API requires a Meta Business account, a verified business
profile, message templates approved in advance, and per-conversation pricing. It is a
product/legal decision, not a code change. Combined with §13.3 option (c), it would also
require a public webhook endpoint with signature verification — new infrastructure that
does not exist today.

### 13.11 Analytics dashboard

**Status: basic aggregates exist.**

The dashboard reports today/week/month order counts, total sales, and customer count.
`GET /admin/orders` computes a filtered count and sales total alongside the paginated
list (`admin.py:674-685`).

Gaps: no revenue-over-time series, no product or category performance, no average order
value trend, no branch breakdown (blocked on §13.2), and no cancellation rate. The
cancelled-order counting question from §13.6 applies here too — sales totals must use one
consistent rule.

### 13.12 Image and media management

**Status: functional, minimal.**

Upload works, is bounded, and uses UUID filenames (`utils.py:102`). There is no
resizing, no thumbnail generation, no WebP conversion, no orphan cleanup when a product
or category is deleted, and no multi-image gallery — `Category.image_url` and
`Product.image_url` are single strings (`models.py:32`, `models.py:51`).

`app/uploads` is a named volume in compose (`compose.yaml:34-35`), which is correct.

### 13.13 Search

**Status: functional, unindexed.**

`build_search_variants` (`utils.py:51`) handles Arabic orthographic variants —
normalisation, `ة`/`ه`, `ي`/`ى`, and a space-stripped form — which is a thoughtful touch
for an Arabic menu. Matching is `ilike '%term%'` (`public.py:107`, `api.py:94`),
optionally repeated per variant, so a single search can issue up to six `ILIKE`
predicates.

It works at current scale. Beyond a few thousand products, PostgreSQL full-text search
with an Arabic configuration, or `pg_trgm`, would be the fix. Not a V2 blocker.

---

## 14. Prioritised plan, risks, and open questions

### 14.1 Phase 0 — fix before any feature work

These are small, independently testable, and each one is a real defect rather than an
improvement. None of them touches the schema or a public contract.

| # | Action | Files | Test to add |
| --- | --- | --- | --- |
| P0.1 | Validate `customer_phone` at the schema boundary: require a minimum digit count after normalization and reject anything that normalizes to empty. Guard `customer is None` in the `first_order_only` block. | `app/schemas.py`, `app/routers/public.py` | Eight-space phone + `first_order_only` coupon returns 400, not 500 |
| P0.2 | Add login throttling to `POST /admin/login`, keyed by IP + username, with a lockout window. | `app/main.py`, `app/routers/admin_auth.py` | Login blocked after N attempts |
| P0.3 | Move the service worker to a root scope so the manifest's `scope: "/"` is actually honoured. Bump the cache version. | `app/static/js/sw.js`, `app/templates/*/base.html` | Assert the registered scope is `/` |
| P0.4 | Set `COOKIE_SECURE=true` in compose when `APP_ENV=production`, and document that a TLS terminator is required. | `compose.yaml`, `.env.example` | — |
| P0.5 | Vendor Alpine.js into `/static/js/` and drop the CDN dependency. | `app/templates/public/base.html` | Page works with the network blocked |
| P0.6 | Add `pywebpush` to `requirements.txt` or remove the unused VAPID keys from `.env`. | `requirements.txt`, `.env` | A clean image can send push |
| P0.7 | Add basic application logging with a request id, and log failed logins and all admin mutations. | new `app/logging_config.py` | — |
| P0.8 | Make `/health` verify database connectivity, and add a separate `/ready` if needed. | `app/main.py` | Healthcheck fails when Postgres is down |
| P0.9 | Support `ADMIN_PASSWORD` rotation in `seed_admin`, or document that rotation requires a manual update. | `app/seed_admin.py`, `README.md` | — |
| P0.10 | Add `pytest` and `httpx` to a `requirements-dev.txt`, convert the highest-value scripts, and add a CI workflow that runs them. | new files, `.github/workflows/` | — |

### 14.2 Phase 1 — unblock the schema work

1. Answer the two blocking questions in §14.4.
2. Convert the ad-hoc test scripts to a real pytest suite **before** touching models, so
   migrations have a safety net.
3. Add the `branches` table and a nullable `branch_id` on the entities in §13.2. Do not
   add the non-null constraint in the same revision.
4. Add `pytest` fixtures that build the schema from migrations on a scratch database, and
   verify `alembic upgrade head` on an empty database as well as on a V1 copy.

### 14.3 Phase 2 — features, ordered by dependency

`push_subscriptions` and the notification service → order status history and audit →
customer tracking → loyalty (depending on the §13.7 scope) → CSV import → analytics →
admin theming polish.

Multi-branch spans all of these and should be finished before analytics or tracking, since
both need branch dimensions.

### 14.4 Open questions requiring a product decision

| # | Question | Blocks | Reference |
| --- | --- | --- | --- |
| Q1 | Shared catalog with per-branch availability and price overrides, or fully independent per-branch catalogs? | The entire multi-branch schema | §13.2 |
| Q2 | Abandoned-checkout policy: keep and expire, add a customer confirm step, or confirm server-side via the WhatsApp API? | Order status model, customer tracking, and what to do with existing `pending` rows | §13.3 |
| Q3 | Is "loyalty" points-based, or coupon segmentation of existing customers? | Three new tables versus none | §13.7 |
| Q4 | CSV import duplicate policy: update, skip, or fail? | Import implementation | §13.6 |
| Q5 | Do cancelled orders count toward revenue, order counts, and exported spend? Today the detail page says no and the dashboard is unstated. | Dashboard, export, tracking | §13.6, §13.11 |
| Q6 | Is push opt-in only, and is there a marketing opt-in distinct from transactional? Legal exposure differs substantially. | Push consent flow | §13.5 |
| Q7 | Should customers be blocked from ordering outside working hours, or only warned? | Hours enforcement | §13.4 |
| Q8 | What is the restaurant's timezone, and how should it be stored — per branch or global? | Scheduled-time display, which is currently server-local | §13.4, D10 |

### 14.5 Risk register

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Multi-branch migration corrupts or mis-scopes live data | Medium | Critical | Additive-only revisions, backfill to a default branch, non-null constraint in a later revision, dry-run on a V1 copy first |
| No test safety net before schema work | High if unaddressed | Critical | Phase 0.10 and Phase 1.2 must complete first |
| Migrations race on scale-out (`Dockerfile:21`) | Medium | High | Move migrations to a one-shot job or gate them on a Postgres advisory lock |
| Rate limiting silently disabled behind a proxy (S3) | High | High | Enable proxy headers and forward allow-list, or move to a shared store |
| Abandoned orders pollute the order book and any future analytics | High | Medium | Q2, plus a one-time cleanup of existing `pending` rows |
| Uploads directory grows without bound, no cleanup | Medium | Low | Delete an image when its owning row is deleted |
| Tailwind cannot be rebuilt, blocking UI features | Medium | Medium | Q1-style decision: add `package.json` and a build step, or commit to CSS custom properties only |
| Contacting the WhatsApp Business API without legal preparation | Medium | High | Q2 option (c) must include a provider and legal review before any code |
| Coupon `usage_count` drift after cancellations | Certain today | Low | Decrement on cancellation, or document the behaviour |

### 14.6 What was deliberately not recommended

Per the agreed constraints, this audit does **not** propose: microservices, Kubernetes,
CQRS or event sourcing, a full DDD layering, a repository pattern refactor, Kafka or
RabbitMQ, Redis, multi-tenancy, or a rewrite of any module. Every recommendation above
is achievable inside the current monolith with additive schema changes and contained code
fixes.

---

## Appendix A — Verification method

Every finding in this document was established by reading the files listed below, not by
executing the application. Line numbers correspond to commit `df45f4a`.

**Read in full:** `app/main.py`, `app/config.py`, `app/database.py`, `app/models.py`,
`app/schemas.py`, `app/ordering.py`, `app/utils.py`, `app/security.py`, `app/deps.py`,
`app/seed_admin.py`, `app/routers/public.py`, `app/routers/api.py`,
`app/routers/admin_auth.py`, `app/routers/admin.py`, `app/static/js/sw.js`,
`app/static/js/cart.js`, `Dockerfile`, `compose.yaml`, `render.yaml`, `.env.example`,
`.gitignore`, `alembic/versions/3a2b1c0d9e8f_coupons_first_order_only.py`,
`alembic/versions/4d5e6f7a8b9c_normalize_customer_phone.py`.

**Inspected selectively:** `app/templates/public/base.html`,
`app/templates/admin/base.html`, `app/templates/public/components/checkout_form.html`,
`app/templates/public/partials/product_card.html`,
`app/templates/public/components/featured_slider.html`, all 12 `test_*.py` script
headers, the full tracked file list, and all 15 migration revision/down_revision pairs.

**Confirmed absent by search:** any reference to `vapid`, `pywebpush`, `PushManager`, or
`Notification`; any `logging` import; any `package.json`; any CI configuration; any
`pytest` or `httpx` entry in `requirements.txt`; any reference to roles or permissions
beyond `Admin.is_active`.

**Repository state:** `git status` clean before and after writing this document, except
for this file. No dependency was installed, no migration was run, and the database was
not touched.
