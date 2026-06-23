"""Additional core-coverage tests: geometry, scoring monotonicity & weighting,
detector edge cases, parser robustness, model serialization. Offline, stdlib.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aisgapwatch import (Gap, GapDetector, ParseError, Ping, ScoreConfig,
                         detect_gaps, haversine_nm, parse_line, parse_text,
                         score_gap)
from aisgapwatch.geo import EARTH_RADIUS_NM


# --------------------------------------------------------------------- geo
def test_haversine_pole_to_pole():
    # ~ half the great circle: pi * R
    d = haversine_nm(-90, 0, 90, 0)
    assert d == pytest.approx(3.14159265 * EARTH_RADIUS_NM, rel=1e-4)


def test_haversine_quarter_circle():
    d = haversine_nm(0, 0, 0, 90)
    assert d == pytest.approx(1.5707963 * EARTH_RADIUS_NM, rel=1e-4)


def test_haversine_antimeridian():
    # just across the dateline is a tiny distance
    d = haversine_nm(0, 179.99, 0, -179.99)
    assert d == pytest.approx(60.0 * 0.02, abs=0.5)


def test_haversine_monotonic_in_separation():
    base = haversine_nm(0, 0, 0, 1)
    more = haversine_nm(0, 0, 0, 2)
    assert more > base


def test_haversine_nonnegative():
    for a in [(0, 0, 0, 0), (45, 45, -45, -45), (10, 200 - 360, 20, 30)]:
        assert haversine_nm(*a) >= 0.0


# ------------------------------------------------------------------- scoring
def test_score_monotonic_in_duration():
    s1, _ = score_gap(3600, 10, 5)
    s2, _ = score_gap(7200, 10, 5)
    assert s2 >= s1


def test_score_monotonic_in_speed():
    s1, _ = score_gap(3600, 10, 5)
    s2, _ = score_gap(3600, 10, 35)
    assert s2 >= s1


def test_score_monotonic_in_distance():
    s1, _ = score_gap(3600, 5, 5)
    s2, _ = score_gap(3600, 45, 5)
    assert s2 >= s1


def test_score_zero_inputs():
    s, reasons = score_gap(0, 0, 0)
    assert s == 0.0
    assert reasons == ()


def test_score_all_signals_max():
    s, reasons = score_gap(6 * 3600, 50, 40)
    assert s == pytest.approx(1.0)
    assert len(reasons) == 3


def test_score_custom_weights():
    cfg = ScoreConfig(w_duration=1.0, w_speed=0.0, w_distance=0.0)
    # only duration matters now
    s_dur, _ = score_gap(6 * 3600, 0, 0, cfg)
    s_speed, _ = score_gap(0, 0, 40, cfg)
    assert s_dur == pytest.approx(1.0)
    assert s_speed == pytest.approx(0.0)


def test_score_config_rejects_nonpositive_saturation():
    with pytest.raises(ValueError):
        ScoreConfig(duration_full_h=0)
    with pytest.raises(ValueError):
        ScoreConfig(speed_impossible_kn=-1)
    with pytest.raises(ValueError):
        ScoreConfig(distance_full_nm=0)


def test_score_reason_threshold_half():
    # implied speed exactly half of saturation -> reason fires at >= 0.5
    s, reasons = score_gap(60, 1, 20)  # 20/40 = 0.5
    assert any("implied-speed" in r for r in reasons)


def test_score_reason_below_half_silent():
    s, reasons = score_gap(60, 1, 19.9)
    assert not any("implied-speed" in r for r in reasons)


def test_score_returns_rounded():
    s, _ = score_gap(1234, 12.34, 5.67)
    assert s == round(s, 4)


# ------------------------------------------------------------------- detect
def _track(mmsi, start, steps):
    return [Ping(start + timedelta(minutes=m), mmsi, lat, lon)
            for m, lat, lon in steps]


def test_detect_empty_input():
    assert detect_gaps([]) == []


def test_detect_single_ping_no_gap():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert detect_gaps(_track(1, start, [(0, 0, 0)])) == []


def test_detect_multiple_gaps_one_vessel():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (300, 1, 1), (700, 2, 2)])
    gaps = detect_gaps(pings, min_gap_s=1800)
    assert len(gaps) == 2


def test_detect_exact_threshold_excluded():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (30, 0, 0.01)])  # exactly 1800s
    assert detect_gaps(pings, min_gap_s=1800) == []


def test_detect_just_over_threshold_included():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (31, 0, 0.01)])  # 1860s
    assert len(detect_gaps(pings, min_gap_s=1800)) == 1


def test_detector_reusable_across_calls():
    det = GapDetector(min_gap_s=1800)
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    a = det.detect(_track(1, start, [(0, 0, 0), (300, 1, 1)]))
    b = det.detect(_track(2, start, [(0, 0, 0), (300, 1, 1)]))
    assert a[0].mmsi == 1 and b[0].mmsi == 2


def test_detect_custom_config_changes_scores():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (200, 0, 0.5)])
    loose = detect_gaps(pings, min_gap_s=1800, config=ScoreConfig())
    strict = detect_gaps(pings, min_gap_s=1800,
                         config=ScoreConfig(duration_full_h=1.0,
                                            speed_impossible_kn=10.0,
                                            distance_full_nm=10.0))
    assert strict[0].score >= loose[0].score


def test_detect_gap_fields_consistent():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 37.0, -122.0), (300, 39.0, -124.0)])
    g = detect_gaps(pings, min_gap_s=1800)[0]
    assert g.start_pos == (37.0, -122.0)
    assert g.end_pos == (39.0, -124.0)
    assert g.duration_s == pytest.approx(18000.0)
    assert 0.0 <= g.score <= 1.0


def test_detect_implied_speed_matches_manual():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (60, 0, 1)])  # ~60nm in 1h
    g = detect_gaps(pings, min_gap_s=1800)[0]
    assert g.implied_speed_kn == pytest.approx(60.0, abs=1.0)


# ------------------------------------------------------------------- parsers
def test_parse_line_with_whitespace():
    p = parse_line("  2026-06-01T00:00:00Z , 1 , 2.5 , 3.5 ")
    assert p.mmsi == 1 and p.lat == pytest.approx(2.5)


def test_parse_line_too_few_fields():
    with pytest.raises(ValueError):
        parse_line("2026-06-01T00:00:00Z,1,2")


def test_parse_text_no_header():
    pings = parse_text("2026-06-01T00:00:00Z,1,0,0\n"
                       "2026-06-01T01:00:00Z,1,1,1\n")
    assert len(pings) == 2


def test_parse_midfile_bad_row_raises():
    with pytest.raises(ParseError):
        parse_text("2026-06-01T00:00:00Z,1,0,0\n"
                   "not,a,number,row\n")


def test_parse_extra_fields_ignored():
    p = parse_line("2026-06-01T00:00:00Z,1,2,3,extra,more")
    assert p.lon == pytest.approx(3.0)


def test_parse_lenient_counts_survivors():
    pings = parse_text("2026-06-01T00:00:00Z,1,0,0\n"
                       "junk\n"
                       "2026-06-01T02:00:00Z,2,1,1\n", strict=False)
    assert len(pings) == 2


# -------------------------------------------------------------------- models
def test_ping_to_dict_iso():
    p = Ping(datetime(2026, 6, 1, tzinfo=timezone.utc), 1, 2.0, 3.0)
    d = p.to_dict()
    assert d["timestamp"].startswith("2026-06-01")
    assert d["mmsi"] == 1


def test_ping_aware_timestamp_converted_to_utc():
    from datetime import timedelta as td
    tz = timezone(td(hours=5))
    p = Ping(datetime(2026, 6, 1, 5, 0, 0, tzinfo=tz), 1, 0, 0)
    assert p.timestamp.tzinfo is timezone.utc
    assert p.timestamp.hour == 0


def test_gap_duration_h_property():
    g = Gap(1, datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 3, tzinfo=timezone.utc), 10800, 1, 1,
            (0, 0), (0, 0), 0.1, ())
    assert g.duration_h == pytest.approx(3.0)


def test_gap_to_dict_complete():
    g = Gap(1, datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 1, tzinfo=timezone.utc), 3600, 5, 5,
            (1, 2), (3, 4), 0.2, ("x",))
    d = g.to_dict()
    for key in ("mmsi", "start", "end", "duration_s", "distance_nm",
                "implied_speed_kn", "start_pos", "end_pos", "score",
                "reasons", "duration_h"):
        assert key in d


def test_ping_boundary_coords_ok():
    Ping(datetime(2026, 1, 1, tzinfo=timezone.utc), 1, 90.0, 180.0)
    Ping(datetime(2026, 1, 1, tzinfo=timezone.utc), 1, -90.0, -180.0)
