#!/bin/bash
# update_tools.sh — UnlockOS Tool Updater
# Updates all GitHub-cloned tools and Python packages
set -e

BUILD_DIR="${HOME}/UnlockOS_Build"
LOG="$BUILD_DIR/update_$(date +%Y%m%d_%H%M%S).log"

echo "[*] Mise à jour des outils UnlockOS..." | tee "$LOG"
echo "[*] Log: $LOG"

cd "$BUILD_DIR" 2>/dev/null || { echo "❌ BUILD_DIR introuvable: $BUILD_DIR"; exit 1; }

# ── Git repos ─────────────────────────────────────────────
for d in */; do
  if [ -d "$d/.git" ]; then
    echo "[+] git pull: $d" | tee -a "$LOG"
    cd "$d"
    git pull --rebase --autostash 2>&1 | tee -a "$LOG" || echo "[!] Échec pull: $d"
    # Recompile C tools if Makefile present
    if [ -f Makefile ]; then
      make -s 2>&1 | tee -a "$LOG" || true
    fi
    cd ..
  fi
done

# ── Python packages ───────────────────────────────────────
echo "[*] Mise à jour des packages Python..." | tee -a "$LOG"
pip3 install --upgrade --quiet \
  flask mtkclient mitmproxy pyarmor pyusb pyserial 2>&1 | tee -a "$LOG"

# ── Palera1n binary ───────────────────────────────────────
if command -v palera1n &>/dev/null; then
  echo "[*] Mise à jour palera1n..." | tee -a "$LOG"
  ARCH=$(uname -m)
  if [ "$ARCH" = "x86_64" ]; then
    URL="https://github.com/palera1n/palera1n/releases/latest/download/palera1n-linux-x86_64"
  else
    URL="https://github.com/palera1n/palera1n/releases/latest/download/palera1n-linux-arm64"
  fi
  curl -sL "$URL" -o /tmp/palera1n_new && \
    sudo mv /tmp/palera1n_new /usr/local/bin/palera1n && \
    sudo chmod +x /usr/local/bin/palera1n && \
    echo "[+] palera1n mis à jour" | tee -a "$LOG"
fi

sudo ldconfig 2>/dev/null || true

echo "" | tee -a "$LOG"
echo "✅ Mise à jour complète. Log: $LOG" | tee -a "$LOG"
