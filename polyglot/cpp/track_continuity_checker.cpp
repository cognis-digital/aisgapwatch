// polyglot/cpp/track_continuity_checker.cpp
// AIS Track Continuity Checker for aisgapwatch
// Detects and scores transponder-gap anomalies in vessel tracks

#include <iostream>
#include <fstream>
// #include <sstream>
#include <vector>
#include <string>
#include <map>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <memory>
#include <functional>
#include <limits>

namespace aisgapwatch {

using namespace std::chrono;

// ============================================================================
// Configuration & Constants
// ============================================================================

struct Config {
    // Time thresholds (in seconds)
    double max_expected_interval = 120.0;      // Max normal interval between fixes
    double min_gap_to_warn = 300.0;            // Gap duration to start warning
    double min_gap_to_critical = 900.0;        // Gap duration for critical alert
    
    // Scoring weights
    double weight_duration = 1.5;              // Duration multiplier in score
    double weight_consecutive = 2.0;           // Multiplier for consecutive gaps
    double weight_position_drift = 3.0;        // Position drift penalty factor
    
    // Position thresholds (nautical miles)
    double max_position_jump = 10.0;           // Max allowed position jump between fixes
    
    // Output settings
    bool output_detailed = true;               // Include detailed log entries
    int min_severity_for_output = 2;           // Min severity level to print (0-3)
};

// ============================================================================
// Data Structures
// ============================================================================

struct AISFix {
    uint64_t mmsi;                            // Maritime Mobile Service Identity
    double lat, lon;                          // Position in degrees
    double speed_over_ground = 0.0;           // SOG in knots
    double course_over_ground = 0.0;          // COG in degrees
    int24 status;                             // AIS status bits
    uint32_t timestamp_epoch_ms;              // UTC time in milliseconds
    
    // Computed fields (set by checker)
    double interval_from_prev_fix = 0.0;      // Time since last fix
    double position_jump_nm = 0.0;             // Distance from previous position
    int gap_score = 0;                        // Continuity score for this fix
    
    AISFix() : mmsi(0), lat(0), lon(0), status(0), 
               timestamp_epoch_ms(0), interval_from_prev_fix(0.0),
               position_jump_nm(0.0), gap_score(0) {}
    
    bool operator<(const AISFix& other) const {
        return timestamp_epoch_ms < other.timestamp_epoch_ms;
    }
};

struct TrackState {
    uint64_t mmsi = 0;
    std::vector<AISFix> fixes;
    double total_gap_score = 0.0;
    int consecutive_gaps = 0;
    double max_position_jump = 0.0;
    double avg_interval = 0.0;
    size_t fix_count = 0;
    
    TrackState() : mmsi(0), total_gap_score(0.0), 
                   consecutive_gaps(0), max_position_jump(0.0),
                   avg_interval(0.0), fix_count(0) {}
};

struct GapEvent {
    uint64_t mmsi;
    double start_time_epoch_ms;
    double end_time_epoch_ms;
    double duration_seconds;
    int consecutive_gaps;
    double position_drift_nm;
    std::string severity;  // "NORMAL", "WARNING", "CRITICAL"
    
    GapEvent() : mmsi(0), start_time_epoch_ms(0), end_time_epoch_ms(0),
                  duration_seconds(0.0), consecutive_gaps(0), 
                  position_drift_nm(0.0) {}
};

// ============================================================================
// Utility Functions
// ============================================================================

inline double haversine_distance(double lat1, double lon1, 
                                  double lat2, double lon2) {
    const double R = 3440.065;  // Earth radius in nautical miles
    
    double dlat = (lat2 - lat1) * M_PI / 180.0;
    double dlon = (lon2 - lon1) * M_PI / 180.0;
    
    double a = sin(dlat/2) * sin(dlat/2) + 
               cos(lat1 * M_PI / 180.0) * cos(lat2 * M_PI / 180.0) *
               sin(dlon/2) * sin(dlon/2);
    
    double c = 2 * atan2(sqrt(a), sqrt(1 - a));
    
    return R * c;
}

inline double calculate_interval_seconds(uint64_t prev_ts_ms, 
                                         uint64_t curr_ts_ms) {
    if (prev_ts_ms == 0 || curr_ts_ms == 0) return 0.0;
    return static_cast<double>(curr_ts_ms - prev_ts_ms) / 1000.0;
}

inline std::string severity_to_string(int level, const Config& cfg) {
    if (level < cfg.min_gap_to_warn) return "NORMAL";
    if (level < cfg.min_gap_to_critical) return "WARNING";
    return "CRITICAL";
}

// ============================================================================
// Core Continuity Checker Logic
// ============================================================================

class TrackContinuityChecker {
public:
    explicit TrackContinuityChecker(const Config& cfg = Config()) 
        : config_(cfg), events_() {}
    
    void process_fix(AISFix& fix) {
        if (fix.fixes.empty()) return;
        
        // Calculate interval from previous fix
        double interval = calculate_interval_seconds(
            fix.fixes.back().timestamp_epoch_ms, 
            fix.timestamp_epoch_ms);
        
        fix.interval_from_prev_fix = interval;
        
        // Calculate position jump
        double dist = haversine_distance(
            fix.fixes.back().lat, fix.fixes.back().lon,
            fix.lat, fix.lon);
        fix.position_jump_nm = dist;
        
        // Update track state
        update_track_state(fix.mmsi, interval, dist);
    }
    
    void process_fix(const AISFix& fix) {
        AISFix temp = fix;
        process_fix(temp);
    }
    
    TrackState get_or_create_track(uint64_t mmsi) const {
        auto it = tracks_.find(mmsi);
        if (it != tracks_.end()) return it->second;
        
        TrackState state;
        state.mmsi = mmsi;
        return state;
    }
    
    void update_track_state(uint64_t mmsi, double interval, double position_jump) {
        auto& track = get_or_create_track(mmsi);
        
        if (track.fixes.empty()) {
            // First fix for this vessel - establish baseline
            return;
        }
        
        // Update consecutive gap counter
        double expected_interval = track.avg_interval > 0 ? 
                                  track.avg_interval : config_.max_expected_interval / 2.0;
        
        if (interval > expected_interval * 1.5) {
            // Potential gap detected
            track.consecutive_gaps++;
            
            // Calculate gap score contribution
            double duration_score = interval / config_.min_gap_to_warn;
            double consecutive_bonus = 1.0 + 
                std::log1p(track.consecutive_gaps * config_.weight_consecutive);
            
            double gap_score = duration_score * consecutive_bonus;
            track.total_gap_score += gap_score;
            
            // Update max position jump if applicable
            if (position_jump > track.max_position_jump) {
                track.max_position_jump = position_jump;
            }
        } else {
            // Normal interval - reset consecutive counter
            track.consecutive_gaps = 0;
        }
        
        // Update average interval
        double new_avg = (track.avg_interval * (track.fix_count - 1) + 
                         std::max(interval, expected_interval)) / track.fix_count;
        track.avg_interval = new_avg;
        track.fix_count++;
    }

    void finalize_track(uint64_t mmsi) {
        auto& track = get_or_create_track(mmsi);
        
        // Calculate final severity for this track
        int max_severity_level = 0;
        
        if (track.total_gap_score > config_.min_gap_to_critical * 
            config_.weight_duration) {
            max_severity_level = 3;
        } else if (track.total_gap_score > config_.min_gap_to_warn * 
                   config_.weight_duration) {
            max_severity_level = 2;
        }
        
        // Record final event for this track
        GapEvent event;
        event.mmsi = mmsi;
        event.duration_seconds = track.total_gap_score / config_.weight_duration;
        event.consecutive_gaps = track.consecutive_gaps;
        event.position_drift_nm = track.max_position_jump;
        event.severity = severity_to_string(max_severity_level, config_);
        
        events_.push_back(event);
    }

    void process_track(const std::vector<AISFix>& fixes) {
        if (fixes.empty()) return;
        
        // Sort by timestamp to handle out-of-order messages
        std::sort(fixes.begin(), fixes.end());
        
        AISFix current = fixes[0];
        for (size_t i = 1; i < fixes.size(); ++i) {
            process_fix(current);
            current = fixes[i];
        }
        
        // Process last fix and finalize track
        process_fix(current);
        finalize_track(current.mmsi);
    }

    void add_event(const GapEvent& event) {
        events_.push_back(event);
    }

    const std::vector<GapEvent>& get_events() const {
        return events_;
    }

    double get_total_score(uint64_t mmsi) const {
        auto it = tracks_.find(mmsi);
        if (it != tracks_.end()) return it->second.total_gap_score;
        return 0.0;
    }

    int get_severity_level(uint64_t mmsi) const {
        double score = get_total_score(mmsi);
        if (score < config_.min_gap_to_warn * config_.weight_duration) 
            return 0;
        if (score < config_.min_gap_to_critical * config_.weight_duration) 
            return 1;
        return 2;
    }

private:
    Config config_;
    mutable std::map<uint64_t, TrackState> tracks_;
    std::vector<GapEvent> events_;
};

// ============================================================================
// Output & Reporting
// ============================================================================

class ContinuityReporter {
public:
    explicit ContinuityReporter(const Config& cfg = Config()) 
        : checker_(cfg), config_(cfg) {}
    
    void add_fix(AISFix fix) {
        checker_.process_fix(fix);
    }
    
    void add_fixes(const std::vector<AISFix>& fixes) {
        checker_.process_track(fixes);
    }
    
    void finalize() {
        // Finalize all tracks with remaining data
        for (auto& [mmsi, track] : checker_.tracks_) {
            if (!track.fixes.empty()) {
                double last_interval = calculate_interval_seconds(
                    0, track.fixes.back().timestamp_epoch_ms);
                
                TrackState temp;
                temp.mmsi = mmsi;
                temp.fixes = track.fixes;
                temp.avg_interval = track.avg_interval;
                temp.consecutive_gaps = track.consecutive_gaps;
                temp.total_gap_score = track.total_gap_score;
                temp.max_position_jump = track.max_position_jump;
                temp.fix_count = track.fix_count;
                
                checker_.update_track_state(mmsi, last_interval, 0.0);
                checker_.finalize_track(mmsi);
            }
        }
    }

    void report_summary(std::ostream& os) const {
        if (config_.output_detailed) {
            os << "=== AIS Track Continuity Summary ===\n";
            os << "Total tracks analyzed: " << checker_.tracks_.size() << "\n\n";
            
            // Sort events by severity and duration for priority reporting
            auto& events = checker_.get_events();
            
            if (events.empty()) {
                os << "No significant gaps detected.\n";
                return;
            }
            
            // Group by severity level
            std::vector<GapEvent> critical, warning, normal;
            for (const auto& e : events) {
                if (e.severity == "CRITICAL") critical.push_back(e);
                else if (e.severity == "WARNING") warning.push_back(e);
                else normal.push_back(e);
            }
            
            // Report critical first
            if (!critical.empty()) {
                os << "\n--- CRITICAL GAPS ---\n";
                for (const auto& e : critical) {
                    os << "  MMSI: " << std::hex << e.mmsi << std::dec 
                        << " | Duration: " << std::fixed 
                        << std::setprecision(1) << e.duration_seconds 
                        << "s | Consecutive: " << e.consecutive_gaps
                        << " | Position Drift: " << e.position_drift_nm << " NM\n";
                }
            }
            
            // Report warnings
            if (!warning.empty()) {
                os << "\n--- WARNING GAPS ---\n";
                for (const auto& e : warning) {
                    os << "  MMSI: " << std::hex << e.mmsi << std::dec 
                        << " | Duration: " << std::fixed 
                        << std::setprecision(1) << e.duration_seconds 
                        << "s | Consecutive: " << e.consecutive_gaps
                        << " | Position Drift: " << e.position_drift_nm << " NM\n";
                }
            }
            
            // Report normal gaps (if detailed output enabled)
            if (!normal.empty() && config_.output_detailed) {
                os << "\n--- NORMAL GAPS ---\n";
                for (const auto& e : normal) {
                    os << "  MMSI: " << std::hex << e.mmsi << std::dec 
                        << " | Duration: " << std::fixed 
                        << std::setprecision(1) << e.duration_seconds 
                        << "s\n";
                }
            }
            
            // Summary statistics
            os << "\n=== STATISTICS ===\n";
            double total_duration = 0.0;
            for (const auto& e : events) {
                total_duration += e.duration_seconds;
            }
            os << "Total gap duration: " << std::fixed 
               << std::setprecision(1) << total_duration << " seconds\n";
            os << "Critical: " << critical.size() 
               << ", Warning: " << warning.size() 
               << ", Normal: " << normal.size() << "\n";
        }
    }

    void report_track(uint64_t mmsi, std::ostream& os) const {
        double score = checker_.get_total_score(mmsi);
        int level = checker_.get_severity_level(mmsi);
        
        if (level >= config_.min_severity_for_output) {
            os << "MMSI: " << std::hex << mmsi << std::dec 
               << " | Score: " << std::fixed << std::setprecision(2) 
               << score << " | Level: " << level << "\n";
        }
    }

private:
    TrackContinuityChecker checker_;
    Config config_;
};

// ============================================================================
// Command-Line Interface & Demo
// ============================================================================

std::string format_mmsi(uint64_t mmsi) {
    std::ostringstream oss;
    oss << "0x" << std::hex << mmsi << std::dec;
    return oss.str();
}

void print_usage(const char* prog_name) {
    std::cout << "Usage: " << prog_name << " [options] <input_file>\n";
    std::cout << "\nOptions:\n";
    std::cout << "  -c, --config FILE   Load configuration from file\n";
    std::cout << "  -s, --summary       Print summary report (default: yes)\n";
    std::cout << "  -q, --quiet         Minimal output\n";
    std::cout << "  -h, --help          Show this help\n";
    std::cout << "\nInput file format:\n";
    std::cout << "  M