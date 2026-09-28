// Teste fara retea: NOD si lengo.ro sunt simulate prin inlocuirea lui fetch.
import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import worker, { ruleaza } from "../src/index.js";
import { parseazaPret, parseazaStoc, stocNod } from "../src/nod.js";
import { adaosPentru, pretVanzare, rotunjeste99, stocPublicat } from "../src/pret.js";
import { citesteConfig, planifica } from "../src/sincronizare.js";

const CFG = citesteConfig({ ADAOS_CATEGORII: '{"Laptopuri": 10}', ADAOS_IMPLICIT: "30" });

describe("politica de pret", () => {
  test("exemplele din discutie: laptop si mouse", () => {
    // laptop: 2000 x 1,10 = 2200 -> x 1,21 = 2662 -> 2662,99
    assert.equal(pretVanzare(2000, 10, CFG), 2662.99);
    // mouse: 30 x 1,30 = 39 < 30 + 25 = 55 -> 55 x 1,21 = 66,55 -> 66,99
    assert.equal(pretVanzare(30, 30, CFG), 66.99);
  });

  test("rotunjirea la ,99 nu scade niciodata pretul", () => {
    assert.equal(rotunjeste99(66.55), 66.99);
    assert.equal(rotunjeste99(100), 100.99);
    assert.equal(rotunjeste99(99.99), 99.99);
  });

  test("fara rotunjire", () => {
    assert.equal(pretVanzare(100, 30, { ...CFG, rotunjire99: false }), 157.3);
  });

  test("adaosul dupa categorie, fara diacritice si majuscule", () => {
    assert.equal(adaosPentru(["LAPTOPURI"], { Laptopuri: 10 }, 30), 10);
    assert.equal(adaosPentru(["Mouse", "Accesorii"], { Laptopuri: 10 }, 30), 30);
    assert.equal(adaosPentru(["Căști"], { Casti: 25 }, 30), 25);
  });

  test("stocul publicat: rezerva si maxim", () => {
    assert.equal(stocPublicat(10, { stocRezerva: 2, stocMaxim: 5 }), 5);
    assert.equal(stocPublicat(1, { stocRezerva: 2, stocMaxim: 0 }), 0);
  });
});

describe("citire NOD", () => {
  test("stoc in diverse forme", () => {
    for (const [v, e] of [[5, 5], ["7", 7], [">10", 10], ["10+", 10], ["da", 1], ["nu", 0], [-3, 0], [null, null]]) {
      assert.equal(parseazaStoc(v), e, `stoc ${v}`);
    }
  });

  test("pret in diverse forme", () => {
    for (const [v, e] of [["1.234,56", 1234.56], ["1,234.56", 1234.56], ["99,9", 99.9], [12.5, 12.5], ["", null]]) {
      assert.equal(parseazaPret(v), e, `pret ${v}`);
    }
  });

  test("acelasi cod din mai multe depozite: stocurile se aduna", () => {
    const m = stocNod([{ code: "a1", stock: 2, price: 10 }, { code: "A1", stock: "3", price: 9 }], {});
    assert.deepEqual(m.get("A1"), { cod: "a1", stoc: 5, cost: 9 });
  });
});

describe("planificare", () => {
  const produs = (x) => ({ id: 1, sku: "L1", nume: "Laptop", tip: "simple", categorii: ["Laptopuri"], stoc: 0, pret: 1, ...x });

  test("produs care nu e la NOD -> ascuns (stoc 0)", () => {
    const { modificari } = planifica([produs({ stoc: 4 })], new Map(), {}, CFG);
    assert.deepEqual(modificari, [{ id: 1, stoc: 0 }]);
  });

  test("frana: cost sarit cu peste 10% -> pretul nu se schimba, stocul da", () => {
    const nod = new Map([["L1", { cod: "L1", stoc: 3, cost: 2500 }]]);
    const r = planifica([produs({ pret: 2662.99 })], nod, { L1: 2000 }, CFG);
    assert.deepEqual(r.modificari, [{ id: 1, stoc: 3 }]);
    assert.deepEqual(r.deAprobat, { L1: 2500 });
    assert.equal(r.costuri.L1, 2000);
    assert.ok(r.linii[0].alerta);
  });

  test("pret fix sub cost -> produsul e ascuns", () => {
    const nod = new Map([["L1", { cod: "L1", stoc: 3, cost: 100 }]]);
    const cfg = { ...CFG, preturiFixe: { L1: 110 } }; // 110 < 100 x 1,21
    const r = planifica([produs({ pret: 110 })], nod, {}, cfg);
    assert.deepEqual(r.modificari, []);
    assert.equal(r.linii[0].stocNou, 0);
  });

  test("prefix SKU", () => {
    const nod = new Map([["123", { cod: "123", stoc: 2, cost: 30 }]]);
    const r = planifica([produs({ sku: "NOD-123", categorii: [] })], nod, {}, { ...CFG, skuPrefix: "NOD-" });
    assert.deepEqual(r.modificari, [{ id: 1, stoc: 2, pret: 66.99 }]);
  });
});

// ---------------------------------------------------------------- flux complet

const NOD = [
  { code: "LAP-1", stock: ">10", price: "2000,00" },
  { code: "MOU-1", stock: 3, price: 30 },
  { code: "CAS-1", stock: 0, price: 100 },
];
const LENGO = [
  { id: 11, sku: "LAP-1", name: "Laptop X", type: "simple", categories: [{ name: "Laptopuri" }],
    manage_stock: true, stock_quantity: 2, regular_price: "2500.00" },
  { id: 12, sku: "MOU-1", name: "Mouse Y", type: "simple", categories: [{ name: "Accesorii" }],
    manage_stock: true, stock_quantity: 3, regular_price: "66.99" },
  { id: 13, sku: "CAS-1", name: "Casti Z", type: "simple", categories: [{ name: "Accesorii" }],
    manage_stock: true, stock_quantity: 5, regular_price: "150.00" },
  { id: 14, sku: "", name: "Fara cod", type: "simple", categories: [], manage_stock: false, regular_price: "10" },
];

let cereri;

function kv() {
  const m = new Map();
  return { get: async (k) => m.get(k) ?? null, put: async (k, v) => void m.set(k, v), m };
}

function mediu(extra = {}) {
  return {
    STARE: kv(), SITE_URL: "https://lengo.ro", WOO_CHEIE: "ck_x", WOO_SECRET: "cs_y",
    NOD_URL: "https://nod.test/produse", RAPORT_PAROLA: "secret",
    ADAOS_CATEGORII: '{"Laptopuri": 10}', ADAOS_IMPLICIT: "30", ...extra,
  };
}

beforeEach(() => {
  cereri = [];
  globalThis.fetch = async (url, optiuni = {}) => {
    const u = new URL(url);
    cereri.push({ metoda: optiuni.method || "GET", url: u, corp: optiuni.body && JSON.parse(optiuni.body), headere: optiuni.headers });
    if (u.host === "nod.test") return new Response(JSON.stringify(NOD));
    if (u.pathname === "/wp-json/wc/v3/products") return new Response(JSON.stringify(u.searchParams.get("page") === "1" ? LENGO : []));
    if (u.pathname === "/wp-json/wc/v3/products/batch") return new Response("{}");
    return new Response("negasit", { status: 404 });
  };
});

describe("flux complet", () => {
  test("mod verificare: calculeaza, dar NU scrie pe site", async () => {
    const env = mediu();
    const r = await ruleaza(env);
    assert.equal(r.mod, "verificare");
    assert.equal(r.modificari, 2);
    assert.equal(cereri.filter((c) => c.metoda === "POST").length, 0);
    assert.match(cereri.find((c) => c.url.host === "lengo.ro").headere.Authorization, /^Basic /);
    assert.ok(env.STARE.m.get("raport"));
  });

  test("mod actualizare: trimite doar diferentele intr-un singur lot", async () => {
    await ruleaza(mediu({ MOD: "actualizare" }));
    const posturi = cereri.filter((c) => c.metoda === "POST");
    assert.equal(posturi.length, 1);
    assert.deepEqual(posturi[0].corp.update.sort((a, b) => a.id - b.id), [
      { id: 11, manage_stock: true, stock_quantity: 10, regular_price: "2662.99" },
      { id: 13, manage_stock: true, stock_quantity: 0, regular_price: "157.99" },
    ]);
  });

  test("eroare NOD -> salvata in raport", async () => {
    const env = mediu({ NOD_URL: "" });
    await assert.rejects(ruleaza(env), /NOD_URL gol/);
    assert.match(JSON.parse(env.STARE.m.get("raport")).eroare.mesaj, /NOD_URL gol/);
  });

  test("pagina de raport cere parola si apoi se afiseaza", async () => {
    const env = mediu();
    await ruleaza(env);
    const fara = await worker.fetch(new Request("https://w.dev/"), env);
    assert.equal(fara.status, 401);
    const auth = { Authorization: "Basic " + btoa("x:secret") };
    const cu = await worker.fetch(new Request("https://w.dev/", { headers: auth }), env);
    assert.equal(cu.status, 200);
    const html = await cu.text();
    assert.match(html, /Laptop X/);
    assert.match(html, /2\.662,99/);
    const gresit = await worker.fetch(new Request("https://w.dev/", { headers: { Authorization: "Basic " + btoa("x:nu") } }), env);
    assert.equal(gresit.status, 401);
  });

  test("aprobarea unui cost sarit -> la rularea urmatoare pretul se aplica", async () => {
    const env = mediu({ MOD: "actualizare" });
    await env.STARE.put("costuri", JSON.stringify({ "LAP-1": 1500 })); // 2000 fata de 1500 = +33%
    await ruleaza(env);
    assert.deepEqual(JSON.parse(env.STARE.m.get("deAprobat")), { "LAP-1": 2000 });
    let lot = cereri.find((c) => c.metoda === "POST").corp.update.find((u) => u.id === 11);
    assert.equal(lot.regular_price, undefined); // pret oprit, doar stocul

    const auth = { Authorization: "Basic " + btoa("x:secret") };
    const r = await worker.fetch(new Request("https://w.dev/aproba", {
      method: "POST", headers: auth, body: new URLSearchParams({ sku: "LAP-1" }),
    }), env);
    assert.equal(r.status, 303);

    cereri = [];
    await ruleaza(env);
    lot = cereri.find((c) => c.metoda === "POST").corp.update.find((u) => u.id === 11);
    assert.equal(lot.regular_price, "2662.99");
  });
});
