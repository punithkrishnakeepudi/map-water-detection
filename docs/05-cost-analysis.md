# Cost Analysis

## MVP: $0/month

| Component | Choice | Cost |
|---|---|---|
| Satellite/vector data | Planetary Computer STAC + SAS-sign (zero-auth, confirmed live) | $0 |
| Basemap tiles | OpenStreetMap raster (subject to fair-use policy) | $0 |
| Frontend hosting | Hugging Face Spaces free tier or Render free static/web tier | $0 |
| Backend hosting | Hugging Face Spaces free tier or Render free tier (with cold-start sleep) | $0 |
| Database | PostgreSQL+PostGIS — Render free Postgres tier (90-day expiry on free instances — plan to re-provision, or use SQLite+SpatiaLite for a pure demo with no persistence guarantee) | $0 |

Caveat carried over from [research-log.md](research-log.md): PC's own rate-limit/fair-use terms weren't confirmed this session (JS-rendered ToS page). At personal/demo query volume this is very unlikely to matter; re-check before assuming it holds at any real traffic.

## Small production: $5–20/month

| Component | Choice | Est. cost |
|---|---|---|
| Backend hosting (always-on, no cold start) | Railway/Render paid tier | ~$5–7/mo |
| Object storage for cached composites/tiles | S3-compatible, ~50 GB | ~$1–2/mo |
| Managed Postgres+PostGIS (persistent, no free-tier expiry) | Railway/Render/Supabase paid | ~$5–10/mo |
| Domain (optional) | — | ~$1/mo amortized |

## Larger scale: $50–200/month

| Component | Driver |
|---|---|
| GPU workers | Only needed if/when v2 adds deep-learning segmentation (deferred, see [03](03-detection-methodology.md)) |
| CDN | Serving many users' cached tile/mask outputs |
| Larger managed database | More AOIs cached, more concurrent users |

## What would break the $0 assumption

- Query volume high enough to hit an undocumented PC rate limit (unconfirmed ceiling — see above).
- Needing always-on backend (free tiers commonly sleep/cold-start).
- Needing durable, non-expiring Postgres storage beyond a free tier's retention window.
- Wanting a genuinely free-tier-compliant satellite *basemap* from a third party instead of the app's own composites — none was found (see [02](02-data-sources-verified.md)); this doesn't cost anything since the app doesn't need one, but flagging it here so it isn't silently assumed later.
