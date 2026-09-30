"""
Teste: genereaza extrase PDF in stilul bancilor romanesti si verifica ce citeste agentul.

    python -m pytest teste      (sau: python teste/test_extrase_pdf.py)
"""

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_facturi as af  # noqa: E402

FIRMA = af.Firma("Alfa Tech SRL", "RO11223344", "", ("ALFATECH",))  # firma de test
TMP = Path(tempfile.mkdtemp())


def _font():
    for cale in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf",
                 "/Library/Fonts/Arial.ttf"):
        if Path(cale).exists():
            pdfmetrics.registerFont(TTFont("T", cale))
            return "T"
    return "Helvetica"


FONT = _font()


def pdf_coloane(nume, header, pagini, x_coloane):
    """Extras fara chenare: text pe coloane, sumele aliniate la dreapta."""
    cale = TMP / nume
    c = canvas.Canvas(str(cale), pagesize=A4)
    for randuri in pagini:
        c.setFont(FONT, 12)
        c.drawString(40, 800, "EXTRAS DE CONT - RO49AAAA1B31007593840000")
        c.setFont(FONT, 8)
        y = 760
        for text, x in zip(header, x_coloane):
            c.drawRightString(x, y, text) if x > 300 else c.drawString(x, y, text)
        y -= 16
        for rand in randuri:
            for text, x in zip(rand, x_coloane):
                if text:
                    c.drawRightString(x, y, text) if x > 300 else c.drawString(x, y, text)
            y -= 12
        c.showPage()
    c.save()
    return cale


def test_bt_fara_chenare_doua_pagini():
    # BT: data apare doar la prima tranzactie din zi; RULAJ/SOLD au sume dar nu sunt tranzactii
    header = ["Data", "Descriere", "Debit", "Credit", "Sold"]
    x = [40, 95, 400, 470, 550]
    p1 = [
        ["01.09.2026", "SOLD ANTERIOR", "", "", "10.000,00"],
        ["02.09.2026", "Incasare OP - canal electronic", "", "2.420,00", "12.420,00"],
        ["", "ALFA TECH SRL CUI 11223344", "", "", ""],
        ["", "contravaloare servicii august", "", "", ""],
        ["", "Plata OP intra - canal electronic", "350,75", "", "12.069,25"],
        ["", "ENEL ENERGIE factura 998877 total", "", "", ""],
        ["", "RULAJ ZI", "350,75", "2.420,00", ""],
        ["", "SOLD FINAL ZI", "", "", "12.069,25"],
    ]
    p2 = [
        ["15.09.2026", "Incasare OP", "", "1 210,00", "13.279,25"],
        ["", "ALFATECH mentenanta retea", "", "", ""],
        ["", "Incasare OP BETA CONSTRUCT SA avans", "", "5.000,00", "18.279,25"],
        ["20.09.2026", "Plata OP Alfa Tech S.R.L. retur", "100,00", "", "18.179,25"],
        ["", "SOLD FINAL", "", "", "18.179,25"],
    ]
    cale = pdf_coloane("bt.pdf", header, [p1, p2], x)
    tr = af.citeste_extras(cale)
    assert len(tr) == 5, tr
    alfa = af.filtreaza(tr, FIRMA, "incasare")
    assert [t.suma for t in alfa] == [Decimal("2420.00"), Decimal("1210.00")]
    assert [t.data.day for t in alfa] == [2, 15]
    assert [t.suma for t in af.filtreaza(tr, FIRMA, "plata")] == [Decimal("100.00")]
    enel = [t for t in tr if "ENEL" in t.descriere][0]
    assert enel.directie == "plata" and enel.suma == Decimal("350.75")


def test_ing_data_pe_fiecare_rand_si_data_valuta():
    header = ["Data", "Data valutei", "Detalii tranzacție", "Debit", "Credit"]
    x = [40, 95, 150, 440, 540]
    p = [
        ["03.09.2026", "03.09.2026", "Transfer primit", "", "1.500,50"],
        ["", "", "Ordonator: Alfa Tech S.R.L.", "", ""],
        ["04.09.2026", "04.09.2026", "Plată către Alfa Tech SRL", "200,00", ""],
        ["05.09.2026", "05.09.2026", "Cumpărare POS Kaufland", "87,30", ""],
    ]
    tr = af.citeste_extras(pdf_coloane("ing.pdf", header, [p], x))
    assert len(tr) == 3, tr
    alfa = af.filtreaza(tr, FIRMA, "toate")
    assert [(t.directie, t.suma) for t in alfa] == [("incasare", Decimal("1500.50")),
                                                     ("plata", Decimal("200.00"))]


def test_coloana_suma_cu_semn():
    header = ["Data", "Descriere", "Suma", "Sold"]
    x = [40, 110, 460, 550]
    p = [
        ["06.09.2026", "Alfa Tech SRL servicii", "3.025,00", "3.025,00"],
        ["07.09.2026", "Orange Romania", "-99,00", "2.926,00"],
    ]
    tr = af.citeste_extras(pdf_coloane("suma.pdf", header, [p], x))
    assert [(t.directie, t.suma) for t in tr] == [("incasare", Decimal("3025.00")),
                                                   ("plata", Decimal("99.00"))]


def test_tabel_cu_chenare():
    from reportlab.platypus import SimpleDocTemplate, Table
    cale = TMP / "chenare.pdf"
    SimpleDocTemplate(str(cale), pagesize=A4).build([Table([
        ["Data", "Descriere", "Debit", "Credit"],
        ["02.09.2026", "Incasare ALFA TECH SRL", "", "2.420,00"],
        ["05.09.2026", "Plata ENEL", "350,75", ""],
    ], style=[("GRID", (0, 0), (-1, -1), 0.5, "black")])])
    tr = af.citeste_extras(cale)
    assert [(t.directie, t.suma) for t in tr] == [("incasare", Decimal("2420.00")),
                                                   ("plata", Decimal("350.75"))]


def pdf_wise(nume):
    """Extras Wise: Description | Incoming | Outgoing | Amount (sold); data e sub descriere."""
    cale = TMP / nume
    c = canvas.Canvas(str(cale), pagesize=A4)
    c.setFont(FONT, 8)
    c.drawString(40, 800, "EUR statement  1 August 2026 - 30 September 2026")
    for text, x in (("Description", 40), ("Incoming", 400), ("Outgoing", 470), ("Amount", 550)):
        c.drawRightString(x, 760, text) if x > 300 else c.drawString(x, 760, text)
    randuri = [
        ("Received money from NET COMMUNICATIONS SYSTEMS SRL", "7,900.00", "", "7,900.00"),
        ("with reference GROW-2026-08", "", "", ""),
        ("31 August 2026 Transaction: TRANSFER-1111", "", "", ""),
        ("Card transaction of 25.00 EUR issued by Google Workspace", "", "25.00", "7,875.00"),
        ("2 September 2026 Transaction: CARD-2222", "", "", ""),
        ("Received money from NET COMMUNICATION SYSTEMS S.R.L.", "1,000.00", "", "8,875.00"),
        ("10 September 2026 Transaction: TRANSFER-3333", "", "", ""),
        ("Received money from NET COMMUNICATIONS SYSTEMS SRL", "4,310.00", "", "13,185.00"),
        ("25 September 2026 Transaction: TRANSFER-4444", "", "", ""),
    ]
    y = 744
    for desc, inc, out, sold in randuri:
        c.drawString(40, y, desc)
        for text, x in ((inc, 400), (out, 470), (sold, 550)):
            if text:
                c.drawRightString(x, y, text)
        y -= 12
    c.save()
    return cale


NCS = af.Firma("NET COMMUNICATIONS SYSTEMS SRL", "RO34291656",
               "Str. Samuil Vulcan 12D, corp A, et. 2, ap. birou 13; Sector 5, Bucharest 077160, Romania",
               ("NET COMMUNICATION SYSTEMS",), "J40/3729/2015")


def test_wise_date_cu_litere_si_sold_ignorat():
    tr = af.citeste_extras(pdf_wise("wise.pdf"))
    assert [(t.data.isoformat(), t.directie, t.suma) for t in tr] == [
        ("2026-08-31", "incasare", Decimal("7900.00")),
        ("2026-09-02", "plata", Decimal("25.00")),
        ("2026-09-10", "incasare", Decimal("1000.00")),
        ("2026-09-25", "incasare", Decimal("4310.00")),
    ]
    assert len(af.filtreaza(tr, NCS, "incasare")) == 3


def test_factura_grow_o_factura_pe_luna():
    import pdfplumber
    folder = TMP / "grow"
    folder.mkdir()
    extras = folder / "wise_septembrie.pdf"
    extras.write_bytes(pdf_wise("wise2.pdf").read_bytes())
    cfg = af.citeste_config(af.BAZA / "config.exemplu.txt")
    create = af.proceseaza_extras(extras, [NCS], cfg, af.Stare(folder / ".stare.json"))
    assert [p.name for p in create] == ["Invoice_GROW202608.pdf", "Invoice_GROW202609.pdf"]

    text = pdfplumber.open(create[1]).pages[0].extract_text()
    for asteptat in ("Grow LLC", "INVOICE", "No. GROW-2026-09", "Issue date: September 30, 2026",
                     "NET COMMUNICATIONS SYSTEMS SRL", "VAT / CUI: RO34291656",
                     "Trade Register: J40/3729/2015", "IT services & marketing services",
                     "September 2026", "Total 5,310.00 EUR", "IBAN: BE00 0000 0000 0000",
                     "quote invoice number GROW-2026-09"):
        assert asteptat in text, asteptat
    assert "Google" not in (folder / "Invoice_GROW202609_plati.csv").read_text(encoding="utf-8-sig")

    # a doua rulare nu suprascrie facturile existente
    extras.touch()
    assert af.proceseaza_extras(extras, [NCS], cfg, af.Stare(folder / ".stare.json")) == []


def test_firme_txt():
    firme = af.citeste_firme(af.BAZA / "firme.exemplu.txt")
    assert [(f.nume, f.cui, f.reg_com) for f in firme] == [
        ("NET COMMUNICATIONS SYSTEMS SRL", "RO34291656", "J40/3729/2015")]


def test_pdf_scanat_nu_crapa():
    cale = TMP / "scanat.pdf"
    c = canvas.Canvas(str(cale), pagesize=A4)
    c.rect(50, 50, 400, 400, fill=1)  # doar grafica, fara text
    c.save()
    assert af.citeste_extras(cale) == []


if __name__ == "__main__":
    for nume, f in list(globals().items()):
        if nume.startswith("test_"):
            f()
            print("OK ", nume)
