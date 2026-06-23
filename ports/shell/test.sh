#!/usr/bin/env bash
# Smoke test for the shell port. Exercises the same behaviours as the other
# ports: a real gap is found (exit 2, correct table), a quiet track is clean
# (exit 0), and --min-score filtering works. Pure shell + awk, no network.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SH="$HERE/aisgapwatch.sh"
pass=0
fail=0

check() {
  if [ "$1" = "$2" ]; then pass=$((pass+1));
  else fail=$((fail+1)); echo "FAIL: $3 (expected '$2', got '$1')"; fi
}

# --- 1. a long dark gap with an implausible jump is found, exit code 2 ---
out=$(printf 'timestamp,mmsi,lat,lon\n2026-06-01T00:00:00Z,366,37.0,-122.0\n2026-06-01T06:00:00Z,366,40.0,-125.0\n' | "$SH" -)
rc=$?
check "$rc" "2" "gap found exit code"
echo "$out" | grep -q "366" && check "1" "1" "gap row present" || check "0" "1" "gap row present"
echo "$out" | grep -q "implied-speed" && check "1" "1" "implied-speed reason" || check "0" "1" "implied-speed reason"

# --- 2. a quiet, short track is clean, exit code 0 ---
out=$(printf '2026-06-01T00:00:00Z,366,37.0,-122.0\n2026-06-01T00:05:00Z,366,37.0,-122.0\n' | "$SH" -)
rc=$?
check "$rc" "0" "clean track exit code"
echo "$out" | grep -q "no gaps found" && check "1" "1" "clean message" || check "0" "1" "clean message"

# --- 3. --min-score filters everything out ---
out=$(printf '2026-06-01T00:00:00Z,1,0,0\n2026-06-01T00:35:00Z,1,0,0.001\n' | "$SH" - --min-score 0.9)
rc=$?
check "$rc" "0" "min-score filters to clean"

# --- 4. header/comment/blank skipped, multiple vessels ---
out=$(printf '# note\ntimestamp,mmsi,lat,lon\n\n2026-06-01T00:00:00Z,1,0,0\n2026-06-01T05:00:00Z,1,5,5\n2026-06-01T00:00:00Z,2,10,10\n2026-06-01T05:00:00Z,2,10.05,10.05\n' | "$SH" -)
echo "$out" | grep -q " 1 " && check "1" "1" "vessel 1 surfaces" || check "0" "1" "vessel 1 surfaces"

# --- 5. --version ---
v=$("$SH" --version)
echo "$v" | grep -q "0.2.0" && check "1" "1" "version string" || check "0" "1" "version string"

echo "shell port: $pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
