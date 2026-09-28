// Pagina HTML de raport (o singura pagina, fara fisiere externe).

const esc = (x) =>
  String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const lei = (x) =>
  x == null ? "–" : x.toLocaleString("ro-RO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const nr = (x) => (x == null ? "–" : String(x));

const ora = (iso) =>
  iso ? new Date(iso).toLocaleString("ro-RO", { timeZone: "Europe/Bucharest", dateStyle: "short", timeStyle: "short" }) : "–";

function schimbare(vechi, nou, format) {
  if (nou == null || nou === vechi) return `<span>${format(vechi)}</span>`;
  return `<span class="vechi">${format(vechi)}</span> → <b>${format(nou)}</b>`;
}

function rand(l, deAprobat) {
  const clasa = l.alerta ? "alerta" : l.deModificat ? "modificat" : "";
  const buton = deAprobat[l.sku] != null
    ? `<form method="post" action="/aproba"><input type="hidden" name="sku" value="${esc(l.sku)}"><button>Aprobă noul cost</button></form>`
    : "";
  return `<tr class="${clasa}">
    <td><div class="nume">${esc(l.nume)}</div><div class="sku">${esc(l.sku || "fără SKU")}</div></td>
    <td class="num">${lei(l.cost)}</td>
    <td class="num">${l.adaos == null ? "–" : l.adaos + "%"}</td>
    <td class="num">${schimbare(l.pretSite, l.pretNou, lei)}</td>
    <td class="num">${nr(l.stocNod)}</td>
    <td class="num">${schimbare(l.stocSite, l.stocNou, nr)}</td>
    <td>${l.note.map(esc).join("<br>")}${buton}</td>
  </tr>`;
}

export function paginaRaport(raport, deAprobat, cfg) {
  const linii = [...(raport?.linii || [])].sort(
    (a, b) => Number(!!b.deModificat) - Number(!!a.deModificat) || b.note.length - a.note.length,
  );
  const nAprobare = Object.keys(deAprobat).length;
  const adaosuri = Object.entries(cfg.adaosuri).map(([c, p]) => `${esc(c)} ${p}%`).join(", ");

  return `<!doctype html>
<html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stoc lengo.ro</title>
<style>
  :root { --fundal:#f7f7f5; --card:#fff; --text:#1d1d1b; --slab:#6b6b66; --linie:#e4e3de;
          --accent:#1f6feb; --pe-accent:#fff; --verde:#e8f5ec; --rosu:#fdecea; --rosu-text:#a4251b; }
  @media (prefers-color-scheme: dark) {
    :root { --fundal:#161615; --card:#1f1f1e; --text:#ecebe6; --slab:#9b9a94; --linie:#33332f;
            --accent:#6ea8ff; --pe-accent:#0b1f3d; --verde:#17301f; --rosu:#3a1c19; --rosu-text:#ff9d93; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--fundal); color:var(--text);
         font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width:1150px; margin:0 auto; padding:24px 16px 48px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--slab); margin:0 0 20px; }
  .carduri { display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--linie); border-radius:10px; padding:12px 14px; }
  .card b { display:block; font-size:22px; font-variant-numeric:tabular-nums; }
  .card span { color:var(--slab); font-size:13px; }
  .eroare { background:var(--rosu); color:var(--rosu-text); border-radius:10px; padding:12px 14px; margin-bottom:16px; }
  .actiuni { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }
  button { font:inherit; border:1px solid var(--accent); background:var(--accent); color:var(--pe-accent);
           border-radius:8px; padding:6px 12px; cursor:pointer; }
  td button { margin-top:6px; font-size:13px; padding:4px 10px; }
  .tabel { overflow-x:auto; background:var(--card); border:1px solid var(--linie); border-radius:10px; }
  table { border-collapse:collapse; width:100%; min-width:760px; }
  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--linie); vertical-align:top; }
  th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--slab); font-weight:600; }
  .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .sku { color:var(--slab); font-size:12px; }
  .vechi { color:var(--slab); text-decoration:line-through; }
  tr.modificat { background:var(--verde); }
  tr.alerta { background:var(--rosu); }
  tr:last-child td { border-bottom:0; }
  .reguli { color:var(--slab); font-size:13px; margin-top:16px; }
</style></head>
<body><main>
  <h1>Stoc și prețuri lengo.ro</h1>
  <p class="sub">Ultima verificare: ${ora(raport?.ora)} · mod <b>${esc(raport?.mod || (cfg.actualizare ? "actualizare" : "verificare"))}</b>
    ${raport?.mod === "verificare" ? " (doar raport, nu am schimbat nimic pe site)" : ""}</p>

  ${raport?.eroare ? `<div class="eroare"><b>Eroare la ${ora(raport.eroare.ora)}:</b> ${esc(raport.eroare.mesaj)}</div>` : ""}

  <div class="carduri">
    <div class="card"><b>${nr(raport?.produseSite)}</b><span>produse pe site</span></div>
    <div class="card"><b>${nr(raport?.coduriNod)}</b><span>coduri la NOD</span></div>
    <div class="card"><b>${nr(raport?.modificari)}</b><span>${raport?.aplicate ? "modificări aplicate" : "modificări propuse"}</span></div>
    <div class="card"><b>${nAprobare}</b><span>prețuri de aprobat</span></div>
  </div>

  <div class="actiuni">
    <form method="post" action="/ruleaza"><button>Verifică acum</button></form>
    ${nAprobare ? `<form method="post" action="/aproba"><input type="hidden" name="sku" value="*"><button>Aprobă toate (${nAprobare})</button></form>` : ""}
  </div>

  ${linii.length ? `<div class="tabel"><table>
    <thead><tr><th>Produs</th><th class="num">Cost NOD</th><th class="num">Adaos</th>
      <th class="num">Preț site (cu TVA)</th><th class="num">Stoc NOD</th><th class="num">Stoc site</th><th>Observații</th></tr></thead>
    <tbody>${linii.map((l) => rand(l, deAprobat)).join("")}</tbody>
  </table></div>` : `<p>Nicio verificare încă. Apasă „Verifică acum”.</p>`}

  <p class="reguli">Reguli: adaos ${adaosuri}, restul ${cfg.adaosImplicit}% · marjă minimă ${cfg.marjaMinima} lei ·
    TVA ${cfg.tva}% · ${cfg.rotunjire99 ? "rotunjire la ,99 · " : ""}frână la salturi de cost peste ${cfg.pragSalt}%
    ${cfg.stocRezerva ? ` · rezervă ${cfg.stocRezerva} buc.` : ""}${cfg.stocMaxim ? ` · stoc maxim afișat ${cfg.stocMaxim}` : ""}</p>
</main></body></html>`;
}
