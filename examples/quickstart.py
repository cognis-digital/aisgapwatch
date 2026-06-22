"""Runnable quickstart: parse a track, detect scored gaps, print the worst one.

    python examples/quickstart.py
"""
from __future__ import annotations

from aisgapwatch import detect_gaps, parse_text

TRACK = """
timestamp,mmsi,lat,lon
2026-06-01T00:00:00Z,366123456,37.80,-122.40
2026-06-01T00:30:00Z,366123456,37.84,-122.50
2026-06-01T07:10:00Z,366123456,39.90,-124.80
"""


def main() -> None:
    pings = parse_text(TRACK)
    gaps = detect_gaps(pings, min_gap_s=1800)
    print(f"parsed {len(pings)} pings, found {len(gaps)} gap(s)")
    if gaps:
        g = gaps[0]
        print(f"worst gap: MMSI {g.mmsi}  score={g.score}  "
              f"{g.duration_h:.1f}h  {g.distance_nm:.0f}nm  "
              f"implied {g.implied_speed_kn:.0f}kn")
        print("reasons:", ", ".join(g.reasons))


if __name__ == "__main__":
    main()
