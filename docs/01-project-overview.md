# Project Overview

## Problem statement

Water bodies — ponds, lakes, wetlands, small reservoirs — disappear under construction with no public record of when or how much was lost. Local news, environmental researchers, and residents can pull up a satellite view today, but not a **year-by-year trail** proving "this was a lake in 2020, buildings by 2024." This app builds that trail automatically from free public satellite archives.

## Target users

- Environmental journalists and researchers documenting water-body loss.
- Residents/NGOs building an evidence base for a specific local lake or pond dispute.
- Urban planners/GIS hobbyists exploring land-cover change at a location.

Not targeted at v1: enterprise/commercial GIS teams needing bulk/automated country-scale monitoring (that's a v2+ scaling conversation, see [05-cost-analysis.md](05-cost-analysis.md)).

## MVP scope (one sentence)

Pick a point on a map, get a year-by-year (2021–2026) water/no-water verdict with confidence, sourced from Sentinel-2, cross-checked against known building footprints and historical water occurrence — no accounts, no paid APIs, $0 to run.

## What "done" looks like for MVP

```
2026: Building detected, NO WATER
2025: Building detected, NO WATER
2024: NO WATER
2023: WATER DETECTED (MNDWI confidence: 97%)
2022: WATER DETECTED
Report: "Water body existed 2020–2023, disappeared by 2024"
```

See [08-mvp-specification.md](08-mvp-specification.md) for the exact feature list and [07-limitations-and-risks.md](07-limitations-and-risks.md) for what this verdict does and doesn't guarantee.
