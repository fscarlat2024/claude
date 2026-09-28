// Worker Cloudflare: stoc + pret NOD -> lengo.ro
//
//  - scheduled: ruleaza singur, dupa cron-ul din wrangler.toml (implicit o data pe ora)
//  - GET  /        pagina de raport (parola: secretul RAPORT_PAROLA)
//  - POST /ruleaza porneste o verificare acum
//  - POST /aproba  aproba un pret oprit de frana la salturi (sau toate)

import { WooCommerce } from "./lengo.js";
import { descarcaNod, stocNod } from "./nod.js";
import { citesteConfig, planifica } from "./sincronizare.js";
import { paginaRaport } from "./raport.js";

async function citesteKV(env, cheie, implicit) {
  const v = await env.STARE.get(cheie);
  return v ? JSON.parse(v) : implicit;
}

const scrieKV = (env, cheie, valoare) => env.STARE.put(cheie, JSON.stringify(valoare));

export async function ruleaza(env) {
  const inceput = new Date().toISOString();
  const cfg = citesteConfig(env);
  try {
    const nod = stocNod(await descarcaNod(env), env);
    const site = new WooCommerce(env.SITE_URL, env.WOO_CHEIE, env.WOO_SECRET, env.WOO_CHEI_IN_URL === "da");
    const produse = await site.produse();
    const costuriVechi = await citesteKV(env, "costuri", {});
    const plan = planifica(produse, nod, costuriVechi, cfg);

    if (cfg.actualizare && plan.modificari.length) await site.actualizeaza(plan.modificari);

    const raport = {
      ora: inceput,
      mod: cfg.actualizare ? "actualizare" : "verificare",
      produseSite: produse.length,
      coduriNod: nod.size,
      modificari: plan.modificari.length,
      aplicate: cfg.actualizare,
      linii: plan.linii,
    };
    await Promise.all([
      scrieKV(env, "costuri", plan.costuri),
      scrieKV(env, "deAprobat", plan.deAprobat),
      scrieKV(env, "raport", raport),
    ]);
    return raport;
  } catch (e) {
    const vechi = await citesteKV(env, "raport", {});
    await scrieKV(env, "raport", { ...vechi, eroare: { ora: inceput, mesaj: String(e.message || e) } });
    throw e;
  }
}

function egalSigur(a, b) {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  let dif = ea.length ^ eb.length;
  for (let i = 0; i < Math.max(ea.length, eb.length); i++) dif |= (ea[i] ?? 0) ^ (eb[i] ?? 0);
  return dif === 0;
}

function autorizat(request, env) {
  const antet = request.headers.get("Authorization") || "";
  if (!antet.startsWith("Basic ")) return false;
  let parola = "";
  try {
    parola = atob(antet.slice(6)).split(":").slice(1).join(":");
  } catch {
    return false;
  }
  return egalSigur(parola, env.RAPORT_PAROLA);
}

const redirect = (url) => Response.redirect(new URL("/", url).toString(), 303);

export default {
  async scheduled(_eveniment, env, ctx) {
    ctx.waitUntil(ruleaza(env));
  },

  async fetch(request, env) {
    if (!env.RAPORT_PAROLA) {
      return new Response("Seteaza parola: npx wrangler secret put RAPORT_PAROLA", { status: 503 });
    }
    if (!autorizat(request, env)) {
      return new Response("Autentificare necesara", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Stoc lengo.ro", charset="UTF-8"' },
      });
    }
    const url = new URL(request.url);

    if (request.method === "POST") {
      const origine = request.headers.get("Origin");
      if (origine && origine !== url.origin) return new Response("Origine nepermisa", { status: 403 });

      if (url.pathname === "/ruleaza") {
        try {
          await ruleaza(env);
        } catch {
          // eroarea e salvata in raport si afisata pe pagina
        }
        return redirect(url);
      }
      if (url.pathname === "/aproba") {
        const formular = await request.formData();
        const sku = formular.get("sku");
        const [costuri, deAprobat] = await Promise.all([
          citesteKV(env, "costuri", {}),
          citesteKV(env, "deAprobat", {}),
        ]);
        for (const [s, cost] of Object.entries(deAprobat)) {
          if (sku === "*" || sku === s) {
            costuri[s] = cost; // noul cost devine referinta -> la urmatoarea rulare pretul se aplica
            delete deAprobat[s];
          }
        }
        await Promise.all([scrieKV(env, "costuri", costuri), scrieKV(env, "deAprobat", deAprobat)]);
        return redirect(url);
      }
      return new Response("Negasit", { status: 404 });
    }

    if (url.pathname === "/") {
      const [raport, deAprobat] = await Promise.all([
        citesteKV(env, "raport", null),
        citesteKV(env, "deAprobat", {}),
      ]);
      return new Response(paginaRaport(raport, deAprobat, citesteConfig(env)), {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
      });
    }
    return new Response("Negasit", { status: 404 });
  },
};
