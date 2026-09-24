#!/usr/bin/env bash
# Times a status line render: ./tools/bench.sh [runs] [command ...]
#
# Feeds tools/sample-payload.json, pointed at this checkout so that git has a
# real work tree to read, to the command `runs` times (50 by default) and
# prints the mean wall-clock time per render. With no command it measures
# ./statusline.sh; pass another one to compare, e.g.
#
#   ./tools/bench.sh 50 npx -y ccstatusline
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
runs=50
if [ $# -gt 0 ]; then runs=$1; shift; fi
case "$runs" in ""|*[!0-9]*) echo "runs must be a number" >&2; exit 1 ;; esac
if [ $# -eq 0 ]; then set -- "$here/statusline.sh"; fi

payload=$(jq -c --arg dir "$here" '.workspace.current_dir = $dir' "$here/tools/sample-payload.json")
"$@" <<<"$payload" >/dev/null 2>&1          # warm the file cache first

TIMEFORMAT=%R
elapsed=$( { time for (( i = 0; i < runs; i++ )); do "$@" <<<"$payload" >/dev/null 2>&1; done; } 2>&1 )
awk -v t="$elapsed" -v n="$runs" 'BEGIN { printf "%.1f ms per render over %d runs\n", t * 1000 / n, n }'
