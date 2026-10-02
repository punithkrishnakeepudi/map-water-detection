# Limitations and Risks

These must be visible to end users (see disclaimer draft in [06](06-legal-and-licensing.md)), not just documented here.

## 1. Minimum detectable pond size: ~900 m²

Sentinel-2's 10 m pixels mean a water body needs to be roughly 30×30 m (larger in practice for irregular shapes, due to mixed edge pixels) to register reliably. Small ponds, narrow channels, and thin drainage lines will systematically read as "no water" even when present. This is a hard sensor-resolution limit, not a tunable parameter — Landsat's 30 m fallback is strictly worse on this axis, not better.

## 2. Cloud cover in monsoon regions

India (and similar monsoon climates) can have well under 20% usably-clear Sentinel-2 imagery Jun–Sep in a given month. Default to dry-season (Nov–Feb) composites for such regions, and treat "no clear imagery this year" as a distinct, visible UI state — never silently fall back to reporting "no water" when the real answer is "couldn't see."

## 3. False positives: shadows, dark roofs, swimming pools

MNDWI (like NDWI) can't fully distinguish water from other low-reflectance, SWIR-absorbing surfaces. Sentinel-2's SCL layer catches cast-shadow classes but not dark rooftops or small pools. Expect occasional false "water" flags at building-shadow edges and near dark-roofed structures — cross-checking against MS Building Footprints (does something already classified as a building sit exactly on the "water" pixel?) helps but doesn't eliminate this.

## 4. Seasonal vs. permanent water

A single wet-season scene can show temporary flooding that isn't a real, persistent water body. Mitigation: require water detection to hold across a composite/multiple scenes before calling it "the water body" for a given year, and prefer dry-season composites as the default comparison basis (see [03](03-detection-methodology.md)).

## 5. Turbid/sediment-laden water

Weakens the MNDWI signal, pushing affected pixels toward the Otsu threshold — small scene-to-scene noise can flip the verdict year to year even with no real change. Confidence scores should reflect distance from the threshold, not just "above/below."

## 6. No ground-truth verification — but now measured against independent references

Every verdict in this app is inferred from public satellite indices, not validated against site visits, survey records, or higher-resolution commercial imagery. Treat the app's output as a **lead for further investigation**, not a legal or regulatory finding — this must be explicit in the UI, not buried in a docs folder.

**What has been measured** (2026-09-29, `backend/validation/`, 9 sites chosen *by* the reference rather than by hand, ~380k scored pixels, Sentinel-2 dry-season 2020): the detector was scored against Sen2Cor's SCL water class on the same scene, and against JRC Global Surface Water v1_3.

| | precision | recall | F1 |
|---|---|---|---|
| Pooled, real-water sites (vs JRC GSW) | **0.994** | **0.768** | **0.867** |
| Pooled, real-water sites (vs Sen2Cor SCL, same scene) | 0.996 | 0.748 | 0.854 |

Per-site F1 vs JRC ranges from **0.993** (Lake Geneva, clear deep water) and **0.986** (Pallikaranai marsh) down to **0.703** (Lake Nasser) and **0.696** (Poyang Lake).

**The asymmetry is the thing to understand: precision ~0.99, recall ~0.77.** When the app says "water", it is almost always right. When it says "no water", it is meaningfully less reliable — it misses roughly a quarter of the water a reference dataset sees. For an app whose headline claim is *"the water disappeared"*, the unreliable direction is the one the headline rests on: a missed detection reads as a disappearance that never happened. Recall is worst on turbid and sediment-laden water (Poyang, Nasser), exactly as #5 predicts.

Part of the recall gap is the references' own fault — JRC occurrence averages 1984–2020, so at a shrinking reservoir like Mead it counts now-dry shoreline as "permanent water", which is real change, not detector error. But Test A rules that explanation out on its own: Sen2Cor, reading **the same pixels at the same instant**, also finds water the detector misses (recall 0.748). So the under-detection is genuine, not an artefact of reference vintage.

**Not yet done:** no tuning has been attempted to recover that recall, because raising sensitivity trades against the 0.99 precision. See `backend/validation/README.md` to re-run the measurement before and after any such change.

## 7. Resolution ceiling means "replaced by a small building" can be invisible

If a pond was small (near or under the ~900 m² floor) and is replaced by an equally small structure, the change may not be cleanly detectable either before or after — both states can look ambiguous at 10 m. This app is best suited to water bodies large enough to be unambiguous at Sentinel-2 resolution (which, per [09-test-locations.md](09-test-locations.md), covers most of the visually-striking "lake became a neighborhood" cases this app targets, even if it misses small individual ponds).

## 8. Data-source coverage gaps for pre-2015 disappearance

If a water body was already gone before 2015 (Sentinel-2's effective start), Sentinel-2 alone can only confirm "no water since at least 2015" — it can't show when the water existed. The app's Landsat fallback (2013+) and JRC GSW cross-check (1984+) partially extend this, but JRC's own upper-bound coverage year needs re-confirming (see [research-log.md](research-log.md)) and neither dataset goes back arbitrarily far. See the Songdo case in [09-test-locations.md](09-test-locations.md) for a concrete example where this limitation bites.

## 9. Unconfirmed platform rate limits

Planetary Computer's own commercial-use/rate-limit terms weren't retrievable as text this session (JS-rendered page) — see [research-log.md](research-log.md). Unlikely to matter at personal/demo scale; re-verify before scaling.
