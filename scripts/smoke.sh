#!/usr/bin/env bash
# Launch `python -m muse` on a fresh data dir, hit /api/health with and without
# the token (header and ?t=), and check the token never reaches the log.
# `bash scripts/smoke.sh <binary>` smoke-tests a frozen sidecar instead.
set -u
D=$(mktemp -d)
MUSE_DATA_DIR="$D/data" MUSE_TOKEN=smoke-token-123 ${1:-uv run python -m muse} >"$D/log" 2>&1 &
PID=$!
for _ in $(seq 100); do grep -q MUSE_LISTENING "$D/log" && break; sleep 0.2; done
PORT=$(grep -o 'MUSE_LISTENING [0-9]*' "$D/log" | cut -d' ' -f2)
NO=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health")
YES=$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Muse-Token: smoke-token-123' "http://127.0.0.1:$PORT/api/health")
QS=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/health?t=smoke-token-123")
kill $PID; wait $PID 2>/dev/null
LEAKS=$(grep -c smoke-token-123 "$D/log")
echo "port=$PORT no_token=$NO with_token=$YES query_token=$QS token_lines=$LEAKS"
[ "$NO" = 401 ] && [ "$YES" = 200 ] && [ "$QS" = 200 ] && [ "$LEAKS" = 0 ] && echo SMOKE-OK || { cat "$D/log"; echo SMOKE-FAIL; exit 1; }
