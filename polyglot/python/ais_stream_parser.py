"""
polyglot/python/ais_stream_parser.py

AIS Stream Parser for Maritime Situational Awareness / Defensive OSINT

Parses NMEA 0183 and AIS binary streams, extracts vessel tracks, detects
transponder-gap anomalies (sudden position jumps, speed spikes, course whiplash).

Usage:
    python -m polyglot.python.ais_stream_parser <input.nmea> [--binary]
"""

import argparse
import csv
import io
import math
import os
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Iterator, Optional, TextIO

# =============================================================================
# CONSTANTS & CONFIGURATION
# =============================================================================

NMEA_SENTENCE_LENGTH_LIMIT = 256
DEFAULT_BUFFER_SIZE = 4096
MIN_TRACK_POINTS_FOR_GAP = 3
GAP_THRESHOLD_METERS = 100.0
SPEED_SPIKE_MULTIPLIER = 3.0
COURSE_WHIPLASH_DEGREES = 45.0

# NMEA Sentence Types (ISO 13628-5 / IEC 61993-2)
NMEA_SENTENCE_TYPES: dict[str, str] = {
    "GGA": "Global Positioning System Fix Data",
    "RMB": "Recommended Minimum Navigation Information",
    "VTG": "Course over Ground (COG)",
    "VWR": "Water Speed Over Ground",
    "HDM": "Heading Magnetic",
    "HDA": "Heading True",
    "HDG": "True Heading",
    "XTE": "Cross Track Error",
    "RMA": "Recommended Minimum Navigation Information - Aided",
    "RMC": "Recommended Minimum Navigation Information (Position)",
    "ZDA": "Zulu Time and Date",
    "VDM": "Class B Data Message",
    "VDO": "Class B Data Message - Outbound",
    "VDN": "Class B Data Message - NMEA 0183 Format",
}

# AIS Binary Frame Types (ISO 19765)
AIS_FRAME_TYPES: dict[int, str] = {
    1: "Position Report (SOTDMA)",
    2: "Position Report (SOTDMA/ITDMA)",
    3: "Position Report (ITDMA)",
    4: "SAR Aircraft Position",
    5: "Static Data & Voyage Data",
    6: "Dynamic Data",
    7: "Rapid Update",
    8: "Extended Class B",
    9: "Class A ARQ/FR",
    10: "Position Report (ARQ)",
    11: "Static & Voyage Data (ARQ)",
    12: "Dynamic Data (ARQ)",
    13: "Rapid Update (ARQ)",
    14: "Extended Class B (ARQ)",
    15: "Position Report (FR)",
    16: "Static & Voyage Data (FR)",
    17: "Dynamic Data (FR)",
    18: "Rapid Update (FR)",
    19: "Extended Class B (FR)",
}

# =============================================================================
# DATA MODELS
# =============================================================================

class NMEASentenceType(Enum):
    GGA = "GGA"
    RMB = "RMB"
    VTG = "VTG"
    VWR = "VWR"
    HDM = "HDM"
    HDA = "HDA"
    HDG = "HDG"
    XTE = "XTE"
    RMA = "RMA"
    RMC = "RMC"
    ZDA = "ZDA"
    VDM = "VDM"
    VDO = "VDO"
    VDN = "VDN"

@dataclass(frozen=True)
class NMEASentence:
    """Raw parsed NMEA sentence."""
    raw: str
    type: NMEASentenceType
    checksum_valid: bool
    fields: list[str]  # Original field values (before splitting)
    
    @classmethod
    def parse(cls, raw: str) -> Optional["NMEASentence"]:
        """Parse a single NMEA sentence."""
        if not raw or len(raw) > NMEA_SENTENCE_LENGTH_LIMIT:
            return None
        
        fields = raw.split(",")
        
        # Validate checksum (last 2 chars must be *XX)
        if len(fields) < 3 or not fields[-1].startswith("*"):
            return None
        
        try:
            checksum_chars = fields[-1][1:]
            expected_checksum = sum(ord(c) for c in raw[:-3]) & 0xFF
            actual_checksum = int(checksum_chars, 16)
            checksum_valid = (expected_checksum == actual_checksum)
        except ValueError:
            return None
        
        # Extract sentence type
        if len(fields) < 2:
            return None
        
        try:
            type_val = fields[0].upper()
            type_enum = NMEASentenceType(type_val)
        except (ValueError, KeyError):
            type_enum = NMEASentenceType.GGA  # Default fallback
            
        return cls(raw=raw, type=type_enum, checksum_valid=checksum_valid, fields=fields)

@dataclass(frozen=True)
class VesselPosition:
    """Single vessel position fix."""
    timestamp: datetime
    lat: float  # Degrees N/S (positive = North)
    lon: float  # Degrees E/W (positive = East)
    speed_over_ground: Optional[float] = None  # Knots
    course_over_ground: Optional[float] = None  # Degrees True
    true_heading: Optional[float] = None  # Degrees True
    mag_heading: Optional[float] = None  # Degrees Magnetic
    water_speed: Optional[float] = None  # Knots
    cross_track_error: Optional[float] = None  # Meters (signed)
    quality_flags: int = 0  # GGA quality indicator
    
    def __post_init__(self):
        if self.quality_flags & 1:
            self.lat = -self.lat  # Negative = South
        
        if self.quality_flags & 2:
            self.lon = -self.lon  # Negative = West

@dataclass(frozen=True)
class AISMessage:
    """Parsed AIS binary message."""
    frame_type: int
    mmsi: int
    timestamp: datetime
    lat: float
    lon: float
    sog: Optional[float] = None  # Speed over ground (knots)
    cog: Optional[float] = None   # Course over ground (degrees)
    hdg: Optional[float] = None   # Heading (degrees)
    raim: bool = False           # Receiver in autonomous mode
    raim_quality: int = 0        # RAIM quality indicator
    
@dataclass(frozen=True)
class TrackPoint:
    """Aggregated track point with derived metrics."""
    vessel_id: str
    timestamp: datetime
    lat: float
    lon: float
    sog: Optional[float] = None
    cog: Optional[float] = None
    hdg: Optional[float] = None
    
    # Derived anomaly scores (0.0 - 1.0, higher = worse)
    gap_score: float = 0.0
    speed_anomaly_score: float = 0.0
    course_whiplash_score: float = 0.0
    total_anomaly_score: float = 0.0
    
    # Previous values for comparison (set during stream processing)
    prev_lat: Optional[float] = None
    prev_lon: Optional[float] = None
    prev_sog: Optional[float] = None
    prev_cog: Optional[float] = None
    prev_hdg: Optional[float] = None
    
    @property
    def is_anomalous(self) -> bool:
        return self.total_anomaly_score > 0.5

@dataclass(frozen=True)
class StreamState:
    """Per-vessel state maintained during stream processing."""
    vessel_id: str
    points: Deque[TrackPoint] = field(default_factory=deque)
    
    # Running statistics for anomaly detection
    avg_sog: float = 0.0
    sog_stddev: float = 0.0
    last_gap_score: float = 0.0
    
    @property
    def count(self) -> int:
        return len(self.points)

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def degrees_to_radians(degrees: float) -> float:
    """Convert degrees to radians."""
    return math.radians(degrees)

def radians_to_degrees(radians: float) -> float:
    """Convert radians to degrees."""
    return math.degrees(radians)

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points in meters.
    
    Args:
        lat1, lon1: First point (degrees)
        lat2, lon2: Second point (degrees)
        
    Returns:
        Distance in nautical miles (multiply by 1852 for meters)
    """
    R = 3440.065  # Earth radius in nautical miles
    
    dlat = degrees_to_radians(lat2 - lat1)
    dlon = degrees_to_radians(lon2 - lon1)
    
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(degrees_to_radians(lat1)) * math.cos(degrees_to_radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

def calculate_track_speed(lat1: float, lon1: float, 
                         lat2: float, lon2: float,
                         time_diff_seconds: float) -> Optional[float]:
    """Calculate speed over ground from two track points.
    
    Args:
        (lat1, lon1), (lat2, lon2): Two consecutive positions (degrees)
        time_diff_seconds: Time difference between fixes
        
    Returns:
        Speed in knots, or None if time_diff is zero or negative
    """
    if time_diff_seconds <= 0:
        return None
    
    distance_nm = haversine_distance(lat1, lon1, lat2, lon2)
    speed_knots = (distance_nm / time_diff_seconds) * 60.0
    return max(0.0, speed_knots)

def calculate_course_change(cog1: Optional[float], cog2: Optional[float]) -> float:
    """Calculate absolute course change between two fixes.
    
    Args:
        cog1, cog2: Course over ground (degrees True), or None
        
    Returns:
        Absolute course change in degrees [0, 360)
    """
    if cog1 is None or cog2 is None:
        return 0.0
    
    diff = abs(cog2 - cog1) % 360
    return min(diff, 360 - diff)

def calculate_heading_change(hdg1: Optional[float], hdg2: Optional[float]) -> float:
    """Calculate absolute heading change between two fixes."""
    if hdg1 is None or hdg2 is None:
        return 0.0
    
    diff = abs(hdg2 - hdg1) % 360
    return min(diff, 360 - diff)

def calculate_anomaly_score(base_score: float, 
                           multiplier: float,
                           threshold: float) -> float:
    """Calculate anomaly score with saturation at 1.0."""
    raw = base_score * multiplier
    return min(1.0, max(0.0, raw))

# =============================================================================
# NMEA STREAM PARSER
# =============================================================================

class NMESentenceParser:
    """Parse and buffer NMEA sentences from a stream."""
    
    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE):
        self.buffer_size = buffer_size
        self._buffer: Deque[str] = deque(maxlen=buffer_size)
        
    def feed(self, data: str) -> Iterator[NMEASentence]:
        """Feed raw NMEA data and yield parsed sentences."""
        # Split into individual sentences (handle multi-line records)
        lines = data.strip().split("\n")
        
        for line in lines:
            line = line.strip()
            if not line or line[0] == "!":  # Skip empty/comment lines
                continue
                
            sentence = NMEASentence.parse(line)
            if sentence:
                self._buffer.append(sentence)
                yield sentence
    
    def flush(self) -> Iterator[NMEASentence]:
        """Yield any remaining buffered sentences."""
        for sentence in self._buffer:
            yield sentence
        self._buffer.clear()

# =============================================================================
# AIS BINARY PARSER (ISO 19765 / IEC 61993-2)
# =============================================================================

class AISBinaryParser:
    """Parse raw AIS binary frames from NMEA VDM/VDO/VDN sentences."""
    
    # AIS Frame Header Format (bits):
    #   F0-F1: Frame Type (4 bits, 0x00-0x0F)
    #   F2: RAIM flag (1 bit)
    #   F3: Reserved (1 bit)
    #   F4-F5: Message ID (2 bits)
    
    @classmethod
    def parse_vdm(cls, vdm: str) -> Optional[AISMessage]:
        """Parse a VDM (Class A) sentence.
        
        VDM format: <FrameType><RAIMFlag><MsgID><Data>
        Example: 1023456789ABCDEF...
        """
        if not vdm or len(vdm) < 4:
            return None
        
        try:
            # Extract header fields
            frame_type = int(vdm[0]) & 0x0F
            raim_flag = (int(vdm[1]) >> 6) & 0x01
            msg_id = (int(vdm[2]) >> 4) & 0x03
            
            # Validate frame type
            if frame_type not in AIS_FRAME_TYPES:
                return None
            
            # Parse message data (simplified - full parsing requires ISO 19765 spec)
            # For demo purposes, extract basic fields from hex payload
            payload = vdm[4:]
            
            # Decode latitude and longitude (20 bits each, little-endian)
            lat_bits = int(payload[:4], 16) if len(payload) >= 4 else 0
            lon_bits = int(payload[4:8], 16) if len(payload) >= 8 else 0
            
            # Convert to degrees (20-bit fixed point, 1/100000 scale)
            lat_degrees = (lat_bits / 100000.0) * 60.0 - 90.0 if lat_bits > 3145728 else (lat_bits / 100000.0) * 60.0 - 90.0
            lon_degrees = (lon_bits / 100000.0) * 60.0 - 180.0 if lon_bits > 3145728 else (lon_bits / 100000.0) * 60.0 - 180.0
            
            # Adjust for hemisphere
            if lat_degrees < 0:
                lat_degrees = 360.0 + lat_degrees
            
            return AISMessage(
                frame_type=frame_type,
                mmsi=int(vdm[2]),
                timestamp=datetime.now(timezone.utc),
                lat=lat_degrees,
                lon=lon_degrees,
                raim=bool(raim_flag)
            )
            
        except (ValueError, IndexError):
            return None

# =============================================================================
# TRACK PROCESSOR & ANOMALY DETECTOR
# =============================================================================

class TrackProcessor:
    """Process vessel tracks and detect anomalies."""
    
    def __init__(self, 
                 gap_threshold_meters: float = GAP_THRESHOLD_METERS,
                 speed_spike_multiplier: float = SPEED_SPIKE_MULTIPLIER,
                 course_whiplash_degrees: float = COURSE_WHIPLASH_DEGREES):
        self.gap_threshold_meters = gap_threshold_meters
        self.speed_spike_multiplier = speed_spike_multiplier
        self.course_whiplash_degrees = course_whiplash_degrees
        
    def process_point(self, point: TrackPoint, 
                      state: StreamState) -> tuple[TrackPoint, StreamState]:
        """Process a single track point and update anomaly scores."""
        
        # Calculate gap score (position jump anomaly)
        if len(state.points) >= MIN_TRACK_POINTS_FOR_GAP:
            prev = state.points[-1]
            
            distance_nm = haversine_distance(
                prev.lat, prev.lon, 
                point.lat, point.lon
            )
            distance_meters = distance_nm * 1852.0
            
            if distance_meters > self.gap_threshold_meters:
                # Exponential decay with gap size
                raw_gap_score = min(1.0, (distance_meters