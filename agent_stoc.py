#!/usr/bin/env python3
"""
Agent stoc: furnizor (API) -> website.

La fiecare X minute:
  1. citeste stocul de la furnizor prin API (implicit ELKO, api.elko.cloud);
  2. citeste produsele de pe website (WooCommerce / Shopify);
  3. compara dupa cod (SKU) si scrie un raport CSV cu diferentele;
  4. daca MOD=actualizare, pune stocul nou pe website.

Pornire:
    python agent_stoc.py            # ruleaza continuu
    python agent_stoc.py --o-data   # o singura verificare si iese
    python agent_stoc.py --test     # arata ce raspunde API-ul furnizorului (primele produse)
"""

import argparse
import base64
import csv
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_facturi import citeste_config

BAZA = Path(__file__).resolve().parent
log = logging.getLogger("agent_stoc")


# --------------------------------------------------------------------------
# HTTP (doar biblioteca standard, fara pachete in plus)
# --------------------------------------------------------------------------

def cere(metoda: str, url: str, headere: dict | None = None, corp=None, incercari: int = 3):
    """Cerere HTTP cu JSON. Intoarce (date_json, headere_raspuns). Reincearca la erori de retea / 429 / 5xx."""
    date = json.dumps(corp).encode() if corp is not None else None
    h = {"Accept": "application/json", "User-Agent": "agent-stoc/1.0", **(headere or {})}
    if date is not None:
        h["Content-Type"] = "application/json"
    for i in range(incercari):
        try:
            req = urllib.request.Request(url, data=date, headers=h, method=metoda)
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8-sig")
                return (json.loads(text) if text.strip() else None), dict(r.headers)
        except urllib.error.HTTPError as e:
            mesaj = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and i < incercari - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise RuntimeError(f"{metoda} {url} -> HTTP {e.code}: {mesaj}") from None
        except urllib.error.URLError as e:
            if i < incercari - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise RuntimeError(f"{metoda} {url} -> {e.reason}") from None


def ia_camp(obiect, cale: str):
    """ia_camp({'a': {'b': 5}}, 'a.b') -> 5. Cale goala = obiectul intreg."""
    for parte in filter(None, (cale or "").split(".")):
        if not isinstance(obiect, dict):
            return None
        obiect = obiect.get(parte)
    return obiect


def parseaza_stoc(valoare) -> int | None:
    """10 / '10' / '>10' / '10+' / '5-10' / 'da' -> numar intreg (None daca nu se intelege)."""
    if valoare is None:
        return None
    if isinstance(valoare, bool):
        return 1 if valoare else 0
    if isinstance(valoare, (int, float)):
        return max(int(valoare), 0)
    s = str(valoare).strip().lower()
    numere = re.findall(r"\d+", s)
    if numere:
        return int(numere[0])  # '>10' -> 10 (minimul sigur), '5-10' -> 5
    if s in ("da", "yes", "true", "in stoc", "in stock", "available"):
        return 1
    if s in ("", "nu", "no", "false", "0", "stoc epuizat", "out of stock"):
        return 0
    return None


# --------------------------------------------------------------------------
# Furnizor
# --------------------------------------------------------------------------

@dataclass
class ProdusFurnizor:
    cod: str
    stoc: int
    nume: str = ""


def _headere_furnizor(cfg: dict) -> dict:
    token = cfg.get("FURNIZOR_TOKEN", "")
    if not token:
        return {}
    header = cfg.get("FURNIZOR_HEADER_TOKEN", "Authorization")
    prefix = cfg.get("FURNIZOR_PREFIX_TOKEN", "Bearer")
    return {header: f"{prefix} {token}".strip()}


def descarca_furnizor(cfg: dict) -> list:
    """Toate inregistrarile brute de la furnizor (cu paginare daca e configurata)."""
    url = cfg["FURNIZOR_URL"]
    param_pagina = cfg.get("FURNIZOR_PARAM_PAGINA", "")
    lista = cfg.get("FURNIZOR_LISTA", "")
    headere = _headere_furnizor(cfg)

    toate, pagina = [], int(cfg.get("FURNIZOR_PRIMA_PAGINA", "1") or 1)
    while True:
        u = url
        if param_pagina:
            u += ("&" if "?" in u else "?") + urllib.parse.urlencode({param_pagina: pagina})
        date, _ = cere("GET", u, headere)
        bucata = ia_camp(date, lista) if lista else date
        if not isinstance(bucata, list):
            raise RuntimeError(f"Raspunsul furnizorului nu e o lista (verifica FURNIZOR_LISTA). "
                               f"Primit: {str(date)[:200]}")
        toate += bucata
        if not param_pagina or not bucata:
            return toate
        pagina += 1


def stoc_furnizor(cfg: dict, brute: list) -> dict:
    """{cod: ProdusFurnizor} din inregistrarile brute, dupa campurile din config."""
    camp_cod = cfg.get("FURNIZOR_CAMP_COD", "manufacturerCode")
    camp_stoc = cfg.get("FURNIZOR_CAMP_STOC", "quantity")
    camp_nume = cfg.get("FURNIZOR_CAMP_NUME", "name")
    rezultat, neclare = {}, 0
    for p in brute:
        cod = str(ia_camp(p, camp_cod) or "").strip()
        if not cod:
            continue
        stoc = parseaza_stoc(ia_camp(p, camp_stoc))
        if stoc is None:
            neclare += 1
            stoc = 0
        # acelasi cod de mai multe ori (depozite diferite) -> se aduna
        if cod.upper() in rezultat:
            rezultat[cod.upper()].stoc += stoc
        else:
            rezultat[cod.upper()] = ProdusFurnizor(cod, stoc, str(ia_camp(p, camp_nume) or ""))
    if neclare:
        log.warning("  %d produse cu stoc de neinteles la furnizor (considerate 0)", neclare)
    return rezultat


# --------------------------------------------------------------------------
# Website
# --------------------------------------------------------------------------

@dataclass
class ProdusSite:
    id: str
    sku: str
    nume: str
    stoc: int | None
    extra: dict  # ce ii trebuie platformei ca sa actualizeze (ex. inventory_item_id)


class WooCommerce:
    """WooCommerce REST API v3 (cheie + secret din WooCommerce > Setari > Avansat > REST API)."""

    def __init__(self, cfg: dict):
        self.url = cfg["SITE_URL"].rstrip("/") + "/wp-json/wc/v3"
        cheie = f"{cfg['SITE_CHEIE']}:{cfg['SITE_SECRET']}".encode()
        self.h = {"Authorization": "Basic " + base64.b64encode(cheie).decode()}

    def produse(self) -> list:
        rezultat, pagina = [], 1
        while True:
            date, _ = cere("GET", f"{self.url}/products?per_page=100&page={pagina}", self.h)
            if not date:
                return rezultat
            for p in date:
                rezultat.append(ProdusSite(str(p["id"]), p.get("sku") or "", p.get("name", ""),
                                           p.get("stock_quantity") if p.get("manage_stock") else None,
                                           {"parent": None}))
                if p.get("type") == "variable":
                    rezultat += self._variatii(p)
            pagina += 1

    def _variatii(self, p: dict) -> list:
        rezultat, pagina = [], 1
        while True:
            date, _ = cere("GET", f"{self.url}/products/{p['id']}/variations?per_page=100&page={pagina}",
                           self.h)
            if not date:
                return rezultat
            for v in date:
                rezultat.append(ProdusSite(str(v["id"]), v.get("sku") or "", p.get("name", ""),
                                           v.get("stock_quantity") if v.get("manage_stock") else None,
                                           {"parent": p["id"]}))
            pagina += 1

    def actualizeaza(self, modificari: list):
        """modificari: [(ProdusSite, stoc_nou)] - trimise in loturi de cate 100."""
        simple = [(p, s) for p, s in modificari if not p.extra.get("parent")]
        for i in range(0, len(simple), 100):
            lot = [{"id": int(p.id), "manage_stock": True, "stock_quantity": s}
                   for p, s in simple[i:i + 100]]
            cere("POST", f"{self.url}/products/batch", self.h, {"update": lot})
        variatii = {}
        for p, s in modificari:
            if p.extra.get("parent"):
                variatii.setdefault(p.extra["parent"], []).append(
                    {"id": int(p.id), "manage_stock": True, "stock_quantity": s})
        for parinte, lot in variatii.items():
            for i in range(0, len(lot), 100):
                cere("POST", f"{self.url}/products/{parinte}/variations/batch", self.h,
                     {"update": lot[i:i + 100]})


class Shopify:
    """Shopify Admin API (token de la o aplicatie custom cu drepturi read/write products + inventory)."""

    VERSIUNE = "2024-10"

    def __init__(self, cfg: dict):
        self.url = cfg["SITE_URL"].rstrip("/") + f"/admin/api/{self.VERSIUNE}"
        self.h = {"X-Shopify-Access-Token": cfg["SITE_CHEIE"]}
        self.locatie = cfg.get("SHOPIFY_LOCATIE", "")

    def _locatie(self) -> str:
        if not self.locatie:
            date, _ = cere("GET", f"{self.url}/locations.json", self.h)
            self.locatie = str(date["locations"][0]["id"])
        return self.locatie

    def produse(self) -> list:
        rezultat, url = [], f"{self.url}/products.json?limit=250&fields=id,title,variants"
        while url:
            date, headere = cere("GET", url, self.h)
            for p in date.get("products", []):
                for v in p.get("variants", []):
                    rezultat.append(ProdusSite(str(v["id"]), v.get("sku") or "", p.get("title", ""),
                                               v.get("inventory_quantity"),
                                               {"inventory_item_id": v["inventory_item_id"]}))
            urmator = re.search(r'<([^>]+)>;\s*rel="next"', headere.get("Link", "") or "")
            url = urmator.group(1) if urmator else None
        return rezultat

    def actualizeaza(self, modificari: list):
        locatie = int(self._locatie())
        for p, s in modificari:
            cere("POST", f"{self.url}/inventory_levels/set.json", self.h,
                 {"location_id": locatie, "inventory_item_id": p.extra["inventory_item_id"], "available": s})
            time.sleep(0.5)  # Shopify permite ~2 cereri/secunda


PLATFORME = {"woocommerce": WooCommerce, "shopify": Shopify}


# --------------------------------------------------------------------------
# Comparare
# --------------------------------------------------------------------------

@dataclass
class Linie:
    sku: str
    nume: str
    stoc_site: int | None
    stoc_furnizor: int | None
    stoc_nou: int | None
    actiune: str  # "actualizat" / "de actualizat" / "la fel" / "lipsa la furnizor" / "fara SKU"


def stoc_de_publicat(stoc: int, cfg: dict) -> int:
    """Scade rezerva de siguranta si limiteaza la maxim (ca sa nu vinzi ultimele bucati)."""
    stoc = max(stoc - int(cfg.get("STOC_REZERVA", "0") or 0), 0)
    maxim = int(cfg.get("STOC_MAXIM", "0") or 0)
    return min(stoc, maxim) if maxim else stoc


def compara(site: list, furnizor: dict, cfg: dict) -> tuple:
    """Intoarce (linii_raport, modificari[(ProdusSite, stoc_nou)])."""
    prefix = cfg.get("SKU_PREFIX", "")
    lipsa_zero = cfg.get("LIPSA_LA_FURNIZOR", "ignora").lower() == "zero"
    linii, modificari = [], []
    for p in site:
        if not p.sku:
            linii.append(Linie("", p.nume, p.stoc, None, None, "fara SKU"))
            continue
        cod = p.sku[len(prefix):] if prefix and p.sku.startswith(prefix) else p.sku
        f = furnizor.get(cod.strip().upper())
        if f is None:
            if lipsa_zero and p.stoc != 0:
                linii.append(Linie(p.sku, p.nume, p.stoc, None, 0, "de actualizat"))
                modificari.append((p, 0))
            else:
                linii.append(Linie(p.sku, p.nume, p.stoc, None, None, "lipsa la furnizor"))
            continue
        nou = stoc_de_publicat(f.stoc, cfg)
        if p.stoc == nou:
            linii.append(Linie(p.sku, p.nume, p.stoc, f.stoc, nou, "la fel"))
        else:
            linii.append(Linie(p.sku, p.nume, p.stoc, f.stoc, nou, "de actualizat"))
            modificari.append((p, nou))
    return linii, modificari


def scrie_raport(cale: Path, linii: list):
    ordine = {"de actualizat": 0, "actualizat": 0, "lipsa la furnizor": 1, "fara SKU": 2, "la fel": 3}
    with cale.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["SKU", "Produs", "Stoc site", "Stoc furnizor", "Stoc nou", "Actiune"])
        for l in sorted(linii, key=lambda x: (ordine.get(x.actiune, 9), x.sku)):
            w.writerow([l.sku, l.nume, "" if l.stoc_site is None else l.stoc_site,
                        "" if l.stoc_furnizor is None else l.stoc_furnizor,
                        "" if l.stoc_nou is None else l.stoc_nou, l.actiune])


# --------------------------------------------------------------------------
# Bucla agentului
# --------------------------------------------------------------------------

def o_verificare(cfg: dict, folder_rapoarte: Path):
    log.info("Citesc stocul de la furnizor...")
    furnizor = stoc_furnizor(cfg, descarca_furnizor(cfg))
    log.info("  %d coduri la furnizor", len(furnizor))

    platforma = cfg.get("SITE_PLATFORMA", "woocommerce").lower()
    site = PLATFORME[platforma](cfg)
    log.info("Citesc produsele de pe site (%s)...", platforma)
    produse = site.produse()
    log.info("  %d produse pe site", len(produse))

    linii, modificari = compara(produse, furnizor, cfg)
    actualizare = cfg.get("MOD", "verificare").lower() == "actualizare"
    if actualizare and modificari:
        site.actualizeaza(modificari)
        for l in linii:
            if l.actiune == "de actualizat":
                l.actiune = "actualizat"

    folder_rapoarte.mkdir(parents=True, exist_ok=True)
    raport = folder_rapoarte / f"stoc_{datetime.now():%Y-%m-%d_%H-%M}.csv"
    scrie_raport(raport, linii)

    numar = {a: sum(1 for l in linii if l.actiune == a)
             for a in ("la fel", "lipsa la furnizor", "fara SKU")}
    log.info("  %s: %d | la fel: %d | lipsa la furnizor: %d | fara SKU: %d",
             "ACTUALIZATE" if actualizare else "de actualizat (MOD=verificare, nu am modificat nimic)",
             len(modificari), numar["la fel"], numar["lipsa la furnizor"], numar["fara SKU"])
    log.info("  Raport: %s", raport)


def test_furnizor(cfg: dict):
    brute = descarca_furnizor(cfg)
    print(f"\nAm primit {len(brute)} produse. Primele 2, exact cum vin din API:\n")
    print(json.dumps(brute[:2], indent=2, ensure_ascii=False))
    produse = list(stoc_furnizor(cfg, brute).values())[:5]
    print("\nCum le intelege agentul (cu campurile din config_stoc.txt):")
    for p in produse:
        print(f"  cod={p.cod!r}  stoc={p.stoc}  nume={p.nume[:50]!r}")
    if not produse:
        print("  nimic - verifica FURNIZOR_CAMP_COD / FURNIZOR_CAMP_STOC dupa exemplul de mai sus")


def main():
    ap = argparse.ArgumentParser(description="Agent: stoc furnizor (API) -> website")
    ap.add_argument("--config", default=str(BAZA / "config_stoc.txt"))
    ap.add_argument("--o-data", action="store_true", help="o singura verificare si iesi")
    ap.add_argument("--test", action="store_true", help="arata raspunsul API-ului furnizorului")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(BAZA / "agent_stoc.log", encoding="utf-8")])

    cfg = citeste_config(Path(args.config))
    if args.test:
        test_furnizor(cfg)
        return

    folder = Path(cfg.get("FOLDER_RAPOARTE", "rapoarte_stoc"))
    if not folder.is_absolute():
        folder = BAZA / folder
    log.info("Agent stoc pornit. Mod: %s", cfg.get("MOD", "verificare"))
    while True:
        try:
            o_verificare(cfg, folder)
        except Exception as e:  # o eroare de retea nu trebuie sa opreasca agentul
            log.error("Verificare esuata: %s", e)
        if args.o_data:
            break
        time.sleep(float(cfg.get("INTERVAL_MINUTE", "60")) * 60)
        cfg = citeste_config(Path(args.config))  # poti schimba config-ul din mers


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Agent oprit.")
