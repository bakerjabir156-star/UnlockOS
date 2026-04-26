#!/bin/bash
# =============================================================================
#  UnlockOS — Script de Build pour CI GitHub Actions (sans GUI)
#  Adapte de build_cubic.sh pour tourner dans un chroot debootstrap.
#  Les commandes non disponibles en chroot (systemctl, udev reload)
#  sont protegees avec || true pour ne pas bloquer le build.
# =============================================================================
set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[UnlockOS-CI]${NC} $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${NC} $*"; }
step() { echo -e "\n${BOLD}${CYAN}--- $* ---${NC}\n"; }

BUILD_DIR="/opt/UnlockOS_Build"
DASHBOARD_DIR="/opt/unlockos/dashboard"
DASHBOARD_SRC="/tmp/unlockos-dashboard"
LOG_FILE="/var/log/unlockos_build.log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "==========================================================="
echo "  UnlockOS CI Build — $(date)"
echo "==========================================================="

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 1 : MAJ systeme
# ─────────────────────────────────────────────────────────────────────────────
step "1/9 — Mise a jour systeme"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get upgrade -y -qq 2>/dev/null || warn "Upgrade partiel"
ok "Systeme mis a jour"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 2 : Dependances
# ─────────────────────────────────────────────────────────────────────────────
step "2/9 — Dependances"
apt-get install -y \
  build-essential git cmake make autoconf automake libtool pkg-config \
  libssl-dev libusb-1.0-0-dev libcurl4-openssl-dev libzip-dev libbz2-dev \
  zlib1g-dev \
  python3 python3-pip python3-dev python3-venv python3-setuptools \
  curl wget tar unzip ca-certificates \
  usbutils iproute2 net-tools \
  openssl nano vim \
  -qq 2>/dev/null || warn "Certains paquets ont echoue"
ok "Dependances installees"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 3 : libimobiledevice (stack Apple)
# ─────────────────────────────────────────────────────────────────────────────
step "3/9 — libimobiledevice stack"

# Installer via apt d'abord (plus rapide en CI)
apt-get install -y \
  libimobiledevice-dev libimobiledevice-utils \
  usbmuxd libusbmuxd-dev libirecovery-dev libplist-dev \
  ideviceinstaller \
  -qq 2>/dev/null || warn "libimobiledevice apt: certains paquets manquants"

mkdir -p "$BUILD_DIR/apple"
cd "$BUILD_DIR/apple"

# Compiler uniquement ce qui manque en apt
for repo_url in \
  "https://github.com/libimobiledevice/libideviceactivation"
do
  repo_name=$(basename "$repo_url")
  log "Compilation: $repo_name"
  if git clone --depth=1 "$repo_url" "$repo_name" 2>/dev/null; then
    cd "$repo_name"
    ./autogen.sh --prefix=/usr/local --without-cython 2>/dev/null \
      || ./configure --prefix=/usr/local 2>/dev/null \
      || { warn "$repo_name: configure echoue"; cd ..; continue; }
    make -j"$(nproc)" 2>/dev/null && make install && ldconfig
    ok "$repo_name installe"
    cd ..
  else
    warn "$repo_name: clone echoue"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 4 : Exploits iOS
# ─────────────────────────────────────────────────────────────────────────────
step "4/9 — Exploits iOS"
mkdir -p "$BUILD_DIR/exploits"
cd "$BUILD_DIR/exploits"

# Gaster
if git clone --depth=1 https://github.com/0x7ff/gaster gaster 2>/dev/null; then
  cd gaster
  make -j"$(nproc)" 2>/dev/null && cp gaster /usr/local/bin/gaster && chmod +x /usr/local/bin/gaster
  ok "Gaster installe"
  cd ..
else
  warn "Gaster: clone echoue"
fi

# Palera1n (binaire officiel)
PALERA1N_URL="https://github.com/palera1n/palera1n/releases/latest/download/palera1n-linux-x86_64"
if curl -sSL "$PALERA1N_URL" -o /usr/local/bin/palera1n --max-time 60; then
  chmod +x /usr/local/bin/palera1n
  ok "Palera1n installe"
else
  warn "Palera1n: telechargement echoue"
fi

# ipwndfu
git clone --depth=1 https://github.com/axi0mX/ipwndfu "$BUILD_DIR/exploits/ipwndfu" 2>/dev/null \
  && ok "ipwndfu clone" || warn "ipwndfu: echec"

# Ramiel
git clone --depth=1 https://github.com/MatthewPierson/Ramiel "$BUILD_DIR/exploits/Ramiel" 2>/dev/null \
  && ok "Ramiel clone" || warn "Ramiel: echec"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 5 : Bypass MDM/iCloud
# ─────────────────────────────────────────────────────────────────────────────
step "5/9 — Bypass MDM/iCloud"
mkdir -p "$BUILD_DIR/bypass"
cd "$BUILD_DIR/bypass"

git clone --depth=1 https://github.com/fled-dev/MDMPatcher-Enhanced 2>/dev/null \
  && ok "MDMPatcher-Enhanced clone" || warn "MDMPatcher: echec"

mkdir -p "$BUILD_DIR/bypass/Meow-Activator"
echo "Meow-Activator — a placer manuellement. Voir README." \
  > "$BUILD_DIR/bypass/Meow-Activator/README.txt"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 6 : Outils Android
# ─────────────────────────────────────────────────────────────────────────────
step "6/9 — Outils Android"
mkdir -p "$BUILD_DIR/android"

pip3 install --break-system-packages --quiet mtkclient pyusb pyserial cryptography colorama 2>/dev/null \
  && ok "MTKClient installe" || warn "mtkclient pip: echec partiel"

git clone --depth=1 https://github.com/offici5l/MiUnlockTool "$BUILD_DIR/android/MiUnlockTool" 2>/dev/null \
  && ok "MiUnlockTool clone" || warn "MiUnlockTool: echec"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 7 : Reseau (mitmproxy)
# ─────────────────────────────────────────────────────────────────────────────
step "7/9 — Infrastructure reseau"
pip3 install --break-system-packages --quiet mitmproxy 2>/dev/null && ok "mitmproxy installe" || warn "mitmproxy: echec"

# Tailscale (optionnel en CI)
curl -fsSL https://tailscale.com/install.sh | sh 2>/dev/null \
  && ok "Tailscale installe" || warn "Tailscale: non installe (optionnel)"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 8 : Dashboard Flask
# ─────────────────────────────────────────────────────────────────────────────
step "8/9 — Dashboard UnlockOS"
pip3 install --break-system-packages --quiet flask 2>/dev/null && ok "Flask installe"

mkdir -p "$DASHBOARD_DIR"

if [ -d "$DASHBOARD_SRC" ] && [ -f "$DASHBOARD_SRC/app.py" ]; then
  cp -r "$DASHBOARD_SRC"/. "$DASHBOARD_DIR/"
  ok "Dashboard copie depuis $DASHBOARD_SRC"
else
  warn "Sources non trouvees dans $DASHBOARD_SRC"
fi

# Symlinks bypass
ln -sf "$BUILD_DIR/bypass/MDMPatcher-Enhanced" "$DASHBOARD_DIR/MDMPatcher-Enhanced" 2>/dev/null || true
ln -sf "$BUILD_DIR/bypass/Meow-Activator"      "$DASHBOARD_DIR/Meow-Activator"      2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 9 : Systemd + Branding (adapte CI — sans systemctl actif)
# ─────────────────────────────────────────────────────────────────────────────
step "9/9 — Systemd + Branding"

# Service systemd (copie uniquement, sera active au boot reel)
if [ -f "$DASHBOARD_SRC/unlockos.service" ]; then
  cp "$DASHBOARD_SRC/unlockos.service" /etc/systemd/system/unlockos.service
  ok "unlockos.service installe dans /etc/systemd/system/"
else
  cat > /etc/systemd/system/unlockos.service << EOF
[Unit]
Description=UnlockOS Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${DASHBOARD_DIR}
ExecStart=/usr/bin/python3 ${DASHBOARD_DIR}/app.py --port 5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
fi

# Activer les services (ignore les erreurs chroot — sera OK au boot)
systemctl enable unlockos.service 2>/dev/null || true
systemctl enable usbmuxd          2>/dev/null || true

# udev rules
cat > /etc/udev/rules.d/99-unlockos.rules << 'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="05ac", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="05c6", ATTR{idProduct}=="9008", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTR{idVendor}=="04e8", MODE="0666", GROUP="plugdev"
EOF

# Mot de passe root
echo "root:UnlockOS@2024!" | chpasswd

# OS identity
cat > /etc/os-release << 'EOF'
NAME="UnlockOS"
VERSION="1.0"
ID=unlockos
ID_LIKE=ubuntu
PRETTY_NAME="UnlockOS 1.0 (Mobile Unlock Toolkit)"
VERSION_ID="1.0"
HOME_URL="http://localhost:5000"
EOF

echo "UnlockOS" > /etc/hostname

cat > /etc/motd << 'EOF'

  =====================================================
      UnlockOS 1.0 - Mobile Unlock Toolkit
  =====================================================
  Dashboard : http://localhost:5000
  Logs      : journalctl -u unlockos -f
  =====================================================

EOF

# Script de lancement manuel
cat > /usr/local/bin/unlockos-start << EOF
#!/bin/bash
cd $DASHBOARD_DIR
python3 app.py --port 5000
EOF
chmod +x /usr/local/bin/unlockos-start

# Nettoyage
apt-get autoremove -y -qq 2>/dev/null || true
apt-get clean -qq
rm -rf /var/lib/apt/lists/*
ldconfig

echo ""
echo "==========================================================="
echo "  BUILD CI TERMINE — $(date)"
echo "==========================================================="
echo "  Dashboard : $DASHBOARD_DIR"
echo "  Service   : /etc/systemd/system/unlockos.service"
echo "==========================================================="
