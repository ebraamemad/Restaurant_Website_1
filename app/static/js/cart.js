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
};

// ===== سلة الصفحة الرئيسية (Alpine) =====
function storeCart() {
  return {
    cartOpen: false,
    loading: false,
    error: "",
    items: [],
    qtyMap: {}, // كمية مؤقتة لكل كارت منتج على الصفحة الرئيسية
    customer: { name: "", phone: "", address: "", notes: "" },

    load() {
      this.items = Cart.read();
    },
    persist() {
      Cart.write(this.items);
    },

    formatPrice(value) {
      return Number(value || 0).toFixed(2);
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
    total() {
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
    },

    // كمية سطر السلة (أرقام صحيحة)
    increaseQty(item) {
      item.quantity = Math.min(999, Number(item.quantity || 1) + 1);
      this.persist();
    },
    decreaseQty(item) {
      item.quantity = Math.max(1, Number(item.quantity || 1) - 1);
      this.persist();
    },
    setQty(item, value) {
      let n = Math.floor(Number(value) || 1);
      if (n < 1) n = 1;
      if (n > 999) n = 999;
      item.quantity = n;
      this.persist();
    },
    remove(lineId) {
      this.items = this.items.filter((i) => i.lineId !== lineId);
      this.persist();
    },

    async submitOrder() {
      if (!this.items.length) return;
      this.loading = true;
      this.error = "";
      try {
        const resp = await fetch("/orders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            customer_name: this.customer.name,
            customer_phone: this.customer.phone,
            customer_address: this.customer.address,
            notes: this.customer.notes || null,
            items: this.items.map((i) => ({
              product_id: i.id,
              quantity: i.quantity,
              option_ids: (i.optionIds || []).map(Number),
              addon_ids: (i.addonIds || []).map(Number),
              item_note: i.item_note || null,
            })),
          }),
        });
        if (!resp.ok) {
          let msg = "راجع بيانات الطلب.";
          try {
            const detail = (await resp.json()).detail;
            if (detail) msg = detail;
          } catch {}
          this.error = msg;
          return;
        }
        const data = await resp.json();
        Cart.clear();
        this.items = [];
        window.location.href = data.whatsapp_url;
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
