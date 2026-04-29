"""
engine.py — UnlockOS Pipeline Engine
Manages the device detection loop, pipeline FSM, SSE event broadcasting,
proxy subprocess, and SQLite history writes.

Runs entirely in background threads — Flask never blocks.
"""

import queue
import threading
import time
import subprocess
import random
import json
from datetime import datetime
from typing import Optional

import db
from config import (
    SIMULATE_MODE,
    DEVICE_POLL_INTERVAL,
    POST_UNLOCK_COOLDOWN,
    MAX_LOG_QUEUE_SIZE,
    SIM_DEVICE_APPEAR_DELAY,
    SIM_PIPELINE_STEP_DELAY,
    PROXY_PORT,
    ACTIVATION_HIJACK_SCRIPT,
    BASE_DIR,
    MDM_TOOL,
)
import AutoUnlocker_NoAPI as unlocker


# ===========================================================================
# Pipeline States
# ===========================================================================

class PipelineState:
    IDLE       = "IDLE"
    DETECTED   = "DETECTED"
    EXPLOITING = "EXPLOITING"
    BYPASSING  = "BYPASSING"
    PROXYING   = "PROXYING"
    FINALIZING = "FINALIZING"
    SUCCESS    = "SUCCESS"
    FAILED     = "FAILED"


PIPELINE_STAGES = [
    "DETECTION",
    "EXPLOIT",
    "BYPASS",
    "PROXY",
    "FINALIZE",
]

STAGE_DISPLAY = {
    "DETECTION": "Détection & Identification",
    "EXPLOIT":   "Exploitation (Checkm8 / MTK)",
    "BYPASS":    "Bypass MDM / iCloud",
    "PROXY":     "Proxy d'Activation",
    "FINALIZE":  "Finalisation & Reboot",
}


# ===========================================================================
# Global Engine State (shared between threads and Flask routes)
# ===========================================================================

_lock = threading.Lock()

# One queue per connected SSE client
_sse_clients: list[queue.Queue] = []

# Current active devices  {id: device_dict}
_devices: dict[str, dict] = {}

# Pipeline state per device
_pipeline: dict[str, dict] = {}

# Proxy subprocess
_proxy_proc: Optional[subprocess.Popen] = None

# Internal log queue (engine → SSE broadcaster)
_log_q: queue.Queue = queue.Queue(maxsize=2000)

# Engine stats
_stats = {"start_time": datetime.now().isoformat(), "total_processed": 0}


# ===========================================================================
# SSE Pub/Sub
# ===========================================================================

def subscribe() -> queue.Queue:
    """Register a new SSE client and return its dedicated queue."""
    q = queue.Queue(maxsize=MAX_LOG_QUEUE_SIZE)
    with _lock:
        _sse_clients.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    """Remove a disconnected SSE client's queue."""
    with _lock:
        try:
            _sse_clients.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict) -> None:
    """Fan-out a single event to all connected SSE clients."""
    payload = json.dumps(event)
    with _lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _emit(level: str, stage: str, message: str, device: Optional[dict] = None) -> None:
    """Create a structured log event and broadcast it + store in local queue."""
    event = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "stage": stage,
        "message": message,
        "device_id": device.get("id") if device else None,
    }
    _broadcast(event)
    try:
        _log_q.put_nowait(event)
    except queue.Full:
        pass


# ===========================================================================
# Pipeline Stage Tracker
# ===========================================================================

def _update_pipeline(device_id: str, stage: str, state: str, progress: int) -> None:
    """Update pipeline state for a device and broadcast a status event."""
    with _lock:
        if device_id not in _pipeline:
            _pipeline[device_id] = {}
        _pipeline[device_id].update({
            "stage": stage,
            "state": state,
            "progress": progress,
            "updated": datetime.now().isoformat(),
        })
    _broadcast({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "type": "pipeline_update",
        "device_id": device_id,
        "stage": stage,
        "state": state,
        "progress": progress,
    })


def _register_device(device: dict) -> None:
    """Add or update a device in the active device dict."""
    dev_id = device["id"]
    with _lock:
        _devices[dev_id] = device
    _broadcast({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "type": "device_update",
        "devices": list(_devices.values()),
    })


def _remove_device(device_id: str) -> None:
    """Remove a device and its pipeline state."""
    with _lock:
        _devices.pop(device_id, None)
        _pipeline.pop(device_id, None)
    _broadcast({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "type": "device_update",
        "devices": list(_devices.values()),
    })


# ===========================================================================
# Proxy Management
# ===========================================================================

def start_proxy() -> bool:
    """Start mitmproxy in the background. Returns True if started."""
    global _proxy_proc
    if _proxy_proc and _proxy_proc.poll() is None:
        _emit("INFO", "PROXY", "⚡ Proxy déjà actif (PID {})".format(_proxy_proc.pid))
        return True
    try:
        _proxy_proc = subprocess.Popen(
            ["mitmdump", "-s", ACTIVATION_HIJACK_SCRIPT, "-p", str(PROXY_PORT),
             "--quiet", "--no-http2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _emit("SUCCESS", "PROXY",
              f"🌐 Serveur Proxy démarré (port {PROXY_PORT}, PID {_proxy_proc.pid})")
        return True
    except FileNotFoundError:
        _emit("ERROR", "PROXY",
              "❌ mitmdump introuvable — installez mitmproxy: pip install mitmproxy")
        return False
    except Exception as e:
        _emit("ERROR", "PROXY", f"❌ Erreur démarrage proxy: {e}")
        return False


def stop_proxy() -> None:
    """Terminate the proxy subprocess if running."""
    global _proxy_proc
    if _proxy_proc and _proxy_proc.poll() is None:
        _proxy_proc.terminate()
        _emit("INFO", "PROXY", "🛑 Proxy d'activation arrêté.")
    _proxy_proc = None


def proxy_status() -> str:
    if _proxy_proc is None:
        return "STOPPED"
    return "RUNNING" if _proxy_proc.poll() is None else "CRASHED"


# ===========================================================================
# Queue Bridge (AutoUnlocker_NoAPI → SSE)
# ===========================================================================

def _queue_bridge(src_q: queue.Queue) -> None:
    """
    Relay events from AutoUnlocker_NoAPI's internal queue to SSE clients.
    Runs as a daemon thread during pipeline execution.
    """
    while True:
        try:
            event = src_q.get(timeout=0.5)
            _emit(
                event.get("level", "INFO"),
                event.get("stage", "BYPASS"),
                event.get("message", ""),
                event.get("device"),
            )
        except queue.Empty:
            break


# ===========================================================================
# Real Hardware Pipeline Runner
# ===========================================================================

def _run_device_pipeline(device: dict) -> None:
    """Execute the full unlock pipeline for a detected device (real hardware)."""
    dev_id = device["id"]
    _register_device(device)
    start_t = time.time()

    _emit("INFO", "DETECTION",
          f"📱 Appareil détecté: {device['model']} | SN: {device['serial']} | "
          f"iOS {device['version']}", device)
    _update_pipeline(dev_id, "DETECTION", PipelineState.DETECTED, 5)

    # Create a bridge queue that AutoUnlocker writes to
    bridge_q: queue.Queue = queue.Queue()

    _update_pipeline(dev_id, "EXPLOIT", PipelineState.EXPLOITING, 20)

    # Run in thread so bridge can drain concurrently
    result_holder = [False, "unknown"]

    def _run():
        ok, method = unlocker.run_unlock_pipeline(bridge_q, device)
        result_holder[0] = ok
        result_holder[1] = method

    pipeline_thread = threading.Thread(target=_run, daemon=True)
    pipeline_thread.start()

    # Bridge events while pipeline runs
    while pipeline_thread.is_alive():
        try:
            event = bridge_q.get(timeout=0.3)
            _emit(event.get("level", "INFO"), event.get("stage", "BYPASS"),
                  event.get("message", ""), event.get("device"))
            # Advance progress based on stage
            stage_progress = {
                "DETECTION": 10, "EXPLOIT": 40, "BYPASS": 65,
                "PROXY": 80, "FINALIZE": 90,
            }
            stg = event.get("stage", "BYPASS")
            _update_pipeline(dev_id, stg, PipelineState.BYPASSING,
                             stage_progress.get(stg, 50))
        except queue.Empty:
            pass

    # Drain remaining
    while not bridge_q.empty():
        try:
            event = bridge_q.get_nowait()
            _emit(event.get("level", "INFO"), event.get("stage", "BYPASS"),
                  event.get("message", ""), event.get("device"))
        except queue.Empty:
            break

    pipeline_thread.join()
    duration = round(time.time() - start_t, 1)
    ok, method = result_holder

    final_state = PipelineState.SUCCESS if ok else PipelineState.FAILED
    final_level = "SUCCESS" if ok else "ERROR"
    if method == "recovery_exit" and ok:
        final_msg = f"🔄 Sortie de Recovery RÉUSSIE — {device['model']} | Durée: {duration}s"
    else:
        final_msg = (
            f"✅ Déblocage RÉUSSI — {device['model']} | Méthode: {method} | Durée: {duration}s"
            if ok else
            f"❌ Déblocage ÉCHOUÉ — {device['model']} | Méthode: {method} | Durée: {duration}s"
        )
    _emit(final_level, "FINALIZE", final_msg, device)
    _update_pipeline(dev_id, "FINALIZE", final_state, 100)

    db.log_result(
        model=device["model"],
        serial_num=device["serial"],
        status="SUCCESS" if ok else "FAILED",
        method=method,
        ios_version=device.get("version", ""),
        chipset=device.get("chipset", ""),
        duration_s=duration,
    )

    with _lock:
        _stats["total_processed"] += 1

    time.sleep(POST_UNLOCK_COOLDOWN)
    _remove_device(dev_id)


# ===========================================================================
# Simulation Mode
# ===========================================================================

_SIM_DEVICES = [
    {"platform": "ios",     "model": "iPhone10,3", "version": "16.7.2",
     "serial": "F2LXQ9KZHG7X", "chipset": "A11",   "connection": "local"},
    {"platform": "ios",     "model": "iPhone14,5", "version": "17.4.1",
     "serial": "H4RKM2PLWQ1Y", "chipset": "A15",   "connection": "remote"},
    {"platform": "android", "model": "Android/MTK", "version": "Android 13",
     "serial": "MTKLB4920X",   "chipset": "MediaTek", "connection": "local"},
    {"platform": "ios",     "model": "iPhone12,1", "version": "17.2.0",
     "serial": "G7TRN3QLWK5Z", "chipset": "A13",   "connection": "remote"},
    {"platform": "ios",     "model": "iPhone9,1",  "version": "15.8.1",
     "serial": "C8PLQ7XMNR2J", "chipset": "A10",   "connection": "local"},
]

_SIM_LOG_LINES = {
    "checkm8": [
        ("INFO",    "DETECTION",  "📱 iPhone X (A11) détecté — vulnérable Checkm8"),
        ("INFO",    "EXPLOIT",    "Gaster: Detecting device in DFU mode..."),
        ("INFO",    "EXPLOIT",    "Gaster: Found: CPID:8015 CPRV:11 CPFM:03 SCEP:01"),
        ("INFO",    "EXPLOIT",    "Gaster: Sending exploit payload..."),
        ("SUCCESS", "EXPLOIT",    "✅ Checkm8 — DFU exploit successful! Device in pwned DFU"),
        ("INFO",    "BYPASS",     "Palera1n: Booting ramdisk..."),
        ("INFO",    "BYPASS",     "Palera1n: Mounting rootfs..."),
        ("INFO",    "BYPASS",     "Meow Activator: Patching activation records..."),
        ("SUCCESS", "BYPASS",     "✅ iCloud Bypass applied successfully"),
        ("INFO",    "FINALIZE",   "Sending reboot command via idevicediagnostics..."),
        ("SUCCESS", "FINALIZE",   "✅ Device rebooted — unlock complete"),
    ],
    "mdm_bypass": [
        ("INFO",    "DETECTION",  "📱 iPhone (A15+) détecté — aucun exploit hardware disponible"),
        ("INFO",    "BYPASS",     "Pairing device via idevicepair..."),
        ("INFO",    "BYPASS",     "MDMPatcher-Enhanced: Scanning MDM profiles..."),
        ("INFO",    "BYPASS",     "MDMPatcher-Enhanced: Found 1 supervision profile"),
        ("INFO",    "BYPASS",     "MDMPatcher-Enhanced: Attempting profile removal..."),
        ("INFO",    "BYPASS",     "MDMPatcher-Enhanced: Injecting bypass payload..."),
        ("SUCCESS", "BYPASS",     "✅ MDM Bypass applied — supervision removed"),
        ("INFO",    "FINALIZE",   "Cleaning up temporary files..."),
        ("SUCCESS", "FINALIZE",   "✅ Device unlocked via MDM bypass"),
    ],
    "proxy_hijack": [
        ("INFO",    "DETECTION",  "📱 iPhone (A13) détecté — MDM Bypass insuffisant"),
        ("INFO",    "BYPASS",     "Escalade vers Proxy Activation Hijack..."),
        ("INFO",    "PROXY",      "🌐 Configuration proxy: 127.0.0.1:8080"),
        ("INFO",    "PROXY",      "🤖 Démarrage de l'activation USB automatisée (Simulation O.MG)..."),
        ("INFO",    "PROXY",      "ideviceactivation: Sending activation request via 127.0.0.1:8080..."),
        ("INFO",    "PROXY",      "activation_hijack.py: Request intercepted → albert.apple.com"),
        ("INFO",    "PROXY",      "activation_hijack.py: Patching Plist response..."),
        ("SUCCESS", "PROXY",      "✅ [SUCCESSFULLY BYPASSED] Activation hijack complete"),
        ("SUCCESS", "PROXY",      "✅ iPhone activé avec succès via USB (Payload injecté) !"),
        ("SUCCESS", "FINALIZE",   "✅ iPhone activated via proxy — lock screen bypassed"),
    ],
    "mtk_unlock": [
        ("INFO",    "DETECTION",  "🤖 Android MTK détecté (VID: 0e8d)"),
        ("INFO",    "EXPLOIT",    "MTKClient: Connecting in BROM mode..."),
        ("INFO",    "EXPLOIT",    "MTKClient: Sending auth bypass payload..."),
        ("SUCCESS", "EXPLOIT",    "✅ AUTH bypass successful — BROM access granted"),
        ("INFO",    "BYPASS",     "MTKClient: Sending Stage2 payload..."),
        ("INFO",    "BYPASS",     "MTKClient: Unlocking bootloader..."),
        ("SUCCESS", "BYPASS",     "✅ Bootloader unlocked successfully"),
        ("SUCCESS", "FINALIZE",   "✅ Android device fully unlocked"),
    ],
}


def _sim_pipeline(device: dict) -> None:
    """Simulate a full unlock pipeline with realistic timing and log events."""
    dev_id = device["id"]
    _register_device(device)

    # Pick the right log script
    model = device.get("model", "")
    platform = device.get("platform", "ios")
    chipset = device.get("chipset", "")

    if platform == "android":
        script_key = "mtk_unlock"
        method = "mtk_unlock"
    elif "iPhone10," in model or "iPhone9," in model or "iPhone8," in model:
        script_key = "checkm8"
        method = "checkm8"
    elif "iPhone14," in model or "iPhone15," in model or "iPhone16," in model:
        # 50% chance: MDM or Proxy
        if random.random() > 0.5:
            script_key = "proxy_hijack"
            method = "proxy_hijack"
        else:
            script_key = "mdm_bypass"
            method = "mdm_bypass"
    else:
        script_key = "mdm_bypass"
        method = "mdm_bypass"

    start_t = time.time()
    _emit("INFO", "DETECTION",
          f"📡 [SIM] Appareil simulé: {device['model']} | SN: {device['serial']} | "
          f"Connection: {device['connection']}", device)
    _update_pipeline(dev_id, "DETECTION", PipelineState.DETECTED, 5)
    time.sleep(SIM_PIPELINE_STEP_DELAY * 0.5)

    log_lines = _SIM_LOG_LINES.get(script_key, [])
    stage_progress = {"DETECTION": 10, "EXPLOIT": 40, "BYPASS": 65, "PROXY": 80, "FINALIZE": 95}
    current_progress = 10

    for i, (level, stage, message) in enumerate(log_lines):
        _emit(level, stage, f"[SIM] {message}", device)
        progress = stage_progress.get(stage, current_progress)
        current_progress = max(current_progress, progress)
        _update_pipeline(dev_id, stage, PipelineState.BYPASSING, current_progress)
        time.sleep(SIM_PIPELINE_STEP_DELAY * (0.6 + random.random() * 0.8))

    # Simulate occasional failure
    success = random.random() > 0.15
    duration = round(time.time() - start_t, 1)

    final_state = PipelineState.SUCCESS if success else PipelineState.FAILED
    final_level = "SUCCESS" if success else "ERROR"
    final_msg = (
        f"✅ [SIM] Déblocage RÉUSSI — {device['model']} | Méthode: {method} | Durée: {duration}s"
        if success else
        f"❌ [SIM] Déblocage ÉCHOUÉ — {device['model']} | Méthode: {method} | Durée: {duration}s"
    )
    _emit(final_level, "FINALIZE", final_msg, device)
    _update_pipeline(dev_id, "FINALIZE", final_state, 100)

    db.log_result(
        model=device["model"],
        serial_num=device["serial"],
        status="SUCCESS" if success else "FAILED",
        method=method,
        ios_version=device.get("version", ""),
        chipset=device.get("chipset", ""),
        duration_s=duration,
        notes="[SIMULATION]",
    )

    with _lock:
        _stats["total_processed"] += 1

    time.sleep(POST_UNLOCK_COOLDOWN * 0.5)
    _remove_device(dev_id)


# ===========================================================================
# Detection Loops
# ===========================================================================

_active_device_ids: set = set()
_pipeline_threads: dict[str, threading.Thread] = {}


def _detection_loop_sim() -> None:
    """Simulation detection loop — cycles through fake devices."""
    _emit("INFO", "DETECTION",
          "🟡 [SIMULATION MODE] Démarrage du serveur UnlockOS en mode simulateur...")
    _emit("INFO", "DETECTION",
          "ℹ  Aucun appareil réel requis. Les appareils simulés apparaîtront dans quelques secondes.")

    time.sleep(SIM_DEVICE_APPEAR_DELAY)

    device_index = 0
    while True:
        device_data = _SIM_DEVICES[device_index % len(_SIM_DEVICES)]
        dev_id = f"sim_{device_index}"
        device = {**device_data, "id": dev_id}

        _emit("INFO", "DETECTION",
              f"🔌 [SIM] Appareil branché: {device['model']} ({device['connection']})")

        t = threading.Thread(target=_sim_pipeline, args=(device,), daemon=True)
        _pipeline_threads[dev_id] = t
        t.start()
        t.join()  # Wait for this device to finish before next

        device_index += 1
        # Brief idle between devices
        idle = POST_UNLOCK_COOLDOWN * 0.3
        _emit("INFO", "DETECTION",
              f"⏳ Prochaine simulation dans {idle:.0f}s...")
        time.sleep(idle)


def _detection_loop_real() -> None:
    """Real hardware detection loop."""
    _emit("INFO", "DETECTION", "🟢 UnlockOS en ligne — En attente d'appareil (USB ou VirtualHere)...")

    processed_serials: set = set()

    while True:
        # Try iOS first
        device = unlocker.get_ios_device_info(_log_q)
        if device:
            sn = device.get("serial", "unknown")
            dev_id = f"ios_{sn}"
            device["id"] = dev_id
            if dev_id not in _active_device_ids:
                _active_device_ids.add(dev_id)
                t = threading.Thread(target=_run_device_pipeline, args=(device,), daemon=True)
                t.start()
                def _cleanup(did=dev_id, thread=t):
                    thread.join()
                    # Attendre un peu avant de permettre la ré-identification du même SN
                    time.sleep(2)
                    _active_device_ids.discard(did)
                threading.Thread(target=_cleanup, daemon=True).start()
        else:
            # Si aucun iOS n'est détecté, on s'assure que les IDs actifs sont bien nettoyés si débranchés physiquement
            # (Note: le cleanup de thread s'en occupe déjà, mais on peut forcer ici si besoin)
            pass

        # Try Android
        adevice = unlocker.get_android_device_info(_log_q)
        if adevice:
            dev_id = f"android_{adevice['chipset']}"
            adevice["id"] = dev_id
            if dev_id not in _active_device_ids:
                _active_device_ids.add(dev_id)
                t = threading.Thread(target=_run_device_pipeline, args=(adevice,), daemon=True)
                t.start()
                def _cleanup_a(did=dev_id, thread=t):
                    thread.join()
                    time.sleep(2)
                    _active_device_ids.discard(did)
                threading.Thread(target=_cleanup_a, daemon=True).start()

        time.sleep(DEVICE_POLL_INTERVAL)


# ===========================================================================
# Manual Action Handlers (called from Flask /api/action)
# ===========================================================================

def action_force_mdm_bypass() -> str:
    if SIMULATE_MODE:
        _emit("INFO", "BYPASS", "🛡️ [SIM] Forçage MDM Bypass déclenché manuellement...")
        time.sleep(0.5)
        _emit("SUCCESS", "BYPASS", "✅ [SIM] MDM Bypass manuel appliqué")
        return "MDM Bypass simulé avec succès"
    _emit("INFO", "BYPASS", "🛡️ Forçage MDM Bypass déclenché manuellement...")
    result = subprocess.run(
        ["python3", MDM_TOOL, "--bypass", "--force"],
        capture_output=True, text=True,
    )
    _emit("INFO", "BYPASS", f"MDM Bypass manuel: {result.stdout.strip() or 'Exécuté'}")
    return "MDM Bypass déclenché"


def action_start_proxy() -> str:
    ok = start_proxy()
    return "Proxy démarré" if ok else "Erreur démarrage proxy (voir logs)"


def action_stop_proxy() -> str:
    stop_proxy()
    return "Proxy arrêté"


def action_save_tickets() -> str:
    if SIMULATE_MODE:
        _emit("INFO", "FINALIZE", "💾 [SIM] Sauvegarde des tickets d'activation...")
        time.sleep(0.5)
        _emit("SUCCESS", "FINALIZE", "✅ [SIM] Tickets sauvegardés dans ~/UnlockOS_Backups/")
        return "Tickets simulés sauvegardés"
    _emit("INFO", "FINALIZE", "💾 Sauvegarde manuelle des tickets en cours...")
    devices = get_devices()
    if not devices:
        _emit("WARNING", "FINALIZE", "⚠ Aucun appareil détecté pour la sauvegarde.")
        return "Échec : Aucun appareil"
    
    # Save for the first device
    device = devices[0]
    threading.Thread(target=unlocker.save_activation_tickets, args=(_log_q, device), daemon=True).start()
    return f"Sauvegarde lancée pour {device['model']}"


def action_reinstall_libs() -> str:
    if SIMULATE_MODE:
        _emit("INFO", "DETECTION", "📦 [SIM] Réinstallation des librairies manquantes demandée...")
        time.sleep(1)
        _emit("SUCCESS", "DETECTION", "✅ [SIM] Librairies simulées comme réinstallées avec succès.")
        return "Librairies réinstallées (Simulation)"
    
    _emit("INFO", "DETECTION", "📦 Lancement du script de réinstallation des librairies...")
    try:
        import os
        script_path = os.path.join(BASE_DIR, "update_tools.sh")
        subprocess.Popen(["bash", script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _emit("ERROR", "DETECTION", f"❌ Échec de la réinstallation : {e}")
        return "Erreur lors de la réinstallation"
    
    return "Réinstallation des librairies lancée en arrière-plan"


def action_emergency_stop() -> str:
    _emit("WARNING", "DETECTION", "🛑 ARRÊT D'URGENCE — Pipeline interrompu par l'opérateur")
    stop_proxy()
    return "Arrêt d'urgence exécuté"


# ===========================================================================
# Public Accessors (for Flask routes)
# ===========================================================================

def get_devices() -> list[dict]:
    with _lock:
        return list(_devices.values())


def get_pipeline_state() -> dict:
    with _lock:
        return dict(_pipeline)


def get_stats() -> dict:
    with _lock:
        s = dict(_stats)
    s["uptime_s"] = round(
        (datetime.now() - datetime.fromisoformat(s["start_time"])).total_seconds()
    )
    s["proxy_status"] = proxy_status()
    s["simulate_mode"] = SIMULATE_MODE
    db_stats = db.get_stats()
    s.update(db_stats)
    return s


def get_recent_logs(n: int = 50) -> list[dict]:
    """Return recent log events from the internal queue (snapshot)."""
    items = []
    tmp = []
    while not _log_q.empty() and len(tmp) < 500:
        try:
            tmp.append(_log_q.get_nowait())
        except queue.Empty:
            break
    for item in tmp:
        _log_q.put_nowait(item)
    return tmp[-n:]


# ===========================================================================
# Engine Bootstrap
# ===========================================================================

def start_engine() -> None:
    """Initialize DB and start background detection thread."""
    db.init_db()
    _emit("INFO", "DETECTION",
          "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    _emit("INFO", "DETECTION", "  🔓 UnlockOS Dashboard — Serveur d'Automatisation")
    _emit("INFO", "DETECTION", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    target = _detection_loop_sim if SIMULATE_MODE else _detection_loop_real
    t = threading.Thread(target=target, daemon=True, name="DetectionLoop")
    t.start()
    _emit("INFO", "DETECTION", f"✅ Moteur de détection démarré (thread: {t.name})")
