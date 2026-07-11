import { AISTrackPoint, TrackContinuityResult, ContinuityConfig } from './types';

export const DEFAULT_CONFIG: ContinuityConfig = {
  maxTimeGapSeconds: 60 * 5, // 5 minutes between points before flagging as gap
  maxSpeedChangeKnots: 20,    // Max reasonable speed change per interval
  minSOGForValidPoint: 0.1,   // Minimum SOG to consider point valid (avoid static noise)
  positionJumpThresholdNauticalMiles: 5, // Max jump before flagging as anomaly
};

export function createContinuityChecker(config?: ContinuityConfig): TrackContinuityChecker {
  const cfg = { ...DEFAULT_CONFIG, ...(config || {}) };
  
  return {
    checkTrack,
    analyzeGap,
    calculateAnomalyScore,
  };
}

function createResult(
  points: AISTrackPoint[],
  gaps: GapInfo[],
  anomalies: AnomalyInfo[],
  summary: TrackSummary
): TrackContinuityResult {
  return {
    points,
    gaps,
    anomalies,
    summary,
    metadata: {
      checkedAt: new Date().toISOString(),
      configUsed: cfg,
      totalPoints: points.length,
    },
  };
}

function analyzeGap(
  prevPoint: AISTrackPoint | null,
  currPoint: AISTrackPoint,
  index: number
): GapInfo | undefined {
  if (!prevPoint) return undefined;
  
  const timeDiffSeconds = (currPoint.timestamp.getTime() - prevPoint.timestamp.getTime()) / 1000;
  
  // Check if gap exceeds threshold
  if (timeDiffSeconds > cfg.maxTimeGapSeconds) {
    return {
      index,
      prevIndex: index - 1,
      timeDeltaSeconds: timeDiffSeconds,
      isCritical: timeDiffSeconds > cfg.maxTimeGapSeconds * 2,
      estimatedDistanceTraveled: calculateEstimatedTravel(
        prevPoint,
        currPoint,
        timeDiffSeconds
      ),
    };
  }
  
  return undefined;
}

function analyzeSpeedChange(prevPoint: AISTrackPoint, currPoint: AISTrackPoint): AnomalyInfo | undefined {
  if (!prevPoint || !currPoint) return undefined;
  
  const speedDiff = Math.abs(currPoint.sog - prevPoint.sog);
  
  // Flag extreme speed changes
  if (speedDiff > cfg.maxSpeedChangeKnots) {
    return {
      type: 'SPEED_CHANGE',
      index: currPoint.index,
      severity: speedDiff / cfg.maxSpeedChangeKnots,
      details: {
        prevSpeed: prevPoint.sog,
        currentSpeed: currPoint.sog,
        change: speedDiff,
      },
    };
  }
  
  return undefined;
}

function analyzePositionJump(prevPoint: AISTrackPoint, currPoint: AISTrackPoint): AnomalyInfo | undefined {
  if (!prevPoint || !currPoint) return undefined;
  
  const distance = calculateDistance(prevPoint.lat, prevPoint.lon, currPoint.lat, currPoint.lon);
  
  // Flag impossible position jumps
  if (distance > cfg.positionJumpThresholdNauticalMiles) {
    const timeDiffHours = (currPoint.timestamp.getTime() - prevPoint.timestamp.getTime()) / (1000 * 3600);
    
    return {
      type: 'POSITION_JUMP',
      index: currPoint.index,
      severity: distance / cfg.positionJumpThresholdNauticalMiles,
      details: {
        previousPosition: `${prevPoint.lat.toFixed(4)}, ${prevPoint.lon.toFixed(4)}`,
        currentPosition: `${currPoint.lat.toFixed(4)}, ${currPoint.lon.toFixed(4)}`,
        distanceNauticalMiles: distance,
        timeDeltaHours: timeDiffHours,
        impliedSpeedKnots: timeDiffHours > 0 ? (distance / timeDiffHours).toFixed(2) : 'N/A',
      },
    };
  }
  
  return undefined;
}

function analyzeStaticNoise(point: AISTrackPoint): AnomalyInfo | undefined {
  if (!point.sog || point.sog < cfg.minSOGForValidPoint) {
    // Very low or zero SOG might indicate static noise or sensor issue
    if (point.sog === 0 || (point.sog && point.sog < 0.1)) {
      return {
        type: 'STATIC_NOISE',
        index: point.index,
        severity: 1 - (point.sog / cfg.minSOGForValidPoint), // Higher severity for lower SOG
        details: {
          sog: point.sog,
          note: 'Near-zero speed detected',
        },
      };
    }
  }
  
  return undefined;
}

function calculateEstimatedTravel(
  prevPoint: AISTrackPoint,
  currPoint: AISTrackPoint,
  timeDiffSeconds: number
): { distanceNauticalMiles: number; avgSpeedKnots: number } | null {
  if (timeDiffSeconds <= 0) return null;
  
  const distance = calculateDistance(prevPoint.lat, prevPoint.lon, currPoint.lat, currPoint.lon);
  const avgSpeed = timeDiffSeconds > 0 ? (distance / (timeDiffSeconds / 3600)) : 0;
  
  return {
    distanceNauticalMiles: distance,
    avgSpeedKnots: avgSpeed,
  };
}

function calculateDistance(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 3440.065; // Earth radius in nautical miles
  
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  
  const a = 
    Math.sin(dLat/2) * Math.sin(dLat/2) +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon/2) * Math.sin(dLon/2);
  
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  
  return R * c;
}

function calculateTrackSummary(points: AISTrackPoint[]): TrackSummary {
  if (points.length === 0) {
    return {
      totalPoints: 0,
      timeSpanHours: 0,
      avgSpeedKnots: 0,
      maxSpeedKnots: 0,
      minSpeedKnots: 0,
      distanceCoveredNauticalMiles: 0,
    };
  }
  
  const sorted = [...points].sort((a, b) => 
    a.timestamp.getTime() - b.timestamp.getTime()
  );
  
  const timeSpanHours = (sorted[sorted.length - 1].timestamp.getTime() - sorted[0].timestamp.getTime()) / (1000 * 3600);
  const avgSpeed = sorted.reduce((sum, p) => sum + p.sog || 0, 0) / sorted.length;
  
  return {
    totalPoints: points.length,
    timeSpanHours: Math.max(0, timeSpanHours),
    avgSpeedKnots: avgSpeed,
    maxSpeedKnots: Math.max(...sorted.map(p => p.sog || 0)),
    minSpeedKnots: Math.min(...sorted.map(p => p.sog || Infinity)),
    distanceCoveredNauticalMiles: calculateDistance(sorted[0].lat, sorted[0].lon, 
      sorted[sorted.length - 1].lat, sorted[sorted.length - 1].lon),
  };
}

function calculateAnomalyScore(
  result: TrackContinuityResult,
  baseWeight: number = 50
): number {
  let score = baseWeight;
  
  // Gap penalties (heavier for critical gaps)
  const gapPenalty = result.gaps.reduce((sum, g) => 
    sum + (g.isCritical ? 15 : 8), 0);
  score -= gapPenalty * 2;
  
  // Speed change penalties
  const speedChangePenalty = result.anomalies.filter(a => a.type === 'SPEED_CHANGE')
    .reduce((sum, a) => sum + (a.severity - 1) * 5, 0);
  score -= speedChangePenalty;
  
  // Position jump penalties (very heavy as they indicate major issues)
  const jumpPenalty = result.anomalies.filter(a => a.type === 'POSITION_JUMP')
    .reduce((sum, a) => sum + (a.severity - 1) * 20, 0);
  score -= jumpPenalty;
  
  // Static noise penalties
  const staticPenalty = result.anomalies.filter(a => a.type === 'STATIC_NOISE')
    .reduce((sum, a) => sum + (a.severity - 1) * 3, 0);
  score -= staticPenalty;
  
  return Math.max(0, Math.min(100, score)); // Clamp to 0-100
}

export interface GapInfo {
  index: number;
  prevIndex: number;
  timeDeltaSeconds: number;
  isCritical: boolean;
  estimatedDistanceTraveled?: number;
}

export interface AnomalyInfo {
  type: 'SPEED_CHANGE' | 'POSITION_JUMP' | 'STATIC_NOISE';
  index: number;
  severity: number; // 0-1 scale, higher = worse
  details: Record<string, any>;
}

export interface TrackSummary {
  totalPoints: number;
  timeSpanHours: number;
  avgSpeedKnots: number;
  maxSpeedKnots: number;
  minSpeedKnots: number;
  distanceCoveredNauticalMiles: number;
}

export interface TrackContinuityResult {
  points: AISTrackPoint[];
  gaps: GapInfo[];
  anomalies: AnomalyInfo[];
  summary: TrackSummary;
  metadata: {
    checkedAt: string;
    configUsed: ContinuityConfig;
    totalPoints: number;
  };
}

export interface ContinuityConfig {
  maxTimeGapSeconds?: number;
  maxSpeedChangeKnots?: number;
  minSOGForValidPoint?: number;
  positionJumpThresholdNauticalMiles?: number;
}

interface TrackContinuityChecker {
  checkTrack: (points: AISTrackPoint[], config?: ContinuityConfig) => TrackContinuityResult;
  analyzeGap: (prev: AISTrackPoint | null, curr: AISTrackPoint, index: number) => GapInfo | undefined;
  calculateAnomalyScore: (result: TrackContinuityResult, baseWeight?: number) => number;
}

// ============================================================================
// ENTRY POINT / DEMO
// ============================================================================

function createDemoData(): AISTrackPoint[] {
  const now = new Date();
  
  // Create a realistic vessel track with some anomalies for testing
  
  const baseLat = 35.0;
  const baseLon = -125.0;
  
  let lat = baseLat;
  let lon = baseLon;
  let sog = 15.0;
  let cog = 90.0; // Eastward
  
  const points: AISTrackPoint[] = [];
  
  for (let i = 0; i < 24; i++) {
    // Simulate movement with some noise and anomalies
    lat += Math.sin(i * 0.5) * 0.01 + 0.002;
    lon += Math.cos(i * 0.5) * 0.01 + 0.003;
    
    // Add realistic SOG variation
    sog = 14.0 + (Math.random() - 0.5) * 2.0;
    
    // Inject anomalies at specific points
    
    // Anomaly 1: Gap at point 8-9 (about 7 minutes gap)
    if (i === 8) {
      sog = 16.0;
    } else if (i === 9) {
      // Jump forward in time to simulate a 7-minute gap
      const baseTime = new Date(now.getTime() - (24 * i) * 60000);
      points.push({
        index: i,
        timestamp: new Date(baseTime.getTime() + 7 * 60000), // 7 minute jump!
        lat: parseFloat((lat).toFixed(5)),
        lon: parseFloat((lon).toFixed(5)),
        sog: sog,
        cog: 90.0,
      });
    } else {
      points.push({
        index: i,
        timestamp: new Date(now.getTime() - (24 * i) * 60000),
        lat: parseFloat((lat).toFixed(5)),
        lon: parseFloat((lon).toFixed(5)),
        sog: sog,
        cog: 90.0,
      });
    }
    
    // Anomaly 2: Speed spike at point 15
    if (i === 15) {
      sog = 35.0; // Unusual speed increase
    }
    
    // Anomaly 3: Position jump at point 20
    if (i === 20) {
      lat += 0.5; // Jump half a degree south!
    }
    
    // Anomaly 4: Static noise at point 22
    if (i === 22) {
      sog = 0.1; // Near-zero speed
    }
  }
  
  return points;
}

function runDemo(): void {
  console.log('='.repeat(60));
  console.log('AIS Track Continuity Checker - Demo');
  console.log('='.repeat(60));
  console.log();
  
  // Create demo data with known anomalies
  const demoPoints = createDemoData();
  
  // Check continuity with default config
  const checker = createContinuityChecker();
  const result = checker.checkTrack(demoPoints);
  
  // Calculate anomaly score
  const anomalyScore = checker.calculateAnomalyScore(result, 50);
  
  console.log('TRACK SUMMARY');
  console.log('-'.repeat(40));
  console.log(`Total Points:    ${result.summary.totalPoints}`);
  console.log(`Time Span:       ${(result.summary.timeSpanHours).toFixed(2)} hours`);
  console.log(`Avg Speed:       ${(result.summary.avgSpeedKnots).toFixed(1)} knots`);
  console.log(`Max Speed:       ${(result.summary.maxSpeedKnots).toFixed(1)} knots`);
  console.log(`Min Speed:       ${(result.summary.minSpeedKnots).toFixed(1)} knots`);
  console.log(`Distance:        ${(result.summary.distanceCoveredNauticalMiles).toFixed(2)} NM`);
  console.log();
  
  console.log('GAP ANALYSIS');
  console.log('-'.repeat(40));
  if (result.gaps.length === 0) {
    console.log('No significant gaps detected.');
  } else {
    result.gaps.forEach((gap, idx) => {
      const status = gap.isCritical ? '⚠️ CRITICAL' : '📍';
      console.log(`${idx + 1}. ${status} - Gap of ${(gap.timeDeltaSeconds).toFixed(0)} seconds`);
      if (gap.estimatedDistanceTraveled) {
        console.log(`   Estimated travel: ${gap.estimatedDistanceTraveled.toFixed(2)} NM @ ${(gap.estimatedDistanceTraveled / (gap.timeDeltaSeconds/3600)).toFixed(1)} knots`);
      }
    });
  }
  console.log();
  
  console.log('ANOMALY DETECTION');
  console.log('-'.repeat(40));
  if (result.anomalies.length === 0) {
    console.log('No anomalies detected.');
  } else {
    result.anomalies.forEach((anomaly, idx) => {
      const typeLabel = anomaly.type;
      const severityPct = (anomaly.severity * 100).toFixed(1);
      
      let detailText = '';
      switch (anomaly.type) {
        case 'SPEED_CHANGE':
          detailText = `Speed changed from ${anomaly.details.prevSpeed.toFixed(1)} to ${anomaly.details.currentSpeed.toFixed(1)} knots (${anomaly.details.change.toFixed(1)} knot change)`;
          break;
        case 'POSITION_JUMP':