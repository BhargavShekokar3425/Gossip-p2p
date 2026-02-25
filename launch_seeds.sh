#!/usr/bin/env bash
# launch_seeds.sh — Start all seed nodes defined in config.txt
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:-$DIR/config.txt}"
echo "=== Starting Seed Nodes ==="
PIDS=()
while IFS=: read -r host port; do
    [[ -z "$host" || "$host" == \#* ]] && continue
    echo "  Seed $host:$port"
    python3 "$DIR/seed.py" --host "$host" --port "$port" --config "$CONFIG" &
    PIDS+=($!)
    sleep 0.3
done < "$CONFIG"
echo "PIDs: ${PIDS[*]}"
echo "Press Ctrl+C to stop."
trap 'kill "${PIDS[@]}" 2>/dev/null; wait; echo Done.' INT TERM
wait
