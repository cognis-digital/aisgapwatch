/*
 * polyglot/c/track_continuity_checker.c
 * 
 * AIS Transponder-Gap Anomaly Detector & Scorer
 * Maritime Situational Awareness / Defensive OSINT Tool
 * 
 * Detects breaks in vessel track continuity, scores anomaly severity,
 * and identifies potential spoofing or transponder failures.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <ctype.h>
#include <errno.h>

/* ==================== CONFIGURATION ==================== */

#define DEFAULT_LAT_MIN  -90.0
#define DEFAULT_LAT_MAX   90.0
#define DEFAULT_LON_MIN -180.0
#define DEFAULT_LON_MAX  180.0

#define MIN_TIME_GAP_SEC  1.0    /* Minimum expected time between fixes */
#define MAX_TIME_GAP_SEC  3600.0 /* Maximum allowed gap before flagging */

/* Gap scoring thresholds (in nautical miles) */
#define GAP_NORMAL_MAX     2.0   /* Normal operation */
#define GAP_WARNING        5.0   /* Investigate */
#define GAP_CRITICAL       10.0  /* Likely anomaly/spoofing */

/* Velocity sanity check bounds (knots) */
#define MIN_VELOCITY_KTS    0.1
#define MAX_VELOCITY_KTS  120.0

/* ==================== DATA STRUCTURES ==================== */

typedef enum {
    GAP_STATUS_NORMAL,
    GAP_STATUS_WARNING,
    GAP_STATUS_CRITICAL,
    GAP_STATUS_UNKNOWN
} GapStatus;

typedef struct {
    double lat;
    double lon;
    time_t timestamp;
    uint16_t mmsi;
    int32_t  speed_over_ground;   /* SOG in knots */
    int8_t   course_over_ground;  /* COG in degrees */
} AISFix;

typedef struct {
    double lat_min, lat_max;
    double lon_min, lon_max;
    time_t start_time;
    time_t end_time;
    uint16_t mmsi;
    int32_t  avg_speed_kts;
    int      fix_count;
    int      warning_count;
    int      critical_count;
    double   total_gap_nm;
    double   max_gap_nm;
    GapStatus overall_status;
} TrackSummary;

typedef struct {
    AISFix *fixes;
    size_t capacity;
    size_t count;
    uint16_t mmsi;
    time_t start_time;
    time_t end_time;
    int32_t  avg_speed_kts;
    int      warning_count;
    int      critical_count;
    double   total_gap_nm;
    double   max_gap_nm;
    GapStatus overall_status;
} TrackBuffer;

/* ==================== UTILITY FUNCTIONS ==================== */

static inline double deg_to_rad(double deg) {
    return deg * M_PI / 180.0;
}

static inline double rad_to_deg(double rad) {
    return rad * 180.0 / M_PI;
}

/* Haversine distance in nautical miles */
static double haversine_nm(double lat1, double lon1, 
                           double lat2, double lon2) {
    double dlat = deg_to_rad(lat2 - lat1);
    double dlon = deg_to_rad(lon2 - lon1);
    
    double a = sin(dlat/2.0)*sin(dlat/2.0) +
               cos(deg_to_rad(lat1)) * cos(deg_to_rad(lat2)) *
               sin(dlon/2.0) * sin(dlon/2.0);
    
    double c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a));
    
    /* Earth radius in nautical miles */
    return (6378.137 / 1852.0) * c;
}

/* Time difference in seconds, handling wraparound */
static int64_t time_diff_sec(time_t t1, time_t t2) {
    int64_t diff = (int64_t)t2 - (int64_t)t1;
    
    /* Handle day boundary crossing */
    if (diff < 0 && t2 > t1) {
        diff += 86400;
    } else if (diff > 0 && t2 < t1) {
        diff -= 86400;
    }
    
    return diff;
}

/* ==================== PARSING FUNCTIONS ==================== */

typedef struct {
    double lat, lon;
    time_t timestamp;
    uint16_t mmsi;
    int32_t sog;
    int8_t cog;
    int valid;  /* 0 = invalid/empty fix */
} ParsedFix;

/* Parse NMEA GGA sentence (primary source for position/time) */
static int parse_gga(const char *line, ParsedFix *fix) {
    if (!line || !fix) return -1;
    
    /* Format: $GPGGA,hhmmss.ss,lat,N/S,lon,E/W,...,age,fix,type*hh\r\n */
    static const char *fields[] = {
        "06", "02", "03", "04", "05", "07", "08"  /* time, lat, hemi, lon, hemi, sog, cog */
    };
    
    int field_idx = 0;
    char *token = (char *)line;
    const char *field_ptr = fields[0];
    
    while (*token && field_idx < 8) {
        if (*field_ptr == '\0') break;
        
        /* Skip comma */
        if (*token == ',') {
            token++;
            field_ptr++;
            continue;
        }
        
        /* Extract numeric field */
        char *endptr;
        double value = strtod(token, &endptr);
        
        switch (field_idx) {
            case 0:  /* Time - convert to seconds since midnight */
                fix->timestamp = time(NULL);
                break;
            
            case 1:  /* Latitude */
                if (!isnan(value)) {
                    fix->lat = value;
                    fix->valid = 1;
                } else {
                    fix->lat = DEFAULT_LAT_MIN;
                    fix->valid = 0;
                }
                break;
            
            case 2:  /* Latitude hemisphere */
                if (value == 'N') fix->lat = fabs(fix->lat);
                else if (value == 'S') fix->lat = -fabs(fix->lat);
                break;
            
            case 3:  /* Longitude */
                if (!isnan(value)) {
                    fix->lon = value;
                    fix->valid = 1;
                } else {
                    fix->lon = DEFAULT_LON_MIN;
                    fix->valid = 0;
                }
                break;
            
            case 4:  /* Longitude hemisphere */
                if (value == 'E') fix->lon = fabs(fix->lon);
                else if (value == 'W') fix->lon = -fabs(fix->lon);
                break;
            
            case 5:  /* Speed over ground (knots) */
                if (!isnan(value)) {
                    fix->sog = (int32_t)value;
                } else {
                    fix->sog = 0;
                }
                break;
            
            case 6:  /* Course over ground (degrees) */
                if (!isnan(value)) {
                    fix->cog = (int8_t)(value % 360.0);
                } else {
                    fix->cog = 0;
                }
                break;
            
            case 7:  /* Quality indicator - skip */
                field_idx++;
                break;
        }
        
        token = endptr;
        if (*token == ',') token++;
        else if (!*token) break;
        
        field_ptr++;
        field_idx++;
    }
    
    return fix->valid ? 0 : -1;
}

/* Parse NMEA RMC sentence (alternative source) */
static int parse_rmc(const char *line, ParsedFix *fix) {
    if (!line || !fix) return -1;
    
    /* Format: $GPRMC,hhmmss.ss,lat,N,S,lon,E,W,...*hh\r\n */
    static const char *fields[] = {
        "06", "02", "03", "04", "05"  /* time, lat, hemi, lon, hemi */
    };
    
    int field_idx = 0;
    char *token = (char *)line;
    const char *field_ptr = fields[0];
    
    while (*token && field_idx < 6) {
        if (*field_ptr == '\0') break;
        
        if (*token == ',') {
            token++;
            field_ptr++;
            continue;
        }
        
        char *endptr;
        double value = strtod(token, &endptr);
        
        switch (field_idx) {
            case 0:  /* Time */
                fix->timestamp = time(NULL);
                break;
            
            case 1:  /* Latitude */
                if (!isnan(value)) {
                    fix->lat = value;
                    fix->valid = 1;
                } else {
                    fix->lat = DEFAULT_LAT_MIN;
                    fix->valid = 0;
                }
                break;
            
            case 2:  /* Latitude hemisphere */
                if (value == 'N') fix->lat = fabs(fix->lat);
                else if (value == 'S') fix->lat = -fabs(fix->lat);
                break;
            
            case 3:  /* Longitude */
                if (!isnan(value)) {
                    fix->lon = value;
                    fix->valid = 1;
                } else {
                    fix->lon = DEFAULT_LON_MIN;
                    fix->valid = 0;
                }
                break;
            
            case 4:  /* Longitude hemisphere */
                if (value == 'E') fix->lon = fabs(fix->lon);
                else if (value == 'W') fix->lon = -fabs(fix->lon);
                break;
        }
        
        token = endptr;
        if (*token == ',') token++;
        else if (!*token) break;
        
        field_ptr++;
        field_idx++;
    }
    
    /* Default values from RMC */
    fix->sog = 0;
    fix->cog = 0;
    
    return fix->valid ? 0 : -1;
}

/* Try both parsers and return best result */
static int parse_nmea_line(const char *line, ParsedFix *fix) {
    if (!line || !fix) return -1;
    
    /* Clear previous values */
    memset(fix, 0, sizeof(*fix));
    fix->valid = 0;
    
    /* Try GGA first (more complete data) */
    int gga_ok = parse_gga(line, fix);
    if (gga_ok && fix->valid) return 0;
    
    /* Fall back to RMC */
    int rmc_ok = parse_rmc(line, fix);
    if (rmc_ok && fix->valid) return 0;
    
    /* Try to extract MMSI from any valid-looking line */
    char *mmsi_ptr = strstr(line, ",123456789");
    if (!mmsi_ptr) {
        mmsi_ptr = strstr(line, ",12345678");
    }
    
    /* Extract MMSI from position fields */
    char *pos_start = strchr(line, ',');
    while (pos_start && pos_start[0] == ',') {
        pos_start++;
        
        uint16_t mmsi_val;
        if (sscanf(pos_start, "%hu", &mmsi_val) == 1) {
            fix->mmsi = mmsi_val;
        } else {
            break;
        }
    }
    
    /* If we got a reasonable MMSI and some coordinates, accept it */
    if (fix->mmsi > 0 && fix->lat != DEFAULT_LAT_MIN) {
        fix->valid = 1;
        return 0;
    }
    
    return -1;
}

/* ==================== TRACK BUFFER MANAGEMENT ==================== */

static void track_init(TrackBuffer *track, uint16_t mmsi) {
    if (!track) return;
    
    memset(track, 0, sizeof(*track));
    track->mmsi = mmsi;
    track->capacity = 256;
    track->fixes = malloc(sizeof(AISFix) * track->capacity);
    track->count = 0;
}

static void track_cleanup(TrackBuffer *track) {
    if (!track || !track->fixes) return;
    
    free(track->fixes);
    track->fixes = NULL;
    track->count = 0;
}

/* Add a new fix to the buffer */
static int track_add_fix(TrackBuffer *track, ParsedFix *parsed) {
    if (!track || !parsed) return -1;
    
    /* Check capacity and expand if needed */
    if (track->count >= track->capacity) {
        size_t new_cap = track->capacity * 2;
        AISFix *new_fixes = realloc(track->fixes, sizeof(AISFix) * new_cap);
        
        if (!new_fixes) return -1;
        
        track->fixes = new_fixes;
        track->capacity = new_cap;
    }
    
    /* Convert parsed to stored format */
    AISFix *fix = &track->fixes[track->count];
    fix->lat = parsed->lat;
    fix->lon = parsed->lon;
    fix->timestamp = parsed->timestamp;
    fix->mmsi = track->mmsi;
    fix->speed_over_ground = parsed->sog;
    fix->course_over_ground = parsed->cog;
    
    /* Update time bounds */
    if (track->count == 0) {
        track->start_time = parsed->timestamp;
    } else {
        int64_t diff = time_diff_sec(track->end_time, parsed->timestamp);
        if (diff > 0) {
            track->end_time = parsed->timestamp;
        }
    }
    
    /* Update average speed */
    if (track->count == 1 || !track->avg_speed_kts) {
        track->avg_speed_kts = parsed->sog;
    } else {
        int32_t new_avg = (int32_t)((track->avg_speed_kts * (track->count - 1) + 
                                    parsed->sog) / track->count);
        if (new_avg != track->avg_speed_kts) {
            track->avg_speed_kts = new_avg;
        }
    }
    
    track->count++;
    return 0;
}

/* ==================== GAP ANALYSIS ENGINE ==================== */

static void analyze_gaps(TrackBuffer *track, TrackSummary *summary) {
    if (!track || !track->fixes || track->count < 2 || !summary) return;
    
    memset(summary, 0, sizeof(*summary));
    summary->mmsi = track->mmsi;
    summary->start_time = track->start_time;
    summary->end_time = track->end_time;
    summary->avg_speed_kts = track->avg_speed_kts;
    
    double prev_lat, prev_lon;
    int64_t prev_time;
    int first_fix = 1;
    
    for (size_t i = 0; i < track->count; i++) {
        AISFix *fix = &track->fixes[i];
        
        /* Skip invalid fixes */
        if (!first_fix && fix->lat == DEFAULT_LAT_MIN) continue;
        
        double gap_nm, time_gap_sec;
        
        if (first_fix) {
            prev_lat = fix->lat;
            prev_lon = fix->lon;
            prev_time = fix->timestamp;
            first_fix = 0;
            continue;
        }
        
        /* Calculate spatial and temporal gaps */
        gap_nm = haversine_nm(prev_lat, prev_lon, 
                             fix->lat, fix->lon);
        time_gap_sec = (int64_t)(fix->timestamp - prev_time);
        
        if (time_gap_sec < 0) {
            /* Time went backwards - likely duplicate or sensor glitch */
            gap_nm = 0.0;
            time_gap_sec = 0;
        } else if (time_gap_sec > MAX_TIME_GAP_SEC) {
            /* Massive time jump - treat as critical anomaly */
            summary->max_gap_nm = fmax(summary->max_gap_nm, GAP_CRITICAL);
            summary->critical_count++;
            gap_nm = GAP_CRITICAL;  /* Penalty for huge time gap */
        } else if (time_gap_sec < MIN_TIME_GAP_SEC) {
            /* Near-duplicate fix - small penalty */
            gap_nm *= 0.1;  /* Reduce impact of near-instant repeats */
        }
        
        /* Accumulate statistics */
        summary->total_gap_nm += gap_nm;
        summary->max_gap_nm = fmax(summary->max_gap_nm, gap