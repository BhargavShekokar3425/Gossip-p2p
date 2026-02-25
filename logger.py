"""
Logging utilities for the Gossip P2P Network.

Provides structured, colored console logging AND writes all significant
events to the shared outputfile.txt as required by the assignment spec.
"""

import copy
import logging
import os
import re
import sys
import threading
from datetime import datetime

# ─── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Each experiment gets a unique timestamped log file under logs/
_SESSION_TS = datetime.now().strftime('%Y%m%d_%H%M%S')
OUTPUT_FILE = os.path.join(LOGS_DIR, f"output_{_SESSION_TS}.log")

# Also keep outputfile.txt in the project root (spec requirement)
ROOT_OUTPUT_FILE = os.path.join(SCRIPT_DIR, "outputfile.txt")

# Thread-safe lock for writing to the shared output files
_output_lock = threading.Lock()


# ─── Colors for terminal output ──────────────────────────────────────────────
COLORS = {
    'RESET':   '\033[0m',
    'RED':     '\033[91m',
    'GREEN':   '\033[92m',
    'YELLOW':  '\033[93m',
    'BLUE':    '\033[94m',
    'MAGENTA': '\033[95m',
    'CYAN':    '\033[96m',
    'WHITE':   '\033[97m',
}


class ColorFormatter(logging.Formatter):
    """Formatter that adds color codes based on log level."""
    LEVEL_COLORS = {
        logging.DEBUG:    COLORS['CYAN'],
        logging.INFO:     COLORS['GREEN'],
        logging.WARNING:  COLORS['YELLOW'],
        logging.ERROR:    COLORS['RED'],
        logging.CRITICAL: COLORS['MAGENTA'],
    }

    def format(self, record):
        rec = copy.copy(record)
        color = self.LEVEL_COLORS.get(rec.levelno, COLORS['WHITE'])
        reset = COLORS['RESET']
        rec.msg = f"{color}{rec.msg}{reset}"
        return super().format(rec)


class OutputFileHandler(logging.Handler):
    """
    Handler that appends every INFO-or-above message to the shared
    outputfile.txt.  Uses a process-wide lock so multiple threads
    within the same process cannot interleave writes.
    """

    def __init__(self, *filepaths: str):
        super().__init__(level=logging.INFO)
        self._filepaths = filepaths

    def emit(self, record):
        try:
            msg = self.format(record)
            with _output_lock:
                for fp in self._filepaths:
                    with open(fp, 'a') as f:
                        f.write(msg + '\n')
        except Exception:
            self.handleError(record)


def get_logger(name: str, node_type: str = "node", port: int = 0) -> logging.Logger:
    """
    Create a logger that writes to:
      1. Console  (colored, INFO+)
      2. logs/output_YYYYMMDD_HHMMSS.log  (per-experiment)
      3. outputfile.txt  (root-level, spec requirement)

    Args:
        name:      Logger name
        node_type: 'seed' or 'peer'
        port:      Port number for identification
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # Already configured

    fmt_str = f'[%(asctime)s] [{node_type.upper()}:{port}] %(levelname)s - %(message)s'
    datefmt = '%Y-%m-%d %H:%M:%S'

    # ── Console handler (colored) ─────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(ColorFormatter(fmt=fmt_str, datefmt=datefmt))

    # ── Shared file handler (writes to both logs/ and root outputfile.txt) ─
    output_handler = OutputFileHandler(OUTPUT_FILE, ROOT_OUTPUT_FILE)
    output_handler.setFormatter(logging.Formatter(fmt=fmt_str, datefmt=datefmt))

    logger.addHandler(console_handler)
    logger.addHandler(output_handler)

    # ── Write session header into log files ───────────────────────────────
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    banner = (
        f"\n{'='*60}\n"
        f"  SESSION START — {now}\n"
        f"  Node: {node_type.upper()}:{port}\n"
        f"{'='*60}"
    )
    with _output_lock:
        for fp in (OUTPUT_FILE, ROOT_OUTPUT_FILE):
            with open(fp, 'a') as f:
                f.write(banner + '\n')

    return logger
