"""End-to-end CLI tests for the new output formats and flags. All offline:
the CLI only ever reads the tmp file or stdin and writes stdout.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aisgapwatch.cli import build_parser, main


SAMPLE = (
    "timestamp,mmsi,lat,lon\n"
    "2026-06-01T00:00:00Z,366123456,37.80,-122.40\n"
    "2026-06-01T07:10:00Z,366123456,39.90,-124.80\n"
    "2026-06-01T00:00:00Z,477888999,33.70,-118.20\n"
    "2026-06-01T02:15:00Z,477888999,33.71,-118.22\n"
)


def _write(tmp_path, text=SAMPLE):
    f = tmp_path / "track.csv"
    f.write_text(text, encoding="utf-8")
    return f


# --------------------------------------------------------------------- parser
def test_parser_defaults():
    args = build_parser().parse_args(["x.csv"])
    assert args.min_gap == 1800.0
    assert args.format is None
    assert not args.json


def test_parser_format_choices():
    for fmt in ("json", "ndjson", "csv", "geojson", "stix"):
        args = build_parser().parse_args(["x.csv", "--format", fmt])
        assert args.format == fmt


def test_parser_rejects_bad_format():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["x.csv", "--format", "yaml"])


# ------------------------------------------------------------ in-process main
def test_main_table_returns_two_on_gap(tmp_path, capsys):
    rc = main([str(_write(tmp_path)), "--min-gap", "1800"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "gap(s)" in out


def test_main_json_compat_flag(tmp_path, capsys):
    rc = main([str(_write(tmp_path)), "--json"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["pings"] == 4
    assert len(payload["gaps"]) >= 1


def test_main_format_json_equivalent(tmp_path, capsys):
    main([str(_write(tmp_path)), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert "gaps" in payload


def test_main_ndjson(tmp_path, capsys):
    main([str(_write(tmp_path)), "--format", "ndjson"])
    out = capsys.readouterr().out.strip()
    for line in out.splitlines():
        json.loads(line)  # each line valid JSON


def test_main_csv(tmp_path, capsys):
    main([str(_write(tmp_path)), "--format", "csv"])
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("mmsi")


def test_main_geojson(tmp_path, capsys):
    main([str(_write(tmp_path)), "--format", "geojson"])
    fc = json.loads(capsys.readouterr().out)
    assert fc["type"] == "FeatureCollection"


def test_main_stix(tmp_path, capsys):
    main([str(_write(tmp_path)), "--format", "stix"])
    bundle = json.loads(capsys.readouterr().out)
    assert bundle["type"] == "bundle"


def test_main_stats_table(tmp_path, capsys):
    main([str(_write(tmp_path)), "--stats"])
    out = capsys.readouterr().out
    assert "feed-quality stats" in out


def test_main_context_table(tmp_path, capsys):
    main([str(_write(tmp_path)), "--context"])
    out = capsys.readouterr().out
    assert "bearing" in out and "plaus" in out


def test_main_top_limits(tmp_path, capsys):
    main([str(_write(tmp_path)), "--json", "--top", "1"])
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["gaps"]) == 1


def test_main_min_score_filters_all(tmp_path, capsys):
    rc = main([str(_write(tmp_path)), "--min-score", "0.99"])
    assert rc == 0


def test_main_stdin(tmp_path, capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(SAMPLE))
    rc = main(["-", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["pings"] == 4
    assert rc == 2


# ---------------------------------------------------------------- subprocess
def test_subprocess_version():
    proc = subprocess.run([sys.executable, "-m", "aisgapwatch", "--version"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "aisgapwatch" in proc.stdout


def test_subprocess_geojson_exit_code(tmp_path):
    f = _write(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "aisgapwatch", str(f), "--format", "geojson"],
        capture_output=True, text=True)
    assert proc.returncode == 2
    json.loads(proc.stdout)
