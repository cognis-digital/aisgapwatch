"""Command-line interface: ``python -m aisgapwatch FILE [options]``.

Reads an AIS file, detects scored gaps, and prints them as a human table or in
a machine format (``--format json|ndjson|csv|geojson|stix``) for piping into
another tool or a CTI/GIS platform. Exit code is ``0`` when no gap meets
``--min-score`` and ``2`` when at least one does — so it composes in shell
pipelines and CI checks (``aisgapwatch tracks.csv --min-score 0.7 || alert``).

This tool is passive and offline: it only reads the position-report file you
give it. It never opens a network connection or touches anything but stdin/the
named file and stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .analyze import gap_context, track_stats
from .data import load_pings_list
from .detect import detect_gaps
from .emit import FORMATS, to_csv, to_geojson, to_ndjson, to_stix
from .scoring import ScoreConfig

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aisgapwatch",
        description="Detect & score AIS transponder-gap anomalies (defensive OSINT).")
    p.add_argument("file", help="AIS file: timestamp,mmsi,lat,lon per line (- for stdin)")
    p.add_argument("--min-gap", type=float, default=1800.0,
                   help="minimum gap length in seconds to report (default 1800)")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="only report gaps scoring >= this in [0,1] (default 0)")
    p.add_argument("--top", type=int, default=0, help="show only the top N gaps (0 = all)")
    p.add_argument("--format", choices=FORMATS, default=None,
                   help="machine output format: " + ", ".join(FORMATS))
    p.add_argument("--json", action="store_true",
                   help="shorthand for --format json (kept for compatibility)")
    p.add_argument("--stats", action="store_true",
                   help="also report per-vessel feed-quality stats (table mode)")
    p.add_argument("--context", action="store_true",
                   help="annotate table rows with reappearance bearing + plausibility")
    p.add_argument("--lenient", action="store_true", help="skip malformed rows instead of failing")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _format_table(gaps, *, context: bool = False) -> str:
    if not gaps:
        return "no gaps found"
    if context:
        rows = ["score  mmsi       duration  dist_nm  impl_kn  bearing  plaus       reasons",
                "-----  ---------  --------  -------  -------  -------  ----------  -------"]
        for g in gaps:
            ctx = gap_context(g)
            rows.append(
                f"{g.score:<5.2f}  {g.mmsi:<9}  {g.duration_h:>6.2f}h  "
                f"{g.distance_nm:>7.1f}  {g.implied_speed_kn:>7.1f}  "
                f"{ctx['reappear_compass']:>4}{ctx['bearing_deg']:>4.0f}  "
                f"{ctx['plausibility']:<10}  {','.join(g.reasons) or '-'}")
        return "\n".join(rows)
    rows = ["score  mmsi       duration  dist_nm  impl_kn  reasons",
            "-----  ---------  --------  -------  -------  -------"]
    for g in gaps:
        rows.append(f"{g.score:<5.2f}  {g.mmsi:<9}  {g.duration_h:>6.2f}h  "
                    f"{g.distance_nm:>7.1f}  {g.implied_speed_kn:>7.1f}  "
                    f"{','.join(g.reasons) or '-'}")
    return "\n".join(rows)


def _format_stats_table(stats) -> str:
    rows = ["mmsi       pings  span_h  med_int_s  max_int_s  dist_nm",
            "---------  -----  ------  ---------  ---------  -------"]
    for s in stats:
        rows.append(f"{s.mmsi:<9}  {s.n_pings:>5}  {s.span_h:>6.2f}  "
                    f"{s.median_interval_s:>9.0f}  {s.max_interval_s:>9.0f}  "
                    f"{s.total_distance_nm:>7.1f}")
    return "\n".join(rows)


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.file == "-":
        from .parsers import parse_text
        pings = parse_text(_read_text(args.file), strict=not args.lenient)
    else:
        pings = load_pings_list(args.file, strict=not args.lenient)

    gaps = detect_gaps(pings, min_gap_s=args.min_gap, min_score=args.min_score,
                       config=ScoreConfig())
    if args.top > 0:
        gaps = gaps[:args.top]

    fmt = args.format or ("json" if args.json else None)

    if fmt == "json":
        print(json.dumps({"file": args.file, "pings": len(pings),
                          "gaps": [g.to_dict() for g in gaps]}, indent=2))
    elif fmt == "ndjson":
        out = to_ndjson(gaps)
        if out:
            print(out)
    elif fmt == "csv":
        print(to_csv(gaps))
    elif fmt == "geojson":
        print(to_geojson(gaps))
    elif fmt == "stix":
        print(to_stix(gaps, source=args.file))
    else:
        print(f"{len(pings)} pings · {len(gaps)} gap(s)\n")
        print(_format_table(gaps, context=args.context))
        if args.stats:
            print("\nfeed-quality stats:\n")
            print(_format_stats_table(track_stats(pings)))

    return 2 if gaps else 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
