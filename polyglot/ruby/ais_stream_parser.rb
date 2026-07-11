#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'csv'
require 'time'
require 'optparse'

module AisGapWatch
  # Configuration constants
  DEFAULT_TIME_GAP_THRESHOLD = 60.0       # seconds before considering a gap
  DEFAULT_SPEED_THRESHOLD_KNOTS = 120.0    # knots - vessel moving too fast
  DEFAULT_POSITION_DRIFT_THRESHOLD = 500.0 # meters - position jumped too far
  DEFAULT_MIN_MESSAGES_FOR_TRACK = 3       # minimum messages to establish track

  class << self
    def config
      @config ||= {
        time_gap_threshold: DEFAULT_TIME_GAP_THRESHOLD,
        speed_threshold_knots: DEFAULT_SPEED_THRESHOLD_KNOTS,
        position_drift_threshold: DEFAULT_POSITION_DRIFT_THRESHOLD,
        min_messages_for_track: DEFAULT_MIN_MESSAGES_FOR_TRACK
      }
    end

    def config=(cfg)
      @config = cfg.merge(config).with_indifferent_access
    end

    # Parse a single NMEA AIS message into structured data
    def parse_ais_message(sentence, msg_index = 0)
      return nil unless sentence && !sentence.strip.empty?

      parts = sentence.split(',')
      return nil if parts.length < 2

      {
        raw: sentence,
        index: msg_index,
        talker: parts[1],
        type: parts[2],
        seq_num: parts[3].to_i,
        checksum_valid: validate_checksum(parts),
        data: parse_ais_data(parts)
      }
    rescue StandardError => e
      { raw: sentence, index: msg_index, error: e.message }
    end

    private

    def validate_checksum(parts)
      return true if parts.length < 6

      sum = (0...parts.length - 2).map { |i| parts[i].to_i }.sum & 0xFFFF
      expected = (0xFFFF - sum) & 0xFFFF
      checksum_str = parts[5] || ''
      expected == checksum_str.to_i
    end

    def parse_ais_data(parts)
      type_num = parts[2].to_i
      data = {}

      case type_num
      when 1..6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19
        # Standard AIS messages with position data
        parse_position_data(parts)
      else
        { raw: parts.slice(2..-1).join(',') }
      end
    end

    def parse_position_data(parts)
      # M00-M07: Position fields (M00 = M04 + M05, etc.)
      position_fields = [
        ['m00', 'm01', 'm02', 'm03'],  # M00-M03: Primary positions
        ['m04', 'm05', 'm06', 'm07']   # M04-M07: Secondary positions
      ]

      position_data = {}

      position_fields.each_with_index do |fields, idx|
        pos_parts = parts.slice(2 + fields[0].to_i * 3..-1).map(&:to_f)
        
        next if pos_parts.length < 4

        # Extract: lat, lon, speed_over_ground, course_over_ground
        position_data["m#{idx}"] = {
          latitude: format_latitude(pos_parts[0]),
          longitude: format_longitude(pos_parts[1]),
          sog_knots: pos_parts[2],
          cog_degrees: pos_parts[3]
        }

        # Convert lat/lon to decimal degrees for easier processing
        position_data["m#{idx}"][:lat_decimal] = parse_lat_to_decimal(
          position_data["m#{idx}"][:latitude]
        )
        position_data["m#{idx}"][:lon_decimal] = parse_lon_to_decimal(
          position_data["m#{idx}"][:longitude]
        )
      end

    rescue StandardError => e
      { raw: parts.slice(2..-1).join(','), error: e.message }
    end

    def format_latitude(value)
      # Handle DDDMM.MMMM or DDMM.MMMM formats
      value.to_s
    end

    def format_longitude(value)
      # Handle DDDMM.MMMM or DDMM.MMMM formats
      value.to_s
    end

    def parse_lat_to_decimal(lat_str)
      return 0.0 unless lat_str && !lat_str.empty?

      parts = lat_str.split('.')
      degrees = parts[0].to_i
      minutes = parts[1] ? (parts[1].to_f / 60.0) : 0.0

      # Determine hemisphere and adjust sign
      if lat_str.start_with?('S') || lat_str.end_with?('S')
        -degrees - minutes
      else
        degrees + minutes
      end
    rescue StandardError => e
      0.0
    end

    def parse_lon_to_decimal(lon_str)
      return 0.0 unless lon_str && !lon_str.empty?

      parts = lon_str.split('.')
      degrees = parts[0].to_i
      minutes = parts[1] ? (parts[1].to_f / 60.0) : 0.0

      # Determine hemisphere and adjust sign
      if lon_str.start_with?('W') || lon_str.end_with?('W')
        -degrees - minutes
      else
        degrees + minutes
      end
    rescue StandardError => e
      0.0
    end

    def parse_timestamp(timestamp_str)
      return Time.now unless timestamp_str && !timestamp_str.empty?

      # Handle various NMEA timestamp formats (HHMMSS.SSS)
      time_parts = timestamp_str.split('.')
      
      if time_parts[0].length == 6
        # HHMMSS format
        [time_parts[0][0,2], '0', time_parts[0][2,2]].join(':')
      elsif time_parts[0].length == 8
        # HHMMSS.SS format with fractional seconds
        [time_parts[0][0,2], '0', time_parts[0][2,2]].join(':')
      else
        Time.now.to_s
      end
    rescue StandardError => e
      Time.now.to_s
    end

    def calculate_track_score(track)
      score = 100.0
      
      # Penalize for gaps
      track.gaps.each do |gap|
        severity = gap.severity / DEFAULT_TIME_GAP_THRESHOLD * 25.0
        score -= [severity, 25].min
      end

      # Penalize for high speeds
      if track.max_speed > config[:speed_threshold_knots]
        excess = (track.max_speed - config[:speed_threshold_knots]) / 10.0
        score -= [excess * 10, 25].min
      end

      # Penalize for position jumps
      track.position_jumps.each do |jump|
        severity = jump.severity / DEFAULT_POSITION_DRIFT_THRESHOLD * 25.0
        score -= [severity, 25].min
      end

      # Bonus for consistent tracks (fewer gaps)
      consistency_bonus = (1.0 - track.gaps.count.to_f / [track.messages.size, 3].max) * 10.0
      score += consistency_bonus

      [score, 0.0].max.round(2)
    end
  end
end

# Track builder for maintaining vessel state across messages
class VesselTrackBuilder
  def initialize(config: AisGapWatch.config)
    @config = config.with_indifferent_access
    @tracks = {}
    @message_index = 0
  end

  # Process a single AIS message and update the appropriate track
  def add_message(message_data, timestamp_str = nil)
    msg_type = message_data[:type] || 'M'
    
    # Create or get existing track for this vessel
    mci = message_data['m00'] ? "MCI-#{message_data['m00']['raw']}" : "UNKNOWN"
    @tracks[mci] ||= VesselTrack.new(config: @config)

    track = @tracks[mci]

    # Parse timestamp if provided
    parsed_ts = AisGapWatch.parse_timestamp(timestamp_str) if timestamp_str
    
    # Extract position data from message
    pos_data = extract_position_data(message_data, msg_type)
    
    # Create or update the last known position
    track.last_known_pos ||= Position.new(
      lat: pos_data[:lat_decimal] || 0.0,
      lon: pos_data[:lon_decimal] || 0.0,
      sog: pos_data[:sog_knots] || 0.0,
      cog: pos_data[:cog_degrees] || 0.0,
      timestamp: parsed_ts || Time.now
    )

    # Update track metadata
    track.messages << message_data
    track.last_message_index = @message_index
    track.last_timestamp = parsed_ts || Time.now

    # Calculate time since last position update
    if track.last_known_pos && parsed_ts
      time_diff = (parsed_ts - track.last_known_pos.timestamp).to_f
      track.time_since_update = [time_diff, 0].max
    end

    @message_index += 1

    track
  rescue StandardError => e
    puts "Warning: Error processing message: #{e.message}"
    nil
  end

  # Extract position data from AIS message based on type
  def extract_position_data(message_data, msg_type)
    { lat_decimal: 0.0, lon_decimal: 0.0, sog_knots: 0.0, cog_degrees: 0.0 }
  end

  # Get all tracks with their anomaly scores
  def get_all_tracks
    @tracks.values.sort_by { |t| -t.score }
  end

  # Get a specific track by MCI identifier
  def get_track(mci)
    @tracks[mci]
  end

  # Clear all tracks (useful for new batch processing)
  def clear!
    @tracks.clear
    @message_index = 0
  end
end

# Position class representing a vessel's location at a point in time
class Position
  attr_reader :lat, :lon, :sog, :cog, :timestamp

  def initialize(lat: 0.0, lon: 0.0, sog: 0.0, cog: 0.0, timestamp: Time.now)
    @lat = lat
    @lon = lon
    @sog = sog
    @cog = cog
    @timestamp = timestamp
  end

  def to_h
    {
      lat: @lat.round(6),
      lon: @lon.round(6),
      sog_knots: @sog.round(2),
      cog_degrees: @cog.round(2),
      timestamp: @timestamp.iso8601
    }
  end

  def to_json(*args)
    JSON.generate(to_h, *args)
  end
end

# Track class representing a vessel's movement history with anomaly detection
class VesselTrack
  attr_reader :mci, :messages, :last_known_pos, :score, :gaps, :position_jumps, :max_speed

  def initialize(config: AisGapWatch.config)
    @config = config.with_indifferent_access
    @mci = 'Unknown'
    @messages = []
    @last_known_pos = nil
    @score = 100.0
    @gaps = []
    @position_jumps = []
    @max_speed = 0.0
    @time_since_update = 0.0
  end

  def add_position(pos_data)
    # Store position for later gap analysis
    if pos_data && !pos_data.empty?
      @last_known_pos = Position.new(
        lat: pos_data[:lat_decimal] || 0.0,
        lon: pos_data[:lon_decimal] || 0.0,
        sog: pos_data[:sog_knots] || 0.0,
        cog: pos_data[:cog_degrees] || 0.0,
        timestamp: Time.now
      )

      # Track maximum speed observed
      @max_speed = [@max_speed, pos_data[:sog_knots] || 0.0].max if pos_data[:sog_knots]

      # Check for position jumps (large sudden changes)
      check_position_jump(pos_data)
    end
  end

  def check_position_jump(current_pos_data)
    return unless @last_known_pos && current_pos_data
    
    lat_diff = (@last_known_pos.lat - (current_pos_data[:lat_decimal] || 0.0)).abs
    lon_diff = (@last_known_pos.lon - (current_pos_data[:lon_decimal] || 0.0)).abs

    # Approximate distance in meters (simplified)
    approx_distance = Math.sqrt((lat_diff * 111_000)**2 + (lon_diff * 111_000)**2)

    if approx_distance > @config[:position_drift_threshold]
      @position_jumps << {
        distance_meters: approx_distance.round(1),
        timestamp: Time.now,
        severity: approx_distance / @config[:position_drift_threshold] * 100.0
      }

      # Penalize score for position jumps
      @score -= [approx_distance / 500.0, 25].min
    end
  end

  def calculate_time_gap(current_timestamp)
    return 0.0 unless @last_known_pos && current_timestamp
    
    (current_timestamp - @last_known_pos.timestamp).to_f
  rescue StandardError => e
    0.0
  end

  def check_time_gap(current_timestamp)
    time_diff = calculate_time_gap(current_timestamp)
    
    if time_diff > @config[:time_gap_threshold]
      # Record the gap
      @gaps << {
        duration_seconds: time_diff.round(1),
        timestamp: current_timestamp,
        severity: (time_diff / @config[:time_gap_threshold]) * 25.0
      }

      # Penalize score for gaps
      @score -= [time_diff / 60.0, 25].min
    end

    time_diff
  end

  def update_score!
    recalculate_score
  end

  def recalculate_score
    base = 100.0
    
    # Gap penalties
    @gaps.each do |gap|
      severity = gap[:severity] || 0.0
      base -= [severity, 25].min
    end

    # Speed penalty
    if @max_speed > @config[:speed_threshold_knots]
      excess = (@max_speed - @config[:speed_threshold_knots]) / 10.0
      base -= [excess * 10, 25].min
    end

    # Position jump penalties
    @position_jumps.each do |jump|
      severity = jump[:severity] || 0.0
      base -= [severity, 25].min
    end

    # Consistency bonus
    total_messages = [@messages.size, 3].max
    consistency_factor = (1.0 - @gaps.count.to_f / total_messages) * 10.0
    base += consistency_factor

    @score = [base, 0.0].max.round(2)
  end

  def to_h
    {
      mci: @mci,
      messages_count: @messages.size,
      last_known_pos: @last_known_pos ? @last_known_pos.to_h : nil,
      max_speed_knots: @max_speed.round(2),
      time_since_update_seconds: @time_since_update.round(1),
      score: @score,
      gaps_count: @gaps.size,
      position_jumps_count: @position_jumps.size,
      anomalies: {
        critical: (@gaps.select { |g| g[:severity] > 50 }.count + 
                   @position_jumps.select { |j| j[:severity] > 100 }.count),
        high: (@gaps.select { |g| g[:severity] >= 25 && g[:severity] <= 50 }.count + 
               @position_jumps.select { |j| j[:severity] >= 50 && j[:severity] <= 100 }.count),
        medium: (@gaps.select { |g| g[:severity] >= 12.5 && g[:severity] < 25 }.count + 
                 @position_jumps.select { |j| j[:severity] >= 25 && j[:severity] < 50 }.count),
        low: (@gaps.select { |g| g[:severity] < 12.5 }.count + 
              @position_jumps.select { |j| j[:severity] < 25 }.count)
      }
    }
  end

  def to_json(*args)
    JSON.generate(to_h, *args)
  end
end

# Main AIS Stream Parser class - orchestrates the entire pipeline
class AisStreamParser
  include Singleton

  def initialize(config: AisGapWatch.config)
    @config = config.with_indifferent_access
    @builder = VesselTrackBuilder.new(config: @config)
    @parsers = {}
    @stats = {
      total_messages: 0,
      valid_messages: 0,
      error_messages: 0,
      tracks_created: 0,
      gaps_detected: 0,
      jumps_detected: 0
    }
  end

  # Parse a