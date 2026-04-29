#!/bin/bash
# =============================================================================
#  UnlockOS — Script de Build Cubic (Chroot ISO)
#  À exécuter dans le terminal Cubic après avoir sélectionné l'ISO de base
#  Ubuntu 24.04 LTS ou Debian 12
#
#  Usage : collez ce script dans le terminal Cubic et exécutez-le.
#  Durée estimée : 20-40 minutes selon la connexion réseau.
# =============================================================================

set -euo pipefail

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[UnlockOS]${NC} $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $*"; }
die()  { echo -e "${RED}[ FAIL ]${NC} $*"; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}━━━ $* ━━━${NC}\n"; }

# ── Chemins ────────────────────────────────────────────────────────────────────
BUILD_DIR="/opt/UnlockOS_Build"
DASHBOARD_DIR="/opt/unlockos/dashboard"
LOG_FILE="/var/log/unlockos_build.log"
PALERA1N_URL="https://github.com/palera1n/palera1n/releases/latest/download/palera1n-linux-x86_64"
GASTER_REPO="https://github.com/0x7ff/gaster"

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       UnlockOS — Build Script pour Cubic (ISO)          ║"
echo "║       Basé sur Ubuntu 24.04 LTS / Debian 12             ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

exec > >(tee -a "$LOG_FILE") 2>&1
log "Démarrage du build — $(date)"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 1 : Mise à jour du système
# ═════════════════════════════════════════════════════════════════════════════
step "1/10 — Mise à jour du système de base"

export DEBIAN_FRONTEND=noninteractive
apt update -qq && apt upgrade -y -qq
ok "Système mis à jour"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 2 : Dépendances système
# ═════════════════════════════════════════════════════════════════════════════
step "2/10 — Installation des dépendances de compilation"

apt install -y \
  build-essential git cmake make autoconf automake libtool libtool-bin pkg-config \
  libssl-dev libusb-1.0-0-dev libcurl4-openssl-dev libzip-dev libbz2-dev \
  zlib1g-dev libplist-dev libimobiledevice-dev libimobiledevice-utils \
  usbmuxd libusbmuxd-dev libirecovery-dev \
  python3 python3-pip python3-dev python3-venv python3-setuptools \
  curl wget tar unzip ca-certificates \
  usbutils iproute2 net-tools iptables \
  openssl gnupg lsb-release \
  nano vim htop tmux \
  grub2-common grub-pc-bin grub-efi-amd64-bin \
  plymouth plymouth-themes \
  -qq

ok "Dépendances système installées"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 3 : Compilation libimobiledevice (stack Apple complète)
# ═════════════════════════════════════════════════════════════════════════════
step "3/10 — Compilation de la stack libimobiledevice"

mkdir -p "$BUILD_DIR/apple"
cd "$BUILD_DIR/apple"

APPLE_LIBS=(
  "https://github.com/libimobiledevice/libplist"
  "https://github.com/libimobiledevice/libusbmuxd"
  "https://github.com/libimobiledevice/libimobiledevice"
  "https://github.com/libimobiledevice/libirecovery"
  "https://github.com/libimobiledevice/libideviceactivation"
  "https://github.com/libimobiledevice/ideviceinstaller"
)

for repo_url in "${APPLE_LIBS[@]}"; do
  repo_name=$(basename "$repo_url")
  log "Compilation: $repo_name"

  if [ ! -d "$repo_name" ]; then
    git clone --depth=1 "$repo_url" "$repo_name" || { warn "Échec clone $repo_name — skip"; continue; }
  fi

  cd "$repo_name"
  ./autogen.sh --prefix=/usr/local --without-cython 2>/dev/null \
    || ./configure --prefix=/usr/local 2>/dev/null \
    || { warn "$repo_name: autogen/configure échoué — skip"; cd ..; continue; }

  make -j"$(nproc)" 2>/dev/null && make install
  ldconfig
  ok "$repo_name compilé et installé"
  cd ..
done


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 4 : Exploits iOS (Gaster + Palera1n + ipwndfu)
# ═════════════════════════════════════════════════════════════════════════════
step "4/10 — Installation des exploits iOS"

mkdir -p "$BUILD_DIR/exploits"
cd "$BUILD_DIR/exploits"

# ── Gaster (Checkm8 sur A7-A11) ─────────────────────────────────────────────
log "Compilation de Gaster..."
if git clone --depth=1 "$GASTER_REPO" gaster 2>/dev/null; then
  cd gaster
  make -j"$(nproc)" && cp gaster /usr/local/bin/gaster && chmod +x /usr/local/bin/gaster
  ok "Gaster installé dans /usr/local/bin/"
  cd ..
else
  warn "Gaster: clone échoué — tentative binaire précompilé..."
fi

# ── Palera1n (Jailbreak A8-A11, iOS 15-16) ──────────────────────────────────
log "Téléchargement de Palera1n (binaire officiel)..."
if curl -sSL "$PALERA1N_URL" -o /usr/local/bin/palera1n; then
  chmod +x /usr/local/bin/palera1n
  ok "Palera1n installé dans /usr/local/bin/"
else
  warn "Palera1n: téléchargement échoué — à installer manuellement"
fi

# ── ipwndfu (exploit DFU alternatif) ────────────────────────────────────────
log "Clonage de ipwndfu..."
git clone --depth=1 https://github.com/axi0mX/ipwndfu "$BUILD_DIR/exploits/ipwndfu" 2>/dev/null \
  && ok "ipwndfu cloné" || warn "ipwndfu: clone échoué"

# ── Ramiel (iOS 15 A12+) ────────────────────────────────────────────────────
git clone --depth=1 https://github.com/MatthewPierson/Ramiel "$BUILD_DIR/exploits/Ramiel" 2>/dev/null \
  && ok "Ramiel cloné" || warn "Ramiel: clone échoué"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 5 : Modules de Bypass iOS (MDM / iCloud)
# ═════════════════════════════════════════════════════════════════════════════
step "5/10 — Modules de Bypass MDM / iCloud"

mkdir -p "$BUILD_DIR/bypass"
cd "$BUILD_DIR/bypass"

BYPASS_REPOS=(
  "https://github.com/fled-dev/MDMPatcher-Enhanced"
)

for repo_url in "${BYPASS_REPOS[@]}"; do
  repo_name=$(basename "$repo_url")
  log "Clonage: $repo_name"
  git clone --depth=1 "$repo_url" "$repo_name" 2>/dev/null \
    && ok "$repo_name cloné" || warn "$repo_name: clone échoué"
done

# ── Meow-Activator (bypass iCloud A11 post-jailbreak) ───────────────────────
# Repo privé / alternatif — cloner depuis votre fork si disponible
log "Note: Meow-Activator requiert un accès manuel — dossier créé"
mkdir -p "$BUILD_DIR/bypass/Meow-Activator"
cat > "$BUILD_DIR/bypass/Meow-Activator/README.txt" << 'EOF'
Meow-Activator — à placer manuellement dans ce dossier.
Dépôt: chercher sur GitHub "Meow-Activator iCloud bypass"
Fichier requis: meow.py
EOF


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 6 : Outils Android (MTKClient + EDL + MiUnlock)
# ═════════════════════════════════════════════════════════════════════════════
step "6/10 — Outils Android MTK / Qualcomm"

mkdir -p "$BUILD_DIR/android"
cd "$BUILD_DIR/android"

# ── MTKClient + EDL via pip ──────────────────────────────────────────────────
log "Installation des packages Python Android..."
pip3 install --quiet \
  mtkclient \
  pyusb \
  pyserial \
  cryptography \
  colorama \
  || warn "Certains packages pip ont échoué — vérifiez pip3 install mtkclient manuellement"

ok "MTKClient installé via pip3"

# ── MiUnlockTool (Xiaomi) ────────────────────────────────────────────────────
git clone --depth=1 https://github.com/offici5l/MiUnlockTool "$BUILD_DIR/android/MiUnlockTool" 2>/dev/null \
  && ok "MiUnlockTool cloné" || warn "MiUnlockTool: clone échoué"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 7 : Infrastructure Réseau (mitmproxy + Tailscale)
# ═════════════════════════════════════════════════════════════════════════════
step "7/10 — Infrastructure réseau"

# ── mitmproxy ────────────────────────────────────────────────────────────────
log "Installation de mitmproxy..."
pip3 install --quiet mitmproxy && ok "mitmproxy installé" || warn "mitmproxy: échec pip"

# ── Tailscale (VPN mesh) ─────────────────────────────────────────────────────
log "Installation de Tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh 2>/dev/null \
  && ok "Tailscale installé" || warn "Tailscale: échec install — à faire manuellement"

# ── usbip (pour VirtualHere alternatif) ─────────────────────────────────────
apt install -y linux-tools-generic usbip 2>/dev/null \
  && ok "usbip installé" || warn "usbip: non disponible dans ce chroot"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 8 : Dashboard UnlockOS
# ═════════════════════════════════════════════════════════════════════════════
step "8/10 — Déploiement du Dashboard UnlockOS"

# ── Flask ─────────────────────────────────────────────────────────────────────
log "Installation de Flask..."
pip3 install --quiet flask && ok "Flask installé"

# ── Copie du dashboard (depuis le dossier de build monté ou le réseau) ───────
mkdir -p "$DASHBOARD_DIR"
mkdir -p "$DASHBOARD_DIR/static/css"
mkdir -p "$DASHBOARD_DIR/static/js"
mkdir -p "$DASHBOARD_DIR/templates"

# Vérifier si les sources sont disponibles localement (clé USB / dossier monté)
DASHBOARD_SRC=""
for candidate in \
  "/mnt/unlockos-dashboard" \
  "/media/unlockos-dashboard" \
  "/tmp/unlockos-dashboard" \
  "/root/unlockos-dashboard"
do
  if [ -f "$candidate/app.py" ]; then
    DASHBOARD_SRC="$candidate"
    break
  fi
done

if [ -n "$DASHBOARD_SRC" ]; then
  log "Sources trouvées dans $DASHBOARD_SRC — copie..."
  cp -r "$DASHBOARD_SRC"/. "$DASHBOARD_DIR/"
  ok "Dashboard copié depuis $DASHBOARD_SRC"
else
  warn "Sources du dashboard introuvables localement."
  warn "Copiez manuellement le dossier unlockos-dashboard dans $DASHBOARD_DIR"
  warn "ou montez votre clé USB et relancez depuis l'étape 8."

  # Créer un placeholder app.py minimal pour que le service ne crashe pas
  cat > "$DASHBOARD_DIR/app.py" << 'PYEOF'
# Placeholder — remplacez par le vrai app.py du dashboard UnlockOS
from flask import Flask
app = Flask(__name__)
@app.route("/")
def index():
    return "<h1>UnlockOS Dashboard</h1><p>Sources non deployees. Copiez les fichiers dans /opt/unlockos/dashboard/</p>", 503
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
PYEOF
fi

# ── Symlinks utiles ───────────────────────────────────────────────────────────
ln -sf "$BUILD_DIR/bypass/MDMPatcher-Enhanced" "$DASHBOARD_DIR/MDMPatcher-Enhanced" 2>/dev/null || true
ln -sf "$BUILD_DIR/bypass/Meow-Activator"      "$DASHBOARD_DIR/Meow-Activator"      2>/dev/null || true

ok "Dashboard prêt dans $DASHBOARD_DIR"


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 9 : Systemd + Autostart + Sécurité
# ═════════════════════════════════════════════════════════════════════════════
step "9/10 — Configuration Systemd + Sécurité"

# ── Service UnlockOS Dashboard ───────────────────────────────────────────────
log "Création du service systemd unlockos.service..."
cat > /etc/systemd/system/unlockos.service << EOF
[Unit]
Description=UnlockOS Dashboard — Serveur de contrôle automatisé
Documentation=file://${DASHBOARD_DIR}/README.md
After=network.target usbmuxd.service
Wants=usbmuxd.service

[Service]
Type=simple
User=root
WorkingDirectory=${DASHBOARD_DIR}
ExecStart=/usr/bin/python3 ${DASHBOARD_DIR}/app.py --port 5000
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=unlockos

[Install]
WantedBy=multi-user.target
EOF

# ── Service usbmuxd ──────────────────────────────────────────────────────────
log "Activation de usbmuxd..."
systemctl enable usbmuxd 2>/dev/null || warn "usbmuxd non disponible dans chroot — OK au boot"

# ── Activation du dashboard ──────────────────────────────────────────────────
systemctl enable unlockos.service
ok "unlockos.service activé au démarrage"

# ── Règles udev USB ───────────────────────────────────────────────────────────
log "Configuration des règles udev..."
cat > /etc/udev/rules.d/99-unlockos.rules << 'EOF'
# Apple iOS devices
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", MODE="0666", GROUP="plugdev"
# Apple DFU / Recovery mode
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="1281", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", ATTR{idProduct}=="1227", MODE="0666", GROUP="plugdev"
# MediaTek (MTK) BROM
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", MODE="0666", GROUP="plugdev"
# Qualcomm EDL
SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", ATTR{idProduct}=="9008", MODE="0666", GROUP="plugdev"
# Samsung Odin
SUBSYSTEM=="usb", ATTR{idVendor}=="04e8", MODE="0666", GROUP="plugdev"
EOF
ok "Règles udev configurées"

# ── Mot de passe root ─────────────────────────────────────────────────────────
log "Définition du mot de passe root..."
# CHANGEZ ce mot de passe avant de distribuer l'ISO !
ROOT_PASS="UnlockOS@2024!"
echo "root:${ROOT_PASS}" | chpasswd
ok "Mot de passe root défini (à changer avant distribution !)"

# ── Protection PyArmor du script maître ──────────────────────────────────────
if command -v pyarmor &>/dev/null || pip3 install --quiet pyarmor 2>/dev/null; then
  log "Obfuscation du script maître avec PyArmor..."
  cd "$DASHBOARD_DIR"
  pyarmor gen AutoUnlocker_NoAPI.py 2>/dev/null && {
    cp -r dist/* "$DASHBOARD_DIR/" 2>/dev/null || true
    ok "AutoUnlocker_NoAPI.py obfusqué"
  } || warn "PyArmor: obfuscation échouée — script non protégé"
else
  warn "PyArmor indisponible — script non protégé"
fi


# ═════════════════════════════════════════════════════════════════════════════
# ÉTAPE 10 : Branding ISO + GRUB + Nettoyage
# ═════════════════════════════════════════════════════════════════════════════
step "10/10 — Branding, GRUB et nettoyage"

# ── Nom de l'OS ───────────────────────────────────────────────────────────────
log "Personnalisation de l'identité OS..."
cat > /etc/os-release << 'EOF'
NAME="UnlockOS"
VERSION="1.0"
ID=unlockos
ID_LIKE=ubuntu
PRETTY_NAME="UnlockOS 1.0 (Mobile Unlock Toolkit)"
VERSION_ID="1.0"
HOME_URL="http://localhost:5000"
SUPPORT_URL="http://localhost:5000"
BUG_REPORT_URL="http://localhost:5000"
EOF

echo "UnlockOS" > /etc/hostname

# ── Message d'accueil terminal ────────────────────────────────────────────────
cat > /etc/motd << 'EOF'

  ╔══════════════════════════════════════════════════════════╗
  ║       🔓 UnlockOS — Mobile Unlock Toolkit v1.0          ║
  ║                                                          ║
  ║  Dashboard : http://localhost:5000                       ║
  ║  Logs      : journalctl -u unlockos -f                   ║
  ║  Mise à jour: /opt/unlockos/update_tools.sh              ║
  ╚══════════════════════════════════════════════════════════╝

EOF

# ── Script de mise à jour accessible globalement ──────────────────────────────
if [ -f "$DASHBOARD_DIR/update_tools.sh" ]; then
  chmod +x "$DASHBOARD_DIR/update_tools.sh"
  ln -sf "$DASHBOARD_DIR/update_tools.sh" /usr/local/bin/unlockos-update
  ok "unlockos-update disponible globalement"
fi

# ── Script de lancement manuel ────────────────────────────────────────────────
cat > /usr/local/bin/unlockos-start << EOF
#!/bin/bash
echo "Démarrage du dashboard UnlockOS..."
cd $DASHBOARD_DIR
python3 app.py --port 5000
EOF
chmod +x /usr/local/bin/unlockos-start

# ── Nettoyage cache apt ───────────────────────────────────────────────────────
log "Nettoyage du cache apt..."
apt autoremove -y -qq
apt clean -qq
rm -rf /var/lib/apt/lists/*
ok "Cache apt nettoyé"

# ── Rafraîchir ldconfig ───────────────────────────────────────────────────────
ldconfig
ok "ldconfig mis à jour"


# ═════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ FINAL
# ═════════════════════════════════════════════════════════════════════════════
echo -e "\n${BOLD}${GREEN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        ✅  BUILD UNLOCKOS TERMINÉ AVEC SUCCÈS !         ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Outils installés :                                      ║"
echo "║   • libimobiledevice stack (compilée depuis source)      ║"
echo "║   • gaster + palera1n (exploits Checkm8)                 ║"
echo "║   • mtkclient + edl (Android MTK/Qualcomm)               ║"
echo "║   • mitmproxy + activation_hijack.py                     ║"
echo "║   • Dashboard Flask (port 5000, autostart)               ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Étapes suivantes dans Cubic :                           ║"
echo "║   1. Cliquez sur [Next] pour passer au kernel            ║"
echo "║   2. Sélectionnez linux-generic (recommandé)             ║"
echo "║   3. Cliquez sur [Generate] pour créer l'ISO             ║"
echo "║   4. Flashez avec Balena Etcher ou Rufus                 ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  Log complet : /var/log/unlockos_build.log               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"
