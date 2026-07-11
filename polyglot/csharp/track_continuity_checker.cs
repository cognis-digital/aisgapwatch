using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Serialization;

namespace aisgapwatch.polyglot.cs
{
    /// <summary>
    /// Represents a single AIS position fix from a vessel transponder.
    </summary>
    public readonly record struct TrackPoint(
        double Latitude,
        double Longitude,
        DateTime Timestamp,
        double? SpeedOverGround = null,
        double? CourseOverGround = null,
        int MMSI = 0,
        string? VesselName = null
    );

    /// <summary>
    /// Configuration for the continuity checker.
    </summary>
    public readonly record struct ContinuityConfig(
        TimeSpan MaxTimeGap = TimeSpan.FromMinutes(10),      // Maximum expected time between fixes
        double MinExpectedSpeedKnots = 2.0,                  // Minimum vessel speed (knots)
        double MaxExpectedSpeedKnots = 35.0,                 // Maximum vessel speed (knots)
        double DistanceToleranceNauticalMiles = 0.1,         // Extra tolerance for position errors
        int MinPointsForAnalysis = 2                         // Minimum points needed to detect gaps
    );

    /// <summary>
    /// Represents a detected gap anomaly in vessel tracking.
    </summary>
    public readonly record struct GapAnomaly(
        string VesselName,
        int MMSI,
        TrackPoint PreviousFix,
        TrackPoint NextFix,
        TimeSpan TimeGap,
        double DistanceGapNauticalMiles,
        double ExpectedDistanceNauticalMiles,
        double SpeedRequiredKnots,
        string AnomalyType,                               // "TIME_GAP", "DISTANCE_JUMP", "SPEED_ANOMALY"
        int SeverityScore                                // 0-100 scale
    );

    /// <summary>
    /// Core engine for detecting and scoring AIS transponder gaps.
    </summary>
    public static class TrackContinuityChecker
    {
        private const double EarthRadiusNauticalMiles = 3440.065;
        
        /// <summary>
        /// Calculates great-circle distance between two positions in nautical miles.
        /// </summary>
        public static double CalculateDistance(TrackPoint a, TrackPoint b)
        {
            if (a.Timestamp == b.Timestamp || a.Latitude == 0 && b.Latitude == 0)
                return 0;

            const double degToRad = Math.PI / 180.0;
            
            // Haversine formula for accuracy at any distance
            double dLat = (b.Latitude - a.Latitude) * degToRad;
            double dLon = (b.Longitude - a.Longitude) * degToRad;
            double lat1 = a.Latitude * degToRad;
            double lat2 = b.Latitude * degToRad;

            double aHav = Math.Sin(dLat / 2.0) * Math.Sin(dLat / 2.0) +
                         Math.Cos(lat1) * Math.Cos(lat2) *
                         Math.Sin(dLon / 2.0) * Math.Sin(dLon / 2.0);
            double cHav = 2.0 * Math.Atan2(Math.Sqrt(aHav), Math.Sqrt(1 - aHav));

            return EarthRadiusNauticalMiles * cHav;
        }

        /// <summary>
        /// Calculates expected distance based on vessel's own speed and course.
        /// </summary>
        public static double CalculateExpectedDistance(TrackPoint from, TrackPoint to)
        {
            if (from.SpeedOverGround.HasValue && from.CourseOverGround.HasValue)
            {
                // Use the vessel's reported motion
                double timeHours = (to.Timestamp - from.Timestamp).TotalHours;
                return from.SpeedOverGround.Value * timeHours;
            }

            // Fallback: estimate based on typical vessel speeds
            const double TypicalSpeedKnots = 12.0;
            double timeHours = (to.Timestamp - from.Timestamp).TotalHours;
            return Math.Max(0, TypicalSpeedKnots * timeHours);
        }

        /// <summary>
        /// Main entry point for gap detection and scoring.
        /// </summary>
        public static List<GapAnomaly> CheckContinuity(
            IEnumerable<TrackPoint> points,
            ContinuityConfig config = default)
        {
            var sortedPoints = points
                .OrderBy(p => p.Timestamp)
                .ThenByDescending(p => p.MMSI);

            if (sortedPoints.Count < 2)
                return new List<GapAnomaly>();

            // Group by MMSI to process each vessel separately
            var grouped = sortedPoints.GroupBy(p => p.MMSI);

            var anomalies = new List<GapAnomaly>();

            foreach (var group in grouped)
            {
                var vesselName = group.Key == 0 ? "Unknown" : 
                    GetVesselName(group, config);

                // Ensure we have enough points for meaningful analysis
                if (group.Count() < config.MinPointsForAnalysis)
                    continue;

                var vesselAnomalies = CheckSingleTrack(
                    group.OrderBy(p => p.Timestamp).ToList(),
                    vesselName,
                    config
                );

                anomalies.AddRange(vesselAnomalies);
            }

            return anomalies;
        }

        private static string GetVesselName(IEnumerable<TrackPoint> points, ContinuityConfig config)
        {
            // Prefer name from first point with data
            var named = points.FirstOrDefault(p => !string.IsNullOrEmpty(p.VesselName));
            
            if (named != null && !string.IsNullOrEmpty(named.VesselName))
                return named.VesselName;

            // Fallback: generate identifier from MMSI
            return $"Vessel_{points.First().MMSI}";
        }

        private static List<GapAnomaly> CheckSingleTrack(
            List<TrackPoint> points,
            string vesselName,
            ContinuityConfig config)
        {
            var anomalies = new List<GapAnomaly>();
            
            // Need at least 2 points to detect a gap
            if (points.Count < 2)
                return anomalies;

            for (int i = 1; i < points.Count; i++)
            {
                TrackPoint prev = points[i - 1];
                TrackPoint curr = points[i];

                // Skip duplicate timestamps
                if (prev.Timestamp == curr.Timestamp)
                    continue;

                double distance = CalculateDistance(prev, curr);
                
                // Check for time-based gap
                if (IsTimeGapAnomaly(prev, curr, config))
                {
                    anomalies.Add(CreateTimeGapAnomaly(vesselName, prev.MMSI, 
                        prev, curr, distance, config));
                }

                // Check for distance jump anomaly
                else if (IsDistanceJumpAnomaly(prev, curr, config))
                {
                    anomalies.Add(CreateDistanceJumpAnomaly(vesselName, prev.MMSI,
                        prev, curr, distance, config));
                }

                // Check for speed anomaly when vessel reports motion
                else if (prev.SpeedOverGround.HasValue && 
                         IsSpeedAnomaly(prev, curr, config))
                {
                    anomalies.Add(CreateSpeedAnomaly(vesselName, prev.MMSI,
                        prev, curr, distance, config));
                }
            }

            return anomalies;
        }

        private static bool IsTimeGapAnomaly(TrackPoint prev, TrackPoint curr, ContinuityConfig config)
        {
            double timeHours = (curr.Timestamp - prev.Timestamp).TotalHours;
            double expectedDistance = CalculateExpectedDistance(prev, curr);

            // A gap exists if expected distance is small but time elapsed is large
            // This indicates the vessel stopped reporting while still moving
            return expectedDistance < 0.5 && 
                   timeHours > config.MaxTimeGap.TotalHours * 0.8;
        }

        private static bool IsDistanceJumpAnomaly(TrackPoint prev, TrackPoint curr, ContinuityConfig config)
        {
            double timeHours = (curr.Timestamp - prev.Timestamp).TotalHours;
            double expectedDistance = CalculateExpectedDistance(prev, curr);

            // A jump is when distance traveled exceeds what's possible given typical speeds
            if (timeHours <= 0 || expectedDistance <= 0)
                return false;

            double maxPossibleSpeedKnots = 45.0; // Emergency/maximum vessel speed
            double maxExpectedDistance = maxPossibleSpeedKnots * timeHours + config.DistanceToleranceNauticalMiles;

            return distance > maxExpectedDistance;
        }

        private static bool IsSpeedAnomaly(TrackPoint prev, TrackPoint curr, ContinuityConfig config)
        {
            if (!prev.SpeedOverGround.HasValue || !curr.SpeedOverGround.HasValue)
                return false;

            double speedDiff = Math.Abs(curr.SpeedOverGround.Value - prev.SpeedOverGround.Value);
            
            // Flag sudden acceleration/deceleration beyond normal variation
            const double NormalSpeedVariationKnots = 3.0;
            return speedDiff > NormalSpeedVariationKnots * 2.0;
        }

        private static GapAnomaly CreateTimeGapAnomaly(
            string vesselName, int mmsi, TrackPoint prev, TrackPoint curr,
            double distance, ContinuityConfig config)
        {
            double timeHours = (curr.Timestamp - prev.Timestamp).TotalHours;
            double expectedDistance = CalculateExpectedDistance(prev, curr);

            // Severity: larger gaps and longer times are worse
            int severity = CalculateSeverity(
                timeHours, 
                distance / expectedDistance,
                config.MaxTimeGap.TotalHours
            );

            return new GapAnomaly(
                vesselName, mmsi, prev, curr,
                TimeSpan.FromHours(timeHours),
                distance,
                expectedDistance,
                expectedDistance > 0 ? expectedDistance / timeHours : 0,
                "TIME_GAP",
                severity
            );
        }

        private static GapAnomaly CreateDistanceJumpAnomaly(
            string vesselName, int mmsi, TrackPoint prev, TrackPoint curr,
            double distance, ContinuityConfig config)
        {
            double timeHours = (curr.Timestamp - prev.Timestamp).TotalHours;
            double expectedDistance = CalculateExpectedDistance(prev, curr);

            // Severity: larger jumps are more suspicious
            int severity = Math.Min(100, (int)(distance * 2));

            return new GapAnomaly(
                vesselName, mmsi, prev, curr,
                TimeSpan.FromHours(timeHours),
                distance,
                expectedDistance,
                expectedDistance > 0 ? expectedDistance / timeHours : 0,
                "DISTANCE_JUMP",
                severity
            );
        }

        private static GapAnomaly CreateSpeedAnomaly(
            string vesselName, int mmsi, TrackPoint prev, TrackPoint curr,
            double distance, ContinuityConfig config)
        {
            double speedDiff = Math.Abs(curr.SpeedOverGround.Value - prev.SpeedOverGround.Value);
            
            // Severity: larger changes are more anomalous
            int severity = (int)Math.Min(100, speedDiff * 5);

            return new GapAnomaly(
                vesselName, mmsi, prev, curr,
                TimeSpan.FromHours((curr.Timestamp - prev.Timestamp).TotalHours),
                distance,
                CalculateExpectedDistance(prev, curr),
                (prev.SpeedOverGround.Value + curr.SpeedOverGround.Value) / 2,
                "SPEED_ANOMALY",
                severity
            );
        }

        private static int CalculateSeverity(double timeGapHours, double ratio, ContinuityConfig config)
        {
            // Base score from time gap relative to expected max
            int baseScore = (int)(timeGapHours / config.MaxTimeGap.TotalHours * 40);
            
            // Additional penalty for large distance gaps
            int distancePenalty = Math.Min(30, (int)(ratio * 20));

            return Math.Min(100, baseScore + distancePenalty);
        }

        /// <summary>
        /// Filters anomalies by minimum severity threshold.
        /// </summary>
        public static List<GapAnomaly> FilterBySeverity(List<GapAnomaly> anomalies, int minScore)
        {
            return anomalies.Where(a => a.SeverityScore >= minScore).ToList();
        }

        /// <summary>
        /// Groups consecutive anomalies for the same vessel into single events.
        /// </summary>
        public static List<GapAnomaly> ConsolidateConsecutive(List<GapAnomaly> anomalies)
        {
            if (anomalies.Count == 0)
                return new List<GapAnomaly>();

            var consolidated = new List<GapAnomaly>();
            
            foreach (var anomaly in anomalies)
            {
                // Check if this follows a previous anomaly for the same vessel
                var last = consolidated.Last();
                
                if (last != null && 
                    last.MMSI == anomaly.MMSI &&
                    (anomaly.PreviousFix.Timestamp - last.NextFix.Timestamp).TotalMinutes < 5)
                {
                    // Merge into existing anomaly
                    last.TimeGap += anomaly.TimeGap;
                    last.DistanceGapNauticalMiles += anomaly.DistanceGapNauticalMiles;
                    last.SeverityScore = Math.Max(last.SeverityScore, anomaly.SeverityScore);
                }
                else
                {
                    consolidated.Add(anomaly);
                }
            }

            return consolidated;
        }

        /// <summary>
        /// Produces a summary report of all detected anomalies.
        /// </summary>
        public static string GenerateReport(List<GapAnomaly> anomalies, ContinuityConfig config)
        {
            if (anomalies.Count == 0)
                return "No significant gaps detected.";

            var byType = anomalies.GroupBy(a => a.AnomalyType);
            
            var report = new StringBuilder();
            report.AppendLine($"AIS Gap Analysis Report");
            report.AppendLine(new string('=', 40));
            report.AppendLine($"Total Gaps Detected: {anomalies.Count}");
            report.AppendLine($"Max Time Gap Allowed: {config.MaxTimeGap.TotalMinutes:F1} minutes");
            report.AppendLine();

            foreach (var typeGroup in byType)
            {
                var totalScore = typeGroup.Sum(a => a.SeverityScore);
                var avgSeverity = typeGroup.Average(a => a.SeverityScore).ToString("F1");
                
                report.AppendLine($"--- {typeGroup.Key} ---");
                report.AppendLine($"Count: {typeGroup.Count()}, Avg Severity: {avgSeverity}/100");
                report.AppendLine($"Total Score: {totalScore}");
                report.AppendLine();

                // Show top 5 most severe by type
                var sorted = typeGroup.OrderByDescending(a => a.SeverityScore).Take(5);
                
                foreach (var item in sorted)
                {
                    report.AppendLine($"  [{item.SeverityScore:3}/100] {item.VesselName}");
                    report.AppendLine($"    Time Gap: {item.TimeGap.TotalMinutes:F2} min, Distance: {item.DistanceGapNauticalMiles:F2} NM");
                    report.AppendLine();
                }
            }

            return report.ToString().TrimEnd();
        }

        /// <summary>
        /// Main demo entry point.
        */
        public static void RunDemo()
        {
            Console.WriteLine("=== AIS Gap Watch - Track Continuity Checker Demo ===\n");

            // Create sample vessel tracks with intentional gaps for testing
            var sampleData = GenerateSampleTrackData();

            // Configure checker parameters
            var config = new ContinuityConfig(
                MaxTimeGap: TimeSpan.FromMinutes(10),
                MinExpectedSpeedKnots: 2.0,
                MaxExpectedSpeedKnots: 35.0
            );

            Console.WriteLine("Configuration:");
            Console.WriteLine($"  Max Time Gap: {config.MaxTimeGap.TotalMinutes} minutes");
            Console.WriteLine($"  Speed Range: {config.MinExpectedSpeedKnots:F1} - {config.MaxExpectedSpeedKnots:F1} knots\n");

            // Run detection
            var anomalies = CheckContinuity(sampleData, config);

            // Display results
            Console.WriteLine("Detection Results:");
            Console.WriteLine(new string('-', 50));
            
            if (anomalies.Count == 0)
            {
                Console.WriteLine("No significant gaps detected.");
            }
            else
            {
                var consolidated = ConsolidateConsecutive(anomalies);
                
                foreach (var a in consolidated)
                {
                    string typeIcon = a.AnomalyType switch
                    {
                        "TIME_GAP" => "[T]",
                        "DISTANCE_JUMP" => "[J]",
                        "SPEED_ANOMALY" => "[S]",
                        _ => "?"
                    };

                    Console.WriteLine($"{typeIcon} [{a.SeverityScore:3}/100] {a.VesselName}");
                    Console.WriteLine($"    Gap: {a.TimeGap.TotalMinutes:F2} min | Distance: {a