/**
 * تحديث حالة الطلب في صفحة التتبّع.
 *
 * استطلاع (polling) مش WebSocket، وده اختيار مقصود:
 * - حالة الطلب ماتغيرتش أكتر من كل دقيقة في شغل مطعم عادي. استطلاع
 *   كل 20 ثانية أكتر من كافٍ ومكلّفه أقل بكتير من connection مفتوح.
 * - مفيش broker ولا Redis في المرحلة دي (مستبعدين حسب النطاق)،
 *   فـWebSocket هيعني process in-memory من غير فايدة لما يعمل
 *   restart.
 *
 * السلوك: بن.poll مرة واحدة عند التحميل، وبعدها كل 20 ثانية لحد ما
 * الطلب يوصل حالة نهائية. بعدها بنوقف — مفيش فايدة من الاستطلاع على
 * طلب مسلّم.
 */

(function () {
  const POLL_INTERVAL_MS = 20000;
  const TERMINAL_STATUSES = new Set(['completed', 'delivered', 'cancelled']);

  const root = document.getElementById('live-status');
  if (!root) return;

  const token = root.getAttribute('data-token');
  if (!token) return;

  const statusText = document.getElementById('live-status-text');
  let pollTimer = null;
  let stopped = false;

  function stopPolling() {
    stopped = true;
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function refresh() {
    if (stopped) return;
    try {
      const response = await fetch(`/api/orders/track?token=${encodeURIComponent(token)}`, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;

      const order = await response.json();
      if (statusText && order.status_label) {
        statusText.textContent = order.status_label;
      }

      // حالة نهائية: نوقف. لو رجع `cancelled` كمان بيقف.
      if (TERMINAL_STATUSES.has(order.status)) {
        stopPolling();
        // الصفحة اتعمل render من حالة أقدم، فالأسهل reload واحد ياخد
        // كل حاجة (الأصناف، الرسوم المعلّقة، سبب الإلغاء) من السيرفر.
        // تكلفة request واحد، والصفحة كانت هتتحدث لوحدها برضو.
        window.setTimeout(() => window.location.reload(), 1200);
      }
    } catch (e) {
      // شبكة ضعيفة أو اوفلاين. بنسيب المحاولة الجاية تجرب تاني —
      // مفيش رسالة error للمستخدم لأن الحالة اللي معروضة لسه صح.
    }
  }

  pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS);

  // نوقف الـpolling لما التاب يقفل: tab مخفي مينفعش يفضل يضرب السيرفر
  // كل 20 ثانية والمطعم مقفول أصلاً.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    } else if (!stopped && pollTimer === null) {
      refresh();
      pollTimer = window.setInterval(refresh, POLL_INTERVAL_MS);
    }
  });
})();
