"""Zero-auth client for Microsoft Planetary Computer STAC + SAS-sign.

Verified live (see docs/research-log.md): anonymous search, anonymous sign,
no account/key anywhere in this module.
"""
import datetime
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_SIGN = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"

# Confirmed empirically (docs/research-log.md): PC's anonymous endpoints 429
# under concurrent load - no published numeric limit, so back off and retry
# rather than assume a fixed rate.
_MAX_RETRIES = 6
_BASE_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 30.0


class RateLimited(Exception):
    """PC throttled us and kept throttling across every retry.

    Distinct from a generic failure so callers can tell the user "the data
    service is busy, try again" instead of leaking an HTTP trace.
    """


def user_facing_error(e: Exception) -> str:
    """Why a fetch failed, in words a reader can act on.

    A raw exception repr in the UI ("<HTTPError 429: 'Too Many Requests'>")
    tells a journalist nothing and looks like a crash, so every path that can
    surface an error to the frontend routes through here.
    """
    if isinstance(e, RateLimited):
        return "The satellite data service is busy right now - re-run to retry this year."
    if isinstance(e, urllib.error.HTTPError) and e.code == 404:
        return "The imagery for this year is no longer available from the data service."
    return "Could not retrieve imagery for this year (upstream data service error)."


def _backoff_seconds(attempt: int, err: urllib.error.HTTPError) -> float:
    """Honour Retry-After when the server sends one, else exponential backoff.

    Jitter matters here: this app fires several years concurrently, and without
    it every worker retries on the same beat and re-triggers the same 429.
    """
    retry_after = err.headers.get("Retry-After") if err.headers else None
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_S)
        except ValueError:
            pass  # http-date form - fall through to exponential
    return min(_BASE_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S) * (0.5 + random.random())


def _get_json_with_retry(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    last_err = None
    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 or e.code >= 500:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_backoff_seconds(attempt, e))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            # Transient connection drops deserve the same treatment as a 5xx.
            last_err = e
            if attempt < _MAX_RETRIES - 1:
                time.sleep(min(_BASE_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S) * (0.5 + random.random()))
            continue
    if isinstance(last_err, urllib.error.HTTPError) and last_err.code == 429:
        raise RateLimited("Planetary Computer is rate-limiting this client") from last_err
    raise last_err


# PC's tiler serves XYZ raster tiles straight off a STAC item, anonymously
# (verified live: 200 image/jpeg, no key). The template is fully determined by
# collection + item id, so it can be built without a tilejson round-trip.
PC_TILER = "https://planetarycomputer.microsoft.com/api/data/v1/item/tiles/WebMercatorQuad/{z}/{x}/{y}@1x"

# Landsat surface reflectance is dim and flat straight out of the archive;
# this is the stretch PC's own previews use for these collections.
_LANDSAT_COLOR = "gamma RGB 2.7, saturation 1.5, sigmoidal RGB 15 0.55"


def imagery_tile_url(collection: str, item_id: str, assets: list[str]) -> str:
    """XYZ tile template for one scene's true-colour rendering."""
    params = [("collection", collection), ("item", item_id)]
    params += [("assets", a) for a in assets]
    if collection.startswith("landsat"):
        params.append(("color_formula", _LANDSAT_COLOR))
    return PC_TILER + "?" + urllib.parse.urlencode(params)


def stac_search(collection: str, bbox: list[float], datetime_range: str | None, limit: int = 20) -> list[dict]:
    params = {
        "collections": collection,
        "bbox": ",".join(map(str, bbox)),
        "limit": limit,
    }
    if datetime_range:
        params["datetime"] = datetime_range
    url = PC_STAC + "?" + urllib.parse.urlencode(params)
    d = _get_json_with_retry(url, headers={"Accept": "application/geo+json"})
    return d.get("features", [])


# ---------------------------------------------------------------------------
# SAS token cache
#
# The /sign endpoint is the single slowest thing in this app. Measured against
# the live service: one sign call cost **82.6 s** (it 429s immediately, and the
# retry ladder waits it out) while the pixel read it enabled took 2.7 s. At
# three assets per scene and six years per request, that was ~18 sign calls per
# query - the whole reason a location takes minutes.
#
# The fix is that a PC SAS token is scoped to the **storage container**, not to
# the blob: verified live, a token minted for .../sentinel2-l2/.../B03.tif reads
# B11 and SCL in the same container (HTTP 206). The response carries
# `msft:expiry` (~1 h out). So one token serves every asset of every scene in
# that container, and 18 calls collapse to 1.
_SAS_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_SAS_CACHE_LOCK = threading.Lock()
_SAS_KEY_LOCKS: dict[tuple[str, str], threading.Lock] = {}
# Renew this far before real expiry, so a long read can't outlive its token.
_SAS_SAFETY_S = 300.0


def _container_key(href: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(href)
    container = parts.path.lstrip("/").split("/", 1)[0]
    return (parts.netloc, container)


def _key_lock(key: tuple[str, str]) -> threading.Lock:
    with _SAS_CACHE_LOCK:
        return _SAS_KEY_LOCKS.setdefault(key, threading.Lock())


def _parse_expiry(value: str | None) -> float:
    if not value:
        return time.time() + 1800.0  # no expiry given - assume a cautious 30 min
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return time.time() + 1800.0


def _cached_token(key: tuple[str, str]) -> str | None:
    with _SAS_CACHE_LOCK:
        entry = _SAS_CACHE.get(key)
    if entry and entry[1] - time.time() > _SAS_SAFETY_S:
        return entry[0]

    # Fall back to the on-disk copy: a token outlives any one process, and
    # re-minting it on every restart is the difference between a fast request
    # and a throttled one.
    from . import cache  # local import keeps this module free of app imports at load

    stored = cache.get_token("|".join(key))
    if stored and stored[1] - time.time() > _SAS_SAFETY_S:
        with _SAS_CACHE_LOCK:
            _SAS_CACHE[key] = stored
        return stored[0]
    return None


def sign(href: str) -> str:
    """Return `href` with a valid SAS query appended, minting one only if needed."""
    key = _container_key(href)
    token = _cached_token(key)
    if token is not None:
        return href + "?" + token

    # Serialise per container: without this, every worker thread misses the
    # cache at once and fires a simultaneous burst at the endpoint that is
    # already rate-limiting us - the exact thing this cache exists to avoid.
    with _key_lock(key):
        token = _cached_token(key)  # another thread may have filled it while we waited
        if token is not None:
            return href + "?" + token

        url = PC_SIGN + "?href=" + urllib.parse.quote(href, safe="")
        d = _get_json_with_retry(url)
        signed = d["href"]
        if "?" not in signed:
            return signed  # unsigned/public asset - nothing to cache
        token = signed.split("?", 1)[1]
        expiry = _parse_expiry(d.get("msft:expiry"))
        with _SAS_CACHE_LOCK:
            _SAS_CACHE[key] = (token, expiry)
        from . import cache

        cache.set_token("|".join(key), token, expiry)
        return signed
