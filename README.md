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

---

# Agent stoc: furnizor (API) -> website

Se conectează la API-ul furnizorului (implicit **ELKO**, `api.elko.cloud`), citește stocul,
îl compară cu produsele de pe site (**WooCommerce** sau **Shopify**) și, dacă vrei, actualizează
stocul pe site. Nu are nevoie de pachete în plus (doar Python).

Ca un gestionar care sună la depozitul ELKO în fiecare oră, notează ce s-a schimbat și
corectează etichetele din magazin.

## Pornire

1. Completează **`config_stoc.txt`**:
   - `FURNIZOR_TOKEN` – tokenul din contul ELKO (se ia de pe https://api.elko.cloud);
   - `SITE_URL`, `SITE_CHEIE`, `SITE_SECRET` – pentru WooCommerce: *WooCommerce > Setări > Avansat >
     REST API > Adaugă cheie* (permisiuni Citire/Scriere).
2. Rulează `python agent_stoc.py --test` – îți arată exact ce trimite ELKO. Dacă numele câmpurilor
   diferă (ex. `elkoCode` în loc de `manufacturerCode`), schimbă `FURNIZOR_CAMP_COD` /
   `FURNIZOR_CAMP_STOC`.
3. Rulează `python agent_stoc.py --o-data` cu `MOD=verificare`. Nu schimbă nimic pe site, doar face
   raportul `rapoarte_stoc/stoc_<data>.csv` (se deschide în Excel):

   | SKU | Produs | Stoc site | Stoc furnizor | Stoc nou | Acțiune |
   |-----|--------|-----------|---------------|----------|---------|
   | ABC-1 | Laptop | 2 | 10 | 10 | de actualizat |
   | XYZ-9 | Cablu | 5 | | | lipsă la furnizor |

4. Când raportul arată corect, pune `MOD=actualizare` și pornește `porneste_stoc.bat` (Windows) /
   `./porneste_stoc.sh`. Verifică la fiecare `INTERVAL_MINUTE` minute.

## Cum leagă produsele

**SKU-ul** produsului de pe site trebuie să fie același cu codul de la furnizor (`FURNIZOR_CAMP_COD`).
Dacă ai pus un prefix (ex. `ELK-12345`), completează `SKU_PREFIX=ELK-`.
Produsele fără SKU sau care nu există la furnizor apar în raport, ca să le corectezi.

## Reguli utile

- `STOC_REZERVA=2` – afișează cu 2 bucăți mai puțin decât are ELKO (stocul lor se mișcă între
  verificări; e mai ieftin să pierzi o vânzare decât să anulezi o comandă);
- `STOC_MAXIM=20` – nu arăta clienților că ai 500 de bucăți;
- `LIPSA_LA_FURNIZOR=zero` – produsul care a dispărut din lista ELKO este pus pe stoc 0;
- stocurile de tipul „>10” sunt citite ca 10 (minimul sigur).

Teste: `python -m pytest teste/test_agent_stoc.py` (simulează ELKO și WooCommerce, fără internet).

---

# Agent stoc și prețuri NOD → lengo.ro (Cloudflare Worker)

Varianta care rulează în cloud, fără calculator pornit: vezi [`worker-stoc/README.md`](worker-stoc/README.md).
