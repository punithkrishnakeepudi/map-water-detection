"""Core MNDWI + Otsu detection pipeline.

Method verified in notebooks/mndwi_validation.ipynb against live Sentinel-2
data via Planetary Computer. See docs/03-detection-methodology.md for the
rationale behind each choice (MNDWI over NDWI, Otsu over a fixed threshold,
SCL-based cloud masking).
"""
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

# rasterio's actual pixel reads go through GDAL's own HTTP client (not the
# urllib calls in pc_client.py), which needs its own retry config to survive
# the 429s confirmed under concurrent load (docs/research-log.md).
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")

# COG-over-HTTP tuning. Without these GDAL treats a remote blob like a local
# file: on open it lists the "directory" (a full container listing against blob
# storage) and probes for sidecar files (.aux.xml, .ovr, .msk) that do not
# exist, costing several round-trips before a single pixel is read. The AOI
# windows here are tiny - 60x60 px - so latency, not bandwidth, is the cost.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.TIF,.tiff")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_VERSION", "2")
# Pull the COG header in one request instead of discovering it in pieces.
os.environ.setdefault("GDAL_INGESTED_BYTES_AT_OPEN", "32768")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("VSI_CACHE_SIZE", "10000000")

import rasterio
import rasterio.warp
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from scipy.ndimage import zoom
from shapely.geometry import shape, mapping
from shapely.ops import transform, unary_union
from skimage.filters import threshold_otsu
from rasterio import features

from . import pc_client
from .pc_client import imagery_tile_url

# SCL classes to exclude from analysis (cloud/shadow/snow/nodata/unclassified).
# 6 = water (Sen2Cor's own classifier, kept as a cross-check, not excluded).
SCL_EXCLUDE = [0, 1, 2, 3, 7, 8, 9, 10, 11]

METERS_PER_DEGREE_LAT = 111_320.0

# Open water reflects more green than SWIR, so its MNDWI is positive by
# construction. Otsu adapts the cut point *within* that regime; it must never
# be allowed to place the cut in negative territory, because on a scene with
# no water at all Otsu still returns a split - it just bisects dry land.
#
# Measured (scratchpad accuracy harness, 9 reference-selected sites): on pure
# Sahara desert, unguarded Otsu returned a threshold of -0.460 and labelled
# 55.2% of the AOI water, with precision 0.000 against Sen2Cor. Central Delhi:
# -0.259 and 49.1% false positives. Every site with genuine water produced a
# threshold >= -0.011, so flooring at 0 removes the failure without touching
# the true-positive cases.
MNDWI_WATER_FLOOR = 0.0


def point_to_bbox(lat: float, lon: float, buffer_m: float) -> list[float]:
    """Square bbox of side 2*buffer_m centered on (lat, lon)."""
    dlat = buffer_m / METERS_PER_DEGREE_LAT
    dlon = buffer_m / (METERS_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.1))
    return [lon - dlon, lat - dlat, lon + dlon, lat + dlat]


def _read_window(signed_href: str, bbox: list[float]) -> tuple[np.ndarray, dict]:
    with rasterio.open(signed_href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        window = from_bounds(*b, transform=src.transform)
        arr = src.read(1, window=window).astype("float32")
        win_transform = src.window_transform(window)
        return arr, {"crs": src.crs, "transform": win_transform}


def _resample_to(arr: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    if arr.shape == target_shape:
        return arr
    zy = target_shape[0] / arr.shape[0]
    zx = target_shape[1] / arr.shape[1]
    return zoom(arr, (zy, zx), order=0)


@dataclass
class SceneResult:
    status: str  # "ok" | "point_obscured" | "no_clear_imagery" | "degenerate_scene" | "error"
    scene_id: str | None = None
    date: str | None = None
    cloud_cover_pct: float | None = None
    otsu_threshold: float | None = None
    otsu_threshold_raw: float | None = None
    threshold_floored: bool = False
    water_fraction_aoi: float | None = None
    valid_pixel_fraction: float | None = None
    water_at_point: bool | None = None
    mndwi_at_point: float | None = None
    confidence_pct: float | None = None
    water_geojson: dict | None = None
    error: str | None = None
    scenes_considered: int = 0
    window_used: str | None = None
    sensor: str = "sentinel-2"
    imagery_tiles: str | None = None


def pick_best_scene(bbox: list[float], datetime_range: str, max_cloud: float = 60) -> list[dict]:
    feats = pc_client.stac_search("sentinel-2-l2a", bbox, datetime_range, limit=20)
    feats = [f for f in feats if f["properties"].get("eo:cloud_cover", 100) <= max_cloud]
    feats.sort(key=lambda f: f["properties"].get("eo:cloud_cover", 100))
    return feats


def _read_bands(assets: dict, names: list[str], bbox: list[float]) -> list[tuple]:
    """Sign and window-read several bands at once.

    The three bands are independent HTTP reads of roughly equal cost, so doing
    them in sequence spends three round-trip latencies where one will do. The
    first sign() mints the container token and the rest hit the cache.
    """
    hrefs = [pc_client.sign(assets[n]["href"]) for n in names]
    with ThreadPoolExecutor(max_workers=len(hrefs)) as pool:
        return list(pool.map(lambda h: _read_window(h, bbox), hrefs))


def analyze_scene(feat: dict, bbox: list[float], lat: float, lon: float) -> SceneResult:
    assets = feat["assets"]
    try:
        (green, geo), (swir_raw, _), (scl_raw, _) = _read_bands(
            assets, ["B03", "B11", "SCL"], bbox
        )
    except Exception as e:  # noqa: BLE001 - surface any network/format issue as a scene-level error
        return SceneResult(status="error", error=pc_client.user_facing_error(e))

    if green.size == 0:
        return SceneResult(status="degenerate_scene", error="empty AOI window")

    swir = _resample_to(swir_raw, green.shape)
    scl = _resample_to(scl_raw, green.shape)

    result = water_verdict(
        green,
        swir,
        valid=~np.isin(scl, SCL_EXCLUDE),
        geo=geo,
        lat=lat,
        lon=lon,
        scene_id=feat["id"],
        date=feat["properties"]["datetime"],
        cloud_cover_pct=feat["properties"].get("eo:cloud_cover"),
    )
    # True-colour tiles for the very scene this verdict came from, so the
    # imagery the user looks at is the imagery that was measured.
    result.imagery_tiles = imagery_tile_url("sentinel-2-l2a", feat["id"], ["visual"])
    return result


def water_verdict(
    green: np.ndarray,
    swir: np.ndarray,
    valid: np.ndarray,
    geo: dict,
    lat: float,
    lon: float,
    scene_id: str,
    date: str,
    cloud_cover_pct: float | None,
) -> SceneResult:
    """MNDWI -> Otsu -> per-point verdict. Sensor-agnostic.

    Takes green/SWIR already in comparable units (reflectance for Landsat,
    raw DN for Sentinel-2 - MNDWI is a normalised ratio, so a common linear
    *scale* cancels; a nonzero *offset* does not, which is why landsat.py
    applies its scale/offset before calling in here).
    """
    denom = green + swir
    mndwi = np.where(denom == 0, 0, (green - swir) / np.where(denom == 0, 1, denom))

    valid_pixels = mndwi[valid]
    if valid_pixels.size < 25 or np.nanstd(valid_pixels) < 1e-6:
        # Too few readable pixels, or a flat histogram Otsu can't split
        # meaningfully (docs/03-detection-methodology.md, "Failure mode").
        return SceneResult(
            status="degenerate_scene",
            scene_id=scene_id,
            date=date,
            cloud_cover_pct=cloud_cover_pct,
            valid_pixel_fraction=float(valid.mean()),
        )

    otsu = float(threshold_otsu(valid_pixels))
    # Floor, never lower: Otsu may raise the bar for turbid or bright scenes,
    # but it may not call negative-MNDWI ground "water" (see MNDWI_WATER_FLOOR).
    thresh = max(otsu, MNDWI_WATER_FLOOR)
    water_mask = (mndwi > thresh) & valid
    water_frac = float(water_mask.sum() / valid.sum()) if valid.sum() > 0 else 0.0

    # sample the pixel nearest the queried point
    row, col = rasterio.transform.rowcol(geo["transform"], lon, lat)
    row = min(max(row, 0), mndwi.shape[0] - 1)
    col = min(max(col, 0), mndwi.shape[1] - 1)
    mndwi_at_point = float(mndwi[row, col])
    point_is_valid = bool(valid[row, col])
    water_at_point = bool(water_mask[row, col]) if point_is_valid else None

    confidence = min(99.0, 50.0 + abs(mndwi_at_point - thresh) * 250.0)
    if not point_is_valid:
        confidence = 0.0  # point itself is cloud/shadow/nodata - can't claim any confidence

    return SceneResult(
        # The AOI-wide numbers below are valid either way, but if the queried
        # pixel itself was cloud/shadow/nodata we cannot say water-or-not *there*.
        # That must read as "couldn't see", never as "no water" (docs/07, #2).
        status="ok" if point_is_valid else "point_obscured",
        scene_id=scene_id,
        date=date,
        cloud_cover_pct=cloud_cover_pct,
        otsu_threshold=thresh,
        otsu_threshold_raw=otsu,
        threshold_floored=bool(otsu < MNDWI_WATER_FLOOR),
        water_fraction_aoi=water_frac,
        valid_pixel_fraction=float(valid.mean()),
        water_at_point=water_at_point,
        mndwi_at_point=mndwi_at_point,
        confidence_pct=round(confidence, 1),
        water_geojson=_polygonize(water_mask, geo),
    )


def _polygonize(water_mask: np.ndarray, geo: dict) -> dict | None:
    """Water mask -> WGS84 GeoJSON for the map overlay. Best-effort."""
    try:
        shapes_gen = features.shapes(
            water_mask.astype("uint8"), mask=water_mask, transform=geo["transform"]
        )
        polys = [shape(geom) for geom, val in shapes_gen if val == 1]
        if not polys:
            return None
        merged = unary_union(polys)
        # merged is in the source raster's CRS (e.g. UTM) - reproject to WGS84
        # lon/lat before simplifying, since the frontend map expects EPSG:4326.
        src_crs = geo["crs"]

        def _to_wgs84(xs, ys):
            return rasterio.warp.transform(src_crs, "EPSG:4326", xs, ys)

        return mapping(transform(_to_wgs84, merged).simplify(0.000005, preserve_topology=True))
    except Exception:  # noqa: BLE001 - overlay is cosmetic, never block the core verdict
        return None


def analyze_year(lat: float, lon: float, year: int, buffer_m: float, max_cloud: float = 60) -> SceneResult:
    bbox = point_to_bbox(lat, lon, buffer_m)

    # Primary: dry-season window ending in `year` (Nov 1 of year-1 -> Feb 28 of year).
    # Rationale: docs/03-detection-methodology.md - dry-season composites avoid
    # monsoon-driven false negatives from cloud cover / temporary flooding.
    primary_range = f"{year - 1}-11-01/{year}-02-28"
    feats = pick_best_scene(bbox, primary_range, max_cloud)
    window_used = "dry_season"

    if not feats:
        # Fallback: widen to the full calendar year, per the spec's
        # "extend further if no [clear imagery] found" requirement.
        fallback_range = f"{year}-01-01/{year}-12-31"
        feats = pick_best_scene(bbox, fallback_range, max_cloud)
        window_used = "full_year_fallback"

    if not feats:
        return SceneResult(status="no_clear_imagery", scenes_considered=0)

    result = analyze_scene(feats[0], bbox, lat, lon)
    result.scenes_considered = len(feats)
    result.window_used = window_used
    return result
