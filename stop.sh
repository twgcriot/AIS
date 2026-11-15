#!/usr/bin/env bash
# Stop the Entra page and the Python agent.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
cd "$root"

stop_pid() {
  local pid="$1"
  local cmd
  [[ -n "$pid" ]] || return 0
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  cmd="$(ps -p "$pid" -o command=)"
  if [[ "$cmd" != *test_ais_tinytot.py* && "$cmd" != *frontend.py* ]]; then
    echo "Leaving pid $pid alone; it is not this demo."
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 25); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  kill -9 "$pid" 2>/dev/null || true
}

for pidfile in .run/agent.pid .run/frontend.pid .run/console.pid; do
  if [[ -f "$pidfile" ]]; then
    stop_pid "$(cat "$pidfile")"
    rm -f "$pidfile"
  fi
done

if command -v lsof >/dev/null; then
  for port in 7777 9999; do
    while read -r pid; do
      stop_pid "$pid"
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u)
  done
fi

echo "Stopped."
