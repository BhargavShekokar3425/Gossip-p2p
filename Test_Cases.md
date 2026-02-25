# Test Cases — Gossip P2P Network

This document describes each test stage in the automated test suite (`test_network.py`).
The stages mirror the assignment requirements.

---

## Stage 1: Basic Socket Testing

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 1 | TCP connection to seed | Opens a TCP socket to `127.0.0.1:6000` and checks that the seed node accepts it. | Connection succeeds within 5 s. |
| 2 | Seed responds to messages | Sends a `GET_PEER_LIST` request to the seed and verifies a response arrives. | At least one JSON-framed message is returned. |
| 3 | Response type correct | Inspects the response type field. | Type == `PEER_LIST`. |

**Why it matters:** Validates that the seed node can bind a TCP port, accept connections, and respond with correctly framed JSON messages — the foundation for everything else.

---

## Stage 2: Seed Registration with Consensus

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 4 | First peer registration | Sends `REGISTER_REQUEST` for `127.0.0.1:7000` to seed 6000. Seed 6000 proposes it to seeds 6001 and 6002 and collects votes. | `REGISTER_ACK` returned, meaning ⌊3/2⌋+1 = 2 seeds approved. |
| 5 | Peer appears in Peer List | After registration, queries the seed for its peer list and checks the new peer is present. | `127.0.0.1:7000` found in the PL. |
| 6 | Registration via different seed | Sends `REGISTER_REQUEST` for `127.0.0.1:7001` to seed 6001 (not the same seed as test 4). | `REGISTER_ACK` returned — consensus works from any seed. |

**Why it matters:** Verifies the core two-level consensus: no single seed can admit a peer unilaterally; a quorum (⌊n/2⌋+1) of seeds must agree.

---

## Stage 3: Overlay Formation & Power-Law Topology

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 7 | Sufficient peers registered | Starts 5 peer processes (ports 7000–7004), waits, then queries a seed for the Peer List. | ≥ 3 peers appear in the seed's PL. |
| 8 | Peer reachability | Tries TCP-connecting to several peer ports. | At least one peer accepts connections. |

**Why it matters:** After registering, each peer fetches the Peer List and builds its neighbor set using a Zipf-weighted random selection (α = 1.0), producing a power-law degree distribution. A well-connected overlay is essential for efficient gossip propagation.

**How the topology is built (inside `peer.py`):**
1. Peer receives the full PL from seeds.
2. Assigns weight 1/k^α to the k-th peer in the list.
3. Normalizes weights into probabilities.
4. Samples ⌊n/2⌋+1 neighbors without replacement (or fewer if the overlay is small).

---

## Stage 4: Gossip Dissemination

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 9 | Gossip message accepted | Crafts a `GOSSIP` message with a valid `msg_id` (`<timestamp>:<IP>:<Port>:<Msg#>`) and SHA-256 hash, sends it to peer 7000. | No connection error — message is accepted. |
| 10 | Duplicate gossip handling | Re-sends the exact same gossip (same hash). | Peer silently ignores the duplicate (hash already seen). |

**Why it matters:**
- Each peer generates **at most 10** gossip messages (spec requirement). The 10-message cap is enforced inside `peer.py`'s gossip loop.
- When a peer receives a gossip it hasn't seen before, it logs the message, records the SHA-256 hash, and forwards it to all neighbors.
- When it receives a duplicate (hash already in `seen_hashes`), it simply drops the message — no re-forwarding, no error.

---

## Stage 5: Failure Detection & Dead-Node Removal

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 11 | Peer confirmed dead | Terminates the process for peer 7004, then checks it is unreachable. | TCP connection to port 7004 fails. |
| 12 | Peer removed from seed PL | Waits ~20 s for the liveness cycle, then queries the seed for the Peer List. | `127.0.0.1:7004` no longer in the PL (or detection still in progress — both are acceptable within the wait window). |

**How failure detection works (two-level consensus):**

1. **Peer-level consensus:**
   - Each peer periodically pings its neighbors (`PING`/`PONG` over TCP).
   - If a neighbor does not respond within the timeout, the peer marks it *suspected*.
   - It queries its other neighbors with `SUSPECT_QUERY` messages.
   - If a majority of queried neighbors also consider the node dead, the suspicion is **confirmed**.
   
2. **Seed-level consensus:**
   - The detecting peer sends a `DEAD_NODE_REPORT` to all seeds.
   - The report string follows the required format: `Dead Node:<IP>:<Port>:<timestamp>:<reporter.IP>`
   - The receiving seed proposes removal to other seeds (`PROPOSE_REMOVE`).
   - If ⌊n/2⌋+1 seeds vote `YES`, the peer is removed from PL and a `REMOVAL_NOTIFY` is broadcast.

---

## Stage 6: Security Testing

| # | Test | What It Does | Expected Result |
|---|------|-------------|-----------------|
| 13 | False dead-node report | Sends a `DEAD_NODE_REPORT` claiming `127.0.0.1:9999` is dead — a peer that was never registered. | Seed handles it gracefully (ignores or rejects). No crash. |
| 14 | Idempotent re-registration | Sends a `REGISTER_REQUEST` for a peer that is already in the PL (port 7000). | Returns `REGISTER_ACK` without duplicating the entry. |

**Why it matters:** Malicious or buggy nodes could submit false reports or duplicate registrations. The system must:
- Not remove peers based on a single unverified report.
- Not crash or corrupt state when receiving bogus data.
- Handle duplicate registrations gracefully (idempotent).

---

## Extra: `outputfile.txt` Check

After all stages, the test verifies that `outputfile.txt` exists and contains logged events. This file is the shared log produced by all seed and peer processes (append mode), as required by the assignment specification.

---

## Running the Tests

```bash
python3 test_network.py
```

The test suite starts all seed and peer processes automatically, runs through the six stages, prints per-test PASS/FAIL results, and cleans up all processes on exit.

---

## How to Verify Results in the Logs

After a test run (or a manual `./launch_network.sh` experiment), open the logs to trace what happened.

### Where to Look

| File | What it Contains |
|------|---------|
| `outputfile.txt` | Combined chronological log of **all** seeds + peers for the latest run. Best for tracing the full flow. |
| `logs/output_YYYYMMDD_HHMMSS.log` | One file per process. Useful for isolating a single seed or peer's perspective. |

### Log Line Format

```
[timestamp] [NODE_TYPE:PORT] LEVEL - message
```

`SEED:6000` = seed on port 6000, `PEER:7002` = peer on port 7002.

### What to Look For — Per Stage

#### Stage 1 (Socket)
Grep: `grep "listening\|Sending Peer List" outputfile.txt`

You should see seeds announcing their listener and responding to the test's `GET_PEER_LIST` query.

#### Stage 2 (Registration Consensus)
Grep: `grep "PROPOSAL\|Vote\|CONSENSUS OUTCOME\|APPROVED\|REJECTED" outputfile.txt`

For each peer registration you should see this sequence:
```
PROPOSAL: Register peer 127.0.0.1:7000  [id=..., self-vote=YES, votes=1/2 needed]
Vote from 127.0.0.1:6001: YES  (total 2/2)
CONSENSUS OUTCOME — APPROVED: Peer 127.0.0.1:7000  [votes=3/3, quorum=2]
Current Peer List: ['127.0.0.1:7000']
```
If you see `REJECTED`, it means quorum was not reached (possible if a seed was unreachable).

#### Stage 3 (Overlay)
Grep: `grep "Received Peer List\|neighbors" outputfile.txt`

Each peer logs the PL it received and the neighbors it selected via power-law weighting.

#### Stage 4 (Gossip)
Grep: `grep "Generated gossip\|Gossip received" outputfile.txt`

You should see:
- `Generated gossip #N` from each peer (up to #10)
- `Gossip received [from=..., msg=..., time=...]` on neighboring peers

If a message appears only once per peer (no duplicates logged), deduplication is working.

#### Stage 5 (Failure Detection)
Grep: `grep -i "dead\|suspect\|removal\|CONFIRMED" outputfile.txt`

After killing peer 7004, you should eventually see:
```
DEAD NODE REPORT: Dead Node:127.0.0.1:7004:...:127.0.0.1
PROPOSAL: Remove peer 127.0.0.1:7004 ...
CONSENSUS OUTCOME — APPROVED: Remove 127.0.0.1:7004 ...
CONFIRMED REMOVAL: 127.0.0.1:7004
```
(Timing depends on ping intervals — may need 15–30 s after the kill.)

#### Stage 6 (Security)
Grep: `grep -i "unknown\|already registered\|ignor" outputfile.txt`

False dead-node reports for non-existent peers should be ignored. Re-registration of existing peers should return ACK without PL duplication.

### Useful One-Liners

```bash
# Full consensus timeline
grep "PROPOSAL\|Vote\|OUTCOME\|REMOVAL" outputfile.txt

# All activity for one peer
grep "\[PEER:7002\]" outputfile.txt

# Gossip propagation trace
grep "gossip\|Gossip" outputfile.txt

# Count gossip messages generated by each peer
grep "Generated gossip" outputfile.txt | awk '{print $3}' | sort | uniq -c

# Compare two experiment runs
diff <(grep OUTCOME logs/output_20260224_2032*.log) <(grep OUTCOME logs/output_20260224_2045*.log)
```
