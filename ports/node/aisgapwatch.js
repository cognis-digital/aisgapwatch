#!/usr/bin/env node
"use strict";
/*
 * aisgapwatch — Node.js port of the core CLI surface.
 *
 * Detect & score AIS transponder-gap anomalies in vessel tracks. Mirrors the
 * Python reference (haversine_nm, score_gap, detect_gaps) and the CLI's table
 * and JSON output with the same pipeline-friendly exit codes (2 = gaps found,
 * 0 = clean). Dependency-free: Node stdlib only, no network.
 *
 * Usage:
 *   node aisgapwatch.js FILE [--min-gap S] [--min-score X] [--top N] [--json]
 *   node aisgapwatch.js -            # read from stdin
 */

const fs = require("fs");

const EARTH_RADIUS_NM = 3440.065;

function haversineNm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180.0;
  const rlat1 = lat1 * r;
  const rlat2 = lat2 * r;
  const dlat = (lat2 - lat1) * r;
  const dlon = (lon2 - lon1) * r;
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(rlat1) * Math.cos(rlat2) * Math.sin(dlon / 2) ** 2;
  const c = 2 * Math.asin(Math.min(1, Math.sqrt(a)));
  return EARTH_RADIUS_NM * c;
}

const DEFAULTS = {
  durationFullH: 6.0,
  speedImpossibleKn: 40.0,
  distanceFullNm: 50.0,
  wDuration: 0.4,
  wSpeed: 0.4,
  wDistance: 0.2,
};

function sat(value, full) {
  if (value <= 0) return 0.0;
  return Math.min(1.0, value / full);
}

function scoreGap(durationS, distanceNm, impliedSpeedKn, cfg = DEFAULTS) {
  const d = sat(durationS / 3600.0, cfg.durationFullH);
  const s = sat(impliedSpeedKn, cfg.speedImpossibleKn);
  const x = sat(distanceNm, cfg.distanceFullNm);
  const score = cfg.wDuration * d + cfg.wSpeed * s + cfg.wDistance * x;
  const reasons = [];
  if (d >= 0.5) reasons.push(`long-duration-${(durationS / 3600.0).toFixed(1)}h`);
  if (s >= 0.5) reasons.push(`implied-speed-${Math.round(impliedSpeedKn)}kn`);
  if (x >= 0.5) reasons.push(`position-jump-${Math.round(distanceNm)}nm`);
  return { score: Math.round(Math.min(1.0, score) * 1e4) / 1e4, reasons };
}

function parseTimestamp(raw) {
  let s = raw.trim();
  // Node's Date parses ISO-8601; a bare timestamp without zone is treated as
  // UTC to match the Python reference ("naive == UTC").
  if (s.endsWith("Z")) {
    // already UTC
  } else if (!/[+-]\d\d:?\d\d$/.test(s)) {
    s = s + "Z";
  }
  const ms = Date.parse(s);
  if (Number.isNaN(ms)) throw new Error(`bad timestamp: ${raw}`);
  return ms;
}

function isInt(s) {
  return /^-?\d+$/.test(s.trim());
}

function parseLine(line) {
  const parts = line.split(",").map((p) => p.trim());
  if (parts.length < 4) throw new Error(`expected 4 fields, got ${parts.length}`);
  const ts = parseTimestamp(parts[0]);
  const mmsi = parseInt(parts[1], 10);
  const lat = parseFloat(parts[2]);
  const lon = parseFloat(parts[3]);
  if (!(lat >= -90 && lat <= 90)) throw new Error(`latitude out of range: ${lat}`);
  if (!(lon >= -180 && lon <= 180)) throw new Error(`longitude out of range: ${lon}`);
  if (!(mmsi > 0)) throw new Error(`mmsi must be positive: ${mmsi}`);
  return { ts, mmsi, lat, lon };
}

function parseText(text, strict = true) {
  const pings = [];
  let seenData = false;
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || line.startsWith("#")) continue;
    if (!seenData) {
      const fields = line.split(",").map((p) => p.trim());
      if (fields.length >= 2 && !isInt(fields[1])) continue; // header
    }
    try {
      pings.push(parseLine(line));
      seenData = true;
    } catch (e) {
      if (strict) throw new Error(`line ${i + 1}: ${e.message}`);
    }
  }
  return pings;
}

function impliedSpeedKn(distanceNm, durationS) {
  const hours = durationS / 3600.0;
  if (hours <= 0) return 0.0;
  return distanceNm / hours;
}

function detectGaps(pings, { minGapS = 1800.0, minScore = 0.0, cfg = DEFAULTS } = {}) {
  if (!(minGapS > 0)) throw new Error("minGapS must be positive");
  const byVessel = new Map();
  for (const p of pings) {
    if (!byVessel.has(p.mmsi)) byVessel.set(p.mmsi, []);
    byVessel.get(p.mmsi).push(p);
  }
  const gaps = [];
  for (const [mmsi, track] of byVessel) {
    track.sort((a, b) => a.ts - b.ts);
    for (let i = 1; i < track.length; i++) {
      const prev = track[i - 1];
      const cur = track[i];
      const durationS = (cur.ts - prev.ts) / 1000.0;
      if (durationS <= minGapS) continue;
      const dist = haversineNm(prev.lat, prev.lon, cur.lat, cur.lon);
      const speed = impliedSpeedKn(dist, durationS);
      const { score, reasons } = scoreGap(durationS, dist, speed, cfg);
      if (score < minScore) continue;
      gaps.push({
        mmsi,
        start: new Date(prev.ts).toISOString(),
        end: new Date(cur.ts).toISOString(),
        duration_s: durationS,
        duration_h: Math.round((durationS / 3600.0) * 1e3) / 1e3,
        distance_nm: Math.round(dist * 1e3) / 1e3,
        implied_speed_kn: Math.round(speed * 1e3) / 1e3,
        start_pos: [prev.lat, prev.lon],
        end_pos: [cur.lat, cur.lon],
        score,
        reasons,
      });
    }
  }
  gaps.sort((a, b) => b.score - a.score);
  return gaps;
}

function formatTable(gaps) {
  if (gaps.length === 0) return "no gaps found";
  const rows = [
    "score  mmsi       duration  dist_nm  impl_kn  reasons",
    "-----  ---------  --------  -------  -------  -------",
  ];
  for (const g of gaps) {
    rows.push(
      `${g.score.toFixed(2).padEnd(5)}  ${String(g.mmsi).padEnd(9)}  ` +
        `${g.duration_h.toFixed(2).padStart(6)}h  ` +
        `${g.distance_nm.toFixed(1).padStart(7)}  ` +
        `${g.implied_speed_kn.toFixed(1).padStart(7)}  ` +
        `${g.reasons.join(",") || "-"}`
    );
  }
  return rows.join("\n");
}

function parseArgs(argv) {
  const opts = { minGap: 1800.0, minScore: 0.0, top: 0, json: false, lenient: false, file: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--min-gap") opts.minGap = parseFloat(argv[++i]);
    else if (a === "--min-score") opts.minScore = parseFloat(argv[++i]);
    else if (a === "--top") opts.top = parseInt(argv[++i], 10);
    else if (a === "--json") opts.json = true;
    else if (a === "--lenient") opts.lenient = true;
    else if (a === "--version") { console.log("aisgapwatch 0.2.0 (node port)"); process.exit(0); }
    else if (!a.startsWith("--")) opts.file = a;
  }
  return opts;
}

function main(argv) {
  const opts = parseArgs(argv);
  if (!opts.file) {
    console.error("usage: aisgapwatch.js FILE [--min-gap S] [--min-score X] [--top N] [--json]");
    return 1;
  }
  const text = opts.file === "-" ? fs.readFileSync(0, "utf8") : fs.readFileSync(opts.file, "utf8");
  const pings = parseText(text, !opts.lenient);
  let gaps = detectGaps(pings, { minGapS: opts.minGap, minScore: opts.minScore });
  if (opts.top > 0) gaps = gaps.slice(0, opts.top);
  if (opts.json) {
    console.log(JSON.stringify({ file: opts.file, pings: pings.length, gaps }, null, 2));
  } else {
    console.log(`${pings.length} pings · ${gaps.length} gap(s)\n`);
    console.log(formatTable(gaps));
  }
  return gaps.length ? 2 : 0;
}

module.exports = { haversineNm, scoreGap, parseText, parseLine, detectGaps, formatTable, main };

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
