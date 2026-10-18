#!/bin/bash
# PlexPie rollback: puts back the setup that was there before the first install.
# Usage: sudo bash ~/plexpie/rollback.sh
set -uo pipefail
[ "$EUID" -eq 0 ] || exec sudo bash "$0" "$@"

BACKUP_DIR=/var/backups/plexpie
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$BACKUP_DIR/original.tar.gz" ] || { echo "No backup found in $BACKUP_DIR"; exit 1; }
# shellcheck disable=SC1091
[ -f "$REPO/plexpie.conf" ] && . "$REPO/plexpie.conf"
U="${APPLIANCE_USER:-${SUDO_USER:-$(getent passwd 1000 | cut -d: -f1)}}"
UHOME=$(getent passwd "$U" | cut -d: -f6)

echo "==> Stopping PlexPie services"
systemctl disable --now plexpie plexpie-airplay plexpie-squeezelite nqptp 2>/dev/null
rm -f /etc/systemd/system/plexpie.service /etc/systemd/system/plexpie-airplay.service \
      /etc/systemd/system/plexpie-squeezelite.service /etc/systemd/system/plexamp.service.d/plexpie.conf \
      /etc/sudoers.d/plexpie /etc/udev/rules.d/99-plexpie-backlight.rules
rm -f "$UHOME/.config/wireplumber/wireplumber.conf.d/51-plexpie-dac.conf" \
      "$UHOME/.config/pipewire/pipewire.conf.d/10-plexpie-rates.conf"

echo "==> Restoring files from $BACKUP_DIR/original.tar.gz"
[ -f /etc/asound.conf ] && ! tar -tzf "$BACKUP_DIR/original.tar.gz" etc/asound.conf >/dev/null 2>&1 && rm -f /etc/asound.conf
tar -xzpf "$BACKUP_DIR/original.tar.gz" -C /

echo "==> Restoring service states"
while read -r name value; do
  case "$name" in
    linger) [ "$value" = yes ] || loginctl disable-linger "$U" ;;
    *) [ "$value" = enabled ] && systemctl enable "$name" 2>/dev/null ;;
  esac
done < "$BACKUP_DIR/state.txt"
systemctl daemon-reload

echo
rm -f /usr/local/bin/plexpie /usr/share/applications/plexpie-back.desktop 2>/dev/null || true
echo "Rolled back. Packages and the AirPlay build stay installed but unused."
echo "Reboot to finish:  sudo reboot"
