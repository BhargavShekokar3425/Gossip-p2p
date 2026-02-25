#!/usr/bin/env bash
# launch_network.sh — Start seeds + peers in one go, optionally with GUI
# Usage: ./launch_network.sh [num_seeds] [num_peers]
#        ./launch_network.sh gui [num_seeds] [num_peers]
# Examples:
#   ./launch_network.sh              # 3 seeds, 5 peers  (defaults)
#   ./launch_network.sh 5 10         # 5 seeds, 10 peers
#   ./launch_network.sh gui          # 3 seeds, 5 peers + live GUI
#   ./launch_network.sh gui 4 8      # 4 seeds, 8 peers + live GUI
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Parse "gui" flag ──────────────────────────────────────────────────────
GUI=false
if [[ "$1" == "gui" ]]; then
    GUI=true; shift
fi

NUM_SEEDS="${1:-3}"
NUM_PEERS="${2:-5}"
SEED_BASE_PORT=6000
PEER_BASE_PORT=7000

# ── Auto-generate config.txt with the requested number of seeds ───────────
CONFIG="$DIR/config.txt"
> "$CONFIG"
for ((i=0;i<NUM_SEEDS;i++)); do
    echo "127.0.0.1:$((SEED_BASE_PORT+i))" >> "$CONFIG"
done

echo "╔══════════════════════════════════════════════╗"
echo "║   Gossip-Based P2P Network with Consensus   ║"
echo "╚══════════════════════════════════════════════╝"
# Clear root outputfile.txt so it reflects only this experiment
> "$DIR/outputfile.txt"
# Seeds
echo "▶ Starting $NUM_SEEDS seed nodes…"
SEED_PIDS=()
while IFS=: read -r host port; do
    [[ -z "$host" || "$host" == \#* ]] && continue
    echo "  Seed $host:$port"
    python3 "$DIR/seed.py" --host "$host" --port "$port" --config "$CONFIG" &
    SEED_PIDS+=($!)
    sleep 0.3
done < "$CONFIG"
sleep 2
# Peers
echo "▶ Starting $NUM_PEERS peer nodes…"
PEER_PIDS=()
for ((i=0;i<NUM_PEERS;i++)); do
    PORT=$((PEER_BASE_PORT+i))
    echo "  Peer 127.0.0.1:$PORT"
    python3 "$DIR/peer.py" --host 127.0.0.1 --port "$PORT" --config "$CONFIG" &
    PEER_PIDS+=($!)
    sleep 1
done
ALL=("${SEED_PIDS[@]}" "${PEER_PIDS[@]}")
echo "═══════════════════════════════════════════════"
echo "Network: ${#SEED_PIDS[@]} seeds + ${#PEER_PIDS[@]} peers"
echo "Output:  $DIR/outputfile.txt  (latest run)"
echo "Logs:    $DIR/logs/           (all experiments)"
echo "═══════════════════════════════════════════════"

# ── Launch GUI if requested ───────────────────────────────────────────────
if $GUI; then
    echo "▶ Launching live GUI visualizer…"
    python3 "$DIR/gui.py" --log "$DIR/outputfile.txt" &
    GUI_PID=$!
    ALL+=($GUI_PID)
fi

echo "Press Ctrl+C to stop."
trap 'kill "${ALL[@]}" 2>/dev/null; wait; echo "Network stopped."' INT TERM
wait
