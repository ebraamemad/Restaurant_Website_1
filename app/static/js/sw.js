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

  // 2) الأصول الثابتة: cache-first. أسرع، وبتخدم من غير شبكة.
  const isStaticAsset =
    request.destination === 'style' ||
    request.destination === 'script' ||
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
