"""Landsat Collection-2 Level-2 fallback, for reaching back before Sentinel-2.

Used only when Sentinel-2 finds no water anywhere in the requested window
(MVP spec #9, docs/08-mvp-specification.md): Sentinel-2's usable archive
starts ~2015, so a water body that vanished before then shows a flat
"no water" timeline with no transition in it. Landsat reaches back to 1982
(and usefully to 2013 for OLI), which is often enough to catch the change.

Same MNDWI + Otsu method as the Sentinel-2 path - only the band names,
the cloud mask, and the reflectance scaling differ, so the verdict logic
itself is shared via detection.water_verdict().
"""
import numpy as np

from . import pc_client
from .detection import SceneResult, _read_bands, _resample_to, point_to_bbox, water_verdict

# Collection-2 QA_PIXEL bit flags. Bits 0-5 are all reasons to drop a pixel:
# 0 fill, 1 dilated cloud, 2 cirrus, 3 cloud, 4 cloud shadow, 5 snow.
# (Bit 6 "clear" and bit 7 "water" are informational, not exclusions.)
QA_EXCLUDE_BITS = 0b111111

# Landsat 7's scan-line corrector failed in 2003, leaving wedge-shaped data
# gaps in every later scene. Usable, but only after OLI sensors are exhausted.
_SLC_OFF_PLATFORM = "landsat-7"


def _scale_offset(feat: dict, asset: str) -> tuple[float, float]:
    """Surface-reflectance DN -> reflectance. Offset is nonzero, so it must be
    applied before MNDWI: a common scale cancels in the ratio, an offset does not."""
    bands = feat["assets"][asset].get("raster:bands") or [{}]
    return float(bands[0].get("scale", 1.0)), float(bands[0].get("offset", 0.0))


def pick_scenes(bbox: list[float], datetime_range: str, max_cloud: float = 60) -> list[dict]:
    feats = pc_client.stac_search("landsat-c2-l2", bbox, datetime_range, limit=20)
    feats = [f for f in feats if f["properties"].get("eo:cloud_cover", 100) <= max_cloud]
    # Cloud cover first, but push SLC-off Landsat 7 behind any usable OLI scene.
    feats.sort(
        key=lambda f: (
            f["properties"].get("platform") == _SLC_OFF_PLATFORM,
            f["properties"].get("eo:cloud_cover", 100),
        )
    )
    return feats


def analyze_scene(feat: dict, bbox: list[float], lat: float, lon: float) -> SceneResult:
    assets = feat["assets"]
    try:
        (green_raw, geo), (swir_raw, _), (qa_raw, _) = _read_bands(
            assets, ["green", "swir16", "qa_pixel"], bbox
        )
    except Exception as e:  # noqa: BLE001 - surface any network/format issue as a scene-level error
        return SceneResult(status="error", error=pc_client.user_facing_error(e), sensor="landsat")

    if green_raw.size == 0:
        return SceneResult(status="degenerate_scene", error="empty AOI window", sensor="landsat")

    swir_raw = _resample_to(swir_raw, green_raw.shape)
    qa = _resample_to(qa_raw, green_raw.shape)

    g_scale, g_off = _scale_offset(feat, "green")
    s_scale, s_off = _scale_offset(feat, "swir16")
    green = green_raw * g_scale + g_off
    swir = swir_raw * s_scale + s_off

    # DN 0 is the fill value in both bands, and maps to a bogus negative
    # reflectance once the offset is applied - drop it alongside the QA flags.
    valid = ((qa.astype("uint16") & QA_EXCLUDE_BITS) == 0) & (green_raw > 0) & (swir_raw > 0)

    result = water_verdict(
        green,
        swir,
        valid=valid,
        geo=geo,
        lat=lat,
        lon=lon,
        scene_id=feat["id"],
        date=feat["properties"]["datetime"],
        cloud_cover_pct=feat["properties"].get("eo:cloud_cover"),
    )
    result.sensor = "landsat"
    result.imagery_tiles = pc_client.imagery_tile_url(
        "landsat-c2-l2", feat["id"], ["red", "green", "blue"]
    )
    return result


def analyze_year(lat: float, lon: float, year: int, buffer_m: float, max_cloud: float = 60) -> SceneResult:
    """One Landsat verdict for `year`, same dry-season-first strategy as Sentinel-2."""
    bbox = point_to_bbox(lat, lon, buffer_m)

    feats = pick_scenes(bbox, f"{year - 1}-11-01/{year}-02-28", max_cloud)
    window_used = "dry_season"
    if not feats:
        feats = pick_scenes(bbox, f"{year}-01-01/{year}-12-31", max_cloud)
        window_used = "full_year_fallback"
    if not feats:
        return SceneResult(status="no_clear_imagery", scenes_considered=0, sensor="landsat")

    result = analyze_scene(feats[0], bbox, lat, lon)
    result.scenes_considered = len(feats)
    result.window_used = window_used
    return result
