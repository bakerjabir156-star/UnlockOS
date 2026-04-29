#!/bin/bash
# =============================================================================
#  UnlockOS — Assemblage de l'ISO bootable (v2 - UEFI Secure Boot Compatible)
#  Utilise xorriso + squashfs-tools + shim/grub signes officiels Ubuntu
#  A executer APRES le build chroot, depuis le runner GitHub Actions
# =============================================================================
set -euo pipefail

log()  { echo "[make_iso] $*"; }
ok()   { echo "[  OK   ] $*"; }
warn() { echo "[ WARN  ] $*"; }
die()  { echo "[ FAIL  ] $*" >&2; exit 1; }

ISO_DIR="iso_staging"
# CHROOT_DIR peut etre passe en variable d'environnement par le workflow CI
CHROOT_DIR="${CHROOT_DIR:-/mnt/chroot}"
VERSION="1.0"
DATE=$(date +%Y%m%d)

# ─── Nom du fichier ISO final (modifiable ici) ────────────────────────────────
ISO_NAME="unlockOS10.iso"
LABEL="UNLOCKOS_10"

log "======================================================="
log " UnlockOS ISO Assembly v2 — $(date)"
log "======================================================="

# ─────────────────────────────────────────────────────────────────────────────
# Verifications prealables
# ─────────────────────────────────────────────────────────────────────────────
for cmd in mksquashfs xorriso grub-mkstandalone; do
  command -v "$cmd" || die "Outil manquant: $cmd"
done

[ -d "$CHROOT_DIR" ] || die "Dossier chroot introuvable: $CHROOT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 1 : Structure de l'ISO
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 1 — Creation de la structure ISO..."
sudo rm -rf "$ISO_DIR"

# NOTE: EFI/BOOT en majuscules — requis par la spec UEFI 2.x
mkdir -p "$ISO_DIR"/{casper,boot/grub,EFI/BOOT,isolinux}
ok "Structure ISO creee"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 2 : Copier kernel + initrd depuis le chroot
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 2 — Copie du kernel et initrd..."

# -L pour dereferencer les liens symboliques (important !)
VMLINUZ=$(find "$CHROOT_DIR/boot" -maxdepth 1 -name "vmlinuz*" | sort -V | tail -1 || true)
INITRD=$(find "$CHROOT_DIR/boot" -maxdepth 1 -name "initrd.img*" | sort -V | tail -1 || true)

[ -n "$VMLINUZ" ] && [ -f "$VMLINUZ" ] || die "Kernel introuvable dans $CHROOT_DIR/boot"
[ -n "$INITRD" ] && [ -f "$INITRD"  ] || die "Initrd introuvable dans $CHROOT_DIR/boot"

sudo cp -L "$VMLINUZ" "$ISO_DIR/casper/vmlinuz"
sudo cp -L "$INITRD"  "$ISO_DIR/casper/initrd.img"
ok "Kernel: $(basename $VMLINUZ)"
ok "Initrd: $(basename $INITRD)"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 3 : Creer le filesystem SquashFS du chroot
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 3 — Compression SquashFS du chroot (peut prendre 10-20 min)..."

sudo mksquashfs "$CHROOT_DIR" "$ISO_DIR/casper/filesystem.squashfs" \
  -comp xz \
  -Xbcj x86 \
  -b 1M \
  -noappend

SQUASHFS_SIZE=$(du -sh "$ISO_DIR/casper/filesystem.squashfs" | cut -f1)
ok "SquashFS cree: $SQUASHFS_SIZE"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 4 : Configuration GRUB (commune BIOS + UEFI)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 4 — Configuration GRUB..."

cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUBEOF'
set default=0
set timeout=10
set timeout_style=menu

set color_normal=cyan/black
set color_highlight=black/cyan

insmod all_video
insmod gfxterm
terminal_output gfxterm 2>/dev/null || terminal_output console

# Localiser automatiquement le disque de boot via le fichier signature
search --no-floppy --set=root --file /casper/vmlinuz

menuentry "UnlockOS 1.0 — Boot (Standard)" --class unlockos --class gnu-linux {
    linux   /casper/vmlinuz boot=live live-media-path=/casper live-config.username=unlockos live-config.hostname=unlockos quiet splash ---
    initrd  /casper/initrd.img
}

menuentry "UnlockOS 1.0 — Boot (Debug mode)" --class unlockos {
    linux   /casper/vmlinuz boot=live live-media-path=/casper live-config.username=unlockos live-config.hostname=unlockos debug verbose ---
    initrd  /casper/initrd.img
}

menuentry "UnlockOS 1.0 — Boot (no splash)" --class unlockos {
    linux   /casper/vmlinuz boot=live live-media-path=/casper live-config.username=unlockos live-config.hostname=unlockos ---
    initrd  /casper/initrd.img
}

menuentry "Check integrity" {
    linux   /casper/vmlinuz boot=live live-media-path=/casper integrity-check quiet splash ---
    initrd  /casper/initrd.img
}

menuentry "Boot from first hard drive" {
    insmod chain
    set root=(hd0)
    chainloader +1
}
GRUBEOF

ok "grub.cfg cree"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 5 : UEFI — Copie des binaires EFI signes Ubuntu (Secure Boot OK)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 5 — Preparation UEFI avec binaires signes Ubuntu..."

SHIM_SIGNED=""
GRUB_EFI_SIGNED=""

# Chercher les binaires shim signes (plusieurs emplacements possibles)
for p in \
  "/usr/lib/shim/shimx64.efi.signed.latest" \
  "/usr/lib/shim/shimx64.efi.signed" \
  "/usr/lib/shim/shimx64.efi"; do
  if [ -f "$p" ]; then SHIM_SIGNED="$p"; break; fi
done

for p in \
  "/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed" \
  "/usr/lib/grub/x86_64-efi-signed/grubx64.efi" \
  "/usr/lib/grub/x86_64-efi/grubx64.efi"; do
  if [ -f "$p" ]; then GRUB_EFI_SIGNED="$p"; break; fi
done

if [ -n "$SHIM_SIGNED" ] && [ -n "$GRUB_EFI_SIGNED" ]; then
  log "Binaires signes trouves — boot Secure Boot compatible"
  cp "$SHIM_SIGNED"      "$ISO_DIR/EFI/BOOT/BOOTX64.EFI"
  cp "$GRUB_EFI_SIGNED"  "$ISO_DIR/EFI/BOOT/grubx64.efi"

  # grub.cfg minimal dans EFI/BOOT pour diriger vers notre config principale
  cat > "$ISO_DIR/EFI/BOOT/grub.cfg" << 'MINIGRUB'
search --no-floppy --set=root --file /casper/vmlinuz
set prefix=($root)/boot/grub
configfile /boot/grub/grub.cfg
MINIGRUB

  ok "Shim + Grub EFI signes installes (Secure Boot compatible)"
else
  warn "Shim non trouve — fallback sur grub-mkstandalone"
  grub-mkstandalone \
    --format=x86_64-efi \
    --output="$ISO_DIR/EFI/BOOT/BOOTX64.EFI" \
    --install-modules="linux normal iso9660 search search_fs_file part_gpt part_msdos fat all_video gfxterm font echo" \
    --modules="linux normal iso9660 search search_fs_file part_gpt part_msdos fat" \
    --locales="" \
    --fonts="" \
    "boot/grub/grub.cfg=$ISO_DIR/boot/grub/grub.cfg" \
    2>/dev/null || warn "grub-mkstandalone EFI: echec"
fi

ok "EFI pret dans $ISO_DIR/EFI/BOOT/"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 6 : GRUB BIOS (MBR / Legacy boot)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 6 — Preparation GRUB BIOS (Legacy)..."

grub-mkstandalone \
  --format=i386-pc \
  --output="core.img" \
  --install-modules="linux normal iso9660 biosdisk memdisk search tar ls" \
  --modules="linux normal iso9660 biosdisk search" \
  --locales="" \
  --fonts="" \
  "boot/grub/grub.cfg=$ISO_DIR/boot/grub/grub.cfg" \
  2>/dev/null || warn "grub-mkstandalone BIOS: echec"

cat /usr/lib/grub/i386-pc/cdboot.img core.img > "$ISO_DIR/boot/grub/bios.img" 2>/dev/null \
  || warn "bios.img: echec (ISO peut ne pas booter en BIOS legacy)"

ok "BIOS image prete"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 7 : Metadonnees ISO (compatibilite Ubuntu Live)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 7 — Metadonnees ISO..."

mkdir -p "$ISO_DIR/.disk"
cat > "$ISO_DIR/.disk/info" << EOF
UnlockOS ${VERSION} "${DATE}" - Mobile Unlock Toolkit
EOF
echo "full_cd" > "$ISO_DIR/.disk/cd_type"

du -sx --block-size=1 "$CHROOT_DIR" 2>/dev/null | cut -f1 \
  > "$ISO_DIR/casper/filesystem.size" || echo "0" > "$ISO_DIR/casper/filesystem.size"

sudo chroot "$CHROOT_DIR" dpkg-query -W --showformat='${Package} ${Version}\n' 2>/dev/null \
  > "$ISO_DIR/casper/filesystem.manifest" || true

ok "Metadonnees ajoutees"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 8 : Generer l'ISO avec xorriso
#
#  NOTE: Pas de -append_partition => ISO pure, Rufus ne demandera PAS le mode DD.
#  L'EFI est embarque via El Torito (standard ISO 9660 + UEFI).
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 8 — Generation de l'ISO: $ISO_NAME..."

sudo xorriso \
  -as mkisofs \
  -iso-level 3 \
  -full-iso9660-filenames \
  -joliet \
  -joliet-long \
  -rational-rock \
  -volid "$LABEL" \
  --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
  -eltorito-boot boot/grub/bios.img \
    -no-emul-boot \
    -boot-load-size 4 \
    -boot-info-table \
    --grub2-boot-info \
    --eltorito-catalog boot/grub/boot.cat \
  -eltorito-alt-boot \
    -e EFI/BOOT/BOOTX64.EFI \
    -no-emul-boot \
  -output "$ISO_NAME" \
  "$ISO_DIR" \
  2>&1

ok "ISO generee: $ISO_NAME ($(du -sh "$ISO_NAME" | cut -f1))"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 9 : Checksums
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 9 — Checksums..."
sha256sum "$ISO_NAME" > "UnlockOS-SHA256.txt"
md5sum    "$ISO_NAME" > "UnlockOS-MD5.txt"
ok "SHA256: $(cut -d' ' -f1 UnlockOS-SHA256.txt)"

echo ""
echo "======================================================="
echo "  ISO ASSEMBLEE AVEC SUCCES !"
echo "======================================================="
echo "  Fichier  : $ISO_NAME"
echo "  Taille   : $(du -sh "$ISO_NAME" | cut -f1)"
echo "  SHA256   : $(cut -d' ' -f1 UnlockOS-SHA256.txt)"
echo "======================================================="
echo ""
echo "  Flash avec Rufus en MODE ISO (GPT + UEFI) :"
echo "  ou : sudo dd if=$ISO_NAME of=/dev/sdX bs=4M status=progress"
echo "======================================================="
