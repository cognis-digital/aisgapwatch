"""Command-line interface: ``python -m aisgapwatch FILE [options]``.

Reads an AIS file, detects scored gaps, and prints them as a human table or as
JSON (``--json``) for piping into another tool. Exit code is ``0`` when no gap
meets ``--min-score`` and ``2`` when at least one does — so it composes in shell
pipelines and CI checks (``aisgapwatch tracks.csv --min-score 0.7 || alert``).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .data import load_pings_list
from .detect import detect_gaps
from .scoring import ScoreConfig

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aisgapwatch",
        description="Detect & score AIS transponder-gap anomalies (defensive OSINT).")
    p.add_argument("file", help="AIS file: timestamp,mmsi,lat,lon per line")
    p.add_argument("--min-gap", type=float, default=1800.0,
                   help="minimum gap length in seconds to report (default 1800)")
    p.add_argument("--min-score", type=float, default=0.0,
                   help="only report gaps scoring >= this in [0,1] (default 0)")
    p.add_argument("--top", type=int, default=0, help="show only the top N gaps (0 = all)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("--lenient", action="store_true", help="skip malformed rows instead of failing")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _format_table(gaps) -> str:
    if not gaps:
        return "no gaps found"
    rows = ["score  mmsi       duration  dist_nm  impl_kn  reasons",
            "-----  ---------  --------  -------  -------  -------"]
    for g in gaps:
        rows.append(f"{g.score:<5.2f}  {g.mmsi:<9}  {g.duration_h:>6.2f}h  "
                    f"{g.distance_nm:>7.1f}  {g.implied_speed_kn:>7.1f}  "
                    f"{','.join(g.reasons) or '-'}")
    return "\n".join(rows)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pings = load_pings_list(args.file, strict=not args.lenient)
    gaps = detect_gaps(pings, min_gap_s=args.min_gap, min_score=args.min_score,
                       config=ScoreConfig())
    if args.top > 0:
        gaps = gaps[:args.top]
    if args.json:
        print(json.dumps({"file": args.file, "pings": len(pings),
                          "gaps": [g.to_dict() for g in gaps]}, indent=2))
    else:
        print(f"{len(pings)} pings · {len(gaps)} gap(s)\n")
        print(_format_table(gaps))
    return 2 if gaps else 0


if __name__ == "__main__":   # pragma: no cover
    sys.exit(main())
