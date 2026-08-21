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
  // نفس المنتج + نفس الإضافات = نفس السطر. نفس المنتج بإضافات مختلفة = سطور منفصلة.
  lineId(productId, addonIds = []) {
    return [productId, ...[...addonIds].sort((a, b) => a - b)].join("-");
  },
  add(item) {
    const items = this.read();
    const id = this.lineId(item.id, item.addonIds || []);
    const found = items.find((i) => i.lineId === id);
    if (found) {
      found.quantity = Math.min(999, found.quantity + item.quantity);
    } else {
      items.push({ lineId: id, ...item });
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

    // سعر الوحدة = سعر المنتج + الإضافات
    unitPrice(item) {
      const base = Number(item.price || 0);
      const addons = (item.addons || []).reduce(
        (sum, a) => sum + Number(a.price || 0),
        0
      );
      return base + addons;
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
      const addonIds = (product.addonIds || []).map(Number);
      const id = Cart.lineId(product.id, addonIds);
      const found = this.items.find((i) => i.lineId === id);
      if (found) {
        found.quantity = Math.min(999, found.quantity + qty);
      } else {
        this.items.push({
          lineId: id,
          id: product.id,
          name: product.name,
          price: Number(product.price || 0),
          quantity: qty,
          addons: product.addons || [],
          addonIds: addonIds,
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
              addon_ids: (i.addonIds || []).map(Number),
            })),
          }),
        });
        if (!resp.ok) {
          this.error = "راجع بيانات الطلب.";
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
function productDetail(product, addons) {
  return {
    product,
    addons,
    selectedAddons: [],
    quantity: 1,

    formatPrice(value) {
      return Number(value || 0).toFixed(2);
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
    totalPrice() {
      return (Number(this.product.price || 0) + this.addonTotal()) * this.quantity;
    },
    inc() {
      this.quantity = Math.min(999, this.quantity + 1);
    },
    dec() {
      this.quantity = Math.max(1, this.quantity - 1);
    },
    addToCart() {
      const chosen = this.chosenAddons().map((a) => ({
        id: a.id,
        name: a.name,
        price: Number(a.price),
      }));
      Cart.add({
        id: this.product.id,
        name: this.product.name,
        price: Number(this.product.price),
        quantity: this.quantity,
        addons: chosen,
        addonIds: chosen.map((a) => a.id),
      });
      window.location.href = "/";
    },
  };
}
