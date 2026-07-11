require 'ostruct'
require 'json'
require 'time'
require 'forwardable'

# =============================================================================
# AIS Track Continuity Checker
# Detects anomalies in vessel track continuity (gaps, jumps, speed spikes)
# =============================================================================

class AISConfig
  DEFAULTS = {
    time_gap_warning: 30.0,      # seconds - warning threshold
    time_gap_critical: 60.0,     # seconds - critical threshold  
    distance_jump_warning: 500.0, # meters - max jump before flagging
    speed_spike_multiplier: 3.0,   # x normal speed = spike
    min_track_points: 2,           # minimum points for analysis
    default_speed_knots: 15.0     # assumed cruise speed if unknown
  }

  def initialize(options = {})
    @config = DEFAULTS.merge(options)
  end

  attr_reader :time_gap_warning, :time_gap_critical, 
              :distance_jump_warning, :speed_spike_multiplier,
              :min_track_points, :default_speed_knots

  def time_gap_threshold(level: :warning)
    level == :critical ? @config[:time_gap_critical] : @config[:time_gap_warning]
  end

  def distance_threshold(level: :warning)
    level == :critical ? @config[:distance_jump_warning] * 2.0 : @config[:distance_jump_warning]
  end
end

class AISMessage < OpenStruct
  # Standard MIM (Maritime Information Model) fields
  REQUIRED = [:mmsi, :timestamp, :lat, :lon].freeze
  
  def initialize(attrs = {})
    super().merge!(attrs)
    
    # Auto-convert strings to numbers
    [:lat, :lon, :speed_over_ground, :course_over_ground, 
     :heading, :navigation_status, :rate_of_turn].each do |field|
      self[field] = (self[field] || 0.0).to_f if self[field].is_a?(String)
    end
    
    # Validate required fields
    REQUIRED.each { |f| raise ArgumentError, "Missing #{f}" unless self[f] }
  end

  def to_h
    hash = {}
    instance_variables.each do |var|
      value = instance_variable_get(var)
      hash[var.to_s[1..-1]] = value if !value.nil?
    end
    hash
  end

  def distance_from(other_msg)
    return 0.0 if other_msg.nil? || !other_msg.respond_to?(:lat) || !other_msg.respond_to?(:lon)
    
    dlat = (self.lat - other_msg.lat).to_f * 60_762 / 180.0
    dlon = (self.lon - other_msg.lon).to_f * 60_762 / 360.0
    Math.sqrt(dlat**2 + dlon**2)
  end

  def time_since(other_msg, unit: :seconds)
    return 0.0 if other_msg.nil? || !other_msg.respond_to?(:timestamp)
    
    t1 = self.timestamp.to_f
    t2 = other_msg.timestamp.to_f
    
    delta = (t1 - t2).abs
    { seconds: delta, minutes: delta / 60.0, hours: delta / 3600.0 }[unit] || delta
  end

  def speed_knots
    self.speed_over_ground || 0.0
  end

  def course_degrees
    self.course_over_ground || 0.0
  end
end

class TrackContinuityChecker
  extend Forwardable
  
  # Delegate to config for clean API
  delegate :time_gap_warning, :time_gap_critical, 
           :distance_jump_warning, :speed_spike_multiplier,
           :min_track_points, :default_speed_knots, to: :@config

  def initialize(config = nil)
    @config = AISConfig.new(config || {})
    @anomalies = []
    @track_stats = {}
  end

  # Main entry point - analyze a single message against its track history
  def check_continuity(msg, track_history = [])
    anomalies.clear
    @track_stats[msg.mmsi] ||= { 
      last_msg: nil, 
      total_distance: 0.0, 
      avg_speed: 0.0, 
      point_count: 0 
    }

    stats = @track_stats[msg.mmsi]
    
    # Calculate metrics against previous position
    prev_msg = stats[:last_msg]
    
    if prev_msg
      time_delta = msg.time_since(prev_msg)
      dist_delta = msg.distance_from(prev_msg)
      
      # 1. Time gap analysis
      check_time_gap(time_delta, msg.mmsi)
      
      # 2. Distance jump analysis  
      check_distance_jump(dist_delta, prev_msg, msg, stats)
      
      # 3. Speed consistency check
      if dist_delta > 0 && time_delta > 0
        speed_knots = (dist_delta * 60762 / 1852.0) / (time_delta / 60.0)
        check_speed_anomaly(speed_knots, prev_msg.speed_knots, msg.mmsi)
      end
      
      # Update stats
      stats[:last_msg] = msg
      stats[:total_distance] += dist_delta
      stats[:point_count] += 1
    else
      # First message - just set baseline
      stats[:baseline_speed] = msg.speed_knots
    end

    anomalies
  end

  private

  def check_time_gap(delta, mmsi)
    threshold = @config.time_gap_threshold(level: :warning)
    
    if delta > @config.time_gap_critical
      add_anomaly(mmsi, :critical_gap, { time_delta: delta })
    elsif delta > threshold
      add_anomaly(mmsi, :warning_gap, { time_delta: delta })
    end
  end

  def check_distance_jump(dist, prev_msg, curr_msg, stats)
    threshold = @config.distance_threshold(level: :warning)
    
    if dist > @config.distance_jump_warning * 2.0
      add_anomaly(curr_msg.mmsi, :critical_jump, { 
        distance: dist, 
        expected_max: threshold,
        time_delta: curr_msg.time_since(prev_msg)
      })
    elsif dist > threshold
      add_anomaly(curr_msg.mmsi, :warning_jump, { 
        distance: dist,
        expected_max: threshold,
        time_delta: curr_msg.time_since(prev_msg)
      })
    end
  end

  def check_speed_anomaly(speed_knots, prev_speed, mmsi)
    if prev_speed > 0 && speed_knots > prev_speed * @config.speed_spike_multiplier
      add_anomaly(mmsi, :speed_spike, { 
        current: speed_knots, 
        previous: prev_speed,
        multiplier: (speed_knots / prev_speed).round(2)
      })
    elsif prev_speed > 0 && speed_knots < prev_speed * 0.1
      add_anomaly(mmsi, :speed_drop, { 
        current: speed_knots, 
        previous: prev_speed
      })
    end
  end

  def add_anomaly(mmsi, type, details = {})
    anomaly = OpenStruct.new(
      mmsi: mmsi,
      type: type,
      timestamp: Time.now.utc,
      details: details
    )
    
    @anomalies << anomaly
    
    # Group by severity for quick access
    if type == :critical_gap || type == :critical_jump
      @track_stats[mmsi][:critical_count] ||= 0
      @track_stats[mmsi][:critical_count] += 1
    elsif type == :warning_gap || type == :warning_jump
      @track_stats[mmsi][:warning_count] ||= 0
      @track_stats[mmsi][:warning_count] += 1
    end
    
    anomaly
  end

  # Batch process a list of messages in order
  def analyze_track(messages)
    messages.each do |msg|
      check_continuity(msg, @track_stats[msg.mmsi][:last_msg])
    end
    
    generate_report
  end

  def generate_report
    report = {
      timestamp: Time.now.utc.iso8601,
      summary: {},
      anomalies: [],
      track_stats: {}
    }

    # Aggregate by MMSI
    @track_stats.each do |mmsi, stats|
      next if stats[:point_count] < 2
      
      avg_speed = (stats[:total_distance] / 
                   ((stats[:last_msg].timestamp - messages.first.timestamp).to_f / 3600.0)) rescue 0.0
      
      report[:track_stats][mmsi.to_s] = {
        mmsi: mmsi,
        points: stats[:point_count],
        total_distance_km: (stats[:total_distance] / 1000).round(2),
        avg_speed_knots: avg_speed.round(2),
        warnings: stats[:warning_count] || 0,
        criticals: stats[:critical_count] || 0,
        first_seen: messages.first.timestamp.iso8601,
        last_seen: stats[:last_msg].timestamp.iso8601
      }

      # Add anomalies to report
      @anomalies.select { |a| a.mmsi.to_s == mmsi.to_s }.each do |anom|
        next if anom.type.start_with?('warning') && 
                 (report[:summary][:total_warnings] || 0) >= 50
        
        report[:anomalies] << {
          type: anom.type,
          mmsi: anom.mmsi,
          timestamp: anom.timestamp.iso8601,
          details: anom.details
        }
      end
      
      # Limit anomalies per MMSI to prevent explosion
      next if report[:anomalies].size >= 500
    end

    # Add summary stats
    total = @track_stats.values.sum { |s| s[:warning_count] || 0 } + 
            @track_stats.values.sum { |s| s[:critical_count] || 0 }
    
    report[:summary] = {
      total_messages: messages.size,
      unique_vessels: @track_stats.keys.size,
      total_anomalies: report[:anomalies].size,
      critical_events: @track_stats.values.sum { |s| s[:critical_count] || 0 },
      warning_events: @track_stats.values.sum { |s| s[:warning_count] || 0 } - 
                       @track_stats.values.sum { |s| s[:critical_count] || 0 }
    }

    report
  end

  # Get current anomalies for a specific vessel
  def get_anomalies_for(mmsi)
    @anomalies.select { |a| a.mmsi.to_s == mmsi.to_s }.reverse
  end

  # Clear all state (useful between batches)
  def reset!
    @anomalies.clear
    @track_stats.clear
  end

  # Get summary statistics without generating full report
  def summary_stats
    {
      total_anomalies: @anomalies.size,
      critical_count: @track_stats.values.sum { |s| s[:critical_count] || 0 },
      warning_count: @track_stats.values.sum { |s| s[:warning_count] || 0 } - 
                      @track_stats.values.sum { |s| s[:critical_count] || 0 }
    }
  end

  # Export anomalies as JSON
  def export_anomalies(format: :json)
    case format
    when :json
      @anomalies.map { |a| a.to_h }.to_json
    when :csv
      csv = StringIO.new
      writer = CSV::Writer.new(csv, [:mmsi, :type, :timestamp])
      
      @anomalies.each do |anom|
        ts = anom.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
        writer << [anom.mmsi.to_s, anom.type, ts]
      end
      
      csv.string
    else
      raise ArgumentError, "Unknown format: #{format}"
    end
  end

  # Check if a vessel is in distress (heuristic)
  def distress_check?(mmsi)
    stats = @track_stats[mmsi.to_s]
    return false unless stats
    
    # Heuristics for potential distress:
    # - Multiple critical gaps
    # - Erratic speed patterns
    # - Very short total track (stopped?)
    
    if stats[:critical_count] >= 3 || 
       (stats[:total_distance] < 100 && stats[:point_count] > 5)
      return true
    end
    
    false
  end

  # Get time range covered by analysis
  def time_range
    first = @track_stats.values.first&.[](:last_msg)&.timestamp
    last = @track_stats.values.last&.[](:last_msg)&.timestamp
    
    [first, last].compact.map { |t| t.iso8601 }
  end

  # Calculate overall track quality score (0-100)
  def track_quality_score(mmsi)
    stats = @track_stats[mmsi.to_s]
    return nil unless stats
    
    point_count = stats[:point_count]
    criticals = stats[:critical_count] || 0
    warnings = stats[:warning_count] || 0
    
    # Base score starts at 100
    score = 100.0
    
    # Penalize for gaps (exponential decay)
    gap_penalty = ((warnings + criticals) / point_count) * 25.0
    score -= gap_penalty
    
    # Heavy penalty for critical events
    score -= criticals * 5.0
    
    # Bonus for long tracks with few issues
    if point_count > 100 && (warnings + criticals) < 3
      score += 10.0
    end

    [0, score].max.round(2)
  end
end

# =============================================================================
# Demo / Test Data Generator
# =============================================================================

def generate_test_data(config = AISConfig.new)
  # Create a vessel with various anomaly scenarios
  base_time = Time.parse('2024-01-15T10:00:00Z')
  
  messages = []
  
  # Normal track - steady cruise at 12 knots, ~30s intervals
  (0..9).each do |i|
    t = base_time + i * 30
    lat = 51.4775 + (i * 0.0005)
    lon = -0.1412 + (i * 0.0006)
    
    messages << AISMessage.new(
      mmsi: '235009800',
      timestamp: t,
      lat: lat.round(4),
      lon: lon.round(4),
      speed_over_ground: 12.0 + (Math.sin(i) * 1.0).round(1),
      course_over_ground: 135.0,
      navigation_status: 5, # Under way using engine
      rate_of_turn: 0.0
    )
  end
  
  # Add a gap - vessel goes silent for 45 seconds (warning) then 90s (critical)
  messages << AISMessage.new(
    mmsi: '235009800',
    timestamp: base_time + 10 * 30,
    lat: 51.4775 + 0.005,
    lon: -0.1412 + 0.006,
    speed_over_ground: 12.0,
    course_over_ground: 135.0,
    navigation_status: 5,
    rate_of_turn: 0.0
  )
  
  # Normal track resumes
  (0..8).each do |i|
    t = base_time + 12 * 30 + i * 30
    lat = 51.4775 + 0.005 + (i * 0.0005)
    lon = -0.1412 + 0.006 + (i * 0.0006)
    
    messages << AISMessage.new(
      mmsi: '235009800',
      timestamp: t,
      lat: lat.round(4),
      lon: lon.round(4),
      speed_over_ground: 12.0,
      course_over_ground: 135.0,
      navigation_status: 5,
      rate_of_turn: 0.0
    )
  end
  
  # Add a distance jump anomaly - sudden position change
  messages << AISMessage.new(
    mmsi: '235009800',
    timestamp: base_time