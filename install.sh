#!/bin/bash
# PlexPie installer: turns the Pi into a one-screen music endpoint
# (Plexamp, Apple Music, AirPlay 2, Music Assistant, Home Assistant control).
# Safe to re-run after editing plexpie.conf.
#
# Usage: sudo bash ~/plexpie/install.sh [--skip-airplay-build] [--no-restart-ui]
set -Eeuo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/var/log/plexpie-install.log
BACKUP_DIR=/var/backups/plexpie
SRC_DIR=/usr/local/src/plexpie
SKIP_AIRPLAY_BUILD=no
RESTART_UI=yes

for arg in "$@"; do
  case "$arg" in
    --skip-airplay-build) SKIP_AIRPLAY_BUILD=yes ;;
    --no-restart-ui) RESTART_UI=no ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

[ "$EUID" -eq 0 ] || exec sudo bash "$0" "$@"

exec > >(tee -a "$LOG") 2>&1
trap 'echo >&2; echo "!! Install failed at line $LINENO: $BASH_COMMAND" >&2; echo "!! Full log: $LOG" >&2' ERR

step() { echo; echo "==> $*"; }
ok()   { echo "    ok: $*"; }
note() { echo "    note: $*"; }
yes_() { case "${1,,}" in yes|true|1|on) return 0 ;; *) return 1 ;; esac; }

echo "PlexPie install started $(date)"

if [ ! -f "$REPO/plexpie.conf" ]; then
  if [ -f "$REPO/plexpie.conf.example" ]; then
    cp "$REPO/plexpie.conf.example" "$REPO/plexpie.conf"
    echo "Created $REPO/plexpie.conf from the example."
    echo "Edit it (MQTT password, addresses, player name), then run this installer again."
  else
    echo "plexpie.conf not found next to install.sh"
  fi
  exit 1
fi
# shellcheck disable=SC1091
. "$REPO/plexpie.conf"
U="${APPLIANCE_USER:-sz}"
id "$U" >/dev/null 2>&1 || { echo "User '$U' does not exist"; exit 1; }
AUID=$(id -u "$U")
UHOME=$(getent passwd "$U" | cut -d: -f6)
AIRPLAY_NAME="${AIRPLAY_NAME:-$PLAYER_NAME}"

as_user() {
  sudo -u "$U" HOME="$UHOME" XDG_RUNTIME_DIR="/run/user/$AUID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUID/bus" "$@"
}
render() {
  sed -e "s|@USER@|$U|g" -e "s|@UID@|$AUID|g" -e "s|@HOME@|$UHOME|g" \
      -e "s|@AIRPLAY_NAME@|$AIRPLAY_NAME|g" "$1"
}
pick_pkg() {
  for p in "$@"; do
    if apt-cache show "$p" >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  return 0
}
latest_tag() {   # newest release tag, ignoring a leading "v" when comparing (v2.0.0 < 4.3.7)
  git -C "$1" tag | grep -E '^v?[0-9]+(\.[0-9]+)+$' \
    | awk '{ k = $0; sub(/^v/, "", k); print k, $0 }' | sort -V -r | head -1 | cut -d' ' -f2 || true
}
fetch_src() {   # url dir -> prints the release tag it checked out
  local url=$1 dir=$2 tag
  if [ -d "$dir/.git" ]; then git -C "$dir" fetch --tags -q >&2; else git clone -q "$url" "$dir" >&2; fi
  # A previous build leaves generated files behind; start from a clean tree.
  git -C "$dir" reset -q --hard >&2
  git -C "$dir" clean -qfdx >&2
  tag=$(latest_tag "$dir")
  [ -n "$tag" ] && git -C "$dir" checkout -q "$tag" >&2
  echo "${tag:-HEAD}"
}
build() {       # name dir configure-args...
  local name=$1 dir=$2; shift 2
  local blog="$SRC_DIR/$name-build.log"
  if ! (cd "$dir" && autoreconf -fi && ./configure "$@" && make -j3 && make install) >"$blog" 2>&1; then
    echo "    $name build failed, last lines of $blog:"
    tail -30 "$blog"
    return 1
  fi
}

# ---------------------------------------------------------------- 1. backup
step "Backing up the current setup"
mkdir -p "$BACKUP_DIR"
if [ ! -f "$BACKUP_DIR/original.tar.gz" ]; then
  paths=()
  for p in "$UHOME/.xinitrc" "$UHOME/.xprofile" "$UHOME/.bash_profile" "$UHOME/bin" \
           /etc/asound.conf /etc/systemd/system/plexamp.service /etc/systemd/system/display-control.service \
           /etc/lightdm/lightdm.conf "$UHOME/.local/share/Plexamp"; do
    [ -e "$p" ] && paths+=("${p#/}")
  done
  tar -czpf "$BACKUP_DIR/original.tar.gz" -C / "${paths[@]}"
  {
    for unit in lightdm display-control; do
      echo "$unit $(systemctl is-enabled "$unit" 2>/dev/null || true)"
    done
    echo "linger $(loginctl show-user "$U" -p Linger --value 2>/dev/null || echo no)"
  } > "$BACKUP_DIR/state.txt"
  ok "saved $BACKUP_DIR/original.tar.gz (undo with: sudo bash $REPO/rollback.sh)"
else
  ok "original backup already exists, keeping it"
fi

# -------------------------------------------------------------- 2. packages
step "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
PKGS=(pipewire-alsa wireplumber pipewire-bin python3 python3-paho-mqtt python3-websocket python3-tk python3-xlib
      xprintidle xdotool curl avahi-daemon xinit x11-xserver-utils alsa-utils chromium)
yes_ "${ENABLE_MUSIC_ASSISTANT:-yes}" && PKGS+=(squeezelite)
if yes_ "${ENABLE_AIRPLAY:-yes}" && [ "$SKIP_AIRPLAY_BUILD" = no ]; then
  PKGS+=(build-essential git autoconf automake libtool pkg-config libpopt-dev libconfig-dev
         libasound2-dev libavahi-client-dev libssl-dev libsoxr-dev libsodium-dev libavutil-dev
         libavcodec-dev libavformat-dev libswresample-dev uuid-dev xxd)
  for alts in "libplist-dev libplist-2.0-dev" "libgcrypt-dev libgcrypt20-dev" "libplist-utils" "systemd-dev"; do
    # shellcheck disable=SC2086
    p=$(pick_pkg $alts)
    [ -n "$p" ] && PKGS+=("$p")
  done
fi
apt-get install -y -q --no-install-recommends "${PKGS[@]}"
# Debian's own Squeezelite service would run a second player; ours replaces it.
systemctl disable --now squeezelite >/dev/null 2>&1 || true
ok "packages installed"

# --------------------------------------------------------------- 3. audio
step "Routing all audio through PipeWire (so every source can share the DAC)"
loginctl enable-linger "$U"
for _ in $(seq 30); do [ -d "/run/user/$AUID/systemd" ] && break; sleep 0.5; done
install -m 644 "$REPO/files/audio/asound.conf" /etc/asound.conf
as_user mkdir -p "$UHOME/.config/wireplumber/wireplumber.conf.d" "$UHOME/.config/pipewire/pipewire.conf.d"
install -o "$U" -g "$U" -m 644 "$REPO/files/audio/51-plexpie-dac.conf" "$UHOME/.config/wireplumber/wireplumber.conf.d/"
install -o "$U" -g "$U" -m 644 "$REPO/files/audio/10-plexpie-rates.conf" "$UHOME/.config/pipewire/pipewire.conf.d/"
as_user systemctl --user enable pipewire.socket pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true
if as_user systemctl --user restart pipewire pipewire-pulse wireplumber; then
  ok "PipeWire restarted"
else
  note "PipeWire will pick up the new settings at the next reboot"
fi
# The DAC used to get the full signal (Plexamp wrote to it directly). Keep it at
# 100% and leave volume to the Onkyo, Plexamp and the phone.
for _ in $(seq 20); do
  as_user wpctl status 2>/dev/null | grep -q "SMSL" && break
  sleep 0.5
done
if as_user wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null; then
  ok "DAC output volume set to 100%"
fi

# -------------------------------------------------------------- 4. plexamp
step "Plexamp"
if [ -f /opt/plexamp/js/index.js ]; then
  mkdir -p /etc/systemd/system/plexamp.service.d
  render "$REPO/files/systemd/plexamp-override.conf" > /etc/systemd/system/plexamp.service.d/plexpie.conf
  ok "kept your existing Plexamp install and sign-in; it now plays through PipeWire"
else
  note "Plexamp headless is not in /opt/plexamp. Install it, run 'node /opt/plexamp/js/index.js' once"
  note "to claim it, then re-run this installer."
fi

# --------------------------------------------------------------- 5. airplay
install -d -m 750 -o root -g "$U" /etc/plexpie
if yes_ "${ENABLE_AIRPLAY:-yes}"; then
  step "AirPlay 2 receiver"
  if dpkg -s shairport-sync >/dev/null 2>&1; then apt-get purge -y -q shairport-sync; fi
  if [ "$SKIP_AIRPLAY_BUILD" = no ]; then
    mkdir -p "$SRC_DIR"
    tag=$(fetch_src https://github.com/mikebrady/nqptp.git "$SRC_DIR/nqptp")
    if [ "$(cat "$SRC_DIR/nqptp.built" 2>/dev/null || true)" != "$tag" ] || ! command -v nqptp >/dev/null; then
      echo "    building nqptp $tag"
      build nqptp "$SRC_DIR/nqptp" --with-systemd-startup
      echo "$tag" > "$SRC_DIR/nqptp.built"
    fi
    ok "nqptp $tag"
    tag=$(fetch_src https://github.com/mikebrady/shairport-sync.git "$SRC_DIR/shairport-sync")
    if [ "$(cat "$SRC_DIR/shairport-sync.built" 2>/dev/null || true)" != "$tag" ] || [ ! -x /usr/local/bin/shairport-sync ]; then
      echo "    building shairport-sync $tag (takes about 10 minutes on a Pi 4)"
      build shairport-sync "$SRC_DIR/shairport-sync" --sysconfdir=/etc --with-alsa --with-soxr \
        --with-avahi --with-ssl=openssl --with-airplay-2 --with-metadata --with-metadata-pipe
      echo "$tag" > "$SRC_DIR/shairport-sync.built"
    fi
    ok "shairport-sync $tag"
  fi
  systemctl daemon-reload
  systemctl enable --now nqptp >/dev/null 2>&1
  systemctl disable --now shairport-sync >/dev/null 2>&1 || true
  render "$REPO/files/shairport-sync.conf" > /etc/plexpie/shairport-sync.conf
  chmod 644 /etc/plexpie/shairport-sync.conf
  ok "AirPlay name: $AIRPLAY_NAME"
fi

# ---------------------------------------------------------- 6. plexpie files
step "Installing PlexPie"
install -d -m 755 /opt/plexpie /opt/plexpie/www
install -m 755 "$REPO/files/plexpied.py" "$REPO/files/plexpie-overlay.py" "$REPO/files/plexpie-session" /opt/plexpie/
install -m 644 "$REPO/files/www/"*.html /opt/plexpie/www/
if [ -f /etc/plexpie/plexpie.conf ] && ! cmp -s "$REPO/plexpie.conf" /etc/plexpie/plexpie.conf; then
  cp -p /etc/plexpie/plexpie.conf /etc/plexpie/plexpie.conf.prev
fi
install -m 640 -o root -g "$U" "$REPO/plexpie.conf" /etc/plexpie/plexpie.conf
for unit in plexpie plexpie-airplay plexpie-squeezelite; do
  render "$REPO/files/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
done

# Let the daemon restart its own services and reboot, nothing else.
cat > /etc/sudoers.d/plexpie <<EOF
# PlexPie daemon: restart apps and reboot without a password
$U ALL=(root) NOPASSWD: /usr/bin/systemctl restart plexamp, /usr/bin/systemctl restart plexpie-airplay, /usr/bin/systemctl restart plexpie-squeezelite, /usr/sbin/reboot
EOF
chmod 440 /etc/sudoers.d/plexpie
visudo -cf /etc/sudoers.d/plexpie >/dev/null

# Backlight control for the touchscreen
cat > /etc/udev/rules.d/99-plexpie-backlight.rules <<'EOF'
# PlexPie: let the video group turn the touchscreen backlight on and off
SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys%p/brightness /sys%p/bl_power", RUN+="/bin/chmod g+w /sys%p/brightness /sys%p/bl_power"
EOF
usermod -aG video "$U"
for d in /sys/class/backlight/*; do
  [ -e "$d/brightness" ] || continue
  chgrp video "$d/brightness" "$d/bl_power" 2>/dev/null || true
  chmod g+w "$d/brightness" "$d/bl_power" 2>/dev/null || true
done
ok "PlexPie files in /opt/plexpie, settings in /etc/plexpie/plexpie.conf"

# ------------------------------------------------------------ 7. session
step "Touchscreen session"
systemctl disable --now display-control.service >/dev/null 2>&1 || true
systemctl disable lightdm.service >/dev/null 2>&1 || true
cat > "$UHOME/.xinitrc" <<'EOF'
#!/bin/sh
# PlexPie: the whole screen is run by /opt/plexpie/plexpie-session
export DISPLAY=:0
mkdir -p "$HOME/.local/state/plexpie"
LOG="$HOME/.local/state/plexpie/session.log"
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 5000000 ]; then mv "$LOG" "$LOG.old"; fi
exec /opt/plexpie/plexpie-session >>"$LOG" 2>&1
EOF
cat > "$UHOME/.bash_profile" <<'EOF'
# Load the normal login settings (PATH etc.)
[ -f "$HOME/.profile" ] && . "$HOME/.profile"

# PlexPie: start the touchscreen session on the local console only
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  startx -- -nocursor
  logout
fi
EOF
chown "$U:$U" "$UHOME/.xinitrc" "$UHOME/.bash_profile"
chmod 755 "$UHOME/.xinitrc"
if [ ! -f /etc/systemd/system/getty@tty1.service.d/autologin.conf ]; then
  mkdir -p /etc/systemd/system/getty@tty1.service.d
  printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin %s --noclear %%I $TERM\n' "$U" \
    > /etc/systemd/system/getty@tty1.service.d/autologin.conf
fi
systemctl enable getty@tty1.service >/dev/null 2>&1
ok "console autologin -> startx -> plexpie-session"

# -------------------------------------------------------------- 8. start
step "Starting everything"
systemctl daemon-reload
systemctl enable plexpie >/dev/null 2>&1
if yes_ "${ENABLE_AIRPLAY:-yes}"; then systemctl enable plexpie-airplay >/dev/null 2>&1
else systemctl disable --now plexpie-airplay >/dev/null 2>&1 || true; fi
if yes_ "${ENABLE_MUSIC_ASSISTANT:-yes}"; then systemctl enable plexpie-squeezelite >/dev/null 2>&1
else systemctl disable --now plexpie-squeezelite >/dev/null 2>&1 || true; fi

[ -f /opt/plexamp/js/index.js ] && systemctl restart plexamp
systemctl restart plexpie
yes_ "${ENABLE_AIRPLAY:-yes}" && systemctl restart plexpie-airplay
yes_ "${ENABLE_MUSIC_ASSISTANT:-yes}" && systemctl restart plexpie-squeezelite

if [ "$RESTART_UI" = yes ]; then
  # End the current screen session; autologin starts the new one.
  pkill -u "$U" -f 'bin/appliance-watchdog\.sh|bin/display-control\.sh|plexpie-session' || true
  pkill -u "$U" -x Xorg || true
  sleep 3
  systemctl is-active --quiet getty@tty1 || systemctl restart getty@tty1
  ok "touchscreen restarting"
fi

sleep 4
step "Status"
for unit in plexamp plexpie plexpie-airplay plexpie-squeezelite nqptp; do
  printf '    %-22s %s\n' "$unit" "$(systemctl is-active "$unit" 2>/dev/null || true)"
done
if curl -fs --max-time 3 http://127.0.0.1:8080/api/state >/dev/null; then ok "launcher answering on the Pi"; fi

cat <<EOF

PlexPie is installed.
  * Touchscreen: launcher with Plexamp, Apple Music, AirPlay and Music Assistant
  * Home Assistant: device "$(hostname)" appears under Settings -> Devices (MQTT)
  * AirPlay: choose "$AIRPLAY_NAME" on your iPhone
  * Logs: journalctl -u plexpie -f   and   ~/.local/state/plexpie/session.log
  * Undo: sudo bash $REPO/rollback.sh
A reboot is a good final test:  sudo reboot
EOF
