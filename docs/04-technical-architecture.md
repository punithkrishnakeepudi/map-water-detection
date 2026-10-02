# Technical Architecture

## Stack

- **Frontend**: React + MapLibre GL JS (2D map only for MVP — no 3D/Cesium, see [08-mvp-specification.md](08-mvp-specification.md))
- **Backend**: Python + FastAPI
- **Geospatial**: `rasterio`, `geopandas`, `shapely`, `numpy`, `scikit-image` (Otsu)
- **Database**: PostgreSQL + PostGIS — stores computed water masks (as GeoJSON/raster refs) and cached building-footprint tiles, so repeat queries on the same AOI don't re-hit the data APIs. **MVP as built uses SQLite** (`backend/app/cache.py`): the app runs no spatial queries of its own (Planetary Computer's STAC does that), so a key-value cache keyed by rounded point + year + buffer is sufficient for a single instance. Move to Postgres+PostGIS when the cache must be shared across instances or queried spatially.
- **Deployment**: Hugging Face Spaces (free tier) or Render free tier

## Data flow

```
User clicks a point on the map (React + MapLibre)
        │
        ▼
FastAPI: build AOI bbox around the point
        │
        ▼
For each year in range (default 2021–2026):
        │
        ├─ Query Planetary Computer STAC search
        │    (sentinel-2-l2a, bbox, dry-season datetime window)
        │    → filter by eo:cloud_cover / classification:classes
        │      metadata BEFORE downloading pixels
        │
        ├─ Sign the chosen scene's B03/B11/SCL asset hrefs
        │    (PC SAS sign endpoint, no auth)
        │
        ├─ rasterio: read windowed B03/B11/SCL for the AOI only
        │    (not the whole scene — keeps bandwidth/compute small)
        │
        ├─ Compute MNDWI = (B03-B11)/(B03+B11)
        ├─ Mask clouds/shadows/snow via SCL
        ├─ Otsu threshold → binary water mask for that year
        │
        └─ If no clear scene in dry-season window → widen window,
             else mark year as "no clear imagery" (not "no water")
        │
        ▼
Cross-check: query ms-buildings (PC) for the AOI's *current* footprints
Cross-check: query jrc-gsw (PC) historical water occurrence for the AOI
        │
        ▼
Store per-year result (mask GeoJSON, confidence, source scene id,
cloud %, verdict) in PostGIS
        │
        ▼
Return timeline JSON to frontend → MapLibre overlay + timeline slider
```

## Why Planetary Computer over the originally-specified CDSE/OAuth path

Verified live this session (see [02-data-sources-verified.md](02-data-sources-verified.md)): PC serves the same Sentinel-2 L2A data, plus Landsat, JRC GSW, and MS Building Footprints, all through one zero-auth STAC + SAS-sign mechanism. This removes an entire subsystem (OAuth2 token refresh, or S3 credential distribution/rotation) from the MVP backend. CDSE stays documented as a fallback (see [02](02-data-sources-verified.md)) — swapping providers is a single STAC-client base-URL change since both are standard STAC APIs, not a rearchitecture.

## Key design choices and why

- **Windowed reads, not full-scene downloads**: a Sentinel-2 scene is ~500 MB+ across all bands; the app only ever needs a small AOI window. `rasterio` supports HTTP range-read windowing directly against the signed COG URL — no need to download a full scene to disk.
- **Metadata-first scene selection**: STAC item properties (`eo:cloud_cover`, `classification:classes`) let the backend pick the best scene per year without downloading any pixels, keeping bandwidth and latency down.
- **No satellite basemap dependency**: the "satellite" layer the user sees *is* the fetched Sentinel-2 composite, not a third-party imagery basemap (see [02](02-data-sources-verified.md) for why no free key-less one exists).
- **PostGIS cache**: satellite/detection compute is the expensive step; caching per-AOI-per-year results means a second user (or the same user re-visiting a location) doesn't repeat the STAC query + pixel read + MNDWI compute.

## Latency: what actually costs time (measured 2026-09-29)

A fresh six-year query originally took ~100 s. Profiling showed the cost was **not** where it looked:

| Step | Time |
|---|---|
| STAC search | 1.3 s |
| **SAS `/sign` call, cold** | **82–87 s** |
| SAS `/sign`, token cached | 0.0001 s |
| 3 band windows, read in parallel | 2.5 s |
| One further year, token cached | 3.0 s |

Almost the entire wait was the Planetary Computer signing endpoint throttling us, and a naive implementation called it 18 times per query (3 assets × 6 years). Pixel reads — the part that *looks* expensive — were never the problem.

Four changes, in order of impact:

1. **Cache the SAS token per storage container** (`app/pc_client.py`). PC's token is container-scoped, not per-blob, so one token covers every asset of every scene in that container. Minting is serialised per container, so concurrent workers can't all miss and stampede the endpoint that is already throttling us.
2. **Persist tokens in SQLite** (`app/cache.py`), so a restart or redeploy doesn't re-pay the ~85 s.
3. **Warm all four containers at startup**, in a background thread (`app/main.py`). Sentinel-2, land cover, JRC GSW and Landsat each live in their own container and so need their own token — a location that needed a fresh land-cover *and* JRC tile took 60 s until all four were warmed, versus 21 s for one that didn't.
4. **Read the three bands in parallel** rather than in sequence (`_read_bands`), turning three round-trip latencies into one.

**Measured result: ~100 s → 20–34 s for a fresh location; ~0.2 s cached.**

One thing deliberately *not* done: raising the per-year thread pool from 3 to 6. It was tried and measured as a **regression** (141 s vs ~98 s) — with three parallel band reads per year that puts ~18 requests in flight, and PC throttles the burst harder than the extra parallelism buys back. The pool stays at 3.

## Explicitly out of scope for MVP

- 3D globe (Cesium) — defer until the 2D flow is proven ([DO NOT list](DECISION.md))
- Deep learning water segmentation (U-Net/SAM) — see [03-detection-methodology.md](03-detection-methodology.md)
- User accounts/auth — no login system for MVP
- Google Earth Engine — see [06-legal-and-licensing.md](06-legal-and-licensing.md)
