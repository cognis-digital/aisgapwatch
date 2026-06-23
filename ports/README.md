# Language ports

The Python package in [`../aisgapwatch`](../aisgapwatch) is the reference
implementation. These ports mirror its **core CLI surface** — the same
`haversine` → `score` → `detect` pipeline, the same table/JSON output, and the
same pipeline-friendly exit codes (`2` = gaps found, `0` = clean) — so you can
triage an AIS track on whatever runtime a host already has, including
air-gapped or minimal boxes. Every port is dependency-free and offline.

All three are verified against the same `examples/sample_track.csv`: the dark
vessel `366123456` scores **0.87** (≈164 nm jump implying ≈27 kn over a ~6 h
blackout) and the slow drifter `477888999` scores **0.06** — identical to the
Python reference.

| Port | Runtime | Run | Test |
|------|---------|-----|------|
| [`node/`](node) | Node ≥ 18 | `node aisgapwatch.js FILE --json` | `node --test` |
| [`go/`](go) | Go ≥ 1.21 | `go run . FILE --json` | `go test ./...` |
| [`shell/`](shell) | POSIX sh + awk | `./aisgapwatch.sh FILE` | `bash test.sh` |

CI (`.github/workflows/ports.yml`) builds and tests every port on each push, so
they stay real and verifiable — not vaporware.

## Why a shell port?

The `shell/` port is a single `sh` + `awk` program. On an air-gapped or
busybox-class box with no Python, Node, or Go installed, it still triages a
track export end-to-end (parse → group by MMSI → haversine → score → ranked
table). It is the smallest possible footprint for the same analysis.

## Parity notes

- Timestamps without a timezone are treated as UTC, matching the Python
  reference's "naive == UTC" rule.
- Scores are rounded to 4 decimals and distances/speeds to 3, as in Python.
- Each port keeps the same default thresholds: `--min-gap 1800` s, saturation
  at 6 h / 40 kn / 50 nm, weights 0.4 / 0.4 / 0.2.

These are ports of the **core** surface (detect + score + table/JSON). The
richer emitters (GeoJSON, STIX 2.1, NDJSON, CSV) and the derived analysis
helpers live in the Python package.
