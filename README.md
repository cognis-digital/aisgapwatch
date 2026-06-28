# aisgapwatch

**Detect and score AIS transponder-gap anomalies in vessel tracks** — maritime
situational awareness and defensive OSINT, in a dependency-free Python package
with Go / Node / shell ports.

When a vessel's AIS transponder goes quiet and then reappears somewhere it could
not plausibly have reached, that silence is worth a second look. `aisgapwatch`
finds those gaps and scores how suspicious each one is, so an analyst can triage
thousands of tracks down to the handful that matter — then export the survivors
straight to a map (GeoJSON), a CTI platform (STIX 2.1), or a log pipeline
(NDJSON/CSV).

> **Scope:** this is a *defensive*, descriptive, open-source-intelligence tool.
> It analyzes position-report data you already have. It is **passive and
> offline** — it never opens a network connection, never tracks, targets, or
> interferes with anything, and emits no active probes. See
> [Scope, authorization & safety](#scope-authorization--safety).

---


<!-- cognis:example:start -->
## 🔎 Example output

Real, reproducible output from the tool — runs offline:

```console
$ aisgapwatch --version
aisgapwatch 0.2.0
```

```console
$ aisgapwatch --help
usage: aisgapwatch [-h] [--min-gap MIN_GAP] [--min-score MIN_SCORE]
                   [--top TOP] [--format {json,ndjson,csv,geojson,stix}]
                   [--json] [--stats] [--context] [--lenient] [--version]
                   file

Detect & score AIS transponder-gap anomalies (defensive OSINT).

positional arguments:
  file                  AIS file: timestamp,mmsi,lat,lon per line (- for
                        stdin)

options:
  -h, --help            show this help message and exit
  --min-gap MIN_GAP     minimum gap length in seconds to report (default 1800)
  --min-score MIN_SCORE
                        only report gaps scoring >= this in [0,1] (default 0)
  --top TOP             show only the top N gaps (0 = all)
  --format {json,ndjson,csv,geojson,stix}
                        machine output format: json, ndjson, csv, geojson,
                        stix
  --json                shorthand for --format json (kept for compatibility)
  --stats               also report per-vessel feed-quality stats (table mode)
  --context             annotate table rows with reappearance bearing +
                        plausibility
  --lenient             skip malformed rows instead of failing
  --version             show program's version number and exit
```

> Blocks above are real `aisgapwatch` output — reproduce them from a clone.

**Sample result format** _(illustrative values — run on your own data for real findings):_

```
{
"results": [
{
"gaps": [
{
"start_time": "2023-02-15T14:30:00Z",
"end_time": "2023-02-15T14:35:00Z",
"mmsi": 123456,
"lat": 45.1234,
"lon": -122.3456,
"score": 0.8
},
{
"start_time": "2023-02-16T10:20:00Z",
"end_time": "2023-02-16T10:25:00Z",
"mmsi": 789012,
"lat": 37.6543,
"lon": -87.9012,
"score": 0.9
}
],
"stats": {
"total_gaps": 5,
"top_score": 0.9,
"avg_gap_len": 300
}
}
]
```

<!-- cognis:example:end -->

## Why this exists

A "dark" period in an AIS feed is one of the strongest open signals of sanctions
evasion, illicit ship-to-ship transfer (a *dark rendezvous*), or GPS/AIS
spoofing. But raw feeds are enormous and most gaps are benign (a weak receiver, a
vessel at the edge of coverage). The hard part is **ranking**: which silences are
physically implausible? `aisgapwatch` answers that with a transparent, tunable
score instead of a black box.

## Install

```bash
pip install -e .          # from a clone
# or, once published:
pip install aisgapwatch
```

Stdlib-only — no NumPy, no pandas, no network. Python 3.9+.

## Quickstart (library)

```python
from aisgapwatch import parse_text, detect_gaps

pings = parse_text("""
timestamp,mmsi,lat,lon
2026-06-01T00:00:00Z,366123456,37.80,-122.40
2026-06-01T00:30:00Z,366123456,37.84,-122.50
2026-06-01T07:10:00Z,366123456,39.90,-124.80
""")

for gap in detect_gaps(pings, min_gap_s=1800):
    print(gap.mmsi, gap.score, gap.reasons)
# 366123456 0.8657 ('long-duration-6.2h', 'implied-speed-27kn', 'position-jump-164nm')
```

## Quickstart (CLI)

```bash
python -m aisgapwatch examples/sample_track.csv --min-gap 1800
```
```
9 pings · 4 gap(s)

score  mmsi       duration  dist_nm  impl_kn  reasons
-----  ---------  --------  -------  -------  -------
0.87   366123456    6.17h    163.9     26.6  long-duration-6.2h,implied-speed-27kn,position-jump-164nm
0.06   477888999    0.75h      0.6      0.8  -
```

- **Exit code** is `2` when any gap is reported and `0` when none are — so it
  drops straight into a shell pipeline or CI alert:
  `aisgapwatch tracks.csv --min-score 0.8 && echo clean || alert`.
- `--min-score 0.7` reports only the suspicious gaps; `--top N` keeps the worst N.
- `--context` annotates each row with the reappearance bearing and a coarse
  plausibility class; `--stats` adds a per-vessel feed-quality table.
- Pass `-` as the file to read a track from **stdin**.

### Output formats

`--format` renders machine-readable output for whatever you feed next:

| `--format` | Shape | Feeds |
|------------|-------|-------|
| `json`     | one object with `file`, `pings`, `gaps[]` (also `--json`) | jq, scripts |
| `ndjson`   | one JSON gap per line | Splunk, Elastic, log pipelines |
| `csv`      | flat table, RFC-4180 quoted | spreadsheets, pandas |
| `geojson`  | `FeatureCollection`: a reappearance `LineString` + centroid `Point` per gap | QGIS, kepler.gl, geojson.io |
| `stix`     | STIX 2.1 `bundle` of `observed-data` wrapping `x-ais-gap` observables (deterministic ids) | MISP, OpenCTI, other CTI |

```bash
# Drop the suspicious gaps onto a map
python -m aisgapwatch tracks.csv --min-score 0.7 --format geojson > gaps.geojson

# Stream into a CTI platform
python -m aisgapwatch tracks.csv --format stix > gaps.stix.json

# Tail straight into jq
python -m aisgapwatch tracks.csv --format ndjson | jq 'select(.score > 0.8)'
```

STIX ids are derived deterministically from gap content (sha1 → UUID shape), so
re-emitting the same gap is stable and a CTI platform de-duplicates cleanly.
**No identifier, vessel, or intel is ever fabricated** — every field is computed
from the position reports you provided.

## Input format

One comma-separated position report per line: `timestamp,mmsi,lat,lon`. A header
row is detected and skipped automatically; `#` comments and blank lines are
ignored. Timestamps are ISO-8601 (a trailing `Z` is fine; a bare timestamp is
treated as UTC).

## How the score works (in plain terms)

Every gap gets a score from 0 (boring) to 1 (look at this now), blended from
three signals that each *saturate* so none can dominate:

| Signal | Plain meaning | Maxes out at |
|--------|---------------|--------------|
| **duration** | how long the vessel was silent | 6 hours |
| **implied speed** | how fast it *would* have had to travel to reappear where it did | 40 knots |
| **distance** | how far the reappearance jumped | 50 nm |

A merchant ship cannot do 40 knots, so a high implied speed is the loudest tell.
Tune the saturation points and weights with `ScoreConfig` — see
[`docs/01_ARCHITECTURE.md`](docs/01_ARCHITECTURE.md) for the design and
[`docs/02_EXPLAINED_SIMPLY.md`](docs/02_EXPLAINED_SIMPLY.md) for a from-scratch tour.

The score is *descriptive*, never a verdict: `--context` adds a coarse
plausibility band (`normal` / `fast` / `improbable` / `impossible`) and the
reappearance bearing to help an analyst, without ever changing the score.

## Public API

```python
from aisgapwatch import (
    Ping, Gap,                       # data models
    parse_text, parse_pings, parse_line, ParseError,
    load_pings, load_pings_list,     # file I/O
    haversine_nm,                    # great-circle distance (nm)
    score_gap, ScoreConfig,          # scoring
    detect_gaps, GapDetector,        # detection
    to_ndjson, to_csv, to_geojson, to_stix, FORMATS,   # emitters
    track_stats, gap_context,        # derived, read-only analysis
    initial_bearing_deg, compass_point, plausibility_class,
)
```

## Language ports

The core CLI surface is mirrored in three runtimes so you can triage a track on
whatever a host already has. All are dependency-free and produce the same scores
and exit codes as the Python reference (see [`ports/`](ports)).

| Port | Runtime | Run | Test |
|------|---------|-----|------|
| [`ports/node`](ports/node)   | Node ≥ 18 | `node aisgapwatch.js FILE --json` | `node --test` |
| [`ports/go`](ports/go)       | Go ≥ 1.21 | `go run . FILE --json` | `go test ./...` |
| [`ports/shell`](ports/shell) | POSIX sh + awk | `./aisgapwatch.sh FILE` | `bash test.sh` |

CI (`.github/workflows/ports.yml`) builds and tests every port on each push.

## Edge / air-gap

`aisgapwatch` is built for the disconnected case. There are **no runtime
dependencies and no network calls** in any implementation, so the whole tool
runs from a thumb drive on an air-gapped analysis box. The
[`ports/shell`](ports/shell) port is a single `sh` + `awk` script — on a
busybox-class machine with no Python, Node, or Go, it still triages a track
export end-to-end. Copy the repo (or just one port) across and run; nothing
phones home.

## Development

```bash
pip install -e ".[dev]"
pytest -q                 # 135 tests, no network
python examples/quickstart.py
```

CI runs the Python suite on 3.9 / 3.11 / 3.13 (`.github/workflows/ci.yml`) and
the ports on every push (`.github/workflows/ports.yml`).

## Scope, authorization & safety

- **Defensive / authorized-use only.** `aisgapwatch` analyzes AIS
  position-report data you already have a lawful basis to process. It is for
  situational awareness, force protection, sanctions/safety analysis, and
  research — not for targeting, interdiction, or any offensive use.
- **Passive and offline.** It performs *no* active scanning, transmission, or
  network access. It reads a file (or stdin) and writes a report. There is no
  flag that turns on any network behaviour, because there is none.
- **No fabricated intelligence.** Every score, reason, coordinate, and exported
  identifier is computed from your input. The tool never invents vessels, CVEs,
  fingerprints, or attribution.
- A high score means *"a human should look at this,"* not *"this vessel did
  something wrong."* Benign explanations (receiver coverage, equipment faults)
  are common; treat the output as triage, not adjudication.

## License

MIT © Cognis Digital. See [LICENSE](LICENSE).
