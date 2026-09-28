# Agent stoc și prețuri: NOD → lengo.ro (Cloudflare Worker)

Un mic program care stă în Cloudflare și, **o dată pe oră**:

1. citește stocul și prețul de achiziție de la **NOD**;
2. citește produsele de pe **lengo.ro** (WooCommerce);
3. calculează prețul de vânzare după politica de preț;
4. **pe site rămân doar produsele în stoc la NOD** (celelalte primesc stoc 0 și dispar din catalog);
5. arată totul pe o pagină de raport protejată cu parolă.

Nu se instalează nimic în WordPress: agentul intră pe site prin API-ul WooCommerce, cu o cheie
pe care o poți retrage oricând.

## Politica de preț

```
preț vânzare = max( cost NOD × (1 + adaos),  cost NOD + 25 lei ) × 1,21 TVA  → rotunjit la ,99
```

| Categorie pe lengo.ro | Adaos |
|---|---|
| Laptopuri | 10% |
| orice altceva (accesorii) | 30% |

| Exemplu | Cost NOD | Calcul | Preț pe site |
|---|---|---|---|
| Laptop | 2.000 lei | 2.000 × 1,10 = 2.200 × 1,21 = 2.662,00 | **2.662,99** |
| Mouse | 30 lei | 30 × 1,30 = 39 < 30 + 25 = 55 → 55 × 1,21 = 66,55 | **66,99** |

**Siguranțe:**
- **Frână la salturi:** dacă prețul NOD se schimbă cu peste 10% față de ultima verificare, prețul
  nu se aplică până nu apeși „Aprobă” în raport (stocul se actualizează în continuare).
- **Preț fix sub cost:** un preț fixat de mână (`PRETURI_FIXE`) sub cost + TVA ascunde produsul.
- **Mod verificare:** până nu treci pe `MOD = "actualizare"`, agentul doar raportează.

Totul se schimbă din `wrangler.toml`, secțiunea `[vars]`, apoi `npx wrangler deploy`.

## Instalare (o singură dată, ~20 minute)

### A. Pe lengo.ro (WordPress)

1. *WooCommerce → Setări → Avansat → REST API → Adaugă cheie*: utilizator = contul de admin,
   permisiuni **Citire/Scriere**. Păstrează `ck_...` și `cs_...` (nu le trimite nimănui).
2. *WooCommerce → Setări → Produse → Inventar*: bifează **„Ascunde produsele fără stoc din catalog”**.
3. La fiecare produs, câmpul **SKU** (*Produs → Inventar*) = codul produsului din NOD.
4. Verifică numele categoriei de laptopuri. Dacă nu e exact „Laptopuri”, schimbă
   `ADAOS_CATEGORII` în `wrangler.toml`.

### B. În Cloudflare

Ai nevoie de [Node.js](https://nodejs.org) instalat. Din folderul `worker-stoc`:

```sh
npm install
npx wrangler login                           # se deschide browserul, te loghezi în Cloudflare
npx wrangler kv namespace create STARE       # copiezi id-ul primit în wrangler.toml (PUNE_AICI_ID_UL_KV)

npx wrangler secret put WOO_CHEIE            # lipești ck_...
npx wrangler secret put WOO_SECRET           # lipești cs_...
npx wrangler secret put RAPORT_PAROLA        # alegi o parolă pentru pagina de raport

npx wrangler deploy                          # publică agentul
```

La final primești o adresă de forma `https://lengo-stoc.<cont>.workers.dev`. Aceea e **pagina de
raport** (orice utilizator + parola aleasă).

### C. NOD (când primim documentația API)

Partea NOD e momentan un conector general (`src/nod.js`) care citește o listă JSON/CSV de la
`NOD_URL`. După ce primim documentația NOD, completăm autentificarea exactă.

## Folosire

- **Prima săptămână:** `MOD = "verificare"`. Deschizi raportul și verifici prețurile și stocurile
  calculate. Verde = s-ar schimba; roșu = are nevoie de tine.
- **Apoi:** `MOD = "actualizare"` în `wrangler.toml` și `npx wrangler deploy`.
- `npm run loguri` arată în timp real ce face agentul.
- `npm test` rulează testele (NOD și lengo.ro simulate, fără internet).

## Probleme posibile

- **401 de la lengo.ro deși cheile sunt corecte:** unele hostinguri șterg antetul de autentificare.
  Pune `WOO_CHEI_IN_URL = "da"`.
- **403 de la lengo.ro:** un plugin de securitate (Wordfence etc.) blochează API-ul. Adaugă o
  excepție pentru `/wp-json/wc/`.
- **Produs „fără SKU” în raport:** completează SKU-ul cu codul NOD.
