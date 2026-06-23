#!/usr/bin/env bash
# aisgapwatch — POSIX shell + awk port of the core CLI surface.
#
# Detect & score AIS transponder-gap anomalies in vessel tracks (defensive
# maritime OSINT). This port exists for the air-gapped / minimal-footprint case:
# a busybox-class box with only sh + awk can triage a track export without
# Python, Node, or Go installed. It mirrors the reference haversine + score +
# detect math and prints the same table with the same exit codes
# (2 = gaps found, 0 = clean). No network access.
#
# Usage:
#   ./aisgapwatch.sh FILE [--min-gap S] [--min-score X] [--top N]
#   cat track.csv | ./aisgapwatch.sh -
set -eu

MIN_GAP=1800
MIN_SCORE=0
TOP=0
FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --min-gap)   MIN_GAP="$2"; shift 2 ;;
    --min-score) MIN_SCORE="$2"; shift 2 ;;
    --top)       TOP="$2"; shift 2 ;;
    --version)   echo "aisgapwatch 0.2.0 (shell port)"; exit 0 ;;
    -h|--help)
      echo "usage: aisgapwatch.sh FILE [--min-gap S] [--min-score X] [--top N]"
      exit 0 ;;
    *)           FILE="$1"; shift ;;
  esac
done

if [ -z "$FILE" ]; then
  echo "usage: aisgapwatch.sh FILE [--min-gap S] [--min-score X] [--top N]" >&2
  exit 1
fi

read_input() {
  if [ "$FILE" = "-" ]; then cat; else cat "$FILE"; fi
}

# The whole pipeline is one awk program: parse -> group by mmsi -> sort by time
# -> walk consecutive pairs -> haversine + score -> emit table. awk's mktime
# converts the ISO timestamp (rewritten to "YYYY MM DD HH MM SS") to epoch.
OUTPUT=$(read_input | awk -v min_gap="$MIN_GAP" -v min_score="$MIN_SCORE" -v top="$TOP" '
function abs(x){ return x<0 ? -x : x }
function min(a,b){ return a<b ? a : b }
function radians(d){ return d * 3.141592653589793 / 180.0 }
function haversine(lat1, lon1, lat2, lon2,   r,rlat1,rlat2,dlat,dlon,a,c){
  r = 3440.065
  rlat1=radians(lat1); rlat2=radians(lat2)
  dlat=radians(lat2-lat1); dlon=radians(lon2-lon1)
  a = sin(dlat/2)^2 + cos(rlat1)*cos(rlat2)*sin(dlon/2)^2
  c = 2*atan2(sqrt(a), sqrt(1-a))
  return r*c
}
function sat(v, full){ if (v<=0) return 0; return min(1.0, v/full) }
function epoch(ts,   s,y,mo,d,h,mi,se){
  # ts like 2026-06-01T07:10:00Z (Z optional)
  gsub(/Z$/, "", ts)
  gsub(/[T:-]/, " ", ts)
  split(ts, a, " ")
  return mktime(a[1]" "a[2]" "a[3]" "a[4]" "a[5]" "a[6])
}
function isint(s){ return s ~ /^-?[0-9]+$/ }
{
  line=$0
  sub(/\r$/, "", line)
  gsub(/^[ \t]+|[ \t]+$/, "", line)
  if (line=="" || line ~ /^#/) next
  n=split(line, f, ",")
  for (i=1;i<=n;i++){ gsub(/^[ \t]+|[ \t]+$/, "", f[i]) }
  if (!seen && n>=2 && !isint(f[2])) next   # header
  if (n<4) next
  ts=epoch(f[1]); mmsi=f[2]+0; lat=f[3]+0; lon=f[4]+0
  if (lat< -90 || lat>90 || lon< -180 || lon>180 || mmsi<=0) next
  seen=1
  cnt[mmsi]++
  idx=cnt[mmsi]
  T[mmsi,idx]=ts; LA[mmsi,idx]=lat; LO[mmsi,idx]=lon
}
END {
  ng=0
  for (m in cnt){
    c=cnt[m]
    # insertion sort each track by timestamp
    for (i=2;i<=c;i++){
      kt=T[m,i]; ka=LA[m,i]; ko=LO[m,i]; j=i-1
      while (j>=1 && T[m,j]>kt){
        T[m,j+1]=T[m,j]; LA[m,j+1]=LA[m,j]; LO[m,j+1]=LO[m,j]; j--
      }
      T[m,j+1]=kt; LA[m,j+1]=ka; LO[m,j+1]=ko
    }
    for (i=2;i<=c;i++){
      dur = T[m,i] - T[m,i-1]
      if (dur <= min_gap) continue
      dist = haversine(LA[m,i-1], LO[m,i-1], LA[m,i], LO[m,i])
      hours = dur/3600.0
      speed = (hours>0)? dist/hours : 0
      d=sat(hours,6.0); s=sat(speed,40.0); x=sat(dist,50.0)
      score = 0.4*d + 0.4*s + 0.2*x
      if (score>1) score=1
      if (score < min_score) continue
      reasons=""
      if (d>=0.5) reasons=reasons sprintf("long-duration-%.1fh,", hours)
      if (s>=0.5) reasons=reasons sprintf("implied-speed-%.0fkn,", speed)
      if (x>=0.5) reasons=reasons sprintf("position-jump-%.0fnm,", dist)
      sub(/,$/, "", reasons)
      if (reasons=="") reasons="-"
      ng++
      GS[ng]=score; GM[ng]=m; GH[ng]=hours; GD[ng]=dist; GP[ng]=speed; GR[ng]=reasons
    }
  }
  # sort gaps by descending score (insertion sort on parallel arrays)
  for (i=2;i<=ng;i++){
    ks=GS[i]; km=GM[i]; kh=GH[i]; kd=GD[i]; kp=GP[i]; kr=GR[i]; j=i-1
    while (j>=1 && GS[j]<ks){
      GS[j+1]=GS[j];GM[j+1]=GM[j];GH[j+1]=GH[j];GD[j+1]=GD[j];GP[j+1]=GP[j];GR[j+1]=GR[j];j--
    }
    GS[j+1]=ks;GM[j+1]=km;GH[j+1]=kh;GD[j+1]=kd;GP[j+1]=kp;GR[j+1]=kr
  }
  limit = ng
  if (top>0 && top<ng) limit=top
  printf "GAPS %d\n", limit
  if (limit==0){ print "no gaps found"; exit }
  print "score  mmsi       duration  dist_nm  impl_kn  reasons"
  print "-----  ---------  --------  -------  -------  -------"
  for (i=1;i<=limit;i++){
    printf "%-5.2f  %-9d  %6.2fh  %7.1f  %7.1f  %s\n", GS[i], GM[i], GH[i], GD[i], GP[i], GR[i]
  }
}
')

NGAPS=$(printf '%s\n' "$OUTPUT" | awk 'NR==1{print $2}')
printf '%s\n' "$OUTPUT" | sed '1d'

if [ "${NGAPS:-0}" -gt 0 ]; then exit 2; else exit 0; fi
