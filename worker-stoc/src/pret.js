// Politica de pret si de stoc. Functii pure: fara retea, usor de testat.

const bani = (x) => Math.round(x * 100) / 100;

/** 'Laptopuri & Notebook-uri' -> 'laptopuri notebook uri' (fara diacritice, litere mici). */
export function normalizeaza(text) {
  return String(text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/**
 * Adaosul (%) pentru un produs, dupa categoriile lui de pe site.
 * adaosuri = {"Laptopuri": 10}; prima categorie gasita castiga, altfel adaosul implicit.
 */
export function adaosPentru(categorii, adaosuri, implicit) {
  const nume = new Set(categorii.map(normalizeaza));
  for (const [categorie, procent] of Object.entries(adaosuri)) {
    if (nume.has(normalizeaza(categorie))) return Number(procent);
  }
  return Number(implicit);
}

/** 2710.40 -> 2710.99 ; 66.55 -> 66.99 (niciodata mai mic decat pretul calculat). */
export function rotunjeste99(pret) {
  const r = Math.floor(bani(pret)) + 0.99;
  return bani(r);
}

/**
 * Pretul de vanzare cu TVA:
 *   max(cost x (1 + adaos%), cost + marja minima) x (1 + TVA%), rotunjit la ,99
 */
export function pretVanzare(cost, adaos, cfg) {
  const faraTva = Math.max(cost * (1 + adaos / 100), cost + cfg.marjaMinima);
  const cuTva = faraTva * (1 + cfg.tva / 100);
  return cfg.rotunjire99 ? rotunjeste99(cuTva) : bani(cuTva);
}

/** Stocul afisat pe site: minus rezerva de siguranta, plafonat la maxim (0 = fara plafon). */
export function stocPublicat(stoc, cfg) {
  const s = Math.max(Math.floor(stoc) - cfg.stocRezerva, 0);
  return cfg.stocMaxim > 0 ? Math.min(s, cfg.stocMaxim) : s;
}

/** Cat la suta s-a schimbat costul fata de ultima data (null daca nu avem istoric). */
export function saltProcent(costNou, costVechi) {
  if (!costVechi) return null;
  return Math.abs(costNou - costVechi) / costVechi * 100;
}
