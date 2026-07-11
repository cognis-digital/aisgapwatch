using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

namespace polyglot.csharp
{
    /// <summary>
    /// AIS Stream Parser - Detects transponder-gap anomalies in vessel tracks.
    /// Designed for maritime situational awareness and defensive OSINT applications.
    /// </summary>
    public static class AisStreamParser
    {
        // Default thresholds (configurable via constructor)
        private const double DEFAULT_TIME_GAP_SECONDS = 30.0;      // Max expected interval between fixes
        private const double DEFAULT_POSITION_JUMP_METERS = 5000.0; // Sudden displacement threshold
        private const double DEFAULT_VELOCITY_KNOTS = 60.0;         // Max reasonable speed
        private const int DEFAULT_TRACK_WINDOW_SIZE = 120;         // Seconds of history to keep

        public class AisConfig
        {
            public double TimeGapSeconds { get; set; }
            public double PositionJumpMeters { get; set; }
            public double MaxVelocityKnots { get; set; }
            public int TrackWindowSize { get; set; }
            public double ScoreDecayRate { get; set; } // 0.95-1.0 for recency weighting
        }

        private class VesselTrack
        {
            public string MMSI { get; set; }
            public List<PositionFix> Fixes { get; set; } = new();
            public double CurrentScore { get; set; } = 0.0;
            public DateTime LastUpdateTime { get; set; }
            public bool IsActive { get; set; }

            // Computed properties for quick access
            private PositionFix? _lastValidFix;
            private PositionFix? _secondLastFix;

            public double? VelocityKnots => ComputeVelocity();
            public double? CourseDegrees => ComputeCourse();

            private PositionFix? ComputeSecondLast()
            {
                if (Fixes.Count < 2) return null;
                var last = Fixes.LastOrDefault();
                var secondLast = Fixes[Fixes.Count - 2];
                
                // Filter out obvious outliers before computing
                if (last != null && secondLast != null)
                {
                    double dist = CalculateDistance(last, secondLast);
                    if (dist < PositionJumpMeters * 10.0) // Allow some noise
                        return secondLast;
                }
                return null;
            }

            private double? ComputeVelocity()
            {
                var last = _lastValidFix;
                var prev = _secondLastFix ?? ComputeSecondLast();

                if (last == null || prev == null) return null;

                double timeDiffHours = (last.Time - prev.Time).TotalHours;
                if (timeDiffHours <= 0.001) return null; // Avoid division by zero

                double distMeters = CalculateDistance(last, prev);
                double knots = (distMeters / 1852.0) / timeDiffHours;
                
                // Sanity check - filter impossible velocities
                if (knots < 0 || knots > MaxVelocityKnots * 3.0) return null;

                _lastValidFix = last;
                _secondLastFix = prev;
                return knots;
            }

            private double? ComputeCourse()
            {
                var last = _lastValidFix;
                var prev = _secondLastFix ?? ComputeSecondLast();

                if (last == null || prev == null) return null;

                // Calculate bearing using atan2 for proper quadrant handling
                double latDiff = (last.Latitude - prev.Latitude) * Math.PI / 180.0;
                double lonDiff = (last.Longitude - prev.Longitude) * Math.PI / 180.0;
                
                double meanLat = ((last.Latitude + prev.Latitude) / 2.0) * Math.PI / 180.0;
                double bearingRad = Math.Atan2(
                    Math.Sin(lonDiff),
                    Math.Cos(meanLat) * Math.Tan(latDiff)
                );
                
                double degrees = (bearingRad * 180.0 / Math.PI + 360.0) % 360.0;

                _lastValidFix = last;
                _secondLastFix = prev;
                return degrees;
            }

            private static double CalculateDistance(PositionFix a, PositionFix b)
            {
                // Haversine formula for great-circle distance
                double dLat = (b.Latitude - a.Latitude) * Math.PI / 180.0;
                double dLon = (b.Longitude - a.Longitude) * Math.PI / 180.0;
                
                double aVal = Math.Sin(dLat / 2.0) * Math.Sin(dLat / 2.0) +
                            Math.Cos(a.Latitude * Math.PI / 180.0) *
                            Math.Cos(b.Latitude * Math.PI / 180.0) *
                            Math.Sin(dLon / 2.0) * Math.Sin(dLon / 2.0);
                
                double c = 2.0 * Math.Atan2(Math.Sqrt(aVal), Math.Sqrt(1.0 - aVal));
                return 6371000.0 * c; // Earth radius in meters
            }

            public void AddFix(PositionFix fix)
            {
                Fixes.Add(fix);
                
                // Keep window bounded for memory efficiency
                while (Fixes.Count > DEFAULT_TRACK_WINDOW_SIZE + 5)
                {
                    Fixes.RemoveAt(0);
                }

                _lastValidFix = fix;
                LastUpdateTime = DateTime.UtcNow;
            }

            public void UpdateScore(double anomalyScore, double decayFactor)
            {
                CurrentScore = (CurrentScore * decayFactor) + (anomalyScore * (1.0 - decayFactor));
            }

            public bool IsAnomalous() => CurrentScore > 50.0; // Threshold for alerting
        }

        private class PositionFix
        {
            public DateTime Time { get; set; }
            public double Latitude { get; set; }
            public double Longitude { get; set; }
            public double? SpeedOverGroundKnots { get; set; }
            public double? CourseOverGroundDegrees { get; set; }
            public byte? Status { get; set; } // 0=underway, 1=anchor, etc.

            public override string ToString() => 
                $"Fix({Time:HH:mm:ss}, Lat={Latitude:F4}, Lon={Longitude:F4})";
        }

        private class AisAnomalyEvent
        {
            public DateTime Timestamp { get; set; }
            public string MMSI { get; set; }
            public AnomalyType Type { get; set; }
            public double Severity { get; set; } // 0-100
            public string Description { get; set; }

            public enum AnomalyType
            {
                TimeGap,
                PositionJump,
                VelocityAnomaly,
                CourseChange,
                UnknownFix,
                DuplicateFix
            }
        }

        private class AisStreamResult
        {
            public Dictionary<string, VesselTrack> ActiveVessels { get; set; } = new();
            public List<AisAnomalyEvent> Anomalies { get; set; } = new();
            public DateTime StartTime { get; set; }
            public DateTime EndTime { get; set; }

            public double GetMaxScore() => ActiveVessels.Values.Max(v => v.CurrentScore);
            
            public VesselTrack? GetMostAnomalous() => 
                ActiveVessels.OrderByDescending(v => v.CurrentScore).FirstOrDefault();
        }

        /// <summary>
        /// Parses a single NMEA 0183 sentence and extracts position data.
        /// </summary>
        private static bool TryParseNmeaSentence(string line, out PositionFix? fix)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                fix = null;
                return false;
            }

            // Remove checksum and carriage returns
            string cleanLine = line.Trim().Replace("\r", "").Replace("\n", "");
            
            // Extract MMSI from GPRMC sentence: $GPRMC,123519,A,4807.038,N,01131.000,E,...
            if (cleanLine.StartsWith("$GPRMC"))
            {
                string[] parts = cleanLine.Split(',');
                if (parts.Length >= 8)
                {
                    // Part 4: Latitude
                    // Part 5: N/S indicator
                    // Part 6: Longitude  
                    // Part 7: E/W indicator
                    
                    double? lat = ParseCoordinate(parts[3], parts[4]);
                    double? lon = ParseCoordinate(parts[5], parts[6]);

                    if (lat.HasValue && lon.HasValue)
                    {
                        fix = new PositionFix
                        {
                            Time = DateTime.ParseExact(
                                $"1970-01-01T{parts[1]}Z", 
                                "yyyy-MM-ddTHHmmssZ", 
                                null),
                            Latitude = lat.Value,
                            Longitude = lon.Value,
                            Status = parts.Length > 8 ? byte.Parse(parts[8]) : (byte?)null
                        };

                        // Try to extract speed and course from optional fields
                        if (parts.Length >= 10)
                        {
                            double? sog = ParseCoordinate(parts[9], "N");
                            fix.SpeedOverGroundKnots = sog;
                            
                            double? cog = ParseCoordinate(parts[10], "T");
                            fix.CourseOverGroundDegrees = cog;
                        }

                        return true;
                    }
                }
            }
            
            // Also check for GPVTG (course/speed) if needed
            else if (cleanLine.StartsWith("$GPVTG"))
            {
                string[] parts = cleanLine.Split(',');
                if (parts.Length >= 4 && !string.IsNullOrEmpty(parts[1]))
                {
                    double? cog = ParseCoordinate(parts[1], "T");
                    
                    // Try to find MMSI from previous GPRMC - this is a simplified approach
                    fix = new PositionFix
                    {
                        Time = DateTime.ParseExact(
                            $"1970-01-01T{parts[0]}Z", 
                            "yyyy-MM-ddTHHmmssZ", 
                            null),
                        CourseOverGroundDegrees = cog,
                        Status = parts.Length > 4 ? byte.Parse(parts[3]) : (byte?)null
                    };
                    
                    // Find associated MMSI from context - would need state tracking
                    fix.MMSI = "VTG"; // Placeholder
                }
            }

            fix = null;
            return false;
        }

        private static double? ParseCoordinate(string coord, string direction)
        {
            if (string.IsNullOrEmpty(coord)) return null;

            try
            {
                // Convert DMS to decimal degrees
                // Format: 4807.038 or 1131.000
                double dms = double.Parse(coord);
                
                // Split into degrees and minutes
                int deg = (int)(dms / 60.0);
                double min = dms - (deg * 60.0);
                
                return deg + (min / 60.0);
            }
            catch
            {
                // Try direct parse if already decimal
                try
                {
                    string cleanCoord = coord.Replace("N", "").Replace("S", "")
                                              .Replace("E", "").Replace("W", "");
                    return double.Parse(cleanCoord);
                }
                catch
                {
                    return null;
                }
            }
        }

        /// <summary>
        /// Main entry point for parsing AIS stream.
        /// Reads from stdin by default, or file path if provided.
        /// </summary>
        public static AisStreamResult ParseStream(Stream input, AisConfig? config = null)
        {
            config ??= new AisConfig();

            var result = new AisStreamResult
            {
                StartTime = DateTime.UtcNow,
                ActiveVessels = new Dictionary<string, VesselTrack>()
            };

            using (var reader = new StreamReader(input))
            {
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (!TryParseNmeaSentence(line, out PositionFix? fix))
                        continue;

                    // Skip VTG-only fixes without MMSI context
                    if (fix.MMSI == "VTG") continue;

                    result.EndTime = DateTime.UtcNow;

                    // Create or update vessel track
                    if (!result.ActiveVessels.TryGetValue(fix.MMSI, out VesselTrack? track))
                    {
                        track = new VesselTrack
                        {
                            MMSI = fix.MMSI,
                            CurrentScore = 0.0,
                            IsActive = true
                        };
                        result.ActiveVessels[fix.MMSI] = track;
                    }

                    // Detect anomalies before adding to track
                    CheckForAnomalies(track, fix);

                    // Add fix to track
                    track.AddFix(fix);

                    // Apply decay and update score
                    double decayFactor = 1.0 - (config.ScoreDecayRate * 0.05);
                    track.UpdateScore(0.0, decayFactor);
                }
            }

            return result;
        }

        /// <summary>
        /// Analyzes a new position fix against the vessel's history to detect anomalies.
        /// </summary>
        private static void CheckForAnomalies(VesselTrack track, PositionFix? newFix)
        {
            if (newFix == null || track.Fixes.Count < 2) return;

            // Get previous fix for comparison
            var prevFix = track.Fixes[track.Fixes.Count - 2];

            double timeDiffSeconds = (newFix.Time - prevFix.Time).TotalSeconds;
            
            // Time gap detection
            if (timeDiffSeconds > config.TimeGapSeconds)
            {
                AddAnomaly(track, newFix.MMSI, AisAnomalyEvent.AnomalyType.TimeGap,
                    Math.Min(100.0, 50.0 + timeDiffSeconds / 2.0),
                    $"Time gap detected: {timeDiffSeconds:F1}s (threshold: {config.TimeGapSeconds}s)");
            }

            // Position jump detection
            double distance = CalculateDistance(prevFix, newFix);
            if (distance > config.PositionJumpMeters)
            {
                AddAnomaly(track, newFix.MMSI, AisAnomalyEvent.AnomalyType.PositionJump,
                    Math.Min(100.0, 80.0 + distance / 50.0),
                    $"Large position jump: {distance:F0}m (threshold: {config.PositionJumpMeters}m)");
            }

            // Velocity anomaly detection
            double? velocity = track.VelocityKnots;
            if (velocity.HasValue && velocity.Value > config.MaxVelocityKnots * 2.5)
            {
                AddAnomaly(track, newFix.MMSI, AisAnomalyEvent.AnomalyType.VelocityAnomaly,
                    Math.Min(100.0, 70.0 + (velocity.Value - config.MaxVelocityKnots) / 5.0),
                    $"High velocity: {velocity:F1} knots (threshold: {config.MaxVelocityKnots} knots)");
            }

            // Course change detection
            double? course = newFix.CourseOverGroundDegrees;
            if (course.HasValue && track.Fixes.Count >= 3)
            {
                var prevCourse = track.Fixes[track.Fixes.Count - 2].CourseOverGroundDegrees;
                
                if (prevCourse.HasValue)
                {
                    double courseDiff = Math.Abs(course.Value - prevCourse.Value);
                    
                    // Handle wrap-around at 0/360 degrees
                    if (courseDiff > 180.0)
                        courseDiff = 360.0 - courseDiff;

                    if (courseDiff > 45.0)
                    {
                        AddAnomaly(track, newFix.MMSI, AisAnomalyEvent.AnomalyType.CourseChange,
                            Math.Min(100.0, 60.0 + courseDiff / 2.0),
                            $"Abrupt course change: {courseDiff:F1}°");
                    }
                }
            }

            // Duplicate fix detection (same position within short time)
            if (track.Fixes.Count >= 3 && distance < 10.0 && 
                timeDiffSeconds < 5.0)
            {
                AddAnomaly(track, newFix.MMS