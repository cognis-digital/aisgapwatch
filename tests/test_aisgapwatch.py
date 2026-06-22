"""Full test suite for aisgapwatch. No network, no fixtures on disk except a
tmp_path file for the loader. Run: pytest -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aisgapwatch import (Gap, GapDetector, ParseError, Ping, ScoreConfig,
                         detect_gaps, haversine_nm, load_pings_list, parse_line,
                         parse_text, score_gap)


# --------------------------------------------------------------------- models
def test_ping_normalizes_naive_to_utc():
    p = Ping(datetime(2026, 6, 1, 12, 0, 0), 366000001, 37.8, -122.4)
    assert p.timestamp.tzinfo is timezone.utc


def test_ping_rejects_bad_coords_and_mmsi():
    with pytest.raises(ValueError):
        Ping(datetime.now(timezone.utc), 1, 95.0, 0.0)
    with pytest.raises(ValueError):
        Ping(datetime.now(timezone.utc), 1, 0.0, 200.0)
    with pytest.raises(ValueError):
        Ping(datetime.now(timezone.utc), 0, 0.0, 0.0)


def test_ping_sorts_chronologically():
    a = Ping(datetime(2026, 1, 1, tzinfo=timezone.utc), 1, 0, 0)
    b = Ping(datetime(2026, 1, 2, tzinfo=timezone.utc), 1, 0, 0)
    assert sorted([b, a]) == [a, b]


def test_gap_to_dict_roundtrips_iso():
    g = Gap(1, datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 2, tzinfo=timezone.utc), 7200, 10, 5,
            (0, 0), (0.1, 0.1), 0.3, ("long-duration-2.0h",))
    d = g.to_dict()
    assert d["start"].startswith("2026-01-01")
    assert d["duration_h"] == 2.0
    assert d["reasons"] == ["long-duration-2.0h"]


# ------------------------------------------------------------------------ geo
def test_haversine_known_distance():
    # one degree of longitude at the equator ≈ 60 nm
    assert haversine_nm(0, 0, 0, 1) == pytest.approx(60.0, abs=0.2)


def test_haversine_zero_and_symmetry():
    assert haversine_nm(10, 20, 10, 20) == pytest.approx(0.0, abs=1e-9)
    assert haversine_nm(1, 2, 3, 4) == pytest.approx(haversine_nm(3, 4, 1, 2))


# -------------------------------------------------------------------- parsers
def test_parse_line_basic():
    p = parse_line("2026-06-01T00:00:00Z,366123456,37.80,-122.40")
    assert p.mmsi == 366123456 and p.lat == pytest.approx(37.80)


def test_parse_text_skips_header_comments_blanks():
    text = ("timestamp,mmsi,lat,lon\n"
            "# a comment\n"
            "\n"
            "2026-06-01T00:00:00Z,1,0,0\n"
            "2026-06-01T01:00:00,2,1,1\n")   # no trailing Z still parses
    pings = parse_text(text)
    assert len(pings) == 2
    assert {p.mmsi for p in pings} == {1, 2}


def test_parse_strict_raises_with_line_number():
    with pytest.raises(ParseError) as ei:
        parse_text("2026-06-01T00:00:00Z,1,0,0\nGARBAGE,x,y\n")
    assert ei.value.line_no == 2


def test_parse_lenient_skips_bad_rows():
    pings = parse_text("2026-06-01T00:00:00Z,1,0,0\nGARBAGE,x,y\n", strict=False)
    assert len(pings) == 1


# -------------------------------------------------------------------- scoring
def test_score_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        ScoreConfig(w_duration=0.5, w_speed=0.5, w_distance=0.5)


def test_score_impossible_speed_flags():
    score, reasons = score_gap(duration_s=600, distance_nm=20, implied_speed_kn=120)
    assert score > 0
    assert any("implied-speed" in r for r in reasons)


def test_score_quiet_gap_is_low():
    score, reasons = score_gap(duration_s=300, distance_nm=0.5, implied_speed_kn=6)
    assert score < 0.2
    assert reasons == ()


def test_score_bounded():
    score, _ = score_gap(duration_s=99999, distance_nm=9999, implied_speed_kn=9999)
    assert 0.0 <= score <= 1.0


# --------------------------------------------------------------------- detect
def _track(mmsi, start, steps):
    """steps = list of (minutes_after_start, lat, lon)."""
    return [Ping(start + timedelta(minutes=m), mmsi, lat, lon) for m, lat, lon in steps]


def test_detect_finds_gap_over_threshold():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 37.0, -122.0), (300, 37.5, -122.5)])  # 5h gap
    gaps = detect_gaps(pings, min_gap_s=1800)
    assert len(gaps) == 1
    assert gaps[0].mmsi == 1
    assert gaps[0].duration_h == pytest.approx(5.0)


def test_detect_ignores_short_gaps():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 37.0, -122.0), (10, 37.0, -122.01)])  # 10 min
    assert detect_gaps(pings, min_gap_s=1800) == []


def test_detect_groups_by_vessel_and_sorts_unordered():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    a = _track(1, start, [(0, 0, 0), (300, 1, 1)])
    b = _track(2, start, [(0, 10, 10), (600, 11, 11)])
    pings = [a[1], b[1], a[0], b[0]]              # interleaved + out of order
    gaps = detect_gaps(pings, min_gap_s=1800)
    assert {g.mmsi for g in gaps} == {1, 2}


def test_detect_sorts_by_score_desc():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # vessel 1: teleport (impossible speed). vessel 2: slow drift.
    a = _track(1, start, [(0, 0, 0), (60, 5, 5)])       # huge jump in 1h
    b = _track(2, start, [(0, 0, 0), (120, 0.05, 0.05)])
    gaps = detect_gaps(pings=a + b, min_gap_s=1800)
    assert gaps[0].score >= gaps[-1].score


def test_detector_validates_params():
    with pytest.raises(ValueError):
        GapDetector(min_gap_s=0)


def test_min_score_filters():
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pings = _track(1, start, [(0, 0, 0), (35, 0, 0.001)])  # just over 30min, tiny move
    assert detect_gaps(pings, min_gap_s=1800, min_score=0.9) == []


# ----------------------------------------------------------------------- data
def test_load_pings_from_file(tmp_path):
    f = tmp_path / "track.csv"
    f.write_text("timestamp,mmsi,lat,lon\n2026-06-01T00:00:00Z,1,0,0\n"
                 "2026-06-01T05:00:00Z,1,1,1\n", encoding="utf-8")
    pings = load_pings_list(f)
    assert len(pings) == 2


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_pings_list(tmp_path / "nope.csv")


# ------------------------------------------------------------------------ cli
def test_cli_json_and_exit_code(tmp_path):
    f = tmp_path / "track.csv"
    f.write_text("2026-06-01T00:00:00Z,366,37.0,-122.0\n"
                 "2026-06-01T06:00:00Z,366,40.0,-125.0\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "aisgapwatch", str(f),
                           "--json", "--min-gap", "1800"],
                          capture_output=True, text=True)
    assert proc.returncode == 2          # a gap was found
    payload = json.loads(proc.stdout)
    assert payload["pings"] == 2
    assert payload["gaps"][0]["mmsi"] == 366


def test_cli_no_gaps_exit_zero(tmp_path):
    f = tmp_path / "track.csv"
    f.write_text("2026-06-01T00:00:00Z,366,37.0,-122.0\n"
                 "2026-06-01T00:05:00Z,366,37.0,-122.0\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "aisgapwatch", str(f)],
                          capture_output=True, text=True)
    assert proc.returncode == 0
