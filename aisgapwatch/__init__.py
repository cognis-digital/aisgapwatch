"""aisgapwatch — detect and score AIS transponder-gap anomalies in vessel tracks.

Maritime situational awareness, defensive OSINT. Feed it position reports; it
finds the silences where a vessel went dark and scores how suspicious each one
is (long duration, an impossible implied speed on reappearance, a big positional
jump — the classic tells of AIS spoofing and dark rendezvous).

Quickstart
----------
>>> from aisgapwatch import detect_gaps, parse_text
>>> pings = parse_text('''
... 2026-06-01T00:00:00Z,366123456,37.80,-122.40
... 2026-06-01T05:00:00Z,366123456,38.50,-123.90
... ''')
>>> gaps = detect_gaps(pings, min_gap_s=1800)
>>> gaps[0].mmsi, round(gaps[0].score, 2)        # doctest: +SKIP
(366123456, 0.5)
"""
from __future__ import annotations

from .models import Gap, Ping
from .parsers import ParseError, parse_line, parse_pings, parse_text
from .data import load_pings, load_pings_list
from .geo import haversine_nm
from .scoring import ScoreConfig, score_gap
from .detect import GapDetector, detect_gaps
from .emit import to_ndjson, to_csv, to_geojson, to_stix, FORMATS
from .analyze import (
    initial_bearing_deg, compass_point, plausibility_class, PLAUSIBILITY_BANDS,
    track_stats, TrackStats, gap_context,
)

__version__ = "0.2.0"

__all__ = [
    "Ping", "Gap",
    "ParseError", "parse_line", "parse_pings", "parse_text",
    "load_pings", "load_pings_list",
    "haversine_nm",
    "ScoreConfig", "score_gap",
    "GapDetector", "detect_gaps",
    # emitters
    "to_ndjson", "to_csv", "to_geojson", "to_stix", "FORMATS",
    # derived analysis
    "initial_bearing_deg", "compass_point", "plausibility_class",
    "PLAUSIBILITY_BANDS", "track_stats", "TrackStats", "gap_context",
    "__version__",
]
