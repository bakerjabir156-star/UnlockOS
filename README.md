# 🔓 UnlockOS — Mobile Unlock Toolkit v1.0

**UnlockOS** est un système Linux bootable (ISO) conçu pour automatiser le déverrouillage des appareils mobiles (iOS & Android).  
Il intègre un dashboard web Flask accessible à `http://localhost:5000` dès le démarrage.

---

## 📦 Contenu du projet

| Fichier | Description |
|---|---|
| `app.py` | Serveur Flask — Dashboard web principal |
| `engine.py` | Moteur AutoUnlocker — Logique de détection et bypass |
| `AutoUnlocker_NoAPI.py` | Script principal sans dépendance API externe |
| `activation_hijack.py` | Module de détournement d'activation iCloud |
| `build_cubic.sh` | Script de build ISO via Cubic (Ubuntu 24.04 / Debian 12) |
| `install_dashboard.sh` | Script d'installation rapide du dashboard |
| `unlockos.service` | Fichier systemd pour l'autostart du dashboard |
| `requirements.txt` | Dépendances Python |
| `config.py` | Configuration centralisée |
| `db.py` | Gestion de la base de données historique |
| `update_tools.sh` | Script de mise à jour des outils |

---

## 🚀 Création de l'ISO

### Prérequis
- **Cubic** installé sur Linux (Ubuntu/Debian)
- ISO de base : **Ubuntu 24.04 LTS** ou **Debian 12**

### Étapes
1. Ouvrez **Cubic** et sélectionnez l'ISO Ubuntu 24.04 de base
2. Dans le terminal Cubic (chroot), exécutez :
   ```bash
   curl -fsSL https://raw.githubusercontent.com/YDAANOUN/UnlockOS/main/build_cubic.sh | bash
   ```
3. Attendez la fin du build (20-40 minutes)
4. Dans Cubic, cliquez sur **[Next]** → sélectionnez le kernel → **[Generate]**
5. Flashez l'ISO avec **Balena Etcher** ou **Rufus**

---

## 💻 Dashboard Web

Accessible via `http://localhost:5000` après démarrage de l'ISO.

### Lancement manuel
```bash
unlockos-start
```

### Logs en temps réel
```bash
journalctl -u unlockos -f
```

---

## 🔧 Appareils supportés

### iOS
- **A7–A11** : Exploit Checkm8 via `gaster`
- **A8–A11, iOS 15-16** : Jailbreak via `palera1n`
- Bypass MDM / iCloud : `MDMPatcher-Enhanced`

### Android
- **MediaTek (MTK)** : `mtkclient` via mode BROM
- **Qualcomm EDL** : mode 9008
- **Xiaomi** : `MiUnlockTool`

---

## ⚙️ Configuration réseau

- **mitmproxy** : Interception HTTPS pour l'activation
- **Tailscale** : VPN mesh pour accès distant

---

## ⚠️ Avertissement légal

> Ce projet est destiné **uniquement** à la recherche en sécurité et au déverrouillage d'appareils dont vous êtes le propriétaire légitime.  
> Toute utilisation illégale est de la responsabilité exclusive de l'utilisateur.

---

## 📝 Licence

MIT License — © 2024 YDAANOUN
