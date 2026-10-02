# Research Log — Water-Detection Web App

Verification date: **2026-09-28**. Two passes: (1) background research agent checking official docs, (2) live API calls from this session (curl/python) against the real endpoints to close gaps the docs alone couldn't settle. Every finding below is either HIGH (confirmed against an official source or a live successful API call made in this session), MEDIUM (official source found but a sub-detail unconfirmed), or LOW (no official source found; treat as unverified).

## Headline finding (changes the architecture)

Live-tested **Microsoft Planetary Computer (PC) STAC API** (`https://planetarycomputer.microsoft.com/api/stac/v1`) during this session:
- Anonymous search: `GET .../search?collections=sentinel-2-l2a&bbox=...&datetime=...` → `200`, no token, no API key.
- Asset pixel access: raw blob URL → `409` (needs signing) → `GET https://planetarycomputer.microsoft.com/api/sas/v1/sign?href=<url>` (also anonymous, no key) → returns a SAS-signed URL → range-read on that URL → `206 Partial Content`, real bytes.
- Confirmed hosting **all four required datasets** on one platform, same zero-auth pattern: `sentinel-2-l2a`, `landsat-c2-l2` (and `landsat-c2-l1`), `jrc-gsw`, `ms-buildings`. Bonus: `esa-worldcover` and `io-lulc-annual-v02` (10 m annual land cover incl. a "built area" class) also present — optional cross-check, not required for MVP.

This means the **entire MVP data pipeline can run with zero accounts, zero API keys, and zero OAuth flows** — a simpler path than the CDSE OAuth2 + S3-credentials route the original spec assumed. Recommend PC as primary; CDSE/USGS documented below as fallback/alternative since they're the systems the spec named explicitly. See [`DECISION.md`](DECISION.md).

Confidence: **HIGH** (live-tested end-to-end, this session, exact commands reproducible — see [02-data-sources-verified.md](02-data-sources-verified.md)).
Open item: PC's own rate-limit / fair-use / commercial-use terms page is a JS-rendered SPA and did not yield text via fetch in this session — **LOW confidence on rate limits**, recommend a manual check of `https://planetarycomputer.microsoft.com/terms` and `/docs/concepts/sas/` in a browser before scaling past hobby traffic.

---

## 1. Copernicus Data Space Ecosystem (CDSE) — Sentinel-2 L2A (fallback path)

| Question | Finding | Source | Confidence |
|---|---|---|---|
| STAC endpoint | `https://stac.dataspace.copernicus.eu/v1/` (old `catalogue.dataspace.copernicus.eu/stac` deprecated, redirects, retired per a 2025-11-17 notice) | [documentation.dataspace.copernicus.eu/APIs/STAC.html](https://documentation.dataspace.copernicus.eu/APIs/STAC.html) | HIGH |
| Collection id | `sentinel-2-l2a` — confirmed live: `GET /v1/collections/sentinel-2-l2a/items?bbox=...` → `200`, real items returned (tested this session against Pallikaranai/Chennai bbox) | live test | HIGH |
| Search auth | **Anonymous** — confirmed live, no token needed for search/metadata | live test | HIGH |
| Pixel/asset download auth | Two paths, both auth-gated: (a) primary asset `href` is `s3://eodata/...` requiring **CDSE S3 access keys** (`storage:refs: [cdse-s3, creodias-s3]`), issued via the CDSE dashboard's S3 keys manager; (b) each asset also carries an `alternate.https` OData URL (`download.dataspace.copernicus.eu/odata/v1/Products(...)/Nodes(...)`) requiring an **OAuth2 bearer token** (`auth:refs: [oidc]`) from the identity server. Confirmed by inspecting live STAC item JSON this session. | live test + [documentation.dataspace.copernicus.eu/APIs/Token.html](https://documentation.dataspace.copernicus.eu/APIs/Token.html) | HIGH |
| OAuth2 token endpoint | `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token`, password grant, `client_id=cdse-public` | official docs | HIGH |
| Rate limits / quota | No published numeric req/sec or GB/day limit. Documented policy: download volume capped on a **rolling 30-day total, checked hourly**, exceeding it **throttles** (doesn't hard-block). Separate WAF-level 429s reported by users. | [FAQ](https://documentation.dataspace.copernicus.eu/FAQ.html), community forum | MEDIUM |
| L2A temporal coverage | Native/operational global L2A generation began 2018-12-13; a "Collection-1" reprocessing campaign back-filled L2A from the 2015 Sentinel-2A launch through 2023-12-13, so **2015-present is available**, just not natively processed that far back originally. | [SentiWiki](https://sentiwiki.copernicus.eu/web/s2-mission) | HIGH |
| Resolution / revisit | 10 m (B2,3,4,8), 20 m (B1,5-7,8A,11,12 + SCL), 60 m (all + SCL). Revisit ~5 days single-satellite, ~2-3 days combined constellation. | [dataspace docs](https://documentation.dataspace.copernicus.eu/Data/SentinelMissions/Sentinel2.html) | HIGH |
| License / attribution | Free, no restriction on commercial use. Required credit line: **"Copernicus Sentinel data [Year]"**. | [copernicus.eu/en/terms-use](https://www.copernicus.eu/en/terms-use/how-access-data) | HIGH |

## 2. USGS M2M API — Landsat 8/9 (fallback path)

| Question | Finding | Source | Confidence |
|---|---|---|---|
| Endpoint | Portal `https://m2m.cr.usgs.gov/`; JSON API docs at `/api/docs/json/`. Some older USGS pages still reference `ims.cr.usgs.gov` — inconsistent across USGS properties. | official USGS | MEDIUM |
| Auth | Requires an ERS account **plus** a manual "MACHINE" access-type application approval, then up to 10 application tokens (password-based `login` deprecated 2025-02-26) used against `login-token`. | [USGS M2M Token PDF](https://d9-wret.s3.us-west-2.amazonaws.com/assets/palladium/production/s3fs-public/media/files/M2M%20Application%20Token%20Documentation_072024.pdf) | MEDIUM-HIGH |
| Approval wait time | Not stated in any official USGS doc found; commonly reported as "a few days" anecdotally. | community reports | LOW |
| Download quota | Not documented anywhere official. | — | LOW |
| Licensing | Public domain (US federal work); USGS requests, doesn't require, citation. | [USGS Data Policy](https://www.usgs.gov/emergency-operations-portal/data-policy) | HIGH |

**Note:** Since PC's `landsat-c2-l2` collection serves the same data with zero auth (confirmed live), the M2M account-approval friction is avoidable for MVP — keep M2M only as a documented fallback if PC access ever changes.

## 3. JRC Global Surface Water (JRC GSW)

| Question | Finding | Source | Confidence |
|---|---|---|---|
| Hosting | Google Earth Engine catalog (`JRC/GSW1_4/...`) and JRC's own Global Surface Water Explorer download portal. **Also confirmed live on Planetary Computer** as collection `jrc-gsw`, zero-auth. | live test + [EE catalog](https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_GlobalSurfaceWater) | HIGH |
| Temporal coverage | The commonly-referenced GEE `v1.4` dataset covers **1984-03-16 to 2021-12-31**. A `v1.5` extending to 2022-2024 exists per JRC's own site but wasn't independently confirmed in the EE catalog. **Live-tested this session**: PC's own `jrc-gsw` collection returns items versioned **`v1_3`** (e.g. tile `80E_20Nv1_3_2020`, `datetime: 2020-07-01`) — an *older* vintage than GEE's v1.4, not the newer v1.5. **Do not assume PC serves 2022–2024 coverage** — its JRC GSW copy appears to top out around 2020. | live test (PC) + [EE catalog](https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_GlobalSurfaceWater) | HIGH (PC serves v1_3/~2020) |
| License / attribution | Free, no restriction. Required credit: **"Source: EC JRC/Google"** + cite Pekel et al. 2016 (*Nature*). | [EE catalog page](https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_GlobalSurfaceWater) | HIGH |

Role in this project: **validation only**, per the original spec — cross-check MNDWI-derived water masks against JRC's independently-computed historical water occurrence, not a primary detection input.

## 4. Microsoft Global ML Building Footprints

| Question | Finding | Source | Confidence |
|---|---|---|---|
| Access | GitHub `microsoft/GlobalMLBuildingFootprints` (gzip line-delimited GeoJSON, partitioned by country + level-9 quadkey) **and** confirmed live on Planetary Computer as STAC collection `ms-buildings`, zero-auth. | live test + [GitHub repo](https://github.com/microsoft/GlobalMLBuildingFootprints) | HIGH |
| License | **CDLA-Permissive-2.0** (not ShareAlike). Repo shows a recent transition from ODbL to CDLA-Permissive-2.0 around **2026-03-11** — if any footprints were cached before that date, double check which license applied at fetch time. | [LICENSE file](https://github.com/microsoft/GlobalMLBuildingFootprints/blob/main/LICENSE) | HIGH |
| Coverage | 225 countries/regions; **India included** (a recent update alone added ~110M edited India footprints). | repo README/release notes | HIGH |
| Format | Line-delimited GeoJSON, gzip, quadkey-partitioned (not MVT vector tiles). | repo README | HIGH |

## 5. MapLibre GL JS + basemap tiles

| Question | Finding | Source | Confidence |
|---|---|---|---|
| Current version | `6.11.2` (checked directly against npm registry `latest` dist-tag). | [registry.npmjs.org/maplibre-gl](https://registry.npmjs.org/maplibre-gl/latest) | HIGH |
| Key-less **street** basemap | Yes — `tile.openstreetmap.org` raster tiles, no key. **Live-tested this session**: `200 OK` with a descriptive `User-Agent`. Usage policy requires client-side caching (≥7 days per server cache headers), no bulk/offline pre-fetch, valid UA — no published hard numeric rate limit but abusive use gets silently blocked. | [OSMF Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/) + live test | HIGH |
| Key-less **satellite** basemap | **None found that's both key-less and terms-compliant for a standalone production app.** Esri World Imagery tile URL returns `200` with no key (live-tested this session) but Esri's own terms restrict use to licensed ArcGIS Online/Enterprise contexts — not a free grant for arbitrary apps. Mapbox/MapTiler satellite layers require tokens by design. | Esri community/dev docs + live test | MEDIUM-HIGH |
| **Architecture implication** | Don't depend on a third-party satellite basemap. **Render the project's own Sentinel-2/Landsat composites as the imagery layer** — which the app needs to fetch anyway for detection, so there's no extra dependency. | — | — |

## 6. Google Earth Engine (GEE) licensing

| Question | Finding | Source | Confidence |
|---|---|---|---|
| Free tier scope | Free only for **noncommercial use**: nonprofits, research/scientific/educational, qualifying journalism. Commercial/operational use by a for-profit or government entity requires a paid account **regardless of usage volume** — the trigger is use-case classification, not a quota. | [EE FAQ](https://earthengine.google.com/faq/), [EE Terms](https://earthengine.google.com/terms/) | HIGH |
| Pricing (confirms spec's $500-2000/mo claim) | **Basic**: $500/mo (2 seats, 100 batch EECU-hr, 10 online EECU-hr, 100 GB storage). **Professional**: flat $2,000/mo. Pay-as-you-go alternative: $1.33/online-EECU-hr, $0.40/batch-EECU-hr, $0.026/GB storage. | [cloud.google.com/earth-engine/pricing](https://cloud.google.com/earth-engine/pricing) | HIGH |

**Recommendation:** Avoid GEE entirely for this project (a personal/potentially-public app, not obviously an exempt nonprofit/research entity) — confirmed unnecessary anyway since PC/CDSE/USGS cover the same data for free with no use-case restriction.

## 7. Sentinel-2 SCL (Scene Classification Layer)

Confirmed present in every L2A item (asset `SCL_20m`/`SCL_60m`), and **the STAC item metadata itself carries a per-scene SCL class-percentage histogram** (`classification:classes`) — meaning cloud/water/shadow % per scene can be read directly from search results **without downloading any pixels**, a cheap pre-filter before fetching imagery. Confirmed live this session (see JSON excerpt in [03-detection-methodology.md](03-detection-methodology.md)).

| Class | Value | Treatment |
|---|---|---|
| No data | 0 | exclude |
| Saturated/defective | 1 | exclude |
| Dark area pixels / cast shadow | 2 | exclude (false-positive risk for water) |
| Cloud shadows | 3 | exclude |
| Vegetation | 4 | — |
| Not vegetated | 5 | — |
| **Water** | **6** | **target class** |
| Unclassified | 7 | exclude |
| Cloud medium probability | 8 | exclude |
| Cloud high probability | 9 | exclude |
| Thin cirrus | 10 | exclude |
| Snow/ice | 11 | exclude (false-positive risk) |

Source: [SentiWiki S2 Processing](https://sentiwiki.copernicus.eu/web/s2-processing) (ESA/Copernicus-maintained), cross-checked against live STAC metadata. HIGH confidence.

---

## Rate limiting — now characterised (2026-09-29)

The "LOW confidence on rate limits" item below is partly resolved, empirically rather than from docs. The `/api/sas/v1/sign` endpoint throttles aggressively under sustained use: it returns `429` on the first attempt and a full retry ladder takes **82–87 s** to get through. By contrast the STAC search API and the blob reads themselves were never the problem (1.3 s and ~2.5 s respectively).

The practical consequence is an architecture rule, not just a caveat: **cache the SAS token per storage container and persist it across restarts** (it is container-scoped and lives ~1 h — both verified live). With that, a six-year query went from ~100 s to ~21 s, and the throttle stops being on the user's critical path. Full numbers in [02-data-sources-verified.md](02-data-sources-verified.md).

Still unknown: the actual published quota, and whether it is per-IP, per-token or global. Treat the above as "how it behaves under one session's load", not as documented policy.

## Unresolved items (do before scaling past a personal demo)

1. **PC rate limits / commercial-use terms** — `/terms` and `/docs/concepts/sas/` are JS-rendered SPAs; couldn't extract text via fetch this session. Check manually in a browser. **Update 2026-09-29 (MVP build):** the limit is real — running 5–6 years in parallel (each year = 1 STAC search + 3 SAS signs + 3 windowed COG reads) produced `HTTP 429 Too Many Requests` on 4 of 5 years at 6 concurrent workers, 1 of 5 at 3 workers. Mitigated in the backend with exponential-backoff retries (urllib + `GDAL_HTTP_MAX_RETRY`), concurrency capped at 3, and a sequential mop-up pass. Numeric limit still unpublished.
2. **CDSE exact numeric rate limits** — only the "30-day rolling, hourly-checked, throttle" policy is documented; no req/sec number found.
3. **USGS M2M download quota and real approval turnaround** — not documented; low-priority since PC covers Landsat with zero auth.
4. **JRC GSW end-year** — PC serves `v1_3` (~2020 vintage per live test), older than GEE's v1.4 (through 2021) or the claimed v1.5 (through 2024). If 2021-2024 historical coverage matters, query GEE directly instead of PC for this one dataset — don't claim "1984-2024" in user-facing copy based on PC's copy.
5. **MS Building Footprints license-transition date** — confirm exact ODbL→CDLA-Permissive-2.0 cutover if mixing footprints cached at different times matters for attribution.
