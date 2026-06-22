"""Core data models for aisgapwatch.

A :class:`Ping` is one position report from a vessel's AIS transponder. A
:class:`Gap` is a stretch of time between two consecutive pings of the same
vessel that is long enough to be worth a second look, annotated with a
suspiciousness :attr:`Gap.score` and the human-readable :attr:`Gap.reasons`
that produced it.

Everything here is plain, typed, and dependency-free so the models can be
serialized, logged, or fed into another tool without surprises.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

__all__ = ["Ping", "Gap"]


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC (naive inputs are assumed UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True, order=True)
class Ping:
    """A single AIS position report.

    Ordering is by ``timestamp`` first so a list of pings sorts chronologically.
    Latitude/longitude are validated to real-world ranges; an out-of-range value
    raises ``ValueError`` rather than silently corrupting downstream geometry.
    """

    timestamp: datetime
    mmsi: int = field(compare=False)
    lat: float = field(compare=False)
    lon: float = field(compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp))
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")
        if self.mmsi <= 0:
            raise ValueError(f"mmsi must be positive: {self.mmsi}")

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp.isoformat(), "mmsi": self.mmsi,
                "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True)
class Gap:
    """A suspicious silence in a vessel's track.

    ``score`` is in ``[0, 1]`` (see :mod:`aisgapwatch.scoring`); ``reasons`` are
    short tags explaining why it scored where it did (e.g. ``"long-duration"``,
    ``"implied-speed-58kn"``). ``implied_speed_kn`` is the speed a vessel would
    have needed to travel ``distance_nm`` during the gap — a physically
    implausible value is a classic spoofing / dark-activity tell.
    """

    mmsi: int
    start: datetime
    end: datetime
    duration_s: float
    distance_nm: float
    implied_speed_kn: float
    start_pos: tuple[float, float]
    end_pos: tuple[float, float]
    score: float = 0.0
    reasons: tuple[str, ...] = ()

    @property
    def duration_h(self) -> float:
        return self.duration_s / 3600.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        d["duration_h"] = round(self.duration_h, 3)
        d["reasons"] = list(self.reasons)
        return d
