package main

import (
	"math"
	"testing"
)

func TestHaversineEquatorDegree(t *testing.T) {
	d := HaversineNM(0, 0, 0, 1)
	if math.Abs(d-60.0) > 0.2 {
		t.Fatalf("expected ~60nm, got %v", d)
	}
}

func TestHaversineZeroAndSymmetry(t *testing.T) {
	if HaversineNM(10, 20, 10, 20) > 1e-6 {
		t.Fatal("zero distance expected")
	}
	if math.Abs(HaversineNM(1, 2, 3, 4)-HaversineNM(3, 4, 1, 2)) > 1e-9 {
		t.Fatal("haversine should be symmetric")
	}
}

func TestScoreImpossibleSpeed(t *testing.T) {
	score, reasons := ScoreGap(600, 20, 120, DefaultConfig())
	if score <= 0 || score > 1 {
		t.Fatalf("score out of bounds: %v", score)
	}
	found := false
	for _, r := range reasons {
		if len(r) >= 13 && r[:13] == "implied-speed" {
			found = true
		}
	}
	if !found {
		t.Fatal("expected implied-speed reason")
	}
}

func TestScoreQuietGapLow(t *testing.T) {
	score, reasons := ScoreGap(300, 0.5, 6, DefaultConfig())
	if score >= 0.2 {
		t.Fatalf("expected low score, got %v", score)
	}
	if len(reasons) != 0 {
		t.Fatal("expected no reasons")
	}
}

func TestScoreAllMax(t *testing.T) {
	score, reasons := ScoreGap(6*3600, 50, 40, DefaultConfig())
	if math.Abs(score-1.0) > 1e-9 {
		t.Fatalf("expected 1.0, got %v", score)
	}
	if len(reasons) != 3 {
		t.Fatalf("expected 3 reasons, got %d", len(reasons))
	}
}

func TestParseHeaderCommentBlank(t *testing.T) {
	pings, err := ParseText("timestamp,mmsi,lat,lon\n# c\n\n2026-06-01T00:00:00Z,1,0,0\n2026-06-01T01:00:00Z,2,1,1\n", true)
	if err != nil {
		t.Fatal(err)
	}
	if len(pings) != 2 {
		t.Fatalf("expected 2 pings, got %d", len(pings))
	}
}

func TestParseStrictRejectsGarbage(t *testing.T) {
	_, err := ParseText("2026-06-01T00:00:00Z,1,0,0\nGARBAGE,x,y,z\n", true)
	if err == nil {
		t.Fatal("expected error on garbage row")
	}
}

func TestParseLenientSkips(t *testing.T) {
	pings, err := ParseText("2026-06-01T00:00:00Z,1,0,0\njunk\n", false)
	if err != nil {
		t.Fatal(err)
	}
	if len(pings) != 1 {
		t.Fatalf("expected 1 ping, got %d", len(pings))
	}
}

func TestParseRejectsBadCoords(t *testing.T) {
	_, err := ParseLine("2026-06-01T00:00:00Z,1,200,0")
	if err == nil {
		t.Fatal("expected out-of-range latitude error")
	}
}

func TestDetectFindsGap(t *testing.T) {
	pings, _ := ParseText("2026-06-01T00:00:00Z,1,37.0,-122.0\n2026-06-01T05:00:00Z,1,37.5,-122.5\n", true)
	gaps, err := DetectGaps(pings, 1800, 0, DefaultConfig())
	if err != nil {
		t.Fatal(err)
	}
	if len(gaps) != 1 {
		t.Fatalf("expected 1 gap, got %d", len(gaps))
	}
	if math.Abs(gaps[0].DurationH-5.0) > 1e-6 {
		t.Fatalf("expected 5h, got %v", gaps[0].DurationH)
	}
}

func TestDetectIgnoresShort(t *testing.T) {
	pings, _ := ParseText("2026-06-01T00:00:00Z,1,37.0,-122.0\n2026-06-01T00:10:00Z,1,37.0,-122.01\n", true)
	gaps, _ := DetectGaps(pings, 1800, 0, DefaultConfig())
	if len(gaps) != 0 {
		t.Fatalf("expected 0 gaps, got %d", len(gaps))
	}
}

func TestDetectSortsByScore(t *testing.T) {
	pings, _ := ParseText("2026-06-01T00:00:00Z,1,0,0\n2026-06-01T01:00:00Z,1,5,5\n2026-06-01T00:00:00Z,2,0,0\n2026-06-01T02:00:00Z,2,0.05,0.05\n", true)
	gaps, _ := DetectGaps(pings, 1800, 0, DefaultConfig())
	if len(gaps) < 2 {
		t.Fatalf("expected 2 gaps, got %d", len(gaps))
	}
	if gaps[0].Score < gaps[len(gaps)-1].Score {
		t.Fatal("gaps not sorted by descending score")
	}
}

func TestDetectMinGapPositive(t *testing.T) {
	_, err := DetectGaps(nil, 0, 0, DefaultConfig())
	if err == nil {
		t.Fatal("expected error for non-positive minGapS")
	}
}

func TestDetectMinScoreFilters(t *testing.T) {
	pings, _ := ParseText("2026-06-01T00:00:00Z,1,0,0\n2026-06-01T00:35:00Z,1,0,0.001\n", true)
	gaps, _ := DetectGaps(pings, 1800, 0.9, DefaultConfig())
	if len(gaps) != 0 {
		t.Fatalf("expected 0 gaps after min-score filter, got %d", len(gaps))
	}
}
