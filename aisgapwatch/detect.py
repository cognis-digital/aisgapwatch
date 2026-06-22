"""Detect transponder gaps in vessel tracks and score them.

A *gap* is two consecutive pings of the same vessel (MMSI) separated by more
than ``min_gap_s`` seconds. For each gap we compute the great-circle distance
between the last-seen and next-seen positions and the *implied speed* needed to
cover it, then hand those to :func:`~aisgapwatch.scoring.score_gap`.

Pings from many vessels can be interleaved in the input; we group by MMSI and
sort each track chronologically before walking it, so unordered exports are
handled correctly.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .geo import haversine_nm
from .models import Gap, Ping
from .scoring import ScoreConfig, score_gap

__all__ = ["detect_gaps", "GapDetector"]


def _implied_speed_kn(distance_nm: float, duration_s: float) -> float:
    hours = duration_s / 3600.0
    if hours <= 0:
        return 0.0
    return distance_nm / hours


class GapDetector:
    """Stateful, reusable detector. Construct once, call :meth:`detect` many times."""

    def __init__(self, min_gap_s: float = 1800.0, min_score: float = 0.0,
                 config: ScoreConfig | None = None) -> None:
        if min_gap_s <= 0:
            raise ValueError("min_gap_s must be positive")
        self.min_gap_s = float(min_gap_s)
        self.min_score = float(min_score)
        self.config = config or ScoreConfig()

    def detect(self, pings: Iterable[Ping]) -> list[Gap]:
        by_vessel: dict[int, list[Ping]] = defaultdict(list)
        for p in pings:
            by_vessel[p.mmsi].append(p)

        gaps: list[Gap] = []
        for mmsi, track in by_vessel.items():
            track.sort()  # Ping orders by timestamp
            for prev, cur in zip(track, track[1:]):
                duration_s = (cur.timestamp - prev.timestamp).total_seconds()
                if duration_s <= self.min_gap_s:
                    continue
                dist = haversine_nm(prev.lat, prev.lon, cur.lat, cur.lon)
                speed = _implied_speed_kn(dist, duration_s)
                score, reasons = score_gap(duration_s, dist, speed, self.config)
                if score < self.min_score:
                    continue
                gaps.append(Gap(
                    mmsi=mmsi, start=prev.timestamp, end=cur.timestamp,
                    duration_s=duration_s, distance_nm=round(dist, 3),
                    implied_speed_kn=round(speed, 3),
                    start_pos=(prev.lat, prev.lon), end_pos=(cur.lat, cur.lon),
                    score=score, reasons=reasons,
                ))
        gaps.sort(key=lambda g: g.score, reverse=True)
        return gaps


def detect_gaps(pings: Iterable[Ping], *, min_gap_s: float = 1800.0,
                min_score: float = 0.0, config: ScoreConfig | None = None) -> list[Gap]:
    """Functional convenience wrapper around :class:`GapDetector`.

    Returns gaps sorted by descending suspiciousness score.
    """
    return GapDetector(min_gap_s=min_gap_s, min_score=min_score,
                       config=config).detect(pings)
