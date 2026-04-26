#!/bin/bash
# =============================================================================
#  UnlockOS — Assemblage de l'ISO bootable
#  Utilise xorriso + squashfs-tools + GRUB EFI/BIOS
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
ISO_NAME="UnlockOS-${VERSION}-${DATE}-amd64.iso"
LABEL="UNLOCKOS_10"

log "======================================================="
log " UnlockOS ISO Assembly — $(date)"
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
mkdir -p "$ISO_DIR"/{casper,boot/grub,EFI/BOOT}
ok "Structure ISO creee"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 2 : Copier kernel + initrd depuis le chroot
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 2 — Copie du kernel et initrd..."

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
  -e "$CHROOT_DIR/proc" \
  -e "$CHROOT_DIR/sys" \
  -e "$CHROOT_DIR/dev" \
  -e "$CHROOT_DIR/run" \
  -e "$CHROOT_DIR/tmp" \
  -noappend

SQUASHFS_SIZE=$(du -sh "$ISO_DIR/casper/filesystem.squashfs" | cut -f1)
ok "SquashFS cree: $SQUASHFS_SIZE"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 4 : Configuration GRUB
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 4 — Configuration GRUB..."

cat > "$ISO_DIR/boot/grub/grub.cfg" << 'GRUBEOF'
set default=0
set timeout=10
set timeout_style=menu

# Couleurs GRUB
set color_normal=cyan/black
set color_highlight=black/cyan

insmod all_video
insmod gfxterm
terminal_output gfxterm 2>/dev/null || terminal_output console

# Recherche du peripherique de boot (ISO/USB)
search --no-floppy --set=root --file /casper/vmlinuz

menuentry "UnlockOS 1.0 — Boot (Standard)" --class unlockos --class gnu-linux {
    linux   /casper/vmlinuz boot=casper quiet splash locale=fr_MA.UTF-8 ---
    initrd  /casper/initrd.img
}

menuentry "UnlockOS 1.0 — Boot (Debug mode)" --class unlockos {
    linux   /casper/vmlinuz boot=casper debug verbose locale=fr_MA.UTF-8 ---
    initrd  /casper/initrd.img
}

menuentry "UnlockOS 1.0 — Boot (no splash)" --class unlockos {
    linux   /casper/vmlinuz boot=casper locale=fr_MA.UTF-8 ---
    initrd  /casper/initrd.img
}

menuentry "Check integrity" {
    linux   /casper/vmlinuz boot=casper integrity-check quiet splash ---
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
# ETAPE 5 : GRUB EFI (UEFI boot)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 5 — Preparation GRUB EFI..."

grub-mkstandalone \
  --format=x86_64-efi \
  --output="$ISO_DIR/EFI/BOOT/BOOTX64.EFI" \
  --install-modules="linux normal iso9660 biosdisk memdisk search tar ls all_video gfxterm font echo part_gpt part_msdos fat test" \
  --modules="linux normal iso9660 search part_gpt part_msdos fat" \
  --locales="" \
  --fonts="" \
  "boot/grub/grub.cfg=$ISO_DIR/boot/grub/grub.cfg" \
  2>/dev/null || warn "grub-mkstandalone EFI: erreur (BIOS boot toujours disponible)"

# Image FAT pour EFI (FAT16 pour une meilleure compatibilite)
dd if=/dev/zero of="$ISO_DIR/boot/grub/efi.img" bs=1M count=20 2>/dev/null
mkfs.fat -F 16 -n "EFIBOOT" "$ISO_DIR/boot/grub/efi.img" 2>/dev/null
sudo mkdir -p /mnt/efi_tmp
sudo mount "$ISO_DIR/boot/grub/efi.img" /mnt/efi_tmp 2>/dev/null || warn "Mount EFI img: echec"
sudo mkdir -p /mnt/efi_tmp/EFI/BOOT 2>/dev/null || true
sudo cp "$ISO_DIR/EFI/BOOT/BOOTX64.EFI" /mnt/efi_tmp/EFI/BOOT/ 2>/dev/null || true
sudo umount /mnt/efi_tmp 2>/dev/null || true

ok "EFI image prete"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 6 : GRUB BIOS (MBR boot)
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 6 — Preparation GRUB BIOS..."

grub-mkstandalone \
  --format=i386-pc \
  --output="core.img" \
  --install-modules="linux normal iso9660 biosdisk memdisk search tar ls" \
  --modules="linux normal iso9660 biosdisk search" \
  --locales="" \
  --fonts="" \
  "boot/grub/grub.cfg=$ISO_DIR/boot/grub/grub.cfg" \
  2>/dev/null || warn "grub-mkstandalone BIOS: erreur"

cat /usr/lib/grub/i386-pc/cdboot.img core.img > "$ISO_DIR/boot/grub/bios.img" 2>/dev/null \
  || warn "bios.img: echec (ISO peut ne pas booter en BIOS legacy)"

ok "BIOS image prete"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 7 : Fichiers de metadonnees
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 7 — Metadonnees ISO..."

mkdir -p "$ISO_DIR/.disk"
cat > "$ISO_DIR/.disk/info" << EOF
UnlockOS 1.0 "${DATE}" - Mobile Unlock Toolkit
EOF
echo "full_cd" > "$ISO_DIR/.disk/cd_type"

# Taille filesystem
du -sx --block-size=1 "$CHROOT_DIR" 2>/dev/null | cut -f1 \
  > "$ISO_DIR/casper/filesystem.size" || echo "0" > "$ISO_DIR/casper/filesystem.size"

# Manifest des paquets
sudo chroot "$CHROOT_DIR" dpkg-query -W --showformat='${Package} ${Version}\n' 2>/dev/null \
  > "$ISO_DIR/casper/filesystem.manifest" || true

ok "Metadonnees ajoutees"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 8 : Generer l'ISO avec xorriso
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 8 — Generation de l'ISO avec xorriso..."

sudo xorriso \
  -as mkisofs \
  -iso-level 3 \
  -full-iso9660-filenames \
  -volid "$LABEL" \
  -eltorito-boot "boot/grub/bios.img" \
    -no-emul-boot \
    -boot-load-size 4 \
    -boot-info-table \
    --eltorito-catalog "boot/grub/boot.cat" \
  --grub2-boot-info \
  --grub2-mbr /usr/lib/grub/i386-pc/boot_hybrid.img \
  -eltorito-alt-boot \
    -e "boot/grub/efi.img" \
    -no-emul-boot \
    -append_partition 2 0xef "$ISO_DIR/boot/grub/efi.img" \
  -output "$ISO_NAME" \
  "$ISO_DIR" \
  2>&1

ok "ISO generee: $ISO_NAME ($(du -sh $ISO_NAME | cut -f1))"

# ─────────────────────────────────────────────────────────────────────────────
# ETAPE 9 : Checksums
# ─────────────────────────────────────────────────────────────────────────────
log "ETAPE 9 — Checksums..."
sha256sum "$ISO_NAME" > "UnlockOS-SHA256.txt"
md5sum    "$ISO_NAME" > "UnlockOS-MD5.txt"
ok "SHA256: $(cat UnlockOS-SHA256.txt | cut -d' ' -f1)"

echo ""
echo "======================================================="
echo "  ISO ASSEMBLEE AVEC SUCCES !"
echo "======================================================="
echo "  Fichier  : $ISO_NAME"
echo "  Taille   : $(du -sh $ISO_NAME | cut -f1)"
echo "  SHA256   : $(cat UnlockOS-SHA256.txt | cut -d' ' -f1)"
echo "======================================================="
echo ""
echo "  Flash avec Rufus (Windows) ou :"
echo "  sudo dd if=$ISO_NAME of=/dev/sdX bs=4M status=progress"
echo "======================================================="
