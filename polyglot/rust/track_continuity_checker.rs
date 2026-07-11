use std::time::{SystemTime, Duration};
use std::cmp;

/// AIS message container with position and motion data.
#[derive(Debug, Clone)]
pub struct AisMessage {
    pub utc_time: SystemTime,
    pub latitude: f64,
    pub longitude: f64,
    pub speed_over_ground: f32, // knots
    pub true_heading: f32,       // degrees 0-359.999
    pub accuracy_flag: u8,        // 0 = high accuracy, non-zero = degraded
}

/// Configuration for the continuity checker.
#[derive(Debug, Clone)]
pub struct ContinuityConfig {
    /// Max time gap between messages (seconds). Default: 30s.
    pub max_time_gap: Duration,
    /// Max speed in knots. Default: 45 knots (typical fast vessel).
    pub max_speed_knots: f32,
    /// Min distance for a valid track segment (meters). Default: 100m.
    pub min_segment_distance: f64,
    /// Heading change threshold in degrees. Default: 45°.
    pub heading_change_threshold: f32,
}

impl Default for ContinuityConfig {
    fn default() -> Self {
        Self {
            max_time_gap: Duration::from_secs(30),
            max_speed_knots: 45.0,
            min_segment_distance: 100.0,
            heading_change_threshold: 45.0,
        }
    }
}

/// Severity level for detected anomalies.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum AnomalySeverity {
    /// Minor irregularity (e.g., small time gap).
    Minor,
    /// Moderate concern (requires investigation).
    Medium,
    /// Significant anomaly (likely spoofing or malfunction).
    Major,
}

/// A single detected anomaly with context.
#[derive(Debug, Clone)]
pub struct Anomaly {
    pub timestamp: SystemTime,
    pub severity: AnomalySeverity,
    pub anomaly_type: &'static str,
    pub description: String,
    pub previous_lat: f64,
    pub previous_lon: f64,
    pub current_lat: f64,
    pub current_lon: f64,
}

/// Result of processing a track segment.
#[derive(Debug, Clone)]
pub struct TrackResult {
    pub total_messages: usize,
    pub time_span: Duration,
    pub distance_traveled: f64, // meters
    pub avg_speed_knots: f32,
    pub anomalies: Vec<Anomaly>,
    pub is_continuous: bool,
}

/// Main continuity checker.
pub struct ContinuityChecker {
    config: ContinuityConfig,
    prev_message: Option<AisMessage>,
    segment_start_time: SystemTime,
    segment_distance: f64,
    segment_messages: usize,
    anomalies: Vec<Anomaly>,
}

impl ContinuityChecker {
    pub fn new(config: ContinuityConfig) -> Self {
        Self {
            config,
            prev_message: None,
            segment_start_time: SystemTime::now(),
            segment_distance: 0.0,
            segment_messages: 0,
            anomalies: Vec::new(),
        }
    }

    /// Reset the checker for a new track.
    pub fn reset(&mut self) {
        self.prev_message = None;
        self.segment_start_time = SystemTime::now();
        self.segment_distance = 0.0;
        self.segment_messages = 0;
        self.anomalies.clear();
    }

    /// Process a single AIS message and update state.
    pub fn process(&mut self, msg: AisMessage) {
        let now = SystemTime::now();
        
        // First message in segment - initialize
        if self.prev_message.is_none() {
            self.segment_start_time = msg.utc_time;
            self.segment_distance = 0.0;
            self.segment_messages = 1;
            self.prev_message = Some(msg);
            return;
        }

        let prev = self.prev_message.as_ref().unwrap();
        
        // Check time gap
        let delta = msg.utc_time.duration_since(prev.utc_time).unwrap_or(Duration::ZERO);
        
        if delta > self.config.max_time_gap {
            self.anomalies.push(Anomaly {
                timestamp: msg.utc_time,
                severity: AnomalySeverity::Medium,
                anomaly_type: "TIME_GAP",
                description: format!(
                    "Large time gap detected: {:.1}s (threshold: {}s)",
                    delta.as_secs_f64(),
                    self.config.max_time_gap.as_secs()
                ),
                previous_lat: prev.latitude,
                previous_lon: prev.longitude,
                current_lat: msg.latitude,
                current_lon: msg.longitude,
            });
        }

        // Check speed consistency
        if let Some(distance) = self.calculate_distance(&prev, &msg) {
            if distance > 0.0 {
                let time_delta_secs = delta.as_secs_f64();
                if time_delta_secs > 0.0 {
                    let speed_knots = (distance / 1852.0) / time_delta_secs; // meters to knots
                    
                    if speed_knots > self.config.max_speed_knots {
                        self.anomalies.push(Anomaly {
                            timestamp: msg.utc_time,
                            severity: AnomalySeverity::Major,
                            anomaly_type: "SPEED_SPIKE",
                            description: format!(
                                "Speed spike detected: {:.1} knots (threshold: {} knots)",
                                speed_knots, self.config.max_speed_knots
                            ),
                            previous_lat: prev.latitude,
                            previous_lon: prev.longitude,
                            current_lat: msg.latitude,
                            current_lon: msg.longitude,
                        });
                    }
                }

                // Check heading change
                let heading_diff = self.calculate_heading_difference(prev.true_heading, msg.true_heading);
                
                if heading_diff > self.config.heading_change_threshold {
                    self.anomalies.push(Anomaly {
                        timestamp: msg.utc_time,
                        severity: AnomalySeverity::Minor,
                        anomaly_type: "HEADING_JUMP",
                        description: format!(
                            "Heading jump detected: {:.1}° (threshold: {}°)",
                            heading_diff, self.config.heading_change_threshold
                        ),
                        previous_lat: prev.latitude,
                        previous_lon: prev.longitude,
                        current_lat: msg.latitude,
                        current_lon: msg.longitude,
                    });
                }

                // Check position accuracy degradation
                if msg.accuracy_flag > 0 {
                    self.anomalies.push(Anomaly {
                        timestamp: msg.utc_time,
                        severity: AnomalySeverity::Minor,
                        anomaly_type: "ACCURACY_DEGRADED",
                        description: format!(
                            "Position accuracy flag set (flag value: {})",
                            msg.accuracy_flag
                        ),
                        previous_lat: prev.latitude,
                        previous_lon: prev.longitude,
                        current_lat: msg.latitude,
                        current_lon: msg.longitude,
                    });
                }

                // Accumulate segment stats
                self.segment_distance += distance;
            }
        }

        self.prev_message = Some(msg);
        self.segment_messages += 1;
    }

    /// Calculate great-circle distance between two positions in meters.
    fn calculate_distance(&self, a: &AisMessage, b: &AisMessage) -> Option<f64> {
        let lat1 = a.latitude.to_radians();
        let lon1 = a.longitude.to_radians();
        let lat2 = b.latitude.to_radians();
        let lon2 = b.longitude.to_radians();

        let delta_lat = lat2 - lat1;
        let delta_lon = lon2 - lon1;

        // Haversine formula
        let a_val = (delta_lat / 2.0).powi(2) + 
                    lat1.cos() * lat2.cos() * ((delta_lon / 2.0).powi(2));
        
        if a_val > 0.9999 {
            return Some(6371000.0); // Near antipodal point
        }

        let c = 2.0 * (a_val.sqrt() + (1.0 - a_val).sqrt()).atan();
        
        if c > std::f64::consts::TAU {
            return Some(6371000.0); // Wrapped around
        }

        let distance = 6371000.0 * c;

        if distance < self.config.min_segment_distance || distance > 200000.0 {
            None // Filter out very short or very long jumps (likely errors)
        } else {
            Some(distance)
        }
    }

    /// Calculate the minimum heading difference accounting for wraparound.
    fn calculate_heading_difference(&self, h1: f32, h2: f32) -> f32 {
        let diff = (h2 - h1).abs();
        if diff > 180.0 {
            360.0 - diff
        } else {
            diff
        }
    }

    /// Finalize the current segment and return results.
    pub fn finalize(self) -> TrackResult {
        let total_time = self.segment_start_time.elapsed().unwrap_or(Duration::ZERO);
        
        // Calculate average speed
        let avg_speed_knots = if total_time.as_secs_f64() > 0.0 {
            (self.segment_distance / 1852.0) / total_time.as_secs_f64()
        } else {
            0.0
        };

        // Determine continuity status
        let is_continuous = self.anomalies.len() < 3 && 
                          avg_speed_knots <= self.config.max_speed_knots * 1.5;

        TrackResult {
            total_messages: self.segment_messages,
            time_span: total_time,
            distance_traveled: self.segment_distance,
            avg_speed_knots,
            anomalies: self.anomalies,
            is_continuous,
        }
    }
}

/// Convenience function to process a full track.
pub fn analyze_track(messages: &[AisMessage], config: ContinuityConfig) -> TrackResult {
    let mut checker = ContinuityChecker::new(config);
    
    for msg in messages.iter() {
        checker.process(msg.clone());
    }

    checker.finalize()
}

/// Convenience function to process a track with default config.
pub fn analyze_track_default(messages: &[AisMessage]) -> TrackResult {
    analyze_track(messages, ContinuityConfig::default())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_haversine_distance() {
        let msg1 = AisMessage {
            utc_time: SystemTime::now(),
            latitude: 51.5074, // London
            longitude: -0.1278,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        let msg2 = AisMessage {
            utc_time: SystemTime::now(),
            latitude: 51.5174,
            longitude: -0.1378,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        let distance = ContinuityChecker::calculate_distance(&msg1, &msg2).unwrap();
        
        // Rough estimate: ~1.1 km
        assert!(distance > 1000.0 && distance < 2000.0);
    }

    #[test]
    fn test_heading_difference() {
        let diff = ContinuityChecker::calculate_heading_difference(90.0, 350.0);
        assert!((diff - 160.0).abs() < 0.01); // Should be ~160° (shorter arc)

        let diff2 = ContinuityChecker::calculate_heading_difference(90.0, 270.0);
        assert!((diff2 - 180.0).abs() < 0.01); // Exactly 180°
    }

    #[test]
    fn test_time_gap_detection() {
        let now = SystemTime::now();
        
        let msg1 = AisMessage {
            utc_time: now,
            latitude: 51.5074,
            longitude: -0.1278,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        let msg2 = AisMessage {
            utc_time: now + Duration::from_secs(60), // 60 second gap
            latitude: 51.5074,
            longitude: -0.1278,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        let config = ContinuityConfig {
            max_time_gap: Duration::from_secs(30),
            ..Default::default()
        };

        let mut checker = ContinuityChecker::new(config);
        checker.process(msg1);
        checker.process(msg2);
        
        let result = checker.finalize();
        assert_eq!(result.anomalies.len(), 1);
        assert_eq!(result.anomalies[0].anomaly_type, "TIME_GAP");
    }

    #[test]
    fn test_speed_spike_detection() {
        let now = SystemTime::now();
        
        let msg1 = AisMessage {
            utc_time: now,
            latitude: 51.5074,
            longitude: -0.1278,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        // Move ~30 km in 60 seconds = 300 knots (impossible!)
        let msg2 = AisMessage {
            utc_time: now + Duration::from_secs(60),
            latitude: 51.5074 + 0.0028, // ~30 km north
            longitude: -0.1278,
            speed_over_ground: 10.0,
            true_heading: 90.0,
            accuracy_flag: 0,
        };

        let config = ContinuityConfig {
            max_speed_knots: 45.0,
            ..Default::default()
        };

        let mut checker = ContinuityChecker::new(config);
        checker.process(msg1);
        checker.process(msg2);
        
        let result = checker.finalize();
        assert!(result.anomalies.iter().any(|a| a.anomaly_type == "SPEED_SPIKE"));
    }

    #[test]
    fn test_continuous_track() {
        let now = SystemTime::now();
        
        // Create 5 messages over 10 minutes, ~2 knots average speed
        let mut checker = ContinuityChecker::new(ContinuityConfig::default());
        
        for i in 0..5 {
            let elapsed = Duration::from_secs(i * 120); // every 2 minutes
            let lat_offset = (i as f64) / 3.0; // ~18 km total, ~3.6 knots avg
            
            checker.process(AisMessage {
                utc_time: now + elapsed,
                latitude: 51.5074 + lat_offset,
                longitude: -0.1278,
                speed_over_ground: (i as f32) / 5.0, // 0-1 knots
                true_heading: 90.0,
                accuracy_flag: 0,
            });
        }

        let result = checker.finalize();
        
        assert!(result.is_continuous);
        assert_eq!(result.total_messages, 5);
        assert!(result.distance_traveled > 15000.0 && result.distance_traveled < 20000.0);
    }

    #[test]
    fn test_default_config() {
        let messages = vec![
            AisMessage {
                utc_time: SystemTime::now(),
                latitude: 51.5074,
                longitude: -0.1278,
                speed_over_ground: 10.0,
                true_heading: 90.0,
                accuracy_flag: 0,
            },
        ];

        let result = analyze_track_default(&messages);
        
        assert_eq!(result.total_messages, 1);
        assert!(result.is_continuous);
    }
}

///