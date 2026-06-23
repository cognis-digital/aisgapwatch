"""Serialization seams: turn scored :class:`~aisgapwatch.models.Gap` objects into
the interchange formats analysts and downstream tooling actually consume.

Everything here is *pure* and dependency-free — each emitter takes a list of
gaps (plus a little context) and returns a string. No file I/O, no network, so
the emitters compose cleanly with the CLI, a notebook, or another service.

Formats
-------
* **NDJSON** (:func:`to_ndjson`) — one JSON object per line, the friendliest
  shape for streaming into ``jq``, Splunk, Elastic, or a log pipeline.
* **CSV** (:func:`to_csv`) — a flat spreadsheet-ready table, RFC-4180 quoted.
* **GeoJSON** (:func:`to_geojson`) — a ``FeatureCollection`` where each gap is a
  two-point ``LineString`` (last-seen -> next-seen) plus a centroid ``Point``,
  so a gap drops straight onto a map in QGIS / kepler.gl / geojson.io.
* **STIX 2.1** (:func:`to_stix`) — an ``Observed Data`` bundle of custom
  ``x-ais-gap`` observables, for feeding a CTI platform (MISP, OpenCTI). Ids are
  deterministic (derived from gap content) so re-emitting the same gap is stable.

None of these formats invents data: every field is computed from the input
position reports. There are no fabricated identifiers, vessels, or intel.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Iterable, Sequence

from .models import Gap

__all__ = [
    "to_ndjson",
    "to_csv",
    "to_geojson",
    "to_stix",
    "FORMATS",
]

#: Output formats the CLI knows how to render (besides the human table).
FORMATS = ("json", "ndjson", "csv", "geojson", "stix")


# --------------------------------------------------------------------- NDJSON
def to_ndjson(gaps: Iterable[Gap]) -> str:
    """One compact JSON object per line (newline-delimited JSON)."""
    return "\n".join(json.dumps(g.to_dict(), separators=(",", ":")) for g in gaps)


# ------------------------------------------------------------------------ CSV
_CSV_FIELDS = (
    "mmsi", "start", "end", "duration_h", "distance_nm",
    "implied_speed_kn", "score", "reasons",
    "start_lat", "start_lon", "end_lat", "end_lon",
)


def to_csv(gaps: Iterable[Gap]) -> str:
    """A flat, spreadsheet-ready CSV with a header row (RFC-4180 quoting)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_CSV_FIELDS)
    for g in gaps:
        w.writerow([
            g.mmsi,
            g.start.isoformat(),
            g.end.isoformat(),
            round(g.duration_h, 3),
            g.distance_nm,
            g.implied_speed_kn,
            g.score,
            "|".join(g.reasons),
            g.start_pos[0], g.start_pos[1],
            g.end_pos[0], g.end_pos[1],
        ])
    return buf.getvalue().rstrip("\n")


# -------------------------------------------------------------------- GeoJSON
def _gap_centroid(g: Gap) -> tuple[float, float]:
    """Midpoint of the two endpoints (lon, lat) — good enough for a map marker."""
    lat = (g.start_pos[0] + g.end_pos[0]) / 2.0
    lon = (g.start_pos[1] + g.end_pos[1]) / 2.0
    return lon, lat


def _gap_properties(g: Gap) -> dict:
    return {
        "mmsi": g.mmsi,
        "start": g.start.isoformat(),
        "end": g.end.isoformat(),
        "duration_h": round(g.duration_h, 3),
        "distance_nm": g.distance_nm,
        "implied_speed_kn": g.implied_speed_kn,
        "score": g.score,
        "reasons": list(g.reasons),
    }


def to_geojson(gaps: Iterable[Gap]) -> str:
    """A GeoJSON ``FeatureCollection``: per gap a reappearance ``LineString`` and
    a centroid ``Point`` (GeoJSON order is ``[lon, lat]``)."""
    features = []
    for g in gaps:
        props = _gap_properties(g)
        # LineString from last-seen -> next-seen (the silent leg).
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [g.start_pos[1], g.start_pos[0]],
                    [g.end_pos[1], g.end_pos[0]],
                ],
            },
            "properties": {**props, "feature": "gap-leg"},
        })
        lon, lat = _gap_centroid(g)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {**props, "feature": "gap-centroid"},
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, indent=2)


# --------------------------------------------------------------------- STIX 2.1
_STIX_NAMESPACE = "aisgapwatch"


def _deterministic_id(prefix: str, *parts: object) -> str:
    """A stable STIX-style id: ``prefix--<sha1-derived-uuid>`` from content.

    Re-emitting the same gap yields the same id, so a CTI platform de-duplicates
    cleanly instead of accumulating copies on every run.
    """
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    h = hashlib.sha1(raw).hexdigest()  # noqa: S324 - id derivation, not security
    # Shape the hex into a UUID-like 8-4-4-4-12 string.
    u = f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return f"{prefix}--{u}"


def to_stix(gaps: Sequence[Gap], *, source: str | None = None) -> str:
    """A STIX 2.1 ``bundle`` of ``observed-data`` objects wrapping custom
    ``x-ais-gap`` observables. Deterministic ids; no fabricated intel."""
    objects: list[dict] = []
    for g in gaps:
        obs_id = _deterministic_id(
            "x-ais-gap", g.mmsi, g.start.isoformat(), g.end.isoformat(),
            g.distance_nm, g.implied_speed_kn,
        )
        observable = {
            "type": "x-ais-gap",
            "id": obs_id,
            "mmsi": g.mmsi,
            "start": g.start.isoformat(),
            "end": g.end.isoformat(),
            "duration_h": round(g.duration_h, 3),
            "distance_nm": g.distance_nm,
            "implied_speed_kn": g.implied_speed_kn,
            "score": g.score,
            "reasons": list(g.reasons),
            "start_lat": g.start_pos[0],
            "start_lon": g.start_pos[1],
            "end_lat": g.end_pos[0],
            "end_lon": g.end_pos[1],
        }
        od_id = _deterministic_id("observed-data", obs_id)
        observed = {
            "type": "observed-data",
            "spec_version": "2.1",
            "id": od_id,
            "created": g.start.isoformat(),
            "modified": g.end.isoformat(),
            "first_observed": g.start.isoformat(),
            "last_observed": g.end.isoformat(),
            "number_observed": 1,
            "objects": {"0": observable},
        }
        if source:
            observed["x_aisgapwatch_source"] = source
        objects.append(observed)
    bundle = {
        "type": "bundle",
        "id": _deterministic_id("bundle", *(o["id"] for o in objects)),
        "objects": objects,
    }
    return json.dumps(bundle, indent=2)
