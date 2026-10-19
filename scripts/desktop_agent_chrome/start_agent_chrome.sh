#!/bin/bash
# Start the "Agent Chrome" on this Mac and expose it to the NAS only.
#
#   NAS_IP=192.168.1.10 ./start_agent_chrome.sh
#
# Agent Chrome is a separate Chrome profile (its own window, cookies and
# logins), not your everyday one. Chrome 136+ refuses remote debugging on the
# default profile anyway; keeping the agent's logins separate is the point.
# Log in to the booking sites (Resy, OpenTable, Tock, Ticketmaster...) in this
# window once; the agent reuses those sessions.
#
# Runs in the foreground (the forwarder); launchd keeps it alive, see
# com.aoc.agent-chrome.plist.
set -euo pipefail

: "${NAS_IP:?Set NAS_IP to the LAN address of the NAS, e.g. NAS_IP=192.168.1.10}"
CHROME_APP="${CHROME_APP:-/Applications/Google Chrome.app}"
PROFILE_DIR="${AGENT_CHROME_PROFILE:-$HOME/Library/Application Support/AgentChrome}"
CHROME_PORT="${CHROME_PORT:-9222}"
LISTEN_PORT="${LISTEN_PORT:-9223}"
HERE="$(cd "$(dirname "$0")" && pwd)"

chrome_up() {
    curl -fs --max-time 2 "http://127.0.0.1:${CHROME_PORT}/json/version" >/dev/null 2>&1
}

if ! chrome_up; then
    echo "Starting Agent Chrome (profile: $PROFILE_DIR)..."
    mkdir -p "$PROFILE_DIR"
    # -n: a separate Chrome instance next to your everyday one.
    open -na "$CHROME_APP" --args \
        --user-data-dir="$PROFILE_DIR" \
        --remote-debugging-port="$CHROME_PORT" \
        --no-first-run \
        --no-default-browser-check
    for _ in $(seq 1 30); do
        chrome_up && break
        sleep 1
    done
    chrome_up || { echo "Agent Chrome did not open its debugging port on ${CHROME_PORT}." >&2; exit 1; }
fi

echo "Agent Chrome is up. Forwarding :${LISTEN_PORT} -> 127.0.0.1:${CHROME_PORT} for ${NAS_IP} only."
exec /usr/bin/python3 "$HERE/cdp_forwarder.py" \
    --allow "$NAS_IP" \
    --listen-port "$LISTEN_PORT" \
    --chrome-port "$CHROME_PORT"
