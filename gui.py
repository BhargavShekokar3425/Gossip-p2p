#!/usr/bin/env python3
"""
Live GUI Visualizer for the Gossip P2P Network.

Monitors outputfile.txt in real-time and renders:
  - Network topology graph (seeds + peers + edges)
  - Live event feed
  - Statistics dashboard (registrations, gossip, dead nodes)

Usage:
    python3 gui.py                           # auto-detect outputfile.txt
    python3 gui.py --log /path/to/output.txt # specify log file
"""

import argparse
import math
import os
import re
import sys
import tkinter as tk
from collections import defaultdict
from datetime import datetime

# ─── Constants ────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(SCRIPT_DIR, "outputfile.txt")

POLL_MS = 300          # How often to check for new log lines (ms)
MAX_EVENTS = 200       # Max events in the feed panel

# ─── Colors ───────────────────────────────────────────────────────────────────
BG           = "#1e1e2e"
BG_PANEL     = "#282840"
FG           = "#cdd6f4"
FG_DIM       = "#6c7086"
ACCENT       = "#89b4fa"
SEED_COLOR   = "#f9e2af"
SEED_OUTLINE = "#f5c211"
PEER_COLOR   = "#89b4fa"
PEER_OUTLINE = "#74c7ec"
PEER_DEAD    = "#f38ba8"
EDGE_COLOR   = "#45475a"
GOSSIP_FLASH = "#a6e3a1"
CONSENSUS_OK = "#a6e3a1"
CONSENSUS_NO = "#f38ba8"
DEAD_COLOR   = "#f38ba8"
HEADER_BG    = "#313244"
STAT_FG      = "#cba6f7"

# ─── Log line regex ───────────────────────────────────────────────────────────
LINE_RE = re.compile(
    r'\[(?P<ts>[^\]]+)\]\s+\[(?P<ntype>SEED|PEER):(?P<port>\d+)\]\s+'
    r'(?P<level>\w+)\s+-\s+(?P<msg>.*)'
)


class NetworkState:
    """Parses log lines and maintains the live state of the network."""

    def __init__(self):
        self.seeds: dict[str, dict] = {}       # id -> {status, port}
        self.peers: dict[str, dict] = {}       # id -> {status, port, gossip_count}
        self.edges: set[tuple] = set()          # (a, b) undirected
        self.events: list[dict] = []
        self.gossip_total = 0
        self.consensus_ok = 0
        self.consensus_no = 0
        self.removals = 0
        self.dead_reports = 0
        self._flash_edges: dict[tuple, float] = {}  # edge -> expire_ts

    def _add_event(self, ts, ntype, port, level, msg, tag="info"):
        self.events.append({
            "ts": ts, "node": f"{ntype}:{port}",
            "msg": msg, "tag": tag
        })
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]

    def process_line(self, line: str):
        m = LINE_RE.match(line.strip())
        if not m:
            return
        ts    = m.group("ts")
        ntype = m.group("ntype")
        port  = int(m.group("port"))
        level = m.group("level")
        msg   = m.group("msg")
        nid   = f"127.0.0.1:{port}"

        # ── Track nodes ──────────────────────────────────────────────
        if ntype == "SEED" and nid not in self.seeds:
            self.seeds[nid] = {"port": port, "status": "alive"}
        if ntype == "PEER" and nid not in self.peers:
            self.peers[nid] = {"port": port, "status": "alive", "gossip": 0}

        # ── Consensus ────────────────────────────────────────────────
        if "CONSENSUS OUTCOME" in msg:
            if "APPROVED" in msg:
                self.consensus_ok += 1
                self._add_event(ts, ntype, port, level, msg, "consensus_ok")
            else:
                self.consensus_no += 1
                self._add_event(ts, ntype, port, level, msg, "consensus_no")
            return

        # ── Registration ─────────────────────────────────────────────
        if "PROPOSAL:" in msg and "Register" in msg:
            self._add_event(ts, ntype, port, level, msg, "proposal")
            return

        # ── Overlay / neighbors ──────────────────────────────────────
        if "Overlay built" in msg:
            nm = re.search(r"neighbors=\[([^\]]*)\]", msg)
            if nm:
                nbrs = re.findall(r"['\"]?([\d.]+:\d+)['\"]?", nm.group(1))
                for nb in nbrs:
                    e = tuple(sorted([nid, nb]))
                    self.edges.add(e)
            self._add_event(ts, ntype, port, level, msg, "overlay")
            return

        # ── Gossip generated ─────────────────────────────────────────
        if "Generated gossip" in msg:
            self.gossip_total += 1
            if nid in self.peers:
                self.peers[nid]["gossip"] += 1
            self._add_event(ts, ntype, port, level, msg, "gossip")
            return

        # ── Gossip received ──────────────────────────────────────────
        if "Gossip received" in msg:
            self.gossip_total += 1
            fm = re.search(r"from=([\d.]+:\d+)", msg)
            if fm:
                src = fm.group(1)
                e = tuple(sorted([nid, src]))
                self._flash_edges[e] = datetime.now().timestamp() + 1.5
            self._add_event(ts, ntype, port, level, msg, "gossip")
            return

        # ── Dead node report ─────────────────────────────────────────
        if "DEAD NODE REPORT" in msg:
            self.dead_reports += 1
            dm = re.search(r"Dead Node:([\d.]+):(\d+)", msg)
            if dm:
                did = f"{dm.group(1)}:{dm.group(2)}"
                if did in self.peers:
                    self.peers[did]["status"] = "dead"
            self._add_event(ts, ntype, port, level, msg, "dead")
            return

        # ── Confirmed removal ────────────────────────────────────────
        if "CONFIRMED REMOVAL" in msg:
            self.removals += 1
            rm = re.search(r"CONFIRMED REMOVAL:\s*([\d.]+:\d+)", msg)
            if rm:
                rid = rm.group(1)
                if rid in self.peers:
                    self.peers[rid]["status"] = "removed"
                # Remove edges involving this peer
                self.edges = {e for e in self.edges if rid not in e}
            self._add_event(ts, ntype, port, level, msg, "removal")
            return

        # ── Vote ─────────────────────────────────────────────────────
        if "Vote from" in msg or "Voting" in msg:
            self._add_event(ts, ntype, port, level, msg, "vote")
            return

        # ── Other important events ───────────────────────────────────
        if level in ("WARNING", "ERROR"):
            self._add_event(ts, ntype, port, level, msg, "warn")
        elif any(kw in msg for kw in ("initialized", "listening", "Registration complete",
                                       "Peer List", "fully active")):
            self._add_event(ts, ntype, port, level, msg, "info")

    def is_flash(self, edge):
        exp = self._flash_edges.get(edge, 0)
        return datetime.now().timestamp() < exp


class GossipGUI:
    """Tkinter-based live dashboard."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.state = NetworkState()
        self._file_pos = 0

        # ── Root window ──────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("Gossip Network — Live Visualizer")
        self.root.configure(bg=BG)
        self.root.geometry("1280x780")
        self.root.minsize(900, 600)

        # ── Header ───────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=HEADER_BG, height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text=" ⬡  Gossip Network — Live Visualizer",
                 font=("Consolas", 14, "bold"), bg=HEADER_BG, fg=ACCENT,
                 anchor="w").pack(side=tk.LEFT, padx=12)
        self.status_lbl = tk.Label(hdr, text="● WATCHING",
                                   font=("Consolas", 10), bg=HEADER_BG,
                                   fg=GOSSIP_FLASH, anchor="e")
        self.status_lbl.pack(side=tk.RIGHT, padx=12)

        # ── Main panes ───────────────────────────────────────────────
        main = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                              bg=BG, sashwidth=4, sashrelief=tk.FLAT)
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left: Canvas
        left = tk.Frame(main, bg=BG_PANEL, bd=0)
        main.add(left, width=700)
        tk.Label(left, text="  NETWORK TOPOLOGY", font=("Consolas", 10, "bold"),
                 bg=BG_PANEL, fg=FG_DIM, anchor="w").pack(fill=tk.X, padx=8, pady=(6,0))
        self.canvas = tk.Canvas(left, bg=BG_PANEL, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Right: Stats + Event Feed
        right = tk.Frame(main, bg=BG_PANEL, bd=0)
        main.add(right, width=560)

        # Stats box
        stats_frame = tk.Frame(right, bg=HEADER_BG, bd=0)
        stats_frame.pack(fill=tk.X, padx=6, pady=(6,3))
        tk.Label(stats_frame, text="  DASHBOARD", font=("Consolas", 10, "bold"),
                 bg=HEADER_BG, fg=FG_DIM, anchor="w").pack(fill=tk.X, padx=4, pady=(4,0))
        self.stats_text = tk.Text(stats_frame, height=8, bg=HEADER_BG, fg=FG,
                                  font=("Consolas", 11), wrap=tk.WORD, bd=0,
                                  highlightthickness=0, padx=10, pady=4)
        self.stats_text.pack(fill=tk.X, padx=4, pady=(0,6))
        self.stats_text.config(state=tk.DISABLED)

        # Event feed
        tk.Label(right, text="  LIVE EVENT FEED", font=("Consolas", 10, "bold"),
                 bg=BG_PANEL, fg=FG_DIM, anchor="w").pack(fill=tk.X, padx=8, pady=(6,0))
        feed_frame = tk.Frame(right, bg=BG_PANEL)
        feed_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0,6))
        self.feed_text = tk.Text(feed_frame, bg="#1e1e30", fg=FG,
                                 font=("Consolas", 9), wrap=tk.WORD, bd=0,
                                 highlightthickness=0, padx=8, pady=4)
        scrollbar = tk.Scrollbar(feed_frame, command=self.feed_text.yview)
        self.feed_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.feed_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.feed_text.config(state=tk.DISABLED)

        # Tag colors for feed
        self.feed_text.tag_configure("consensus_ok", foreground=CONSENSUS_OK)
        self.feed_text.tag_configure("consensus_no", foreground=CONSENSUS_NO)
        self.feed_text.tag_configure("proposal",     foreground=SEED_COLOR)
        self.feed_text.tag_configure("gossip",       foreground=GOSSIP_FLASH)
        self.feed_text.tag_configure("dead",         foreground=DEAD_COLOR)
        self.feed_text.tag_configure("removal",      foreground=DEAD_COLOR)
        self.feed_text.tag_configure("overlay",      foreground=PEER_COLOR)
        self.feed_text.tag_configure("vote",         foreground=FG_DIM)
        self.feed_text.tag_configure("info",         foreground=FG)
        self.feed_text.tag_configure("warn",         foreground=CONSENSUS_NO)
        self.feed_text.tag_configure("ts",           foreground=FG_DIM)

        # Legend
        legend = tk.Frame(self.root, bg=HEADER_BG, height=28)
        legend.pack(fill=tk.X)
        legend.pack_propagate(False)
        for color, label in [(SEED_COLOR, "Seed"), (PEER_COLOR, "Peer"),
                              (GOSSIP_FLASH, "Gossip"), (PEER_DEAD, "Dead/Removed")]:
            tk.Label(legend, text="●", fg=color, bg=HEADER_BG,
                     font=("Consolas", 12)).pack(side=tk.LEFT, padx=(12,2))
            tk.Label(legend, text=label, fg=FG_DIM, bg=HEADER_BG,
                     font=("Consolas", 9)).pack(side=tk.LEFT, padx=(0,8))

        self._last_feed_len = 0
        self._node_positions = {}

    # ── Log tailing ──────────────────────────────────────────────────

    def _poll_log(self):
        try:
            if os.path.exists(self.log_path):
                with open(self.log_path, 'r') as f:
                    f.seek(self._file_pos)
                    new_lines = f.readlines()
                    self._file_pos = f.tell()
                for line in new_lines:
                    self.state.process_line(line)
        except Exception:
            pass
        self._redraw()
        self.root.after(POLL_MS, self._poll_log)

    # ── Drawing ──────────────────────────────────────────────────────

    def _compute_positions(self):
        """Arrange seeds in a row at top, peers in a circle below."""
        cw = self.canvas.winfo_width() or 700
        ch = self.canvas.winfo_height() or 500
        pos = {}

        seeds = list(self.state.seeds.keys())
        peers = [p for p, d in self.state.peers.items() if d["status"] != "removed"]
        dead_peers = [p for p, d in self.state.peers.items() if d["status"] == "removed"]

        # Seeds — horizontal row near top
        if seeds:
            spacing = min(120, (cw - 80) / max(len(seeds), 1))
            sx = (cw - spacing * (len(seeds) - 1)) / 2
            for i, sid in enumerate(seeds):
                pos[sid] = (sx + i * spacing, 60)

        # Living peers — circle
        n = len(peers)
        if n > 0:
            cx, cy = cw / 2, ch / 2 + 30
            r = min(cw, ch) * 0.32
            for i, pid in enumerate(peers):
                angle = -math.pi / 2 + 2 * math.pi * i / n
                px = cx + r * math.cos(angle)
                py = cy + r * math.sin(angle)
                pos[pid] = (px, py)

        # Dead/removed — small cluster bottom-right
        for i, pid in enumerate(dead_peers):
            pos[pid] = (cw - 60 - (i % 3) * 40, ch - 40 - (i // 3) * 40)

        self._node_positions = pos

    def _redraw(self):
        c = self.canvas
        c.delete("all")
        self._compute_positions()
        pos = self._node_positions

        # ── Edges ────────────────────────────────────────────────────
        for (a, b) in self.state.edges:
            if a in pos and b in pos:
                x1, y1 = pos[a]
                x2, y2 = pos[b]
                if self.state.is_flash((a, b)) or self.state.is_flash(tuple(sorted([a, b]))):
                    color, width = GOSSIP_FLASH, 2.5
                else:
                    color, width = EDGE_COLOR, 1
                c.create_line(x1, y1, x2, y2, fill=color, width=width)

        # ── Seed nodes ───────────────────────────────────────────────
        r = 22
        for sid, info in self.state.seeds.items():
            if sid not in pos:
                continue
            x, y = pos[sid]
            # Diamond shape for seeds
            pts = [x, y-r, x+r, y, x, y+r, x-r, y]
            c.create_polygon(pts, fill=SEED_COLOR, outline=SEED_OUTLINE, width=2)
            c.create_text(x, y, text=f"S\n{info['port']}", fill="#1e1e2e",
                          font=("Consolas", 8, "bold"), justify=tk.CENTER)

        # ── Peer nodes ───────────────────────────────────────────────
        for pid, info in self.state.peers.items():
            if pid not in pos:
                continue
            x, y = pos[pid]
            if info["status"] == "dead":
                fill, outline = PEER_DEAD, "#e6546d"
            elif info["status"] == "removed":
                fill, outline = "#45475a", "#585b70"
            else:
                fill, outline = PEER_COLOR, PEER_OUTLINE
            c.create_oval(x-r, y-r, x+r, y+r, fill=fill, outline=outline, width=2)
            label = f"P\n{info['port']}"
            c.create_text(x, y, text=label, fill="#1e1e2e",
                          font=("Consolas", 8, "bold"), justify=tk.CENTER)
            # Gossip count badge
            gc = info.get("gossip", 0)
            if gc > 0:
                bx, by = x + r - 2, y - r + 2
                c.create_oval(bx-8, by-8, bx+8, by+8,
                              fill=GOSSIP_FLASH, outline="")
                c.create_text(bx, by, text=str(gc), fill="#1e1e2e",
                              font=("Consolas", 7, "bold"))

        # ── "No data" placeholder ────────────────────────────────────
        if not self.state.seeds and not self.state.peers:
            cw = c.winfo_width() or 700
            ch = c.winfo_height() or 500
            c.create_text(cw/2, ch/2 - 10,
                          text="Waiting for network data…",
                          fill=FG_DIM, font=("Consolas", 14))
            c.create_text(cw/2, ch/2 + 15,
                          text="Start with:  ./launch_network.sh gui",
                          fill=FG_DIM, font=("Consolas", 10))

        # ── Stats panel ──────────────────────────────────────────────
        self._update_stats()

        # ── Event feed ───────────────────────────────────────────────
        self._update_feed()

    def _update_stats(self):
        s = self.state
        alive = sum(1 for p in s.peers.values() if p["status"] == "alive")
        dead  = sum(1 for p in s.peers.values() if p["status"] in ("dead", "removed"))
        total_gossip_gen = sum(p.get("gossip", 0) for p in s.peers.values())

        lines = [
            f"  Seeds:  {len(s.seeds)}          Peers:  {alive} alive / {dead} dead",
            f"  Edges:  {len(s.edges)}          Gossip events:  {s.gossip_total}",
            f"  Consensus:  ✓ {s.consensus_ok}  approved   ✗ {s.consensus_no}  rejected",
            f"  Dead reports:  {s.dead_reports}      Removals:  {s.removals}",
            f"  Gossip generated:  {total_gossip_gen}  /  {alive * 10} max",
        ]
        self.stats_text.config(state=tk.NORMAL)
        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, "\n".join(lines))
        self.stats_text.config(state=tk.DISABLED)

    def _update_feed(self):
        evts = self.state.events
        if len(evts) == self._last_feed_len:
            return
        new_events = evts[self._last_feed_len:]
        self._last_feed_len = len(evts)

        self.feed_text.config(state=tk.NORMAL)
        for ev in new_events:
            ts_str = f"[{ev['ts']}] "
            node_str = f"[{ev['node']}] "
            self.feed_text.insert(tk.END, ts_str, "ts")
            self.feed_text.insert(tk.END, node_str, ev["tag"])
            self.feed_text.insert(tk.END, ev["msg"] + "\n", ev["tag"])
        self.feed_text.see(tk.END)
        self.feed_text.config(state=tk.DISABLED)

    # ── Run ──────────────────────────────────────────────────────────

    def run(self):
        self.root.after(100, self._poll_log)
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Live GUI for Gossip Network")
    parser.add_argument("--log", default=DEFAULT_LOG,
                        help="Path to outputfile.txt (default: auto-detect)")
    args = parser.parse_args()
    gui = GossipGUI(args.log)
    gui.run()


if __name__ == "__main__":
    main()
