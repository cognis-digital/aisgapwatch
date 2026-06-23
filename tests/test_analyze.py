"""Tests for the derived, read-only analysis layer (bearing / plausibility /
track stats). These never change a gap's score — they only describe it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aisgapwatch import (Ping, compass_point, detect_gaps, gap_context,
                         initial_bearing_deg, plausibility_class, track_stats)
from aisgapwatch.analyze import PLAUSIBILITY_BANDS, TrackStats, _median


# ------------------------------------------------------------------- bearing
def test_bearing_due_east():
    assert initial_bearing_deg(0, 0, 0, 1) == pytest.approx(90.0, abs=0.5)


def test_bearing_due_north():
    assert initial_bearing_deg(0, 0, 1, 0) == pytest.approx(0.0, abs=0.5)


def test_bearing_due_west_wraps_positive():
    b = initial_bearing_deg(0, 0, 0, -1)
    assert b == pytest.approx(270.0, abs=0.5)
    assert 0.0 <= b < 360.0


def test_bearing_range_always_valid():
    for lat2, lon2 in [(10, 10), (-10, -10), (45, -90), (-30, 120)]:
        b = initial_bearing_deg(0, 0, lat2, lon2)
        assert 0.0 <= b < 360.0


# ------------------------------------------------------------------- compass
@pytest.mark.parametrize("deg,expect", [
    (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
    (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
    (359, "N"), (360, "N"),
])
def test_compass_points(deg, expect):
    assert compass_point(deg) == expect


def test_compass_wraps_over_360():
    assert compass_point(720 + 90) == "E"


# -------------------------------------------------------------- plausibility
@pytest.mark.parametrize("kn,label", [
    (0, "normal"), (8, "normal"), (14.9, "normal"),
    (15, "fast"), (24, "fast"),
    (25, "improbable"), (39, "improbable"),
    (40, "impossible"), (120, "impossible"),
])
def test_plausibility_classes(kn, label):
    assert plausibility_class(kn) == label


def test_plausibility_bands_are_ordered():
    ceilings = [c for c, _ in PLAUSIBILITY_BANDS]
    assert ceilings == sorted(ceilings)


# ----------------------------------------------------------------- _median
def test_median_odd():
    assert _median([3, 1, 2]) == 2


def test_median_even():
    assert _median([1, 2, 3, 4]) == 2.5


def test_median_empty():
    assert _median([]) == 0.0


def test_median_single():
    assert _median([7]) == 7


# -------------------------------------------------------------- track_stats
def _track(mmsi, start, steps):
    return [Ping(start + timedelta(minutes=m), mmsi, lat, lon)
            for m, lat, lon in steps]


def test_track_stats_basic_counts():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (60, 0, 0.1), (180, 0, 0.2)])
    stats = track_stats(pings)
    assert len(stats) == 1
    s = stats[0]
    assert s.mmsi == 1
    assert s.n_pings == 3
    assert s.span_h == pytest.approx(3.0)


def test_track_stats_intervals():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (30, 0, 0.1), (120, 0, 0.2)])
    s = track_stats(pings)[0]
    # intervals: 30min=1800s, 90min=5400s
    assert s.median_interval_s == pytest.approx(3600.0)
    assert s.max_interval_s == pytest.approx(5400.0)


def test_track_stats_single_ping_vessel():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(99, start, [(0, 10, 10)])
    s = track_stats(pings)[0]
    assert s.n_pings == 1
    assert s.span_h == 0.0
    assert s.max_interval_s == 0.0


def test_track_stats_sorts_sparsest_first():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    dense = _track(1, start, [(0, 0, 0), (10, 0, 0.1), (20, 0, 0.2)])
    sparse = _track(2, start, [(0, 0, 0), (600, 0, 0.1)])  # 10h gap
    stats = track_stats(dense + sparse)
    assert stats[0].mmsi == 2  # sparsest surfaces first


def test_track_stats_to_dict():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    s = track_stats(_track(1, start, [(0, 0, 0), (60, 0, 0.1)]))[0]
    d = s.to_dict()
    assert set(d) == {"mmsi", "n_pings", "span_h", "median_interval_s",
                      "max_interval_s", "total_distance_nm"}


def test_track_stats_distance_accumulates():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (60, 0, 1), (120, 0, 2)])
    s = track_stats(pings)[0]
    # ~60nm per degree longitude at equator, two legs
    assert s.total_distance_nm == pytest.approx(120.0, abs=1.0)


def test_trackstats_object_directly():
    s = TrackStats(1, 2, 1.0, 60.0, 120.0, 5.0)
    assert s.to_dict()["max_interval_s"] == 120.0


# --------------------------------------------------------------- gap_context
def test_gap_context_fields():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 37.0, -122.0), (300, 39.0, -124.0)])
    gaps = detect_gaps(pings, min_gap_s=1800)
    ctx = gap_context(gaps[0])
    assert set(ctx) == {"bearing_deg", "reappear_compass", "plausibility"}
    assert ctx["reappear_compass"] in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}


def test_gap_context_does_not_mutate_score():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (60, 5, 5)])
    g = detect_gaps(pings, min_gap_s=1800)[0]
    before = g.score
    gap_context(g)
    assert g.score == before
