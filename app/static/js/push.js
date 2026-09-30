/**
 * تسجيل اشتراك Web Push من المتصفح.
 *
 * بيتحمّل في صفحات العميل وصفحة الأدمن. مفيهوش framework — Alpine
 * موجود في المشروع فعلاً، بس منطق الـPush محتاج Promise chain
 * طويل (permission → registration → subscribe → fetch)، وربطه بـ
 * Alpine بيعقّده من غير فايدة.
 *
 * ## المبدأ: المستخدم بيتسأل مرة واحدة
 *
 * `Notification.requestPermission()` بترجع `denied` بشكل نهائي لو
 * المستخدم رفض. المستعرض نفسه مش هيسمع بعدها، ولو ضغطنا الزر تاني
 * بنعرض نفس النتيجة ونعنّفه على إنه رفض — وده أسوأ من إنه يفضل
 * شايف الزر ويضغط.
 *
 * فالقاعدة: لو `denied`، الزرار **يتشال** والرسالة بتتبدل بـ"الإشعارات
 * مقفولة من المتصفح". الإعادة ممكنة بس من إعدادات المتصفح،
 * وقولنا له ده بصراحة بدل ما نكابر.
 */

const PUSH_CHOICE_KEY = 'restaurant_push_choice';

/** حالة الإذن كما يعرفها المتصفح: 'default' | 'granted' | 'denied'. */
function pushPermission() {
  if (!('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission;
}

/** هل الـAPI موجود أصلاً؟ Safari iOS 16.4+ بس. */
function pushSupported() {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

/** هل الصفحة على HTTP عادي؟ (المتصفح مش هيسمح بـService Worker) */
function isInsecureContext() {
  return window.isSecureContext === false;
}

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      detail = body && body.detail ? body.detail : '';
    } catch (error) {
      // الرد مش JSON. الـstatus كافي.
    }
    const error = new Error(detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function getCsrfToken() {
  const input = document.querySelector('input[name="csrf_token"]');
  return input ? input.value : null;
}

/** هل المستخدم قبل الإشعارات من قبل على هذا الجهاز؟ */
function pushChoiceMade() {
  try {
    return window.localStorage.getItem(PUSH_CHOICE_KEY) !== null;
  } catch (error) {
    return false;
  }
}
function rememberPushChoice(value) {
  try {
    window.localStorage.setItem(PUSH_CHOICE_KEY, value);
  } catch (error) {
    // private mode. مش مهم — الـpermission نفسه في المتصفح.
  }
}

function clearPushChoice() {
  try {
    window.localStorage.removeItem(PUSH_CHOICE_KEY);
  } catch (error) {
    // نفس الكلام.
  }
}

/**
 * حالة الإشعارات للعرض في الواجهة.
 *
 * بيرجّع object واحد عشان الـ`caller` ما يعملش أكتر من
 * واحد. الحقول:
 * - `supported`: المتصفح نفسه فيه Push API
 * - `enabled`: السيرفر عنده VAPID مظبوط
 * - `permission`: 'default' | 'granted' | 'denied' | 'unsupported'
 * - `subscribed`: الجهاز ده مشترك فعلاً (من رد السيرفر)
 * - `publicKey`: VAPID public key
 * - `reason`: ليه مش بنقدر نشترك (نص جاهز للعرض)
 */
async function getPushState(options) {
  const settings = options || {};
  const state = {
    supported: false,
    enabled: false,
    permission: pushPermission(),
    subscribed: false,
    publicKey: null,
    reason: '',
  };

  if (!pushSupported()) {
    state.reason = 'المتصفح ده مش بيدعم الإشعارات.';
    return state;
  }
  if (isInsecureContext()) {
    // http عادي مش https. المتصفح مش هيسمح بـService Worker أصلاً،
    // فلازم نقول ده بدل ما المستخدم يضغط ومحدش يفهم ليه.
    state.reason = 'الإشعارات محتاجة اتصال آمن (HTTPS).';
    return state;
  }

  const base = settings.admin ? '/admin/api/push' : '/api/push';
  let status;
  try {
    status = await getJson(base + '/status' + (settings.query || ''));
  } catch (error) {
    state.reason = 'مقدرنا نتحقق من حالة الإشعارات.';
    return state;
  }

  state.enabled = Boolean(status.server_enabled);
  state.subscribed = Boolean(status.subscribed);
  state.publicKey = status.public_key;

  if (!state.enabled) {
    state.reason = 'الإشعارات غير مفعّلة على السيرفر.';
  } else if (state.permission === 'denied') {
    state.reason = 'الإشعارات مقفولة من المتصفح — فعّلها من إعدادات المتصفح.';
  }

  return state;
}

/**
 * اشترك في الإشعارات.
 *
 * الترتيب مقصود:
 * 1. `requestPermission` — لازم الأول، من غيرها `subscribe` بيرمي.
 * 2. `getRegistration` — الـSW لازم يكون مسجّل فعلاً.
 * 3. `registration.pushManager.getSubscription()` — لو في اشتراك
 *    موجود على نفس الـendpoint، **بنرجعوه** من غير ما نعمل subscribe
 *    جديد. subscribe جديد = endpoint جديد = سجل قديم يتيم.
 * 4. `POST` التسجيل على السيرفر — السيرفر هو اللي بيكتب الـ`admin_id`
 *    أو `customer_id`، والمتصفح ما يعرفش مين العميل.
 */
async function enablePush(options) {
  const settings = options || {};
  const base = settings.admin ? '/admin/api/push' : '/api/push';

  if (!pushSupported()) {
    throw new Error('المتصفح ده مش بيدعم الإشعارات.');
  }
  if (isInsecureContext()) {
    throw new Error('الإشعارات محتاجة اتصال آمن (HTTPS).');
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    // 'denied' نهائي من المتصفح. بنسجّم عشان الزرار يتشال تاني.
    rememberPushChoice(permission);
    throw new Error(
      permission === 'denied'
        ? 'الإشعارات اترفضت — فعّلها من إعدادات المتصفح.'
        : 'الإشعارات ما اتفعّلتش.'
    );
  }

  const config = await getJson(base + '/public-key');
  if (!config.enabled || !config.public_key) {
    throw new Error('الإشعارات غير مفعّلة على السيرفر.');
  }

  const registration = await navigator.serviceWorker.ready;

  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(config.public_key),
    });
  }

  // `toJSON()` بيرجّع `endpoint` + `expirationTime` + `keys`.
  // السيرفر بيقرا `p256dh` و`auth` بس، وبيتجاهل `expirationTime`
  // لأنه بيختلف بين المتصفحات ومحدش بيسأل عليه.
  const payload = subscription.toJSON();
  const body = {
    endpoint: payload.endpoint,
    p256dh: payload.keys ? payload.keys.p256dh : '',
    auth: payload.keys ? payload.keys.auth : '',
    token: settings.token || null,
    device: settings.device || null,
  };

  const headers = { 'Content-Type': 'application/json' };
  if (settings.admin) {
    // `/admin` محمي بـCSRF على مستوى الراوتر. من غير الـheader ده
    // السيرفر بيرفض بـ403.
    const csrf = getCsrfToken();
    if (!csrf) {
      throw new Error('مش لاقي CSRF token — الصفحة مش كاملة؟');
    }
    headers['X-CSRF-Token'] = csrf;
  }

  await getJson(base + '/subscribe', {
    method: 'POST',
    headers: headers,
    body: JSON.stringify(body),
  });

  rememberPushChoice('granted');
  return { subscribed: true };
}

/** إلغاء الاشتراك من السيرفر. */
async function disablePush(options) {
  const settings = options || {};
  const base = settings.admin ? '/admin/api/push' : '/api/push';

  if (!pushSupported()) {
    return { unsubscribed: false };
  }

  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    clearPushChoice();
    return { unsubscribed: false };
  }

  const payload = subscription.toJSON();
  const headers = { 'Content-Type': 'application/json' };
  if (settings.admin) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers['X-CSRF-Token'] = csrf;
    }
  }

  try {
    await getJson(base + '/unsubscribe', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ endpoint: payload.endpoint, token: settings.token || null }),
    });
  } finally {
    // **بنشيل الاشتراك من المتصفح حتى لو السيرفر رفض.** العكس هيسيب
    // اشتراك ميت على الجهاز: السيرفر عارفه بس المتصفح مش هيبعت
    // بيه، وفي كل مرة هيتنضف من جدولنا.
    try {
      await subscription.unsubscribe();
    } catch (error) {
      console.warn('[push] local unsubscribe failed:', error);
    }
    clearPushChoice();
  }

  return { unsubscribed: true };
}

/** يربط الـSW بالصفحة. مرة واحدة لكل تحميل صفحة. */
async function registerPushWorker() {
  if (!('serviceWorker' in navigator)) {
    return null;
  }
  try {
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  } catch (error) {
    console.warn('[push] service worker registration failed:', error);
    return null;
  }
}
