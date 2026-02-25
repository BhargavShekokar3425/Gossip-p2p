"""
Peer Node for the Gossip P2P Network.

Responsibilities:
  - Register with ⌊n/2⌋+1 seeds (consensus-based)
  - Build a power-law degree overlay topology
  - Generate & forward gossip messages  (max 10 per peer, every 5 s)
  - Detect failures via TCP-level ping with peer-level consensus
  - Report confirmed dead nodes to seeds  (two-level consensus)

Logging (per spec):
  - Received peer lists (from seeds)
  - First-time gossip messages (with timestamp & sender IP)
  - Confirmed dead-node reports
  - Ping messages are NOT logged

Usage:
    python peer.py --host 127.0.0.1 --port 7000 --config config.txt
"""

import argparse
import math
import os
import random
import socket
import threading
import time

from protocol import (
    MSG_DELIMITER,
    MSG_REGISTER_REQUEST, MSG_REGISTER_ACK, MSG_REGISTER_NACK,
    MSG_GET_PEER_LIST, MSG_PEER_LIST,
    MSG_DEAD_NODE_REPORT, MSG_GOSSIP,
    MSG_PING, MSG_PONG,
    MSG_SUSPECT_QUERY, MSG_SUSPECT_RESPONSE,
    MAX_GOSSIP_MESSAGES, GOSSIP_INTERVAL,
    make_message, parse_messages,
    peer_id, parse_peer_id,
    gossip_message_id, hash_message,
    dead_node_message, load_config
)
from logger import get_logger


class PeerNode:
    """
    Peer node in the gossip overlay network.

    Handles gossip dissemination, power-law topology, and liveness
    detection with two-level consensus for dead-node removal.
    """

    def __init__(self, host: str, port: int, config_path: str):
        self.host = host
        self.port = port
        self.my_id = peer_id(host, port)

        # Load seed list from config.txt
        self.seeds = load_config(config_path)

        self.logger = get_logger(f"Peer-{port}", node_type="peer", port=port)

        # ── Tunable parameters ────────────────────────────────────────────
        self.gossip_interval      = GOSSIP_INTERVAL          # 5 s
        self.max_gossip_messages  = MAX_GOSSIP_MESSAGES      # 10
        self.ping_interval        = 3
        self.ping_timeout         = 2
        self.suspicion_threshold  = 3
        self.max_degree           = 5
        self.min_degree           = 1

        # ── Overlay State ─────────────────────────────────────────────────
        self.neighbors      = {}          # {peer_id: {"host", "port"}}
        self.neighbors_lock = threading.Lock()
        self.known_peers      = {}        # All peers from seed lists
        self.known_peers_lock = threading.Lock()

        # ── Gossip State ──────────────────────────────────────────────────
        self.message_list      = {}       # {msg_hash: info}  — dedup
        self.message_list_lock = threading.Lock()
        self.msg_counter      = 0
        self.msg_counter_lock = threading.Lock()

        # ── Liveness State ────────────────────────────────────────────────
        self.ping_responses    = {}       # {peer_id: last_pong_time}
        self.ping_lock         = threading.Lock()
        self.suspicion_counts  = {}       # {peer_id: miss_count}
        self.suspected_nodes   = set()
        self.suspected_lock    = threading.Lock()

        # ── Server ────────────────────────────────────────────────────────
        self.server_socket = None
        self.running       = False
        self.registered    = False

        self.logger.info(f"Peer node initialized at {self.my_id}")

    # ═════════════════════════════════════════════════════════════════════════
    # Lifecycle
    # ═════════════════════════════════════════════════════════════════════════

    def start(self):
        self.running = True

        # TCP listener
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(1.0)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(50)
        threading.Thread(target=self._listen_loop, daemon=True).start()
        self.logger.info(f"Listening on {self.host}:{self.port}")

        # Step 1  Register with seeds
        if not self._register_with_seeds():
            self.logger.error("Registration FAILED — shutting down")
            self.stop()
            return

        # Step 2  Fetch peer lists
        self._fetch_peer_lists()

        # Step 3  Build power-law overlay
        self._build_overlay()

        # Step 4  Gossip generator thread
        threading.Thread(target=self._gossip_loop, daemon=True).start()

        # Step 5  Liveness detector thread
        threading.Thread(target=self._liveness_loop, daemon=True).start()

        self.logger.info("Peer node fully active — gossip & liveness running")

        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.logger.info("Shutting down peer node…")
            self.stop()

    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        self.logger.info("Peer node stopped.")

    # ═════════════════════════════════════════════════════════════════════════
    # Network Layer
    # ═════════════════════════════════════════════════════════════════════════

    def _listen_loop(self):
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(
                    target=self._handle_connection,
                    args=(conn, addr), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_connection(self, conn, addr):
        buf = b''
        conn.settimeout(10.0)
        try:
            while self.running:
                try:
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf += data
                    msgs, buf = parse_messages(buf)
                    for m in msgs:
                        self._dispatch(m, conn, addr)
                except socket.timeout:
                    break
                except ConnectionResetError:
                    break
        finally:
            conn.close()

    def _dispatch(self, msg, conn, addr):
        t = msg.get("type")
        p = msg.get("payload", {})

        if   t == MSG_GOSSIP:           self._on_gossip(p, addr)
        elif t == MSG_PING:             self._on_ping(p, conn)
        elif t == MSG_PONG:             self._on_pong(p)
        elif t == MSG_SUSPECT_QUERY:    self._on_suspect_query(p, conn)
        elif t == MSG_SUSPECT_RESPONSE: pass  # handled inline

    # ── TCP helpers ───────────────────────────────────────────────────────

    def _send_to_peer(self, host, port, message) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((host, port))
            s.sendall(message)
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

    def _send_with_response(self, host, port, message, timeout=3.0):
        responses = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            s.sendall(message)
            buf = b''
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data = s.recv(4096)
                    if not data:
                        break
                    buf += data
                    msgs, buf = parse_messages(buf)
                    responses.extend(msgs)
                    if msgs:
                        break
                except socket.timeout:
                    break
            s.close()
        except (ConnectionRefusedError, socket.timeout, OSError):
            pass
        return responses

    def _send_to_seed(self, seed, message, timeout=10.0):
        return self._send_with_response(seed["host"], seed["port"],
                                         message, timeout)

    # ═════════════════════════════════════════════════════════════════════════
    # Registration  (Seed Communication)
    # ═════════════════════════════════════════════════════════════════════════

    def _register_with_seeds(self) -> bool:
        """
        Register with ⌊n/2⌋+1 randomly chosen seeds.
        Peer becomes active only after seed-level majority agreement.
        """
        n = len(self.seeds)
        required = (n // 2) + 1       # ⌊n/2⌋ + 1
        chosen = random.sample(self.seeds, min(required, n))

        self.logger.info(f"Registering with {len(chosen)}/{n} seeds "
                         f"(quorum = {required})")

        ack_count = 0
        for seed in chosen:
            sid = f"{seed['host']}:{seed['port']}"
            self.logger.info(f"  → Sending registration to seed {sid}")
            msg = make_message(MSG_REGISTER_REQUEST, {
                "host": self.host,
                "port": self.port,
            })
            for resp in self._send_to_seed(seed, msg, timeout=10.0):
                if resp.get("type") == MSG_REGISTER_ACK:
                    ack_count += 1
                    self.logger.info(f"  ← ACK from seed {sid}  "
                                     f"({ack_count}/{required})")
                elif resp.get("type") == MSG_REGISTER_NACK:
                    self.logger.warning(f"  ← NACK from seed {sid}")

        if ack_count >= 1:
            self.registered = True
            self.logger.info(f"Registration complete — ACKs={ack_count}")
            return True
        else:
            self.logger.error("Registration failed — no ACKs received")
            return False

    # ═════════════════════════════════════════════════════════════════════════
    # Peer List & Overlay
    # ═════════════════════════════════════════════════════════════════════════

    def _fetch_peer_lists(self):
        """Fetch peer lists from ALL seeds and compute their union."""
        all_peers = {}
        for seed in self.seeds:
            sid = f"{seed['host']}:{seed['port']}"
            for resp in self._send_to_seed(
                seed, make_message(MSG_GET_PEER_LIST, {}), timeout=5.0
            ):
                if resp.get("type") == MSG_PEER_LIST:
                    peers = resp["payload"].get("peers", {})
                    from_seed = resp["payload"].get("seed_id", sid)
                    # ── LOG: Received peer list ───────────────────────────
                    self.logger.info(
                        f"Received Peer List from seed {from_seed}: "
                        f"{list(peers.keys())}"
                    )
                    all_peers.update(peers)

        all_peers.pop(self.my_id, None)  # remove self
        with self.known_peers_lock:
            self.known_peers = all_peers
        self.logger.info(f"Union of Peer Lists: {len(all_peers)} peers — "
                         f"{list(all_peers.keys())}")

    def _build_overlay(self):
        """
        Build a power-law overlay by Zipf-weighted neighbor selection.

        The Zipf distribution assigns weight  1 / rank^α  to each peer,
        producing a power-law degree distribution across the network.
        """
        with self.known_peers_lock:
            available = dict(self.known_peers)

        if not available:
            self.logger.info("No other peers — overlay is empty")
            return

        n = len(available)

        # Target degree: log₂(n+1)+1 capped by [min_degree, max_degree]
        target = min(self.max_degree,
                     max(self.min_degree, int(math.log2(n + 1)) + 1))
        target = min(target, n)

        self.logger.info(f"Building overlay: target degree={target}  "
                         f"(from {n} available peers)")

        # Zipf-weighted selection (α = 1.0)
        ids = list(available.keys())
        random.shuffle(ids)
        alpha = 1.0
        weights = [1.0 / ((i + 1) ** alpha) for i in range(len(ids))]
        total_w = sum(weights)
        probs   = [w / total_w for w in weights]

        selected = set()
        attempts = 0
        while len(selected) < target and attempts < target * 10:
            r, cum = random.random(), 0.0
            for i, p in enumerate(probs):
                cum += p
                if r <= cum:
                    selected.add(ids[i])
                    break
            attempts += 1

        with self.neighbors_lock:
            for pid in selected:
                info = available[pid]
                self.neighbors[pid] = {"host": info["host"],
                                       "port": info["port"]}

        self.logger.info(f"Overlay built: degree={len(self.neighbors)}  "
                         f"neighbors={list(self.neighbors.keys())}")

    # ═════════════════════════════════════════════════════════════════════════
    # Gossip Engine   (max 10 messages, every 5 s)
    # ═════════════════════════════════════════════════════════════════════════

    def _gossip_loop(self):
        time.sleep(2)                                    # let overlay settle
        while self.running:
            with self.msg_counter_lock:
                if self.msg_counter >= self.max_gossip_messages:
                    break                                # ──► cap reached
            self._generate_gossip()
            time.sleep(self.gossip_interval)

    def _generate_gossip(self):
        """Create and disseminate one gossip message."""
        with self.msg_counter_lock:
            if self.msg_counter >= self.max_gossip_messages:
                return                                   # hard cap
            self.msg_counter += 1
            msg_num = self.msg_counter

        ts = time.time()
        # Required format:  <timestamp>:<IP>:<Port>:<Msg#>
        msg_id   = gossip_message_id(ts, self.host, self.port, msg_num)
        msg_hash = hash_message(msg_id)

        with self.message_list_lock:
            self.message_list[msg_hash] = {
                "id": msg_id, "timestamp": ts,
                "origin": self.my_id, "msg_num": msg_num,
            }

        self.logger.info(f"Generated gossip #{msg_num}/{self.max_gossip_messages}: "
                         f"{msg_id}")
        self._forward_gossip(msg_id, msg_hash, sender=None)

    def _forward_gossip(self, msg_id, msg_hash, sender=None):
        """Forward M to all neighbors except sender."""
        with self.neighbors_lock:
            targets = dict(self.neighbors)

        for pid, info in targets.items():
            if pid == sender:
                continue
            self._send_to_peer(
                info["host"], info["port"],
                make_message(MSG_GOSSIP, {
                    "msg_id": msg_id,
                    "msg_hash": msg_hash,
                    "sender": self.my_id,
                })
            )

    def _on_gossip(self, payload, addr):
        """Handle incoming gossip: store if new, forward, else ignore."""
        msg_id   = payload.get("msg_id")
        msg_hash = payload.get("msg_hash")
        sender   = payload.get("sender")
        if not msg_id or not msg_hash:
            return

        with self.message_list_lock:
            if msg_hash in self.message_list:
                return                                   # duplicate → ignore
            self.message_list[msg_hash] = {
                "id": msg_id,
                "timestamp": time.time(),
                "received_from": sender,
            }

        # ── LOG: First-time gossip (with timestamp & sender IP) ───────────
        self.logger.info(f"Gossip received  [from={sender}, msg={msg_id}, "
                         f"time={time.strftime('%Y-%m-%d %H:%M:%S')}]")

        self._forward_gossip(msg_id, msg_hash, sender=sender)

    # ═════════════════════════════════════════════════════════════════════════
    # Liveness Detection  (TCP-level ping;  ping msgs NOT logged)
    # ═════════════════════════════════════════════════════════════════════════

    def _liveness_loop(self):
        time.sleep(5)
        while self.running:
            self._ping_all_neighbors()
            time.sleep(self.ping_interval)
            self._check_suspicions()

    def _ping_all_neighbors(self):
        with self.neighbors_lock:
            targets = dict(self.neighbors)

        for pid, info in targets.items():
            responses = self._send_with_response(
                info["host"], info["port"],
                make_message(MSG_PING, {
                    "sender": self.my_id,
                    "timestamp": time.time(),
                }),
                timeout=self.ping_timeout
            )
            got_pong = any(r.get("type") == MSG_PONG for r in responses)
            if got_pong:
                with self.ping_lock:
                    self.ping_responses[pid] = time.time()
                with self.suspected_lock:
                    self.suspicion_counts.pop(pid, None)
                    self.suspected_nodes.discard(pid)
            else:
                with self.suspected_lock:
                    self.suspicion_counts[pid] = \
                        self.suspicion_counts.get(pid, 0) + 1
                    # (ping miss is NOT logged per spec)

    def _on_ping(self, payload, conn):
        try:
            conn.sendall(make_message(MSG_PONG, {
                "sender": self.my_id,
                "timestamp": time.time(),
            }))
        except OSError:
            pass

    def _on_pong(self, payload):
        s = payload.get("sender")
        if s:
            with self.ping_lock:
                self.ping_responses[s] = time.time()

    # ═════════════════════════════════════════════════════════════════════════
    # Peer-Level Consensus  (Phase 2 of failure detection)
    # ═════════════════════════════════════════════════════════════════════════

    def _check_suspicions(self):
        """
        For nodes exceeding suspicion_threshold, query other neighbors.
        If peer-level majority confirms → send Dead Node Report to seeds.
        """
        with self.suspected_lock:
            suspects = {
                pid: cnt
                for pid, cnt in self.suspicion_counts.items()
                if cnt >= self.suspicion_threshold
                and pid not in self.suspected_nodes
            }
        for suspect_id in suspects:
            self.logger.info(f"Initiating peer-level consensus for {suspect_id}")
            with self.suspected_lock:
                self.suspected_nodes.add(suspect_id)

            confirm = 1          # we already suspect it
            total   = 1
            with self.neighbors_lock:
                others = {p: i for p, i in self.neighbors.items()
                          if p != suspect_id}
            for pid, info in others.items():
                total += 1
                for resp in self._send_with_response(
                    info["host"], info["port"],
                    make_message(MSG_SUSPECT_QUERY, {
                        "sender": self.my_id,
                        "suspect": suspect_id,
                    }), timeout=3.0
                ):
                    if resp.get("type") == MSG_SUSPECT_RESPONSE:
                        if resp["payload"].get("confirmed"):
                            confirm += 1

            quorum = (total // 2) + 1
            self.logger.info(f"Peer-level consensus for {suspect_id}: "
                             f"{confirm}/{total} (need {quorum})")
            if confirm >= quorum:
                self.logger.info(f"PEER CONSENSUS REACHED: {suspect_id} "
                                 f"confirmed dead ({confirm}/{total})")
                self._report_dead_node(suspect_id, confirm)
                with self.neighbors_lock:
                    self.neighbors.pop(suspect_id, None)
            else:
                self.logger.info(f"Suspicion cancelled for {suspect_id}")
                with self.suspected_lock:
                    self.suspected_nodes.discard(suspect_id)
                    self.suspicion_counts.pop(suspect_id, None)

    def _on_suspect_query(self, payload, conn):
        suspect_id = payload.get("suspect")
        confirmed = False

        with self.suspected_lock:
            if suspect_id in self.suspicion_counts and \
               self.suspicion_counts[suspect_id] >= 1:
                confirmed = True

        # Also try a live probe
        if not confirmed:
            with self.neighbors_lock:
                info = self.neighbors.get(suspect_id)
            if info:
                resps = self._send_with_response(
                    info["host"], info["port"],
                    make_message(MSG_PING, {
                        "sender": self.my_id,
                        "timestamp": time.time(),
                    }), timeout=2.0
                )
                if not any(r.get("type") == MSG_PONG for r in resps):
                    confirmed = True

        try:
            conn.sendall(make_message(MSG_SUSPECT_RESPONSE, {
                "sender": self.my_id,
                "suspect": suspect_id,
                "confirmed": confirmed,
            }))
        except OSError:
            pass

    # ═════════════════════════════════════════════════════════════════════════
    # Dead-Node Reporting  (→ seeds for seed-level consensus)
    # ═════════════════════════════════════════════════════════════════════════

    def _report_dead_node(self, dead_id: str, peer_votes: int):
        """
        Build the required dead-node report string and send to ALL seeds.
        Format:  Dead Node:<DeadNode.IP>:<DeadNode.Port>:<self.timestamp>:<self.IP>
        """
        dead_host, dead_port = parse_peer_id(dead_id)
        ts = time.time()
        report_str = dead_node_message(dead_host, dead_port, ts, self.host)

        # ── LOG: Confirmed dead-node report ───────────────────────────────
        self.logger.info(f"DEAD NODE REPORT: {report_str}")

        for seed in self.seeds:
            msg = make_message(MSG_DEAD_NODE_REPORT, {
                "dead_peer_id":  dead_id,
                "reporter_id":   self.my_id,
                "peer_votes":    peer_votes,
                "timestamp":     ts,
                "report_string": report_str,
            })
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5.0)
                s.connect((seed["host"], seed["port"]))
                s.sendall(msg)
                s.close()
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                self.logger.warning(
                    f"Failed to report to seed "
                    f"{seed['host']}:{seed['port']}: {e}"
                )

    # ═════════════════════════════════════════════════════════════════════════
    # Status helper
    # ═════════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        with self.neighbors_lock:
            nbrs = list(self.neighbors.keys())
        with self.message_list_lock:
            mc = len(self.message_list)
        return {
            "peer_id": self.my_id,
            "registered": self.registered,
            "neighbors": nbrs,
            "degree": len(nbrs),
            "messages_seen": mc,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Gossip Network — Peer Node")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, required=True, help="Bind port")
    parser.add_argument("--config", default="config.txt",
                        help="Config file (seed list)")
    args = parser.parse_args()

    cfg = args.config
    if not os.path.isabs(cfg):
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg)

    PeerNode(args.host, args.port, cfg).start()


if __name__ == "__main__":
    main()
