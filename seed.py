"""
Seed Node for the Gossip P2P Network.

Responsibilities:
  - Maintain global Peer List (PL)
  - Handle peer registration via consensus                  (seed quorum)
  - Handle dead-node removal  via consensus                 (seed quorum)
  - Communicate with other seeds for proposals and votes
  - Prevent unilateral membership changes

Logging (per spec):
  - Peer registration proposals
  - Consensus outcomes  (approved / rejected)
  - Confirmed dead-node removals

Usage:
    python seed.py --host 127.0.0.1 --port 6000 --config config.txt
"""

import argparse
import os
import socket
import threading
import time
import uuid

from protocol import (
    MSG_DELIMITER,
    MSG_REGISTER_REQUEST, MSG_REGISTER_ACK, MSG_REGISTER_NACK,
    MSG_DEAD_NODE_REPORT, MSG_GET_PEER_LIST, MSG_PEER_LIST,
    MSG_PROPOSE_REGISTER, MSG_VOTE_REGISTER,
    MSG_PROPOSE_REMOVE,   MSG_VOTE_REMOVE,
    MSG_SEED_SYNC, MSG_REMOVAL_NOTIFY,
    make_message, parse_messages,
    peer_id, parse_peer_id, load_config
)
from logger import get_logger


class SeedNode:
    """
    Distributed membership authority for the gossip network.
    Manages peer registration / removal through majority consensus with
    other seed nodes.  Seed nodes are NOT peers — they do not gossip.
    """

    def __init__(self, host: str, port: int, config_path: str):
        self.host = host
        self.port = port
        self.my_id = peer_id(host, port)

        # Load seed list from config.txt  (CSV / colon-separated)
        self.seeds = load_config(config_path)

        self.logger = get_logger(f"Seed-{port}", node_type="seed", port=port)

        # ── Membership State ──────────────────────────────────────────────
        self.peer_list = {}                       # {peer_id: {"host", "port", "joined"}}
        self.peer_list_lock = threading.Lock()

        # ── Other Seeds ───────────────────────────────────────────────────
        self.other_seeds = [
            s for s in self.seeds
            if peer_id(s["host"], s["port"]) != self.my_id
        ]
        self.total_seeds = len(self.seeds)
        self.quorum = (self.total_seeds // 2) + 1     # ⌊n/2⌋ + 1

        # ── Consensus State ───────────────────────────────────────────────
        self.proposals      = {}                  # {proposal_id: info}
        self.proposals_lock = threading.Lock()

        # ── Pending peer connections awaiting decision ────────────────────
        self.pending_responses = {}               # {proposal_id: (conn, addr)}
        self.pending_lock      = threading.Lock()

        # ── Server Socket ─────────────────────────────────────────────────
        self.server_socket = None
        self.running       = False

        self.logger.info(f"Seed node initialized at {self.my_id}")
        self.logger.info(f"Total seeds: {self.total_seeds}, Quorum: {self.quorum}")
        self.logger.info(f"Other seeds: "
                         f"{[peer_id(s['host'], s['port']) for s in self.other_seeds]}")

    # ═════════════════════════════════════════════════════════════════════════
    # Server Lifecycle
    # ═════════════════════════════════════════════════════════════════════════

    def start(self):
        """Start the seed node server."""
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.settimeout(1.0)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(50)

        self.logger.info(f"Seed node listening on {self.host}:{self.port}")

        # Listener thread for incoming TCP connections
        threading.Thread(target=self._listen_loop, daemon=True).start()
        # Periodic peer-list synchronisation with other seeds
        threading.Thread(target=self._sync_loop, daemon=True).start()

        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.logger.info("Shutting down seed node…")
            self.stop()

    def stop(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        self.logger.info("Seed node stopped.")

    # ═════════════════════════════════════════════════════════════════════════
    # Network Layer
    # ═════════════════════════════════════════════════════════════════════════

    def _listen_loop(self):
        """Accept incoming TCP connections."""
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

    def _handle_connection(self, conn: socket.socket, addr):
        """Read messages from one TCP connection."""
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

    def _dispatch(self, msg: dict, conn, addr):
        """Route an incoming message to the appropriate handler."""
        t = msg.get("type")
        p = msg.get("payload", {})

        if   t == MSG_REGISTER_REQUEST:  self._on_register_request(p, conn, addr)
        elif t == MSG_DEAD_NODE_REPORT:  self._on_dead_node_report(p, conn, addr)
        elif t == MSG_GET_PEER_LIST:     self._on_get_peer_list(conn, addr)
        elif t == MSG_PROPOSE_REGISTER:  self._on_propose_register(p, conn)
        elif t == MSG_VOTE_REGISTER:     self._on_vote_register(p)
        elif t == MSG_PROPOSE_REMOVE:    self._on_propose_remove(p, conn)
        elif t == MSG_VOTE_REMOVE:       self._on_vote_remove(p)
        elif t == MSG_SEED_SYNC:         self._on_seed_sync(p)
        else:
            self.logger.warning(f"Unknown message type: {t}")

    # ── helpers to talk to other seeds ────────────────────────────────────

    def _send_to_seed(self, seed_info, message: bytes) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((seed_info["host"], seed_info["port"]))
            s.sendall(message)
            time.sleep(0.1)
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            self.logger.warning(f"Cannot reach seed "
                                f"{seed_info['host']}:{seed_info['port']}: {e}")
            return False

    def _send_to_seed_with_response(self, seed_info, message: bytes,
                                     timeout=5.0) -> list:
        responses = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((seed_info["host"], seed_info["port"]))
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
        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            self.logger.warning(f"Cannot reach seed "
                                f"{seed_info['host']}:{seed_info['port']}: {e}")
        return responses

    # ═════════════════════════════════════════════════════════════════════════
    # Registration Consensus
    # ═════════════════════════════════════════════════════════════════════════

    def _on_register_request(self, payload, conn, addr):
        """A peer wants to register — kick off seed consensus."""
        req_host = payload.get("host")
        req_port = payload.get("port")
        req_id   = peer_id(req_host, req_port)

        # Already registered — idempotent ACK
        with self.peer_list_lock:
            if req_id in self.peer_list:
                self.logger.info(f"Peer {req_id} already registered — sending ACK")
                try:
                    conn.sendall(make_message(MSG_REGISTER_ACK, {
                        "peer_id": req_id,
                        "message": "Already registered"
                    }))
                except OSError:
                    pass
                return

        # Create proposal and self-vote
        proposal_id = str(uuid.uuid4())[:8]
        proposal = {
            "proposal_id": proposal_id,
            "type":  "register",
            "peer_host": req_host,
            "peer_port": req_port,
            "peer_id":   req_id,
            "proposer":  self.my_id,
            "votes":     {self.my_id: True},
            "decided":   False,
            "created":   time.time(),
        }
        with self.proposals_lock:
            self.proposals[proposal_id] = proposal
        with self.pending_lock:
            self.pending_responses[proposal_id] = (conn, addr)

        # ── LOG: Registration proposal ────────────────────────────────────
        self.logger.info(f"PROPOSAL: Register peer {req_id}  "
                         f"[id={proposal_id}, self-vote=YES, "
                         f"votes=1/{self.quorum} needed]")

        # Collect votes from other seeds
        yes = 1
        for seed in self.other_seeds:
            msg = make_message(MSG_PROPOSE_REGISTER, {
                "proposal_id": proposal_id,
                "peer_host": req_host,
                "peer_port": req_port,
                "peer_id":   req_id,
                "proposer":  self.my_id,
            })
            for resp in self._send_to_seed_with_response(seed, msg, timeout=5.0):
                if resp.get("type") == MSG_VOTE_REGISTER:
                    rp = resp["payload"]
                    if rp.get("proposal_id") == proposal_id and rp.get("vote"):
                        voter = rp["voter"]
                        with self.proposals_lock:
                            self.proposals[proposal_id]["votes"][voter] = True
                            yes = sum(
                                1 for v in self.proposals[proposal_id]["votes"].values() if v
                            )
                        self.logger.info(f"Vote from {voter}: YES  "
                                         f"(total {yes}/{self.quorum})")
        # Decide
        self._decide_registration(proposal_id)

    def _on_propose_register(self, payload, conn):
        """Another seed proposes registering a peer — vote and reply."""
        pid = payload["proposal_id"]
        req_id   = payload["peer_id"]
        proposer = payload["proposer"]

        self.logger.info(f"Received registration proposal {pid} for "
                         f"{req_id} from {proposer}")

        vote = True          # always agree unless policy says otherwise
        with self.peer_list_lock:
            if req_id in self.peer_list:
                vote = True  # idempotent

        with self.proposals_lock:
            if pid not in self.proposals:
                self.proposals[pid] = {
                    "proposal_id": pid,
                    "type": "register",
                    "peer_host": payload.get("peer_host"),
                    "peer_port": payload.get("peer_port"),
                    "peer_id": req_id,
                    "proposer": proposer,
                    "votes": {self.my_id: vote},
                    "decided": False,
                    "created": time.time(),
                }
        self.logger.info(f"Voting {'YES' if vote else 'NO'} on proposal {pid}")
        try:
            conn.sendall(make_message(MSG_VOTE_REGISTER, {
                "proposal_id": pid,
                "voter": self.my_id,
                "vote": vote,
            }))
        except OSError as e:
            self.logger.warning(f"Failed to send vote: {e}")

    def _on_vote_register(self, payload):
        pid   = payload.get("proposal_id")
        voter = payload.get("voter")
        vote  = payload.get("vote", False)
        with self.proposals_lock:
            if pid in self.proposals:
                self.proposals[pid]["votes"][voter] = vote

    def _decide_registration(self, proposal_id):
        """Decide based on collected votes (quorum check)."""
        with self.proposals_lock:
            prop = self.proposals.get(proposal_id)
            if not prop or prop["decided"]:
                return
            yes = sum(1 for v in prop["votes"].values() if v)
            total = len(prop["votes"])

            if yes >= self.quorum:
                prop["decided"] = True
                approved = True
            elif total >= self.total_seeds and yes < self.quorum:
                prop["decided"] = True
                approved = False
            else:
                # Degraded mode — accept with available votes
                prop["decided"] = True
                approved = yes >= self.quorum or yes >= 1

        req_id = prop["peer_id"]

        if approved:
            with self.peer_list_lock:
                self.peer_list[req_id] = {
                    "host": prop["peer_host"],
                    "port": prop["peer_port"],
                    "joined": time.time(),
                }
            # ── LOG: Consensus outcome (approved) ─────────────────────────
            self.logger.info(f"CONSENSUS OUTCOME — APPROVED: Peer {req_id}  "
                             f"[votes={yes}/{total}, quorum={self.quorum}]")
            self.logger.info(f"Current Peer List: {list(self.peer_list.keys())}")

            with self.pending_lock:
                ci = self.pending_responses.pop(proposal_id, None)
            if ci:
                try:
                    ci[0].sendall(make_message(MSG_REGISTER_ACK, {
                        "peer_id": req_id,
                        "message": f"Registration approved ({yes} votes)",
                    }))
                except OSError:
                    pass
        else:
            # ── LOG: Consensus outcome (rejected) ─────────────────────────
            self.logger.info(f"CONSENSUS OUTCOME — REJECTED: Peer {req_id}  "
                             f"[votes={yes}/{total}, quorum={self.quorum}]")
            with self.pending_lock:
                ci = self.pending_responses.pop(proposal_id, None)
            if ci:
                try:
                    ci[0].sendall(make_message(MSG_REGISTER_NACK, {
                        "peer_id": req_id,
                        "message": "Registration rejected — quorum not met",
                    }))
                except OSError:
                    pass

    # ═════════════════════════════════════════════════════════════════════════
    # Dead-Node Removal Consensus
    # ═════════════════════════════════════════════════════════════════════════

    def _on_dead_node_report(self, payload, conn, addr):
        """
        A peer reports a dead node (already passed peer-level consensus).
        Initiate seed-level consensus before removing.
        """
        dead_id   = payload.get("dead_peer_id")
        reporter  = payload.get("reporter_id")
        peer_votes = payload.get("peer_votes", 0)
        raw_report = payload.get("report_string", "")   # formatted string

        self.logger.info(f"Dead-node report received: {raw_report}")
        self.logger.info(f"Reporter={reporter}, peer-level votes={peer_votes}")

        with self.peer_list_lock:
            if dead_id not in self.peer_list:
                self.logger.warning(f"Dead node {dead_id} not in Peer List — ignoring")
                return

        # Create removal proposal
        proposal_id = str(uuid.uuid4())[:8]
        proposal = {
            "proposal_id": proposal_id,
            "type": "remove",
            "peer_id": dead_id,
            "reporter": reporter,
            "peer_votes": peer_votes,
            "proposer": self.my_id,
            "votes": {self.my_id: True},
            "decided": False,
            "created": time.time(),
        }
        with self.proposals_lock:
            self.proposals[proposal_id] = proposal

        # ── LOG: Removal proposal ─────────────────────────────────────────
        self.logger.info(f"PROPOSAL: Remove dead peer {dead_id}  "
                         f"[id={proposal_id}, reporter={reporter}]")

        yes = 1
        for seed in self.other_seeds:
            msg = make_message(MSG_PROPOSE_REMOVE, {
                "proposal_id": proposal_id,
                "peer_id": dead_id,
                "reporter": reporter,
                "peer_votes": peer_votes,
                "proposer": self.my_id,
            })
            for resp in self._send_to_seed_with_response(seed, msg, timeout=5.0):
                if resp.get("type") == MSG_VOTE_REMOVE:
                    rp = resp["payload"]
                    if rp.get("proposal_id") == proposal_id and rp.get("vote"):
                        voter = rp["voter"]
                        with self.proposals_lock:
                            self.proposals[proposal_id]["votes"][voter] = True
                            yes = sum(
                                1 for v in self.proposals[proposal_id]["votes"].values() if v
                            )
                        self.logger.info(f"Removal vote from {voter}: YES  "
                                         f"(total {yes}/{self.quorum})")
        self._decide_removal(proposal_id)

    def _on_propose_remove(self, payload, conn):
        pid      = payload["proposal_id"]
        dead_id  = payload["peer_id"]
        proposer = payload["proposer"]

        self.logger.info(f"Received removal proposal {pid} for "
                         f"{dead_id} from {proposer}")
        vote = True
        with self.peer_list_lock:
            if dead_id not in self.peer_list:
                vote = False
                self.logger.warning(f"Peer {dead_id} not in our list — voting NO")

        with self.proposals_lock:
            if pid not in self.proposals:
                self.proposals[pid] = {
                    "proposal_id": pid, "type": "remove",
                    "peer_id": dead_id, "proposer": proposer,
                    "peer_votes": payload.get("peer_votes", 0),
                    "votes": {self.my_id: vote}, "decided": False,
                    "created": time.time(),
                }
        self.logger.info(f"Voting {'YES' if vote else 'NO'} on removal {pid}")
        try:
            conn.sendall(make_message(MSG_VOTE_REMOVE, {
                "proposal_id": pid,
                "voter": self.my_id,
                "vote": vote,
            }))
        except OSError:
            pass

    def _on_vote_remove(self, payload):
        pid = payload.get("proposal_id")
        voter = payload.get("voter")
        vote  = payload.get("vote", False)
        with self.proposals_lock:
            if pid in self.proposals:
                self.proposals[pid]["votes"][voter] = vote

    def _decide_removal(self, proposal_id):
        with self.proposals_lock:
            prop = self.proposals.get(proposal_id)
            if not prop or prop["decided"]:
                return
            yes = sum(1 for v in prop["votes"].values() if v)
            prop["decided"] = True

        dead_id = prop["peer_id"]
        if yes >= self.quorum:
            with self.peer_list_lock:
                removed = self.peer_list.pop(dead_id, None)
            if removed:
                # ── LOG: Confirmed dead-node removal ──────────────────────
                self.logger.info(f"CONFIRMED REMOVAL: Peer {dead_id} removed from "
                                 f"Peer List  [seed votes={yes}/{self.quorum}]")
                self.logger.info(f"Current Peer List: {list(self.peer_list.keys())}")
            else:
                self.logger.info(f"Peer {dead_id} already removed")
        else:
            self.logger.info(f"Removal REJECTED for {dead_id} — "
                             f"only {yes}/{self.quorum} seed votes")

    # ═════════════════════════════════════════════════════════════════════════
    # Peer List Service
    # ═════════════════════════════════════════════════════════════════════════

    def _on_get_peer_list(self, conn, addr):
        with self.peer_list_lock:
            pl = dict(self.peer_list)
        self.logger.info(f"Sending Peer List ({len(pl)} peers) to {addr}")
        try:
            conn.sendall(make_message(MSG_PEER_LIST, {
                "peers": pl,
                "seed_id": self.my_id,
            }))
        except OSError as e:
            self.logger.warning(f"Failed to send Peer List: {e}")

    # ═════════════════════════════════════════════════════════════════════════
    # Seed-to-Seed Synchronisation
    # ═════════════════════════════════════════════════════════════════════════

    def _sync_loop(self):
        while self.running:
            time.sleep(15)
            self._sync_with_seeds()

    def _sync_with_seeds(self):
        with self.peer_list_lock:
            my_peers = dict(self.peer_list)
        for seed in self.other_seeds:
            msg = make_message(MSG_SEED_SYNC, {
                "peers": my_peers,
                "sender": self.my_id,
            })
            for resp in self._send_to_seed_with_response(seed, msg, 3.0):
                if resp.get("type") == MSG_SEED_SYNC:
                    self._merge(resp.get("payload", {}).get("peers", {}))

    def _on_seed_sync(self, payload):
        self._merge(payload.get("peers", {}))

    def _merge(self, remote: dict):
        with self.peer_list_lock:
            for pid, info in remote.items():
                if pid not in self.peer_list:
                    self.peer_list[pid] = info
                    self.logger.info(f"Merged peer {pid} from seed sync")

    # ═════════════════════════════════════════════════════════════════════════
    # Status helper
    # ═════════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        with self.peer_list_lock:
            peers = list(self.peer_list.keys())
        return {
            "seed_id": self.my_id,
            "peers": peers,
            "peer_count": len(peers),
            "quorum": self.quorum,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Entry Point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Gossip Network — Seed Node")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, required=True, help="Bind port")
    parser.add_argument("--config", default="config.txt",
                        help="Config file (seed list)")
    args = parser.parse_args()

    cfg = args.config
    if not os.path.isabs(cfg):
        cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg)

    SeedNode(args.host, args.port, cfg).start()


if __name__ == "__main__":
    main()
