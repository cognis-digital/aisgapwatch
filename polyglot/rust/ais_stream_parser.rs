use std::time::{Duration, SystemTime, UNIX_EPOCH};
use std::collections::HashMap;

// ============================================================================
// CONSTANTS & CONFIGURATION
// ============================================================================

pub const DEFAULT_GAP_THRESHOLD_SECONDS: f64 = 300.0; // 5 minutes default
pub const MIN_MESSAGES_FOR_TRACK: usize = 2;
pub const MAX_MMSI_LENGTH: usize = 9;

// Risk score thresholds (higher = more suspicious)
pub struct GapThresholds {
    pub warning_score: f64,      // e.g., 30-50 seconds gap
    pub high_risk_score: f64,    // e.g., 2-5 minutes gap  
    pub critical_score: f64,     // e.g., 10+ minutes gap
}

impl Default for GapThresholds {
    fn default() -> Self {
        Self {
            warning_score: 30.0,
            high_risk_score: 300.0,
            critical_score: 600.0,
        }
    }
}

// ============================================================================
// DATA STRUCTURES
// ============================================================================

#[derive(Debug, Clone)]
pub struct AISTime {
    pub seconds_since_epoch: f64,
    pub utc_hour: u8,
    pub utc_minute: u8,
    pub utc_second: u8,
}

impl AISTime {
    pub fn from_timestamp(ts: f64) -> Self {
        let secs = ts as i64;
        let millis = ((ts - secs as f64) * 1000.0).round() as u32;
        
        let hour = (secs / 3600) % 24;
        let minute = (secs / 60) % 60;
        let second = secs % 60;
        
        Self {
            seconds_since_epoch: ts,
            utc_hour: hour as u8,
            utc_minute: minute as u8,
            utc_second: second as u8,
        }
    }

    pub fn from_nmea_time(hour: u8, minute: u8, second: u8) -> Self {
        let base = SystemTime::UNIX_EPOCH + Duration::from_secs(1970); // 1970-01-01 00:00:00 UTC
        let total_seconds = (hour as i64 * 3600) + (minute as i64 * 60) + second as i64;
        
        Self {
            seconds_since_epoch: base.duration_since(UNIX_EPOCH).unwrap().as_secs_f64() + total_seconds as f64,
            utc_hour: hour,
            utc_minute: minute,
            utc_second: second,
        }
    }

    pub fn duration_since(&self, other: &Self) -> Duration {
        let delta = self.seconds_since_epoch - other.seconds_since_epoch;
        if delta < 0.0 {
            Duration::from_secs((delta * -1.0).round() as i64)
        } else {
            Duration::from_secs(delta.round() as i64)
        }
    }

    pub fn is_valid(&self) -> bool {
        self.utc_hour < 24 && 
        self.utc_minute < 60 && 
        self.utc_second < 60 &&
        (self.seconds_since_epoch - UNIX_EPOCH.as_secs_f64()).abs() < 86400.0 * 365.0 * 10.0 // 10 years
    }

    pub fn is_future(&self, now: f64) -> bool {
        self.seconds_since_epoch > now + 60.0
    }

    pub fn is_past(&self, now: f64) -> bool {
        self.seconds_since_epoch < now - 60.0
    }
}

#[derive(Debug, Clone)]
pub struct AISPosition {
    pub latitude: f32,      // Degrees (positive = North)
    pub longitude: f32,     // Degrees (positive = East)
    pub speed_over_ground: f32, // Knots
    pub course_over_ground: u8, // 0-359 degrees
    pub heading: u8,         // 0-359 degrees
}

impl Default for AISPosition {
    fn default() -> Self {
        Self {
            latitude: 0.0,
            longitude: 0.0,
            speed_over_ground: 0.0,
            course_over_ground: 0,
            heading: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct AISMessage {
    pub mmsi: u32,
    pub msg_type: u8,       // 1-9 for Class A/B
    pub timestamp: AISTime,
    pub position: AISPosition,
    pub nav_status: u8,     // 0 = underway, 1 = anchor, etc.
    pub rate_of_turn: f32,  // Degrees per hour
    pub spare_bits: u8,
}

impl Default for AISMessage {
    fn default() -> Self {
        Self {
            mmsi: 0,
            msg_type: 1,
            timestamp: AISTime::default(),
            position: AISPosition::default(),
            nav_status: 0,
            rate_of_turn: 0.0,
            spare_bits: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct GapAnomaly {
    pub vessel_mmsi: u32,
    pub gap_start_time: AISTime,
    pub gap_end_time: AISTime,
    pub gap_duration_seconds: f64,
    pub last_known_position: AISPosition,
    pub expected_next_position: AISPosition,
    pub risk_score: f64,
    pub anomaly_type: GapType,
}

#[derive(Debug, Clone, Copy)]
pub enum GapType {
    NormalGap,      // Within normal range
    WarningGap,     // Slightly elevated
    HighRiskGap,    // Significant gap
    CriticalGap,    // Major anomaly
    UnknownSource,  // Vessel appeared from nowhere
}

#[derive(Debug, Clone)]
pub struct Track {
    pub mmsi: u32,
    pub vessel_name: String,
    pub callsign: String,
    pub last_update: AISTime,
    pub first_seen: AISTime,
    pub messages_count: usize,
    pub total_distance_nm: f64,
    pub avg_speed_knots: f32,
    pub anomalies: Vec<GapAnomaly>,
}

impl Default for Track {
    fn default() -> Self {
        Self {
            mmsi: 0,
            vessel_name: String::new(),
            callsign: String::new(),
            last_update: AISTime::default(),
            first_seen: AISTime::default(),
            messages_count: 0,
            total_distance_nm: 0.0,
            avg_speed_knots: 0.0,
            anomalies: Vec::new(),
        }
    }
}

// ============================================================================
// NMEA PARSER
// ============================================================================

pub struct AISParser {
    pub current_sentence: String,
    pub checksum_valid: bool,
}

impl Default for AISParser {
    fn default() -> Self {
        Self {
            current_sentence: String::new(),
            checksum_valid: true,
        }
    }
}

impl AISParser {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn parse_nmea(&mut self, sentence: &str) -> Result<Option<AISMessage>, ParseError> {
        let trimmed = sentence.trim();
        
        // Check for NMEA prefix and checksum
        if !trimmed.starts_with('$') || !trimmed.ends_with('*') {
            return Err(ParseError::InvalidPrefix);
        }

        let parts: Vec<&str> = trimmed.split('*').collect();
        if parts.len() != 2 {
            return Err(ParseError::InvalidChecksumFormat);
        }

        let data_part = parts[0];
        let checksum_str = parts[1][..parts[1].len()-1];
        
        // Verify checksum
        let calculated = self.calculate_checksum(data_part);
        if calculated != u8::from_str_radix(checksum_str, 16).unwrap_or(0) {
            return Err(ParseError::ChecksumMismatch(calculated as i32));
        }

        self.checksum_valid = true;
        self.current_sentence = data_part.to_string();

        // Parse fields: $AISSentence,<type>,<data>
        let fields: Vec<&str> = data_part.split(',').collect();
        
        if fields.is_empty() || fields[0].is_empty() {
            return Err(ParseError::EmptyFields);
        }

        let msg_type: u8 = match fields[0] {
            "1" => 1,
            "2" => 2,
            "3" => 3,
            "4" => 4,
            "5" => 5,
            "6" => 6,
            "7" => 7,
            "8" => 8,
            "9" => 9,
            _ => {
                return Err(ParseError::UnknownMessageType(fields[0].parse().unwrap_or(0)));
            }
        };

        // Parse timestamp (fields 3-5 for Type 1)
        let time_str = fields.get(2).map(|s| s.parse::<u8>().ok()).flatten();
        let hour = time_str.map(|t| t / 100).unwrap_or(0);
        let minute = time_str.map(|t| (t / 10) % 10).unwrap_or(0);
        let second = time_str.map(|t| t % 10).unwrap_or(0);

        // Parse position (fields 7-9 for Type 1)
        let lat_str = fields.get(6).map(|s| s.parse::<f32>().ok()).flatten();
        let lon_str = fields.get(7).map(|s| s.parse::<f32>().ok()).flatten();

        // Parse speed and course (fields 10-11)
        let sog_str = fields.get(9).map(|s| s.parse::<f32>().ok()).flatten();
        let cog_str = fields.get(10).map(|s| s.parse::<u8>().ok()).flatten();

        // Parse heading (field 12)
        let hdg_str = fields.get(11).map(|s| s.parse::<u8>().ok()).flatten();

        // Parse nav status (field 4)
        let nav_status: u8 = match fields.get(3).and_then(|s| s.parse().ok()) {
            Some(n) => n,
            None => 0,
        };

        // Convert lat/lon from NMEA format to degrees
        let latitude = lat_str.map(|v| Self::nmea_to_degrees(v)).unwrap_or(0.0);
        let longitude = lon_str.map(|v| Self::nmea_to_degrees(v)).unwrap_or(0.0);

        // Parse MMSI (field 1)
        let mmsi: u32 = fields.get(0).and_then(|s| s.parse().ok()).unwrap_or(0);

        // Build timestamp using current time if not in message
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        Ok(Some(AISMessage {
            mmsi,
            msg_type,
            timestamp: AISTime::from_timestamp(now),
            position: AISPosition {
                latitude,
                longitude,
                speed_over_ground: sog_str.unwrap_or(0.0),
                course_over_ground: cog_str.unwrap_or(0),
                heading: hdg_str.unwrap_or(0),
            },
            nav_status,
            rate_of_turn: 0.0, // Would need more fields to parse properly
            spare_bits: 0,
        }))
    }

    fn nmea_to_degrees(nmea_value: f32) -> f32 {
        let degrees = (nmea_value / 100.0).round();
        
        // Handle hemisphere
        if nmea_value < 0.0 {
            -degrees
        } else {
            degrees
        }
    }

    fn calculate_checksum(data: &str) -> u8 {
        let mut sum = 0u16;
        for byte in data.bytes() {
            sum ^= byte as u16;
        }
        (sum & 0xFF) as u8
    }
}

#[derive(Debug)]
pub enum ParseError {
    InvalidPrefix,
    InvalidChecksumFormat,
    ChecksumMismatch(i32),
    EmptyFields,
    UnknownMessageType(u8),
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidPrefix => write!(f, "Missing NMEA $ prefix"),
            Self::InvalidChecksumFormat => write!(f, "Invalid checksum format"),
            Self::ChecksumMismatch(expected) => write!(f, "Checksum mismatch: expected {}", expected),
            Self::EmptyFields => write!(f, "Empty message fields"),
            Self::UnknownMessageType(t) => write!(f, "Unknown message type: {}", t),
        }
    }
}

// ============================================================================
// TRACK BUILDER
// ============================================================================

pub struct TrackBuilder {
    tracks: HashMap<u32, Track>,
    thresholds: GapThresholds,
}

impl Default for TrackBuilder {
    fn default() -> Self {
        Self {
            tracks: HashMap::new(),
            thresholds: GapThresholds::default(),
        }
    }
}

impl TrackBuilder {
    pub fn new(thresholds: Option<GapThresholds>) -> Self {
        Self {
            tracks: HashMap::new(),
            thresholds: thresholds.unwrap_or_default(),
        }
    }

    pub fn add_message(&mut self, msg: AISMessage) -> Vec<GapAnomaly> {
        let mut anomalies = Vec::new();

        // Check if vessel already exists
        match self.tracks.get_mut(&msg.mmsi) {
            Some(track) => {
                // Update existing track
                track.messages_count += 1;
                track.last_update = msg.timestamp.clone();
                
                // Calculate time since last update
                let delta = msg.timestamp.duration_since(&track.last_update);
                let delta_secs = delta.as_secs_f64();

                if delta_secs > self.thresholds.warning_score {
                    // Check for gap anomaly
                    let expected_pos = Self::project_position(
                        &track.position,
                        track.avg_speed_knots as f64,
                        delta_secs,
                    );

                    anomalies.push(GapAnomaly {
                        vessel_mmsi: msg.mmsi,
                        gap_start_time: track.last_update.clone(),
                        gap_end_time: msg.timestamp.clone(),
                        gap_duration_seconds: delta_secs,
                        last_known_position: track.position.clone(),
                        expected_next_position: expected_pos,
                        risk_score: Self::calculate_risk_score(
                            delta_secs,
                            track.avg_speed_knots as f64,
                            msg.nav_status,
                        ),
                        anomaly_type: if delta_secs > self.thresholds.critical_score {
                            GapType::CriticalGap
                        } else if delta_secs > self.thresholds.high_risk_score {
                            GapType::HighRiskGap
                        } else if delta_secs > self.thresholds.warning_score {
                            GapType::WarningGap
                        } else {
                            GapType::NormalGap
                        },
                    });

                    // Update position to current
                    track.position = msg.position.clone();
                }
            }
            None => {
                // Create new track
                let now = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map(|d| d.as_secs_f64())
                    .unwrap_or(0.0);

                self.tracks.insert(msg.mmsi, Track {
                    mmsi: msg.mmsi,
                    vessel_name: format!("Vessel-{}", msg.mmsi),
                    callsign: String::new(),
                    last_update: msg.timestamp.clone(),
                    first_seen: msg.timestamp.clone(),
                    messages_count: 1,
                    total_distance_nm: 0.0,
                    avg_speed_knots: msg.position.speed_over_ground as f64,
                    anomalies: Vec::new(),
                });
            }
        }

        anomalies
    }

    fn project_position(
        pos: &AISPosition,