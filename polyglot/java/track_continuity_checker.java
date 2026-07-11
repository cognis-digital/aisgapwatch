package polyglot.java;

import java.io.*;
import java.nio.file.*;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.*;
import java.util.stream.Collectors;

/**
 * AIS Track Continuity Checker
 * 
 * Detects and scores gaps in vessel transponder tracks.
 * Maritime situational awareness / defensive OSINT tool.
 */
public class track_continuity_checker {

    // Configuration constants
    private static final double DEFAULT_GAP_THRESHOLD_SECONDS = 300;      // 5 minutes
    private static final int MIN_TRACK_POINTS = 3;                       // Minimum points for analysis
    private static final double SEVERITY_MULTIPLIER = 1.2;               // Exponential growth factor

    /**
     * Represents a single AIS position fix with metadata
     */
    public static class PositionFix {
        private String mmsi;
        private Instant timestamp;
        private double latitude;
        private double longitude;
        private int speedOverGround;
        private int courseOverGround;

        public PositionFix(String mmsi, Instant ts, double lat, double lon, 
                          int sog, int cog) {
            this.mmsi = mmsi;
            this.timestamp = ts;
            this.latitude = lat;
            this.longitude = lon;
            this.speedOverGround = sog;
            this.courseOverGround = cog;
        }

        public String getMMSI() { return mmsi; }
        public Instant getTimestamp() { return timestamp; }
        public double getLatitude() { return latitude; }
        public double getLongitude() { return longitude; }
        public int getSpeedOverGround() { return speedOverGround; }
        public int getCourseOverGround() { return courseOverGround; }

        @Override
        public String toString() {
            return String.format("Fix{%s, %s, %.4f, %.4f}", 
                               mmsi, timestamp, latitude, longitude);
        }
    }

    /**
     * Represents a gap anomaly detected in the track
     */
    public static class GapAnomaly {
        private String mmsi;
        private Instant startTime;
        private Instant endTime;
        private long durationSeconds;
        private int gapIndex;
        private double severityScore;

        public GapAnomaly(String mmsi, Instant start, Instant end, 
                         long duration, int index, double score) {
            this.mmsi = mmsi;
            this.startTime = start;
            this.endTime = end;
            this.durationSeconds = duration;
            this.gapIndex = index;
            this.severityScore = score;
        }

        public String getMMSI() { return mmsi; }
        public Instant getStartTime() { return startTime; }
        public Instant getEndTime() { return endTime; }
        public long getDurationSeconds() { return durationSeconds; }
        public int getGapIndex() { return gapIndex; }
        public double getSeverityScore() { return severityScore; }

        @Override
        public String toString() {
            return String.format("Gap[%s, %d-%d, %.1fs, score=%.2f]", 
                               mmsi, startTime.getEpochSecond(), 
                               endTime.getEpochSecond(), durationSeconds, severityScore);
        }
    }

    /**
     * Represents a scored vessel track with detected anomalies
     */
    public static class ScoredTrack {
        private String mmsi;
        private List<PositionFix> fixes;
        private long totalDurationSeconds;
        private int totalGaps;
        private double overallScore;
        private List<GapAnomaly> gaps;

        public ScoredTrack(String mmsi, List<PositionFix> fixes) {
            this.mmsi = mmsi;
            this.fixes = new ArrayList<>(fixes);
            this.totalGaps = 0;
            this.gaps = new ArrayList<>();
        }

        public String getMMSI() { return mmsi; }
        public List<PositionFix> getFixes() { return fixes; }
        public long getTotalDurationSeconds() { return totalDurationSeconds; }
        public int getTotalGaps() { return totalGaps; }
        public double getOverallScore() { return overallScore; }
        public List<GapAnomaly> getGaps() { return gaps; }

        @Override
        public String toString() {
            return String.format("Track[%s, %.1fs, %d gaps, score=%.2f]", 
                               mmsi, totalDurationSeconds, totalGaps, overallScore);
        }
    }

    /**
     * Main analyzer class for track continuity checking
     */
    public static class TrackContinuityAnalyzer {

        private double gapThreshold;
        private int minPoints;

        public TrackContinuityAnalyzer(double threshold, int minPts) {
            this.gapThreshold = threshold;
            this.minPoints = minPts;
        }

        /**
         * Analyze a single vessel track for continuity anomalies
         */
        public ScoredTrack analyze(String mmsi, List<PositionFix> fixes) {
            if (fixes == null || fixes.isEmpty()) {
                return new ScoredTrack(mmsi, fixes);
            }

            // Sort by timestamp to ensure chronological order
            fixes.sort(Comparator.comparing(PositionFix::getTimestamp));

            // Filter out obviously bad data points
            List<PositionFix> cleanFixes = filterBadData(fixes);

            if (cleanFixes.size() < minPoints) {
                ScoredTrack result = new ScoredTrack(mmsi, fixes);
                result.totalDurationSeconds = 
                    ChronoUnit.SECONDS.between(
                        cleanFixes.get(0).getTimestamp(),
                        cleanFixes.get(cleanFixes.size()-1).getTimestamp());
                return result;
            }

            // Calculate total duration
            long totalDuration = ChronoUnit.SECONDS.between(
                cleanFixes.get(0).getTimestamp(),
                cleanFixes.get(cleanFixes.size() - 1).getTimestamp());

            ScoredTrack scoredTrack = new ScoredTrack(mmsi, fixes);
            scoredTrack.totalDurationSeconds = totalDuration;

            // Detect gaps
            detectGaps(cleanFixes, scoredTrack);

            // Calculate overall score
            calculateOverallScore(scoredTrack);

            return scoredTrack;
        }

        /**
         * Filter out obviously bad data points (NaN coordinates, extreme speeds)
         */
        private List<PositionFix> filterBadData(List<PositionFix> fixes) {
            if (fixes == null || fixes.isEmpty()) {
                return new ArrayList<>();
            }

            // Constants for filtering
            final double MIN_LAT = -90.0;
            final double MAX_LAT = 90.0;
            final double MIN_LON = -180.0;
            final double MAX_LON = 180.0;
            final int MAX_SPEED_KNOTS = 60; // Maximum realistic speed

            List<PositionFix> result = new ArrayList<>();
            PositionFix prev = null;

            for (PositionFix fix : fixes) {
                boolean isValidLat = fix.getLatitude() >= MIN_LAT && 
                                    fix.getLatitude() <= MAX_LAT;
                boolean isValidLon = fix.getLongitude() >= MIN_LON && 
                                    fix.getLongitude() <= MAX_LON;
                boolean isValidSpeed = fix.getSpeedOverGround() >= 0 && 
                                       fix.getSpeedOverGround() <= MAX_SPEED_KNOTS;

                if (isValidLat && isValidLon && isValidSpeed) {
                    result.add(fix);
                    prev = fix;
                }
            }

            return result.isEmpty() ? fixes : result;
        }

        /**
         * Detect gaps in the track sequence
         */
        private void detectGaps(List<PositionFix> cleanFixes, ScoredTrack scoredTrack) {
            if (cleanFixes.size() < 2) {
                return;
            }

            int gapIndex = 0;
            PositionFix prev = cleanFixes.get(0);

            for (int i = 1; i < cleanFixes.size(); i++) {
                PositionFix current = cleanFixes.get(i);
                
                long timeDiff = ChronoUnit.SECONDS.between(prev.getTimestamp(), 
                                                          current.getTimestamp());

                if (timeDiff > gapThreshold) {
                    // Gap detected - calculate severity score
                    double baseScore = 1.0;
                    
                    // Exponential growth with gap size
                    double exponentialFactor = Math.pow(SEVERITY_MULTIPLIER, 
                                                        timeDiff / gapThreshold);
                    
                    // Additional factors
                    double velocityFactor = 1.0 + (prev.getSpeedOverGround() / 60.0) * 0.5;
                    
                    double severityScore = baseScore * exponentialFactor * velocityFactor;

                    GapAnomaly anomaly = new GapAnomaly(
                        scoredTrack.mmsi,
                        prev.getTimestamp(),
                        current.getTimestamp(),
                        timeDiff,
                        gapIndex++,
                        severityScore
                    );

                    scoredTrack.gaps.add(anomaly);
                    scoredTrack.totalGaps++;
                }

                prev = current;
            }
        }

        /**
         * Calculate an overall quality score for the track (0.0 - 1.0)
         */
        private void calculateOverallScore(ScoredTrack scoredTrack) {
            if (scoredTrack.gaps.isEmpty()) {
                // Perfect continuity
                scoredTrack.overallScore = 1.0;
                return;
            }

            double maxPossibleGaps = scoredTrack.totalDurationSeconds / gapThreshold;
            
            // Base score: inverse of gap ratio
            double baseScore = Math.max(0.0, 
                    1.0 - (scoredTrack.totalGaps / maxPossibleGaps));

            // Penalty for large gaps
            long totalGapTime = scoredTrack.gaps.stream()
                .mapToLong(GapAnomaly::getDurationSeconds)
                .sum();
            
            double gapTimePenalty = Math.min(1.0, 
                    (totalGapTime / scoredTrack.totalDurationSeconds));

            // Combine scores with weights
            double overallScore = 0.6 * baseScore + 0.4 * (1.0 - gapTimePenalty);

            scoredTrack.overallScore = Math.max(0.0, Math.min(1.0, overallScore));
        }

        /**
         * Analyze multiple tracks in batch mode
         */
        public List<ScoredTrack> analyzeBatch(String mmsiPrefix, 
                                             List<List<PositionFix>> trackBatches) {
            List<ScoredTrack> results = new ArrayList<>();

            for (int i = 0; i < trackBatches.size(); i++) {
                String mmsi = String.format("%s%03d", mmsiPrefix, i);
                ScoredTrack scored = analyze(mmsi, trackBatches.get(i));
                results.add(scored);
            }

            return results;
        }

        /**
         * Load tracks from CSV file format and analyze
         */
        public List<ScoredTrack> analyzeFromCSV(String csvPath) throws IOException {
            Path path = Paths.get(csvPath);
            
            if (!Files.exists(path)) {
                throw new FileNotFoundException("CSV not found: " + csvPath);
            }

            List<PositionFix> allFixes = new ArrayList<>();
            
            // Expected CSV format: mmsi,timestamp,lat,lon,sog,cog
            try (BufferedReader reader = Files.newBufferedReader(path)) {
                String headerLine = reader.readLine(); // Skip header
                
                while ((headerLine = reader.readLine()) != null) {
                    if (headerLine.trim().isEmpty() || 
                        headerLine.startsWith("#")) {
                        continue;
                    }

                    try {
                        String[] parts = headerLine.split(",");
                        
                        if (parts.length < 4) {
                            continue; // Malformed line
                        }

                        String mmsi = parts[0].trim();
                        Instant timestamp = Instant.parse(parts[1].trim());
                        double lat = Double.parseDouble(parts[2].trim());
                        double lon = Double.parseDouble(parts[3].trim());
                        
                        int sog = 0;
                        int cog = 0;
                        
                        if (parts.length >= 5) {
                            sog = Integer.parseInt(parts[4].trim());
                        }
                        if (parts.length >= 6) {
                            cog = Integer.parseInt(parts[5].trim());
                        }

                        allFixes.add(new PositionFix(mmsi, timestamp, lat, lon, sog, cog));
                    } catch (Exception e) {
                        // Skip malformed lines
                        continue;
                    }
                }
            }

            // Group by MMSI
            Map<String, List<PositionFix>> grouped = allFixes.stream()
                .collect(Collectors.groupingBy(PositionFix::getMMSI));

            return analyzeBatch("UNKNOWN", 
                              grouped.values().stream()
                                  .map(list -> {
                                      if (list.isEmpty()) {
                                          return new ArrayList<>();
                                      }
                                      // Sort by timestamp within group
                                      list.sort(Comparator.comparing(
                                          PositionFix::getTimestamp));
                                      return list;
                                  })
                                  .collect(Collectors.toList()));
        }

        /**
         * Generate a human-readable report of analysis results
         */
        public String generateReport(List<ScoredTrack> tracks) {
            StringBuilder sb = new StringBuilder();
            
            sb.append("=== AIS TRACK CONTINUITY ANALYSIS REPORT ===\n\n");
            sb.append(String.format("Configuration:\n");
            sb.append(String.format("  Gap Threshold: %.1f seconds (%.2f minutes)\n", 
                                   gapThreshold, gapThreshold / 60.0));
            sb.append(String.format("  Min Points Required: %d\n\n", minPoints));

            if (tracks.isEmpty()) {
                sb.append("No tracks analyzed.\n");
                return sb.toString();
            }

            // Summary statistics
            int totalFixes = 0;
            long totalDurationAll = 0;
            double avgScore = 0.0;

            for (ScoredTrack track : tracks) {
                totalFixes += track.fixes.size();
                totalDurationAll += track.totalDurationSeconds;
                avgScore += track.overallScore;
            }

            int avgGapsPerTrack = (int)(tracks.stream()
                    .mapToInt(ScoredTrack::getTotalGaps)
                    .average().orElse(0));

            sb.append(String.format("Summary Statistics:\n");
            sb.append(String.format("  Total Tracks Analyzed: %d\n", tracks.size()));
            sb.append(String.format("  Total Position Fixes: %d\n", totalFixes));
            sb.append(String.format("  Average Track Duration: %.1f seconds\n", 
                                   (double)totalDurationAll / tracks.size()));
            sb.append(String.format("  Average Gaps per Track: %.2f\n", avgGapsPerTrack);
            sb.append(String.format("  Average Continuity Score: %.3f\n\n", avgScore / tracks.size()));

            // Detailed results for each track
            sb.append("\n=== DETAILED RESULTS ===\n\n");

            int criticalCount = 0;
            
            for (int i = 0; i < tracks.size(); i++) {
                ScoredTrack track = tracks.get(i);
                
                String status = "OK";
                if (track.overallScore < 0.7) {
                    status = "WARNING";
                } else if (track.overallScore < 0.4) {
                    status = "CRITICAL";
                    criticalCount++;
                }

                sb.append(String.format("Track %d: %s\n", i + 1, track.mmsi));
                sb.append(String.format("  Status: %s\n", status);
                sb.append(String.format("  Duration: %.1f seconds (%.2f minutes)\n", 
                                       track.totalDurationSeconds, 
                                       track.totalDurationSeconds / 60.0));
                sb.append(String.format("  Total Gaps: %d\n", track.totalGaps));
                sb.append(String.format("  Overall Score: %.3f/1.0\n", track.overallScore));

                if (!track.gaps.isEmpty()) {
                    // Show top 5 largest gaps
                    List<GapAnomaly> sortedGaps = track.gacks.stream()
                        .sorted((a, b) -> Long.compare(b.getDurationSeconds(), 
                                                        a.getDurationSeconds()))
                        .limit(5)
                        .collect(Collectors.toList());

                    sb.append("  Top Gaps:\n");
                    
                    for (int j = 0; j < sortedGaps.size(); j++) {
                        GapAnomaly gap = sortedGaps.get(j);
                        
                        // Format timestamp as readable string
                        String startStr = formatTimestamp(gap.startTime);
                        String endStr = formatTimestamp(gap.endTime);

                        sb.append(String.format("    %d. %.1f seconds\n", 
                                               j + 1, gap.durationSeconds));
                        sb.append(String.format("       Start: %s\n", startStr);
                        sb.append(String.format("       End:   %s\n", endStr);
                        sb.append(String.format("       Severity Score: %.3f\n", gap.severityScore);
                    }
                } else {
                    sb.append("  No gaps detected.\n");
                }

                sb.append("\n");
            }

            // Critical summary
            if (criticalCount > 0) {
                sb.append(String.format("\n=== CRITICAL SUMMARY ===\n");
                sb.append(String.format("Tracks with critical issues: %d\n", criticalCount));