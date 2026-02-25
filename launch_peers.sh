#!/usr/bin/env bash
# launch_peers.sh — Start N peer nodes
# Usage: ./launch_peers.sh [num_peers] [start_port] [config]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NUM="${1:-3}"; START="${2:-7000}"; CONFIG="${3:-$DIR/config.txt}"
echo "=== Starting $NUM Peer Nodes (ports $START–$((START+NUM-1))) ==="
PIDS=()
for ((i=0;i<NUM;i++)); do
    PORT=$((START+i))
    echo "  Peer 127.0.0.1:$PORT"
    python3 "$DIR/peer.py" --host 127.0.0.1 --port "$PORT" --config "$CONFIG" &
    PIDS+=($!)
    sleep 1
done
echo "PIDs: ${PIDS[*]}"
echo "Press Ctrl+C to stop."
trap 'kill "${PIDS[@]}" 2>/dev/null; wait; echo Done.' INT TERM
wait
