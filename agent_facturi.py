#!/usr/bin/env python3
"""
Agent facturi din extrase bancare.

Pui un extras PDF in folder (implicit ./extrase). Agentul cauta in el toate
incasarile de la firmele din firme.txt si scrie langa extras factura in formatul
Grow LLC (o factura pe luna, ex. Invoice_GROW202608.pdf) + un CSV cu platile gasite.

Pornire:
    Genereaza-Factura.ps1              # o data: proceseaza extrasele si iese
    python agent_facturi.py --o-data   # acelasi lucru, direct
    python agent_facturi.py            # ruleaza continuu si asteapta extrase
"""

import argparse
import csv
import json
import logging
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path

BAZA = Path(__file__).resolve().parent
EXTENSII = {".pdf"}
DOI_ZECIMALE = Decimal("0.01")

log = logging.getLogger("agent_facturi")


# --------------------------------------------------------------------------
# Configurare
# --------------------------------------------------------------------------

def citeste_config(cale: Path) -> dict:
    """config.txt: linii CHEIE=valoare, '#' = comentariu."""
    cfg = {}
    for linie in cale.read_text(encoding="utf-8-sig").splitlines():
        linie = linie.strip()
        if not linie or linie.startswith("#") or "=" not in linie:
            continue
        cheie, valoare = linie.split("=", 1)
        cfg[cheie.strip().upper()] = valoare.strip()
    return cfg


@dataclass
class Firma:
    nume: str
    cui: str = ""
    adresa: str = ""          # randurile adresei, separate prin ';'
    cuvinte_cheie: tuple = ()
    reg_com: str = ""


def citeste_firme(cale: Path) -> list:
    """
    firme.txt: cate un bloc CHEIE=valoare pentru fiecare firma; fiecare bloc incepe cu NUME=.
        NUME=NET COMMUNICATIONS SYSTEMS SRL
        CUI=RO34291656
        REG_COM=J40/3729/2015
        ADRESA=Str. ... ; Sector 5, Bucharest 077160, Romania
        ALIAS=NET COMMUNICATION SYSTEMS, NETCOM
    """
    firme = []
    if not cale.exists():
        return firme
    for linie in cale.read_text(encoding="utf-8-sig").splitlines():
        linie = linie.strip()
        if not linie or linie.startswith("#") or "=" not in linie:
            continue
        cheie, valoare = (x.strip() for x in linie.split("=", 1))
        cheie = cheie.upper()
        if cheie == "NUME":
            firme.append(Firma(valoare))
        elif not firme:
            continue
        elif cheie == "CUI":
            firme[-1].cui = valoare
        elif cheie == "REG_COM":
            firme[-1].reg_com = valoare
        elif cheie == "ADRESA":
            firme[-1].adresa = valoare
        elif cheie == "ALIAS":
            firme[-1].cuvinte_cheie = tuple(c.strip() for c in valoare.split(",") if c.strip())
    return firme


# --------------------------------------------------------------------------
# Normalizare text / numere / date
# --------------------------------------------------------------------------

FORME_JURIDICE = {"srl", "sa", "pfa", "ii", "if", "snc", "scs", "sca", "ra", "srld", "ong"}


def normalizeaza(text: str) -> str:
    """'S.C. Alfa-Tech S.R.L.' -> 'alfa tech' (fara diacritice si forma juridica)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"\bs\.?\s?c\.?\s", " ", text)
    text = re.sub(r"\bs\.\s?r\.\s?l\.?", " srl ", text)
    text = re.sub(r"\bs\.\s?a\.", " sa ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(t for t in text.split() if t not in FORME_JURIDICE)


def doar_cifre(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def parseaza_suma(text) -> Decimal | None:
    """Accepta 1.234,56 / 1,234.56 / 1234.56 / -1 234,56 / 1234,56 RON."""
    if text is None:
        return None
    if isinstance(text, (int, float, Decimal)):
        return Decimal(str(text))
    s = str(text).strip().replace("\u00a0", "").replace(" ", "")
    s = re.sub(r"[A-Za-z]", "", s)
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


FORMATE_DATA = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%y", "%Y/%m/%d")
RE_DATA = re.compile(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
# "31 August 2026", "31 aug. 2026", "Aug 31, 2026" (Wise, Revolut, extrase in engleza/romana)
RE_DATA_TEXT = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,10})\.?,?\s+(\d{4})\b"
                          r"|\b([A-Za-z]{3,10})\.?\s+(\d{1,2}),?\s+(\d{4})\b")
LUNI = {"jan": 1, "ian": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "iun": 6,
        "jul": 7, "iul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
LUNI_EN = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def _data_din_text(m) -> date | None:
    zi, luna, an = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(5), m.group(4), m.group(6))
    nr_luna = LUNI.get(luna[:3].lower())
    try:
        return date(int(an), nr_luna, int(zi)) if nr_luna else None
    except ValueError:
        return None


def parseaza_data(text) -> date | None:
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    if not text:
        return None
    text = str(text)
    candidati = []
    m = RE_DATA.search(text)
    if m:
        for fmt in FORMATE_DATA:
            try:
                candidati.append((m.start(), datetime.strptime(m.group(1), fmt).date()))
                break
            except ValueError:
                continue
    for m in RE_DATA_TEXT.finditer(text):
        d = _data_din_text(m)
        if d:
            candidati.append((m.start(), d))
            break
    return min(candidati)[1] if candidati else None


def incepe_cu_data(text: str) -> bool:
    text = text.strip()
    if RE_DATA.match(text):
        return True
    m = RE_DATA_TEXT.match(text)
    return bool(m and _data_din_text(m))


def sterge_date(text: str) -> str:
    return RE_DATA_TEXT.sub(" ", RE_DATA.sub(" ", text))


def rotunjeste(x: Decimal) -> Decimal:
    return x.quantize(DOI_ZECIMALE, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Citire extrase
# --------------------------------------------------------------------------

@dataclass
class Tranzactie:
    data: date
    descriere: str
    suma: Decimal        # mereu pozitiva
    directie: str        # "incasare" (bani primiti) sau "plata" (bani trimisi)
    data_provizorie: bool = False  # data luata de la tranzactia de deasupra


CHEI_DATA = ("data", "date")
CHEI_DEBIT = ("debit", "iesiri", "plati", "outgoing", "withdraw")
CHEI_CREDIT = ("credit", "intrari", "incasari", "incoming", "deposit")
BIGRAME_DEBIT = ("paid out", "money out")
BIGRAME_CREDIT = ("paid in", "money in")
CHEI_SUMA = ("suma", "amount", "valoare")
CHEI_IGNORATE = ("sold", "balance", "referinta", "valuta", "currency", "moneda")


def _cauta_header(randuri: list) -> int | None:
    """Primul rand care arata a cap de tabel (are 'data' + debit/credit/suma)."""
    for i, rand in enumerate(randuri[:60]):
        celule = [normalizeaza(str(c)) for c in rand if c is not None]
        are_data = any(c.startswith(CHEI_DATA) for c in celule)
        are_suma = any(any(k in c for k in CHEI_DEBIT + CHEI_CREDIT + CHEI_SUMA) for c in celule)
        if are_data and are_suma:
            return i
    return None


def tranzactii_din_tabel(randuri: list) -> list:
    """Transforma un tabel (lista de randuri) in tranzactii, ghicind coloanele."""
    idx = _cauta_header(randuri)
    if idx is None:
        return []
    header = [normalizeaza(str(c or "")) for c in randuri[idx]]

    col_data = next((i for i, h in enumerate(header) if h.startswith(CHEI_DATA)), None)
    col_debit = next((i for i, h in enumerate(header) if any(k in h for k in CHEI_DEBIT)), None)
    col_credit = next((i for i, h in enumerate(header) if any(k in h for k in CHEI_CREDIT)), None)
    col_suma = next((i for i, h in enumerate(header)
                     if any(k in h for k in CHEI_SUMA) and i not in (col_debit, col_credit)), None)
    coloane_numerice = {col_data, col_debit, col_credit, col_suma}
    col_text = [i for i, h in enumerate(header)
                if i not in coloane_numerice and not any(k in h for k in CHEI_IGNORATE)
                and not h.startswith(CHEI_DATA)]

    rezultat = []
    curenta = None
    for rand in randuri[idx + 1:]:
        rand = list(rand) + [None] * (len(header) - len(rand))
        d = parseaza_data(rand[col_data]) if col_data is not None else None
        text = " ".join(str(rand[i]).strip() for i in col_text if rand[i] not in (None, ""))

        debit = parseaza_suma(rand[col_debit]) if col_debit is not None else None
        credit = parseaza_suma(rand[col_credit]) if col_credit is not None else None
        suma = parseaza_suma(rand[col_suma]) if col_suma is not None else None

        if d is None:
            # rand de continuare (unele banci pun detaliile pe mai multe randuri)
            if curenta is not None and text:
                curenta.descriere += " " + text
            continue

        if credit:
            curenta = Tranzactie(d, text, abs(credit), "incasare")
        elif debit:
            curenta = Tranzactie(d, text, abs(debit), "plata")
        elif suma:
            curenta = Tranzactie(d, text, abs(suma), "incasare" if suma > 0 else "plata")
        else:
            curenta = None
            continue
        rezultat.append(curenta)
    return rezultat


RE_SUMA = re.compile(r"^-?\d{1,3}(?:[.\s]\d{3})*,\d{2}$|^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+[.,]\d{2}$")
RE_SUMA_TEXT = re.compile(r"-?\d{1,3}(?:[.\s]\d{3})*,\d{2}\b|-?\d+\.\d{2}\b")
CUVINTE_INCASARE = ("incasare", "credit", "primit", "incoming", "transfer in", "received")
# randuri de total/sold din extras care NU sunt tranzactii (au sume in coloane, dar sunt totaluri)
CUVINTE_TOTAL = ("sold", "rulaj", "total", "sume blocate", "balance", "opening", "closing")


def _linii_din_cuvinte(cuvinte: list, toleranta: float = 3) -> list:
    """Grupeaza cuvintele PDF in linii (acelasi 'top'), sortate stanga -> dreapta."""
    linii = []
    for c in sorted(cuvinte, key=lambda w: (round(w["top"]), w["x0"])):
        if linii and abs(linii[-1][0]["top"] - c["top"]) <= toleranta:
            linii[-1].append(c)
        else:
            linii.append([c])
    linii = [sorted(l, key=lambda w: w["x0"]) for l in linii]
    # lipeste sumele scrise cu spatiu la mii: "2 420,00" -> "2420,00"
    for linie in linii:
        i = 0
        while i < len(linie) - 1:
            a, b = linie[i], linie[i + 1]
            if (re.fullmatch(r"-?\d{1,3}", a["text"]) and re.fullmatch(r"\d{3}([.,]\d{2})?", b["text"])
                    and b["x0"] - a["x1"] < 5):
                linie[i] = {**a, "text": a["text"] + b["text"], "x1": b["x1"]}
                del linie[i + 1]
            else:
                i += 1
    return linii


def _gaseste_coloane(linie: list) -> dict | None:
    """Daca linia e capul de tabel, intoarce pozitia (centrul x) coloanelor numerice."""
    coloane = {}
    for i, w in enumerate(linie):
        n = normalizeaza(w["text"])
        centru = (w["x0"] + w["x1"]) / 2
        urm = linie[i + 1] if i + 1 < len(linie) else None
        if urm and urm["x0"] - w["x1"] < 8:
            bigrama = f"{n} {normalizeaza(urm['text'])}"
            centru2 = (w["x0"] + urm["x1"]) / 2
            if bigrama in BIGRAME_DEBIT:
                coloane.setdefault("debit", centru2)
                continue
            if bigrama in BIGRAME_CREDIT:
                coloane.setdefault("credit", centru2)
                continue
        if n in ("in", "out"):
            continue  # a doua jumatate a unei bigrame
        if n.startswith(CHEI_DEBIT):
            coloane.setdefault("debit", centru)
        elif n.startswith(CHEI_CREDIT):
            coloane.setdefault("credit", centru)
        elif n.startswith(("sold", "balance")):
            coloane.setdefault("sold", centru)
        elif n.startswith(CHEI_SUMA):
            coloane.setdefault("suma", centru)
    if any(RE_SUMA.match(w["text"]) for w in linie):
        return None  # un cap de tabel nu contine sume
    if "debit" in coloane and "credit" in coloane:
        if "suma" in coloane:  # cu Debit + Credit separate, coloana "Amount" e soldul
            coloane.setdefault("sold", coloane.pop("suma"))
        return coloane
    if "suma" in coloane and any(normalizeaza(w["text"]).startswith(CHEI_DATA) for w in linie):
        return coloane
    return None


def _tranzactii_pe_coloane(pagini: list) -> list:
    """
    Citire dupa pozitia pe pagina - merge pe extrasele fara chenare (BT, BCR, ING...).
    Ca un om care pune rigla pe capul de tabel: o suma aflata sub 'Credit' e incasare,
    una sub 'Debit' e plata, una sub 'Sold' se ignora.
    """
    tranzactii, coloane, curenta, data_curenta = [], None, None, None
    for cuvinte in pagini:
        for linie in _linii_din_cuvinte(cuvinte):
            gasite = _gaseste_coloane(linie)
            if gasite:
                coloane, curenta = gasite, None
                continue
            if not coloane:
                continue

            prag_stanga = min(coloane.values()) - 60  # sumele din descriere nu conteaza
            sume, text = {}, []
            d, n = _data_la_inceput(linie)
            for w in linie[n:]:
                centru = (w["x0"] + w["x1"]) / 2
                if RE_SUMA.match(w["text"]) and centru >= prag_stanga:
                    col = min(coloane, key=lambda k: abs(coloane[k] - centru))
                    sume[col] = parseaza_suma(w["text"])
                elif not (RE_DATA.fullmatch(w["text"]) and not text):
                    text.append(w["text"])
            text = " ".join(text)

            if normalizeaza(text).startswith(CUVINTE_TOTAL):
                curenta = None  # sold / rulaj / total: nu e tranzactie
                continue

            suma, directie = None, None
            if sume.get("credit"):
                suma, directie = sume["credit"], "incasare"
            elif sume.get("debit"):
                suma, directie = sume["debit"], "plata"
            elif sume.get("suma"):
                suma, directie = sume["suma"], "incasare" if sume["suma"] > 0 else "plata"

            if suma is not None:
                curenta = Tranzactie(d or data_curenta, text, abs(suma), directie,
                                     data_provizorie=d is None)
                tranzactii.append(curenta)
            elif curenta is not None:
                if d and curenta.data_provizorie:
                    # data e pe randul de sub descriere (ex. Wise: "31 August 2026 Transaction: ...")
                    curenta.data, curenta.data_provizorie = d, False
                if text:
                    curenta.descriere += " " + text  # detaliile de pe randurile urmatoare
            if d:
                data_curenta = d
    return tranzactii


def _data_la_inceput(linie: list) -> tuple:
    """(data, cate cuvinte ocupa) daca linia incepe cu o data: '02.09.2026' sau '31 August 2026'."""
    if RE_DATA.fullmatch(linie[0]["text"]):
        return parseaza_data(linie[0]["text"]), 1
    trei = " ".join(w["text"] for w in linie[:3])
    m = RE_DATA_TEXT.match(trei)
    if m and _data_din_text(m) and len(linie) >= 3:
        return _data_din_text(m), 3
    return None, 0


def _tranzactii_din_text(linii: list) -> list:
    """Ultima varianta: o linie care incepe cu o data deschide o tranzactie."""
    tranzactii, blocuri = [], []
    for linie in linii:
        if incepe_cu_data(linie):
            blocuri.append([linie.strip()])
        elif blocuri:
            blocuri[-1].append(linie.strip())
    for bloc in blocuri:
        text = " ".join(bloc)
        if normalizeaza(sterge_date(bloc[0])).startswith(CUVINTE_TOTAL):
            continue
        fara_date = [sterge_date(x) for x in (bloc[0], text)]  # "01.09" nu e suma
        sume = RE_SUMA_TEXT.findall(fara_date[0]) or RE_SUMA_TEXT.findall(fara_date[1])
        if not sume:
            continue
        suma = parseaza_suma(sume[0])
        directie = "incasare" if any(k in normalizeaza(text) for k in CUVINTE_INCASARE) else "plata"
        tranzactii.append(Tranzactie(parseaza_data(bloc[0]), text, abs(suma), directie))
    return tranzactii


def citeste_extras(cale: Path) -> list:
    """
    Citeste un extras PDF, in 3 trepte (se opreste la prima care gaseste ceva):
      1. tabel cu chenare
      2. coloane dupa pozitie (extrasele obisnuite, fara chenare)
      3. text simplu, linie cu linie
    """
    import pdfplumber
    tabel_total, pagini, linii = [], [], []
    with pdfplumber.open(cale) as pdf:
        for pagina in pdf.pages:
            for tabel in pagina.extract_tables() or []:
                tabel_total += tabel
            pagini.append(pagina.extract_words(keep_blank_chars=False, x_tolerance=1.5))
            linii += (pagina.extract_text() or "").splitlines()

    if not any(pagini):
        log.warning("  PDF-ul nu are text (e scanat/poza). Descarca extrasul din internet banking, nu scanat.")
        return []

    for metoda, citire in (("tabel", lambda: tranzactii_din_tabel(tabel_total)),
                           ("coloane", lambda: _tranzactii_pe_coloane(pagini)),
                           ("text", lambda: _tranzactii_din_text(linii))):
        tranzactii = citire()
        if tranzactii:
            log.info("  citit prin metoda: %s", metoda)
            return tranzactii
    return []


# --------------------------------------------------------------------------
# Potrivire firma
# --------------------------------------------------------------------------

def e_a_firmei(tr: Tranzactie, firma: Firma) -> bool:
    desc = f" {normalizeaza(tr.descriere)} "
    nume = normalizeaza(firma.nume)
    if nume and f" {nume} " in desc:
        return True
    cui = doar_cifre(firma.cui)
    if len(cui) >= 4 and cui in doar_cifre(tr.descriere):
        return True
    return any(f" {normalizeaza(k)} " in desc for k in firma.cuvinte_cheie if normalizeaza(k))


def filtreaza(tranzactii: list, firma: Firma, directie: str) -> list:
    return [t for t in tranzactii
            if e_a_firmei(t, firma) and (directie == "toate" or t.directie == directie)]


# --------------------------------------------------------------------------
# Factura PDF
# --------------------------------------------------------------------------

def format_en(x: Decimal) -> str:
    """7900 -> '7,900.00' (ca pe factura Grow LLC)"""
    return f"{rotunjeste(x):,.2f}"


def data_en(d: date) -> str:
    return f"{LUNI_EN[d.month - 1]} {d.day}, {d.year}"


def perioada_serviciu(t: Tranzactie, cfg: dict) -> tuple:
    """Luna facturata pentru o plata: luna platii sau (LUNA_SERVICIU=anterioara) luna dinainte."""
    an, luna = t.data.year, t.data.month
    if cfg.get("LUNA_SERVICIU", "aceeasi").lower().startswith("anterio"):
        an, luna = (an - 1, 12) if luna == 1 else (an, luna - 1)
    return an, luna


def ultima_zi(an: int, luna: int) -> date:
    urm = date(an + 1, 1, 1) if luna == 12 else date(an, luna + 1, 1)
    return date.fromordinal(urm.toordinal() - 1)


def _ascii_sigur(text: str) -> str:
    """Helvetica (fontul facturii Grow) nu are ș/ț/ă: le scriem fara diacritice."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))


def genereaza_factura(cale_pdf: Path, firma: Firma, linii: list, cfg: dict,
                      numar: str, data_factura: date) -> Decimal:
    """
    Factura in formatul Grow LLC (A4, Helvetica, albastru #0F4C81).
    linii = [(descriere, perioada, suma), ...]
    """
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    W, H = A4
    ALBASTRU, NEGRU = HexColor("#0F4C81"), HexColor("#1A1A1A")
    GRI, LINIE, FUNDAL = HexColor("#5F6368"), HexColor("#D5D5D5"), HexColor("#F2F5F8")
    moneda = cfg.get("MONEDA", "EUR")
    furnizor = cfg.get("FURNIZOR_NUME", "Grow LLC")
    zile = int(cfg.get("ZILE_SCADENTA", "0") or 0)
    scadenta = date.fromordinal(data_factura.toordinal() + zile)
    total = sum((s for _, _, s in linii), Decimal(0))

    c = canvas.Canvas(str(cale_pdf), pagesize=A4)
    c.setTitle(f"Invoice {numar}")
    c.setAuthor(furnizor)

    def scrie(x, top, text, marime=9.5, bold=False, culoare=NEGRU, dreapta=False):
        # 'top' = distanta de la marginea de sus a paginii pana la varful textului
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, marime)
        c.setFillColor(culoare)
        text = _ascii_sigur(text)
        y = H - top - marime * 0.793
        (c.drawRightString if dreapta else c.drawString)(x, y, text)
        return stringWidth(text, font, marime)

    def dreptunghi(x, top, lat, inalt, culoare):
        c.setFillColor(culoare)
        c.rect(x, H - top - inalt, lat, inalt, stroke=0, fill=1)

    def linie_oriz(x0, x1, top, culoare, grosime):
        c.setStrokeColor(culoare)
        c.setLineWidth(grosime)
        c.line(x0, H - top, x1, H - top)

    def eticheta_valoare(top, eticheta, valoare):
        lat = stringWidth(_ascii_sigur(valoare), "Helvetica-Bold", 9)
        scrie(539, top, valoare, 9, bold=True, dreapta=True)
        scrie(539 - lat, top, eticheta + " ", 9, culoare=GRI, dreapta=True)

    # --- antet
    scrie(57, 65, furnizor, 22, bold=True, culoare=ALBASTRU)
    scrie(539, 64, "INVOICE", 17, bold=True, dreapta=True)
    scrie(57, 91, cfg.get("FURNIZOR_LOCALITATE", ""), 9.5, culoare=GRI)
    eticheta_valoare(91, "No.", numar)
    eticheta_valoare(104, "Issue date:", data_en(data_factura))
    eticheta_valoare(117, "Due date:", data_en(scadenta))
    dreptunghi(57, 148, 482, 1, ALBASTRU)

    # --- de la / catre
    scrie(57, 173, "INVOICE FROM", 7.5, bold=True, culoare=GRI)
    scrie(57, 191, furnizor, 11, bold=True)
    for i, rand in enumerate(r.strip() for r in cfg.get("FURNIZOR_RANDURI", "").split("|") if r.strip()):
        scrie(57, 205 + i * 13.7, rand, culoare=GRI)

    randuri_client = [r.strip() for r in firma.adresa.split(";") if r.strip()]
    if firma.cui:
        randuri_client.append(f"VAT / CUI: {firma.cui}")
    if firma.reg_com:
        randuri_client.append(f"Trade Register: {firma.reg_com}")
    scrie(320, 173, "INVOICE TO", 7.5, bold=True, culoare=GRI)
    scrie(320, 191, firma.nume, 11, bold=True)
    for i, rand in enumerate(randuri_client):
        scrie(320, 205 + i * 13.7, rand, culoare=GRI)
    jos = max(len(randuri_client), 4) - 4  # o adresa mai lunga impinge tabelul in jos
    d = jos * 13.7

    # --- tabel
    dreptunghi(57, 289 + d, 482, 20, ALBASTRU)
    scrie(63, 295 + d, "DESCRIPTION", 8.5, bold=True, culoare=white)
    scrie(304, 295 + d, "PERIOD", 8.5, bold=True, culoare=white)
    scrie(533, 295 + d, f"AMOUNT ({moneda})", 8.5, bold=True, culoare=white, dreapta=True)
    for i, (descriere, perioada, suma) in enumerate(linii):
        top = 318 + d + i * 24
        scrie(63, top, descriere)
        scrie(304, top, perioada)
        scrie(533, top, format_en(suma), dreapta=True)
        linie_oriz(57, 539, top + 18, LINIE, 0.5)
    d += (len(linii) - 1) * 24

    # --- totaluri
    scrie(269, 355 + d, "Subtotal")
    scrie(527, 355 + d, f"{format_en(total)} {moneda}", dreapta=True)
    dreptunghi(263, 371 + d, 270, 26, FUNDAL)
    linie_oriz(263, 533, 371 + d, NEGRU, 0.8)
    scrie(269, 379 + d, "Total", 12, bold=True)
    scrie(527, 379 + d, f"{format_en(total)} {moneda}", 12, bold=True, dreapta=True)

    # --- plata
    scrie(63, 433 + d, "PAYMENT DETAILS", 7.5, bold=True, culoare=GRI)
    scrie(63, 450 + d, f"Beneficiary: {cfg.get('BENEFICIAR', furnizor)}")
    scrie(63, 463 + d, f"IBAN: {cfg.get('IBAN', '')}  •  SWIFT/BIC: {cfg.get('SWIFT', '')}"
                       f"  •  Currency: {moneda}")
    scrie(63, 485 + d, f"Payment terms: bank transfer, {moneda}. "
                       f"Please quote invoice number {numar} as reference.")
    scrie(63, 538 + d, cfg.get("SUBSOL", furnizor), 8, bold=True)

    c.showPage()
    c.save()
    return total


def scrie_rezumat(cale_csv: Path, tranzactii: list):
    with cale_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data", "Directie", "Suma", "Descriere din extras"])
        for t in sorted(tranzactii, key=lambda x: x.data or date.min):
            w.writerow([t.data.strftime("%d.%m.%Y") if t.data else "", t.directie,
                        format_en(t.suma), t.descriere])


# --------------------------------------------------------------------------
# Stare (ce extrase au fost deja procesate)
# --------------------------------------------------------------------------

class Stare:
    def __init__(self, cale: Path):
        self.cale = cale
        self.date = {"procesate": {}}
        if cale.exists():
            self.date.update(json.loads(cale.read_text(encoding="utf-8")))

    def salveaza(self):
        self.cale.write_text(json.dumps(self.date, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def amprenta(cale: Path) -> str:
        s = cale.stat()
        return f"{s.st_size}-{int(s.st_mtime)}"

    def e_procesat(self, cale: Path) -> bool:
        return self.date["procesate"].get(cale.name) == self.amprenta(cale)

    def marcheaza(self, cale: Path):
        self.date["procesate"][cale.name] = self.amprenta(cale)


# --------------------------------------------------------------------------
# Bucla agentului
# --------------------------------------------------------------------------

def facturi_pe_luni(gasite: list, cfg: dict) -> dict:
    """{(an, luna): [tranzactii]} - o factura pe luna, ca GROW-2026-08."""
    luni = {}
    for t in gasite:
        if t.data is None:
            log.warning("  Plata fara data, sarita: %s %s", format_en(t.suma), t.descriere[:60])
            continue
        luni.setdefault(perioada_serviciu(t, cfg), []).append(t)
    return dict(sorted(luni.items()))


def proceseaza_extras(cale: Path, firme: list, cfg: dict, stare: Stare) -> list:
    """Intoarce lista facturilor create."""
    log.info("Extras: %s", cale.name)
    try:
        tranzactii = citeste_extras(cale)
    except Exception as e:  # un extras stricat nu trebuie sa opreasca agentul
        log.error("Nu am putut citi %s: %s", cale.name, e)
        return []
    log.info("  %d tranzactii citite", len(tranzactii))
    if not tranzactii:
        log.warning("  Nu am recunoscut nicio tranzactie. Trimite-mi PDF-ul ca sa adaug formatul bancii tale.")

    directie = cfg.get("DIRECTIE", "incasare").lower()
    sablon_numar = cfg.get("NUMAR_FACTURA", "GROW-{an}-{luna}")
    create = []
    for firma in firme:
        gasite = filtreaza(tranzactii, firma, directie)
        if not gasite:
            log.info("  %s: nicio tranzactie (%s)", firma.nume, directie)
            continue
        for (an, luna), plati in facturi_pe_luni(gasite, cfg).items():
            numar = sablon_numar.format(an=an, luna=f"{luna:02d}")
            cale_pdf = cale.parent / f"Invoice_{re.sub(r'[^A-Za-z0-9]', '', numar)}.pdf"
            if cale_pdf.exists():
                log.warning("  %s exista deja - nu o suprascriu. Sterge-o daca vrei s-o refaci.",
                            cale_pdf.name)
                continue
            if cfg.get("DATA_FACTURA", "sfarsit_luna").lower() == "azi":
                data_factura = date.today()
            else:
                data_factura = ultima_zi(an, luna)
            linie = (cfg.get("DESCRIERE_LINIE", "IT services & marketing services"),
                     f"{LUNI_EN[luna - 1]} {an}", sum((t.suma for t in plati), Decimal(0)))
            total = genereaza_factura(cale_pdf, firma, [linie], cfg, numar, data_factura)
            scrie_rezumat(cale_pdf.with_name(cale_pdf.stem + "_plati.csv"), plati)
            log.info("  %s: %d plati -> %s (total %s %s)", firma.nume, len(plati), cale_pdf.name,
                     format_en(total), cfg.get("MONEDA", "EUR"))
            create.append(cale_pdf)
    stare.marcheaza(cale)
    stare.salveaza()
    return create


def extrase_noi(folder: Path, stare: Stare) -> list:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTENSII
                  and not p.name.startswith(("Invoice_", "Factura_", "~$", "."))
                  and not stare.e_procesat(p))


def main():
    ap = argparse.ArgumentParser(description="Agent: extrase bancare PDF -> facturi Grow LLC")
    ap.add_argument("--folder", help="folderul urmarit (implicit: FOLDER din config.txt)")
    ap.add_argument("--config", default=str(BAZA / "config.txt"))
    ap.add_argument("--firme", default=str(BAZA / "firme.txt"))
    ap.add_argument("--o-data", action="store_true", help="proceseaza o data si iesi")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(BAZA / "agent.log", encoding="utf-8")])

    for cale in (Path(args.config), Path(args.firme)):
        model = cale.with_name(cale.stem + ".exemplu" + cale.suffix)
        if not cale.exists() and model.exists():
            shutil.copy(model, cale)
            log.warning("Am creat %s din model - completeaza-l cu datele tale reale.", cale.name)

    cfg = citeste_config(Path(args.config))
    folder = Path(args.folder or cfg.get("FOLDER", "extrase"))
    if not folder.is_absolute():
        folder = BAZA / folder
    folder.mkdir(parents=True, exist_ok=True)
    stare = Stare(folder / ".stare_agent.json")
    interval = float(cfg.get("INTERVAL_SECUNDE", "5"))

    log.info("Agent pornit. Urmaresc folderul: %s", folder)
    create = []
    marimi = {}  # asteptam ca fisierul sa nu mai creasca (copiere terminata)
    while True:
        firme = citeste_firme(Path(args.firme))  # recitit mereu: poti edita firme.txt din mers
        cfg = citeste_config(Path(args.config))
        if not firme:
            log.warning("firme.txt e gol - adauga cel putin o firma.")
        for cale in extrase_noi(folder, stare):
            marime = cale.stat().st_size
            if not args.o_data and marimi.get(cale) != marime:
                marimi[cale] = marime
                continue
            marimi.pop(cale, None)
            if firme:
                create += proceseaza_extras(cale, firme, cfg, stare)
        if args.o_data:
            if not create:
                log.info("Nicio factura noua. Ai pus extrasul PDF in %s ?", folder)
            break
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Agent oprit.")
