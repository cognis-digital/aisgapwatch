"""
polyglot/python/track_continuity_checker.py

AIS Transponder-Gap Continuity Checker
======================================

Detects anomalies in AIS transponder reporting continuity. Identifies:
  - Time gaps (long pauses between reports)
  - Position jumps (unusual distance changes)
  - Velocity spikes (impossible speeds)

Scoring combines severity of each anomaly type into a single risk score.

Usage:
    from track_continuity_checker import ContinuityChecker
    
    checker = ContinuityChecker(
        time_gap_threshold=60,      # seconds before flagging
        pos_jump_threshold=500,     # meters for position jump
        velocity_max=45             # knots max reasonable speed
    )
    
    results = checker.check_tracks(tracks)
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Iterator, Dict, Any
from enum import Enum


class GapType(Enum):
    """Types of continuity anomalies detected."""
    TIME_GAP = "time_gap"           # Long pause in reporting
    POSITION_JUMP = "position_jump" # Unusual distance change
    VELOCITY_SPIKE = "velocity_spike"  # Impossible speed


@dataclass
class PositionReport:
    """Single AIS position report entry."""
    timestamp: datetime
    lat: float
    lon: float
    speed_over_ground: Optional[float] = None  # knots, optional
    course_over_ground: Optional[float] = None  # degrees, optional
    
    def to_tuple(self) -> Tuple[datetime, float, float]:
        return (self.timestamp, self.lat, self.lon)


@dataclass
class VesselTrack:
    """Container for a vessel's complete track history."""
    mmsi: str
    reports: List[PositionReport] = field(default_factory=list)
    
    def append(self, report: PositionReport):
        self.reports.append(report)
    
    @property
    def start_time(self) -> Optional[datetime]:
        return self.reports[0].timestamp if self.reports else None
    
    @property
    def end_time(self) -> Optional[datetime]:
        return self.reports[-1].timestamp if self.reports else None
    
    @property
    def duration_seconds(self) -> float:
        start = self.start_time
        end = self.end_time
        if start and end:
            return (end - start).total_seconds()
        return 0.0


@dataclass
class GapAnomaly:
    """Single detected anomaly event."""
    gap_type: GapType
    vessel_mmsi: str
    timestamp: datetime  # When the gap was observed
    severity_score: float  # 0-100, higher = worse
    
    def __str__(self) -> str:
        return f"{self.gap_type.value}: {self.severity_score:.1f}/100"


@dataclass
class TrackAnalysisResult:
    """Complete analysis result for one vessel."""
    mmsi: str
    total_reports: int
    duration_seconds: float
    avg_report_interval: float  # seconds between reports
    
    # Anomaly counts and scores
    time_gaps: List[GapAnomaly] = field(default_factory=list)
    position_jumps: List[GapAnomaly] = field(default_factory=list)
    velocity_spikes: List[GapAnomaly] = field(default_factory=list)
    
    # Aggregated metrics
    max_severity_score: float = 0.0
    total_anomalies: int = 0
    
    @property
    def overall_risk_score(self) -> float:
        """Composite risk score (0-100)."""
        if not self.time_gaps and not self.position_jumps and not self.velocity_spikes:
            return 0.0
        
        # Weighted average of max severities
        scores = [g.severity_score for g in self.time_gaps] + \
                 [g.severity_score for g in self.position_jumps] + \
                 [g.severity_score for g in self.velocity_spikes]
        
        if not scores:
            return 0.0
        
        # Normalize to 0-100 range, with more weight on recent issues
        max_possible = len(scores) * 100
        normalized = sum(scores) / max_possible * 100
        
        # Apply decay factor for older anomalies (less concerning)
        if self.duration_seconds > 0:
            age_factor = min(1.0, 3600 / (self.duration_seconds + 1))  # Max effect at 1 hour
            return normalized * age_factor
        
        return normalized


class ContinuityChecker:
    """
    Main engine for detecting AIS continuity anomalies.
    
    Configurable thresholds allow tuning for different operational contexts.
    """
    
    DEFAULT_CONFIG = {
        'time_gap_threshold': 60,      # seconds - flag if > this
        'position_jump_threshold': 500, # meters - flag if > this
        'velocity_max_knots': 45,       # max reasonable speed
        'velocity_spike_multiplier': 3.0,  # multiplier for spike detection
        'report_interval_normal': 120,   # expected normal interval (seconds)
        'report_interval_stddev': 60,    # acceptable variation
        'time_weight': 0.4,             # weight in severity calculation
        'pos_weight': 0.35,
        'vel_weight': 0.25,
    }
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        
        # Pre-compute derived thresholds
        self.pos_threshold_meters = self.config['position_jump_threshold']
        self.vel_max_knots = self.config['velocity_max_knots']
        self.vel_spike_threshold = self.config['velocity_max_knots'] * \
                                  self.config['velocity_spike_multiplier']
        
    def check_tracks(self, tracks: List[VesselTrack]) -> List[TrackAnalysisResult]:
        """
        Analyze multiple vessel tracks and return results.
        
        Args:
            tracks: List of VesselTrack objects to analyze
            
        Returns:
            List of TrackAnalysisResult for each track
        """
        results = []
        
        for track in tracks:
            result = self._analyze_single_track(track)
            results.append(result)
        
        return results
    
    def check_stream(self, 
                     iterator: Iterator[PositionReport],
                     batch_size: int = 1000) -> Iterator[Tuple[VesselTrack, TrackAnalysisResult]]:
        """
        Process a stream of reports in batches for memory efficiency.
        
        Args:
            iterator: Stream of PositionReport objects
            batch_size: Number of reports to buffer before processing
            
        Yields:
            Tuple of (VesselTrack, TrackAnalysisResult) when batch is complete
        """
        current_track = None
        batch: List[PositionReport] = []
        
        for report in iterator:
            # Initialize track on first report
            if not current_track:
                current_track = VesselTrack(mmsi=report.lat, reports=[report])
            
            current_track.append(report)
            batch.append(report)
            
            if len(batch) >= batch_size:
                result = self._analyze_single_track(current_track)
                yield (current_track, result)
                batch.clear()
        
        # Handle remaining items in buffer
        if batch:
            current_track.reports.extend(batch)
            result = self._analyze_single_track(current_track)
            yield (current_track, result)
    
    def _analyze_single_track(self, track: VesselTrack) -> TrackAnalysisResult:
        """Analyze a single vessel's complete track."""
        if not track.reports:
            return TrackAnalysisResult(mmsi=track.mmsi, total_reports=0, 
                                       duration_seconds=0.0)
        
        # Initialize result with basic stats
        result = TrackAnalysisResult(
            mmsi=track.mmsi,
            total_reports=len(track.reports),
            duration_seconds=track.duration_seconds,
            avg_report_interval=track.duration_seconds / max(len(track.reports) - 1, 1)
        )
        
        # Analyze time gaps
        result.time_gaps = self._detect_time_gaps(track)
        
        # Analyze position jumps
        result.position_jumps = self._detect_position_jumps(track)
        
        # Analyze velocity spikes
        result.velocity_spikes = self._detect_velocity_spikes(track)
        
        # Calculate aggregated metrics
        result.max_severity_score = max(
            (g.severity_score for g in result.time_gaps), default=0.0,
            (g.severity_score for g in result.position_jumps), default=0.0,
            (g.severity_score for g in result.velocity_spikes), default=0.0,
        )
        
        result.total_anomalies = len(result.time_gaps) + \
                                len(result.position_jumps) + \
                                len(result.velocity_spikes)
        
        return result
    
    def _detect_time_gaps(self, track: VesselTrack) -> List[GapAnomaly]:
        """Detect gaps in reporting time sequence."""
        gaps = []
        prev_report = None
        
        for report in track.reports:
            if prev_report is None:
                prev_report = report
                continue
            
            delta = (report.timestamp - prev_report.timestamp).total_seconds()
            
            if delta > self.config['time_gap_threshold']:
                # Calculate severity based on how far over threshold
                excess_ratio = delta / self.config['time_gap_threshold']
                severity = min(100.0, 50 + (excess_ratio - 1) * 30)
                
                gaps.append(GapAnomaly(
                    gap_type=GapType.TIME_GAP,
                    vessel_mmsi=track.mmsi,
                    timestamp=report.timestamp,
                    severity_score=severity
                ))
            
            prev_report = report
        
        return gaps
    
    def _detect_position_jumps(self, track: VesselTrack) -> List[GapAnomaly]:
        """Detect unusual position changes between consecutive reports."""
        jumps = []
        
        for i in range(1, len(track.reports)):
            prev = track.reports[i - 1]
            curr = track.reports[i]
            
            # Calculate distance (Haversine approximation)
            delta_lat = math.radians(curr.lat - prev.lat)
            delta_lon = math.radians(curr.lon - prev.lon)
            
            a = (math.sin(delta_lat/2)**2 + 
                 math.cos(math.radians(prev.lat)) * math.cos(math.radians(curr.lat)) *
                 math.sin(delta_lon/2)**2)
            
            c = 2 * math.asin(math.sqrt(a))
            distance_meters = 6371000 * c
            
            if distance_meters > self.pos_threshold_meters:
                # Severity based on jump size relative to threshold
                ratio = distance_meters / self.pos_threshold_meters
                severity = min(100.0, 40 + (ratio - 1) * 25)
                
                jumps.append(GapAnomaly(
                    gap_type=GapType.POSITION_JUMP,
                    vessel_mmsi=track.mmsi,
                    timestamp=curr.timestamp,
                    severity_score=severity
                ))
        
        return jumps
    
    def _detect_velocity_spikes(self, track: VesselTrack) -> List[GapAnomaly]:
        """Detect impossible or unusual velocity changes."""
        spikes = []
        
        for i in range(1, len(track.reports)):
            prev = track.reports[i - 1]
            curr = track.reports[i]
            
            # Calculate time delta (avoid division by zero)
            if prev.timestamp == curr.timestamp:
                continue
            
            time_delta_hours = (curr.timestamp - prev.timestamp).total_seconds() / 3600.0
            
            # Calculate distance
            delta_lat = math.radians(curr.lat - prev.lat)
            delta_lon = math.radians(curr.lon - prev.lon)
            
            a = (math.sin(delta_lat/2)**2 + 
                 math.cos(math.radians(prev.lat)) * math.cos(math.radians(curr.lat)) *
                 math.sin(delta_lon/2)**2)
            
            c = 2 * math.asin(math.sqrt(a))
            distance_miles = 3440.5 * c
            
            # Calculate speed in knots (nautical miles per hour)
            if time_delta_hours > 0:
                speed_knots = distance_miles / time_delta_hours
                
                if speed_knots > self.vel_spike_threshold:
                    ratio = speed_knots / self.vel_spike_threshold
                    severity = min(100.0, 30 + (ratio - 1) * 25)
                    
                    spikes.append(GapAnomaly(
                        gap_type=GapType.VELOCITY_SPIKE,
                        vessel_mmsi=track.mmsi,
                        timestamp=curr.timestamp,
                        severity_score=severity
                    ))
        
        return spikes
    
    def filter_by_min_severity(self, results: List[TrackAnalysisResult], 
                               min_score: float = 20.0) -> List[TrackAnomaly]:
        """
        Filter anomalies by minimum severity score.
        
        Args:
            results: Analysis results to filter
            min_score: Minimum severity threshold (0-100)
            
        Returns:
            Flattened list of GapAnomaly objects above threshold
        """
        all_anomalies = []
        
        for result in results:
            if not result.time_gaps and not result.position_jumps and \
               not result.velocity_spikes:
                continue
            
            for gap in result.time_gaps + result.position_jumps + result.velocity_spikes:
                if gap.severity_score >= min_score:
                    all_anomalies.append(gap)
        
        return sorted(all_anomalies, key=lambda x: -x.severity_score)


# =============================================================================
# Demo / Self-Contained Test Data Generator and Runner
# =============================================================================

def generate_demo_tracks(num_vessels: int = 5, 
                         num_reports_per_vessel: int = 100,
                         include_anomalies: bool = True) -> List[VesselTrack]:
    """Generate realistic demo track data with optional anomalies."""
    import random
    
    tracks = []
    
    for vessel_idx in range(num_vessels):
        # Create a base trajectory (simple linear path with noise)
        start_lat, start_lon = 35.0 + vessel_idx * 0.1, -120.0 + vessel_idx * 0.1
        end_lat, end_lon = 36.0, -121.0
        
        # Base speed in knots
        base_speed_knots = 15 + random.uniform(-3, 3)
        
        track = VesselTrack(mmsi=f"367{vessel_idx:04d}")
        
        current_time = datetime.now() - timedelta(hours=num_reports_per_vessel)
        
        for report_idx in range(num_reports_per_vessel):
            # Add some randomness to reporting intervals (normal 120s +/- 30s)
            if report_idx == 0:
                interval = random.uniform(90, 150)
            else:
                expected_time = current_time + timedelta(seconds=report_idx * 120)
                
                # Occasionally create a time gap (simulates transponder off)
                if include_anomalies and random.random() < 0.15:
                    gap_duration = random.uniform(180, 900)  # 3-15 minutes
                    expected_time += timedelta(seconds=gap_duration)
                
                interval = (expected_time - current_time).total_seconds()
            
            # Add small position noise (simulates GPS error)
            lat_noise = random.gauss(0.0, 0.02)  # ~2km max
            lon_noise = random.gauss(0.0, 0.02)
            
            report_lat = start_lat + (end_lat - start_lat) * (report_idx / num_reports_per_vessel) + lat_noise
            report_lon = start_lon + (end_lon - start_lon) * (report_idx / num_reports_per_vessel) + lon_noise
            
            # Occasionally create a position jump (simulates satellite handoff error)
            if include_anomalies and random.random() < 0.05:
                jump_lat = random.gauss(0.0, 0.15)  # ~15km max
                jump_lon = random.gauss(0.0, 0.15)
                report_lat += jump_lat
                report_lon += jump_lon
            
            report = PositionReport(
                timestamp=current_time + timedelta(seconds=interval),
                lat=report_lat,
                lon=report_lon,
                speed_over_ground=base_speed_knots + random.gauss(0, 1)
            )
            
            track.append(report)
            current_time = expected_time
        
        tracks.append(track)
    
    return tracks


def main():
    """Run self-contained demo with realistic data."""
    print("=" * 60)
    print("AIS Track Continuity Checker - Demo")
    print("=" * 60)
    
    # Generate and analyze demo data
    tracks = generate_demo_tracks(num_vessels=10, num_reports_per_vessel=200)
    
    # Configure