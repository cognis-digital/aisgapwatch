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

## Output emitters (`emit.py`)

`Gap.to_dict()` is the serialization seam; `emit.py` builds on it with pure,
dependency-free functions that turn a list of gaps into an interchange format:

| Function | Format | Consumer |
|----------|--------|----------|
| `to_ndjson` | newline-delimited JSON | log/SIEM pipelines |
| `to_csv` | RFC-4180 CSV | spreadsheets, pandas |
| `to_geojson` | `FeatureCollection` (per gap: a reappearance `LineString` + centroid `Point`) | QGIS, kepler.gl |
| `to_stix` | STIX 2.1 `bundle` of `observed-data` / `x-ais-gap` | MISP, OpenCTI |

STIX object ids are derived deterministically (`sha1(content)` shaped into a
UUID), so re-emitting the same gap yields the same id and downstream platforms
de-duplicate instead of accumulating copies. The CLI selects an emitter with
`--format`; everything detection-side is unchanged.

## Derived analysis (`analyze.py`)

A strictly read-only layer that *describes* gaps and tracks without touching the
score: `initial_bearing_deg` / `compass_point` give the reappearance direction,
`plausibility_class` buckets an implied speed into a human label, and
`track_stats` computes per-vessel feed-quality (span, median/max interval, total
distance). Because it only reads, the scoring core's provable `[0, 1]` bound is
untouched.

## Language ports (`ports/`)

The core `haversine → score → detect` pipeline plus the table/JSON CLI is
mirrored in Node, Go, and a POSIX `sh`+`awk` port. They share the reference's
thresholds, rounding, and exit codes and are verified against the same sample
track. The richer emitters and analysis layer remain Python-only. CI builds and
tests every port (`.github/workflows/ports.yml`).

## Extension points

- **New signals.** Add a saturated term in `score_gap` and a weight in
  `ScoreConfig` (keep the weights summing to 1.0). Candidates: proximity to a
  known dark-activity zone, time-of-day, or loitering before the gap.
- **New inputs.** Add a parser that yields `Ping` objects (e.g. NMEA-0183, an
  API client) — everything downstream is input-agnostic.
- **New outputs.** Add a function to `emit.py` next to the existing emitters; it
  only needs to consume `Gap` objects.
