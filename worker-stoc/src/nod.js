// Conector furnizor NOD.
//
// PROVIZORIU: documentatia API NOD nu e publica (o primesti din contul b2b.nod.ro).
// Pana atunci, conectorul citeste orice lista JSON sau CSV de la un URL (API sau feed),
// cu numele campurilor setate in wrangler.toml. Cand avem documentatia, aici se adauga
// autentificarea exacta NOD - restul Worker-ului nu se schimba.

function iaCamp(obiect, cale) {
  for (const parte of (cale || "").split(".").filter(Boolean)) {
    if (obiect == null || typeof obiect !== "object") return undefined;
    obiect = obiect[parte];
  }
  return obiect;
}

/** 10 / "10" / ">10" / "10+" / "5-10" / "da" -> numar (null daca nu se intelege). */
export function parseazaStoc(valoare) {
  if (valoare == null) return null;
  if (typeof valoare === "boolean") return valoare ? 1 : 0;
  if (typeof valoare === "number") return Math.max(Math.floor(valoare), 0);
  const s = String(valoare).trim().toLowerCase();
  const numar = s.match(/\d+/);
  if (numar) return Number(numar[0]); // ">10" -> 10 (minimul sigur)
  if (["da", "yes", "true", "in stoc", "in stock", "disponibil"].includes(s)) return 1;
  if (["", "nu", "no", "false", "stoc epuizat", "indisponibil", "out of stock"].includes(s)) return 0;
  return null;
}

/** "1.234,56" / "1,234.56" / "1234.56" / 1234.56 -> 1234.56 */
export function parseazaPret(valoare) {
  if (valoare == null || valoare === "") return null;
  if (typeof valoare === "number") return valoare;
  let s = String(valoare).replace(/[^\d.,-]/g, "");
  if (s.includes(",") && s.includes(".")) {
    s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
  } else if (s.includes(",")) {
    s = /,\d{1,2}$/.test(s) ? s.replace(",", ".") : s.replace(/,/g, "");
  }
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function parseazaCsv(text) {
  const linii = text.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  const sep = (linii[0].match(/;/g) || []).length >= (linii[0].match(/,/g) || []).length ? ";" : ",";
  const celule = (linie) => linie.split(sep).map((c) => c.trim().replace(/^"|"$/g, ""));
  const cap = celule(linii[0]);
  return linii.slice(1).map((l) => Object.fromEntries(celule(l).map((v, i) => [cap[i], v])));
}

/** Inregistrarile brute de la NOD. */
export async function descarcaNod(env) {
  if (!env.NOD_URL) {
    throw new Error("Conectorul NOD nu e configurat inca (NOD_URL gol). Asteptam documentatia API NOD.");
  }
  const headere = { Accept: "application/json, text/csv, */*" };
  if (env.NOD_TOKEN) headere[env.NOD_HEADER_TOKEN || "Authorization"] = env.NOD_TOKEN;
  const r = await fetch(env.NOD_URL, { headers: headere });
  const text = await r.text();
  if (!r.ok) throw new Error(`NOD -> HTTP ${r.status}: ${text.slice(0, 300)}`);

  if ((env.NOD_FORMAT || "json").toLowerCase() === "csv") return parseazaCsv(text);
  const date = JSON.parse(text);
  const lista = env.NOD_LISTA ? iaCamp(date, env.NOD_LISTA) : date;
  if (!Array.isArray(lista)) {
    throw new Error(`Raspunsul NOD nu e o lista (verifica NOD_LISTA). Primit: ${text.slice(0, 200)}`);
  }
  return lista;
}

/** Map cod (litere mari) -> {cod, stoc, cost}. Acelasi cod de mai multe ori -> stocurile se aduna. */
export function stocNod(brute, env) {
  const campCod = env.NOD_CAMP_COD || "code";
  const campStoc = env.NOD_CAMP_STOC || "stock";
  const campPret = env.NOD_CAMP_PRET || "price";
  const rezultat = new Map();
  for (const p of brute) {
    const cod = String(iaCamp(p, campCod) ?? "").trim();
    if (!cod) continue;
    const stoc = parseazaStoc(iaCamp(p, campStoc)) ?? 0;
    const cost = parseazaPret(iaCamp(p, campPret));
    const existent = rezultat.get(cod.toUpperCase());
    if (existent) {
      existent.stoc += stoc;
      if (cost != null && (existent.cost == null || cost < existent.cost)) existent.cost = cost;
    } else {
      rezultat.set(cod.toUpperCase(), { cod, stoc, cost });
    }
  }
  return rezultat;
}
