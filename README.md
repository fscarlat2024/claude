# Agent facturi Grow LLC din extrase bancare

Pui extrasul PDF în folderul `extrase`, rulezi `Genereaza-Factura.ps1`, iar agentul scrie
lângă extras factura Grow LLC către NET COMMUNICATIONS SYSTEMS SRL, în același format ca
`Invoice_GROW202608.pdf`.

```
extrase/
  extras_wise_septembrie.pdf           <- îl pui tu
  Invoice_GROW202608.pdf               <- apare automat (plățile din august)
  Invoice_GROW202608_plati.csv         <- plățile găsite, ca să verifici
  Invoice_GROW202609.pdf               <- plățile din septembrie
  Invoice_GROW202609_plati.csv
```

## Prima dată

1. Instalează Python 3.10+ de pe python.org (bifează „Add Python to PATH”).
2. Copiază `config.exemplu.txt` ca `config.txt` și `firme.exemplu.txt` ca `firme.txt`
   (sau rulează o dată scriptul, care le creează singur), apoi completează în `config.txt`
   **EIN-ul, IBAN-ul și numele tău**.
   `config.txt` și `firme.txt` nu se urcă pe GitHub (repo-ul e public).

## De fiecare dată

1. Pui extrasul PDF în folderul `extrase`.
2. Click dreapta pe **`Genereaza-Factura.ps1`** → **Run with PowerShell**.
3. Factura se deschide singură și rămâne salvată lângă extras.

Dacă Windows blochează scriptul („running scripts is disabled”), rulează o dată în PowerShell:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
sau pornește-l așa: `powershell -ExecutionPolicy Bypass -File Genereaza-Factura.ps1`.

## Ce face

- Ia din extras **încasările** de la NET COMMUNICATIONS SYSTEMS (după nume, alias-uri sau CUI).
  Alte tranzacții (carduri, abonamente etc.) sunt ignorate.
- Face **o factură pe lună**: toate plățile NCS dintr-o lună intră într-un singur rând
  „IT services & marketing services | August 2026”, numerotat `GROW-2026-08`, cu data
  facturii în ultima zi a lunii (se poate schimba în `config.txt`).
- Dacă plata din septembrie e pentru august, pune în `config.txt`: `LUNA_SERVICIU=anterioara`.
- Nu suprascrie niciodată o factură existentă. Ca s-o refaci, șterge PDF-ul și rulează din nou.
- Un extras e procesat o singură dată (reținut în `extrase/.stare_agent.json`).

## Ce extrase înțelege

Doar **PDF** descărcat din aplicația băncii (Wise, BT, BCR, ING, Revolut etc.), nu scanat.
Citește coloanele după poziție: suma de sub „Incoming/Credit” e încasare, cea de sub
„Outgoing/Debit” e plată, iar cea de sub „Amount/Balance/Sold” e ignorată. Înțelege și date
scrise cu litere („31 August 2026”). La primul extras dintr-o bancă nouă verifică `_plati.csv`.

Teste: `python -m pytest teste`.
