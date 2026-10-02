"""Lightweight on-disk cache keyed by (rounded location, year, buffer).

Simplification vs docs/04-technical-architecture.md's Postgres+PostGIS cache:
this app does no spatial queries of its own (Planetary Computer's STAC API
does all the geospatial work), so a plain key-value store is enough for a
single-instance MVP demo. Swap for Postgres+PostGIS if/when caching needs to
be shared across multiple backend instances.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "cache.sqlite3"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sas_tokens ("
        "  key TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at REAL NOT NULL)"
    )
    return conn


def get_token(key: str) -> tuple[str, float] | None:
    """A previously minted SAS token, if one is stored. See set_token()."""
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT token, expires_at FROM sas_tokens WHERE key = ?", (key,)
            ).fetchone()
    except sqlite3.Error:
        return None
    return (row[0], row[1]) if row else None


def set_token(key: str, token: str, expires_at: float) -> None:
    """Persist a SAS token so it outlives this process.

    Tokens are valid ~1 hour and are scoped to a storage container, so keeping
    them only in memory means every server restart pays the /sign cost again -
    and that call is the slowest thing in the app when PC is throttling
    (measured at 82 s against a 2.7 s pixel read).
    """
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sas_tokens (key, token, expires_at) VALUES (?, ?, ?)",
                (key, token, expires_at),
            )
    except sqlite3.Error:
        pass  # cache is an optimisation - never fail a request over it


# Bump whenever the detection pipeline's output shape or semantics change, so
# stale rows miss instead of being served under new meanings.
#   v2: added the "point_obscured" status, which v1 rows recorded as plain "ok".
#   v3: added imagery_tiles; v2 rows carry no satellite layer for their year.
#   v4: MNDWI_WATER_FLOOR - v3 rows were computed with unguarded Otsu, which
#       labelled water-free scenes up to 55% water (backend/validation/).
SCHEMA_VERSION = "v4"


def make_key(lat: float, lon: float, year: int, buffer_m: float) -> str:
    # round to ~10m precision - re-clicking near-enough the same spot hits cache
    return f"{SCHEMA_VERSION}:{round(lat, 4)}:{round(lon, 4)}:{year}:{int(buffer_m)}"


def get(key: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM results WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def set(key: str, value: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO results (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
