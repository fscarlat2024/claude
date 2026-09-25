#!/usr/bin/env python3
"""
Agent facturi din extrase bancare.

Urmareste un folder (implicit ./extrase). Cand apare un extras nou
PDF, cauta in el toate tranzactiile cu firmele trecute
in firme.txt si genereaza pentru fiecare firma o factura PDF + un rezumat
CSV cu platile gasite, direct in acelasi folder.

Pornire:
    python agent_facturi.py            # ruleaza continuu si asteapta extrase
    python agent_facturi.py --o-data   # proceseaza ce e in folder si se opreste
"""

import argparse
import csv
import json
import logging
import re
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
    adresa: str = ""
    cuvinte_cheie: tuple = ()


def citeste_firme(cale: Path) -> list:
    """firme.txt: o firma pe linie -> Nume | CUI | Adresa | cuvant1, cuvant2"""
    firme = []
    if not cale.exists():
        return firme
    for linie in cale.read_text(encoding="utf-8-sig").splitlines():
        linie = linie.strip()
        if not linie or linie.startswith("#"):
            continue
        parti = [p.strip() for p in linie.split("|")]
        parti += [""] * (4 - len(parti))
        cuvinte = tuple(c.strip() for c in parti[3].split(",") if c.strip())
        firme.append(Firma(parti[0], parti[1], parti[2], cuvinte))
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


def parseaza_data(text) -> date | None:
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    if not text:
        return None
    m = RE_DATA.search(str(text))
    if not m:
        return None
    for fmt in FORMATE_DATA:
        try:
            return datetime.strptime(m.group(1), fmt).date()
        except ValueError:
            continue
    return None


def rotunjeste(x: Decimal) -> Decimal:
    return x.quantize(DOI_ZECIMALE, rounding=ROUND_HALF_UP)


def format_ro(x: Decimal) -> str:
    """1234.5 -> '1.234,50'"""
    s = f"{rotunjeste(x):,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------
# Citire extrase
# --------------------------------------------------------------------------

@dataclass
class Tranzactie:
    data: date
    descriere: str
    suma: Decimal        # mereu pozitiva
    directie: str        # "incasare" (bani primiti) sau "plata" (bani trimisi)


CHEI_DATA = ("data", "date")
CHEI_DEBIT = ("debit", "iesiri", "plati")
CHEI_CREDIT = ("credit", "intrari", "incasari")
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
CUVINTE_INCASARE = ("incasare", "credit", "primit", "incoming", "transfer in")
# randuri de total/sold din extras care NU sunt tranzactii (au sume in coloane, dar sunt totaluri)
CUVINTE_TOTAL = ("sold", "rulaj", "total", "sume blocate", "balance")


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
    for w in linie:
        n = normalizeaza(w["text"])
        centru = (w["x0"] + w["x1"]) / 2
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
        coloane.pop("suma", None)
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
            d = parseaza_data(linie[0]["text"]) if RE_DATA.fullmatch(linie[0]["text"]) else None
            for w in (linie[1:] if d else linie):
                centru = (w["x0"] + w["x1"]) / 2
                if RE_SUMA.match(w["text"]) and centru >= prag_stanga:
                    col = min(coloane, key=lambda k: abs(coloane[k] - centru))
                    sume[col] = parseaza_suma(w["text"])
                elif not (RE_DATA.fullmatch(w["text"]) and not text):
                    text.append(w["text"])
            text = " ".join(text)
            if d:
                data_curenta = d

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

            if suma is not None and data_curenta:
                curenta = Tranzactie(data_curenta, text, abs(suma), directie)
                tranzactii.append(curenta)
            elif curenta is not None and text and not d:
                curenta.descriere += " " + text  # detaliile de pe randurile urmatoare
    return tranzactii


def _tranzactii_din_text(linii: list) -> list:
    """Ultima varianta: o linie care incepe cu o data deschide o tranzactie."""
    tranzactii, blocuri = [], []
    for linie in linii:
        if RE_DATA.match(linie.strip()):
            blocuri.append([linie.strip()])
        elif blocuri:
            blocuri[-1].append(linie.strip())
    for bloc in blocuri:
        text = " ".join(bloc)
        if normalizeaza(RE_DATA.sub(" ", bloc[0])).startswith(CUVINTE_TOTAL):
            continue
        fara_date = [RE_DATA.sub(" ", x) for x in (bloc[0], text)]  # "01.09" nu e suma
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

def _inregistreaza_font():
    """Font cu diacritice romanesti (ș, ț). Intoarce (normal, bold)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidati = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ]
    for normal, bold in candidati:
        if Path(normal).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("FontF", normal))
            pdfmetrics.registerFont(TTFont("FontF-Bold", bold))
            return "FontF", "FontF-Bold", True
    return "Helvetica", "Helvetica-Bold", False


def calculeaza_linii(tranzactii: list, cfg: dict) -> tuple:
    cota = Decimal(cfg.get("COTA_TVA", "21") or "0")
    include_tva = cfg.get("SUMELE_INCLUD_TVA", "da").lower() in ("da", "yes", "1", "true")
    sablon = cfg.get("DESCRIERE_LINIE", "Servicii conform plata din {data}")
    linii = []
    for t in sorted(tranzactii, key=lambda x: x.data or date.min):
        if include_tva and cota:
            baza = rotunjeste(t.suma / (1 + cota / 100))
            tva = rotunjeste(t.suma) - baza
        else:
            baza = rotunjeste(t.suma)
            tva = rotunjeste(baza * cota / 100)
        denumire = sablon.format(data=t.data.strftime("%d.%m.%Y") if t.data else "-",
                                 suma=format_ro(t.suma), detalii=t.descriere[:80])
        linii.append((denumire, baza, tva))
    total_baza = sum((l[1] for l in linii), Decimal(0))
    total_tva = sum((l[2] for l in linii), Decimal(0))
    return linii, cota, total_baza, total_tva


def genereaza_factura(cale_pdf: Path, firma: Firma, tranzactii: list, cfg: dict,
                      serie: str, numar: int, data_factura: date):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font, font_b, are_diacritice = _inregistreaza_font()

    def t(x: str) -> str:
        x = x or ""
        if not are_diacritice:
            x = "".join(c for c in unicodedata.normalize("NFKD", x) if not unicodedata.combining(c))
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    st = ParagraphStyle("n", fontName=font, fontSize=9, leading=12)
    st_b = ParagraphStyle("b", parent=st, fontName=font_b)
    st_titlu = ParagraphStyle("t", fontName=font_b, fontSize=18, leading=22)
    moneda = cfg.get("MONEDA", "RON")

    linii, cota, total_baza, total_tva = calculeaza_linii(tranzactii, cfg)
    zile = int(cfg.get("ZILE_SCADENTA", "0") or 0)
    scadenta = date.fromordinal(data_factura.toordinal() + zile)

    elemente = [
        Paragraph(t("FACTURĂ"), st_titlu),
        Paragraph(t(f"Seria {serie} nr. {numar:04d}   ·   Data: {data_factura:%d.%m.%Y}"
                    f"   ·   Scadență: {scadenta:%d.%m.%Y}").replace("   ", "&nbsp;&nbsp;&nbsp;"), st),
        Spacer(1, 8 * mm),
    ]

    furnizor = [
        Paragraph(t("FURNIZOR"), st_b),
        Paragraph(t(cfg.get("FURNIZOR_NUME", "")), st_b),
        Paragraph(t(f"CUI: {cfg.get('FURNIZOR_CUI', '')}"), st),
        Paragraph(t(f"Reg. Com.: {cfg.get('FURNIZOR_REG_COM', '')}"), st),
        Paragraph(t(cfg.get("FURNIZOR_ADRESA", "")), st),
        Paragraph(t(f"IBAN: {cfg.get('FURNIZOR_IBAN', '')}"), st),
        Paragraph(t(f"Banca: {cfg.get('FURNIZOR_BANCA', '')}"), st),
    ]
    client = [
        Paragraph(t("CLIENT"), st_b),
        Paragraph(t(firma.nume), st_b),
        Paragraph(t(f"CUI: {firma.cui}"), st),
        Paragraph(t(firma.adresa), st),
    ]
    parti = Table([[furnizor, client]], colWidths=[95 * mm, 85 * mm])
    parti.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    elemente += [parti, Spacer(1, 8 * mm)]

    date_tabel = [[Paragraph(t(h), st_b) for h in
                   ("Nr.", "Denumire", "U.M.", "Cant.", f"Preț unitar ({moneda})",
                    f"Valoare ({moneda})", f"TVA {cota}%")]]
    for i, (denumire, baza, tva) in enumerate(linii, 1):
        date_tabel.append([str(i), Paragraph(t(denumire), st), t(cfg.get("UM", "buc")), "1",
                           format_ro(baza), format_ro(baza), format_ro(tva)])
    tabel = Table(date_tabel, colWidths=[10 * mm, 66 * mm, 13 * mm, 14 * mm, 26 * mm, 26 * mm, 25 * mm],
                  repeatRows=1)
    tabel.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8ECF1")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elemente += [tabel, Spacer(1, 5 * mm)]

    totaluri = Table([
        [t("Total fără TVA:"), f"{format_ro(total_baza)} {moneda}"],
        [t(f"TVA {cota}%:"), f"{format_ro(total_tva)} {moneda}"],
        [t("TOTAL DE PLATĂ:"), f"{format_ro(total_baza + total_tva)} {moneda}"],
    ], colWidths=[45 * mm, 40 * mm], hAlign="RIGHT")
    totaluri.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTNAME", (0, 2), (-1, 2), font_b),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
    ]))
    elemente.append(totaluri)

    if cfg.get("MENTIUNI"):
        elemente += [Spacer(1, 10 * mm), Paragraph(t(cfg["MENTIUNI"]), st)]

    SimpleDocTemplate(str(cale_pdf), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm,
                      title=f"Factura {serie} {numar:04d}").build(elemente)
    return total_baza + total_tva


def scrie_rezumat(cale_csv: Path, tranzactii: list):
    with cale_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data", "Directie", "Suma", "Descriere din extras"])
        for t in sorted(tranzactii, key=lambda x: x.data or date.min):
            w.writerow([t.data.strftime("%d.%m.%Y") if t.data else "", t.directie,
                        format_ro(t.suma), t.descriere])


# --------------------------------------------------------------------------
# Stare (ce extrase au fost procesate, ultimul numar de factura)
# --------------------------------------------------------------------------

class Stare:
    def __init__(self, cale: Path, numar_start: int):
        self.cale = cale
        self.date = {"numar_urmator": numar_start, "procesate": {}}
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

    def ia_numar(self) -> int:
        n = self.date["numar_urmator"]
        self.date["numar_urmator"] = n + 1
        return n


# --------------------------------------------------------------------------
# Bucla agentului
# --------------------------------------------------------------------------

def nume_fisier_sigur(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", normalizeaza(text)).strip("_")[:40] or "firma"


def proceseaza_extras(cale: Path, firme: list, cfg: dict, stare: Stare):
    log.info("Extras nou: %s", cale.name)
    try:
        tranzactii = citeste_extras(cale)
    except Exception as e:  # un extras stricat nu trebuie sa opreasca agentul
        log.error("Nu am putut citi %s: %s", cale.name, e)
        return
    log.info("  %d tranzactii citite", len(tranzactii))
    if not tranzactii:
        log.warning("  Nu am recunoscut nicio tranzactie. Trimite-mi PDF-ul ca sa adaug formatul bancii tale.")

    directie = cfg.get("DIRECTIE", "incasare").lower()
    serie = cfg.get("SERIE", "FCT")
    for firma in firme:
        gasite = filtreaza(tranzactii, firma, directie)
        if not gasite:
            log.info("  %s: nicio tranzactie (%s)", firma.nume, directie)
            continue
        numar = stare.ia_numar()
        baza = f"Factura_{serie}{numar:04d}_{nume_fisier_sigur(firma.nume)}_{cale.stem}"
        total = genereaza_factura(cale.parent / f"{baza}.pdf", firma, gasite, cfg,
                                  serie, numar, date.today())
        scrie_rezumat(cale.parent / f"{baza}_plati.csv", gasite)
        log.info("  %s: %d tranzactii -> %s.pdf (total %s %s)",
                 firma.nume, len(gasite), baza, format_ro(total), cfg.get("MONEDA", "RON"))
    stare.marcheaza(cale)
    stare.salveaza()


def extrase_noi(folder: Path, stare: Stare) -> list:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTENSII
                  and not p.name.startswith(("Factura_", "~$", "."))
                  and not stare.e_procesat(p))


def main():
    ap = argparse.ArgumentParser(description="Agent: extrase bancare -> facturi")
    ap.add_argument("--folder", help="folderul urmarit (implicit: FOLDER din config.txt)")
    ap.add_argument("--config", default=str(BAZA / "config.txt"))
    ap.add_argument("--firme", default=str(BAZA / "firme.txt"))
    ap.add_argument("--o-data", action="store_true", help="proceseaza o data si iesi")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(BAZA / "agent.log", encoding="utf-8")])

    cfg = citeste_config(Path(args.config))
    folder = Path(args.folder or cfg.get("FOLDER", "extrase"))
    if not folder.is_absolute():
        folder = BAZA / folder
    folder.mkdir(parents=True, exist_ok=True)
    stare = Stare(folder / ".stare_agent.json", int(cfg.get("NUMAR_START", "1")))
    interval = float(cfg.get("INTERVAL_SECUNDE", "5"))

    log.info("Agent pornit. Urmaresc folderul: %s", folder)
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
                proceseaza_extras(cale, firme, cfg, stare)
        if args.o_data:
            break
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Agent oprit.")
