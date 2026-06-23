# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semver.

## [0.2.0] - 2026-06-23

### Added
- **Output emitters** (`aisgapwatch.emit`): NDJSON, CSV, GeoJSON
  (`FeatureCollection` of per-gap reappearance `LineString` + centroid `Point`),
  and STIX 2.1 (`bundle` of `observed-data` wrapping `x-ais-gap` observables with
  deterministic, content-derived ids). Exposed on the CLI via
  `--format json|ndjson|csv|geojson|stix`.
- **Derived analysis** (`aisgapwatch.analyze`): `initial_bearing_deg`,
  `compass_point`, `plausibility_class`, per-vessel `track_stats` (feed-quality:
  span, median/max interval, total distance), and `gap_context`. All read-only —
  they describe a gap, never change its score.
- CLI: `--context` (bearing + plausibility per row), `--stats` (feed-quality
  table), and `-` to read a track from stdin. The legacy `--json` flag still
  works as shorthand for `--format json`.
- **Language ports** of the core CLI surface under `ports/`: Node (`node --test`),
  Go (`go test`), and a POSIX `sh`+`awk` port for air-gapped/minimal hosts — each
  verified to reproduce the reference scores. CI workflow `ports.yml` builds and
  tests all three on every push.
- Test suite expanded to 135 Python tests (216 assertions) plus 15 Node and 8
  shell smoke tests; all offline, stdlib + repo deps only.

### Notes
- Fully backward compatible: all 0.1.0 behaviour, output, exit codes, and the
  public API are unchanged; everything above is additive. Still dependency-free,
  passive, and offline.

## [0.1.0] - 2026-06-22

### Added
- Core data models: `Ping` (validated, UTC-normalized, chronologically ordered)
  and `Gap` (scored, with human-readable reasons and JSON serialization).
- `geo.haversine_nm` — great-circle distance in nautical miles.
- `parsers` — lenient text parsing with auto header-skip, comment/blank handling,
  and `ParseError` carrying line numbers; strict and lenient modes.
- `data.load_pings` / `load_pings_list` — streaming, memory-safe file ingest.
- `scoring.score_gap` + `ScoreConfig` — transparent, tunable, weight-validated
  suspicion score blending duration, implied speed, and positional jump.
- `detect.detect_gaps` / `GapDetector` — per-vessel grouping, ordering, gap
  detection, and score-ranked output.
- CLI (`python -m aisgapwatch`) with table/JSON output and pipeline-friendly exit
  codes (2 = gaps found, 0 = clean).
- Packaging (`pyproject.toml` with a console-script entry point), CI workflow
  (Python 3.9/3.11/3.13), runnable example + sample dataset, and a 24-test suite.
