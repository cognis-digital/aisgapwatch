# aisgapwatch

**Detect and score AIS transponder-gap anomalies in vessel tracks** — maritime
situational awareness and defensive OSINT, in a dependency-free Python package.

When a vessel's AIS transponder goes quiet and then reappears somewhere it could
not plausibly have reached, that silence is worth a second look. `aisgapwatch`
finds those gaps and scores how suspicious each one is, so an analyst can triage
thousands of tracks down to the handful that matter.

> **Scope:** this is a *defensive*, descriptive, open-source-intelligence tool.
> It analyzes position-report data you already have. It does not track, target,
> or interfere with anything.

---

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

- `--json` emits machine-readable output for piping into another tool.
- `--min-score 0.7` reports only the suspicious gaps.
- **Exit code** is `2` when any gap is reported and `0` when none are — so it
  drops straight into a shell pipeline or CI alert:
  `aisgapwatch tracks.csv --min-score 0.8 && echo clean || alert`.

## Input format

One comma-separated position report per line: `timestamp,mmsi,lat,lon`. A header
row is detected and skipped automatically; `#` comments and blank lines are
ignored. Timestamps are ISO-8601 (a trailing `Z` is fine).

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

## Public API

```python
from aisgapwatch import (
    Ping, Gap,                       # data models
    parse_text, parse_pings, parse_line, ParseError,
    load_pings, load_pings_list,     # file I/O
    haversine_nm,                    # great-circle distance (nm)
    score_gap, ScoreConfig,          # scoring
    detect_gaps, GapDetector,        # detection
)
```

## Development

```bash
pip install -e ".[dev]"
pytest -q                 # 24 tests, no network
python examples/quickstart.py
```

CI runs the suite on Python 3.9 / 3.11 / 3.13 (see `.github/workflows/ci.yml`).

## License

MIT © Cognis Digital. See [LICENSE](LICENSE).
