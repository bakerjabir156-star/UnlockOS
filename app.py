"""
app.py — UnlockOS Flask Web Server
Serves the dashboard and exposes REST + SSE endpoints.

Usage:
    python app.py                  # Real hardware mode
    python app.py --simulate       # Simulation mode (Windows dev)
    python app.py --simulate --port 8000
"""

import sys
import io
import json

# Force UTF-8 stdout so emoji in log lines don't crash on Windows (cp1252)
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import time
import queue
from datetime import datetime
from flask import Flask, Response, render_template, jsonify, request, abort

import engine
import db
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, SIMULATE_MODE

# ---------------------------------------------------------------------------
# App Init
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# ===========================================================================
# Dashboard Page
# ===========================================================================

@app.route("/")
def index():
    return render_template("index.html",
                           simulate=SIMULATE_MODE,
                           version="1.0.0")


# ===========================================================================
# SSE — Real-time Log Stream
# ===========================================================================

@app.route("/api/stream")
def sse_stream():
    """
    Server-Sent Events endpoint.
    The client connects once; events are pushed as they happen.
    """
    client_q = engine.subscribe()

    def generate():
        # Send a hello event so the client knows the stream is live
        yield _sse_event("connected", {
            "message": "SSE stream connected",
            "simulate": SIMULATE_MODE,
            "ts": datetime.now().strftime("%H:%M:%S"),
        })
        try:
            while True:
                try:
                    payload = client_q.get(timeout=25)   # 25s heartbeat interval
                    yield _sse_event("log", json.loads(payload))
                except queue.Empty:
                    # Keep-alive heartbeat
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            engine.unsubscribe(client_q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# ===========================================================================
# REST API
# ===========================================================================

@app.route("/api/devices")
def api_devices():
    """Return currently connected devices."""
    return jsonify({
        "devices": engine.get_devices(),
        "count": len(engine.get_devices()),
    })


@app.route("/api/status")
def api_status():
    """Return engine stats + pipeline state."""
    return jsonify({
        "stats": engine.get_stats(),
        "pipeline": engine.get_pipeline_state(),
        "stages": engine.STAGE_DISPLAY,
    })


@app.route("/api/history")
def api_history():
    """Return unlock history from SQLite."""
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = db.get_history(limit=limit)
    stats = db.get_stats()
    return jsonify({"history": rows, "stats": stats})


@app.route("/api/logs")
def api_logs():
    """Return recent log snapshot (for page reload / initial load)."""
    n = min(int(request.args.get("n", 50)), 200)
    return jsonify({"logs": engine.get_recent_logs(n)})


@app.route("/api/action", methods=["POST"])
def api_action():
    """
    Manual action trigger.
    Body: {"action": "force_mdm_bypass" | "start_proxy" | "stop_proxy" | "save_tickets" | "emergency_stop"}
    """
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action", "")

    dispatch = {
        "force_mdm_bypass": engine.action_force_mdm_bypass,
        "start_proxy":      engine.action_start_proxy,
        "stop_proxy":       engine.action_stop_proxy,
        "save_tickets":     engine.action_save_tickets,
        "emergency_stop":   engine.action_emergency_stop,
    }

    handler = dispatch.get(action)
    if not handler:
        abort(400, description=f"Unknown action: '{action}'")

    import threading
    # Run action in background so HTTP response returns immediately
    result_holder = [None]

    def _run():
        result_holder[0] = handler()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=5)

    return jsonify({
        "status": "ok",
        "action": action,
        "result": result_holder[0] or "dispatched",
    })


@app.route("/api/latency")
def api_latency():
    """
    Ping latency for remote VirtualHere connections.
    In simulation mode returns a fake value.
    In real mode, attempts a TCP connect to common VirtualHere ports.
    """
    if SIMULATE_MODE:
        import random
        # Simulate variable latency (good + occasional spike)
        devices = engine.get_devices()
        results = []
        for d in devices:
            if d.get("connection") == "remote":
                latency = random.randint(8, 45) if random.random() > 0.1 else random.randint(120, 400)
                results.append({"id": d["id"], "model": d["model"], "latency_ms": latency})
        return jsonify({"latency": results})

    # Real mode — basic approach using subprocess ping
    import subprocess
    devices = engine.get_devices()
    results = []
    for d in devices:
        if d.get("connection") == "remote" and d.get("host"):
            try:
                t0 = time.monotonic()
                subprocess.run(["ping", "-c", "1", "-W", "1", d["host"]],
                               capture_output=True, timeout=2)
                ms = round((time.monotonic() - t0) * 1000)
                results.append({"id": d["id"], "model": d["model"], "latency_ms": ms})
            except Exception:
                results.append({"id": d["id"], "model": d["model"], "latency_ms": -1})
    return jsonify({"latency": results})


# ===========================================================================
# Error Handlers
# ===========================================================================

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


# ===========================================================================
# Entrypoint
# ===========================================================================

if __name__ == "__main__":
    engine.start_engine()
    mode_label = "[SIMULATION]" if SIMULATE_MODE else "[HARDWARE REEL]"
    print(f"\n{'='*55}")
    print(f"  UnlockOS Dashboard  {mode_label}")
    print(f"  URL: http://{FLASK_HOST}:{FLASK_PORT}")
    print(f"{'='*55}\n")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG,
            threaded=True, use_reloader=False)
