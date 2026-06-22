"""Parsing AIS position reports from text into :class:`~aisgapwatch.models.Ping`.

The reference format is one comma-separated record per line::

    timestamp,mmsi,lat,lon
    2026-06-01T12:00:00Z,366123456,37.8100,-122.4000

Timestamps are parsed leniently (ISO-8601, with or without a trailing ``Z``).
Blank lines and ``#`` comments are skipped. A header line (one whose second
field is not an integer) is skipped automatically, so files exported with a
header "just work". Malformed rows raise :class:`ParseError` with the line
number unless ``strict=False``, in which case they are skipped and counted.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator

from .models import Ping

__all__ = ["ParseError", "parse_line", "parse_pings", "parse_text"]


class ParseError(ValueError):
    """Raised when a record cannot be parsed (carries the 1-based line number)."""

    def __init__(self, line_no: int, message: str) -> None:
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


def _parse_timestamp(raw: str) -> datetime:
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def parse_line(line: str) -> Ping:
    """Parse a single ``timestamp,mmsi,lat,lon`` record into a :class:`Ping`."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        raise ValueError(f"expected 4 fields, got {len(parts)}")
    ts, mmsi, lat, lon = parts[0], parts[1], parts[2], parts[3]
    return Ping(timestamp=_parse_timestamp(ts), mmsi=int(mmsi),
                lat=float(lat), lon=float(lon))


def parse_pings(lines: Iterable[str], *, strict: bool = True) -> Iterator[Ping]:
    """Yield :class:`Ping` objects from an iterable of text lines.

    Skips blanks, ``#`` comments, and a leading header row. In ``strict`` mode a
    bad row raises :class:`ParseError`; otherwise bad rows are silently skipped.
    """
    seen_data = False
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # auto-skip a header ONLY as the first non-blank/non-comment row: a row
        # whose second field isn't an int there is a column header, not data.
        # Mid-file, a non-int mmsi is malformed data and must surface as an error.
        if not seen_data:
            fields = [p.strip() for p in line.split(",")]
            if len(fields) >= 2 and not _is_int(fields[1]):
                continue
        try:
            yield parse_line(line)
            seen_data = True
        except (ValueError, IndexError) as exc:
            if strict:
                raise ParseError(i, str(exc)) from exc
            continue


def parse_text(text: str, *, strict: bool = True) -> list[Ping]:
    """Convenience wrapper: parse a whole blob of text into a list of pings."""
    return list(parse_pings(text.splitlines(), strict=strict))


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False
