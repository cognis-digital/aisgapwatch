"""Tests for the serialization emitters (NDJSON / CSV / GeoJSON / STIX).

All offline, stdlib-only. No fabricated data: every assertion is derived from
the gaps we construct here.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest

from aisgapwatch import (Gap, detect_gaps, parse_text, to_csv, to_geojson,
                         to_ndjson, to_stix)
from aisgapwatch.emit import FORMATS, _deterministic_id


def _sample_gaps():
    text = (
        "timestamp,mmsi,lat,lon\n"
        "2026-06-01T00:00:00Z,366123456,37.80,-122.40\n"
        "2026-06-01T07:10:00Z,366123456,39.90,-124.80\n"
        "2026-06-01T00:00:00Z,477888999,33.70,-118.20\n"
        "2026-06-01T02:15:00Z,477888999,33.71,-118.22\n"
    )
    return detect_gaps(parse_text(text), min_gap_s=1800)


# --------------------------------------------------------------------- FORMATS
def test_formats_tuple_contents():
    assert set(FORMATS) == {"json", "ndjson", "csv", "geojson", "stix"}


# --------------------------------------------------------------------- NDJSON
def test_ndjson_one_object_per_line():
    gaps = _sample_gaps()
    out = to_ndjson(gaps)
    lines = out.splitlines()
    assert len(lines) == len(gaps)
    for line in lines:
        obj = json.loads(line)
        assert "mmsi" in obj and "score" in obj


def test_ndjson_empty_is_empty_string():
    assert to_ndjson([]) == ""


def test_ndjson_is_compact():
    gaps = _sample_gaps()
    out = to_ndjson(gaps)
    # compact separators -> no ", " spacing
    assert ", " not in out
    assert ": " not in out


# ------------------------------------------------------------------------ CSV
def test_csv_has_header_and_rows():
    gaps = _sample_gaps()
    out = to_csv(gaps)
    reader = list(csv.reader(io.StringIO(out)))
    assert reader[0][0] == "mmsi"
    assert "reasons" in reader[0]
    assert len(reader) == len(gaps) + 1


def test_csv_roundtrip_values():
    gaps = _sample_gaps()
    out = to_csv(gaps)
    rows = list(csv.DictReader(io.StringIO(out)))
    mmsis = {int(r["mmsi"]) for r in rows}
    assert {366123456, 477888999} <= mmsis or mmsis == {366123456, 477888999}
    for r in rows:
        assert 0.0 <= float(r["score"]) <= 1.0
        assert float(r["start_lat"]) == pytest.approx(
            float(r["start_lat"]))  # parses


def test_csv_reasons_pipe_joined():
    gaps = _sample_gaps()
    worst = max(gaps, key=lambda g: g.score)
    out = to_csv([worst])
    row = list(csv.DictReader(io.StringIO(out)))[0]
    if worst.reasons:
        assert "|".join(worst.reasons) == row["reasons"]


def test_csv_empty_just_header():
    out = to_csv([])
    assert out.startswith("mmsi")
    assert len(out.splitlines()) == 1


# -------------------------------------------------------------------- GeoJSON
def test_geojson_is_feature_collection():
    gaps = _sample_gaps()
    fc = json.loads(to_geojson(gaps))
    assert fc["type"] == "FeatureCollection"
    # two features (line + centroid) per gap
    assert len(fc["features"]) == 2 * len(gaps)


def test_geojson_linestring_and_point_present():
    gaps = _sample_gaps()
    fc = json.loads(to_geojson(gaps))
    geom_types = {f["geometry"]["type"] for f in fc["features"]}
    assert geom_types == {"LineString", "Point"}


def test_geojson_coordinate_order_is_lon_lat():
    # single gap with known coords
    g = Gap(1, datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 5, tzinfo=timezone.utc), 18000, 100, 20,
            (37.0, -122.0), (39.0, -124.0), 0.5, ("long-duration-5.0h",))
    fc = json.loads(to_geojson([g]))
    line = next(f for f in fc["features"]
                if f["geometry"]["type"] == "LineString")
    coords = line["geometry"]["coordinates"]
    # GeoJSON order is [lon, lat]; start lon is -122.0
    assert coords[0] == [-122.0, 37.0]
    assert coords[1] == [-124.0, 39.0]


def test_geojson_centroid_is_midpoint():
    g = Gap(1, datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 5, tzinfo=timezone.utc), 18000, 100, 20,
            (10.0, 20.0), (30.0, 40.0), 0.5, ())
    fc = json.loads(to_geojson([g]))
    pt = next(f for f in fc["features"] if f["geometry"]["type"] == "Point")
    lon, lat = pt["geometry"]["coordinates"]
    assert lat == pytest.approx(20.0)   # (10+30)/2
    assert lon == pytest.approx(30.0)   # (20+40)/2


def test_geojson_properties_carry_score():
    gaps = _sample_gaps()
    fc = json.loads(to_geojson(gaps))
    for f in fc["features"]:
        assert "score" in f["properties"]
        assert "mmsi" in f["properties"]
        assert f["properties"]["feature"] in {"gap-leg", "gap-centroid"}


def test_geojson_empty_collection():
    fc = json.loads(to_geojson([]))
    assert fc["features"] == []


# --------------------------------------------------------------------- STIX
def test_stix_is_bundle():
    gaps = _sample_gaps()
    bundle = json.loads(to_stix(gaps))
    assert bundle["type"] == "bundle"
    assert bundle["id"].startswith("bundle--")
    assert len(bundle["objects"]) == len(gaps)


def test_stix_observed_data_shape():
    gaps = _sample_gaps()
    bundle = json.loads(to_stix(gaps))
    od = bundle["objects"][0]
    assert od["type"] == "observed-data"
    assert od["spec_version"] == "2.1"
    assert od["number_observed"] == 1
    obs = od["objects"]["0"]
    assert obs["type"] == "x-ais-gap"
    assert "mmsi" in obs and "score" in obs


def test_stix_ids_are_deterministic():
    gaps = _sample_gaps()
    a = json.loads(to_stix(gaps))
    b = json.loads(to_stix(gaps))
    ids_a = [o["id"] for o in a["objects"]]
    ids_b = [o["id"] for o in b["objects"]]
    assert ids_a == ids_b
    assert a["id"] == b["id"]


def test_stix_source_annotation():
    gaps = _sample_gaps()
    bundle = json.loads(to_stix(gaps, source="track.csv"))
    assert bundle["objects"][0]["x_aisgapwatch_source"] == "track.csv"


def test_stix_empty_bundle():
    bundle = json.loads(to_stix([]))
    assert bundle["type"] == "bundle"
    assert bundle["objects"] == []


def test_deterministic_id_shape():
    i = _deterministic_id("x-ais-gap", 1, "a", "b")
    assert i.startswith("x-ais-gap--")
    uuid_part = i.split("--", 1)[1]
    chunks = uuid_part.split("-")
    assert [len(c) for c in chunks] == [8, 4, 4, 4, 12]


def test_deterministic_id_changes_with_content():
    a = _deterministic_id("p", 1, 2)
    b = _deterministic_id("p", 1, 3)
    assert a != b
