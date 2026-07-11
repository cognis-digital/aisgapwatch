package main

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
)

// AISPosition represents a single position fix from an AIS transponder
type AISPosition struct {
	Timestamp   time.Time
	Latitude    float64
	Longitude   float64
	SOG         float64 // Speed Over Ground in knots
	COG         float64 // Course Over Ground in degrees
	MMSI        uint32  // Maritime Mobile Service Identity
	Accuracy    byte    // Position accuracy flag (0-7)
	Rollover    bool    // Timestamp rollover indicator
}

// AISMessage represents a parsed NMEA AIS message
type AISMessage struct {
	Type       int     // Message type (1, 2, 3, etc.)
	Position   AISPosition
	Sentence   string
	ParsedTime time.Time
}

// GapSeverity defines the severity level of a detected gap
type GapSeverity int

const (
	SeverityNormal GapSeverity = iota
	SeverityWarning
	SeverityCritical
	SeverityEmergency
)

var severityNames = map[GapSeverity]string{
	SeverityNormal:   "NORMAL",
	SeverityWarning:  "WARNING",
	SeverityCritical: "CRITICAL",
	SeverityEmergency: "EMERGENCY",
}

// GapReport represents a detected continuity gap
type GapReport struct {
	MMSI          uint32
	StartIndex    int
	EndIndex      int
	GapStartPos   AISPosition
	GapEndPos     AISPosition
	DurationSecs  float64
	DistanceNM    float64
	ExpectedSpeed float64 // Expected speed based on prior track
	ActualSOG     float64  // Speed at gap start
	Severity       GapSeverity
	Score          float64 // 0-100, higher = worse
}

// Config holds configuration for the continuity checker
type Config struct {
	TimeWindowSecs    float64   // Max time between fixes (default: 3 min)
	DistanceThresholdNM float64 // Min distance to consider a gap (default: 5 NM)
	SpeedToleranceKts float64  // Speed tolerance for expected velocity (default: 20 kts)
	MinFixesForScore  int       // Minimum fixes needed before scoring (default: 3)
}

// DefaultConfig returns sensible defaults
func DefaultConfig() Config {
	return Config{
		TimeWindowSecs:    180.0,      // 3 minutes
		DistanceThresholdNM: 5.0,       // 5 nautical miles
		SpeedToleranceKts: 20.0,        // 20 knots
		MinFixesForScore:   3,          // Need at least 3 fixes
	}
}

// NewContinuityChecker creates a new checker instance
func NewContinuityChecker(cfg Config) *ContinuityChecker {
	if cfg.TimeWindowSecs <= 0 {
		cfg.TimeWindowSecs = DefaultConfig().TimeWindowSecs
	}
	if cfg.DistanceThresholdNM <= 0 {
		cfg.DistanceThresholdNM = DefaultConfig().DistanceThresholdNM
	}
	return &ContinuityChecker{
		Config:    cfg,
		GapsFound: []GapReport{},
		LastPos:   AISPosition{},
	}
}

// ContinuityChecker handles the core continuity checking logic
type ContinuityChecker struct {
	Config      Config
	GapsFound   []GapReport
	LastPos     AISPosition
	TotalFixes  int
	TotalGaps   int
}

// AddPosition adds a new position fix to be analyzed
func (c *ContinuityChecker) AddPosition(pos AISPosition) {
	c.TotalFixes++
	
	if c.LastPos.MMSI == 0 && pos.MMSI != 0 {
		c.LastPos = pos
		return
	}

	if pos.MMSI != c.LastPos.MMSI {
		// New vessel - reset tracking
		c.LastPos = pos
		return
	}

	// Calculate time delta in seconds
	timeDelta := pos.Timestamp.Sub(c.LastPos.Timestamp)
	
	if timeDelta < 0 {
		// Timestamp rollover or out-of-order data
		timeDelta = time.Duration(math.MaxInt64)*time.Second
		c.LastPos.Rollover = true
	}

	// Calculate distance between fixes
	dist := c.calculateDistance(c.LastPos, pos)

	// Check if this exceeds our thresholds
	if timeDelta.Seconds() > c.Config.TimeWindowSecs || 
	   dist > c.Config.DistanceThresholdNM {
		
		gapReport := c.createGapReport(pos, timeDelta, dist)
		c.GapsFound = append(c.GapsFound, gapReport)
		c.TotalGaps++
	}

	c.LastPos = pos
}

// calculateDistance computes great circle distance between two positions
func (c *ContinuityChecker) calculateDistance(pos1, pos2 AISPosition) float64 {
	if pos1.Latitude == 0 && pos1.Longitude == 0 {
		return 0.0
	}

	lat1 := math.Radians(pos1.Latitude)
	lat2 := math.Radians(pos2.Latitude)
	dLat := lat2 - lat1
	dLon := math.Radians(pos2.Longitude - pos1.Longitude)

	a := math.Sin(dLat/2)*math.Sin(dLat/2) + 
	     math.Cos(lat1)*math.Cos(lat2)*
	     math.Sin(dLon/2)*math.Sin(dLon/2)
	c = 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))

	// Earth radius in nautical miles
	return 3440.065 * c
}

// createGapReport builds a detailed report for a detected gap
func (c *ContinuityChecker) createGapReport(pos AISPosition, 
	timeDelta time.Duration, dist float64) GapReport {
	
	gapStart := pos.Timestamp.Add(-timeDelta)
	
	// Estimate expected speed from previous track segment
	expectedSpeed := 10.0 // Default fallback
	
	if c.LastPos.SOG > 0 && c.LastPos.SOG < 300 {
		expectedSpeed = c.LastPos.SOG
	}

	severity, score := c.determineSeverity(timeDelta, dist, expectedSpeed)

	return GapReport{
		MMSI:          pos.MMSI,
		StartIndex:    c.TotalFixes - 1,
		EndIndex:      c.TotalFixes,
		GapStartPos:   AISPosition{Timestamp: gapStart}, // Approximate
		GapEndPos:     pos,
		DurationSecs:  timeDelta.Seconds(),
		DistanceNM:    dist,
		ExpectedSpeed: expectedSpeed,
		ActualSOG:     pos.SOG,
		Severity:      severity,
		Score:         score,
	}
}

// determineSeverity assigns a severity level and score to the gap
func (c *ContinuityChecker) determineSeverity(timeDelta time.Duration, 
	dist float64, expectedSpeed float64) (GapSeverity, float64) {
	
	score := 0.0
	
	// Base score from distance
	distanceScore := math.Min(dist/5.0, 30.0) // Cap at 30 points
	
	// Time component - longer gaps are worse
	timeScore := math.Min(timeDelta.Seconds()/60.0, 20.0) / 180.0 * 20.0

	// Speed anomaly detection
	speedVariance := expectedSpeed - pos.SOG
	if speedVariance > 5 { // Significant speed change
		timeScore += 10.0
	}

	score = distanceScore + timeScore
	if score > 40 {
		return SeverityCritical, math.Min(score, 100)
	} else if score > 20 {
		return SeverityWarning, math.Min(score, 100)
	}
	
	return SeverityNormal, math.Min(score, 100)
}

// GetSummary returns a summary of all detected gaps
func (c *ContinuityChecker) GetSummary() string {
	if len(c.GapsFound) == 0 {
		return "No significant gaps detected"
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("Track Continuity Summary\n"))
	sb.WriteString(strings.Repeat("-", 40))
	sb.WriteString(fmt.Sprintf("Total Fixes Analyzed: %d\n", c.TotalFixes))
	sb.WriteString(fmt.Sprintf("Gaps Detected: %d\n", len(c.GapsFound)))

	if len(c.GapsFound) > 0 {
		sb.WriteString("\nGap Details:\n")
		sb.WriteString(strings.Repeat("-", 40))
		
		for i, gap := range c.GapsFound {
			sb.WriteString(fmt.Sprintf("Gap #%d:\n", i+1))
			sb.WriteString(fmt.Sprintf("  MMSI: %d\n", gap.MMSI))
			sb.WriteString(fmt.Sprintf("  Duration: %.2f seconds (%.2f minutes)\n", 
				gap.DurationSecs, gap.DurationSecs/60.0))
			sb.WriteString(fmt.Sprintf("  Distance: %.3f NM\n", gap.DistanceNM))
			sb.WriteString(fmt.Sprintf("  Severity: %s (Score: %.1f/100)\n", 
				severityNames[gap.Severity], gap.Score))
			
			if gap.ActualSOG > 0 {
				sb.WriteString(fmt.Sprintf("  Actual SOG: %.2f knots\n", gap.ActualSOG))
			}
		}
	}

	return sb.String()
}

// GetJSONSummary returns the summary as JSON for programmatic use
func (c *ContinuityChecker) GetJSONSummary() (string, error) {
	type GapSummary struct {
		MMSI          uint32    `json:"mmsi"`
		DurationSecs  float64   `json:"duration_seconds"`
		DistanceNM    float64   `json:"distance_nm"`
		Severity      string    `json:"severity"`
		Score         float64   `json:"score"`
	}

	type Result struct {
		TotalFixes  int             `json:"total_fixes"`
		TotalGaps   int             `json:"total_gaps"`
		Gaps        []GapSummary    `json:"gaps,omitempty"`
	}

	var gaps []GapSummary
	for _, g := range c.GapsFound {
		gaps = append(gaps, GapSummary{
			MMSI:          g.MMSI,
			DurationSecs:  g.DurationSecs,
			DistanceNM:    g.DistanceNM,
			Severity:      severityNames[g.Severity],
			Score:         g.Score,
		})
	}

	result := Result{
		TotalFixes: c.TotalFixes,
		TotalGaps:  len(c.GapsFound),
		Gaps:       gaps,
	}

	return json.MarshalIndent(result, "", "  ")
}

// ParseNMEASentence parses a NMEA AIS sentence into an AISPosition
func ParseNMEASentence(sentence string) (*AISPosition, error) {
	parts := strings.Split(strings.TrimSpace(sentence), ",")
	if len(parts) < 7 {
		return nil, fmt.Errorf("insufficient fields in NMEA sentence: %s", sentence)
	}

	var pos AISPosition
	
	// Parse timestamp (UTC time HHMMSS.SSS)
	timeStr := parts[1]
	pos.Timestamp = parseNMEATime(timeStr)
	
	if pos.Timestamp.IsZero() {
		return nil, fmt.Errorf("invalid or missing timestamp: %s", sentence)
	}

	// Parse latitude and longitude (DDMM.MMMM,DDDMM.MMMM format)
	latStr := parts[2]
	lonStr := parts[3]

	pos.Latitude = parseNMEALat(latStr)
	pos.Longitude = parseNMEA_lon(lonStr)

	if pos.Latitude == 0 && pos.Longitude == 0 {
		return nil, fmt.Errorf("invalid or missing position: %s", sentence)
	}

	// Parse speed over ground (knots)
	sogStr := parts[4]
	pos.SOG, _ = strconv.ParseFloat(sogStr, 64)

	// Parse course over ground (degrees)
	cogStr := parts[5]
	pos.COg, _ = strconv.ParseFloat(cogStr, 64)

	// Parse position accuracy flag (0-7)
	if len(parts) > 6 {
		accByte, err := strconv.Atoi(parts[6])
		if err == nil && accByte >= 0 && accByte <= 7 {
			pos.Accuracy = byte(accByte)
		}
	}

	return &pos, nil
}

// parseNMEATime converts NMEA time string to Go time.Time
func parseNMEATime(timeStr string) time.Time {
	if len(timeStr) < 6 {
		return time.Unix(0, 0)
	}

	hours := int(timeStr[0]-'0')*10 + int(timeStr[1]-'0')
	minutes := int(timeStr[2]-'0')*10 + int(timeStr[3]-'0')
	seconds := float64(int(timeStr[4]-'0')) * 10.0 + 
	          float64(int(timeStr[5]-'0'))

	return time.Date(2024, 1, 1, hours, minutes, int(seconds), 0, time.UTC)
}

// parseNMEALat converts NMEA latitude string to decimal degrees
func parseNMEALat(latStr string) float64 {
	if len(latStr) < 5 || latStr[2] == 'S' {
		return 0.0
	}

	minutes := int(float64(latStr[3]-'0')*10 + float64(latStr[4]-'0'))
	degrees, _ := strconv.ParseFloat(latStr[:2], 64)

	return degrees + (float64(minutes) / 60.0)
}

// parseNMEA_lon converts NMEA longitude string to decimal degrees
func parseNMEA_lon(lonStr string) float64 {
	if len(lonStr) < 5 || lonStr[2] == 'W' {
		return 0.0
	}

	minutes := int(float64(lonStr[3]-'0')*10 + float64(lonStr[4]-'0'))
	degrees, _ := strconv.ParseFloat(lonStr[:2], 64)

	return degrees + (float64(minutes) / 60.0)
}

// ParseAISBinaryMessage parses a binary AIS message type 18 (position report)
func ParseAISBinaryMessage(msgType int, mmsi uint32, data []byte) (*AISPosition, error) {
	if msgType != 18 && msgType != 19 { // Types 18/19 are position reports
		return nil, fmt.Errorf("expected message type 18 or 19, got %d", msgType)
	}

	pos := &AISPosition{
		MMSI:   mmsi,
		Type:   msgType,
	}

	if len(data) < 24 {
		return nil, fmt.Errorf("insufficient data for binary message")
	}

	// Extract fields from binary (simplified - full implementation would use proper bit manipulation)
	pos.Latitude = float64(int16(data[0])<<8 | int(data[1])) / 1e7.0
	pos.Longitude = float64(int16(data[2])<<8 | int(data[3])) / 1e7.0

	// Convert from relative to absolute coordinates (simplified)
	if pos.Latitude < -90 {
		pos.Latitude += 180
	}
	if pos.Longitude < -180 {
		pos.Longitude += 360
	}

	return pos, nil
}

// ProcessNMEASentences processes a slice of NMEA sentences and returns all positions
func ProcessNMEASentences(sentences []string) ([]AISPosition, error) {
	var positions []AISPosition
	
	for _, sentence := range sentences {
		pos, err := ParseNMEASentence(sentence)
		if err != nil {
			continue // Skip malformed sentences
		}
		
		if pos != nil && !pos.Timestamp.IsZero() {
			positions = append(positions, *pos)
		}
	}

	return positions, nil
}

// ProcessBinaryMessages processes binary AIS messages from a stream
func ProcessBinaryMessages(messages []struct{
	Type int
	MMSI uint32
	Data []byte
}) ([]AISPosition, error) {
	var positions []AISPosition
	
	for _, msg := range messages {
		pos, err := ParseAISBinary