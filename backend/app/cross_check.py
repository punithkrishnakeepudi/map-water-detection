"""Cross-checks that put the MNDWI timeline in context.

Neither of these is a detection input - they answer "what does an independent
dataset say about this spot?", which is how a water verdict earns or loses
credibility (docs/03-detection-methodology.md).
"""
import numpy as np
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

from . import pc_client
from .detection import point_to_bbox

# Esri/Impact Observatory annual land cover, 10 m. Class codes come from the
# collection's own file:values metadata (confirmed live against PC).
LULC_BUILT = 7
LULC_WATER = 1
LULC_NODATA = 0


def _read_lulc(bbox: list[float], year: int) -> tuple[np.ndarray, dict] | None:
    feats = pc_client.stac_search(
        "io-lulc-annual-v02", bbox, f"{year}-01-01/{year}-12-31", limit=1
    )
    if not feats:
        return None
    feat = feats[0]
    href = pc_client.sign(feat["assets"]["data"]["href"])
    with rasterio.open(href) as src:
        b = transform_bounds("EPSG:4326", src.crs, *bbox)
        arr = src.read(1, window=from_bounds(*b, transform=src.transform))
    if arr.size == 0:
        return None
    return arr, feat


def built_area(lat: float, lon: float, year: int, buffer_m: float = 150) -> dict | None:
    """How much of the AOI a land-cover dataset independently calls "built area".

    Replaces an earlier check against PC's `ms-buildings` collection, which was
    unusable here: that collection's STAC items are *continent-sized* partitions
    (a Chennai bbox returns an item called "Oceania"), so "an item intersects my
    point" was true almost everywhere on land - it reported a building present in
    the middle of the Pacific. Its real payload is an abfs:// GeoParquet needing
    a quadkey join and extra dependencies, which is v2 work. This raster answers
    the same question honestly, per-year, with the rasterio we already use.

    Returns None if the dataset couldn't be read at all, so callers can tell
    "checked, nothing built" apart from "couldn't check".
    """
    bbox = point_to_bbox(lat, lon, buffer_m)
    try:
        got = _read_lulc(bbox, year)
    except Exception:  # noqa: BLE001
        return None
    if got is None:
        return None
    arr, feat = got

    classified = arr != LULC_NODATA
    if not classified.any():
        # All NoData - open ocean, or outside the product's footprint.
        return None

    # Report the year the tile actually covers, not the year we asked for:
    # a query for 2024 is served by the tile spanning 2023-01-01 to 2024-01-01,
    # and labelling that "2024" would overstate how current the answer is.
    covers = feat["properties"].get("start_datetime", "")[:4]

    return {
        "built_fraction": round(float((arr == LULC_BUILT).mean()), 3),
        "water_fraction": round(float((arr == LULC_WATER).mean()), 3),
        "at_point": bool(arr[arr.shape[0] // 2, arr.shape[1] // 2] == LULC_BUILT),
        "source": feat["id"],
        "year": int(covers) if covers.isdigit() else year,
        "dataset": "Esri/Impact Observatory 10m Annual Land Cover (io-lulc-annual-v02)",
    }


def built_area_latest(lat: float, lon: float, year: int, buffer_m: float = 150) -> dict | None:
    """built_area() for the most recent year the product actually covers.

    The collection lags the Sentinel-2 archive, so asking for the current year
    returns nothing - step back until a tile exists rather than reporting
    "no built area" when the real answer is "no data yet for that year".
    """
    for y in range(year, year - 5, -1):
        got = built_area(lat, lon, y, buffer_m)
        if got is not None:
            return got
    return None


def historical_water_occurrence_pct(lat: float, lon: float) -> dict | None:
    """Sample the JRC Global Surface Water 'occurrence' band at the point.

    PC serves JRC GSW version v1_3 (~2020 vintage, confirmed in docs/research-log.md)
    - NOT the 1984-2024 range sometimes quoted for this dataset elsewhere.
    """
    bbox = point_to_bbox(lat, lon, 30)
    try:
        feats = pc_client.stac_search("jrc-gsw", bbox, datetime_range=None, limit=1)
    except Exception:  # noqa: BLE001
        return None
    if not feats:
        return None
    feat = feats[0]
    try:
        href = pc_client.sign(feat["assets"]["occurrence"]["href"])
        with rasterio.open(href) as src:
            b = transform_bounds("EPSG:4326", src.crs, *bbox)
            window = from_bounds(*b, transform=src.transform)
            arr = src.read(1, window=window)
            if arr.size == 0:
                return None
            occurrence_pct = float(arr.mean())
    except Exception:  # noqa: BLE001
        return None
    return {
        "occurrence_pct": round(occurrence_pct, 1),
        "source_tile": feat["id"],
        "dataset_vintage": feat["properties"].get("datetime"),
        "note": "JRC GSW as served by Planetary Computer tops out around 2020 (v1_3) - not a 2024 figure.",
    }
