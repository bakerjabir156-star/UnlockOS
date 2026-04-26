"""
AutoUnlocker_NoAPI.py — Refactored Master Script (Queue-Aware)
This module is called by engine.py. Instead of printing directly,
all output is sent through a queue.Queue as structured event dicts.

Event structure:
    {
        "level":   "INFO" | "SUCCESS" | "WARNING" | "ERROR",
        "stage":   "DETECTION" | "EXPLOIT" | "BYPASS" | "PROXY" | "FINALIZE",
        "message": str,
        "device":  dict | None   (model, sn, version, chipset, platform)
    }
"""

import os
import subprocess
import queue
import time
from typing import Optional
from config import BUILD_DIR, MDM_TOOL, MEOW_TOOL, MTK_MODULE


def _emit(q: queue.Queue, level: str, stage: str, message: str, device: Optional[dict] = None):
    """Push a structured log event onto the shared queue."""
    q.put_nowait({
        "level": level,
        "stage": stage,
        "message": message,
        "device": device,
    })


def _run_action(q: queue.Queue, name: str, command: str, stage: str, device: dict) -> bool:
    """Execute a shell command and stream its stdout/stderr into the queue."""
    _emit(q, "INFO", stage, f"▶ Executing: {name}", device)
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                _emit(q, "INFO", stage, f"  {line}", device)
        proc.wait()
        if proc.returncode == 0:
            _emit(q, "SUCCESS", stage, f"✅ {name} — completed successfully", device)
            return True
        else:
            _emit(q, "ERROR", stage, f"❌ {name} — exited with code {proc.returncode}", device)
            return False
    except Exception as e:
        _emit(q, "ERROR", stage, f"❌ {name} — exception: {e}", device)
        return False


# ---------------------------------------------------------------------------
# Device Detection
# ---------------------------------------------------------------------------

def get_ios_device_info(q: queue.Queue) -> Optional[dict]:
    """Query ideviceinfo for connected iOS device. Returns None if not found."""
    try:
        def query(key):
            return subprocess.check_output(
                ["ideviceinfo", "-k", key],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()

        model = query("ProductType")
        version = query("ProductVersion")
        sn = query("SerialNumber")
        cpu = query("CPUArchitecture")
        return {
            "platform": "ios",
            "model": model,
            "version": version,
            "serial": sn,
            "chipset": cpu,
            "connection": "local",
        }
    except Exception:
        return None


def get_android_device_info(q: queue.Queue) -> Optional[dict]:
    """Detect MediaTek or Qualcomm Android devices via lsusb."""
    try:
        lsusb = subprocess.check_output(["lsusb"], stderr=subprocess.DEVNULL, timeout=5).decode()
        if "0e8d:" in lsusb:    # MediaTek VID
            return {"platform": "android", "chipset": "MediaTek", "model": "Android/MTK",
                    "serial": "N/A", "version": "N/A", "connection": "local"}
        if "05c6:" in lsusb:    # Qualcomm VID
            return {"platform": "android", "chipset": "Qualcomm/EDL", "model": "Android/QC",
                    "serial": "N/A", "version": "N/A", "connection": "local"}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# iOS Unlock Pipelines
# ---------------------------------------------------------------------------

def pipeline_checkm8(q: queue.Queue, device: dict) -> bool:
    """Full unlock pipeline for A11 and older (Checkm8-vulnerable)."""
    _emit(q, "INFO", "EXPLOIT", "🔓 Checkm8 pipeline selected (A11/iPhone 8–X)", device)

    ok = _run_action(q, "Gaster Pwn — DFU exploit", "gaster pwn", "EXPLOIT", device)
    if not ok:
        return False

    ok = _run_action(q, "Palera1n — Jailbreak", "palera1n -c --force-revert", "EXPLOIT", device)
    if not ok:
        return False

    _run_action(q, "SSH Mount via palera1n", "palera1n --boot-args serial=3", "BYPASS", device)

    ok = _run_action(
        q, "iCloud Bypass — Meow Activator",
        f"python3 {MEOW_TOOL}", "BYPASS", device
    )

    _run_action(q, "Reboot device", "idevicediagnostics restart", "FINALIZE", device)
    return ok


def pipeline_mdm_bypass(q: queue.Queue, device: dict) -> bool:
    """MDM / Activation bypass for A12+ devices (no exploit)."""
    _emit(q, "INFO", "BYPASS", "🛡️ MDM Bypass pipeline selected (A12+)", device)

    # Pair first
    _run_action(q, "iDevice Pair", "idevicepair pair", "BYPASS", device)

    ok = _run_action(
        q, "MDM Patcher Enhanced",
        f"python3 {MDM_TOOL} --bypass --no-api",
        "BYPASS", device
    )

    # Profile cleanup
    _run_action(
        q, "Profile cleanup + restart",
        "idevicepair pair && idevicediagnostics restart",
        "FINALIZE", device
    )
    return ok


def pipeline_proxy_hijack(q: queue.Queue, device: dict,
                          proxy_starter=None) -> bool:
    """
    Fully automated Proxy Activation Hijack pipeline for A12+ devices.

    Steps (all automatic, zero manual interaction):
      1. Auto-start mitmdump in background (via proxy_starter callback or directly)
      2. Install mitmproxy CA cert on the device via USB
      3. Configure device HTTP proxy via idevicesetproxy (USB — no Wi-Fi setup needed)
      4. Poll activation state until success or timeout
    """
    import threading

    _emit(q, "INFO", "PROXY", "🌐 Pipeline Proxy Activation Hijack — démarrage automatique", device)

    # ── Étape 1 : Démarrer mitmdump automatiquement ──────────────────────────
    _emit(q, "INFO", "PROXY", "⏳ Démarrage automatique du serveur proxy (mitmdump)...", device)

    proxy_started = False
    if proxy_starter is not None:
        # Called from engine.py which manages the subprocess
        proxy_started = proxy_starter()
    else:
        # Standalone fallback: start mitmdump directly
        import os, sys
        script_dir = os.path.dirname(os.path.abspath(__file__))
        hijack_script = os.path.join(script_dir, "activation_hijack.py")
        try:
            _proxy_proc = subprocess.Popen(
                ["mitmdump", "-s", hijack_script, "-p", "8080",
                 "--quiet", "--no-http2", "--ssl-insecure"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)  # Give mitmdump time to bind
            proxy_started = _proxy_proc.poll() is None
            if proxy_started:
                _emit(q, "SUCCESS", "PROXY",
                      f"✅ mitmdump démarré (PID {_proxy_proc.pid}, port 8080)", device)
            else:
                _emit(q, "WARNING", "PROXY",
                      "⚠  mitmdump s'est arrêté immédiatement — vérifiez l'installation", device)
        except FileNotFoundError:
            _emit(q, "ERROR", "PROXY",
                  "❌ mitmdump introuvable — installez: pip3 install mitmproxy", device)

    if not proxy_started:
        _emit(q, "WARNING", "PROXY",
              "⚠  Proxy non démarré — poursuite avec configuration USB uniquement", device)

    # ── Étape 2 : Installer le certificat mitmproxy CA sur l'appareil ────────
    # mitmproxy génère son CA dans ~/.mitmproxy/mitmproxy-ca-cert.pem
    import os, platform
    cert_path = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

    if os.path.exists(cert_path):
        _emit(q, "INFO", "PROXY",
              "🔐 Installation automatique du certificat CA mitmproxy sur l'appareil...", device)

        # ideviceinstaller peut installer des profils .mobileconfig
        # On crée un profil de confiance minimal autour du cert PEM
        profile_path = "/tmp/unlockos_ca_profile.mobileconfig"
        _generate_mobileconfig_profile(cert_path, profile_path)

        ok_cert = _run_action(
            q, "Install CA cert profile (mobileconfig)",
            f"ideviceinstaller -i {profile_path}",
            "PROXY", device
        )
        if not ok_cert:
            _emit(q, "WARNING", "PROXY",
                  "⚠  Cert auto-install échoué — l'iPhone devra valider manuellement "
                  "ou utiliser SSL stripping", device)
    else:
        # Certificat pas encore généré — mitmdump le crée au 1er run
        _emit(q, "INFO", "PROXY",
              "ℹ  Certificat CA non trouvé (~/.mitmproxy/) — "
              "mitmdump le créera au premier démarrage.", device)

    # ── Étape 3 : Configurer le proxy via USB (idevicesetproxy) ─────────────
    # Pas besoin de configurer le Wi-Fi manuellement — USB suffit
    _emit(q, "INFO", "PROXY",
          "🔌 Configuration automatique du proxy via USB (idevicesetproxy)...", device)

    ok_proxy = _run_action(
        q, "Configurer proxy HTTP → 127.0.0.1:8080 (via USB)",
        "idevicesetproxy 127.0.0.1 8080",
        "PROXY", device
    )

    if ok_proxy:
        _emit(q, "SUCCESS", "PROXY",
              "✅ Proxy configuré automatiquement — aucune action Wi-Fi requise sur l'iPhone", device)
    else:
        _emit(q, "WARNING", "PROXY",
              "⚠  idevicesetproxy indisponible — "
              "configurez manuellement: Réglages → Wi-Fi → Proxy → 127.0.0.1:8080", device)

    # ── Étape 4 : Attendre l'activation (polling automatique) ────────────────
    _emit(q, "INFO", "PROXY",
          "⏳ Attente de l'activation automatique (timeout: 120s)...", device)

    max_wait = 120  # secondes
    poll_interval = 5
    elapsed = 0
    activation_confirmed = False

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        # Vérifier le statut d'activation via ideviceinfo
        try:
            act_state = subprocess.check_output(
                ["ideviceinfo", "-k", "ActivationState"],
                stderr=subprocess.DEVNULL, timeout=5
            ).decode().strip()

            _emit(q, "INFO", "PROXY",
                  f"  ActivationState: {act_state} ({elapsed}s/{max_wait}s)", device)

            if act_state in ("Activated", "MobileActivated"):
                activation_confirmed = True
                break
        except Exception:
            _emit(q, "INFO", "PROXY",
                  f"  Attente activation... ({elapsed}s/{max_wait}s)", device)

    if activation_confirmed:
        _emit(q, "SUCCESS", "PROXY",
              "✅ iPhone activé avec succès via le proxy d'interception !", device)
        # Nettoyer le proxy après activation réussie
        subprocess.run(["idevicesetproxy", "--disable"],
                       capture_output=True, timeout=5)
        return True
    else:
        _emit(q, "WARNING", "PROXY",
              f"⚠  Timeout ({max_wait}s) — activation non confirmée. "
              "Le proxy continue de tourner en arrière-plan.", device)
        return False


def _generate_mobileconfig_profile(cert_pem_path: str, output_path: str) -> None:
    """
    Génère un profil .mobileconfig Apple permettant d'installer
    le certificat CA mitmproxy comme autorité de confiance sur iOS.
    """
    import base64, uuid
    with open(cert_pem_path, "rb") as f:
        cert_data = f.read()

    # Extraire uniquement le bloc DER en base64
    cert_b64_lines = []
    inside = False
    for line in cert_data.decode().splitlines():
        if "BEGIN CERTIFICATE" in line:
            inside = True
            continue
        if "END CERTIFICATE" in line:
            break
        if inside:
            cert_b64_lines.append(line.strip())
    cert_b64 = "".join(cert_b64_lines)

    profile_uuid = str(uuid.uuid4()).upper()
    payload_uuid = str(uuid.uuid4()).upper()

    mobileconfig = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadCertificateFileName</key>
      <string>UnlockOS-MitmProxy-CA.cer</string>
      <key>PayloadContent</key>
      <data>{cert_b64}</data>
      <key>PayloadDescription</key>
      <string>UnlockOS Proxy CA Certificate</string>
      <key>PayloadDisplayName</key>
      <string>UnlockOS Proxy CA</string>
      <key>PayloadIdentifier</key>
      <string>com.unlockos.proxy.ca.{payload_uuid}</string>
      <key>PayloadType</key>
      <string>com.apple.security.root</string>
      <key>PayloadUUID</key>
      <string>{payload_uuid}</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>PayloadDescription</key>
  <string>Certificat proxy UnlockOS pour interception activation</string>
  <key>PayloadDisplayName</key>
  <string>UnlockOS Proxy CA</string>
  <key>PayloadIdentifier</key>
  <string>com.unlockos.proxy.{profile_uuid}</string>
  <key>PayloadRemovalDisallowed</key>
  <false/>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>{profile_uuid}</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>"""

    with open(output_path, "w") as f:
        f.write(mobileconfig)


# ---------------------------------------------------------------------------
# Android Unlock Pipelines
# ---------------------------------------------------------------------------

def pipeline_mtk_unlock(q: queue.Queue, device: dict) -> bool:
    """Bypass AUTH + unlock bootloader for MediaTek devices."""
    _emit(q, "INFO", "EXPLOIT", "🤖 MTKClient pipeline selected (MediaTek)", device)

    ok = _run_action(
        q, "MTK Auth Bypass",
        f"python3 -m {MTK_MODULE} payload-bypass", "EXPLOIT", device
    )
    if not ok:
        return False

    ok = _run_action(
        q, "MTK Bootloader Unlock",
        f"python3 -m {MTK_MODULE} stage2 unlock", "BYPASS", device
    )
    return ok


def pipeline_edl_unlock(q: queue.Queue, device: dict) -> bool:
    """Qualcomm EDL unlock via bkerler/edl."""
    _emit(q, "INFO", "EXPLOIT", "🤖 EDL pipeline selected (Qualcomm)", device)
    ok = _run_action(q, "EDL Unlock", "python3 -m edl reset", "EXPLOIT", device)
    return ok


# ---------------------------------------------------------------------------
# Backup / Ticket Saving
# ---------------------------------------------------------------------------

def save_activation_tickets(q: queue.Queue, device: dict) -> None:
    """Backup activation tickets for future reference."""
    backup_dir = os.path.join(os.path.expanduser("~"), "UnlockOS_Backups",
                              device.get("serial", "unknown"))
    os.makedirs(backup_dir, exist_ok=True)
    _run_action(
        q, "Save Activation Tickets",
        f"idevicebackup2 backup --full {backup_dir}",
        "FINALIZE", device
    )


# ---------------------------------------------------------------------------
# Main Decision Router
# ---------------------------------------------------------------------------

def run_unlock_pipeline(q: queue.Queue, device: dict) -> tuple[bool, str]:
    """
    Select and execute the appropriate unlock pipeline.
    Returns (success: bool, method_name: str)
    """
    platform = device.get("platform", "ios")

    if platform == "android":
        chipset = device.get("chipset", "")
        if "MediaTek" in chipset:
            return pipeline_mtk_unlock(q, device), "mtk_unlock"
        elif "Qualcomm" in chipset:
            return pipeline_edl_unlock(q, device), "edl_unlock"
        else:
            _emit(q, "WARNING", "DETECTION",
                  f"⚠  Unknown Android chipset: {chipset}. No pipeline available.", device)
            return False, "unknown"

    # iOS routing
    model = device.get("model", "")
    _emit(q, "INFO", "DETECTION",
          f"📱 iOS device identified: {model} | iOS {device.get('version','?')} | "
          f"CPU: {device.get('chipset','?')}", device)

    # A11 and below → Checkm8
    CHECKM8_MODELS = [
        "iPhone10,",   # iPhone 8, 8 Plus, X
        "iPhone9,",    # iPhone 7, 7 Plus
        "iPhone8,",    # iPhone 6s, 6s Plus, SE1
        "iPad",        # many iPads
    ]
    if any(m in model for m in CHECKM8_MODELS):
        _emit(q, "INFO", "DETECTION",
              "🎯 Checkm8-vulnerable device confirmed (A11 or older)", device)
        return pipeline_checkm8(q, device), "checkm8"

    # A12+ → MDM Bypass first, proxy as fallback
    _emit(q, "INFO", "DETECTION",
          "🔍 A12+ device. Attempting MDM Bypass (No-API)...", device)
    success = pipeline_mdm_bypass(q, device)
    if success:
        return True, "mdm_bypass"

    _emit(q, "WARNING", "BYPASS",
          "⚠  MDM Bypass insufficient. Escalating to Proxy Activation Hijack...", device)
    success = pipeline_proxy_hijack(q, device)
    return success, "proxy_hijack"
