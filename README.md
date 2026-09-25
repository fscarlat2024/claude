# Agent facturi din extrase bancare

Pui un extras bancar într-un folder, iar agentul găsește singur toate tranzacțiile
cu firmele din `firme.txt` și îți face factura PDF în același folder.

```
extrase/
  extras_BT_septembrie.pdf                              <- îl pui tu (PDF)
  Factura_FCT0001_alfa_tech_extras_BT_septembrie.pdf    <- apare automat
  Factura_FCT0001_alfa_tech_extras_BT_septembrie_plati.csv  <- lista plăților găsite, ca să verifici
```

## Pornire (o singură dată)

1. Instalează Python 3.10+ (pe Windows bifează „Add Python to PATH”).
2. Completează **`config.txt`** cu datele firmei tale (CUI, IBAN, serie factură, TVA).
3. Scrie firmele în **`firme.txt`**, câte una pe linie:
   ```
   Alfa Tech SRL | RO11223344 | Str. Florilor 10, Cluj | ALFATECH
   ```
   (nume | CUI | adresă | alte denumiri sub care apare în extras)
4. Dublu-click pe **`porneste_agent.bat`** (Windows) sau rulează `./porneste_agent.sh` (Mac/Linux).

Cât timp fereastra e deschisă, agentul verifică folderul la fiecare 5 secunde.
Poți modifica `firme.txt` din mers, fără repornire.

## Ce extrase înțelege

Doar **PDF** – extrasul descărcat din internet banking (BT, BCR, ING, BRD, Raiffeisen etc.).
Fișierele CSV/Excel din folder sunt ignorate.

Agentul încearcă 3 metode, în ordine, și o folosește pe prima care merge:

1. **tabel cu chenare** – PDF-uri cu linii de tabel;
2. **coloane după poziție** – extrasele obișnuite, fără chenare: o sumă aflată sub „Credit” e
   încasare, una sub „Debit” e plată, cea de sub „Sold” e ignorată. Merge și când data apare doar
   la prima tranzacție din zi și când detaliile plății sunt pe mai multe rânduri;
3. **text simplu** – ultima variantă, mai puțin precisă.

Rândurile „SOLD”, „RULAJ ZI”, „TOTAL” sunt sărite (au sume, dar nu sunt tranzacții).

Nu merge pe **PDF-uri scanate** (poze) – agentul te anunță în fereastră. Descarcă extrasul
direct din aplicația băncii. Verifică mereu `_plati.csv` la primele extrase dintr-o bancă nouă.

Test pe extrasul de exemplu: copiază `exemple/extras_BT_septembrie.pdf` în `extrase/`.

## Cum recunoaște firma

- după nume (fără diacritice, fără „SRL/SA”, fără puncte: „Alfa Tech S.R.L.” = „ALFA TECH SRL”)
- după CUI, dacă apare în descrierea plății
- după „alte denumiri” din `firme.txt` (pentru când banca scrie „ALFATECH”)

`DIRECTIE` din `config.txt` alege ce tranzacții intră: `incasare` (bani primiți de la firmă,
implicit), `plata` (bani trimiși către firmă) sau `toate`.

## Important

- Factura PDF **nu înlocuiește e-Factura** (RO e-Factura/SPV ANAF, obligatorie în B2B).
  Folosește PDF-ul ca document intern / de verificare sau emite factura oficială din
  programul tău de facturare.
- Teste: `python -m pytest teste` (generează extrase PDF de probă și verifică citirea).
- Un extras e procesat o singură dată (ținut minte în `extrase/.stare_agent.json`, tot acolo e
  și numărul următoarei facturi). Dacă vrei să-l reprocesezi, șterge linia lui din acel fișier.
