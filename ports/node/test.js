#!/usr/bin/env node
"use strict";
/* Smoke test for the Node port — runs under `node --test` (Node 18+). */

const assert = require("node:assert");
const { test } = require("node:test");
const {
  haversineNm,
  scoreGap,
  parseText,
  parseLine,
  detectGaps,
} = require("./aisgapwatch.js");

test("haversine: one degree longitude at equator ~ 60nm", () => {
  assert.ok(Math.abs(haversineNm(0, 0, 0, 1) - 60.0) < 0.2);
});

test("haversine: zero distance", () => {
  assert.ok(haversineNm(10, 20, 10, 20) < 1e-6);
});

test("haversine: symmetric", () => {
  assert.ok(Math.abs(haversineNm(1, 2, 3, 4) - haversineNm(3, 4, 1, 2)) < 1e-9);
});

test("score: impossible speed flags and is bounded", () => {
  const { score, reasons } = scoreGap(600, 20, 120);
  assert.ok(score > 0 && score <= 1);
  assert.ok(reasons.some((r) => r.includes("implied-speed")));
});

test("score: quiet gap is low", () => {
  const { score, reasons } = scoreGap(300, 0.5, 6);
  assert.ok(score < 0.2);
  assert.deepStrictEqual(reasons, []);
});

test("score: all signals max -> 1.0", () => {
  const { score } = scoreGap(6 * 3600, 50, 40);
  assert.ok(Math.abs(score - 1.0) < 1e-9);
});

test("parse: header/comment/blank skipped", () => {
  const pings = parseText(
    "timestamp,mmsi,lat,lon\n# c\n\n2026-06-01T00:00:00Z,1,0,0\n2026-06-01T01:00:00Z,2,1,1\n"
  );
  assert.strictEqual(pings.length, 2);
});

test("parse: strict rejects mid-file garbage", () => {
  assert.throws(() => parseText("2026-06-01T00:00:00Z,1,0,0\nGARBAGE,x,y,z\n"));
});

test("parse: lenient skips bad rows", () => {
  const pings = parseText("2026-06-01T00:00:00Z,1,0,0\njunk\n", false);
  assert.strictEqual(pings.length, 1);
});

test("parseLine rejects out-of-range coords", () => {
  assert.throws(() => parseLine("2026-06-01T00:00:00Z,1,200,0"));
});

test("detect: finds a 5h gap", () => {
  const text =
    "2026-06-01T00:00:00Z,1,37.0,-122.0\n2026-06-01T05:00:00Z,1,37.5,-122.5\n";
  const gaps = detectGaps(parseText(text), { minGapS: 1800 });
  assert.strictEqual(gaps.length, 1);
  assert.strictEqual(gaps[0].mmsi, 1);
  assert.ok(Math.abs(gaps[0].duration_h - 5.0) < 1e-6);
});

test("detect: ignores short gaps", () => {
  const text =
    "2026-06-01T00:00:00Z,1,37.0,-122.0\n2026-06-01T00:10:00Z,1,37.0,-122.01\n";
  assert.strictEqual(detectGaps(parseText(text), { minGapS: 1800 }).length, 0);
});

test("detect: groups by vessel, sorts by score desc", () => {
  const text =
    "2026-06-01T00:00:00Z,1,0,0\n2026-06-01T01:00:00Z,1,5,5\n" +
    "2026-06-01T00:00:00Z,2,0,0\n2026-06-01T02:00:00Z,2,0.05,0.05\n";
  const gaps = detectGaps(parseText(text), { minGapS: 1800 });
  assert.strictEqual(new Set(gaps.map((g) => g.mmsi)).size, 2);
  assert.ok(gaps[0].score >= gaps[gaps.length - 1].score);
});

test("detect: minGapS must be positive", () => {
  assert.throws(() => detectGaps([], { minGapS: 0 }));
});

test("detect: min-score filters", () => {
  const text =
    "2026-06-01T00:00:00Z,1,0,0\n2026-06-01T00:35:00Z,1,0,0.001\n";
  assert.strictEqual(
    detectGaps(parseText(text), { minGapS: 1800, minScore: 0.9 }).length,
    0
  );
});
