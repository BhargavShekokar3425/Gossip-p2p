# Gossip-Based Peer-to-Peer Network with Consensus-Driven Membership

A secure, scalable, and fault-tolerant P2P network using gossip-based dissemination,
power-law overlay topology, and two-level consensus for membership management.

## Architecture

```
                   ┌──────────────────────────┐
                   │        Seed Nodes        │
                   │  (Membership Authority)  │
                   │   S1   S2   S3 ... Sn    │
                   └──────────┬───────────────┘
                              │
                Registration &│Dead Reports
                              ▼
       ┌─────────────────────────────────────────┐
       │              Peer Overlay               │
       │  P1──P2──P3──P4──P5                     │
       │   │   │    │    │                       │
       │   └───┼────┘                            │
       │ Gossip + Liveness Detection             │
       └─────────────────────────────────────────┘
```

- **Control Plane** → Seed Nodes (membership authority, consensus-driven)
- **Data Plane** → Peer Nodes (gossip, overlay, liveness)

## Project Structure

```
Gossip Network/
├── config.txt            # Seed node addresses (IP:Port per line)
├── protocol.py           # Shared message protocol & utilities
├── logger.py             # Colored console + outputfile.txt logging
├── seed.py               # Seed node implementation
├── peer.py               # Peer node implementation
├── gui.py                # Live network dashboard (tkinter)
├── visualizer.py         # Step-by-step algorithm demo (tkinter)
├── launch_seeds.sh       # Start all seed nodes
├── launch_peers.sh       # Start N peer nodes
├── launch_network.sh     # Start entire network
├── test_network.py       # Automated test suite (6 stages)
├── Test_Cases.md         # Detailed test case documentation
├── outputfile.txt        # Shared log file (auto-created at runtime)
└── README.md
```

## Quick Start

### 1. Start the Full Network

```bash
# Start 3 seeds + 5 peers (default)
./launch_network.sh

# Start 5 seeds + 10 peers
./launch_network.sh 5 10

# Start 4 seeds + 8 peers
./launch_network.sh 4 8

# Start with live GUI dashboard
./launch_network.sh gui

# Start with GUI + custom seed/peer count
./launch_network.sh gui 5 10
```

First argument = number of seeds, second = number of peers. The script auto-generates `config.txt` with that many seeds (ports starting at 6000). Peers start at port 7000. Pass `gui` as the first argument to launch the live tkinter dashboard alongside the network.

### 2. Start Components Separately

```bash
# Terminal 1: Start seeds
./launch_seeds.sh

# Terminal 2: Start peers (after seeds are running)
./launch_peers.sh 5         # 5 peers starting at port 7000
./launch_peers.sh 3 8000    # 3 peers starting at port 8000
```

### 3. Start Individual Nodes

```bash
# Single seed
python3 seed.py --host 127.0.0.1 --port 6000 --config config.txt

# Single peer
python3 peer.py --host 127.0.0.1 --port 7000 --config config.txt
```

### 4. Algorithm Visualizer (Step-by-Step Demo)

```bash
python3 visualizer.py
```

A standalone educational GUI that walks through every phase of the gossip protocol
step by step — no real network required. Enter the number of seeds and peers directly
in the GUI, then watch each phase animate slowly:

| Phase | What it shows |
|-------|---------------|
| ① Seed Initialization | Seed nodes appear with their quorum size |
| ② Peer Registration | Each peer registers; seeds vote via consensus |
| ③ Overlay Construction | Power-law (Zipf) neighbor selection, edges form |
| ④ Gossip Dissemination | Messages propagate along edges; duplicates dropped |
| ⑤ Failure Detection | A peer crashes → PING timeout → peer-level consensus |
| ⑥ Dead-Node Removal | DEAD_NODE_REPORT → seed-level quorum → removal |

Use the **Speed slider** to speed up or slow down, and **Next Phase** to advance.

### 4. Run Tests

```bash
python3 test_network.py
```

Test output shows PASS/FAIL for each check. See `Test_Cases.md` for full details.

---

## Configuration

Edit `config.txt` — one seed per line in `IP:Port` format:

```
127.0.0.1:6000
127.0.0.1:6001
127.0.0.1:6002
```

Both `:` and `,` separators are supported. Lines starting with `#` are comments.

---

## Code Walkthrough

### `config.txt`

Plain-text list of seed node addresses. Every seed and peer reads this file at
startup to discover the seed cluster.

### `protocol.py`

Shared definitions used by both seeds and peers:

| Symbol | Purpose |
|--------|---------|
| `MSG_*` constants | All message type identifiers for every interaction |
| `make_message()` | Encode a typed JSON message terminated by `\n` |
| `parse_messages()` | Decode one or more messages from a TCP byte buffer |
| `gossip_message_id()` | Build `<timestamp>:<IP>:<Port>:<Msg#>` string |
| `hash_message()` | SHA-256 hash for gossip deduplication |
| `dead_node_message()` | Build `Dead Node:<IP>:<Port>:<ts>:<self.IP>` |
| `load_config()` | Parse `config.txt` into `[{host, port}, ...]` |
| `MAX_GOSSIP_MESSAGES` | Cap = 10 messages per peer |
| `GOSSIP_INTERVAL` | 5 seconds between gossip bursts |

All TCP communication uses **JSON-encoded messages delimited by `\n`**.

### `logger.py`

Creates a Python `logging.Logger` that writes to:

1. **Console** — colour-coded by level (green=INFO, yellow=WARNING, red=ERROR).
2. **`outputfile.txt`** — the shared log file required by the spec. All seed and
   peer processes append to this single file using a thread-safe lock.

Format: `[timestamp] [SEED:port] LEVEL - message` or `[timestamp] [PEER:port] LEVEL - message`.

### `seed.py` — Seed Node

The seed is the **membership authority**. It never participates in gossip.

**Startup:**
1. Reads `config.txt` to learn its peer seeds.
2. Opens two TCP listeners: one for seeds, one for peers.
3. Starts a sync loop that periodically merges Peer Lists with other seeds.

**Registration flow (consensus):**
1. A peer sends `REGISTER_REQUEST`.
2. The receiving seed broadcasts `PROPOSE_REGISTER` to all other seeds.
3. Each seed votes `YES` (if the peer isn't already known) or `NO`.
4. If ⌊n/2⌋+1 votes are `YES` → `REGISTER_ACK` + peer added to PL. Otherwise → `REGISTER_NACK`.

**Dead-node removal (consensus):**
1. A peer sends `DEAD_NODE_REPORT` with format `Dead Node:<IP>:<Port>:<ts>:<reporter.IP>`.
2. The seed proposes removal to other seeds (`PROPOSE_REMOVE`) and collects votes.
3. If quorum → peer removed from PL, `REMOVAL_NOTIFY` broadcast.

**What gets logged:**
- `PROPOSAL:` when a new registration/removal is proposed.
- `CONSENSUS OUTCOME — APPROVED/REJECTED:` after voting completes.
- `CONFIRMED REMOVAL:` when a dead node is finally removed.

### `peer.py` — Peer Node

The peer is the **data-plane participant**: gossip, liveness, topology.

**Startup:**
1. Reads `config.txt` to find seeds.
2. Sends `REGISTER_REQUEST` to all reachable seeds; waits for `REGISTER_ACK`.
3. Fetches Peer List from seeds and builds the **power-law neighbor set**.
4. Launches three parallel threads:
   - **Gossip generator** — every 5 s, creates a gossip message (`<timestamp>:<IP>:<Port>:<Msg#>`) and forwards it to all neighbors. Stops after 10 messages.
   - **Listener** — accepts incoming gossip & liveness messages.
   - **Liveness detector** — periodically pings each neighbor via TCP. Uses peer-level consensus to confirm suspicions before reporting.

**Topology (Zipf/power-law):**
```
weight(k) = 1 / k^α      (α = 1.0)
```
Peers are sorted and weighted by rank. The node samples ⌊n/2⌋+1 neighbors
without replacement using the Zipf probabilities.

**Gossip dissemination:**
- Message format: `<timestamp>:<IP>:<Port>:<Msg#>`
- SHA-256 hash stored in `seen_hashes` for deduplication.
- On first receipt: log, store hash, forward to all neighbors.
- On duplicate: drop silently.
- Each peer generates **at most 10** gossip messages (spec requirement).

**Failure detection (two-level consensus):**

| Level | Who | How |
|-------|-----|-----|
| Peer-level | Neighbors | Ping timeout → suspect → query neighbors → majority confirm |
| Seed-level | Seeds | `DEAD_NODE_REPORT` → propose removal → seed quorum votes |

**What gets logged:**
- `Received Peer List from seed ...` when PL is received.
- `Gossip received [from=..., msg=..., time=...]` for first-time gossip.
- `DEAD NODE REPORT: Dead Node:<IP>:<Port>:<ts>:<self.IP>` for confirmed dead reports.
- Ping messages are **not** logged (per spec).

---

## Test Suite

The automated test suite (`test_network.py`) covers 6 stages:

| Stage | Title | What It Tests |
|-------|-------|---------------|
| 1 | Socket Testing | TCP binding, connection, JSON framing |
| 2 | Seed Registration | Consensus-based registration, PL update, multi-seed |
| 3 | Overlay Formation | Power-law topology, peer reachability |
| 4 | Gossip Dissemination | 10-msg cap, hash dedup, forwarding |
| 5 | Failure Detection | Kill peer → liveness timeout → PL removal |
| 6 | Security | False dead reports, idempotent re-registration |

See `Test_Cases.md` for a detailed per-test breakdown with expected results.

---

## Security Guarantees

| Attack | Mitigation |
|--------|-----------|
| False dead-node accusation | Peer majority + seed quorum required |
| Sybil (fake nodes) | Registration needs ⌊n/2⌋+1 seed consensus |
| Seed compromise | No single seed can alter membership |
| Colluding peers | Must control >50% of neighbors AND >50% of seeds |
| Network partition | No minority partition can modify membership |

---

## How to Read the Logs

### Log Locations

| File | Purpose |
|------|---------|
| `outputfile.txt` | Combined log for the **latest** experiment (reset each launch). Use this for quick checks. |
| `logs/output_YYYYMMDD_HHMMSS.log` | **Per-process** log (one per seed/peer). Each experiment creates several of these. They are never overwritten, so you can compare across experiments. |

### Log Line Format

```
[timestamp] [NODE_TYPE:PORT] LEVEL - message
```

Examples:
```
[2026-02-24 20:32:48] [SEED:6000] INFO - Seed node initialized at 127.0.0.1:6000
[2026-02-24 20:32:52] [PEER:7000] INFO - Registering with 2/3 seeds (quorum = 2)
```

- **`SEED:port`** = seed node log entry
- **`PEER:port`** = peer node log entry
- **`INFO`** = normal events, **`WARNING`** = suspicious / retry, **`ERROR`** = failures

### Key Log Keywords (what to grep for)

| Keyword / Pattern | Meaning | Where |
|-------------------|---------|-------|
| `PROPOSAL:` | A seed is proposing a membership change to other seeds | Seed |
| `CONSENSUS OUTCOME — APPROVED` | ⌊n/2⌋+1 seeds voted YES | Seed |
| `CONSENSUS OUTCOME — REJECTED` | Quorum not reached — change denied | Seed |
| `CONFIRMED REMOVAL:` | Dead peer officially removed from PL | Seed |
| `Voting YES` / `Voting NO` | A seed's vote on a proposal | Seed |
| `Vote from` | Incoming vote tally during consensus | Seed |
| `Current Peer List:` | Updated PL after a change | Seed |
| `Received Peer List from seed` | Peer fetched PL from a seed | Peer |
| `Gossip received [from=..., msg=..., time=...]` | First-time gossip arrival | Peer |
| `Generated gossip` | Peer created a new gossip message | Peer |
| `DEAD NODE REPORT:` | Peer confirmed a neighbor is dead and reported it | Peer |
| `Suspect` / `suspected` | Liveness check started for a neighbor | Peer |

### Quick Grep Commands

```bash
# See only consensus decisions
grep "CONSENSUS OUTCOME" outputfile.txt

# See all gossip activity
grep "Gossip\|gossip" outputfile.txt

# Trace a specific peer (e.g. port 7002)
grep "7002" outputfile.txt

# See dead-node detection flow
grep -i "dead\|suspect\|removal\|CONFIRMED" outputfile.txt

# See registration flow only
grep "PROPOSAL\|Vote\|ACK\|APPROVED\|REJECTED" outputfile.txt

# Isolate one process's log (e.g. seed on port 6000)
grep "\[SEED:6000\]" outputfile.txt
```

### Step-by-Step: Reading a Typical Experiment

Open `outputfile.txt` (or any file in `logs/`) and follow this timeline:

**1. Seed Initialization (first few lines)**
```
[...] [SEED:6000] INFO - Seed node initialized at 127.0.0.1:6000
[...] [SEED:6000] INFO - Total seeds: 3, Quorum: 2
[...] [SEED:6000] INFO - Seed node listening on 127.0.0.1:6000
```
Each seed reports its quorum threshold (⌊n/2⌋+1). Confirm all 3 seeds start.

**2. Peer Registration (consensus in action)**
```
[...] [SEED:6000] INFO - PROPOSAL: Register peer 127.0.0.1:7000  [id=abc123, self-vote=YES, votes=1/2 needed]
[...] [SEED:6000] INFO - Vote from 127.0.0.1:6001: YES  (total 2/2)
[...] [SEED:6000] INFO - CONSENSUS OUTCOME — APPROVED: Peer 127.0.0.1:7000  [votes=3/3, quorum=2]
```
You should see PROPOSAL → Votes → OUTCOME for each peer. Verify `votes ≥ quorum`.

**3. Peer List & Overlay**
```
[...] [PEER:7000] INFO - Received Peer List from seed 127.0.0.1:6000
[...] [PEER:7000] INFO - Selected 3 neighbors (power-law): [...]
```
Peers fetch PL from seeds, then select neighbors using Zipf weights.

**4. Gossip Propagation**
```
[...] [PEER:7000] INFO - Generated gossip #1: 1708776008.5:127.0.0.1:7000:1
[...] [PEER:7001] INFO - Gossip received [from=127.0.0.1:7000, msg=..., time=...]
```
Each peer generates up to 10 messages. Check that gossip spreads to other peers.

**5. Failure Detection & Removal (if a peer crashes)**
```
[...] [PEER:7001] INFO - DEAD NODE REPORT: Dead Node:127.0.0.1:7004:1708776030:127.0.0.1
[...] [SEED:6000] INFO - PROPOSAL: Remove peer 127.0.0.1:7004 ...
[...] [SEED:6000] INFO - CONSENSUS OUTCOME — APPROVED: Remove 127.0.0.1:7004 ...
[...] [SEED:6000] INFO - CONFIRMED REMOVAL: 127.0.0.1:7004
```
Follow: DEAD NODE REPORT → PROPOSAL remove → Votes → CONFIRMED REMOVAL.

### Comparing Experiments

Each run produces separate files in `logs/`:
```
logs/
├── output_20260224_203248.log   ← Seed:6000 from run 1
├── output_20260224_203249.log   ← Seed:6001 from run 1
├── output_20260224_203252.log   ← Peer:7000 from run 1
├── output_20260224_204500.log   ← Seed:6000 from run 2
└── ...
```

The timestamp in the filename is the process start time, so files from the same experiment share similar timestamps. You can compare behavior across runs by sorting by date.

---

## Thread Model

```
Seed Node                     Peer Node
├── Main Thread               ├── Main Thread (registration + PL fetch)
├── Peer Request Listener     ├── Listener Thread  (gossip + pings)
├── Seed-to-Seed Listener     ├── Gossip Generator (10 msgs, 5 s interval)
└── Sync Loop                 └── Liveness Detector (ping + consensus)
```
