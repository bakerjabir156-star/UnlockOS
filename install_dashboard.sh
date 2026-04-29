#!/bin/bash
# install_dashboard.sh — UnlockOS Dashboard One-Shot Installer
# Run as root on Debian/Ubuntu inside the ISO (or Cubic chroot)
set -e

INSTALL_DIR="/opt/unlockos/dashboard"
SERVICE_FILE="/etc/systemd/system/unlockos.service"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   UnlockOS Dashboard — Installation Automatique  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. Dépendances système ─────────────────────────────────
echo "[1/5] Installation des dépendances système..."
apt update -qq
apt install -y python3 python3-pip usbmuxd libimobiledevice-utils \
               mitmproxy curl git build-essential

# ── 2. Dépendances Python ─────────────────────────────────
echo "[2/5] Installation des dépendances Python..."
pip3 install flask --quiet

# ── 3. Copie des fichiers du dashboard ────────────────────
echo "[3/5] Déploiement des fichiers dans ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp -r ./* "${INSTALL_DIR}/"
chmod +x "${INSTALL_DIR}/update_tools.sh" 2>/dev/null || true

# ── 4. Service systemd ────────────────────────────────────
echo "[4/5] Configuration du service systemd..."
cp unlockos.service "${SERVICE_FILE}"
# Update path in service file to match install dir
sed -i "s|/opt/unlockos/dashboard|${INSTALL_DIR}|g" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable unlockos.service

# ── 5. Règles udev (accès USB sans sudo) ─────────────────
echo "[5/5] Configuration des règles udev..."
cat > /etc/udev/rules.d/99-unlockos.rules << 'EOF'
# Apple devices
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", MODE="0666", GROUP="plugdev"
# MediaTek devices
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", MODE="0666", GROUP="plugdev"
# Qualcomm EDL
SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", MODE="0666", GROUP="plugdev"
EOF
udevadm control --reload-rules && udevadm trigger

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✅ Installation terminée !                       ║"
echo "║                                                  ║"
echo "║  Dashboard: http://localhost:5000                ║"
echo "║  Logs:      journalctl -u unlockos -f            ║"
echo "║  Démarrer:  systemctl start unlockos             ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
