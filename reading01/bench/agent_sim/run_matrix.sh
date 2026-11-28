#!/usr/bin/env bash
# Base agent vs Skill agent on the mock cases, N repetitions each, P in parallel.
# usage: DEEPSEEK_API_KEY=... ./run_matrix.sh MODEL REPS PARALLEL [skill base]
set -u
MODEL=${1:-deepseek-chat}; REPS=${2:-3}; PAR=${3:-6}; shift 3 || true
CONDS=${*:-"skill base"}
cd "$(dirname "$0")"
: "${DEEPSEEK_API_KEY:?set DEEPSEEK_API_KEY}"
export DEEPSEEK_MODEL=$MODEL
TAG=$(date +%m%d-%H%M)-$MODEL
mkdir -p runs/$TAG
for c in cases/${CASE_GLOB:-*}/; do c=$(basename $c); for cond in $CONDS; do for r in $(seq 1 $REPS); do
  echo "$c $cond $r"; done; done; done |
xargs -P "$PAR" -L 1 bash -c 'python3 harness.py --case cases/$0 --condition $1 --model '"$MODEL"' --skill-dir ../.. --out runs/'"$TAG"'/$0/$1-r$2 > /dev/null 2> runs/'"$TAG"'/$0-$1-r$2.err; echo "done $0 $1 r$2 rc=$?"'
python3 check.py runs/$TAG | tee runs/$TAG/report.txt
