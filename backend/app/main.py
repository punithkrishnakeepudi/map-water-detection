import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import cache, cross_check, landsat, pc_client
from .detection import analyze_year

app = FastAPI(title="Water Disappearance Detector API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP/demo only - tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Every collection this app reads lives in its own storage container, and a SAS
# token is per container - so each one costs a separate /sign call, and each can
# hit the same throttle. Measured: a location needing a fresh land-cover and
# JRC tile took 60 s versus 21 s for one that did not.
_WARM_TARGETS = [
    ("sentinel-2-l2a", "B03"),        # sentinel2l2a01 / sentinel2-l2
    ("io-lulc-annual-v02", "data"),   # ai4edataeuwest / io-lulc
    ("jrc-gsw", "occurrence"),        # ai4edataeuwest / jrcglobalwater
    ("landsat-c2-l2", "green"),       # landsateuwest  / landsat-c2
]
# Any land bbox works - a token is container-wide, so which scene mints it is
# irrelevant. This one is Pallikaranai, which all four collections cover.
_WARM_BBOX = [80.19, 12.90, 80.23, 12.95]


def _warm_one(collection: str, asset: str) -> None:
    try:
        feats = pc_client.stac_search(collection, _WARM_BBOX, None, limit=1)
        if feats and asset in feats[0]["assets"]:
            pc_client.sign(feats[0]["assets"][asset]["href"])
    except Exception:  # noqa: BLE001 - warming is best-effort, never block startup
        pass


def _warm_sas_tokens() -> None:
    """Mint every container's SAS token before a user asks for one.

    /sign is by far the app's slowest call when PC is throttling (measured at
    82-87 s cold, against a 2.5 s pixel read), and every asset read needs a
    valid token. Paying for all four at startup, in the background and in
    parallel, keeps that cost off the first user request entirely.
    """
    with ThreadPoolExecutor(max_workers=len(_WARM_TARGETS)) as pool:
        for collection, asset in _WARM_TARGETS:
            pool.submit(_warm_one, collection, asset)


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_warm_sas_tokens, daemon=True).start()


def _analyze_year_cached(lat: float, lon: float, year: int, buffer_m: float) -> dict:
    key = cache.make_key(lat, lon, year, buffer_m)
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = asdict(analyze_year(lat, lon, year, buffer_m))
    result["year"] = year
    cache.set(key, result)
    return result


# Landsat 8 launched in 2013; earlier Landsats are available but noisier, and
# Landsat 7 is SLC-off from 2003. 2013 is the honest floor for this fallback.
EXTENDED_SEARCH_FLOOR = 2013
EXTENDED_SEARCH_STEP = 3


def _extended_search(lat: float, lon: float, buffer_m: float, before_year: int) -> dict:
    """MVP spec #9: look further back when Sentinel-2 found no water at all.

    A flat "no water" timeline usually means the water was already gone before
    Sentinel-2's archive starts, not that it never existed - so sample Landsat
    backwards and report what it found. Deliberately sparse (every few years):
    this runs only on the no-water path and each probe is a full scene read.
    """
    probe_years = list(range(before_year - 1, EXTENDED_SEARCH_FLOOR - 1, -EXTENDED_SEARCH_STEP))
    probes: list[dict] = []
    for y in probe_years:
        key = cache.make_key(lat, lon, y, buffer_m) + ":landsat"
        cached = cache.get(key)
        if cached is None:
            try:
                cached = asdict(landsat.analyze_year(lat, lon, y, buffer_m))
                cached["year"] = y
                cache.set(key, cached)
            except Exception as e:  # noqa: BLE001
                cached = {"year": y, "status": "error", "error": pc_client.user_facing_error(e), "sensor": "landsat"}
        probes.append(cached)
        if cached.get("water_at_point") is True:
            break  # earliest-water evidence found; no need to keep digging

    found = [p for p in probes if p.get("water_at_point") is True]
    if found:
        hit = max(p["year"] for p in found)
        finding = (
            f"Landsat shows water at this location as recently as {hit}, before the"
            f" requested window - the loss predates {before_year}."
        )
    elif any(p.get("status") in ("ok", "point_obscured") for p in probes):
        finding = (
            f"Landsat found no water back to {probe_years[-1]} either - no evidence"
            " of a water body at this point within the satellite record."
        )
    else:
        finding = "No usable Landsat imagery further back, so the earlier record is unknown."

    return {
        "triggered_because": "no water detected in any readable year of the requested window",
        "sensor": "landsat-c2-l2",
        "years_probed": probe_years,
        "finding": finding,
        "years": probes,
    }


@app.get("/api/timeline")
def timeline(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    start_year: int = Query(2021, ge=2015, le=2026),
    end_year: int = Query(2026, ge=2015, le=2026),
    buffer_m: float = Query(150, ge=30, le=2000, description="Half-width of the square AOI, in meters"),
):
    if end_year < start_year:
        raise HTTPException(400, "end_year must be >= start_year")
    years = list(range(start_year, end_year + 1))
    if len(years) > 12:
        raise HTTPException(400, "range too large for MVP - max 12 years per request")

    results_by_year: dict[int, dict] = {}

    # Cross-checks touch different collections from the per-year work, so they
    # overlap with it instead of adding their latency on the end.
    cc_key = cache.make_key(lat, lon, 0, buffer_m)
    cc = cache.get(cc_key) or {}
    cc_pool = ThreadPoolExecutor(max_workers=2)
    cc_built = (
        cc_pool.submit(cross_check.built_area_latest, lat, lon, end_year, buffer_m)
        if "built" not in cc else None
    )
    cc_gsw = (
        cc_pool.submit(cross_check.historical_water_occurrence_pct, lat, lon)
        if "gsw" not in cc else None
    )

    # Years run concurrently, but modestly. Raising this to 6 was measured as a
    # regression (141 s vs ~98 s cold): with three parallel band reads per year
    # it puts ~18 requests in flight at once and PC throttles the burst harder
    # than the extra parallelism buys back.
    with ThreadPoolExecutor(max_workers=min(3, len(years))) as pool:
        futures = {pool.submit(_analyze_year_cached, lat, lon, y, buffer_m): y for y in years}
        for fut in as_completed(futures):
            y = futures[fut]
            try:
                results_by_year[y] = fut.result()
            except Exception as e:  # noqa: BLE001
                results_by_year[y] = {"year": y, "status": "error", "error": pc_client.user_facing_error(e)}

    # Mop-up pass: retry any year that still failed, one at a time now that
    # the concurrent batch above has finished (PC's 429s are load-dependent,
    # confirmed in docs/research-log.md - a lone retry after the rush usually succeeds).
    failed_years = [y for y in years if results_by_year[y].get("status") == "error"]
    for y in failed_years:
        key = cache.make_key(lat, lon, y, buffer_m)
        try:
            result = asdict(analyze_year(lat, lon, y, buffer_m))
            result["year"] = y
            cache.set(key, result)
            results_by_year[y] = result
        except Exception as e:  # noqa: BLE001
            results_by_year[y] = {"year": y, "status": "error", "error": pc_client.user_facing_error(e)}

    ordered = [results_by_year[y] for y in years]
    for r in ordered:
        if r.get("scene_id") and not r.get("photo_url"):
            r["photo_url"] = _photo_url("sentinel-2-l2a", r.get("scene_id"), lat, lon)

    # Cross-checks are point-only (no year), so cache them under year=0. A None
    # result means the upstream call failed - don't cache that, retry next time.
    try:
        if cc_built is not None:
            b = cc_built.result()
            if b is not None:
                cc["built"] = b
        if cc_gsw is not None:
            g = cc_gsw.result()
            if g is not None:
                cc["gsw"] = g
    finally:
        cc_pool.shutdown(wait=False)
    if cc:
        cache.set(cc_key, cc)
    built = cc.get("built")
    gsw = cc.get("gsw")

    # MVP spec #9 - only on the all-dry path, and only when we could actually see.
    readable = [r for r in ordered if r.get("status") in ("ok", "point_obscured")]
    extended = None
    if readable and not any(r.get("water_at_point") is True for r in ordered):
        extended = _extended_search(lat, lon, buffer_m, start_year)
        if extended and extended.get("years"):
            for ey in extended["years"]:
                if ey.get("scene_id") and not ey.get("photo_url"):
                    ey["photo_url"] = _photo_url("landsat-c2-l2", ey.get("scene_id"), lat, lon)

    summary = _build_summary(ordered, built)
    encroachment = _analyze_encroachment(ordered, built, gsw, extended)

    return {
        "location": {"lat": lat, "lon": lon},
        "buffer_m": buffer_m,
        "years": ordered,
        "built_area": built,
        "historical_water_occurrence": gsw,
        "extended_search": extended,
        "encroachment_analysis": encroachment,
        "summary": summary,
        "disclaimer": (
            "Estimates from public satellite imagery (Sentinel-2, ~10m resolution) using "
            "an automated index (MNDWI) and threshold (Otsu), not a survey. Water bodies "
            "smaller than ~900 sq m may not be detected. 'No water' means none was detected "
            "in the clearest available imagery for that year, not certainty none existed. "
            "Where the search was extended further back, those years come from Landsat at "
            "30 m, so the minimum detectable water body is roughly 9x larger in them. "
            "Verify before relying on this for legal, regulatory, or journalistic claims."
        ),
    }


def _photo_url(collection: str, scene_id: str | None, lat: float, lon: float, zoom: int = 15) -> str | None:
    """Generate direct tile photo URL for the space at zoom 15."""
    if not scene_id:
        return None
    import math
    import urllib.parse
    x = int((lon + 180.0) / 360.0 * (2**zoom))
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * (2**zoom))
    params = [("collection", collection), ("item", scene_id), ("assets", "visual")]
    if collection.startswith("landsat"):
        params.append(("color_formula", "gamma RGB 2.7, saturation 1.5, sigmoidal RGB 15 0.55"))
    return f"https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{zoom}/{x}/{y}@1x?{urllib.parse.urlencode(params)}"


def _analyze_encroachment(
    ordered_years: list[dict],
    built: dict | None,
    gsw: dict | None,
    extended: dict | None,
) -> dict:
    """Detect whether a pond/water space was historically present and later built over by buildings."""
    point_water_years = [
        r["year"] for r in ordered_years
        if r.get("water_at_point") is True
    ]
    aoi_water_years = [
        r["year"] for r in ordered_years
        if (r.get("water_fraction_aoi") or 0) >= 0.25
    ]
    water_years = point_water_years or aoi_water_years
    dry_years = [
        r["year"] for r in ordered_years
        if r.get("status") == "ok" and r.get("water_at_point") is False
    ]

    landsat_water_years = []
    if extended and extended.get("years"):
        landsat_water_years = [y["year"] for y in extended["years"] if y.get("water_at_point") is True]

    gsw_pct = gsw.get("occurrence_pct", 0) if gsw else 0
    built_at_point = bool(built and built.get("at_point"))
    built_fraction = built.get("built_fraction", 0.0) if built else 0.0
    built_year = built.get("year") if built else None

    had_water_historically = bool(water_years or landsat_water_years or gsw_pct >= 15.0)
    has_water_recently = bool(ordered_years and (ordered_years[-1].get("water_at_point") is True or (ordered_years[-1].get("water_fraction_aoi") or 0) >= 0.35))
    has_buildings_now = built_at_point or built_fraction >= 0.25

    if had_water_historically and not has_water_recently and has_buildings_now:
        historical_year = max(point_water_years) if point_water_years else (
            max(landsat_water_years) if landsat_water_years else (
                max(aoi_water_years) if aoi_water_years else "Historical"
            )
        )
        loss_candidates = [y for y in dry_years if not point_water_years or y > min(point_water_years)]
        loss_year = min(loss_candidates) if loss_candidates else (min(dry_years) if dry_years else None)
        severity = "HIGH" if built_at_point else "MEDIUM"
        return {
            "encroachment_detected": True,
            "status": "encroached",
            "badge": "🚨 BUILDING OVER WATER DETECTED",
            "badge_color": "#d93829",
            "confidence": severity,
            "headline": f"Water space was encroached and replaced by buildings by {built_year or 'recent years'}",
            "historical_water_year": historical_year,
            "loss_year": loss_year,
            "built_year": built_year,
            "built_fraction": round(built_fraction, 2),
            "built_at_point": built_at_point,
            "details": (
                f"Satellite analysis confirms that this space previously contained a pond or water body "
                f"(detected in {historical_year}{f', JRC occurrence {gsw_pct}%' if gsw_pct else ''}). "
                f"The water subsequently dried or was filled by {loss_year or 'subsequent years'}. "
                f"Land cover confirms urban structures and buildings currently occupy this space "
                f"({int(built_fraction * 100)}% built-up density{', directly on queried point' if built_at_point else ''})."
            ),
        }

    if has_water_recently:
        return {
            "encroachment_detected": False,
            "status": "water_preserved",
            "badge": "💧 ACTIVE WATER BODY PRESERVED",
            "badge_color": "#1d6fd8",
            "confidence": "HIGH",
            "headline": "Active pond or water body present (no building encroachment)",
            "historical_water_year": water_years[-1] if water_years else None,
            "loss_year": None,
            "built_year": built_year,
            "built_fraction": round(built_fraction, 2),
            "built_at_point": built_at_point,
            "details": "Water remains actively detected at this location in recent satellite passes. No building encroachment detected.",
        }

    if had_water_historically and not has_water_recently and not has_buildings_now:
        historical_year = max(water_years) if water_years else (max(landsat_water_years) if landsat_water_years else "Historical")
        return {
            "encroachment_detected": False,
            "status": "dried_waterbody",
            "badge": "🏜️ WATER DRIED / BARE GROUND",
            "badge_color": "#e67e22",
            "confidence": "MEDIUM",
            "headline": "Historical water body has dried or shrunk, but heavy building construction is not yet recorded",
            "historical_water_year": historical_year,
            "loss_year": min(dry_years) if dry_years else None,
            "built_year": built_year,
            "built_fraction": round(built_fraction, 2),
            "built_at_point": False,
            "details": f"Water was present historically around {historical_year}, but no active water was detected in recent years. Land cover does not yet classify this as a high-density built structure.",
        }

    return {
        "encroachment_detected": False,
        "status": "dry_land",
        "badge": "🌱 NO HISTORICAL WATER FOUND",
        "badge_color": "#5d6572",
        "confidence": "HIGH",
        "headline": "Naturally dry land throughout the analyzed satellite record",
        "historical_water_year": None,
        "loss_year": None,
        "built_year": built_year,
        "built_fraction": round(built_fraction, 2),
        "built_at_point": built_at_point,
        "details": "No evidence of a permanent pond or lake at this location across Sentinel-2 or historical surface water archives.",
    }


PRESETS = [
    {
        "id": "songdo",
        "name": "Songdo Reclaimed District (S. Korea)",
        "tag": "Lake / Basin Reclaimed for Buildings",
        "desc": "Tidal basin & water converted into urban commercial city (water 2021 -> built 2022+)",
        "lat": 37.395,
        "lon": 126.655,
        "start_year": 2021,
        "end_year": 2023,
        "buffer_m": 150,
        "zoom": 15,
    },
    {
        "id": "pallikaranai",
        "name": "Pallikaranai Marsh Encroachment (Chennai)",
        "tag": "Wetland Encroachment",
        "desc": "Historic marshland shrunk and built over by urban infrastructure & IT corridor",
        "lat": 12.9365,
        "lon": 80.2246,
        "start_year": 2021,
        "end_year": 2026,
        "buffer_m": 300,
        "zoom": 14,
    },
    {
        "id": "nellore",
        "name": "Nellore Urban Tank (Andhra Pradesh)",
        "tag": "Urban Lake Encroachment",
        "desc": "Urban water tank shrinking under expanding city construction",
        "lat": 14.451,
        "lon": 79.968,
        "start_year": 2021,
        "end_year": 2025,
        "buffer_m": 300,
        "zoom": 15,
    },
    {
        "id": "bellandur",
        "name": "Bellandur Encroached Buffer (Bengaluru)",
        "tag": "Lake Buffer Construction",
        "desc": "Catchment buffer encroached by tech parks & residential towers",
        "lat": 12.935,
        "lon": 77.678,
        "start_year": 2021,
        "end_year": 2025,
        "buffer_m": 200,
        "zoom": 15,
    },
]


@app.get("/api/presets")
def get_presets():
    return PRESETS


def _build_summary(ordered_years: list[dict], built: dict | None) -> str:
    water_years = [r["year"] for r in ordered_years if r.get("water_at_point") is True]
    no_water_years = [
        r["year"] for r in ordered_years if r.get("status") == "ok" and r.get("water_at_point") is False
    ]
    # Years we genuinely could not read: no scene, a degenerate histogram, a
    # network error, or a scene where the queried pixel itself was under cloud.
    # These are "couldn't see", never "no water" (docs/07-limitations-and-risks.md #2).
    unresolved_years = [
        r["year"]
        for r in ordered_years
        if r.get("status") in ("point_obscured", "no_clear_imagery", "degenerate_scene", "error")
    ]

    unresolved_note = (
        " Could not see this location in: " + ", ".join(map(str, unresolved_years)) + "."
        if unresolved_years
        else ""
    )
    # Only claim built-up ground when the land-cover dataset says so at the
    # point itself, and always name the year it actually describes - it lags
    # the Sentinel-2 archive by a year or more.
    building_note = ""
    if built and built.get("at_point"):
        building_note = (
            f" Land cover classifies this exact point as built-up in {built['year']}"
            f" ({int(built['built_fraction'] * 100)}% of the surrounding area)."
        )
    elif built and built.get("built_fraction", 0) >= 0.25:
        building_note = (
            f" {int(built['built_fraction'] * 100)}% of the surrounding area was"
            f" built-up by {built['year']}, though not the queried point itself."
        )

    if not water_years and not no_water_years:
        return (
            "No usable imagery for this location in the requested range - try widening"
            " the year range or the search radius." + unresolved_note
        )

    if not water_years:
        core = f"No water detected in any readable year ({no_water_years[0]}-{no_water_years[-1]})."
    elif not no_water_years:
        core = (
            f"Water detected in every readable year ({water_years[0]}-{water_years[-1]})"
            " - no disappearance observed in this range."
        )
    else:
        last_water_year = max(water_years)
        first_no_water_after = min([y for y in no_water_years if y > last_water_year], default=None)
        if first_no_water_after:
            core = f"Water body existed through {last_water_year}, disappeared by {first_no_water_after}."
            # A gap of unreadable years between the two straddles the transition,
            # so the disappearance date is bounded, not pinned.
            gap = [y for y in unresolved_years if last_water_year < y < first_no_water_after]
            if gap:
                core += (
                    f" The exact year is uncertain - {', '.join(map(str, gap))} could not be read."
                )
        else:
            core = (
                f"Water detected in {', '.join(map(str, water_years))};"
                f" no water in {', '.join(map(str, no_water_years))}."
            )

    return core + unresolved_note + building_note
