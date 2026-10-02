# Legal and Licensing

## Attribution requirements

| Source | License | Required attribution text |
|---|---|---|
| Sentinel-2 (Copernicus) | Free, incl. commercial use, no restriction | **"Copernicus Sentinel data [Year]"** |
| Landsat (USGS) | Public domain | None required; USGS requests (not requires) citation |
| JRC Global Surface Water | Free, Copernicus programme data | **"Source: EC JRC/Google"** + cite Pekel, J.-F. et al. (2016), *Nature* |
| Microsoft Global ML Building Footprints | CDLA-Permissive-2.0 | Attribution recommended, not legally mandated under this license — still include a credit line as good practice |
| OpenStreetMap tiles | ODbL (data) / tile usage policy (service) | **"© OpenStreetMap contributors"**, plus compliance with the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/) (caching, valid User-Agent, no bulk scraping) |
| Microsoft Planetary Computer (hosting/access layer) | Re-hosts the above under their original licenses | Attribute the underlying dataset per its own row above; PC itself doesn't impose an additional license on the data |

Put all of the above in a single "Data & Attribution" footer/page in the app, not scattered — easier to keep correct as sources change.

## Caching/storage risk — UNCLEAR, flag explicitly

Whether caching satellite tiles/derived composites (e.g. in the PostGIS/object-storage layer described in [04](04-technical-architecture.md)) is permitted long-term under Copernicus/PC terms was **not resolved** by this research — the original spec flagged this as unclear and nothing found this session settles it definitively. Treat cached derived products (MNDWI masks, cropped composites) as: (a) small AOI crops, not full-scene mirrors, (b) tied to attribution, (c) something to re-verify against Copernicus's and PC's terms of use before any commercial deployment or long-term public archive.

## Disclaimer text for end users (draft — refine before shipping)

> This tool estimates historical water presence from public satellite imagery (Sentinel-2, ~10 m resolution) using an automated index (MNDWI) and threshold, cross-referenced against building-footprint and historical water datasets. Results are **estimates, not surveys** — accuracy depends on cloud cover, water turbidity, and water-body size (bodies smaller than ~900 m² may not be detected). A "no water" result for a given year means no water was detected in the clearest available imagery for that year, not a certainty that none existed. Always verify findings against ground truth (site visits, official records, higher-resolution imagery) before relying on them for legal, regulatory, or journalistic claims. Data: Copernicus Sentinel data, EC JRC/Google (Pekel et al. 2016), Microsoft Global ML Building Footprints, © OpenStreetMap contributors.

## Explicitly avoided: Google Earth Engine

Confirmed (see [research-log.md](research-log.md)) that GEE's free tier is licensed for noncommercial/research/qualifying-journalism use only; any other use requires a $500–2,000/mo (or pay-as-you-go) paid account, regardless of how little compute is used. This app does not use GEE and doesn't need to — see [02-data-sources-verified.md](02-data-sources-verified.md).
