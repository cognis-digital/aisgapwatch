// Command aisgapwatch is a Go port of the aisgapwatch core CLI surface:
// detect & score AIS transponder-gap anomalies in vessel tracks (defensive
// maritime OSINT). It mirrors the Python reference — haversineNm, scoreGap,
// detectGaps — and the CLI's table / JSON output with the same pipeline-
// friendly exit codes (2 = gaps found, 0 = clean). Standard library only; no
// network access.
//
// Usage:
//
//	aisgapwatch FILE [-min-gap S] [-min-score X] [-top N] [-json]
//	aisgapwatch -            # read from stdin
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

const earthRadiusNM = 3440.065

// HaversineNM returns the great-circle distance between two lat/lon points in
// nautical miles.
func HaversineNM(lat1, lon1, lat2, lon2 float64) float64 {
	const d2r = math.Pi / 180.0
	rlat1 := lat1 * d2r
	rlat2 := lat2 * d2r
	dlat := (lat2 - lat1) * d2r
	dlon := (lon2 - lon1) * d2r
	a := math.Sin(dlat/2)*math.Sin(dlat/2) +
		math.Cos(rlat1)*math.Cos(rlat2)*math.Sin(dlon/2)*math.Sin(dlon/2)
	c := 2 * math.Asin(math.Min(1, math.Sqrt(a)))
	return earthRadiusNM * c
}

// ScoreConfig holds the saturation points and blend weights.
type ScoreConfig struct {
	DurationFullH     float64
	SpeedImpossibleKn float64
	DistanceFullNm    float64
	WDuration         float64
	WSpeed            float64
	WDistance         float64
}

// DefaultConfig matches the Python reference defaults.
func DefaultConfig() ScoreConfig {
	return ScoreConfig{
		DurationFullH: 6.0, SpeedImpossibleKn: 40.0, DistanceFullNm: 50.0,
		WDuration: 0.4, WSpeed: 0.4, WDistance: 0.2,
	}
}

func sat(value, full float64) float64 {
	if value <= 0 {
		return 0.0
	}
	return math.Min(1.0, value/full)
}

func round4(v float64) float64 { return math.Round(v*1e4) / 1e4 }
func round3(v float64) float64 { return math.Round(v*1e3) / 1e3 }

// ScoreGap returns (score, reasons) for a single gap.
func ScoreGap(durationS, distanceNm, impliedSpeedKn float64, cfg ScoreConfig) (float64, []string) {
	d := sat(durationS/3600.0, cfg.DurationFullH)
	s := sat(impliedSpeedKn, cfg.SpeedImpossibleKn)
	x := sat(distanceNm, cfg.DistanceFullNm)
	score := cfg.WDuration*d + cfg.WSpeed*s + cfg.WDistance*x
	reasons := []string{}
	if d >= 0.5 {
		reasons = append(reasons, fmt.Sprintf("long-duration-%.1fh", durationS/3600.0))
	}
	if s >= 0.5 {
		reasons = append(reasons, fmt.Sprintf("implied-speed-%.0fkn", impliedSpeedKn))
	}
	if x >= 0.5 {
		reasons = append(reasons, fmt.Sprintf("position-jump-%.0fnm", distanceNm))
	}
	return round4(math.Min(1.0, score)), reasons
}

// Ping is one AIS position report.
type Ping struct {
	TS   time.Time
	MMSI int
	Lat  float64
	Lon  float64
}

// Gap is a suspicious silence in a vessel's track.
type Gap struct {
	MMSI           int        `json:"mmsi"`
	Start          string     `json:"start"`
	End            string     `json:"end"`
	DurationS      float64    `json:"duration_s"`
	DurationH      float64    `json:"duration_h"`
	DistanceNm     float64    `json:"distance_nm"`
	ImpliedSpeedKn float64    `json:"implied_speed_kn"`
	StartPos       [2]float64 `json:"start_pos"`
	EndPos         [2]float64 `json:"end_pos"`
	Score          float64    `json:"score"`
	Reasons        []string   `json:"reasons"`
}

func isInt(s string) bool {
	_, err := strconv.Atoi(strings.TrimSpace(s))
	return err == nil
}

func parseTimestamp(raw string) (time.Time, error) {
	s := strings.TrimSpace(raw)
	if strings.HasSuffix(s, "Z") {
		return time.Parse(time.RFC3339, s)
	}
	// try with offset, else assume UTC
	if t, err := time.Parse(time.RFC3339, s); err == nil {
		return t, nil
	}
	return time.Parse("2006-01-02T15:04:05", s)
}

// ParseLine parses one "timestamp,mmsi,lat,lon" record.
func ParseLine(line string) (Ping, error) {
	parts := strings.Split(line, ",")
	if len(parts) < 4 {
		return Ping{}, fmt.Errorf("expected 4 fields, got %d", len(parts))
	}
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	ts, err := parseTimestamp(parts[0])
	if err != nil {
		return Ping{}, fmt.Errorf("bad timestamp: %s", parts[0])
	}
	mmsi, err := strconv.Atoi(parts[1])
	if err != nil {
		return Ping{}, fmt.Errorf("bad mmsi: %s", parts[1])
	}
	lat, err := strconv.ParseFloat(parts[2], 64)
	if err != nil {
		return Ping{}, fmt.Errorf("bad lat: %s", parts[2])
	}
	lon, err := strconv.ParseFloat(parts[3], 64)
	if err != nil {
		return Ping{}, fmt.Errorf("bad lon: %s", parts[3])
	}
	if lat < -90 || lat > 90 {
		return Ping{}, fmt.Errorf("latitude out of range: %v", lat)
	}
	if lon < -180 || lon > 180 {
		return Ping{}, fmt.Errorf("longitude out of range: %v", lon)
	}
	if mmsi <= 0 {
		return Ping{}, fmt.Errorf("mmsi must be positive: %d", mmsi)
	}
	return Ping{TS: ts.UTC(), MMSI: mmsi, Lat: lat, Lon: lon}, nil
}

// ParseText parses a blob of text into pings.
func ParseText(text string, strict bool) ([]Ping, error) {
	var pings []Ping
	seenData := false
	for i, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(strings.TrimRight(raw, "\r"))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if !seenData {
			fields := strings.Split(line, ",")
			if len(fields) >= 2 && !isInt(fields[1]) {
				continue // header
			}
		}
		p, err := ParseLine(line)
		if err != nil {
			if strict {
				return nil, fmt.Errorf("line %d: %v", i+1, err)
			}
			continue
		}
		pings = append(pings, p)
		seenData = true
	}
	return pings, nil
}

func impliedSpeedKn(distanceNm, durationS float64) float64 {
	hours := durationS / 3600.0
	if hours <= 0 {
		return 0.0
	}
	return distanceNm / hours
}

// DetectGaps groups pings by MMSI, orders each track, and returns scored gaps
// sorted by descending suspiciousness.
func DetectGaps(pings []Ping, minGapS, minScore float64, cfg ScoreConfig) ([]Gap, error) {
	if minGapS <= 0 {
		return nil, fmt.Errorf("minGapS must be positive")
	}
	byVessel := map[int][]Ping{}
	for _, p := range pings {
		byVessel[p.MMSI] = append(byVessel[p.MMSI], p)
	}
	var gaps []Gap
	for mmsi, track := range byVessel {
		sort.Slice(track, func(i, j int) bool { return track[i].TS.Before(track[j].TS) })
		for i := 1; i < len(track); i++ {
			prev, cur := track[i-1], track[i]
			durationS := cur.TS.Sub(prev.TS).Seconds()
			if durationS <= minGapS {
				continue
			}
			dist := HaversineNM(prev.Lat, prev.Lon, cur.Lat, cur.Lon)
			speed := impliedSpeedKn(dist, durationS)
			score, reasons := ScoreGap(durationS, dist, speed, cfg)
			if score < minScore {
				continue
			}
			gaps = append(gaps, Gap{
				MMSI: mmsi, Start: prev.TS.Format(time.RFC3339),
				End: cur.TS.Format(time.RFC3339), DurationS: durationS,
				DurationH: round3(durationS / 3600.0), DistanceNm: round3(dist),
				ImpliedSpeedKn: round3(speed),
				StartPos:       [2]float64{prev.Lat, prev.Lon},
				EndPos:         [2]float64{cur.Lat, cur.Lon},
				Score:          score, Reasons: reasons,
			})
		}
	}
	sort.SliceStable(gaps, func(i, j int) bool { return gaps[i].Score > gaps[j].Score })
	return gaps, nil
}

func formatTable(gaps []Gap) string {
	if len(gaps) == 0 {
		return "no gaps found"
	}
	rows := []string{
		"score  mmsi       duration  dist_nm  impl_kn  reasons",
		"-----  ---------  --------  -------  -------  -------",
	}
	for _, g := range gaps {
		reasons := strings.Join(g.Reasons, ",")
		if reasons == "" {
			reasons = "-"
		}
		rows = append(rows, fmt.Sprintf("%-5.2f  %-9d  %6.2fh  %7.1f  %7.1f  %s",
			g.Score, g.MMSI, g.DurationH, g.DistanceNm, g.ImpliedSpeedKn, reasons))
	}
	return strings.Join(rows, "\n")
}

func main() {
	minGap := flag.Float64("min-gap", 1800.0, "minimum gap length in seconds")
	minScore := flag.Float64("min-score", 0.0, "only report gaps scoring >= this")
	top := flag.Int("top", 0, "show only the top N gaps (0 = all)")
	asJSON := flag.Bool("json", false, "emit JSON instead of a table")
	lenient := flag.Bool("lenient", false, "skip malformed rows instead of failing")
	version := flag.Bool("version", false, "print version and exit")
	flag.Parse()

	if *version {
		fmt.Println("aisgapwatch 0.2.0 (go port)")
		return
	}
	args := flag.Args()
	if len(args) < 1 {
		fmt.Fprintln(os.Stderr, "usage: aisgapwatch FILE [-min-gap S] [-min-score X] [-top N] [-json]")
		os.Exit(1)
	}

	var data []byte
	var err error
	if args[0] == "-" {
		data, err = io.ReadAll(bufio.NewReader(os.Stdin))
	} else {
		data, err = os.ReadFile(args[0])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	pings, err := ParseText(string(data), !*lenient)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	gaps, err := DetectGaps(pings, *minGap, *minScore, DefaultConfig())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if *top > 0 && *top < len(gaps) {
		gaps = gaps[:*top]
	}

	if *asJSON {
		out := map[string]interface{}{"file": args[0], "pings": len(pings), "gaps": gaps}
		b, _ := json.MarshalIndent(out, "", "  ")
		fmt.Println(string(b))
	} else {
		fmt.Printf("%d pings · %d gap(s)\n\n", len(pings), len(gaps))
		fmt.Println(formatTable(gaps))
	}
	if len(gaps) > 0 {
		os.Exit(2)
	}
}
