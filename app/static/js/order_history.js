/**
 * سجل طلبات العميل — `/orders/history`.
 *
 * الصفحة نفسها فاضية من السيرفر: مفيش طلبات بترجع من الباك-إند هنا.
 * الجافاسكريبت بيقرا التوكنات من `localStorage` وبيجيب كل طلب لوحده
 * من `/api/orders/track` بـtoken بتاعه.
 *
 * ليه كده: V2 مفيهاش حسابات. أي حل تاني (سيرفر بيسمع على
 * normalized phone) هيعني إن أي حد يعرف رقمك يقدر يشوف طلباتك.
 * الـtoken هو الهوية الوحيدة اللي مينفعش حد يفهمها.
 */

(function () {
  const listEl = document.getElementById('history-list');
  const emptyEl = document.getElementById('history-empty');
  const loadingEl = document.getElementById('history-loading');
  if (!listEl) return;

  const TERMINAL_STATUSES = new Set(['completed', 'delivered', 'cancelled']);

  function setLoading(isLoading) {
    if (loadingEl) loadingEl.classList.toggle('hidden', !isLoading);
  }

  function formatMoney(value, currency) {
    const amount = Number(value || 0);
    return `${amount.toFixed(2)} ${currency || ''}`.trim();
  }

  function formatDate(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('ar-EG', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(date);
  }

  // اسم{cases واحد لكل عنصر (من 3 بحد أقصى). السطر بيبقى قصير، والقائمة
  // كلها بتفضل مقروءة من غير ما نطلع كل أصناف كل طلب.
  function itemsPreview(items) {
    if (!Array.isArray(items)) return [];
    return items.slice(0, 3).map((item) => `${item.quantity} × ${item.name}`);
  }

  /** حالة الدفع + الرسوم المعلّقة في سطر واحد تحت الإجمالي. */
  function orderFooterNotes(order) {
    const notes = [];
    if (order.delivery_fee_pending) {
      notes.push({ text: 'رسوم التوصيل هتحدد من المطعم', className: 'text-amber-600 font-semibold' });
    }
    if (order.cancel_reason) {
      notes.push({ text: order.cancel_reason, className: 'text-red-600' });
    }
    return notes;
  }

  function renderOrderCard(order, token) {
    const card = document.createElement('article');
    card.className = 'rounded-2xl bg-white p-4 shadow-sm';

    const preview = itemsPreview(order.items);
    const notes = orderFooterNotes(order);

    const header = document.createElement('div');
    header.className = 'mb-2 flex items-start justify-between gap-3';

    const idBlock = document.createElement('div');
    const idLine = document.createElement('p');
    idLine.className = 'text-sm font-bold text-slate-800';
    idLine.textContent = `طلب #${order.order_id}`;
    const dateLine = document.createElement('p');
    dateLine.className = 'mt-0.5 text-xs text-slate-500';
    dateLine.textContent = formatDate(order.created_at);
    idBlock.appendChild(idLine);
    idBlock.appendChild(dateLine);

    const totalBlock = document.createElement('div');
    totalBlock.className = 'text-left';
    const totalLine = document.createElement('p');
    totalLine.className = 'text-sm font-bold text-slate-800';
    totalLine.textContent = formatMoney(order.total, order.currency);
    const statusLine = document.createElement('p');
    statusLine.className = 'mt-0.5 text-xs text-slate-500';
    statusLine.textContent = order.status_label || '';
    totalBlock.appendChild(totalLine);
    totalBlock.appendChild(statusLine);

    header.appendChild(idBlock);
    header.appendChild(totalBlock);
    card.appendChild(header);

    if (preview.length) {
      const previewEl = document.createElement('p');
      previewEl.className = 'mb-1 text-xs text-slate-600';
      previewEl.textContent = preview.join('، ');
      if (Array.isArray(order.items) && order.items.length > 3) {
        previewEl.textContent += ` (+${order.items.length - 3})`;
      }
      card.appendChild(previewEl);
    }

    notes.forEach((note) => {
      const noteEl = document.createElement('p');
      noteEl.className = `mb-1 text-xs ${note.className}`;
      noteEl.textContent = note.text;
      card.appendChild(noteEl);
    });

    // الطلبات النشطة بتتقدّم لوحدها: بنعمل polling خفيف عليها. الطلبات
    // النهائية مفيش داعي نلمسها أصلاً.
    if (!TERMINAL_STATUSES.has(order.status)) {
      startPollingCard(card, token, statusLine, totalBlock, order);
    }

    const actions = document.createElement('div');
    actions.className = 'mt-3 flex gap-2';

    const trackLink = document.createElement('a');
    trackLink.href = trackingUrlFor(token);
    trackLink.className = 'flex-1 rounded-lg px-3 py-2 text-center text-xs font-bold text-white';
    trackLink.style.background = 'var(--color-primary)';
    trackLink.textContent = 'تفاصيل الطلب';
    actions.appendChild(trackLink);

    const reorderBtn = document.createElement('button');
    reorderBtn.type = 'button';
    reorderBtn.className =
      'rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold text-slate-700';
    reorderBtn.textContent = 'إعادة الطلب';
    reorderBtn.addEventListener('click', () => reorder(token, reorderBtn));
    actions.appendChild(reorderBtn);

    card.appendChild(actions);
    return card;
  }

  /**
   * إعادة الطلب بتجيب السطور الصالحة من السيرفر وتحوّلها لسلة.
   *
   * الطلب بيبعت `token` بس — السطور بتتبني من `order_items` الطلب نفسه
   * على السيرفر. مش بنبعت `items` من هنا: كده الـtoken يبقى تصريح على
   * طلب واحد بعينه بدل ما يكون تصريح بقراءة أي طلب.
   *
   * ليش POST `/api/orders/reorder` مش قراءة من الطلب القديم مباشرة:
   * الأسعار والحالة بتتغير. منتج اتشال، خيار بقى غير متاح، سعر اتغير
   * — السيرفر بيرجّع السطور الصالحة بس وبيقولنا أي واحد اتخطى وليه.
   * ومفيش طلب بيتعمل أوتوماتيك: العميل بيراجع السلة وبيأكد بنفسه.
   */
  async function reorder(token, button) {
    button.disabled = true;
    button.textContent = 'جاري التحضير…';

    try {
      const response = await fetch('/api/orders/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ token: token }),
      });

      if (!response.ok) {
        const problem = await response.json().catch(() => null);
        const detail = problem && problem.detail ? problem.detail : 'مقدرناش نجهّز إعادة الطلب';
        window.alert(detail);
        return;
      }

      const data = await response.json();
      const added = await applyReorderToCart(data);
      if (added === 0) {
        window.alert('مفيش أصناف من الطلب القديم متاحة دلوقتي.');
        return;
      }

      const skippedCount = (data.skipped || []).length;
      if (skippedCount > 0) {
        const reasons = data.skipped
          .slice(0, 3)
          .map((entry) => entry.reason)
          .join('، ');
        window.alert(
          `حطينا ${added} صنف في السلة.\n` +
            `${skippedCount} صنف مش متاح دلوقتي اتخطّى: ${reasons}`
        );
      }
      window.location.href = '/#cart';
    } catch (e) {
      window.alert('مقدرناش نوصل للسيرفر. اتأكد من الإنترنت وحاول تاني.');
    } finally {
      button.disabled = false;
      button.textContent = 'إعادة الطلب';
    }
  }

  /**
   * السلة موجودة في `cart.js` تحت `window.restaurantCart` — نفس
   * namespace عشان كل حاجة السلة في ملف واحد.
   *
   * `order_history.html` بيحمّل `cart.js` قبل الملف ده، فالمفروض
   * الكائن يبقى موجود. الفحص باقي كشبكة أمان: لو أي وقت اتغيّر ترتيب
   * الـscripts أو الملف اتشال من الصفحة، نبلغ العميل بدل ما نعمل
   * تحويل ناقص ونوديه لصفحة فاضية.
   *
   * **العدد المرجوع هو اللي اتضاف فعلاً** من `mergeFromReorder`، مش
   * عدد السطور في الاستجابة: منتج ممكن يتشال من لوحة الأدمن بين
   * الطلب الأول ودلوقتي، وسطره بيتخطّى. عرض "ضافنا 3" والفعلي 2
   * أحسن من رقم كاذب.
   */
  async function applyReorderToCart(data) {
    const cart = window.restaurantCart;
    if (!cart || typeof cart.mergeFromReorder !== 'function') {
      window.alert('في مشكلة في تحميل السلة. جرّب تعمل تحديث للصفحة.');
      return 0;
    }
    return await cart.mergeFromReorder(data.lines || []);
  }

  /** polling خفيف للسطر النشط بس، وزي صفحة التتبّع.
   *
   * الـinterval بيتوقّف في حالتين: الطلب وصل حالة نهائية، أو الكارت
   * اتشال من الـDOM (العميل مسح السجل). من غير التوقّف الثاني ده،
   * كل كارت اتشال كان بيطلب `/api/orders/track` كل 20 ثانية لحد ما
   * الصفحة تتقفل.
   */
  function startPollingCard(card, token, statusLine, totalBlock, initial) {
    let timer = null;

    const stop = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const tick = async () => {
      if (!card.isConnected) {
        stop();
        return;
      }
      try {
        const response = await fetch(`/api/orders/track?token=${encodeURIComponent(token)}`, {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) return;
        const order = await response.json();
        if (!card.isConnected) {
          stop();
          return;
        }
        if (statusLine) statusLine.textContent = order.status_label || '';
        if (totalBlock && totalBlock.firstElementChild) {
          totalBlock.firstElementChild.textContent = formatMoney(order.total, order.currency);
        }
        if (TERMINAL_STATUSES.has(order.status)) {
          // الطلب وصل نهايته: نعيد بناء الكارت مرة واحدة والسقف بيقف.
          stop();
          card.replaceWith(renderOrderCard(order, token));
        }
      } catch (e) {
        // تجاهل: الشبكة هترجع، والمحاولة الجاية تجرب.
      }
    };

    timer = window.setInterval(tick, 20000);
  }

  async function load() {
    const entries = getStoredTokens();
    if (entries.length === 0) {
      setLoading(false);
      if (emptyEl) emptyEl.classList.remove('hidden');
      return;
    }

    const results = await Promise.all(
      entries.map(async (entry) => {
        try {
          const response = await fetch(
            `/api/orders/track?token=${encodeURIComponent(entry.token)}`,
            { headers: { Accept: 'application/json' } }
          );
          if (!response.ok) return null;
          return await response.json();
        } catch (e) {
          return null;
        }
      })
    );

    setLoading(false);
    const orders = results.filter(Boolean);
    if (orders.length === 0) {
      if (emptyEl) emptyEl.classList.remove('hidden');
      return;
    }

    // المطابقة بالـindex مش بالـorder_id: `results` بيحافظ على ترتيب
    // `entries` (Promise.all)، والـorder_id في السجل ممكن يبقى 0 لو
    // التوكن اتحفظ من كود ما يعرفش الـid. المطابقة بالـindex مضمونة
    // إن كل كارت ياخد التوكن اللي جاب بيه.
    results.forEach((order, index) => {
      if (!order) return;
      listEl.appendChild(renderOrderCard(order, entries[index].token));
    });
  }

  load();
})();
