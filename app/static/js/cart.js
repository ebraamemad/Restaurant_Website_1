function storeCart() {

    return {

        cartOpen: false,

        loading: false,

        error: "",

        items: [],

        qtyMap: {},

        customer: {
            name: "",
            phone: "",
            address: "",
            notes: ""
        },

        load() {
            this.items = JSON.parse(
                localStorage.getItem("cart") || "[]"
            )
            // Ensure every item has an addons array and addonIds array
            this.items.forEach(item => {
                if (!item.addons) item.addons = []
                if (!item.addonIds) item.addonIds = []
            })
        },

        persist() {
            this.items = this.items.filter(
                item => item.quantity > 0
            )
            localStorage.setItem("cart", JSON.stringify(this.items))
        },

        formatPrice(value) {
            const v = Number(value || 0)
            return v.toFixed(2)
        },

        formatQty(value) {
            const amount = Number(value || 1)
            if (!Number.isFinite(amount)) return "1"
            if (Number.isInteger(amount)) return String(amount)
            return String(Number(amount.toFixed(2)).replace(/\.0$/, ""))
        },

        setProductQty(productId, value) {
            const next = Number(value || 0.25)
            this.qtyMap[productId] = Math.max(0.25, Math.min(999, next))
        },

        increaseProductQty(productId) {
            const current = Number(this.qtyMap[productId] || 1)
            this.qtyMap[productId] = Math.min(999, current + 0.25)
        },

        decreaseProductQty(productId) {
            const current = Number(this.qtyMap[productId] || 1)
            const next = current - 0.25
            this.qtyMap[productId] = next < 0.25 ? 0.25 : next
        },

        increaseQty(item) {
            const current = Number(item.quantity || 1)
            item.quantity = Math.min(999, current + 0.25)
            this.persist()
        },

        decreaseQty(item) {
            const current = Number(item.quantity || 1)
            const next = current - 0.25
            item.quantity = next < 0.25 ? 0.25 : next
            this.persist()
        },

        addItemTotal(item) {
            const base = Number(item.price || 0)
            const addonTotal = (item.addons || []).reduce(
                (sum, a) => sum + Number(a.price || 0), 0
            )
            return (base + addonTotal) * item.quantity
        },

        add(product) {
            const quantity = Number(product.qty || 1)
            const addonKey = JSON.stringify(product.addonIds || [])
            const current = this.items.find(
                item => item.id === product.id &&
                    (item.addonKey || "[]") === addonKey
            )
            if (current) {
                current.quantity += quantity
            } else {
                this.items.push({
                    ...product,
                    quantity,
                    addonKey: addonKey,
                    addons: product.addons || [],
                    addonIds: product.addonIds || [],
                })
            }
            this.persist()
        },

        remove(id) {
            this.items = this.items.filter(
                item => item.id !== id
            )
            this.persist()
        },

        total() {
            return this.items.reduce(
                (sum, item) => sum + this.addItemTotal(item),
                0
            )
        },

        async submitOrder() {
            this.loading = true
            this.error = ""

            const response = await fetch("/orders", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    customer_name: this.customer.name,
                    customer_phone: this.customer.phone,
                    customer_address: this.customer.address,
                    notes: this.customer.notes || null,
                    items: this.items.map(item => ({
                        product_id: item.id,
                        quantity: item.quantity,
                        addon_ids: (item.addonIds || []).map(Number)
                    }))
                })
            })

            this.loading = false

            if (!response.ok) {
                this.error = "راجع بيانات الطلب."
                return
            }

            const data = await response.json()
            localStorage.removeItem("cart")
            window.location.href = data.whatsapp_url
        }
    }
}
