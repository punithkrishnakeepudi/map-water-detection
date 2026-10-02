# MVP Specification

## In scope

1. MapLibre GL JS map with a search box (geocoding — e.g. free Nominatim/OSM search, subject to its own usage policy) and click-to-select a point.
2. Fetch Sentinel-2 L2A via Planetary Computer STAC for years 2021–2026 (5-year default window, per the original spec).
3. Compute MNDWI per year using a dry-season composite (Nov–Feb, adjustable), selecting the clearest available scene via STAC metadata pre-filtering (`eo:cloud_cover`, `classification:classes`) before downloading pixels.
4. Apply Otsu thresholding → binary water mask per year, masked by the SCL cloud/shadow/snow classes.
5. Display a timeline slider with the water-extent overlay per year, plus per-year confidence and source-scene metadata (scene id, date, cloud %). **Includes a Satellite / Street map basemap toggle** (satellite by default): the satellite layer is PC's tile rendering of that year's own scene, so it changes as the slider moves — see [02](02-data-sources-verified.md). Each year's `imagery_tiles` URL is returned alongside its verdict.
6. Show an independent built-up cross-check for the AOI. **Implemented against `io-lulc-annual-v02` (Esri/Impact Observatory 10 m annual land cover), not MS Building Footprints** — PC's `ms-buildings` items are continent-sized partitions that cannot answer "is there a building *here*" without a GeoParquet + quadkey pipeline, and a naive check against them reports buildings in the open ocean. See [02](02-data-sources-verified.md). The land-cover route answers the same question per-year, honestly, with no new dependencies. MS footprints stay a v2 item.
7. Export the result as a PNG (map snapshot) and GeoJSON (water masks + metadata) report.
8. Explicit "no clear imagery" UI state distinct from "no water detected" (see [07](07-limitations-and-risks.md), #2).
9. Auto-extend further back (toward Landsat 2013+ / JRC GSW 1984+ cross-check) when no water is found anywhere in the default 5-year window, per the original spec's step 3 — surfaced as a distinct, user-visible fallback step, not silent. **Implemented** in `backend/app/landsat.py` + `_extended_search()`: probes `landsat-c2-l2` backwards from the window start in 3-year steps to a 2013 floor, stopping at the first year with water. Same MNDWI/Otsu verdict logic, different bands (`green`/`swir16`), QA_PIXEL bitmask instead of SCL, and surface-reflectance scale/offset applied before the index (a common scale cancels in the ratio, the offset does not). Landsat 7 is deprioritised behind OLI scenes because of its post-2003 SLC-off data gaps. Rendered in its own "Looked further back" panel, visually separated from the Sentinel-2 series.
10. Visible disclaimer/attribution footer (draft in [06](06-legal-and-licensing.md)).

## Explicitly deferred to v2+

- Landsat as anything beyond the "extend further back" fallback path (already covered by #9 above at a basic level)
- Deep-learning segmentation (U-Net/SAM) — see [03](03-detection-methodology.md)
- 3D globe (Cesium) — 2D MapLibre only until proven
- User accounts / saved searches / history
- Bulk/batch AOI processing or any country-scale monitoring workflow
- Automated recurring monitoring/alerts for a saved location

## Acceptance check before calling MVP "done"

Run the 3 test locations in [09-test-locations.md](09-test-locations.md) through the pipeline and confirm the year-by-year output matches the documented expected pattern for each (within the stated confidence/limitation caveats) — this is the go/no-go gate before considering the detection pipeline validated, separate from writing the code itself.
