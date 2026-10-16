/**
 * Service worker للتطبيق.
 *
 * بيتserve من /sw.js (مش /static/js/sw.js) — عشان الـscope يبقى "/" ويقدر
 * يشتغل على صفحات الـHTML، مش على ملفات الـstatic بس.
 */

const CACHE_NAME = 'restaurant-cache-v3';

const STATIC_ASSETS = [
  '/static/css/tailwind.css',
  '/static/css/app.css',
  '/static/js/cart.js',
  '/static/js/alpine.min.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/',
];

/** المسار اللي بنعتبره الـoffline shell. */
const OFFLINE_SHELL = '/';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        // addAll بتفشل الـinstall كله لو ملف واحد 404. بنضيف كل واحد لوحده
        // عشان مفيش asset ناقص يبوظ التثبيت كله.
        Promise.all(
          STATIC_ASSETS.map((asset) =>
            cache.add(asset).catch((error) => {
              console.warn('[sw] precache skipped:', asset, error);
            })
          )
        )
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('restaurant-cache-') && key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // نفس الأصل بس — مفيش تخزين لـthird-party أو requests كاملة.
  if (url.origin !== self.location.origin) {
    return;
  }

  // طلبات GET بس: POST /api/orders مثلاً ما بتتخزنش أبداً.
  if (request.method !== 'GET') {
    return;
  }

  // 1) تنقّل بين الصفحات: network-first، والـfallback هو الصفحة المخزّنة.
  //    ده بيضمن إن المستخدم بيشوف آخر نسخة منPrices والمخزون أول ما
  //    الشبكة ترجع، بس بيفضل يقدر يتصفح اوفلاين.
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(OFFLINE_SHELL, copy));
          }
          return response;
        })
        .catch(() =>
          caches.match(OFFLINE_SHELL).then((cached) => cached || Response.error())
        )
    );
    return;
  }

  // 2) كود الواجهة (CSS/JS): network-first مع رجوع للـcache.
  //
  //    ده أهم واحد في الملف. لو رجعنا cache-first هنا، أي تعديل على
  //    `app.css`/`tailwind.css` أو أي template هيفضل مخفي ورا الـcache
  //    نقلبة (وحتى لو غيّرنا `CACHE_NAME`، الأصول بتتخزّن لوحدها بعد
  //    الـprecache). النتيجة: لوحة شكلها باظ من غير أي خطأ في الكونسول.
  //
  //    network-first بيخلي آخر نسخة ديماً هي المعروضة، والـcache مجرد
  //    fallback اوفلاين. الكود صغير، فالتأخير ضئيل.
  const isCodeAsset =
    request.destination === 'style' ||
    request.destination === 'script' ||
    url.pathname.startsWith('/static/css/') ||
    url.pathname.startsWith('/static/js/');

  if (isCodeAsset) {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.ok && networkResponse.type === 'basic') {
            const copy = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return networkResponse;
        })
        .catch(() =>
          caches
            .match(request, { ignoreSearch: true })
            .then((cached) => cached || Response.error())
        )
    );
    return;
  }

  // 3) باقي الأصول الثابتة (صور/خطوط/أيقونات): cache-first. ثابتة
  //    فعلاً، فالأولوية للأداء وتوفير الشبكة.
  const isStaticAsset =
    request.destination === 'image' ||
    request.destination === 'font' ||
    url.pathname.startsWith('/static/');

  if (!isStaticAsset) {
    return;
  }

  event.respondWith(
    caches.match(request, { ignoreSearch: true }).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }

      return fetch(request).then((networkResponse) => {
        if (networkResponse && networkResponse.ok && networkResponse.type === 'basic') {
          const copy = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return networkResponse;
      });
    })
  );
});

// ============================================================
// الإشعارات — Phase 3
// ============================================================
//
// ## ليه الـpayload بيتعامل معاه كـ"untrusted"
//
// السيرفر هو اللي بيبعت، بس مينفعش نعامل `event.data.json()` كـobject
// مضمون. المزوّد (FCM/Mozilla) بيعدّيها، لكن أي bug في السيرفر أو
// اشتراك على endpoint حد تاني معناه `data.url` ممكن يبقى أي حاجة.
//
// **القاعدة: `url` لازم يكون path داخلي يبدأ بـ`/`.** لو مش كذلك
// بنتجاه ونروّح العميل لصفحة السجل. `javascript:` و`data:` في
// notificationclick = تنفيذ كود. مش بنفتحهم أبداً.
//
// ## ليه `showNotification` بـ`self.registration` مش متغير
//
// `self.registration` متاح في كل الـSW scope. لو `showNotification`
// رمت (permission اتسحب)، بنبلع الخطأ: المتصفح بيلغّي الإشعارات لو
// رجعنا Tried نعملها تاني.

const PUSH_FALLBACK_URL = '/orders/history';
const MAX_NOTIFICATION_TITLE = 120;
const MAX_NOTIFICATION_BODY = 200;

/** Path داخلي آمن، أو null لو الـpayload مفيهوش واحد صالح. */
function safeInternalUrl(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 500) {
    return null;
  }
  // المطلق ممنوع: `//evil.com` و`https://evil.com` الاتنين برّوا الموقع.
  // `javascript:` و`data:` مش paths أصلاً.
  if (!value.startsWith('/') || value.startsWith('//')) {
    return null;
  }
  return value;
}

function clampText(value, max) {
  if (typeof value !== 'string') {
    return '';
  }
  // السطر الجديد بيخلي العنوان يبدو مكسور في بعض الأجهزة. بنتحوله
  // لمسافة بدل ما نخلي الـSW يفشل.
  return value.replace(/\s+/g, ' ').trim().slice(0, max);
}

function readPayload(event) {
  if (!event.data) {
    return null;
  }
  try {
    const parsed = event.data.json();
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (error) {
    return null;
  }
}

/**
 * هل الإشعار ده لسه مهم؟
 *
 * ترقيم تصاعدي: لو وصل إشعار بنفس الرقم تاني، ده تكرار من المزوّد
 * (أو إعادة إرسال بعد إعادة تسجيل الـSW) — بنسقطه. ده بيمنع
 * "طلب جديد" مكرر 3 مرات لو الشبكة رجعت بـretry.
 */
function notificationTag(payload) {
  const orderId = payload && payload.order_id;
  const status = payload && payload.status;
  if (orderId && status) {
    return `order-${orderId}-${status}`;
  }
  if (orderId) {
    return `order-${orderId}`;
  }
  return null;
}

self.addEventListener('push', (event) => {
  // `push` من غير `data` = "جرّب الاتصال". المتصفح بيبعت الرسالة دي
  // لما الـpermission يتأكّد في أول اشتراك. بنعمل notification بنص
  // ثابت بدل ما نتجاهلها، عشان المستخدم يشوف إن الإعداد اشتغل.
  if (!event.data) {
    event.waitUntil(
      self.registration.showNotification('إشعارات مفعّلة', {
        body: 'هنوصللك تحديثات طلبك هنا.',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        tag: 'push-test',
        data: { url: PUSH_FALLBACK_URL },
      })
    );
    return;
  }

  const payload = readPayload(event);
  if (!payload) {
    // Payload مش JSON: مفيش حاجة نعرف نعرضها. بنتجاه ونصمت — أحسن
    // من `showNotification` بنص فاضي يبان زي bug للمستخدم.
    return;
  }

  const url = safeInternalUrl(payload.url) || PUSH_FALLBACK_URL;
  const title = clampText(payload.title, MAX_NOTIFICATION_TITLE) || 'طلبك اتحدّث';
  const tag = notificationTag(payload);

  const options = {
    body: clampText(payload.body, MAX_NOTIFICATION_BODY),
    // `tag` بيخلي المتصفح **يستبدل** الإشعار القديم بنفس الـtag بدل ما
    // يكوّم. طلب واحد بيتحدّث 5 مرات = إشعار واحد بيتحدّث، مش 5.
    ...(tag ? { tag: tag } : {}),
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    // `renotify: true` بيخلي الصوت/الاهتزاز يتصلوا من غير ما نخلق
    // إشعار مكرر — بس بيتطلب `tag` موجود. من غير tag بصمت.
    ...(tag ? { renotify: true } : {}),
    requireInteraction: false,
    data: { url: url, order_id: payload.order_id || null },
  };

  event.waitUntil(
    self.registration
      .showNotification(title, options)
      // إشعار رفضه المتصفح (permission اتسحب، أو quota) — مفيش
      // إعادة محاولة تفيد. بنسجّم وبس.
      .catch((error) => console.warn('[sw] showNotification failed:', error))
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const target = safeInternalUrl(event.notification.data && event.notification.data.url)
    || PUSH_FALLBACK_URL;

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        // لو فيه نافذة مفتوحة على نفس الأصل: بنجيبها قدام ومفيش
        // تبويب جديد. ده اللي المستخدم بيتوقعه لما يدوس على إشعار.
        //
        // **مش بنعمل `client.focus()` على أي نافذة:** لو المستخدم عنده
        // 5 تبويبات، ده هيجيبه التبويب الأول حتى لو كان صفحة منيو.
        // بنفلتر على نفس المسار بس.
        for (const client of clientList) {
          if (new URL(client.url).pathname === new URL(target, self.location.origin).pathname) {
            return client.focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(target);
        }
        return undefined;
      })
      .catch((error) => console.warn('[sw] notificationclick failed:', error))
  );
});
