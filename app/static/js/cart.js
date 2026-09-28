// ===== السلة في localStorage — مصدر واحد للحقيقة =====
const CART_KEY = "cart";

const Cart = {
  read() {
    try {
      return JSON.parse(localStorage.getItem(CART_KEY) || "[]");
    } catch {
      return [];
    }
  },
  write(items) {
    localStorage.setItem(CART_KEY, JSON.stringify(items));
  },
  // نفس المنتج + نفس الخيارات + نفس الإضافات + نفس الملاحظة = نفس السطر.
  // اختلاف أي حاجة فيهم = سطر منفصل.
  lineId(productId, optionIds = [], addonIds = [], note = "") {
    const opt = [...optionIds].sort((a, b) => a - b).join(".");
    const add = [...addonIds].sort((a, b) => a - b).join(".");
    return [productId, opt, add, note || ""].join("-");
  },
  add(item) {
    const items = this.read();
    const optionIds = (item.optionIds || []).map(Number);
    const addonIds = (item.addonIds || []).map(Number);
    const id = this.lineId(item.id, optionIds, addonIds, item.item_note);
    const found = items.find((i) => i.lineId === id);
    if (found) {
      found.quantity = Math.min(999, found.quantity + item.quantity);
    } else {
      items.push({
        lineId: id,
        id: item.id,
        name: item.name,
        price: Number(item.base_price ?? item.price ?? 0),
        quantity: item.quantity,
        options: item.options || [],
        optionIds,
        addons: item.addons || [],
        addonIds,
        item_note: item.item_note || "",
      });
    }
    this.write(items);
    return items;
  },
  clear() {
    localStorage.removeItem(CART_KEY);
  },
  /**
   * دمج سطور إعادة الطلب في السلة الحالية.
   *
   * السطور الجاية من `/api/orders/reorder` و**متحقّقة منها على السيرفر**
   * (product_id + option_ids + addon_ids بس، من غير أسماء ولا أسعار).
   * عشان كده بنجيب الأسماء والأسعار من المنتج في `/api/products/by-id/{id}`
   * — السلة بتخزّن الاسم والسعر محلياً للعرض، والـBackend بيعيد
   * التحقق من كله وقت الإنشاء على أي حال.
   *
   * **الأسماء بتتجلّب من نفس الاستجابة**: الخيارات جاية في
   * `option_groups[].options` والإضافات في `addons`. من غيرها السطر
   * هيتحفظ ب IDs بس، والسلة بتعرض الأسماء — فالسطر هيبان فاضي
   * والعميل مش هيعرف اشترى إيه.
   */
  async mergeFromReorder(lines) {
    let merged = 0;
    for (const line of lines || []) {
      try {
        const product = await this.fetchProductForCart(line.product_id);
        if (!product) continue;
        const optionIds = (line.option_ids || []).map(Number);
        const addonIds = (line.addon_ids || []).map(Number);
        this.add({
          id: product.id,
          name: product.name,
          base_price: product.base_price,
          quantity: line.quantity,
          options: this.namedOptions(product, optionIds),
          optionIds,
          addons: this.namedAddons(product, addonIds),
          addonIds,
          item_note: line.item_note || "",
        });
        merged += 1;
      } catch {
        // منتج مش موجود — السيرفر كان المفروض يستبعده بـ`skipped`.
      }
    }
    return merged;
  },
  /**
   * أسماء الخيارات المختارة، بنفس الشكل اللي `Cart.add` بيخزّنه من
   * صفحة المنتج: `{id, name, price_adjustment}`.
   *
   * لو الـid مش موجود في المنتج (اتشال من لوحة الأدمن بعد الطلب
   * الأول) بنتخطّاه: العرض يفضّل سطر ناقص على سطر باسم غلط. التحقق
   * الحقيقي بيحصل وقت إنشاء الطلب على السيرفر.
   */
  namedOptions(product, optionIds) {
    const chosen = (product.option_groups || []).flatMap((g) => g.options || []);
    return optionIds
      .map((id) => chosen.find((o) => o.id === id))
      .filter(Boolean)
      .map((o) => ({
        id: o.id,
        name: o.name,
        price_adjustment: Number(o.price_adjustment || 0),
      }));
  },
  /** نفس الفكرة للإضافات: `{id, name, price}`. */
  namedAddons(product, addonIds) {
    const available = product.addons || [];
    return addonIds
      .map((id) => available.find((a) => a.id === id))
      .filter(Boolean)
      .map((a) => ({
        id: a.id,
        name: a.name,
        price: Number(a.price || 0),
      }));
  },
  async fetchProductForCart(productId) {
    const resp = await fetch(`/api/products/by-id/${productId}`);
    if (!resp.ok) return null;
    return await resp.json();
  },
};

// إعادة الطلب بتنادي `Cart.mergeFromReorder` من `order_history.js`،
// وهو ملف تاني مش شايف `Cart`. التصدير ده عشان كل حاجة السلة في
// namespace واحد.
window.restaurantCart = Cart;

// ===== العناوين المحفوظة على الجهاز =====
// المكوّن ده لوحده (مش جوه `storeCart`) عشان صفحة التتبّع كمان
// محتاجاه لو العميل حب يضيف عنوان من هناك.
//
// **ليه localStorage مش جدول في الداتابيز؟**
// 1. V2 مفيهاش حسابات عملاء، فالجدول كان هيتقفل برقم موبايل — ورقم
//    الموبايل مش هوية: ناس كتير بتشاركه، وفي طلبات حد تاني بيظهر.
// 2. كل الـprivacy اللي الـtoken بيديه للعميل (لو الداتابيز اتسرّبت
//    مفيش روابط تتبّع) هيتلخبط لو العنوان متخزّن جنبه.
// 3. مفيش فايدة إن العميل ينسخ العنوان من جهازه عشان يطلب أسرع —
//    ده use case حقيقي ومنتشر.
//
// التكلفة: العنوان مش بينتقل لجهاز تاني. ده مقصود ومكتوب في الصفحة
// نفسها للعميل ("محفوظة على جهازك بس").
const ADDRESS_KEY = "saved_addresses";
const ADDRESS_LIMIT = 10;

const AddressStore = {
  read() {
    try {
      const raw = localStorage.getItem(ADDRESS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((a) => a && typeof a.text === "string" && a.text.trim().length >= 5)
        .map((a) => ({
          id: String(a.id || ""),
          label: String(a.label || "المنزل"),
          text: String(a.text).trim().slice(0, 350),
        }));
    } catch {
      return [];
    }
  },
  write(entries) {
    try {
      localStorage.setItem(ADDRESS_KEY, JSON.stringify(entries.slice(0, ADDRESS_LIMIT)));
    } catch {
      // الحصة ممتلئة أو محجوبة — مش بيمنع إتمام الطلب.
    }
  },
  /**
   * يحفظ عنوان. لو نفس العنوان متسجل قبل كده، بيرجّعه من غير تكرار
   * (المقارنة بنص العنوان بعد trim و lowercase) — عشان تلميح العنوان
   * ما يتقفلش على أدق فروق المسافات.
   */
  save(text, label) {
    const clean = String(text || "").trim().slice(0, 350);
    if (clean.length < 5) return this.read();
    const entries = this.read();
    const key = clean.toLowerCase();
    const existing = entries.find((a) => a.text.toLowerCase() === key);
    if (existing) {
      // نحدّث التسمية بس — العميل لعنوان موجود.
      existing.label = String(label || existing.label || "المنزل").slice(0, 30);
      this.write(entries);
      return entries;
    }
    entries.unshift({
      // id تسلسلي local: مفيش حاجة بتبحث بيه في السيرفر.
      id: `a${Date.now().toString(36)}`,
      label: String(label || "المنزل").slice(0, 30),
      text: clean,
    });
    this.write(entries);
    return entries;
  },
  remove(id) {
    const entries = this.read().filter((a) => a.id !== id);
    this.write(entries);
    return entries;
  },
  clear() {
    localStorage.removeItem(ADDRESS_KEY);
  },
};
window.restaurantAddressStore = AddressStore;

// ===== سلة الصفحة الرئيسية (Alpine) =====
function storeCart() {
  return {
    cartOpen: false,
    loading: false,
    error: "",
    items: [],
    qtyMap: {}, // كمية مؤقتة لكل كارت منتج على الصفحة الرئيسية
    customer: { name: "", phone: "", address: "", notes: "" },

    // ===== Checkout: الفرع + طريقة الاستلام + الوقت + الكوبون + معاينة الحساب =====
    // `branches` من `/api/branches` — فروع مفعّلة بس. `selectedBranchId`
    // لو الـAPI رجّع فروع، بنختار أول واحد (الـdefault بييجي الأول).
    // `orderChannel` بيحدد هل الطلب هيتحوّل لواتساب ولا يفضل جوه الموقع.
    branches: [],
    selectedBranchId: null,
    orderChannel: "whatsapp", // "whatsapp" | "system"
    deliveryOptions: [],
    deliveryZones: [],
    selectedDeliveryId: null,
    selectedZoneId: null,
    scheduling: "asap", // "asap" | "scheduled"
    scheduledTime: "",
    couponCode: "",
    couponValid: null,
    couponMessage: "",
    // Phase 2: طرق الدفع بقت من `/api/payment-methods` — مفعّلة بس.
    // `selectedPaymentMethod` بقي **id** مش نص ("cash"). السبب إن
    // التسمية والاسم والأ-sort كلها قابلة للتغيير من الأدمن، والـid
    // هو الوحيد الثابت. الكود القديم كان بيبعت `payment_method: "cash"`
    // والعميل بيشوف "كاش" في dropdown وقيمة "cash" في الـwire.
    paymentMethods: [],
    selectedPaymentMethodId: null,
    // معاينة الحساب من السيرفر — المصدر الوحيد للإجمالي النهائي.
    preview: { subtotal: 0, discount_amount: 0, delivery_fee: 0, total: 0 },
    previewLoading: false,
    // رقم تسلسلي بيخلينا نتجاهل ردود `/preview` اللي رجعت متأخرة
    // بعد ما العميل غيّر السلة تاني.
    _previewSeq: 0,
    // `call_center`: الرسوم لسه متحددةش من المطعم. السيرفر بيرجّع صفر
    // مؤقت مع `delivery_fee_pending: true`، والواجهة بتعرض "هتحدد"
    // عشان صفر صريح كان هيتفهّم إنه توصيل ببلاش.
    deliveryFeePending: false,
    // Phase 2: العناوين المحفوظة على الجهاز.
    savedAddresses: [],
    selectedAddressId: null,
    newAddressLabel: "",
    saveAddressChecked: false,
    useAddress(addr) {
      this.selectedAddressId = addr.id;
      this.customer.address = addr.text;
    },
    removeAddress(addr) {
      this.savedAddresses = AddressStore.remove(addr.id);
      if (this.selectedAddressId === addr.id) {
        this.selectedAddressId = null;
        this.customer.address = "";
      }
    },
    persistSavedAddress() {
      if (!this.saveAddressChecked) return;
      this.savedAddresses = AddressStore.save(this.customer.address, this.newAddressLabel);
      this.selectedAddressId = this.savedAddresses[0] ? this.savedAddresses[0].id : null;
      this.saveAddressChecked = false;
    },
    // Phase 2: توكن طلب العميل الحالي — بيتحط بعد `submitOrder` بنجاح
    // عشان نوديه على صفحة التتبّع.
    trackingToken: null,

    load() {
      this.items = Cart.read();
      this.savedAddresses = AddressStore.read();
      this.loadCheckout();
    },
    persist() {
      Cart.write(this.items);
    },

    async loadCheckout() {
      try {
        const [optionsResp, zonesResp, branchesResp, methodsResp] = await Promise.all([
          fetch("/api/delivery-options"),
          fetch("/api/delivery-zones"),
          fetch("/api/branches"),
          fetch("/api/payment-methods"),
        ]);
        if (optionsResp.ok) {
          this.deliveryOptions = await optionsResp.json();
          if (this.deliveryOptions.length && this.selectedDeliveryId === null) {
            this.selectedDeliveryId = this.deliveryOptions[0].id;
          }
        }
        if (zonesResp.ok) this.deliveryZones = await zonesResp.json();
        if (branchesResp.ok) {
          this.branches = await branchesResp.json();
          if (this.branches.length && this.selectedBranchId === null) {
            this.selectedBranchId = this.branches[0].id;
          }
        }
        if (methodsResp.ok) {
          this.paymentMethods = await methodsResp.json();
          if (this.paymentMethods.length && this.selectedPaymentMethodId === null) {
            this.selectedPaymentMethodId = this.paymentMethods[0].id;
          }
        }
      } catch {}
      this.refreshPreview();
    },
    selectedBranch() {
      return (
        this.branches.find((b) => b.id === this.selectedBranchId) || null
      );
    },
    selectedDeliveryOption() {
      return (
        this.deliveryOptions.find((o) => o.id === this.selectedDeliveryId) || null
      );
    },
    isPickup() {
      const opt = this.selectedDeliveryOption();
      return !!opt && opt.delivery_type === "pickup";
    },
    isZone() {
      const opt = this.selectedDeliveryOption();
      return !!opt && opt.delivery_type === "zone";
    },
    // `call_center`: العميل يطلب من غير رسوم معروفة، والمطعم يحدّدها
    // بعدين. العناوين لسه مطلوبة (الطلب بيتبعت لعنوان فعلي)، فمفيش
    // استثناء زي الـpickup.
    isCallCenter() {
      const opt = this.selectedDeliveryOption();
      return !!opt && opt.delivery_type === "call_center";
    },
    // أداة العرض: الرسوم جاية من السيرفر، فلو معلّقة بنعرض "هتحدد من
    // المطعم" بدل الرقم. الرقم في الحالة دي صفر مؤقت، والعميل لو شافه
    // هيقول الطلب ببلاش.
    deliveryFeeText() {
      if (this.deliveryFeePending) return "هتحدد من المطعم";
      return this.formatPrice(this.preview.delivery_fee);
    },
    selectedPaymentMethod() {
      return (
        this.paymentMethods.find((m) => m.id === this.selectedPaymentMethodId) || null
      );
    },
    minScheduledTime() {
      const d = new Date(Date.now() + 15 * 60000);
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
        d.getHours()
      )}:${pad(d.getMinutes())}`;
    },
    // عناصر السلة بالشكل اللي بيقبله الـBackend
    mappedItems() {
      return this.items.map((i) => ({
        product_id: i.id,
        quantity: i.quantity,
        option_ids: (i.optionIds || []).map(Number),
        addon_ids: (i.addonIds || []).map(Number),
        item_note: i.item_note || null,
      }));
    },
    // بيطلب من الـBackend يحسب الإجمالي (subtotal/delivery/discount/total) — للعرض قبل التأكيد
    async refreshPreview() {
      if (!this.items.length) {
        this.preview = { subtotal: 0, discount_amount: 0, delivery_fee: 0, total: 0 };
        this.couponValid = null;
        this.couponMessage = "";
        this.deliveryFeePending = false;
        return;
      }
      const seq = ++this._previewSeq;
      this.previewLoading = true;
      try {
        const resp = await fetch("/api/orders/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            items: this.mappedItems(),
            delivery_option_id: this.selectedDeliveryId,
            delivery_zone_id: this.isZone() ? this.selectedZoneId : null,
            coupon_code: this.couponCode.trim() || null,
          }),
        });
        if (seq !== this._previewSeq) return; // نتيجة قديمة — تجاهل
        if (resp.ok) {
          const data = await resp.json();
          this.preview = {
            subtotal: Number(data.subtotal || 0),
            discount_amount: Number(data.discount_amount || 0),
            delivery_fee: Number(data.delivery_fee || 0),
            total: Number(data.total || 0),
          };
          // `call_center` — الرسوم بتتحدد بعد التأكيد. الرقم صفر
          // مؤقت، والـflag بيقول للواجهة تعرض "هتحدد" بدل صفر.
          this.deliveryFeePending = data.delivery_fee_pending === true;
          if (this.couponCode.trim()) {
            this.couponValid = data.coupon_valid === true;
            this.couponMessage = data.coupon_message || "";
          }
        }
      } catch {
      } finally {
        if (seq === this._previewSeq) this.previewLoading = false;
      }
    },
    applyCoupon() {
      const code = this.couponCode.trim();
      if (!code) {
        this.couponValid = null;
        this.couponMessage = "";
        this.refreshPreview();
        return;
      }
      this.refreshPreview();
    },
    onCheckoutChange() {
      this.refreshPreview();
    },

    formatPrice(value) {
      return Number(value || 0).toFixed(2);
    },

    // استجابة 422 من FastAPI بترجع detail كقائمة أخطاء، مش نص.
    // بناخد أول رسالة human-readable عشان نعرضها للعميل.
    extractErrorMessage(payload, fallback) {
      const detail = payload && payload.detail;
      if (typeof detail === "string" && detail) return detail;
      if (Array.isArray(detail)) {
        const first = detail.find((d) => d && typeof d.msg === "string" && d.msg);
        if (first) return first.msg;
      }
      return fallback;
    },

    // تحقق سريع من رقم الموبايل المصري قبل ما نبعته للسيرفر.
    // السيرفر بيتحقق تاني وبيرفض الغلط برضه — ده مجرد UX.
    phoneLooksValid() {
      const digits = this.customer.phone.replace(/\D/g, "").replace(/^00/, "");
      const national = digits.startsWith("20") && digits.length > 10
        ? digits.slice(2)
        : digits;
      const local = national.startsWith("0") && national.length === 11
        ? national.slice(1)
        : national;
      return /^1[0125]\d{8}$/.test(local);
    },

    // سعر الوحدة = سعر المنتج + الإضافات + الخيارات (للعرض فقط — الـBackend بيعيد التحقق)
    unitPrice(item) {
      const base = Number(item.price ?? item.base_price ?? 0);
      const addons = (item.addons || []).reduce(
        (sum, a) => sum + Number(a.price || 0),
        0
      );
      const options = (item.options || []).reduce(
        (sum, o) => sum + Number(o.price_adjustment || 0),
        0
      );
      return base + addons + options;
    },
    // إجمالي السطر = سعر الوحدة × الكمية
    lineTotal(item) {
      return this.unitPrice(item) * Number(item.quantity || 0);
    },
    count() {
      return this.items.length;
    },
    // إجمالي محلي احتياطي لو الـpreview لسه مجاش — النهائي دايمًا من الـBackend
    total() {
      if (this.preview && this.preview.total > 0) return this.preview.total;
      return this.items.reduce((sum, i) => sum + this.lineTotal(i), 0);
    },

    // كمية كارت المنتج (أرقام صحيحة)
    setProductQty(productId, value) {
      let n = Math.floor(Number(value) || 1);
      this.qtyMap[productId] = Math.min(999, Math.max(1, n));
    },
    increaseProductQty(productId) {
      const cur = Number(this.qtyMap[productId] || 1);
      this.qtyMap[productId] = Math.min(999, cur + 1);
    },
    decreaseProductQty(productId) {
      const cur = Number(this.qtyMap[productId] || 1);
      this.qtyMap[productId] = Math.max(1, cur - 1);
    },

    add(product) {
      const qty = Math.floor(Number(product.quantity || 1)) || 1;
      const optionIds = (product.optionIds || []).map(Number);
      const addonIds = (product.addonIds || []).map(Number);
      const id = Cart.lineId(product.id, optionIds, addonIds, product.item_note);
      const found = this.items.find((i) => i.lineId === id);
      if (found) {
        found.quantity = Math.min(999, found.quantity + qty);
      } else {
        this.items.push({
          lineId: id,
          id: product.id,
          name: product.name,
          price: Number(product.base_price ?? product.price ?? 0),
          quantity: qty,
          options: product.options || [],
          optionIds,
          addons: product.addons || [],
          addonIds,
          item_note: product.item_note || "",
        });
      }
      this.persist();
      this.refreshPreview();
    },

    // كمية سطر السلة (أرقام صحيحة)
    increaseQty(item) {
      item.quantity = Math.min(999, Number(item.quantity || 1) + 1);
      this.persist();
      this.refreshPreview();
    },
    decreaseQty(item) {
      item.quantity = Math.max(1, Number(item.quantity || 1) - 1);
      this.persist();
      this.refreshPreview();
    },
    setQty(item, value) {
      let n = Math.floor(Number(value) || 1);
      if (n < 1) n = 1;
      if (n > 999) n = 999;
      item.quantity = n;
      this.persist();
      this.refreshPreview();
    },
    remove(lineId) {
      this.items = this.items.filter((i) => i.lineId !== lineId);
      this.persist();
      this.refreshPreview();
    },

    async submitOrder() {
      if (!this.items.length) return;
      this.error = "";

      // الاسم لازم يكون فيه حرفين على الأقل (نفس شرط السيرفر)
      if (this.customer.name.trim().length < 2) {
        this.error = "من فضلك اكتب اسمك.";
        return;
      }
      // رقم موبايل مصري صحيح
      if (!this.phoneLooksValid()) {
        this.error = "رقم الهاتف غير صحيح — من فضلك اكتب رقم موبايل مصري صحيح (مثال: 01012345678)";
        return;
      }
      // العنوان مطلوب للتوصيل فقط
      if (!this.isPickup() && this.customer.address.trim().length < 5) {
        this.error = "من فضلك أدخل عنوان التوصيل.";
        return;
      }
      // لازم يبقى فيه فرع مفعّل واحد على الأقل
      if (this.branches.length && !this.selectedBranchId) {
        this.error = "من فضلك اختر الفرع.";
        return;
      }
      // طريقة دفع مفعّلة واحدة على الأقل. مفيش fallback لـ"cash" هنا:
      // الـid اللي بيرجع من `/api/payment-methods` هو اللي بيتحقق منه
      // السيرفر، و"cash" كـhardcode كانت هتخلّي الأدمن يقفل الدفع النقدي
      // والعميل يفضل يطلبه.
      if (!this.paymentMethods.length || this.selectedPaymentMethodId === null) {
        this.error = "مفيش طريقة دفع مفعّلة — كلّم المطعم على الواتساب.";
        return;
      }
      // الوقت المحدد لازم يكون في المستقبل
      let pickupTime = null;
      if (this.scheduling === "scheduled") {
        if (!this.scheduledTime) {
          this.error = "من فضلك اختر وقت الطلب.";
          return;
        }
        const d = new Date(this.scheduledTime);
        if (isNaN(d.getTime()) || d.getTime() <= Date.now()) {
          this.error = "وقت الطلب يجب أن يكون في المستقبل.";
          return;
        }
        pickupTime = d.toISOString();
      }

      this.loading = true;
      try {
        const resp = await fetch("/orders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            customer_name: this.customer.name,
            customer_phone: this.customer.phone,
            customer_address: this.isPickup() ? null : this.customer.address.trim(),
            notes: this.customer.notes || null,
            delivery_option_id: this.selectedDeliveryId,
            delivery_zone_id: this.isZone() ? this.selectedZoneId : null,
            coupon_code: this.couponCode.trim() || null,
            pickup_time: pickupTime,
            // Phase 2: الـid من جدول `payment_methods` مش نص ثابت.
            // السيرفر بيتأكد إن الطريقة مفعّلة ويخزن لقطة منها مع الطلب.
            payment_method_id: this.selectedPaymentMethodId,
            // الفرع والقناة — السيرفر بيتحقق منهم تاني وبيربط
            // الطلب بفرع مفعّل قبل ما يحفظه.
            branch_id: this.selectedBranchId,
            channel: this.orderChannel,
            items: this.mappedItems(),
          }),
        });
        if (!resp.ok) {
          let msg = "راجع بيانات الطلب.";
          try {
            msg = this.extractErrorMessage(await resp.json(), msg);
          } catch {}
          this.error = msg;
          return;
        }
        const data = await resp.json();
        // العنوان بيتحفظ قبل ما السلة تتمسح — التحويل محتاج عنوان،
        // ومفيش سبب نضيّع اختيار العميل لو الخطوة اللي بعدها فشلت.
        this.persistSavedAddress();
        Cart.clear();
        this.items = [];
        this.preview = { subtotal: 0, discount_amount: 0, delivery_fee: 0, total: 0 };
        // الـtoken بيرجع **مرة واحدة** هنا. بنحفظه في المتصفح قبل أي
        // تحويل، عشان لو العميل رجع من واتساب يلاقي طلبه في السجل.
        // مفيش استرجاع بعد كده — مفيش حساب ولا بريد ولا هاتف بتبعت عليه.
        if (data.tracking_token) {
          rememberOrderToken(data.tracking_token, data.order_id);
        }
        if (this.orderChannel === "whatsapp" && data.whatsapp_url) {
          // طلب `whatsapp`: العميل بيبعت رسالة جاهزة للمطعم. الطلب متحفظ
          // في الداتابيز فعلاً والتوكن في المتصفح، فالطلب بيظهر في
          // `/orders/history` والصفحة دي بتفتحه لوحدها.
          window.location.href = data.whatsapp_url;
        } else {
          // طلب `system`: بنوديه على صفحة التتبّع على طول. `/order/success`
          // كان بيقبل رقم الطلب في الـquery، وده كان بيخلّي أي حد يفتح
          // تأكيد أي طلب بالتخمين. التوكن هو اللي بيفتح طلب حد بعينه.
          window.location.href = trackingUrlFor(data.tracking_token);
        }
      } catch {
        this.error = "تعذّر إرسال الطلب.";
      } finally {
        this.loading = false;
      }
    },
  };
}

// ===== صفحة تفاصيل المنتج (Alpine) =====
function productDetail(product, addons, optionGroups) {
  return {
    product,
    addons,
    optionGroups: optionGroups || [],
    groupSelections: {}, // groupId -> [optionId, ...]
    selectedAddons: [],
    quantity: 1,
    itemNote: "",
    error: "",

    init() {
      for (const g of this.optionGroups) this.groupSelections[g.id] = [];
    },

    formatPrice(value) {
      return Number(value || 0).toFixed(2);
    },

    pickedIds(group) {
      return this.groupSelections[group.id] || [];
    },
    isPicked(group, option) {
      return this.pickedIds(group).includes(option.id);
    },
    // مجموعات الاختيار الواحد (Radio)
    pickOne(group, option) {
      this.groupSelections[group.id] = [option.id];
      this.error = "";
    },
    // مجموعات الاختيار المتعدد (Checkbox) مع احترام الحد الأقصى
    togglePick(group, option) {
      let ids = [...this.pickedIds(group)];
      if (ids.includes(option.id)) {
        ids = ids.filter((i) => i !== option.id);
        this.error = "";
      } else {
        if (group.max_selections && ids.length >= group.max_selections) {
          this.error = `الحد الأقصى للاختيارات من "${group.name}" هو ${group.max_selections}`;
          return;
        }
        ids.push(option.id);
        this.error = "";
      }
      this.groupSelections[group.id] = ids;
    },

    chosenAddons() {
      return this.addons.filter(
        (a) => a.is_active && this.selectedAddons.includes(a.id)
      );
    },
    addonTotal() {
      return this.chosenAddons().reduce(
        (sum, a) => sum + Number(a.price || 0),
        0
      );
    },
    chosenOptions() {
      const out = [];
      for (const g of this.optionGroups) {
        for (const o of g.options) {
          if (this.pickedIds(g).includes(o.id)) out.push(o);
        }
      }
      return out;
    },
    optionsTotal() {
      return this.chosenOptions().reduce(
        (sum, o) => sum + Number(o.price_adjustment || 0),
        0
      );
    },
    totalPrice() {
      return (
        (Number(this.product.base_price || 0) + this.addonTotal() + this.optionsTotal()) *
        this.quantity
      );
    },
    inc() {
      this.quantity = Math.min(999, this.quantity + 1);
    },
    dec() {
      this.quantity = Math.max(1, this.quantity - 1);
    },

    // التحقق من قواعد المجموعات: إجبارية + حد أدنى (للعرض وتجربة المستخدم — الـBackend بيأكد نهائيًا)
    validateSelections() {
      for (const g of this.optionGroups) {
        const count = this.pickedIds(g).length;
        const min = Math.max(g.is_required ? 1 : 0, g.min_selections || 0);
        if (count < min) {
          this.error = min > 1
            ? `من فضلك اختر ${min} خيارات على الأقل من "${g.name}"`
            : `من فضلك اختر خيارًا من "${g.name}"`;
          return false;
        }
      }
      this.error = "";
      return true;
    },

    addToCart() {
      if (!this.validateSelections()) return;
      const chosenOptions = this.chosenOptions().map((o) => ({
        id: o.id,
        name: o.name,
        price_adjustment: Number(o.price_adjustment),
      }));
      const chosenAddons = this.chosenAddons().map((a) => ({
        id: a.id,
        name: a.name,
        price: Number(a.price),
      }));
      Cart.add({
        id: this.product.id,
        name: this.product.name,
        base_price: Number(this.product.base_price),
        quantity: this.quantity,
        options: chosenOptions,
        optionIds: chosenOptions.map((o) => o.id),
        addons: chosenAddons,
        addonIds: chosenAddons.map((a) => a.id),
        item_note: (this.itemNote || "").trim(),
      });
      window.location.href = "/";
    },
  };
}
