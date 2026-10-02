# Data Sources — Verified

All facts below were confirmed either against official documentation or by live API calls made during this session (2026-09-28). Full detail and sourcing: [research-log.md](research-log.md).

## Primary platform: Microsoft Planetary Computer (PC) STAC

**Recommended primary data host for MVP** — hosts every dataset this project needs, all zero-auth. Verified live this session with real requests (commands below are exactly what was run).

| Property | Value |
|---|---|
| STAC search endpoint | `https://planetarycomputer.microsoft.com/api/stac/v1/search` |
| Auth for search | **None.** Anonymous GET/POST works. |
| Auth for pixel access | **None.** Assets are private blob URLs (`409` unsigned) made readable via a public signing call: `GET https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<asset_href>` → returns a time-limited SAS-signed URL → range-readable with plain HTTPS. No account, no key. **But see the performance note below — this call is the single most expensive thing in the app if used naively.** |
| Relevant collections | `sentinel-2-l2a`, `landsat-c2-l2` (+ `landsat-c2-l1`), `jrc-gsw`, `ms-buildings`, plus optional `esa-worldcover` / `io-lulc-annual-v02` (10 m built-area class, useful cross-check) |
| Rate limits / commercial terms | **Not confirmed this session** — `/terms` and `/docs/concepts/sas/` are JS-rendered pages that didn't yield text via automated fetch. Check manually before scaling past personal/demo traffic. |

Live verification trail (reproducible):
```bash
# 1. anonymous search
curl "https://planetarycomputer.microsoft.com/api/stac/v1/search?collections=sentinel-2-l2a&bbox=80.19,12.90,80.23,12.95&datetime=2023-11-01/2024-02-28"
# -> 200, real Sentinel-2 items over Chennai/Pallikaranai

# 2. asset href is a private blob -> unsigned read fails
curl -r 0-1023 "https://sentinel2l2a01.blob.core.windows.net/.../B03_10m.tif"
# -> 409

# 3. sign it (no auth) then read it
curl "https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<url-encoded-href>"
# -> 200, {"href": "<same url>?st=...&se=...&sp=rl&sig=..."}
curl -r 0-65535 "<signed href>"
# -> 206 Partial Content, real bytes
```

### PERFORMANCE: the `/sign` endpoint is the bottleneck, and its token is container-scoped

Measured live 2026-09-29, and it dominates everything else by more than an order of magnitude:

| Operation | Time |
|---|---|
| STAC search | 1.3 s |
| **`/sign` call, cold (throttled)** | **82–87 s** |
| `/sign` call, token cached | 0.0001 s |
| Windowed read of 3 bands (parallel) | 2.5 s |
| A whole additional year, token already cached | **3.0 s** |

`/sign` returns `429` *immediately* on an un-retried call once you have been using it, and the retry ladder simply waits the throttle out. A naive implementation signs 3 assets × 6 years = **18 times per user query**, which is why a single location took ~100 s.

**The token is scoped to the storage container, not the blob.** Verified live: a token minted for `.../sentinel2-l2/.../B03.tif` reads `B11` and `SCL` in the same container (`HTTP 206`). The response carries `msft:expiry`, ~1 hour out. So:

- One token serves **every asset of every scene** in that container — 18 calls collapse to 1.
- It must be cached **across process restarts** (this app persists it in SQLite), or every deploy/restart re-pays the ~85 s.
- Mint it **at startup in the background**, so no user request is ever the one that pays.
- Requests must be **serialised per container** while minting; otherwise every worker thread misses the cache simultaneously and fires the exact burst that triggers the throttle.

This is what the official `planetary-computer` Python package does internally. This project keeps its own zero-dependency implementation in `backend/app/pc_client.py`.

**Also relevant: more concurrency is not better.** Raising the per-year thread pool from 3 to 6 (with 3 parallel band reads each, so ~18 requests in flight) was measured as a *regression* — 141 s vs ~98 s — because PC throttles the burst harder than the parallelism buys back.

## Fallback / alternative: Copernicus Data Space Ecosystem (CDSE)

The system the original spec named explicitly. Keep documented as a fallback in case PC access policy changes, or if direct Copernicus attribution/provenance matters for a use case.

| Property | Value |
|---|---|
| STAC endpoint | `https://stac.dataspace.copernicus.eu/v1/` |
| Collection id | `sentinel-2-l2a` |
| Search auth | None (confirmed live) |
| Download auth | **Required** — either CDSE S3 access keys (`s3://eodata/...` href) or an OAuth2 bearer token (`identity.dataspace.copernicus.eu`, password grant, `client_id=cdse-public`) against the OData HTTPS alternate href |
| Coverage | 2015–present (native processing from 2018-12-13, back-filled to 2015 via reprocessing) |
| Resolution / revisit | 10/20/60 m, ~5-day single-satellite / ~2-3 day combined |
| License | Free incl. commercial use. Attribution: **"Copernicus Sentinel data [Year]"** |

## Fallback: USGS M2M — Landsat 8/9

| Property | Value |
|---|---|
| Endpoint | `https://m2m.cr.usgs.gov/` |
| Auth | ERS account + manual "MACHINE" access approval + application tokens |
| Download quota | Not published |
| License | Public domain (US federal work) |

Low priority for MVP since PC's `landsat-c2-l2` collection serves the same data with zero auth.

## JRC Global Surface Water — validation reference only

| Property | Value |
|---|---|
| Access | PC collection `jrc-gsw` (zero-auth, confirmed live) or Earth Engine `JRC/GSW1_4/...` |
| Coverage | **PC's `jrc-gsw` collection serves version `v1_3` (~2020 vintage, confirmed live: tile `80E_20Nv1_3_2020`, `datetime: 2020-07-01`) — older than GEE's `v1.4` (through 2021) or the claimed `v1.5` (through 2024).** If 2021+ historical coverage matters for a given AOI, query Earth Engine directly for this dataset rather than PC. |
| License | Free. Attribution: **"Source: EC JRC/Google"** + cite Pekel et al. 2016, *Nature* |
| Role | Cross-check historical water occurrence against the MNDWI-derived mask — not a primary detection input |

## Microsoft Global ML Building Footprints — NOT USED in MVP

| Property | Value |
|---|---|
| Access | PC collection `ms-buildings` (zero-auth, confirmed live) or `github.com/microsoft/GlobalMLBuildingFootprints` (gzip line-delimited GeoJSON, quadkey-partitioned) |
| License | CDLA-Permissive-2.0 (transitioned from ODbL ~2026-03-11 — check which applied if mixing footprints cached at different times) |
| Coverage | 225 countries/regions, India included |

**Why it is not the MVP's building cross-check (verified live 2026-09-29):** this collection's STAC items are *continent-sized* partitions, not per-area footprint tiles — a bbox over Chennai returns an item whose id is `Oceania_2022-07-06`. A naive "does a STAC item intersect my point?" check therefore returns **true almost everywhere on land, and in the open ocean too** (confirmed: mid-Pacific `-30, -140` returned `Oceania`). An earlier MVP build shipped exactly that check and reported "a building footprint exists at this location" for a point 2,000 km from land.

The real payload is `abfs://footprints/global/<date>/ml-buildings.parquet/RegionName=<region>` — a GeoParquet needing an `adlfs`/`fsspec` reader, `geopandas`/`pyarrow`, and a quadkey join to get from "region" down to an AOI. That is a genuine v2 task, not a one-line fix. The MVP uses the raster land-cover collection below instead.

## Esri / Impact Observatory 10 m Annual Land Cover — the MVP's built-up cross-check

| Property | Value |
|---|---|
| Access | PC collection `io-lulc-annual-v02` (zero-auth, confirmed live) |
| Assets | `data` — a plain HTTPS COG on `ai4edataeuwest.blob.core.windows.net`, readable through the same `rasterio` + SAS-sign path as Sentinel-2. No new dependencies. |
| Class codes | From the asset's own `file:values`: `0` No Data, `1` Water, `2` Trees, `4` Flooded vegetation, `5` Crops, `7` **Built area**, `8` Bare ground, `9` Snow/ice, `10` Clouds, `11` Rangeland |
| Coverage | Annual tiles per MGRS supercell (e.g. `44P-2023`). Lags the Sentinel-2 archive — a query for year *N* is served by the tile spanning *N-1*-01-01 → *N*-01-01, so report the tile's own `start_datetime` year, never the requested year. |
| Why it beats the footprint check | Answers per-year, not "now"; returns NoData over open ocean instead of a false positive; and is a raster the existing stack already reads. Live-verified: dense Chennai `13.06, 80.26` → 100% built; Pallikaranai marsh → 0% built, flooded-vegetation dominant; mid-Pacific → all NoData. |
| License | Free. Attribution: **Esri / Impact Observatory / Microsoft** |

## Basemap tiles (MapLibre GL JS)

| Layer | Source | Key needed? | Notes |
|---|---|---|---|
| Street/reference basemap | `tile.openstreetmap.org` raster tiles | No (confirmed live) | Must send descriptive `User-Agent`, cache client-side, no bulk pre-fetch, per [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/) |
| Satellite/imagery layer | **PC's own XYZ tile service, rendering the exact Sentinel-2/Landsat scene each verdict came from** | No (confirmed live) | No key-less, terms-compliant *third-party* satellite basemap exists for a standalone app — Esri World Imagery works technically but its terms restrict use to licensed ArcGIS contexts; Mapbox/MapTiler require tokens. See the row below for what the app uses instead. |

### Satellite imagery layer — PC item tiler (verified live 2026-09-29)

The original conclusion ("render our own composites") turned out to understate what PC offers: it exposes a **per-item XYZ raster tile endpoint**, anonymously, so the app gets a real slippy satellite layer without compositing anything itself.

```
https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{z}/{x}/{y}@1x
    ?collection=sentinel-2-l2a&item=<item id>&assets=visual
```

| Property | Value |
|---|---|
| Auth | **None.** Live-tested: `200 image/jpeg`, ~26 KB tile, no key, no SAS step. |
| Template | Fully determined by `collection` + `item` id, so the backend builds it directly — **no `tilejson.json` round-trip per scene**, which keeps it off the 429-prone request path. |
| Sentinel-2 | `assets=visual` (the pre-built true-colour asset). |
| Landsat | `assets=red&assets=green&assets=blue` plus `color_formula=gamma RGB 2.7, saturation 1.5, sigmoidal RGB 15 0.55` — C2 L2 surface reflectance is dim and flat unstretched. Live-tested: `200 image/jpeg`. |
| Why per-item, not a mosaic | The tiles come from **the same scene the year's verdict was computed from**, so what the user looks at is what was measured. Moving the timeline moves the imagery with it. A generic mosaic basemap would silently show different pixels than the ones that produced the verdict. |
| Attribution | "Copernicus Sentinel data / USGS Landsat, via Microsoft Planetary Computer" — set on the MapLibre raster source, so it appears only while the layer is on. |

## Explicitly avoided: Google Earth Engine

Confirmed free only for noncommercial/research/qualifying-journalism use; commercial/operational use by anyone else requires $500–2,000/mo (or pay-as-you-go) **regardless of volume**. Not needed anyway — PC/CDSE/USGS cover every required dataset with no such restriction. See [06-legal-and-licensing.md](06-legal-and-licensing.md).
