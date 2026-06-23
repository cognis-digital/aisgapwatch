"""Detect gaps once, then emit every interchange format.

    python examples/export_formats.py

Shows how the same scored gaps feed a map (GeoJSON), a CTI platform (STIX 2.1),
a log pipeline (NDJSON), and a spreadsheet (CSV) — all offline, no network.
"""
from __future__ import annotations

from aisgapwatch import (detect_gaps, gap_context, parse_text, to_csv,
                         to_geojson, to_ndjson, to_stix, track_stats)

TRACK = """
timestamp,mmsi,lat,lon
2026-06-01T00:00:00Z,366123456,37.80,-122.40
2026-06-01T00:30:00Z,366123456,37.84,-122.50
2026-06-01T07:10:00Z,366123456,39.90,-124.80
2026-06-01T00:00:00Z,477888999,33.70,-118.20
2026-06-01T02:15:00Z,477888999,33.71,-118.22
"""


def main() -> None:
    pings = parse_text(TRACK)
    gaps = detect_gaps(pings, min_gap_s=1800)
    print(f"parsed {len(pings)} pings, found {len(gaps)} gap(s)\n")

    if gaps:
        worst = gaps[0]
        ctx = gap_context(worst)
        print(f"worst gap: MMSI {worst.mmsi}  score={worst.score}  "
              f"reappeared {ctx['reappear_compass']} ({ctx['bearing_deg']}deg), "
              f"plausibility={ctx['plausibility']}\n")

    print("--- NDJSON ---")
    print(to_ndjson(gaps))
    print("\n--- CSV ---")
    print(to_csv(gaps))
    print("\n--- GeoJSON (truncated) ---")
    print(to_geojson(gaps)[:300], "...")
    print("\n--- STIX 2.1 (truncated) ---")
    print(to_stix(gaps)[:300], "...")

    print("\n--- per-vessel feed-quality ---")
    for s in track_stats(pings):
        d = s.to_dict()
        print(f"  MMSI {d['mmsi']}: {d['n_pings']} pings over {d['span_h']}h, "
              f"max interval {d['max_interval_s']}s")


if __name__ == "__main__":
    main()
