#!/usr/bin/env bash
# Start the Entra page on http://localhost:7777/ and the Python agent on port 9999.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
cd "$root"
mkdir -p .run

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi
export AIS_AGENT_NAME="${AIS_AGENT_NAME:-PythonAgent}"
export AGENT_CLIENT_ID="${AGENT_CLIENT_ID:-3d31bc97-40fc-4554-9ee6-5f8029620628}"
export WEBAPP_ENTRA_TENANT="${WEBAPP_ENTRA_TENANT:-oogwaysanhotmail.onmicrosoft.com}"
export WEBAPP_ENTRA_CLIENT_ID="${WEBAPP_ENTRA_CLIENT_ID:-c254321d-fe98-48e7-b7bf-9f94832acada}"
export WEBAPP_REDIRECT_URI="${WEBAPP_REDIRECT_URI:-http://localhost:7777/callback}"
export AGENT_REDIRECT_URI="${AGENT_REDIRECT_URI:-http://localhost:9999/callback}"
export AIS_FRONTEND_URL="${AIS_FRONTEND_URL:-http://localhost:7777}"
export AIS_GATEWAY="${AIS_GATEWAY:-https://mcp-aigw.portkey.ai}"
export AIS_DEEPWIKI_SLUG="${AIS_DEEPWIKI_SLUG:-deepwikimcp}"
export PYTHONUNBUFFERED=1

start_proc() {
  local name="$1" script="$2" pidfile="$3" ready="$4" logfile="$5" port="$6"
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "$name is already running (pid $(cat "$pidfile"))."
    return 0
  fi
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use. Run ./stop.sh first."
    exit 1
  fi
  : > "$logfile"
  # Start a new session so this process stays up after the launching shell exits.
  nohup python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' python3 "$root/$script" >> "$logfile" 2>&1 &
  echo $! > "$pidfile"
  disown "$!" 2>/dev/null || true
  for _ in $(seq 1 25); do
    if grep -q "$ready" "$logfile"; then
      return 0
    fi
    if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      echo "$name exited. See $logfile"
      exit 1
    fi
    sleep 0.2
  done
  echo "$name started (pid $(cat "$pidfile")), but it has not reported ready yet. See $logfile"
}

start_proc "Agent" test_ais_tinytot.py .run/agent.pid "Agent http://localhost:9999/" .run/agent.log 9999
start_proc "Front-end" frontend.py .run/frontend.pid "Open http://localhost:7777/" .run/frontend.log 7777
echo "Open http://localhost:7777/"
