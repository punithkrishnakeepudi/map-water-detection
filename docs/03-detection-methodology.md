# Detection Methodology

## MNDWI (Modified Normalized Difference Water Index)

```
MNDWI = (Green - SWIR) / (Green + SWIR)
```

For Sentinel-2 L2A: `Green = B03` (10 m), `SWIR = B11` (20 m, resample/align to 10 m or work at 20 m). MNDWI separates open water from built-up/soil better than plain NDWI (which uses NIR instead of SWIR) because built-up surfaces reflect strongly in SWIR — the original NDWI often confuses water with urban shadow/dark roofs, which MNDWI corrects for. This is the standard justification in the remote-sensing literature for using MNDWI over NDWI for exactly this app's failure mode (buildings replacing water).

Output range: `-1` to `+1`. Water pixels trend positive, non-water negative — but the exact cut point isn't a fixed universal number, hence Otsu thresholding below.

## Otsu thresholding (not a fixed threshold)

A fixed MNDWI cutoff (e.g. `> 0`) is a common shortcut but is scene-dependent — turbidity, sensor calibration drift, and atmospheric correction residuals shift the water/non-water separation point scene to scene. **Otsu's method** picks the threshold that best splits the scene's own MNDWI histogram into two classes by maximizing between-class variance — it's parameter-free and adapts per scene, which is why it's the MVP choice over hand-tuning a fixed number. `skimage.filters.threshold_otsu` implements this in one line (see [notebooks/mndwi_validation.ipynb](../notebooks/mndwi_validation.ipynb)).

Failure mode: Otsu assumes a roughly bimodal histogram (clear water vs. clear non-water). A scene with very little or no water, or very noisy/cloudy pixels, can produce a nonsense threshold.

**This failure was measured, not just anticipated** (2026-09-29, `backend/validation/`). On a pure Sahara-desert AOI containing no water at all, unguarded Otsu returned a threshold of **−0.460** and labelled **55.2% of the scene as water**, with precision **0.000** against Sen2Cor's independent classifier. Central Delhi: threshold −0.259, **49.1% false positives** off dark roofs and shadow. The app would have confidently reported a lake in the middle of the Sahara.

Note that the ">60% of the AOI is water" heuristic sketched in earlier drafts of this doc would **not** have caught either case — Sahara came in at 55.2%, just under the line.

### The guard actually used: `MNDWI_WATER_FLOOR`

Open water reflects more green than SWIR, so **its MNDWI is positive by construction**. Otsu's job is to adapt the cut point *within* that regime — it must never be allowed to place the cut in negative territory, because on a water-free scene it still returns a split; it just bisects dry land. So the effective threshold is `max(otsu, 0.0)`.

This is a physical constraint, not a tuned constant, which is why it generalises. Measured separation across the 9 validation sites: every site with genuine water produced a raw Otsu threshold of **≥ −0.011** (Geneva +0.193, Poyang +0.244, Aral +0.319, Pallikaranai +0.169, Nasser +0.127, Mead −0.011), while the two catastrophic sites sat at **−0.259 and −0.460**. Flooring at 0 separates them cleanly.

Cost of the guard, measured on the same sites: pooled F1 on real water went **0.868 → 0.867**. Sahara false positives went **55.2% → 0.0%** (0 of 40,200 pixels), Delhi **49.1% → 2.0%**. The API reports `otsu_threshold_raw` and `threshold_floored` alongside the final threshold so the correction is visible, not hidden.

## Cloud masking — Sentinel-2 SCL layer

Every L2A scene ships a Scene Classification Layer (`SCL_20m`/`SCL_60m`) with per-pixel class labels. Confirmed live via STAC metadata (see [research-log.md](research-log.md)):

| Value | Class | MNDWI treatment |
|---|---|---|
| 0 | No data | mask out |
| 1 | Saturated/defective | mask out |
| 2 | Dark area pixels / cast shadow | mask out (shadow false-positive risk) |
| 3 | Cloud shadows | mask out |
| 4 | Vegetation | keep |
| 5 | Not vegetated | keep |
| 6 | Water (Sen2Cor's own classifier) | keep — cross-check against MNDWI result |
| 7 | Unclassified | mask out |
| 8 | Cloud medium probability | mask out |
| 9 | Cloud high probability | mask out |
| 10 | Thin cirrus | mask out |
| 11 | Snow/ice | mask out (can mimic water spectrally) |

Practical bonus: the STAC item's own metadata carries a `classification:classes` percentage breakdown per scene (confirmed live) — cloud/water % can be read **before downloading any pixels**, so scene selection (pick the clearest scene per year) is a metadata-only filter.

## Accuracy expectations

- **~97% on clear, cloud-free Sentinel-2 imagery** for MNDWI + Otsu, per the literature backing this spec's original claim. This is a **best-case number under good conditions**, not a guarantee — treat every reported confidence in the app's UI as scene-quality-dependent, and show the source scene + cloud % alongside every verdict so a user can judge it themselves.
- Confidence degrades with: cloud/shadow contamination not fully caught by SCL, turbid/sediment-laden water (spectrally duller, can sit near the threshold), and small water bodies near the sensor's resolution limit (mixed pixels).

## Known failure cases (must be visible in the UI, not buried in docs)

1. **Minimum detectable pond size** — ~900 m² for 10 m Sentinel-2 pixels (roughly a 30×30 m water body, and in practice larger for irregular shapes since mixed edge pixels dilute the signal). Smaller ponds will read as "no water" even if present.
2. **Shadows, dark roofs, swimming pools** — spectrally similar to water in NDWI/MNDWI; SCL's shadow classes catch some cast-shadow cases but not dark rooftops or small pools, which can produce false positives.
3. **Turbid/sediment-laden water** — weaker MNDWI signal, can sit near the Otsu threshold and flip year to year on noise rather than real change.
4. **Seasonal vs. permanent water** — a single scene can catch monsoon flooding that isn't a permanent water body. Mitigate with dry-season composites (see below) and by requiring water detection to persist across multiple scenes/years before calling it "the water body," not a one-off wet pixel.
5. **Cloud cover in monsoon regions** — India Jun–Sep can have well under 20% usably clear Sentinel-2 scenes in a given month; the app should default to **dry-season (Nov–Feb) composites** for India-region queries rather than a single date, and should widen the search window (not just a single month) when the default dry-season window itself has no clear scene.

## Composite strategy for MVP

For each year: query the dry-season window (Nov 1–Feb 28, adjustable per hemisphere), pick the scene(s) with lowest `eo:cloud_cover` (metadata-only pre-filter, see above), compute MNDWI, mask with SCL, threshold with Otsu. If zero usably-clear scenes exist in the window, widen it (e.g. full year) before giving up and reporting "no clear imagery for this year" — this is a distinct UI state from "no water," never conflate the two.

## Deferred to v2: deep learning (U-Net / SAM)

Not used for MVP. Reasons: needs labeled training data or a foundation-model API (compute/GPU cost), adds a model-serving dependency, and MNDWI + Otsu already hits ~97% on the clear-imagery case that's the MVP's primary use case. Revisit only if MVP usage surfaces systematic MNDWI failure modes (e.g. persistent turbid-water misses) that composite/threshold tuning can't fix.
