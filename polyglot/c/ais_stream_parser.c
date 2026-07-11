/*
 * ais_stream_parser.c - AIS Stream Parser & Gap Anomaly Detector
 * 
 * Parses NMEA 0183 AIS messages, tracks vessel positions, and detects
 * anomalous "gaps" where a vessel suddenly appears far from expected location.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <ctype.h>

#define MAX_VESSELS 1024
#define MAX_SENTENCE_LEN 256
#define GAP_THRESHOLD_KM 5.0      /* Alert if gap > 5km in short time */
#define GAP_WARN_THRESHOLD_KM 3.0 /* Warn at 3km */
#define NORMAL_MAX_SPEED_KNOTS 25.0
#define MIN_TIME_INTERVAL_SEC 1.0  /* Minimum delta t for valid comparison */

/* Coordinate conversion helpers */
static double deg_to_rad(double deg) {
    return deg * M_PI / 180.0;
}

static double rad_to_deg(double rad) {
    return rad * 180.0 / M_PI;
}

/* Haversine distance in km between two lat/lon pairs */
static double haversine_km(double lat1, double lon1, double lat2, double lon2) {
    const double R = 6371.0; /* Earth radius in km */
    
    double dlat = deg_to_rad(lat2 - lat1);
    double dlon = deg_to_rad(lon2 - lon1);
    
    double a = sin(dlat/2)*sin(dlat/2) + 
               cos(deg_to_rad(lat1))*cos(deg_to_rad(lat2)) *
               sin(dlon/2)*sin(dlon/2);
    
    return 2.0 * R * asin(sqrt(a));
}

/* Calculate bearing from point A to B */
static double bearing(double lat1, double lon1, double lat2, double lon2) {
    double dlon = deg_to_rad(lon2 - lon1);
    double y = sin(dlon) * cos(deg_to_rad(lat2));
    double x = cos(deg_to_rad(lat1)) * sin(deg_to_rad(lat2)) -
               sin(deg_to_rad(lat1)) * cos(deg_to_rad(lat2)) * cos(dlon);
    
    return rad_to_deg(atan2(y, x) + M_PI);
}

/* Normalize angle to 0-360 */
static double normalize_angle(double ang) {
    while (ang < 0.0) ang += 360.0;
    while (ang >= 360.0) ang -= 360.0;
    return ang;
}

/* Vessel tracking state */
typedef struct {
    unsigned long mmsi;              /* Maritime Mobile Service Identity */
    double lat, lon;                 /* Current position */
    double last_lat, last_lon;       /* Previous position for gap detection */
    time_t last_time;               /* Timestamp of last fix */
    double speed_knots;             /* Current SOG (Speed Over Ground) */
    int active;                     /* Is vessel currently tracked? */
} VesselState;

/* Anomaly record */
typedef struct {
    unsigned long mmsi;
    time_t detect_time;
    double gap_km;
    double prev_lat, prev_lon;
    double curr_lat, curr_lon;
    int severity; /* 1=warn, 2=alert, 3=critical */
} AnomalyRecord;

/* Global state */
static VesselState vessels[MAX_VESSELS];
static int vessel_count = 0;
static AnomalyRecord anomalies[64];
static int anomaly_count = 0;

/* Find or create vessel entry by MMSI */
static inline VesselState* find_or_create_vessel(unsigned long mmsi) {
    for (int i = 0; i < vessel_count; i++) {
        if (!vessels[i].active && vessels[i].mmsi == 0) {
            vessels[i].mmsi = mmsi;
            return &vessels[i];
        }
    }
    
    /* No free slot, try to reuse inactive */
    for (int i = 0; i < vessel_count; i++) {
        if (!vessels[i].active) {
            vessels[i].mmsi = mmsi;
            return &vessels[i];
        }
    }
    
    /* Expand array */
    VesselState* new_vessels = realloc(vessels, (MAX_VESSELS + 1024) * sizeof(VesselState));
    if (!new_vessels) {
        fprintf(stderr, "Memory reallocation failed\n");
        exit(1);
    }
    
    vessels = new_vessels;
    VesselState* v = &vessels[vessel_count];
    v->mmsi = mmsi;
    vessel_count++;
    return v;
}

/* Initialize vessel state */
static inline void init_vessel(VesselState* v, unsigned long mmsi) {
    memset(v, 0, sizeof(VesselState));
    v->mmsi = mmsi;
    v->active = 1;
}

/* Calculate expected drift distance based on time since last fix */
static double expected_drift_km(double speed_knots, double delta_t_sec) {
    if (speed_knots <= 0.0 || delta_t_sec < MIN_TIME_INTERVAL_SEC) {
        return 0.1; /* Default small tolerance */
    }
    
    /* Convert knots to km/h, then multiply by hours elapsed */
    double speed_kmh = speed_knots * 1.852;
    double hours = delta_t_sec / 3600.0;
    return speed_kmh * hours + 0.5; /* Add small buffer for current */
}

/* Calculate bearing difference (accounting for wrap-around) */
static inline double bearing_diff(double b1, double b2) {
    double diff = normalize_angle(b1 - b2);
    if (diff > 180.0) diff = 360.0 - diff;
    return diff;
}

/* Check for anomalous gap */
static int check_gap(VesselState* v, time_t now) {
    double delta_t = (double)(now - v->last_time);
    
    if (delta_t < MIN_TIME_INTERVAL_SEC || !v->active) {
        return 0; /* Not enough data yet */
    }
    
    double expected_drift = expected_drift_km(v->speed_knots, delta_t);
    double actual_gap = haversine_km(v->last_lat, v->last_lon, 
                                     v->lat, v->lon);
    
    int severity = 0;
    
    if (actual_gap > GAP_THRESHOLD_KM) {
        /* Critical gap */
        severity = 3;
    } else if (actual_gap > GAP_WARN_THRESHOLD_KM) {
        /* Warning level */
        severity = 2;
    } else if (actual_gap > expected_drift * 1.5 && actual_gap > 0.5) {
        /* Moderate anomaly - exceeds expected drift significantly */
        severity = 1;
    }
    
    return severity;
}

/* Record an anomaly if detected */
static void record_anomaly(VesselState* v, int severity, time_t now) {
    if (anomaly_count >= MAX_VESSELS / 4) {
        /* Rotate buffer - keep most recent */
        for (int i = 0; i < anomaly_count - 1; i++) {
            anomalies[i] = anomalies[i + 1];
        }
        anomaly_count--;
    }
    
    AnomalyRecord* rec = &anomalies[anomaly_count++];
    rec->mmsi = v->mmsi;
    rec->detect_time = now;
    rec->gap_km = haversine_km(v->last_lat, v->last_lon, v->lat, v->lon);
    rec->prev_lat = v->last_lat;
    rec->prev_lon = v->last_lon;
    rec->curr_lat = v->lat;
    rec->curr_lon = v->lon;
    rec->severity = severity;
}

/* Parse NMEA sentence into fields */
static int parse_nmea(const char* sentence, 
                      double* lat, double* lon, 
                      unsigned long* mmsi,
                      double* sog, double* cog) {
    if (!sentence || strlen(sentence) < 4) return -1;
    
    /* Extract fields based on sentence type */
    int len = (int)strlen(sentence);
    char type[2] = {0};
    char body[MAX_SENTENCE_LEN];
    
    /* Get first character for quick routing */
    type[0] = toupper(sentence[0]);
    
    if (len < 5 || !type[0]) return -1;
    
    /* Extract MMSI from RMA (Reported Manually) or VDM/VDO (Data Messages) */
    unsigned long mmsi_val = 0;
    
    if (type[0] == 'R' && len >= 65) {
        /* RMA - Report Manually: $RMA,<MMSI>,<LAT>,<LON>,...
         * Format: MMSI, Lat, Lon, Speed, Course */
        
        char* p = &sentence[4]; /* Skip header */
        
        /* Parse MMSI (typically 7 digits) */
        if (*p == ',') {
            p++;
            mmsi_val = strtoul(p, NULL, 10);
            p += 25; /* Skip MMSI field */
            
            /* Parse Lat/Lon - format: DDMM.MMMM,DDDMM.MMMM */
            if (*p == ',') {
                p++;
                
                double lat_deg = strtod(p, &p) / 100.0;
                double lon_deg = strtod(p, NULL);
                
                *lat = (int)(lat_deg / 100.0) + 
                        ((lat_deg - (int)lat_deg) < 0 ? -1 : 1) * 
                        (int)(lat_deg / 100.0); /* Handle N/S */
                *lon = lon_deg;
                
                /* Parse speed and course if present */
                if (*p == ',') {
                    p++;
                    *sog = strtod(p, NULL) / 100.0;
                    
                    if (*p == ',') {
                        p++;
                        *cog = strtod(p, NULL);
                    }
                }
            }
        }
    } else if (type[0] == 'V' && len >= 65) {
        /* VDM/VDO - Data Messages: $VDM,<N>,<DATA> or $VDO,...
         * These contain encoded data, need decoding */
        
        char* p = &sentence[4];
        
        if (*p == ',') {
            p++;
            
            /* N field indicates message type (1-256) */
            int n_val = atoi(p);
            p += 3; /* Skip N, comma */
            
            if (n_val >= 1 && n_val <= 256) {
                /* Decode VDM/VDO - simplified decoder for common types */
                
                /* Type 1-4: Position report with MMSI */
                if ((n_val & 0xF0) == 0x10 || 
                    (n_val & 0xF0) == 0x20 ||
                    (n_val & 0xF0) == 0x30 ||
                    (n_val & 0xF0) == 0x40) {
                    
                    /* Extract MMSI from bits 16-22 of data field */
                    unsigned long temp = strtoul(p, NULL, 16);
                    mmsi_val = (temp >> 16) & 0xFFFFF;
                    
                    p += 35; /* Skip to position fields */
                    
                    if (*p == ',') {
                        p++;
                        
                        /* Latitude: DDMM.MMMM */
                        double lat_deg = strtod(p, &p);
                        *lat = (int)(lat_deg / 100.0) + 
                                ((lat_deg - (int)lat_deg) < 0 ? -1 : 1) * 
                                (int)(lat_deg / 100.0);
                        
                        if (*p == ',') {
                            p++;
                            
                            /* Longitude: DDDMM.MMMM */
                            double lon_deg = strtod(p, NULL);
                            *lon = lon_deg;
                            
                            /* Speed and course follow in similar format */
                            if (*p == ',') {
                                p++;
                                *sog = strtod(p, NULL) / 100.0;
                                
                                if (*p == ',') {
                                    p++;
                                    *cog = strtod(p, NULL);
                                }
                            }
                        }
                    }
                }
            }
        }
    } else if (type[0] == 'G' && len >= 50) {
        /* GGA - Global Positioning System Fix Data */
        /* $GGA,<TIME>,<LAT>,<LON>,...
         * Less reliable for vessel tracking but can provide position */
        
        char* p = &sentence[4];
        
        if (*p == ',') {
            p++;
            
            double lat_deg = strtod(p, &p);
            double lon_deg = strtod(p, NULL);
            
            *lat = (int)(lat_deg / 100.0) + 
                    ((lat_deg - (int)lat_deg) < 0 ? -1 : 1) * 
                    (int)(lat_deg / 100.0);
            *lon = lon_deg;
            
            /* GGA doesn't include MMSI, use default or track separately */
        }
    } else if (type[0] == 'A' && len >= 45) {
        /* AIVDM - Alternative VDM format (more common in modern AIS) */
        /* $AIVDM,1,3,,A,<DATA>,<CHECKSUM> */
        
        char* p = &sentence[4];
        
        if (*p == ',') {
            p++;
            
            int part_num = atoi(p);
            int total_parts = atoi(&p[2]);
            int msg_type = atoi(&p[5]);
            int data_len = atoi(&p[9]);
            
            /* Part 1, type 1-4 with position data */
            if (part_num == 1 && 
                (msg_type & 0xF0) == 0x10 ||
                (msg_type & 0xF0) == 0x20 ||
                (msg_type & 0xF0) == 0x30 ||
                (msg_type & 0xF0) == 0x40) {
                
                /* Extract MMSI from data field */
                unsigned long temp = strtoul(p, NULL, 16);
                mmsi_val = (temp >> 16) & 0xFFFFF;
                
                p += 35; /* Skip to position fields */
                
                if (*p == ',') {
                    p++;
                    
                    double lat_deg = strtod(p, &p);
                    double lon_deg = strtod(p, NULL);
                    
                    *lat = (int)(lat_deg / 100.0) + 
                            ((lat_deg - (int)lat_deg) < 0 ? -1 : 1) * 
                            (int)(lat_deg / 100.0);
                    *lon = lon_deg;
                    
                    if (*p == ',') {
                        p++;
                        *sog = strtod(p, NULL) / 100.0;
                        
                        if (*p == ',') {
                            p++;
                            *cog = strtod(p, NULL);
                        }
                    }
                }
            }
        }
    } else if (type[0] == 'H' && len >= 45) {
        /* HDM/HDW - Heading/Destination */
        /* Can provide course info but not position */
        
        char* p = &sentence[4];
        
        if (*p == ',') {
            p++;
            
            int type_code = atoi(p);
            double heading = 0.0;
            
            if (type_code == 1) { /* HDM - Heading to Destination */
                heading = strtod(&p[2], NULL) / 100.0;
            } else if (type_code == 2) { /* HDW - Heading Made Good */
                heading = strtod(&p[2], NULL) / 100.0;
            }
            
            *cog = heading;
        }
    }
    
    return mmsi_val ? 1 : 0;
}

/* Process a single NMEA sentence */
static void process_sentence(const char* sentence, time_t now) {
    double lat, lon;
    unsigned long mmsi