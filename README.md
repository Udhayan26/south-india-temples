# South India Temples — GPS Directory

Collects every temple in **Tamil Nadu, Kerala, Karnataka, Andhra Pradesh, Telangana and Puducherry** with
name, local-language name, deity, category, metadata, address and **GPS location**, and keeps an Excel sheet up to date.

It runs automatically on GitHub **every night (about 02:00–03:30 AM IST) for at most 1 hour** (off-peak, when the free
public map servers are least busy). Each night continues where the previous one stopped; once everything is
collected it keeps refreshing the oldest data.

**Download the latest Excel:** Releases → `latest` → `south_india_temples.xlsx`

**View on a map (no download):** open any file in [`data/map/`](data/map) — GitHub shows every temple as a
clickable pin: [Tamil Nadu](data/map/Tamil_Nadu.geojson) · [Kerala](data/map/Kerala.geojson) ·
[Karnataka](data/map/Karnataka.geojson) · [Andhra Pradesh](data/map/Andhra_Pradesh.geojson) ·
[Telangana](data/map/Telangana.geojson) · [Puducherry](data/map/Puducherry.geojson)

## What's in the Excel

| Sheet | Contents |
|---|---|
| Summary | Totals by state, category, top deities, special groups (Divya Desam, Paadal Petra Sthalam…), per-source counts |
| By District | Temples per district, how many have a name / deity / village, most common deity |
| All Temples | One row per temple, filterable |
| Tamil Nadu, Kerala, … | Same columns, one sheet per state |
| About | Sources, licences, category key |

Columns: Temple ID · Temple Name · Local Name · Deity (Moolavar) · Category / Tradition · Deity From (source data or
guessed from name) · Special Group · Religion ·
State · District · City/Village · Address · PIN · **Latitude · Longitude · Google Maps link** · Managed By · Phone ·
Website · Timings · Heritage Status · Built/Inception · Description · Image · OSM / Wikidata / Google / Govt IDs ·
Sources · No. of Sources · Last Updated

## Data sources

| Source | How | Cost |
|---|---|---|
| **OpenStreetMap** (Overpass API) | every `place_of_worship` with religion hindu / jain / buddhist, district by district | free |
| **Wikidata** | temples per district (fast map-box search) + deity, heritage status, inception, images, Divya Desam / Paadal Petra / Jyotirlinga… | free |
| **OSM villages / towns** | nearest village or town (within 4 km) fills *City / Village* for temples without an address | free |
| **Google Maps** (Places API, optional) | adds Google place ID & map link; set secret `GOOGLE_MAPS_API_KEY` | paid API (has monthly free usage) |
| **Government data** | drop `.csv` / `.xlsx` files into `data/sources/govt/` (e.g. TN HR&CE list, data.gov.in, ASI monuments) | — |
| **Private collection** | drop your own survey files into `data/sources/private/` | — |

Govt/private files don't need a fixed format — columns like *Temple Name, Moolavar, District, Lat, Long* or a
single *GPS* column (`12.91, 80.19`) are recognised automatically. See the `TEMPLATE_*.csv` files.
Rows without GPS are still added and matched to mapped temples by name + district.

The same temple from different sources is merged into one row (shared IDs → GPS within 60–200 m + similar
name → same district + near-identical name). Where sources disagree, your private data wins, then government,
then Wikidata, then OSM. Deity & category come from the source when given, otherwise they're guessed from the
name (Murugan / Vinayagar / Perumal / Amman / Eswarar…) — check important rows.

> Google Maps is used **only through the official API**; scraping google.com/maps breaks Google's terms and gets
> blocked. Google's terms also limit storing their content, so by default Google results only enrich temples
> already found elsewhere. `GOOGLE_ADD_NEW=1` adds Google-only temples — check the current terms first.

## Setup (once)

1. Create the repo `south-india-temples` on github.com/Udhayan26 (public or private) and push this folder:
   ```bash
   git remote add origin https://github.com/Udhayan26/south-india-temples.git
   git push -u origin main
   ```
2. Repo → **Settings → Actions → General → Workflow permissions → Read and write**.
3. *(Optional)* Settings → Secrets and variables → Actions → New secret `GOOGLE_MAPS_API_KEY`
   (Google Cloud key with **Places API (New)** enabled). Variable `GOOGLE_MAX_REQUESTS` caps calls per night (default 150).
4. Actions tab → **Daily temple data collection → Run workflow** to start immediately (or wait for 02:17 AM).

## Run on your own computer

```bash
pip install -r requirements.txt
python -m temples.run                      # 50-minute budget
python -m temples.run --minutes 15 --states IN-TN
python -m temples.run --export-only        # rebuild Excel from collected data
```

## Files

```
temples/            collector code (osm, wikidata, google_places, manual, merge, export, run)
data/raw/           per-district raw downloads (committed, so changes are visible in git history)
data/sources/       your govt / private files
data/temples_master.csv   merged list (same as the Excel)
data/map/*.geojson  one map file per state (GitHub renders these as a map)
data/state.json     progress — which district was fetched when; "errors" shows anything that failed
data/run_log.csv    one line per nightly run
```

Licence note: OpenStreetMap data © OpenStreetMap contributors (ODbL) — keep the attribution if you share the sheet.
