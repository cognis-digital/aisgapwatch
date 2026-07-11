package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"
)

// AISMessage represents a parsed NMEA 0183 position fix message
type AISMessage struct {
	Timestamp time.Time
	Lat       float64 // Degrees, North positive
	Lon       float64 // Degrees, East positive
	Speed     float64 // Knots (SOG)
	Course    float64 // Degrees true
	Heading   float64 // Degrees magnetic
}

// GapAnomaly represents a detected gap between consecutive positions
type GapAnomaly struct {
	Timestamp       time.Time
	PreviousLat     float64
	PreviousLon     float64
	CurrentLat      float64
	CurrentLon      float64
	DurationMinutes float64
	DistanceNM      float64
	Score           int
}

// GapConfig holds configuration for gap detection
type GapConfig struct {
	MaxNormalDeltaMin  float64 // Maximum normal time delta (minutes) - typically 1-2 min
	MaxNormalDistNMI   float64 // Maximum normal distance between fixes (nautical miles)
	ScoreThreshold     int    // Minimum score to flag as anomaly
}

// Default configuration values
var DefaultConfig = GapConfig{
	MaxNormalDeltaMin:  2.0,
	MaxNormalDistNMI:   5.0,
	ScoreThreshold:     3,
}

// CalculateDistanceNM computes distance between two latitude/longitude points in nautical miles
func CalculateDistanceNM(lat1, lon1, lat2, lon2 float64) float64 {
	latDiff := math.Abs(lat2 - lat1) * 60.0 // Convert to degrees
	lonDiff := math.Abs(lon2 - lon1) * 60.0
	
	// Haversine formula for more accuracy at larger distances
	const earthRadiusNM = 3440.065
	a := 0.5 * (math.Sin(latDiff/2)*math.Sin(latDiff/2) + 
		math.Cos(lat1*math.Pi/180.0)*math.Cos(lat2*math.Pi/180.0)*
			math.Sin(lonDiff/2)*math.Sin(lonDiff/2))
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	
	return earthRadiusNM * c
}

// ParseNMEATimestamp converts NMEA timestamp to time.Time
func ParseNMEATimestamp(timestamp string) (time.Time, error) {
	// Format: HHMMSS.SSS (e.g., 123456.789)
	if len(timestamp) < 6 {
		return time.Time{}, fmt.Errorf("invalid timestamp length: %s", timestamp)
	}
	
	hour, err := strconv.Atoi(timestamp[:2])
	if err != nil {
		return time.Time{}, fmt.Errorf("invalid hour in timestamp: %s", timestamp)
	}
	
	minute, err := strconv.Atoi(timestamp[2:4])
	if err != nil {
		return time.Time{}, fmt.Errorf("invalid minute in timestamp: %s", timestamp)
	}
	
	second, err := strconv.ParseFloat(timestamp[4:], 64)
	if err != nil {
		return time.Time{}, fmt.Errorf("invalid second in timestamp: %s", timestamp)
	}
	
	return time.Date(2024, 1, 1, hour, minute, int(second), 0, time.UTC), nil
}

// ParseAISMessage parses a NMEA position fix message into AISMessage struct
func ParseAISMessage(nmea string) (*AISMessage, error) {
	// Expected format: $GPRMC,hhmmss.ss,A,dd.mmnnnnn,x.xxxxxxx,N,E,...*checksum
	parts := strings.SplitN(nmea, ",", 10)
	if len(parts) < 8 {
		return nil, fmt.Errorf("insufficient fields in NMEA message: %s", nmea)
	}
	
	timestamp, err := ParseNMEATimestamp(parts[1])
	if err != nil {
		return nil, err
	}
	
	var lat, lon float64
	status := parts[2] // A = active, V = void
	
	if status == "A" && len(parts) >= 7 {
		// Parse latitude: dd.mmnnnnn (e.g., 37.8012345 -> 37.8012345°)
		latStr := parts[3]
		if strings.Contains(latStr, ".") {
			lat, err = strconv.ParseFloat(latStr[:strings.Index(latStr, ".")+1], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid latitude: %s", latStr)
			}
		} else {
			// Handle ddmm.mmm format (multiply by 60)
			lat, err = strconv.ParseFloat(latStr, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid latitude: %s", latStr)
			}
			lat *= 60.0
		}
		
		// Parse longitude: x.xxxxxxx (e.g., 122.456789 -> 122.456789°)
		lonStr := parts[4]
		if strings.Contains(lonStr, ".") {
			lon, err = strconv.ParseFloat(lonStr[:strings.Index(lonStr, ".")+1], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid longitude: %s", lonStr)
			}
		} else {
			lon, err = strconv.ParseFloat(lonStr, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid longitude: %s", lonStr)
			}
			lon *= 60.0
		}
		
		var speed float64
		if len(parts) >= 9 && parts[8] != "" {
			speed, err = strconv.ParseFloat(parts[8], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid speed: %s", parts[8])
			}
		}
		
		var course float64
		if len(parts) >= 10 && parts[9] != "" {
			course, err = strconv.ParseFloat(parts[9], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid course: %s", parts[9])
			}
		}
		
		var heading float64
		if len(parts) >= 11 && parts[10] != "" {
			heading, err = strconv.ParseFloat(parts[10], 64)
			if err != nil {
				return nil, fmt.Errorf("invalid heading: %s", parts[10])
			}
		}
		
		return &AISMessage{
			Timestamp: timestamp,
			Lat:       lat,
			Lon:       lon,
			Speed:     speed,
			Course:    course,
			Heading:   heading,
		}, nil
	}
	
	return nil, fmt.Errorf("inactive or void AIS message")
}

// AnalyzeAISStream processes a slice of NMEA strings and detects gaps
func AnalyzeAISStream(messages []string) ([]GapAnomaly, error) {
	var anomalies []GapAnomaly
	var prev *AISMessage
	
	for _, nmea := range messages {
		parsed, err := ParseAISMessage(nmea)
		if err != nil {
			continue // Skip malformed messages
		}
		
		if parsed == nil || !parsed.Timestamp.IsZero() {
			continue
		}
		
		if prev != nil && !prev.Timestamp.IsZero() {
			duration := float64(parsed.Timestamp.Sub(prev.Timestamp).Minutes())
			distance := CalculateDistanceNM(
				prev.Lat, prev.Lon, parsed.Lat, parsed.Lon)
			
			// Determine if this is a gap anomaly
			if duration > DefaultConfig.MaxNormalDeltaMin || 
			   distance > DefaultConfig.MaxNormalDistNMI {
				
				score := calculateGapScore(duration, distance)
				
				if score >= DefaultConfig.ScoreThreshold {
					anomalies = append(anomalies, GapAnomaly{
						Timestamp:       parsed.Timestamp,
						PreviousLat:     prev.Lat,
						PreviousLon:     prev.Lon,
						CurrentLat:      parsed.Lat,
						CurrentLon:      parsed.Lon,
						DurationMinutes: duration,
						DistanceNM:      distance,
						Score:           score,
					})
				}
			}
		}
		
		prev = parsed
	}
	
	return anomalies, nil
}

// calculateGapScore computes an anomaly score based on gap characteristics
func calculateGapScore(durationMinutes, distanceNM float64) int {
	var score int
	
	// Base score from duration
	if durationMinutes < 5 {
		score += 2 // Minor gap
	} else if durationMinutes < 30 {
		score += 5 // Moderate gap
	} else if durationMinutes < 60 {
		score += 8 // Significant gap
	} else {
		score += 12 // Major gap
	}
	
	// Add penalty for distance traveled during gap (unexpected jump)
	if distanceNM > DefaultConfig.MaxNormalDistNMI*3 {
		score += 4 // Large spatial jump
	} else if distanceNM > DefaultConfig.MaxNormalDistNMI {
		score += 2 // Moderate spatial jump
	}
	
	// Cap score at reasonable maximum
	if score > 20 {
		score = 20
	}
	
	return score
}

// FormatAnomalyOutput formats a GapAnomaly for display
func FormatAnomalyOutput(anomaly GapAnomaly) string {
	return fmt.Sprintf(
		"Gap Anomaly [Score: %d/20]\n"+
			"  Time: %s → %s\n"+
			"  Duration: %.1f min | Distance: %.3f NM\n"+
			"  Previous: %.4f°N, %.4f°E\n"+
			"  Current:  %.4f°N, %.4f°E",
		anomaly.Score,
		anomaly.Timestamp.Format("15:04:05"),
		anomaly.Timestamp.Add(time.Duration(anomaly.DurationMinutes*60)).Format("15:04:05"),
		anomaly.DurationMinutes,
		anomaly.DistanceNM,
		anomaly.PreviousLat, anomaly.PreviousLon,
		anomaly.CurrentLat, anomaly.CurrentLon,
	)
}

// RunDemo demonstrates the AIS stream parser with sample data
func RunDemo() {
	fmt.Println("=== AIS Stream Parser Demo ===\n")
	
	// Sample NMEA messages simulating a vessel track with gaps
	sampleMessages := []string{
		"$GPRMC,123456.78,A,37.8012345,N,122.456789,W,12.34,145.67,0.00,A,37.8012345,N,122.456789,W*6A",
		"$GPRMC,123457.12,A,37.8014567,N,122.4569012,W,12.35,145.68,0.00,A,37.8014567,N,122.4569012,W*6B",
		"$GPRMC,123458.45,A,37.8016789,N,122.4570123,W,12.36,145.69,0.00,A,37.8016789,N,122.4570123,W*6C",
		// Normal progression...
		"$GPRMC,123460.12,A,37.8023456,N,122.4575678,W,12.38,145.71,0.00,A,37.8023456,N,122.4575678,W*6D",
		"$GPRMC,123461.34,A,37.8025678,N,122.4577890,W,12.39,145.72,0.00,A,37.8025678,N,122.4577890,W*6E",
		// Gap introduced - vessel jumps forward in time
		"$GPRMC,123500.56,A,37.8056789,N,122.4612345,W,12.45,145.80,0.00,A,37.8056789,N,122.4612345,W*6F",
		"$GPRMC,123501.78,A,37.8058901,N,122.4614567,W,12.46,145.81,0.00,A,37.8058901,N,122.4614567,W*6G",
	}
	
	fmt.Println("Processing sample AIS stream...")
	anomalies, err := AnalyzeAISStream(sampleMessages)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}
	
	if len(anomalies) == 0 {
		fmt.Println("No significant gaps detected.")
	} else {
		fmt.Printf("\nDetected %d gap anomaly(ies):\n\n", len(anomalies))
		
		for i, a := range anomalies {
			fmt.Printf("--- Anomaly #%d ---\n", i+1)
			fmt.Println(FormatAnomalyOutput(a))
			
			// Severity classification
			if a.Score >= 15 {
				fmt.Println("Severity: HIGH - Investigate vessel trajectory")
			} else if a.Score >= 8 {
				fmt.Println("Severity: MEDIUM - Monitor closely")
			} else {
				fmt.Println("Severity: LOW - Normal variation")
			}
		}
		
		// Summary statistics
		totalDuration := float64(0)
		totalDistance := float64(0)
		for _, a := range anomalies {
			totalDuration += a.DurationMinutes
			totalDistance += a.DistanceNM
		}
		
		fmt.Printf("\n=== Summary ===\n")
		fmt.Printf("Total gap time: %.1f minutes\n", totalDuration)
		fmt.Printf("Total distance jumped: %.3f NM\n", totalDistance)
		fmt.Printf("Average score: %.1f/20\n", float64(anomalies[0].Score)) // Simplified for demo
	}
	
	// Demonstrate with a more obvious gap scenario
	fmt.Println("\n=== Test with Obvious Gap ===\n")
	
	obviousGapMessages := []string{
		"$GPRMC,123456.00,A,37.8000000,N,122.4500000,W,10.00,000.00,0.00,A*6A",
		"$GPRMC,123457.00,A,37.8001000,N,122.4501000,W,10.01,000.01,0.00,A*6B",
		"$GPRMC,123500.00,A,37.8050000,N,122.4550000,W,10.50,000.50,0.00,A*6