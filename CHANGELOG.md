# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semver.

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
