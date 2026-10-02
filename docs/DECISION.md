# DECISION

## Recommended architecture

React + MapLibre GL JS (2D) frontend → FastAPI backend (rasterio/geopandas/numpy/scikit-image) → PostgreSQL+PostGIS cache. Deploy on Hugging Face Spaces or Render free tier. Full detail: [04-technical-architecture.md](04-technical-architecture.md).

## Recommended data sources

**Primary: Microsoft Planetary Computer STAC** — a research finding, not in the original spec: PC hosts Sentinel-2 L2A, Landsat, JRC GSW, *and* MS Building Footprints on one platform, all confirmed **zero-auth** (live-tested this session: anonymous search → SAS-sign → range-read, no account/key anywhere). This is simpler than the originally-specified CDSE OAuth2/S3-credential path, which stays documented as a fallback ([02](02-data-sources-verified.md)). Same STAC standard either way, so switching later is a config change, not a rearchitecture.

## Detection method

MNDWI + Otsu thresholding, masked by Sentinel-2's SCL layer, on dry-season composites, with per-scene metadata pre-filtering (cloud %) before any pixel download. ~97% accuracy is a best-case, clear-imagery number — shown as a per-year confidence tied to source-scene quality, not a flat claim. Deep learning deferred to v2. Full detail: [03-detection-methodology.md](03-detection-methodology.md).

## Estimated costs

**MVP: $0/month**, confirmed viable given the zero-auth PC finding above. Scales to $5–20/mo for durable hosting/storage, $50–200/mo only if v2 adds GPU-based deep learning. Full detail: [05-cost-analysis.md](05-cost-analysis.md).

## Top 5 risks

1. **No free key-less satellite basemap exists** — worked around by rendering the app's own fetched composites as the imagery layer instead of a third-party basemap; not a blocker, but a real architecture constraint (see [02](02-data-sources-verified.md)).
2. **Minimum detectable water body ~900 m²** — small ponds are structurally invisible to this pipeline; must be stated to users, not just documented ([07](07-limitations-and-risks.md)).
3. **Cloud cover in monsoon regions** can leave whole years with no usable imagery — must be a distinct, visible UI state ("no clear imagery"), never conflated with "no water" ([07](07-limitations-and-risks.md)).
4. **PC's own rate-limit/commercial-use terms unconfirmed this session** (JS-rendered ToS page) — low risk at demo scale, needs a manual check before real traffic ([research-log.md](research-log.md)).
5. **No ground-truth verification anywhere in the pipeline** — every verdict is inferred from public indices; must be framed to users as a lead for investigation, not a legal/regulatory finding ([07](07-limitations-and-risks.md)).

## MVP scope

See [08-mvp-specification.md](08-mvp-specification.md) — map + search, 2021–2026 Sentinel-2 MNDWI/Otsu timeline with dry-season composites, building-footprint and JRC-GSW cross-checks, PNG+GeoJSON export, explicit fallback when no water found in the default window. Cesium/3D, deep learning, and user accounts explicitly deferred.

## Unresolved questions requiring your approval

1. **OK to make Planetary Computer the primary data source** (departing from the spec's explicit Copernicus-STAC-as-primary), keeping CDSE as documented fallback? This is the single biggest architecture change from your original spec, backed by live verification this session.
2. **The 3 test locations in [09-test-locations.md](09-test-locations.md)** are researched candidates with approximate coordinates, not ground-truthed — OK to proceed treating them as "confirm during Phase 3 notebook" rather than delaying further for manual survey?
3. **JRC GSW's exact end-year coverage** (1984–2021 confirmed; 2022–2024 unconfirmed for the queryable collection) — OK to ship MVP copy saying "historical reference through at least 2021," adjusting later if 2022–2024 coverage is confirmed?
4. **Deployment target**: Hugging Face Spaces vs. Render free tier — no technical blocker either way; your preference?
