"""
Shared protocol definitions for the Gossip P2P Network.

Message types and serialization for seed-seed, seed-peer, and peer-peer communication.
All messages are JSON-encoded and terminated with a newline delimiter.
"""

import json
import hashlib
import time

# ─── Delimiter for TCP framing ───────────────────────────────────────────────
MSG_DELIMITER = b'\n'

# ─── Message Types ───────────────────────────────────────────────────────────

# Peer → Seed
MSG_REGISTER_REQUEST = "REGISTER_REQUEST"       # Peer wants to join
MSG_DEAD_NODE_REPORT = "DEAD_NODE_REPORT"       # Peer reports a dead node
MSG_GET_PEER_LIST    = "GET_PEER_LIST"           # Peer requests current peer list

# Seed → Peer
MSG_REGISTER_ACK     = "REGISTER_ACK"           # Registration approved
MSG_REGISTER_NACK    = "REGISTER_NACK"          # Registration rejected
MSG_PEER_LIST        = "PEER_LIST"              # Response with peer list
MSG_REMOVAL_NOTIFY   = "REMOVAL_NOTIFY"         # Notify peer of a removal

# Seed ↔ Seed (Consensus)
MSG_PROPOSE_REGISTER = "PROPOSE_REGISTER"       # Propose new peer registration
MSG_VOTE_REGISTER    = "VOTE_REGISTER"          # Vote on registration proposal
MSG_PROPOSE_REMOVE   = "PROPOSE_REMOVE"         # Propose peer removal
MSG_VOTE_REMOVE      = "VOTE_REMOVE"            # Vote on removal proposal
MSG_SEED_SYNC        = "SEED_SYNC"              # Sync peer lists between seeds

# Peer ↔ Peer
MSG_GOSSIP           = "GOSSIP"                 # Gossip message
MSG_PING             = "PING"                   # Liveness ping
MSG_PONG             = "PONG"                   # Liveness pong
MSG_SUSPECT_QUERY    = "SUSPECT_QUERY"          # Ask neighbor about suspected node
MSG_SUSPECT_RESPONSE = "SUSPECT_RESPONSE"       # Response about suspected node

# ─── Maximum gossip messages each peer may generate ──────────────────────────
MAX_GOSSIP_MESSAGES = 10

# ─── Gossip generation interval (seconds) ────────────────────────────────────
GOSSIP_INTERVAL = 5


# ─── Message Construction ────────────────────────────────────────────────────

def make_message(msg_type: str, payload: dict = None) -> bytes:
    """Create a framed message ready to send over TCP."""
    msg = {
        "type": msg_type,
        "timestamp": time.time(),
        "payload": payload or {}
    }
    return json.dumps(msg).encode('utf-8') + MSG_DELIMITER


def parse_messages(buffer: bytes):
    """
    Parse one or more messages from a byte buffer.
    Returns (list_of_parsed_messages, remaining_buffer).
    """
    messages = []
    while MSG_DELIMITER in buffer:
        line, buffer = buffer.split(MSG_DELIMITER, 1)
        if line.strip():
            try:
                messages.append(json.loads(line.decode('utf-8')))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Skip malformed messages
    return messages, buffer


def gossip_message_id(timestamp: float, ip: str, port: int, msg_num: int) -> str:
    """
    Create a gossip message identifier in the required format:
        <timestamp>:<IP>:<Port>:<Msg#>
    """
    return f"{timestamp}:{ip}:{port}:{msg_num}"


def hash_message(content: str) -> str:
    """SHA-256 hash of a message for deduplication."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def peer_id(host: str, port: int) -> str:
    """Canonical peer identifier."""
    return f"{host}:{port}"


def parse_peer_id(pid: str):
    """Parse a peer_id string back into (host, port)."""
    host, port_str = pid.rsplit(':', 1)
    return host, int(port_str)


def dead_node_message(dead_ip: str, dead_port: int, self_timestamp: float, self_ip: str) -> str:
    """
    Build the dead-node report string in the required format:
        Dead Node:<DeadNode.IP>:<DeadNode.Port>:<self.timestamp>:<self.IP>
    """
    return f"Dead Node:{dead_ip}:{dead_port}:{self_timestamp}:{self_ip}"


# ─── Config File Parser ─────────────────────────────────────────────────────

def load_config(config_path: str) -> list:
    """
    Parse config.txt / config.csv.
    Each non-empty line is  IP:Port  of a seed node.
    Returns a list of dicts: [{"host": str, "port": int}, ...]
    """
    seeds = []
    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # Support both  IP:Port  and  IP,Port  formats
            if ',' in line:
                parts = line.split(',', 1)
            else:
                parts = line.rsplit(':', 1)
            if len(parts) == 2:
                host = parts[0].strip()
                try:
                    port = int(parts[1].strip())
                    seeds.append({"host": host, "port": port})
                except ValueError:
                    pass  # Skip malformed lines
    return seeds
