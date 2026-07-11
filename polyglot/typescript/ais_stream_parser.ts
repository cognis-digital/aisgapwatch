// polyglot/typescript/ais_stream_parser.ts

import { AisMessage, VesselTrack, GapEvent, AnomalyScore, AISParserConfig } from './types';

/**
 * AIS Stream Parser - Detects and scores transponder gap anomalies
 */

export class AISStreamParser {
  private config: AISParserConfig;
  private vesselTracks: Map<string, VesselTrack> = new Map();
  private gapEvents: GapEvent[] = [];
  
  constructor(config?: Partial<AISParserConfig>) {
    this.config = {
      defaultSpeedKnots: 20,
      maxGapMinutes: 15,
      portBufferMinutes: 30,
      anomalyThresholdScore: 70,
      ...config
    };
  }

  /**
   * Parse a single NMEA AIS message and update vessel tracks
   */
  public parse(message: string): GapEvent[] {
    const parsed = this.parseNmeaSentence(message);
    
    if (!parsed) return [];

    // Update or create vessel track
    const track = this.updateVesselTrack(parsed, message);
    
    // Check for gaps
    const newGaps = this.checkForGaps(track);
    
    return newGaps;
  }

  /**
   * Parse NMEA sentence into structured data
   */
  private parseNmeaSentence(sentence: string): AisMessage | null {
    if (!sentence || !sentence.trim()) return null;

    // Validate checksum
    const parts = sentence.split('*');
    if (parts.length < 2) return null;

    const hexChecksum = parts[1].trim().toUpperCase();
    let dataString = parts[0].trim();
    
    for (let i = 0; i < dataString.length; i++) {
      if (dataString[i] === '$') dataString = dataString.slice(i + 1);
    }

    const calculatedChecksum = this.calculateChecksum(dataString);
    if (calculatedChecksum !== hexChecksum) return null;

    // Parse message type and extract fields
    const messageType = parseInt(dataString[0], 10);
    
    let aisData: {
      mmsi: string | undefined;
      timestamp: Date;
      lat: number | undefined;
      lon: number | undefined;
      speedOverGround: number | undefined;
      courseOverGround: number | undefined;
      messageClass: number;
    } = {
      messageType,
      mmsi: undefined,
      timestamp: new Date(),
      lat: undefined,
      lon: undefined,
      sog: undefined,
      cog: undefined,
      messageClass: 0
    };

    // Parse based on message type
    aisData = this.parseMessageByType(dataString, messageType);
    
    return { ...aisData, rawSentence: sentence };
  }

  /**
   * Calculate NMEA checksum for validation
   */
  private calculateChecksum(data: string): string {
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      sum += data.charCodeAt(i);
    }
    
    const hex = (sum & 0xFFFF).toString(16).toUpperCase();
    return hex.padStart(4, '0');
  }

  /**
   * Parse fields based on AIS message type
   */
  private parseMessageByType(data: string, messageType: number): {
    mmsi?: string;
    timestamp: Date;
    lat?: number;
    lon?: number;
    sog?: number;
    cog?: number;
    messageClass: number;
  } {
    const fields = data.split(',');
    
    // Common fields for most messages
    let mmsi: string | undefined;
    let timestamp: Date;
    let lat: number | undefined;
    let lon: number | undefined;
    let sog: number | undefined;
    let cog: number | undefined;

    switch (messageType) {
      case 1: // Position Report - Class A
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        lat = this.parseLatLon(fields[6], 'N');
        lon = this.parseLatLon(fields[7], 'E');
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 2: // Position Report - Class B (with position)
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        lat = this.parseLatLon(fields[6], 'N');
        lon = this.parseLatLon(fields[7], 'E');
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 3: // Static Data Report - Class A
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        // Extract call sign, name, etc. if needed
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 4: // Static Data Report - Class B (with position)
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        lat = this.parseLatLon(fields[6], 'N');
        lon = this.parseLatLon(fields[7], 'E');
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 5: // Static Data Report - Class B (without position)
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 6: // Dynamic Data Report - Class B (with position)
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        lat = this.parseLatLon(fields[6], 'N');
        lon = this.parseLatLon(fields[7], 'E');
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 7: // Dynamic Data Report - Class B (without position)
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 8: // Extended Class A/B Data Report - with position
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        lat = this.parseLatLon(fields[6], 'N');
        lon = this.parseLatLon(fields[7], 'E');
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      case 9: // Extended Class A/B Data Report - without position
        mmsi = fields[0];
        timestamp = this.parseTimestamp(fields[4]);
        sog = parseFloat(fields[8]) || undefined;
        cog = parseFloat(fields[9]) || undefined;
        break;

      default:
        // Unknown message type - still extract what we can
        if (fields.length > 0) {
          mmsi = fields[0];
        }
    }

    return {
      messageType,
      mmsi,
      timestamp,
      lat,
      lon,
      sog,
      cog,
      messageClass: messageType
    };
  }

  /**
   * Parse latitude/longitude from NMEA format
   */
  private parseLatLon(nmeaValue: string | undefined, hemisphere: 'N' | 'E'): number | undefined {
    if (!nmeaValue) return undefined;

    const [degreesMinutes, hemi] = nmeaValue.split(',');
    const degrees = parseInt(degreesMinutes.substring(0, 2), 10);
    const minutes = parseFloat(degreesMinutes.substring(2));
    
    // Convert to decimal degrees
    let decimal = (degrees + minutes / 60) * (hemi === 'N' || hemi === 'E' ? 1 : -1);

    return decimal;
  }

  /**
   * Parse NMEA timestamp into Date object
   */
  private parseTimestamp(nmeaTime: string | undefined): Date {
    if (!nmeaTime) {
      // Use current time as fallback
      return new Date();
    }

    const [hhmmss, day] = nmeaTime.split(',');
    
    let dateStr = `${day}-${hhmmss}`;
    // Add year (assume 20xx for NMEA format)
    dateStr += '-20' + hhmmss.substring(4);

    return new Date(dateStr);
  }

  /**
   * Update vessel track with new observation
   */
  private updateVesselTrack(obs: AisMessage, rawSentence: string): VesselTrack {
    const key = obs.mmsi || 'unknown';
    
    let track = this.vesselTracks.get(key);

    if (!track) {
      // First observation - create new track
      track = {
        mmsi: key,
        firstSeen: obs.timestamp,
        lastSeen: obs.timestamp,
        observations: 1,
        positions: [{ time: obs.timestamp, lat: obs.lat, lon: obs.lon }],
        avgSpeedKnots: obs.sog || this.config.defaultSpeedKnots,
        avgCourseDegrees: obs.cog || 0,
        gaps: [],
        anomalyScore: 0,
        rawSentences: [rawSentence]
      };

      this.vesselTracks.set(key, track);
    } else {
      // Update existing track
      const prevTime = track.lastSeen;
      
      if (obs.timestamp > prevTime) {
        // Calculate time delta in minutes
        const deltaTimeMinutes = (obs.timestamp.getTime() - prevTime.getTime()) / 60000;

        // Check for gap
        if (deltaTimeMinutes > this.config.portBufferMinutes && 
            deltaTimeMinutes < this.config.maxGapMinutes * 2) {
          track.gaps.push({
            start: prevTime,
            end: obs.timestamp,
            durationMinutes: deltaTimeMinutes,
            severity: Math.min(deltaTimeMinutes / this.config.maxGapMinutes, 1.0),
            rawSentences: [rawSentence]
          });

          // Update average speed (weighted by time)
          const prevDuration = track.lastSeen.getTime() - track.firstSeen.getTime();
          const newAvgSpeed = ((track.avgSpeedKnots * prevDuration + 
                               (obs.sog || 0) * deltaTimeMinutes) / 
                              (prevDuration + deltaTimeMinutes));
          
          track.avgSpeedKnots = newAvgSpeed;
        } else if (deltaTimeMinutes > this.config.maxGapMinutes * 2) {
          // Large gap - significant anomaly
          track.gaps.push({
            start: prevTime,
            end: obs.timestamp,
            durationMinutes: deltaTimeMinutes,
            severity: Math.min(deltaTimeMinutes / (this.config.maxGapMinutes * 2), 1.0),
            rawSentences: [rawSentence]
          });

          // Recalculate average speed with large gap excluded
          const prevDuration = track.lastSeen.getTime() - track.firstSeen.getTime();
          const newAvgSpeed = ((track.avgSpeedKnots * prevDuration + 
                               (obs.sog || 0) * deltaTimeMinutes) / 
                              (prevDuration + deltaTimeMinutes));
          
          track.avgSpeedKnots = newAvgSpeed;
        }

        // Update last seen and observations
        track.lastSeen = obs.timestamp;
        track.observations++;
      } else {
        // Out of order - adjust timestamps
        const timeDiff = prevTime.getTime() - obs.timestamp.getTime();
        
        if (timeDiff < 60000) { // Within 1 minute, assume clock drift
          track.lastSeen = new Date(prevTime.getTime() + timeDiff);
          
          // Adjust previous position timestamp
          if (track.positions.length > 0) {
            const lastPos = track.positions[track.positions.length - 1];
            lastPos.time = new Date(lastPos.time.getTime() + timeDiff);
          }
        } else {
          // Large offset - treat as gap
          track.gaps.push({
            start: prevTime,
            end: obs.timestamp,
            durationMinutes: Math.abs(timeDiff) / 60000,
            severity: 1.0,
            rawSentences: [rawSentence]
          });

          track.lastSeen = new Date(prevTime.getTime() + timeDiff);
        }
      }
    }

    // Add position if we have coordinates
    if (obs.lat !== undefined && obs.lon !== undefined) {
      const lastPos = track.positions[track.positions.length - 1];
      
      // Check for significant position change
      if (!lastPos || 
          Math.abs(obs.lat - lastPos.lat!) > 0.0001 || 
          Math.abs(obs.lon - lastPos.lon!) > 0.0001) {
        
        track.positions.push({
          time: obs.timestamp,
          lat: obs.lat!,
          lon: obs.lon!
        });

        // Calculate speed between positions if possible
        if (lastPos && lastPos.time !== obs.timestamp) {
          const distanceNauticalMiles = this.calculateDistance(lastPos.lat!, lastPos.lon!, 
                                                               obs.lat, obs.lon);
          const timeHours = (obs.timestamp.getTime() - lastPos.time.getTime()) / 3600000;
          
          if (timeHours > 0) {
            track.avgSpeedKnots = Math.max(track.avgSpeedKnots, 
                                          distanceNauticalMiles / timeHours);
          }
        }
      } else if (!lastPos) {
        // First position with coordinates
        track.positions.push({
          time: obs.timestamp,
          lat: obs.lat!,
          lon: obs.lon!
        });
      }
    }

    return track;
  }

  /**
   * Calculate distance between two positions in nautical miles
   */
  private calculateDistance(lat1: number, lon1: number, 
                           lat2: number, lon2: number): number {
    const R = 3440.065; // Earth radius in nautical miles
    
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * 
              Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    
    return R * c;
  }

  /**
   * Check for gaps in vessel track and generate gap events
   */
  private checkForGaps(track: VesselTrack): GapEvent[] {
    const newEvents: GapEvent[] = [];

    // Analyze each gap
    for (const gap of track.gaps) {
      // Calculate anomaly score for this gap
      let baseScore = 0;
      
      // Duration component (max 40 points)
      const durationScore = Math.min(gap.durationMinutes * 2, 40);
      baseScore += durationScore;

      // Severity component (max 30 points)
      const severityScore = gap.severity * 30;
      baseScore += severityScore;

      // Speed context - faster vessels with gaps are more suspicious
      if (track.avgSpeedKnots > 25) {
        baseScore += 10;
      } else if (track.avgSpeedKnots < 5 && gap.durationMinutes > 10) {
        // Slow vessel with long gap - possibly anchored or disabled
        baseScore += 5;
      }

      // Frequency penalty - recurring gaps are worse
      const frequencyPenalty = Math.min(
        (track.gaps.filter(g => g.start < gap.end).length - 1) * 3, 
        20
      );
      baseScore += frequencyPenalty;

      // Cap at 10