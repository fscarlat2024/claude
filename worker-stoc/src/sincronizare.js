// Compara produsele de pe lengo.ro cu NOD si decide, pentru fiecare, stocul si pretul nou.

import { adaosPentru, pretVanzare, saltProcent, stocPublicat } from "./pret.js";

const json = (text, implicit) => {
  try {
    return text ? JSON.parse(text) : implicit;
  } catch {
    throw new Error(`Valoare JSON gresita in configurare: ${text}`);
  }
};

/** Setarile din wrangler.toml [vars], cu valori implicite. */
export function citesteConfig(env) {
  return {
    actualizare: (env.MOD || "verificare").toLowerCase() === "actualizare",
    tva: Number(env.TVA ?? 21),
    adaosuri: json(env.ADAOS_CATEGORII, { Laptopuri: 10 }),
    adaosImplicit: Number(env.ADAOS_IMPLICIT ?? 30),
    marjaMinima: Number(env.MARJA_MINIMA ?? 25),
    rotunjire99: String(env.ROTUNJIRE_99 ?? "da").toLowerCase() !== "nu",
    pragSalt: Number(env.PRAG_SALT ?? 10),
    stocRezerva: Number(env.STOC_REZERVA ?? 0),
    stocMaxim: Number(env.STOC_MAXIM ?? 0),
    skuPrefix: env.SKU_PREFIX || "",
    preturiFixe: json(env.PRETURI_FIXE, {}),
  };
}

const egal = (a, b) => a != null && b != null && Math.abs(a - b) < 0.005;

/**
 * @param produse       produsele de pe lengo.ro (WooCommerce.produse())
 * @param nod           Map cod -> {cod, stoc, cost} (stocNod())
 * @param costuriVechi  {SKU: cost} din rularea trecuta (pentru frana la salturi)
 * @returns {linii, modificari, costuri, deAprobat}
 */
export function planifica(produse, nod, costuriVechi, cfg) {
  const linii = [];
  const modificari = [];
  const costuri = { ...costuriVechi };
  const deAprobat = {};

  for (const p of produse) {
    const linie = {
      id: p.id, sku: p.sku, nume: p.nume, stocSite: p.stoc, pretSite: p.pret,
      stocNod: null, cost: null, adaos: null, stocNou: null, pretNou: null, note: [], alerta: false, deModificat: false,
    };
    linii.push(linie);

    if (!p.sku) {
      linie.note.push("fără SKU – nu știu care produs NOD este");
      linie.alerta = true;
      continue;
    }
    if (p.tip && p.tip !== "simple") {
      linie.note.push(`produs de tip „${p.tip}” – neacceptat încă`);
      continue;
    }

    const cod = cfg.skuPrefix && p.sku.startsWith(cfg.skuPrefix) ? p.sku.slice(cfg.skuPrefix.length) : p.sku;
    const n = nod.get(cod.toUpperCase());

    if (!n) {
      // pe site raman doar produsele pe stoc la NOD
      linie.stocNou = 0;
      linie.note.push("nu mai apare la NOD – ascuns (stoc 0)");
    } else {
      linie.stocNod = n.stoc;
      linie.cost = n.cost;
      linie.stocNou = stocPublicat(n.stoc, cfg);
      if (linie.stocNou === 0) linie.note.push("fără stoc la NOD – ascuns");

      const pretFix = cfg.preturiFixe[p.sku];
      if (pretFix != null) {
        linie.pretNou = Number(pretFix);
        if (n.cost != null && linie.pretNou < n.cost * (1 + cfg.tva / 100)) {
          linie.stocNou = 0;
          linie.note.push("PREȚ FIX SUB COST – ascuns până îl corectezi");
          linie.alerta = true;
        } else {
          linie.note.push("preț fix (manual)");
        }
      } else if (n.cost == null) {
        linie.note.push("NOD nu are preț pentru el – prețul rămâne neschimbat");
      } else {
        linie.adaos = adaosPentru(p.categorii || [], cfg.adaosuri, cfg.adaosImplicit);
        const salt = saltProcent(n.cost, costuriVechi[p.sku]);
        if (salt != null && salt > cfg.pragSalt) {
          deAprobat[p.sku] = n.cost;
          linie.alerta = true;
          linie.note.push(
            `COSTUL S-A SCHIMBAT CU ${salt.toFixed(1).replace(".", ",")}% (${costuriVechi[p.sku]} → ${n.cost} lei) – preț oprit, așteaptă aprobare`,
          );
        } else {
          linie.pretNou = pretVanzare(n.cost, linie.adaos, cfg);
          costuri[p.sku] = n.cost;
        }
      }
    }

    const modificare = { id: p.id };
    if (linie.stocNou != null && linie.stocNou !== p.stoc) modificare.stoc = linie.stocNou;
    if (linie.pretNou != null && !egal(linie.pretNou, p.pret)) modificare.pret = linie.pretNou;
    if (Object.keys(modificare).length > 1) {
      modificari.push(modificare);
      linie.deModificat = true;
    }
  }
  return { linii, modificari, costuri, deAprobat };
}
