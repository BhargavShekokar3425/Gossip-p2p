#!/usr/bin/env python3
"""
Gossip Network — Step-by-Step Algorithm Visualizer

An interactive, slow-paced educational GUI that demonstrates how the
gossip-based P2P protocol works. Enter the number of seeds and peers,
then watch each phase animate step by step.

Phases:
  1. Seed Initialization
  2. Peer Registration (with consensus voting)
  3. Power-Law Overlay Construction
  4. Gossip Dissemination (message propagation)
  5. Failure Detection (ping timeout + peer consensus)
  6. Dead-Node Removal (seed consensus)

Usage:
    python3 visualizer.py
"""

import math
import random
import tkinter as tk
from tkinter import ttk
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════════════════════
#  THEME
# ═══════════════════════════════════════════════════════════════════════════════
BG           = "#0f0f1a"
BG_PANEL     = "#1a1a2e"
BG_CARD      = "#16213e"
FG           = "#e0e0ef"
FG_DIM       = "#6c7086"
ACCENT       = "#89b4fa"
SEED_FILL    = "#f9e2af"
SEED_STROKE  = "#f5c211"
PEER_FILL    = "#89b4fa"
PEER_STROKE  = "#74c7ec"
PEER_DEAD    = "#f38ba8"
DEAD_STROKE  = "#e6546d"
EDGE_NORMAL  = "#313244"
EDGE_GOSSIP  = "#a6e3a1"
EDGE_PING    = "#fab387"
VOTE_YES     = "#a6e3a1"
VOTE_NO      = "#f38ba8"
MSG_BUBBLE   = "#cba6f7"
BUTTON_BG    = "#313244"
BUTTON_FG    = "#cdd6f4"
PROGRESS_FG  = "#89b4fa"

ANIM_SLOW  = 1200   # ms for slow steps
ANIM_MED   = 700
ANIM_FAST  = 400


# ═══════════════════════════════════════════════════════════════════════════════
#  SIMULATION STATE
# ═══════════════════════════════════════════════════════════════════════════════

class SimNode:
    def __init__(self, nid, ntype, port, x, y):
        self.nid = nid
        self.ntype = ntype       # "seed" or "peer"
        self.port = port
        self.x = x
        self.y = y
        self.status = "alive"    # alive, dead, removed
        self.neighbors = []
        self.gossip_seen = set()
        self.gossip_count = 0
        self.canvas_ids = []
        self.registered = False


class SimEdge:
    def __init__(self, a, b):
        self.a = a
        self.b = b
        self.canvas_id = None


class Simulation:
    def __init__(self, num_seeds, num_peers):
        self.num_seeds = num_seeds
        self.num_peers = num_peers
        self.seeds: dict[str, SimNode] = {}
        self.peers: dict[str, SimNode] = {}
        self.edges: list[SimEdge] = []
        self.quorum = num_seeds // 2 + 1
        self.phase = 0
        self.step = 0


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN GUI
# ═══════════════════════════════════════════════════════════════════════════════

class VisualizerApp:
    PHASE_NAMES = [
        "Setup",
        "① Seed Initialization",
        "② Peer Registration & Consensus",
        "③ Power-Law Overlay Construction",
        "④ Gossip Dissemination",
        "⑤ Failure Detection",
        "⑥ Dead-Node Removal (Seed Consensus)",
        "✓ Complete",
    ]

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Gossip Network — Algorithm Visualizer")
        self.root.configure(bg=BG)
        self.root.geometry("1360x820")
        self.root.minsize(1000, 650)

        self.sim: Simulation | None = None
        self._anim_queue: list = []
        self._anim_running = False
        self._speed = 1.0

        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────────

    def _build_ui(self):
        # Header bar
        hdr = tk.Frame(self.root, bg="#16213e", height=50)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  ⬡  Gossip Protocol — Step-by-Step Visualizer",
                 font=("Consolas", 15, "bold"), bg="#16213e", fg=ACCENT,
                 anchor="w").pack(side=tk.LEFT, padx=12, fill=tk.Y)

        self.phase_label = tk.Label(hdr, text="Phase: Setup",
                                    font=("Consolas", 11), bg="#16213e",
                                    fg=FG_DIM, anchor="e")
        self.phase_label.pack(side=tk.RIGHT, padx=16)

        # Main body: left (canvas) + right (controls + log)
        body = tk.PanedWindow(self.root, orient=tk.HORIZONTAL,
                              bg=BG, sashwidth=4, sashrelief=tk.FLAT)
        body.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Canvas
        canvas_frame = tk.Frame(body, bg=BG_PANEL)
        body.add(canvas_frame, width=820)
        self.canvas = tk.Canvas(canvas_frame, bg=BG_PANEL, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Right panel
        right = tk.Frame(body, bg=BG)
        body.add(right, width=520)

        # ── Input Card ───────────────────────────────────────────────
        input_card = tk.Frame(right, bg=BG_CARD, bd=0)
        input_card.pack(fill=tk.X, padx=8, pady=(8, 4))

        tk.Label(input_card, text="  CONFIGURATION", font=("Consolas", 10, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w", padx=8, pady=(8, 4))

        row1 = tk.Frame(input_card, bg=BG_CARD)
        row1.pack(fill=tk.X, padx=16, pady=2)
        tk.Label(row1, text="Seeds:", font=("Consolas", 11), bg=BG_CARD,
                 fg=FG, width=8, anchor="w").pack(side=tk.LEFT)
        self.seed_var = tk.StringVar(value="3")
        self.seed_entry = tk.Entry(row1, textvariable=self.seed_var,
                                   font=("Consolas", 12), width=6,
                                   bg=BG_PANEL, fg=SEED_FILL,
                                   insertbackground=FG, bd=0,
                                   highlightthickness=1, highlightcolor=ACCENT)
        self.seed_entry.pack(side=tk.LEFT, padx=4)
        tk.Label(row1, text="(ports 6000+)", font=("Consolas", 9),
                 bg=BG_CARD, fg=FG_DIM).pack(side=tk.LEFT, padx=8)

        row2 = tk.Frame(input_card, bg=BG_CARD)
        row2.pack(fill=tk.X, padx=16, pady=2)
        tk.Label(row2, text="Peers:", font=("Consolas", 11), bg=BG_CARD,
                 fg=FG, width=8, anchor="w").pack(side=tk.LEFT)
        self.peer_var = tk.StringVar(value="5")
        self.peer_entry = tk.Entry(row2, textvariable=self.peer_var,
                                   font=("Consolas", 12), width=6,
                                   bg=BG_PANEL, fg=PEER_FILL,
                                   insertbackground=FG, bd=0,
                                   highlightthickness=1, highlightcolor=ACCENT)
        self.peer_entry.pack(side=tk.LEFT, padx=4)
        tk.Label(row2, text="(ports 7000+)", font=("Consolas", 9),
                 bg=BG_CARD, fg=FG_DIM).pack(side=tk.LEFT, padx=8)

        # Speed slider
        row3 = tk.Frame(input_card, bg=BG_CARD)
        row3.pack(fill=tk.X, padx=16, pady=(6, 2))
        tk.Label(row3, text="Speed:", font=("Consolas", 11), bg=BG_CARD,
                 fg=FG, width=8, anchor="w").pack(side=tk.LEFT)
        self.speed_scale = tk.Scale(row3, from_=0.2, to=3.0, resolution=0.1,
                                    orient=tk.HORIZONTAL, length=160,
                                    bg=BG_CARD, fg=FG, troughcolor=BG_PANEL,
                                    highlightthickness=0, bd=0,
                                    activebackground=ACCENT,
                                    font=("Consolas", 9))
        self.speed_scale.set(1.0)
        self.speed_scale.pack(side=tk.LEFT, padx=4)
        tk.Label(row3, text="x", font=("Consolas", 9),
                 bg=BG_CARD, fg=FG_DIM).pack(side=tk.LEFT)

        # Buttons
        btn_row = tk.Frame(input_card, bg=BG_CARD)
        btn_row.pack(fill=tk.X, padx=16, pady=(8, 12))

        self.start_btn = tk.Button(btn_row, text="▶  Start Simulation",
                                   font=("Consolas", 11, "bold"),
                                   bg=ACCENT, fg="#1e1e2e", bd=0,
                                   activebackground="#b4d0fb",
                                   cursor="hand2", padx=16, pady=6,
                                   command=self._on_start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.next_btn = tk.Button(btn_row, text="⏭  Next Phase",
                                  font=("Consolas", 10),
                                  bg=BUTTON_BG, fg=BUTTON_FG, bd=0,
                                  activebackground="#45475a",
                                  cursor="hand2", padx=12, pady=5,
                                  command=self._on_next_phase, state=tk.DISABLED)
        self.next_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.reset_btn = tk.Button(btn_row, text="↺  Reset",
                                   font=("Consolas", 10),
                                   bg=BUTTON_BG, fg=BUTTON_FG, bd=0,
                                   activebackground="#45475a",
                                   cursor="hand2", padx=12, pady=5,
                                   command=self._on_reset)
        self.reset_btn.pack(side=tk.LEFT)

        # ── Explanation Card ─────────────────────────────────────────
        explain_card = tk.Frame(right, bg=BG_CARD, bd=0)
        explain_card.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(explain_card, text="  WHAT'S HAPPENING", font=("Consolas", 10, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w", padx=8, pady=(8, 0))
        self.explain_text = tk.Text(explain_card, height=5, bg=BG_CARD, fg=FG,
                                    font=("Consolas", 10), wrap=tk.WORD, bd=0,
                                    highlightthickness=0, padx=12, pady=6)
        self.explain_text.pack(fill=tk.X, padx=4, pady=(0, 8))
        self.explain_text.insert("1.0", "Configure seeds/peers and press Start.")
        self.explain_text.config(state=tk.DISABLED)

        # ── Event log ────────────────────────────────────────────────
        log_card = tk.Frame(right, bg=BG_CARD, bd=0)
        log_card.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))
        tk.Label(log_card, text="  EVENT LOG", font=("Consolas", 10, "bold"),
                 bg=BG_CARD, fg=FG_DIM).pack(anchor="w", padx=8, pady=(8, 0))
        log_inner = tk.Frame(log_card, bg=BG_CARD)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 8))
        self.log_text = tk.Text(log_inner, bg="#0d0d1a", fg=FG,
                                font=("Consolas", 9), wrap=tk.WORD, bd=0,
                                highlightthickness=0, padx=8, pady=4)
        sb = tk.Scrollbar(log_inner, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

        # Tag colors
        for widget in (self.log_text,):
            widget.tag_configure("seed",      foreground=SEED_FILL)
            widget.tag_configure("peer",      foreground=PEER_FILL)
            widget.tag_configure("consensus", foreground=VOTE_YES)
            widget.tag_configure("reject",    foreground=VOTE_NO)
            widget.tag_configure("gossip",    foreground=EDGE_GOSSIP)
            widget.tag_configure("dead",      foreground=PEER_DEAD)
            widget.tag_configure("edge",      foreground=FG_DIM)
            widget.tag_configure("info",      foreground=FG)
            widget.tag_configure("header",    foreground=ACCENT, font=("Consolas", 10, "bold"))

        # Legend
        legend = tk.Frame(self.root, bg="#16213e", height=30)
        legend.pack(fill=tk.X)
        legend.pack_propagate(False)
        for clr, lbl in [(SEED_FILL, "Seed"), (PEER_FILL, "Peer"),
                          (EDGE_GOSSIP, "Gossip"), (EDGE_PING, "Ping"),
                          (PEER_DEAD, "Dead"), (MSG_BUBBLE, "Message")]:
            tk.Label(legend, text="●", fg=clr, bg="#16213e",
                     font=("Consolas", 11)).pack(side=tk.LEFT, padx=(12, 1))
            tk.Label(legend, text=lbl, fg=FG_DIM, bg="#16213e",
                     font=("Consolas", 9)).pack(side=tk.LEFT, padx=(0, 6))

    # ── Helpers ──────────────────────────────────────────────────────

    def _delay(self, factor=1.0):
        """Return adjusted delay in ms based on speed slider."""
        return max(50, int(ANIM_MED * factor / self.speed_scale.get()))

    def _log(self, text, tag="info"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _explain(self, text):
        self.explain_text.config(state=tk.NORMAL)
        self.explain_text.delete("1.0", tk.END)
        self.explain_text.insert("1.0", text)
        self.explain_text.config(state=tk.DISABLED)

    def _set_phase(self, idx):
        self.sim.phase = idx
        name = self.PHASE_NAMES[min(idx, len(self.PHASE_NAMES)-1)]
        self.phase_label.config(text=f"Phase: {name}")

    # ── Node drawing helpers ─────────────────────────────────────────

    def _draw_seed(self, node: SimNode, highlight=False):
        c = self.canvas
        r = 24
        x, y = node.x, node.y
        stroke = "#ffffff" if highlight else SEED_STROKE
        w = 3 if highlight else 2
        pts = [x, y-r, x+r, y, x, y+r, x-r, y]
        pid = c.create_polygon(pts, fill=SEED_FILL, outline=stroke, width=w)
        tid = c.create_text(x, y, text=f"S{node.port - 5999}",
                            fill="#1e1e2e", font=("Consolas", 9, "bold"))
        lbl = c.create_text(x, y+r+14, text=f":{node.port}",
                            fill=FG_DIM, font=("Consolas", 8))
        node.canvas_ids = [pid, tid, lbl]
        return pid

    def _draw_peer(self, node: SimNode, highlight=False):
        c = self.canvas
        r = 22
        x, y = node.x, node.y
        if node.status == "dead":
            fill, stroke = PEER_DEAD, DEAD_STROKE
        elif node.status == "removed":
            fill, stroke = "#45475a", "#585b70"
        else:
            fill, stroke = PEER_FILL, PEER_STROKE
        if highlight:
            stroke = "#ffffff"
        w = 3 if highlight else 2
        oid = c.create_oval(x-r, y-r, x+r, y+r, fill=fill, outline=stroke, width=w)
        tid = c.create_text(x, y, text=f"P{node.port - 6999}",
                            fill="#1e1e2e", font=("Consolas", 9, "bold"))
        lbl = c.create_text(x, y+r+14, text=f":{node.port}",
                            fill=FG_DIM, font=("Consolas", 8))
        node.canvas_ids = [oid, tid, lbl]
        return oid

    def _draw_edge(self, edge: SimEdge, color=EDGE_NORMAL, width=1, dash=None):
        na = self.sim.seeds.get(edge.a) or self.sim.peers.get(edge.a)
        nb = self.sim.seeds.get(edge.b) or self.sim.peers.get(edge.b)
        if not na or not nb:
            return
        eid = self.canvas.create_line(na.x, na.y, nb.x, nb.y,
                                      fill=color, width=width, dash=dash)
        edge.canvas_id = eid
        # Keep edges below nodes
        self.canvas.tag_lower(eid)
        return eid

    def _redraw_all(self):
        self.canvas.delete("all")
        for e in self.sim.edges:
            self._draw_edge(e)
        for n in self.sim.seeds.values():
            self._draw_seed(n)
        for n in self.sim.peers.values():
            self._draw_peer(n)

    def _animate_message(self, src: SimNode, dst: SimNode, color=MSG_BUBBLE,
                         text="", callback=None):
        """Animate a small circle traveling from src to dst."""
        c = self.canvas
        r = 6
        ball = c.create_oval(src.x-r, src.y-r, src.x+r, src.y+r,
                             fill=color, outline="")
        lbl = None
        if text:
            lbl = c.create_text(src.x, src.y-14, text=text, fill=color,
                                font=("Consolas", 8, "bold"))
        steps = 25
        dx = (dst.x - src.x) / steps
        dy = (dst.y - src.y) / steps
        delay = max(15, int(30 / self.speed_scale.get()))

        def step(i):
            if i >= steps:
                c.delete(ball)
                if lbl:
                    c.delete(lbl)
                if callback:
                    callback()
                return
            c.move(ball, dx, dy)
            if lbl:
                c.move(lbl, dx, dy)
            self.root.after(delay, lambda: step(i+1))
        step(0)

    def _flash_edge(self, edge: SimEdge, color, duration=800, callback=None):
        """Temporarily color an edge, then revert."""
        if edge.canvas_id:
            self.canvas.itemconfigure(edge.canvas_id, fill=color, width=2.5)
        def revert():
            if edge.canvas_id:
                self.canvas.itemconfigure(edge.canvas_id, fill=EDGE_NORMAL, width=1)
            if callback:
                callback()
        self.root.after(int(duration / self.speed_scale.get()), revert)

    def _flash_node(self, node: SimNode, color, duration=600, callback=None):
        if node.canvas_ids:
            self.canvas.itemconfigure(node.canvas_ids[0], outline=color, width=4)
        def revert():
            stroke = SEED_STROKE if node.ntype == "seed" else PEER_STROKE
            if node.status == "dead":
                stroke = DEAD_STROKE
            if node.canvas_ids:
                self.canvas.itemconfigure(node.canvas_ids[0], outline=stroke, width=2)
            if callback:
                callback()
        self.root.after(int(duration / self.speed_scale.get()), revert)

    def _show_vote_badge(self, node: SimNode, vote: str, duration=800):
        """Show ✓ or ✗ near a node temporarily."""
        c = self.canvas
        txt = "✓" if vote == "YES" else "✗"
        clr = VOTE_YES if vote == "YES" else VOTE_NO
        tid = c.create_text(node.x + 28, node.y - 20, text=txt, fill=clr,
                            font=("Consolas", 16, "bold"))
        self.root.after(int(duration / self.speed_scale.get()),
                        lambda: c.delete(tid))

    # ── Layout ───────────────────────────────────────────────────────

    def _compute_layout(self):
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 550
        sim = self.sim

        # Seeds: horizontal row at top
        ns = sim.num_seeds
        spacing = min(110, (cw - 100) / max(ns, 1))
        sx = (cw - spacing * (ns - 1)) / 2
        for i, sid in enumerate(sorted(sim.seeds)):
            sim.seeds[sid].x = sx + i * spacing
            sim.seeds[sid].y = 70

        # Peers: circle
        np_ = sim.num_peers
        cx, cy = cw / 2, ch / 2 + 50
        r = min(cw, ch - 120) * 0.32
        for i, pid in enumerate(sorted(sim.peers)):
            angle = -math.pi / 2 + 2 * math.pi * i / np_
            sim.peers[pid].x = cx + r * math.cos(angle)
            sim.peers[pid].y = cy + r * math.sin(angle)

    # ── Animation queue ──────────────────────────────────────────────

    def _enqueue(self, fn, *args, delay_factor=1.0):
        self._anim_queue.append((fn, args, delay_factor))

    def _run_queue(self):
        if not self._anim_queue:
            self._anim_running = False
            self.next_btn.config(state=tk.NORMAL)
            return
        self._anim_running = True
        fn, args, df = self._anim_queue.pop(0)
        fn(*args)
        self.root.after(self._delay(df), self._run_queue)

    # ── Button handlers ──────────────────────────────────────────────

    def _on_start(self):
        try:
            ns = int(self.seed_var.get())
            np_ = int(self.peer_var.get())
            assert 1 <= ns <= 10 and 1 <= np_ <= 20
        except (ValueError, AssertionError):
            self._explain("Invalid input. Seeds: 1-10, Peers: 1-20.")
            return

        self.start_btn.config(state=tk.DISABLED)
        self.seed_entry.config(state=tk.DISABLED)
        self.peer_entry.config(state=tk.DISABLED)

        # Clear
        self.canvas.delete("all")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Create simulation
        self.sim = Simulation(ns, np_)
        for i in range(ns):
            port = 6000 + i
            nid = f"127.0.0.1:{port}"
            self.sim.seeds[nid] = SimNode(nid, "seed", port, 0, 0)
        for i in range(np_):
            port = 7000 + i
            nid = f"127.0.0.1:{port}"
            self.sim.peers[nid] = SimNode(nid, "peer", port, 0, 0)
        self._compute_layout()

        # Start phases
        self._run_phase1()

    def _on_next_phase(self):
        self.next_btn.config(state=tk.DISABLED)
        phase = self.sim.phase
        if phase == 1:
            self._run_phase2()
        elif phase == 2:
            self._run_phase3()
        elif phase == 3:
            self._run_phase4()
        elif phase == 4:
            self._run_phase5()
        elif phase == 5:
            self._run_phase6()
        elif phase >= 6:
            self._set_phase(7)
            self._explain("Simulation complete! Press Reset to try again.")
            self._log("\n════ SIMULATION COMPLETE ════", "header")
            self.reset_btn.config(state=tk.NORMAL)

    def _on_reset(self):
        self._anim_queue.clear()
        self._anim_running = False
        self.canvas.delete("all")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.sim = None
        self.start_btn.config(state=tk.NORMAL)
        self.seed_entry.config(state=tk.NORMAL)
        self.peer_entry.config(state=tk.NORMAL)
        self.next_btn.config(state=tk.DISABLED)
        self._set_phase(0)
        self._explain("Configure seeds/peers and press Start.")

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 1: Seed Initialization
    # ══════════════════════════════════════════════════════════════════

    def _run_phase1(self):
        self._set_phase(1)
        self._explain(
            "Phase 1 — Seed Initialization\n\n"
            "Seed nodes are the membership authority. They maintain the\n"
            "Peer List and use consensus (⌊n/2⌋+1 quorum) to approve\n"
            "changes. Seeds do NOT participate in gossip."
        )
        self._log("\n══ Phase 1: Seed Initialization ══", "header")
        self._log(f"  Quorum = ⌊{self.sim.num_seeds}/2⌋+1 = {self.sim.quorum}", "info")

        for i, (sid, node) in enumerate(sorted(self.sim.seeds.items())):
            self._enqueue(self._phase1_add_seed, node, i, delay_factor=1.2)

        self._enqueue(self._phase1_done, delay_factor=0.5)
        self._run_queue()

    def _phase1_add_seed(self, node, idx):
        self._draw_seed(node, highlight=True)
        self._log(f"  Seed S{idx+1} started on :{node.port}", "seed")
        self.root.after(self._delay(0.6),
                        lambda: self._flash_node(node, SEED_STROKE))

    def _phase1_done(self):
        self._log(f"  All {self.sim.num_seeds} seeds initialized.\n", "seed")
        self.next_btn.config(state=tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 2: Peer Registration with Consensus
    # ══════════════════════════════════════════════════════════════════

    def _run_phase2(self):
        self._set_phase(2)
        self._explain(
            "Phase 2 — Peer Registration\n\n"
            "Each peer sends REGISTER_REQUEST to a seed. That seed\n"
            "proposes the registration to all other seeds. Each seed\n"
            f"votes YES/NO. Quorum = {self.sim.quorum} votes needed."
        )
        self._log("══ Phase 2: Peer Registration ══", "header")

        peers = list(sorted(self.sim.peers.values(), key=lambda n: n.port))
        for peer in peers:
            self._enqueue(self._phase2_register_peer, peer, delay_factor=2.5)

        self._enqueue(self._phase2_done, delay_factor=0.5)
        self._run_queue()

    def _phase2_register_peer(self, peer: SimNode):
        # Draw the peer (faded first)
        self._draw_peer(peer)
        self._log(f"  Peer P{peer.port-6999} (:{peer.port}) → register request", "peer")

        # Pick a random seed to send the request to
        seed_list = list(self.sim.seeds.values())
        target_seed = random.choice(seed_list)

        self._explain(
            f"Peer :{peer.port} sends REGISTER_REQUEST to Seed :{target_seed.port}.\n"
            f"Seed :{target_seed.port} proposes to all other seeds and\n"
            f"collects votes. Need {self.sim.quorum}/{self.sim.num_seeds} YES votes."
        )

        # Animate message to seed
        def on_arrive_seed():
            self._flash_node(target_seed, "#ffffff")
            self._log(f"    → Seed S{target_seed.port-5999} proposes registration", "seed")
            # Votes from all seeds
            votes = 0
            for i, seed in enumerate(seed_list):
                vote = "YES"  # normally all approve
                votes += 1
                self._show_vote_badge(seed, vote, duration=1000)
                vstr = f"    Seed S{seed.port-5999} votes {vote}  ({votes}/{self.sim.quorum})"
                self._log(vstr, "consensus" if vote == "YES" else "reject")

            approved = votes >= self.sim.quorum
            if approved:
                self._log(f"    ✓ CONSENSUS APPROVED — {votes}/{self.sim.num_seeds} votes", "consensus")
                peer.registered = True
                self._flash_node(peer, VOTE_YES)
            else:
                self._log(f"    ✗ CONSENSUS REJECTED — {votes}/{self.sim.num_seeds} votes", "reject")

        self._animate_message(peer, target_seed, color=PEER_FILL,
                              text="REG", callback=on_arrive_seed)

    def _phase2_done(self):
        registered = sum(1 for p in self.sim.peers.values() if p.registered)
        self._log(f"  {registered}/{self.sim.num_peers} peers registered.\n", "peer")
        self.next_btn.config(state=tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 3: Power-Law Overlay Construction
    # ══════════════════════════════════════════════════════════════════

    def _run_phase3(self):
        self._set_phase(3)
        self._explain(
            "Phase 3 — Overlay Construction (Power-Law / Zipf)\n\n"
            "Each peer fetches the Peer List from seeds, then selects\n"
            "neighbors using Zipf-weighted random sampling:\n"
            "  weight(k) = 1/k^α  (α=1.0)\n"
            "This creates a power-law degree distribution."
        )
        self._log("══ Phase 3: Overlay Construction ══", "header")

        # Compute the actual overlay
        peer_list = list(sorted(self.sim.peers.values(), key=lambda n: n.port))
        n = len(peer_list)
        for peer in peer_list:
            others = [p for p in peer_list if p.nid != peer.nid]
            if not others:
                continue
            # Zipf weights
            weights = [1.0 / (k+1) for k in range(len(others))]
            total = sum(weights)
            probs = [w / total for w in weights]
            # Select floor(n/2)+1 or fewer neighbors
            num_nbrs = min(len(others), max(1, n // 2))
            chosen = random.choices(others, weights=probs, k=num_nbrs)
            chosen = list({c.nid: c for c in chosen}.values())[:num_nbrs]  # dedupe
            peer.neighbors = [c.nid for c in chosen]

            for nb in chosen:
                key = tuple(sorted([peer.nid, nb.nid]))
                if not any((e.a, e.b) == key or (e.b, e.a) == key for e in self.sim.edges):
                    self.sim.edges.append(SimEdge(key[0], key[1]))

        # Animate edges one by one
        for i, edge in enumerate(self.sim.edges):
            self._enqueue(self._phase3_add_edge, edge, i, delay_factor=0.6)

        self._enqueue(self._phase3_done, delay_factor=0.5)
        self._run_queue()

    def _phase3_add_edge(self, edge: SimEdge, idx):
        eid = self._draw_edge(edge, color=ACCENT, width=2)
        na = self.sim.peers.get(edge.a)
        nb = self.sim.peers.get(edge.b)
        pa = na.port - 6999 if na else "?"
        pb = nb.port - 6999 if nb else "?"
        self._log(f"  Edge {idx+1}: P{pa} ↔ P{pb}", "edge")
        # Fade to normal
        self.root.after(self._delay(0.4),
                        lambda: self.canvas.itemconfigure(eid, fill=EDGE_NORMAL, width=1) if eid else None)

    def _phase3_done(self):
        degrees = defaultdict(int)
        for e in self.sim.edges:
            degrees[e.a] += 1
            degrees[e.b] += 1
        deg_str = ", ".join(f"P{self.sim.peers[k].port-6999}={v}"
                           for k, v in sorted(degrees.items()) if k in self.sim.peers)
        self._log(f"  Overlay: {len(self.sim.edges)} edges.  Degrees: {deg_str}\n", "info")
        self._explain(
            "Overlay complete! Notice the power-law distribution:\n"
            "some peers have more edges (hubs) and some have fewer.\n"
            "This ensures efficient gossip propagation."
        )
        self.next_btn.config(state=tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 4: Gossip Dissemination
    # ══════════════════════════════════════════════════════════════════

    def _run_phase4(self):
        self._set_phase(4)
        self._explain(
            "Phase 4 — Gossip Dissemination\n\n"
            "Each peer generates messages (max 10). Format:\n"
            "  <timestamp>:<IP>:<Port>:<Msg#>\n"
            "On first receipt: store SHA-256 hash, forward to neighbors.\n"
            "Duplicates are silently dropped (hash already seen)."
        )
        self._log("══ Phase 4: Gossip Dissemination ══", "header")

        # Simulate a few rounds of gossip
        peer_list = [p for p in self.sim.peers.values() if p.status == "alive"]
        rounds = min(3, len(peer_list))
        for r in range(rounds):
            src = peer_list[r % len(peer_list)]
            self._enqueue(self._phase4_gossip_round, src, r+1, delay_factor=2.0)

        self._enqueue(self._phase4_done, delay_factor=0.5)
        self._run_queue()

    def _phase4_gossip_round(self, src: SimNode, msg_num):
        msg_id = f"msg#{msg_num}"
        src.gossip_count += 1
        src.gossip_seen.add(msg_id)
        self._log(f"  P{src.port-6999} generates gossip #{msg_num}", "gossip")
        self._flash_node(src, EDGE_GOSSIP)

        # Propagate to neighbors
        for nid in src.neighbors:
            nb = self.sim.peers.get(nid)
            if not nb or nb.status != "alive":
                continue
            # Find the edge
            edge = None
            for e in self.sim.edges:
                if (e.a == src.nid and e.b == nid) or (e.b == src.nid and e.a == nid):
                    edge = e
                    break
            if edge and edge.canvas_id:
                self.canvas.itemconfigure(edge.canvas_id, fill=EDGE_GOSSIP, width=2.5)

            is_dup = msg_id in nb.gossip_seen
            if is_dup:
                self._log(f"    → P{nb.port-6999}: DUPLICATE (hash seen, dropped)", "edge")
            else:
                nb.gossip_seen.add(msg_id)
                self._log(f"    → P{nb.port-6999}: NEW gossip received, forwarding…", "gossip")

            def make_revert(e):
                return lambda: (self.canvas.itemconfigure(e.canvas_id, fill=EDGE_NORMAL, width=1)
                                if e.canvas_id else None)
            if edge:
                self.root.after(self._delay(1.0), make_revert(edge))

    def _phase4_done(self):
        total = sum(p.gossip_count for p in self.sim.peers.values())
        self._log(f"  Gossip demo complete. {total} messages generated.\n", "gossip")
        self._explain(
            "Gossip spreads through the overlay. Each peer stores the\n"
            "SHA-256 hash of every message it has seen. If a duplicate\n"
            "arrives (same hash), it is silently dropped — no loops!\n"
            "Each peer generates at most 10 messages."
        )
        self.next_btn.config(state=tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 5: Failure Detection
    # ══════════════════════════════════════════════════════════════════

    def _run_phase5(self):
        self._set_phase(5)

        # Pick a victim (last peer)
        peer_list = sorted(self.sim.peers.values(), key=lambda n: n.port)
        self._victim = peer_list[-1] if peer_list else None
        if not self._victim:
            self.next_btn.config(state=tk.NORMAL)
            return

        self._explain(
            f"Phase 5 — Failure Detection\n\n"
            f"Peer P{self._victim.port-6999} (:{self._victim.port}) crashes!\n"
            f"Neighbors detect the failure via PING timeouts.\n"
            f"They query each other (SUSPECT_QUERY) and if a majority\n"
            f"confirms → the node is declared dead."
        )
        self._log("══ Phase 5: Failure Detection ══", "header")

        self._enqueue(self._phase5_kill, delay_factor=1.5)
        self._enqueue(self._phase5_pings, delay_factor=2.5)
        self._enqueue(self._phase5_consensus, delay_factor=2.0)
        self._enqueue(self._phase5_done, delay_factor=0.5)
        self._run_queue()

    def _phase5_kill(self):
        v = self._victim
        v.status = "dead"
        self._log(f"  ✗ P{v.port-6999} CRASHED!", "dead")
        # Redraw as dead
        for cid in v.canvas_ids:
            self.canvas.delete(cid)
        self._draw_peer(v)
        self._flash_node(v, PEER_DEAD, duration=1200)

    def _phase5_pings(self):
        v = self._victim
        # Find neighbors of the victim
        detectors = []
        for pid, peer in self.sim.peers.items():
            if peer.status != "alive":
                continue
            if v.nid in peer.neighbors:
                detectors.append(peer)

        if not detectors:
            # Pick any alive peer
            for peer in self.sim.peers.values():
                if peer.status == "alive":
                    detectors.append(peer)
                    break

        self._explain(
            f"Neighbors of P{v.port-6999} send PING messages.\n"
            f"No PONG reply → mark as SUSPECTED.\n"
            f"They then ask other neighbors: SUSPECT_QUERY.\n"
            f"Majority confirms → dead."
        )

        for det in detectors[:3]:
            self._log(f"  P{det.port-6999} → PING → P{v.port-6999} … TIMEOUT!", "dead")
            self._log(f"  P{det.port-6999}: P{v.port-6999} is now SUSPECTED", "dead")
            # Animate ping
            edge = None
            for e in self.sim.edges:
                if (e.a == det.nid and e.b == v.nid) or (e.b == det.nid and e.a == v.nid):
                    edge = e; break
            if edge and edge.canvas_id:
                self.canvas.itemconfigure(edge.canvas_id, fill=EDGE_PING, width=2, dash=(4, 4))

        self._detectors = detectors

    def _phase5_consensus(self):
        v = self._victim
        # Peer-level consensus
        confirm = len(self._detectors[:3])
        total_check = max(confirm, 2)
        self._log(f"  Peer-level consensus: {confirm}/{total_check} neighbors confirm dead", "dead")
        if confirm > total_check // 2:
            self._log(f"  ✓ Majority confirms — P{v.port-6999} declared DEAD", "dead")
        else:
            self._log(f"  Not enough confirmations yet", "info")

    def _phase5_done(self):
        self._log("", "info")
        self.next_btn.config(state=tk.NORMAL)

    # ══════════════════════════════════════════════════════════════════
    #  PHASE 6: Dead-Node Removal (Seed Consensus)
    # ══════════════════════════════════════════════════════════════════

    def _run_phase6(self):
        self._set_phase(6)
        v = self._victim
        if not v:
            self._set_phase(7)
            self.next_btn.config(state=tk.NORMAL)
            return

        self._explain(
            f"Phase 6 — Seed-Level Consensus for Removal\n\n"
            f"A peer sends DEAD_NODE_REPORT to all seeds.\n"
            f"Format: Dead Node:{v.nid}:<ts>:<reporter.IP>\n"
            f"Seeds propose removal and vote. Need {self.sim.quorum} YES."
        )
        self._log("══ Phase 6: Dead-Node Removal ══", "header")

        # Pick a reporting peer
        reporter = None
        for peer in self.sim.peers.values():
            if peer.status == "alive":
                reporter = peer
                break

        if reporter:
            self._enqueue(self._phase6_report, reporter, delay_factor=2.0)
        self._enqueue(self._phase6_seed_vote, delay_factor=2.5)
        self._enqueue(self._phase6_remove, delay_factor=1.5)
        self._enqueue(self._phase6_done, delay_factor=0.5)
        self._run_queue()

    def _phase6_report(self, reporter: SimNode):
        v = self._victim
        self._log(f"  P{reporter.port-6999} sends DEAD_NODE_REPORT to all seeds", "dead")
        self._log(f"    \"Dead Node:{v.nid}:ts:{reporter.nid.split(':')[0]}\"", "dead")

        seed_list = list(self.sim.seeds.values())
        for seed in seed_list:
            self._animate_message(reporter, seed, color=PEER_DEAD, text="DEAD")

    def _phase6_seed_vote(self):
        v = self._victim
        seed_list = list(self.sim.seeds.values())
        proposer = seed_list[0]
        self._log(f"  Seed S{proposer.port-5999} proposes removal of P{v.port-6999}", "seed")

        votes = 0
        for seed in seed_list:
            vote = "YES"
            votes += 1
            self._show_vote_badge(seed, vote, duration=1200)
            self._log(f"    S{seed.port-5999} votes {vote}  ({votes}/{self.sim.quorum})", "consensus")

        self._log(f"  ✓ SEED CONSENSUS APPROVED — Remove P{v.port-6999}  [{votes}/{self.sim.num_seeds}]", "consensus")

    def _phase6_remove(self):
        v = self._victim
        v.status = "removed"
        # Remove edges
        self.sim.edges = [e for e in self.sim.edges if e.a != v.nid and e.b != v.nid]
        self._redraw_all()
        self._log(f"  CONFIRMED REMOVAL: P{v.port-6999} removed from Peer List", "dead")

        for peer in self.sim.peers.values():
            if v.nid in peer.neighbors:
                peer.neighbors.remove(v.nid)

    def _phase6_done(self):
        alive = sum(1 for p in self.sim.peers.values() if p.status == "alive")
        self._log(f"  Network now has {alive} active peers.\n", "info")
        self._explain(
            "The dead peer has been removed via two-level consensus:\n"
            "  1. Peer-level: neighbors confirmed via PING timeout\n"
            "  2. Seed-level: ⌊n/2⌋+1 seeds voted YES on removal\n\n"
            "No single node can unilaterally change membership!"
        )
        self.next_btn.config(state=tk.NORMAL)

    # ── Run ──────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = VisualizerApp()
    app.run()
