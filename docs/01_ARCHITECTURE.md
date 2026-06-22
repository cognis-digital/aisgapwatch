# Architecture

A precise account of how `aisgapwatch` is structured, the invariants it
maintains, and where to extend it. For a gentle from-scratch walkthrough see
[`02_EXPLAINED_SIMPLY.md`](02_EXPLAINED_SIMPLY.md).

## Module graph

```
            parsers.py ──┐
   data.py ──────────────┤ (produce Ping objects)
                         ▼
                     models.py  (Ping, Gap)
                         ▲
   geo.py ───────────────┤ (haversine_nm)
   scoring.py ───────────┤ (score_gap, ScoreConfig)
                         ▼
                     detect.py  (GapDetector, detect_gaps)
                         ▲
   cli.py / __main__.py ─┘ (presentation + exit codes)
```

Dependencies point inward toward `models.py`, which depends on nothing but the
standard library. There are no cycles: `detect.py` composes `geo`, `scoring`,
and `models`; the CLI composes `data` and `detect`. This acyclic, single-
direction graph is what lets every layer be unit-tested in isolation.

## Data flow

1. **Ingest** — `parsers.parse_pings` (and the file wrapper `data.load_pings`)
   turn text rows into validated `Ping` instances, *streaming* line-by-line so a
   multi-gigabyte export never has to be resident in memory at once.
2. **Group & order** — `GapDetector.detect` buckets pings by `mmsi` and sorts
   each vessel's track chronologically (the `Ping` dataclass is `order=True` on
   `timestamp`), so interleaved or out-of-order exports are handled correctly.
3. **Measure** — for each consecutive pair exceeding `min_gap_s`, it computes the
   great-circle `distance_nm` (`geo.haversine_nm`) and the *implied speed* the
   vessel would have needed.
4. **Score** — `scoring.score_gap` blends three saturated signals into `[0, 1]`
   with human-readable `reasons`.
5. **Rank & present** — gaps are returned sorted by descending score; the CLI
   renders a table or JSON and sets an exit code.

## Invariants

- **Coordinate validity.** `Ping.__post_init__` rejects latitudes outside
  `[-90, 90]`, longitudes outside `[-180, 180]`, and non-positive MMSIs. Bad
  geometry can never enter the pipeline silently.
- **Timezone normalization.** Every `Ping.timestamp` is coerced to UTC; naive
  datetimes are assumed UTC. Duration arithmetic is therefore always well-defined.
- **Score bounds.** Each signal is clamped to `[0, 1]` before weighting, and the
  weights are validated to sum to 1.0 in `ScoreConfig.__post_init__`, so a score
  is provably within `[0, 1]`.
- **Header heuristic is first-row-only.** A non-integer second field is treated
  as a column header *only* on the first data row; mid-file it is a malformed
  record and raises `ParseError` (in strict mode). This prevents corrupt rows
  from masquerading as headers and being silently dropped.

## Complexity

For *N* pings across *V* vessels: grouping is O(N); per-vessel sorting is
O(n log n), summing to O(N log N) worst case; the gap walk is O(N). Memory is
O(N) for the eager path and O(max track length) for the streaming path. The
arithmetic per gap (haversine + scoring) is constant-time.

## Failure modes & handling

| Failure | Where | Behavior |
|--------|-------|----------|
| Malformed row | `parsers` | `ParseError` with 1-based line number (strict) or skip (`strict=False`) |
| Out-of-range coords / MMSI | `models.Ping` | `ValueError` at construction |
| Missing file | `data.load_pings` | `FileNotFoundError` |
| Bad score weights | `scoring.ScoreConfig` | `ValueError` at construction |
| Zero/negative `min_gap_s` | `detect.GapDetector` | `ValueError` at construction |

Errors fail fast and loud at the boundary; the core never guesses past invalid
input.

## Extension points

- **New signals.** Add a saturated term in `score_gap` and a weight in
  `ScoreConfig` (keep the weights summing to 1.0). Candidates: proximity to a
  known dark-activity zone, time-of-day, or loitering before the gap.
- **New inputs.** Add a parser that yields `Ping` objects (e.g. NMEA-0183, an
  API client) — everything downstream is input-agnostic.
- **New outputs.** `Gap.to_dict()` is the serialization seam; add a GeoJSON or
  STIX emitter beside the CLI without touching detection.
