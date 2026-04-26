"""
config.py — UnlockOS Dashboard Configuration
Central configuration for all paths, ports, and runtime flags.
"""

import os
import argparse

# ---------------------------------------------------------------------------
# Parse CLI flags (--simulate enables Windows dev-mode with fake devices)
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--simulate", action="store_true",
                     help="Run in simulation mode (no real hardware required)")
_parser.add_argument("--port", type=int, default=5000,
                     help="Dashboard HTTP port (default: 5000)")
_parser.add_argument("--host", default="0.0.0.0",
                     help="Dashboard bind address (default: 0.0.0.0)")
_args, _ = _parser.parse_known_args()

# ---------------------------------------------------------------------------
# Runtime Flags
# ---------------------------------------------------------------------------
SIMULATE_MODE: bool = _args.simulate
FLASK_HOST: str = _args.host
FLASK_PORT: int = _args.port
FLASK_DEBUG: bool = False

# ---------------------------------------------------------------------------
# Filesystem Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.expanduser("~/UnlockOS_Build")
MDM_TOOL = os.path.join(BUILD_DIR, "MDMPatcher-Enhanced", "mdm_patcher.py")
MEOW_TOOL = os.path.join(BUILD_DIR, "Meow-Activator", "meow.py")
MTK_MODULE = "mtkclient"
DB_PATH = os.path.join(BASE_DIR, "unlockos_history.db")
LOG_FILE = os.path.join(BASE_DIR, "unlockos.log")

# ---------------------------------------------------------------------------
# Proxy Settings (mitmproxy)
# ---------------------------------------------------------------------------
PROXY_PORT = 8080
ACTIVATION_HIJACK_SCRIPT = os.path.join(BASE_DIR, "activation_hijack.py")

# ---------------------------------------------------------------------------
# Engine Settings
# ---------------------------------------------------------------------------
DEVICE_POLL_INTERVAL = 2        # seconds between device scans
POST_UNLOCK_COOLDOWN = 15       # seconds to wait after unlock before re-scanning
MAX_LOG_QUEUE_SIZE = 500        # max events buffered per SSE client
REMOTE_LATENCY_THRESHOLD = 150  # ms — above this, remote link is "degraded"

# ---------------------------------------------------------------------------
# Simulation Settings (only active when --simulate is passed)
# ---------------------------------------------------------------------------
SIM_DEVICE_APPEAR_DELAY = 4     # seconds before first fake device appears
SIM_PIPELINE_STEP_DELAY = 2.5   # seconds per pipeline stage in simulation
