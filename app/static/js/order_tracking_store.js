/**
 * تخزين توكنات التتبّع على جهاز العميل.
 *
 * الفكرة: عند إنشاء الطلب، السيرفر بيرجّع `tracking_token` **مرة واحدة**.
 * احنا بنحفظه هنا، وبنستخدمه بعدين يفتح صفحة التتبّع أو يبني السجل.
 *
 * ليه `localStorage` مش السيرفر:
 * - V2 مفيهاش حسابات عملاء. مفيش بريد ولا رقم ولا أي حاجة نرجّع بيها
 *   قائمة طلبات لحد.
 * - رقم التليفون **مش** هوية: موجود في رسالة واتساب وفي بحثات، وكمان
 *   ناس كتير بيستخدموا نفس الرقم.
 *
 * ليه `localStorage` مش `sessionStorage`: العميل بيقفل اللاب بين
 * الطلب والاستلام، وعايز يرجع يلاقي طلبه. البيانات دي مش حساسة
 * (token لو leaked = قراءة طلب واحد على الأكثر، والعميل هو اللي
 * leakedه من جهازه)، والبديل الجيد في المرحلة دي هو **مسح يدوي**
 * — الزر في صفحة السجل.
 */

const TRACKING_STORE_KEY = 'restaurant_order_tokens';
const TRACKING_STORE_LIMIT = 50;

/** هل `localStorage` متاح؟ في وضع التصفح الخاص بيرمي أحياناً. */
function storageAvailable() {
  try {
    const probe = '__t__';
    window.localStorage.setItem(probe, '1');
    window.localStorage.removeItem(probe);
    return true;
  } catch (e) {
    return false;
  }
}

const HAS_STORAGE = storageAvailable();

/**
 * يقرأ قائمة التوكنات.
 * بترجع `[]` لو مفيش تخزين أو البيانات تالفة — الكود قدام
 * مستخدم عادي، مينفعش يطيّح لو localStorage اتقفل.
 */
function getStoredTokens() {
  if (!HAS_STORAGE) return [];
  try {
    const raw = window.localStorage.getItem(TRACKING_STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // الشكل المتوقع: `[{ token, order_id, created_at }]`
    return parsed
      .filter((entry) => entry && typeof entry.token === 'string' && entry.token.length > 0)
      .map((entry) => ({
        token: entry.token,
        order_id: Number(entry.order_id) || 0,
        created_at: entry.created_at || null,
      }));
  } catch (e) {
    return [];
  }
}

function setStoredTokens(entries) {
  if (!HAS_STORAGE) return;
  try {
    window.localStorage.setItem(TRACKING_STORE_KEY, JSON.stringify(entries));
  } catch (e) {
    // الحصة ممتلئة أو محجوبة. ده مش بيمنع العميل من متابعة طلبه الحالي
    // — التوكن لسه في الذاكرة وفي رابط الصفحة.
  }
}

/**
 * يحفظ توكن طلب جديد. لو التوكن موجود أصلاً (double submit مثلا) بنحدّث
 * السجل بدل ما نكرّره.
 *
 * @param {string} token
 * @param {number} orderId
 * @returns {Array} القائمة بعد التحديث
 */
function rememberOrderToken(token, orderId) {
  if (!token) return getStoredTokens();
  const entries = getStoredTokens().filter((entry) => entry.token !== token);
  entries.unshift({
    token: token,
    order_id: Number(orderId) || 0,
    created_at: new Date().toISOString(),
  });
  // السقف موجود عشان `localStorage` ما تبقىش مخزن توكنات أبدي.
  // الطلبات الأقدم من الـ50 مش هتظهر في السجل، بس تتبّعها لسه شغال
  // لو حد عنده اللينك.
  const trimmed = entries.slice(0, TRACKING_STORE_LIMIT);
  setStoredTokens(trimmed);
  return trimmed;
}

/** يمسح كل التوكنات من الجهاز — زر "نسيت الأجهزة". */
function clearStoredTokens() {
  if (!HAS_STORAGE) return;
  window.localStorage.removeItem(TRACKING_STORE_KEY);
}

/** رابط التتبّع الكامل لنفس الـorigin. */
function trackingUrlFor(token) {
  return `${window.location.origin}/track?token=${encodeURIComponent(token)}`;
}
