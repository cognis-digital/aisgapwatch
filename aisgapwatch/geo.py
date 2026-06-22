"""Geodesy helpers — great-circle distance on a spherical Earth.

We use the haversine formula. It is numerically stable for the small-to-medium
separations typical between consecutive AIS pings and needs no third-party
library. Distances are returned in nautical miles because that is the unit AIS
speeds (knots = nautical miles per hour) are expressed in, which keeps the
implied-speed math in :mod:`aisgapwatch.scoring` honest.
"""
from __future__ import annotations

import math

__all__ = ["haversine_nm", "EARTH_RADIUS_NM"]

# Mean Earth radius in nautical miles (6 371.0088 km / 1.852 km per nm).
EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in nautical miles.

    >>> round(haversine_nm(0.0, 0.0, 0.0, 1.0), 1)   # 1 degree of longitude at the equator
    60.0
    """
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2)
    # clamp to guard against tiny floating-point excursions past 1.0
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_NM * c
