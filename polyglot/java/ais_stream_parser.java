package polyglot.java;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * AIS Stream Parser - Detects and scores transponder-gap anomalies in vessel tracks.
 * 
 * Designed for maritime situational awareness and defensive OSINT applications.
 */
public class AisStreamParser {

    // NMEA 0183 message types that contain position data
    private static final int[] POSITION_MESSAGE_TYPES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19};
    
    // Time constants for gap detection (in milliseconds)
    private static final long MAX_TIME_GAP_MS = 60_000;      // 60 seconds between messages
    private static final long MIN_TIME_GAP_MS = 500;         // 0.5 seconds - filter noise
    
    // Spatial constants for gap detection (in nautical miles)
    private static final double MAX_SPATIAL_GAP_NM = 10.0;   // 10 NM between positions
    private static final double MIN_SPATIAL_GAP_NM = 0.01;   // 0.01 NM - filter noise
    
    // Scoring thresholds
    private static final int SEVERITY_CRITICAL = 100;        // Immediate attention required
    private static final int SEVERITY_HIGH = 50;             // Significant anomaly
    private static final int SEVERITY_MEDIUM = 25;           // Notable deviation
    private static final int SEVERITY_LOW = 10;              // Minor irregularity
    
    // Thread-safe storage for active tracks
    private final Map<String, VesselTrack> activeTracks = new ConcurrentHashMap<>();

    /**
     * Main entry point with demonstration.
     */
    public static void main(String[] args) throws IOException {
        AisStreamParser parser = new AisStreamParser();
        
        // Sample NMEA AIS messages for testing
        String sampleData = 
            "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.0,M,,*47" +
            "$GPVTG,005,0,T,032,0,M" +
            "$GPGSA,A,3,04,05,06,07,08,09,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50*6B" +
            "$GPGSV,3,1,12,01,45,083,30,02,42,105,35,03,38,167,28,04,35,230,32*6F" +
            "$GPGSV,3,2,12,05,30,280,40,06,28,340,35,07,25,010,42,08,22,170,38*6A" +
            "$GPGSV,3,3,12,09,20,350,45,10,18,040,38,11,15,290,42*6E" +
            "$GPGSV,3,4,12,15,10,120,48,16,08,310,45,17,05,240,42,18,02,190,38*6C" +
            "$GNGGA,123520,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.0,M,,*48" +
            "$GPVTG,005,0,T,032,0,M" +
            "$GNGSA,A,3,04,05,06,07,08,09,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50*6D" +
            "$GNGSV,3,1,12,01,45,083,30,02,42,105,35,03,38,167,28,04,35,230,32*6A" +
            "$GNGSV,3,2,12,05,30,280,40,06,28,340,35,07,25,010,42,08,22,170,38*6D" +
            "$GNGSV,3,3,12,09,20,350,45,10,18,040,38,11,15,290,42*6C" +
            "$GNGSV,3,4,12,15,10,120,48,16,08,310,45,17,05,240,42,18,02,190,38*6F" +
            "$GNGA,123521,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.0,M,,*49";

        // Parse sample data
        System.out.println("=== AIS Stream Parser Demo ===\n");
        
        int messageCount = 0;
        long startTime = Instant.now().toEpochMilli();
        
        for (String line : sampleData.split("\n")) {
            if (line.trim().isEmpty()) continue;
            
            AisMessage msg = parser.parseNMEA(line);
            if (msg != null) {
                messageCount++;
                
                // Process position messages
                if (POSITION_MESSAGE_TYPES.contains(msg.getType())) {
                    System.out.println("Processed Type " + msg.getType() + ": " + line.trim());
                    
                    // Simulate track creation/update with sample MMSI
                    String mmsi = "211990000"; // Sample IMO 211 vessel
                    
                    VesselTrack track = parser.processPosition(
                        msg, 
                        mmsi,
                        messageCount
                    );
                    
                    if (track != null) {
                        System.out.println("  Track state: " + track.getState());
                        
                        // Check for anomalies
                        AnomalyReport report = track.checkAnomalies();
                        if (report.hasAnomalies()) {
                            System.out.println("  ANOMALY DETECTED!");
                            System.out.println("    Type: " + report.getAnomalyType());
                            System.out.println("    Severity: " + report.getSeverity());
                            System.out.println("    Score: " + report.getScore());
                        }
                    }
                }
            } else {
                System.out.println("  Non-position message (skipped): " + line.trim());
            }
        }
        
        long endTime = Instant.now().toEpochMilli();
        System.out.println("\n=== Summary ===");
        System.out.println("Messages processed: " + messageCount);
        System.out.println("Processing time: " + (endTime - startTime) + " ms");
    }

    /**
     * Parses a single NMEA 0183 sentence.
     */
    public AisMessage parseNMEA(String nmeaLine) {
        if (nmeaLine == null || nmeaLine.trim().isEmpty()) {
            return null;
        }

        // Remove checksum and split into fields
        String[] parts = nmeaLine.split(",");
        int checksumHex = 0;
        
        for (int i = 1; i < parts.length - 2; i++) {
            checksumHex ^= Integer.parseInt(parts[i]);
        }
        
        // Verify checksum
        if ((checksumHex & 0xFF) != Integer.parseInt(parts[parts.length - 2])) {
            return null; // Checksum mismatch
        }

        // Parse based on message type
        int msgType = parseMessageType(parts);
        if (msgType == 0) {
            return new AisMessage(0, parts, Instant.now());
        }

        // Extract common fields
        double latitude = 0;
        double longitude = 0;
        double speedOverGround = 0;
        double courseOverGround = 0;
        
        if (msgType >= 1 && msgType <= 5) {
            // Types 1-5: Basic position data
            try {
                latitude = parseLatitude(parts[2]);
                longitude = parseLongitude(parts[3]);
                
                // Speed and course from Type 18/19 (Raima/Gnss fix)
                if (parts.length > 7) {
                    speedOverGround = Double.parseDouble(parts[7]) / 10.0;
                    courseOverGround = Double.parseDouble(parts[8]);
                }
            } catch (NumberFormatException e) {
                // Partial parse - continue with defaults
            }
        } else if (msgType >= 6 && msgType <= 9) {
            // Types 6-9: Extended position data
            try {
                latitude = parseLatitude(parts[2]);
                longitude = parseLongitude(parts[3]);
                
                if (parts.length > 7) {
                    speedOverGround = Double.parseDouble(parts[7]) / 10.0;
                    courseOverGround = Double.parseDouble(parts[8]);
                }
            } catch (NumberFormatException e) {
            }
        } else if (msgType >= 10 && msgType <= 19) {
            // Types 10-19: Raima/Gnss fix with enhanced data
            try {
                latitude = parseLatitude(parts[2]);
                longitude = parseLongitude(parts[3]);
                
                if (parts.length > 7) {
                    speedOverGround = Double.parseDouble(parts[7]) / 10.0;
                    courseOverGround = Double.parseDouble(parts[8]);
                }
            } catch (NumberFormatException e) {
            }
        }

        return new AisMessage(msgType, parts, Instant.now(), latitude, longitude, 
                           speedOverGround, courseOverGround);
    }

    /**
     * Parses the NMEA message type from the sentence.
     */
    private int parseMessageType(String[] parts) {
        if (parts.length < 2) return 0;
        
        try {
            // Extract MMSI and determine type
            String mmsiPart = parts[1];
            
            // Type 1-5: Basic position fixes
            if (mmsiPart.startsWith("2")) {
                return 1 + Integer.parseInt(mmsiPart.substring(1));
            }
            
            // Type 6-9: Extended position fixes  
            if (mmsiPart.startsWith("3")) {
                return 6 + Integer.parseInt(mmsiPart.substring(1));
            }
            
            // Types 10-19: Raima/Gnss fix
            if (mmsiPart.startsWith("4") || mmsiPart.startsWith("5")) {
                int type = 10 + Integer.parseInt(mmsiPart.substring(1));
                return type > 19 ? 19 : type;
            }
            
            // Default: assume Type 18 (Raima) for most modern transponders
            return 18;
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /**
     * Parses latitude from NMEA format.
     */
    private double parseLatitude(String latPart) {
        if (latPart == null || latPart.isEmpty()) return 0;
        
        String[] components = latPart.split(",");
        if (components.length < 2) return 0;
        
        try {
            int degrees = Integer.parseInt(components[0]);
            double minutes = Double.parseDouble(components[1]) / 60.0;
            
            // Determine hemisphere
            boolean north = components.length >= 3 && "N".equals(components[2]);
            
            return (degrees + minutes) * (north ? 1 : -1);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /**
     * Parses longitude from NMEA format.
     */
    private double parseLongitude(String lonPart) {
        if (lonPart == null || lonPart.isEmpty()) return 0;
        
        String[] components = lonPart.split(",");
        if (components.length < 2) return 0;
        
        try {
            int degrees = Integer.parseInt(components[0]);
            double minutes = Double.parseDouble(components[1]) / 60.0;
            
            // Determine hemisphere
            boolean east = components.length >= 3 && "E".equals(components[2]);
            
            return (degrees + minutes) * (east ? 1 : -1);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /**
     * Processes a position message and updates/creates the vessel track.
     */
    public VesselTrack processPosition(AisMessage msg, String mmsi, int sequenceNumber) {
        // Create or update track
        VesselTrack track = activeTracks.computeIfAbsent(mmsi, k -> 
            new VesselTrack(k, Instant.now()));

        // Update with new position data
        track.update(msg, sequenceNumber);
        
        return track;
    }

    /**
     * Checks the current track for anomalies and returns a report.
     */
    public AnomalyReport checkAnomalies(VesselTrack track) {
        if (track == null || !track.hasData()) {
            return new AnomalyReport(track.getMmsi(), 
                                   Collections.emptyList(), 0, "No data");
        }

        List<Anomaly> anomalies = new ArrayList<>();
        
        // Check for time gaps
        AnomaliesResult timeCheck = track.checkTimeGaps();
        if (timeCheck.hasIssues()) {
            for (GapEvent gap : timeCheck.getEvents()) {
                int severity;
                String type;
                
                long durationMs = gap.durationMs;
                double expectedDistance = calculateExpectedDistance(
                    track.getSpeed(), 
                    durationMs / 1000.0
                );
                
                if (durationMs > MAX_TIME_GAP_MS) {
                    severity = SEVERITY_CRITICAL;
                    type = "TIME_GAP_CRITICAL";
                } else if (durationMs > MIN_TIME_GAP_MS * 5) {
                    severity = SEVERITY_HIGH;
                    type = "TIME_GAP_HIGH";
                } else if (durationMs > MIN_TIME_GAP_MS * 2) {
                    severity = SEVERITY_MEDIUM;
                    type = "TIME_GAP_MEDIUM";
                } else {
                    continue; // Within normal bounds
                }

                anomalies.add(new Anomaly(
                    System.currentTimeMillis(),
                    type,
                    severity,
                    gap.durationMs,
                    expectedDistance,
                    track.getLat(),
                    track.getLon()
                ));
            }
        }

        // Check for spatial gaps (jumps in position)
        AnomaliesResult spatialCheck = track.checkSpatialGaps();
        if (spatialCheck.hasIssues()) {
            for (GapEvent gap : spatialCheck.getEvents()) {
                double distanceNM = calculateDistanceNauticalMiles(
                    track.getLastLat(), 
                    track.getLastLon(),
                    track.getPrevLat(),
                    track.getPrevLon()
                );

                int severity;
                String type;

                if (distanceNM > MAX_SPATIAL_GAP_NM)