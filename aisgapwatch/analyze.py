"""Derived, read-only context for a detected gap.

These helpers add *interpretation* on top of a :class:`~aisgapwatch.models.Gap`
without touching the detection or scoring core — they never change a gap's
score, they only describe it. That keeps the scoring math (and its provably
bounded ``[0, 1]`` output) untouched while giving an analyst richer triage
context: which direction the vessel reappeared, a coarse plausibility class for
the implied speed, and simple feed-quality statistics over a whole track.

All functions are pure and dependency-free.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from .geo import haversine_nm
from .models import Gap, Ping

__all__ = [
    "initial_bearing_deg",
    "compass_point",
    "plausibility_class",
    "PLAUSIBILITY_BANDS",
    "track_stats",
    "TrackStats",
]


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees in ``[0, 360)``.

    >>> round(initial_bearing_deg(0, 0, 0, 1))   # due east
    90
    """
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(rlat2)
    x = (math.cos(rlat1) * math.sin(rlat2)
         - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon))
    brng = math.degrees(math.atan2(y, x))
    return (brng + 360.0) % 360.0


_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass_point(bearing_deg: float) -> str:
    """Map a bearing in degrees to one of 8 compass points.

    >>> compass_point(0), compass_point(90), compass_point(225)
    ('N', 'E', 'SW')
    """
    idx = int((bearing_deg % 360.0) / 45.0 + 0.5) % 8
    return _COMPASS[idx]


#: Coarse, human-facing plausibility bands for an implied speed (knots).
#: These are descriptive triage labels, not a verdict — a fast ferry can hit
#: 40 kn, but a laden bulk carrier reappearing at 40 kn is physically impossible.
PLAUSIBILITY_BANDS = (
    (15.0, "normal"),       # routine merchant cruising speed
    (25.0, "fast"),         # fast vessel / favourable current
    (40.0, "improbable"),   # only a few vessel classes sustain this
    (float("inf"), "impossible"),  # not achievable by a surface vessel
)


def plausibility_class(implied_speed_kn: float) -> str:
    """Bucket an implied speed into a descriptive plausibility band.

    >>> plausibility_class(8), plausibility_class(60)
    ('normal', 'impossible')
    """
    for ceiling, label in PLAUSIBILITY_BANDS:
        if implied_speed_kn < ceiling:
            return label
    return "impossible"


class TrackStats:
    """Simple feed-quality statistics for one vessel's track.

    Attributes are plain numbers so the object serializes trivially. ``coverage``
    is the fraction of the observed window actually represented by reports at the
    median cadence — a low value means a sparse or intermittent feed.
    """

    __slots__ = ("mmsi", "n_pings", "span_h", "median_interval_s",
                 "max_interval_s", "total_distance_nm")

    def __init__(self, mmsi: int, n_pings: int, span_h: float,
                 median_interval_s: float, max_interval_s: float,
                 total_distance_nm: float) -> None:
        self.mmsi = mmsi
        self.n_pings = n_pings
        self.span_h = span_h
        self.median_interval_s = median_interval_s
        self.max_interval_s = max_interval_s
        self.total_distance_nm = total_distance_nm

    def to_dict(self) -> dict:
        return {
            "mmsi": self.mmsi,
            "n_pings": self.n_pings,
            "span_h": round(self.span_h, 3),
            "median_interval_s": round(self.median_interval_s, 1),
            "max_interval_s": round(self.max_interval_s, 1),
            "total_distance_nm": round(self.total_distance_nm, 3),
        }


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def track_stats(pings: Iterable[Ping]) -> list[TrackStats]:
    """Per-vessel feed-quality stats, one :class:`TrackStats` per MMSI.

    Vessels are returned sorted by descending ``max_interval_s`` so the sparsest
    (most gap-prone) tracks surface first.
    """
    by_vessel: dict[int, list[Ping]] = defaultdict(list)
    for p in pings:
        by_vessel[p.mmsi].append(p)

    out: list[TrackStats] = []
    for mmsi, track in by_vessel.items():
        track.sort()
        if len(track) < 2:
            out.append(TrackStats(mmsi, len(track), 0.0, 0.0, 0.0, 0.0))
            continue
        intervals: list[float] = []
        dist = 0.0
        for prev, cur in zip(track, track[1:]):
            intervals.append((cur.timestamp - prev.timestamp).total_seconds())
            dist += haversine_nm(prev.lat, prev.lon, cur.lat, cur.lon)
        span_h = (track[-1].timestamp - track[0].timestamp).total_seconds() / 3600.0
        out.append(TrackStats(
            mmsi=mmsi, n_pings=len(track), span_h=span_h,
            median_interval_s=_median(intervals),
            max_interval_s=max(intervals), total_distance_nm=dist,
        ))
    out.sort(key=lambda s: s.max_interval_s, reverse=True)
    return out


def gap_context(gap: Gap) -> dict:
    """A small dict of derived, read-only context for a single gap.

    Adds reappearance bearing/compass point and a plausibility label on top of
    the gap's own fields — handy for a report row, never altering the score.
    """
    bearing = initial_bearing_deg(
        gap.start_pos[0], gap.start_pos[1], gap.end_pos[0], gap.end_pos[1])
    return {
        "bearing_deg": round(bearing, 1),
        "reappear_compass": compass_point(bearing),
        "plausibility": plausibility_class(gap.implied_speed_kn),
    }


__all__.append("gap_context")
