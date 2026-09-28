// Conector WooCommerce (lengo.ro): citeste produsele si trimite stocul + pretul.

export class WooCommerce {
  /**
   * @param {string} siteUrl   ex. https://lengo.ro
   * @param {string} cheie     ck_...
   * @param {string} secret    cs_...
   * @param {boolean} cheiInUrl  unele hostinguri sterg headerul Authorization; atunci cheile merg in URL
   */
  constructor(siteUrl, cheie, secret, cheiInUrl = false) {
    this.baza = siteUrl.replace(/\/+$/, "") + "/wp-json/wc/v3";
    this.cheie = cheie;
    this.secret = secret;
    this.cheiInUrl = cheiInUrl;
  }

  async cere(metoda, cale, corp) {
    const url = new URL(this.baza + cale);
    const headere = { Accept: "application/json", "User-Agent": "lengo-stoc-worker/1.0" };
    if (this.cheiInUrl) {
      url.searchParams.set("consumer_key", this.cheie);
      url.searchParams.set("consumer_secret", this.secret);
    } else {
      headere.Authorization = "Basic " + btoa(`${this.cheie}:${this.secret}`);
    }
    if (corp !== undefined) headere["Content-Type"] = "application/json";
    const r = await fetch(url, {
      method: metoda,
      headers: headere,
      body: corp === undefined ? undefined : JSON.stringify(corp),
    });
    const text = await r.text();
    if (!r.ok) {
      throw new Error(`lengo.ro ${metoda} ${cale} -> HTTP ${r.status}: ${text.slice(0, 300)}`);
    }
    return text ? JSON.parse(text) : null;
  }

  /** Toate produsele simple: [{id, sku, nume, categorii, stoc, pret}]. */
  async produse() {
    const rezultat = [];
    for (let pagina = 1; ; pagina++) {
      const lot = await this.cere("GET", `/products?per_page=100&page=${pagina}&status=any`);
      if (!lot?.length) return rezultat;
      for (const p of lot) {
        rezultat.push({
          id: p.id,
          sku: (p.sku || "").trim(),
          nume: p.name || "",
          tip: p.type,
          categorii: (p.categories || []).map((c) => c.name),
          stoc: p.manage_stock ? p.stock_quantity : null,
          pret: p.regular_price === "" || p.regular_price == null ? null : Number(p.regular_price),
        });
      }
      if (lot.length < 100) return rezultat;
    }
  }

  /** modificari: [{id, stoc?, pret?}] -> loturi de cate 100 prin /products/batch. */
  async actualizeaza(modificari) {
    for (let i = 0; i < modificari.length; i += 100) {
      const update = modificari.slice(i, i + 100).map((m) => {
        const u = { id: m.id };
        if (m.stoc !== undefined) Object.assign(u, { manage_stock: true, stock_quantity: m.stoc });
        if (m.pret !== undefined) u.regular_price = m.pret.toFixed(2);
        return u;
      });
      await this.cere("POST", "/products/batch", { update });
    }
  }
}
