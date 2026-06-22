"""Loading AIS pings from files on disk.

Thin, well-behaved I/O layer over :mod:`aisgapwatch.parsers`: it opens a file
as UTF-8, streams it line-by-line (so a multi-gigabyte track export never has to
fit in memory), and yields validated :class:`~aisgapwatch.models.Ping` objects.
"""
from __future__ import annotations

import os
from typing import Iterator

from .models import Ping
from .parsers import parse_pings

__all__ = ["load_pings", "load_pings_list"]


def load_pings(path: str | os.PathLike[str], *, strict: bool = True) -> Iterator[Ping]:
    """Stream pings from a ``timestamp,mmsi,lat,lon`` file (lazy, memory-safe)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"no such AIS file: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        yield from parse_pings(fh, strict=strict)


def load_pings_list(path: str | os.PathLike[str], *, strict: bool = True) -> list[Ping]:
    """Eager variant of :func:`load_pings` returning a list."""
    return list(load_pings(path, strict=strict))
