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
  echo "No plexpie.conf yet: starting the setup wizard."
  bash "$REPO/configure.sh"
  [ -f "$REPO/plexpie.conf" ] || { echo "Setup cancelled."; exit 1; }
fi
# shellcheck disable=SC1091
. "$REPO/plexpie.conf"
U="${APPLIANCE_USER:-${SUDO_USER:-$(getent passwd 1000 | cut -d: -f1)}}"
if ! id "$U" >/dev/null 2>&1; then
  echo "The setup wizard saved '$U' as the screen user, but there's no such user on this Pi."
  echo "Fix it by running the wizard again (press Enter to accept your own username), then the installer:"
  echo "  bash $REPO/configure.sh && sudo bash $REPO/install.sh"
  exit 1
fi
AUID=$(id -u "$U")
UHOME=$(getent passwd "$U" | cut -d: -f6)
PLAYER_NAME="${PLAYER_NAME:-PlexPie}"
AIRPLAY_NAME="${AIRPLAY_NAME:-$PLAYER_NAME}"
DAC_MATCH="${DAC_MATCH:-alsa_output.usb-.*}"
PLEXAMP_VERSION="${PLEXAMP_VERSION:-4.12.4}"

as_user() {
  sudo -u "$U" HOME="$UHOME" XDG_RUNTIME_DIR="/run/user/$AUID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$AUID/bus" "$@"
}
render() {
  sed -e "s|@USER@|$U|g" -e "s|@UID@|$AUID|g" -e "s|@HOME@|$UHOME|g" \
      -e "s|@AIRPLAY_NAME@|$AIRPLAY_NAME|g" -e "s|@DAC_MATCH@|$DAC_MATCH|g" "$1"
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
  if [ ${#paths[@]} -gt 0 ]; then
    tar -czpf "$BACKUP_DIR/original.tar.gz" --ignore-failed-read -C / "${paths[@]}"
  else
    tar -czf "$BACKUP_DIR/original.tar.gz" --files-from /dev/null   # a fresh Pi: nothing to back up yet
  fi
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
free_mb=$(df -Pm / | awk 'NR==2{print $4}')
if [ "${free_mb:-0}" -lt 2500 ]; then
  note "only ${free_mb} MB free on the memory card; the first install needs about 2.5 GB."
  note "Use a bigger card, or Raspberry Pi OS Lite on a small one, if this runs out of space."
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
PKGS=(pipewire pipewire-pulse pipewire-alsa wireplumber pipewire-bin python3 python3-paho-mqtt
      python3-websocket python3-tk python3-evdev xprintidle xdotool curl git avahi-daemon xserver-xorg xinit
      x11-xserver-utils x11vnc alsa-utils nodejs fonts-dejavu-core python3-numpy python3-pil)
# Bluetooth receiver: BlueZ, PipeWire's Bluetooth plugin, and D-Bus bindings for the pairing helper
yes_ "${ENABLE_BLUETOOTH:-yes}" && PKGS+=(bluez libspa-0.2-bluetooth python3-dbus python3-gi rfkill)
# Chromium is "chromium" on Trixie and "chromium-browser" on older Raspberry Pi OS;
# Widevine (needed by the Apple Music web player) comes from Raspberry Pi's repository.
for alts in "chromium chromium-browser" "libwidevinecdm0"; do
  # shellcheck disable=SC2086
  p=$(pick_pkg $alts)
  [ -n "$p" ] && PKGS+=("$p")
done
# Every player is installed, so the Settings page can switch them on and off instantly.
PKGS+=(squeezelite libvorbis0a libogg0)
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
if ! dpkg -s libwidevinecdm0 >/dev/null 2>&1 && yes_ "${ENABLE_APPLE_MUSIC_WEB:-yes}"; then
  note "Widevine isn't available on this OS, so the Apple Music web player won't play."
  note "Use Raspberry Pi OS (64-bit) for it, or turn ENABLE_APPLE_MUSIC_WEB off."
fi
# Debian's own Squeezelite service would run a second player; ours replaces it.
systemctl disable --now squeezelite >/dev/null 2>&1 || true
ok "packages installed"

# --------------------------------------------------------------- 3. audio
step "Routing all audio through PipeWire (so every source can share the DAC)"
loginctl enable-linger "$U"
for _ in $(seq 30); do [ -d "/run/user/$AUID/systemd" ] && break; sleep 0.5; done
install -m 644 "$REPO/files/audio/asound.conf" /etc/asound.conf
as_user mkdir -p "$UHOME/.config/wireplumber/wireplumber.conf.d" "$UHOME/.config/pipewire/pipewire.conf.d"
render "$REPO/files/audio/51-plexpie-dac.conf" > "$UHOME/.config/wireplumber/wireplumber.conf.d/51-plexpie-dac.conf"
chown "$U:$U" "$UHOME/.config/wireplumber/wireplumber.conf.d/51-plexpie-dac.conf"
install -o "$U" -g "$U" -m 644 "$REPO/files/audio/10-plexpie-rates.conf" "$UHOME/.config/pipewire/pipewire.conf.d/"
install -o "$U" -g "$U" -m 644 "$REPO/files/audio/52-plexpie-bluetooth.conf" "$UHOME/.config/wireplumber/wireplumber.conf.d/"
as_user systemctl --user enable pipewire.socket pipewire-pulse.socket wireplumber.service >/dev/null 2>&1 || true
if as_user systemctl --user restart pipewire pipewire-pulse wireplumber; then
  ok "PipeWire restarted"
else
  note "PipeWire will pick up the new settings at the next reboot"
fi
# Give the DAC the full signal and leave volume to the amplifier, Plexamp and the phone.
for _ in $(seq 20); do
  as_user wpctl inspect @DEFAULT_AUDIO_SINK@ >/dev/null 2>&1 && break
  sleep 0.5
done
if as_user wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null; then
  ok "DAC output volume set to 100%"
fi

# -------------------------------------------------------------- 4. plexamp
step "Plexamp"
node_major=$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || true)
if [ -z "$node_major" ] || [ "$node_major" -lt 20 ]; then
  echo "    installing Node.js 20 (Plexamp needs 20 or newer, this OS has ${node_major:-none})"
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -q nodejs
fi
if [ ! -f /opt/plexamp/js/index.js ]; then
  echo "    downloading Plexamp headless $PLEXAMP_VERSION"
  tmp=$(mktemp -d)
  curl -fsSL "https://plexamp.plex.tv/headless/Plexamp-Linux-headless-v${PLEXAMP_VERSION}.tar.bz2" -o "$tmp/plexamp.tar.bz2"
  tar -xjf "$tmp/plexamp.tar.bz2" -C "$tmp"
  rm -rf /opt/plexamp && mv "$tmp/plexamp" /opt/plexamp
  rm -rf "$tmp"
  chown -R "$U:$U" /opt/plexamp
fi
if [ ! -f /etc/systemd/system/plexamp.service ]; then
  render "$REPO/files/systemd/plexamp.service" > /etc/systemd/system/plexamp.service
fi
mkdir -p /etc/systemd/system/plexamp.service.d
render "$REPO/files/systemd/plexamp-override.conf" > /etc/systemd/system/plexamp.service.d/plexpie.conf
systemctl daemon-reload
systemctl enable plexamp >/dev/null 2>&1
PLEX_TOKEN="$UHOME/.local/share/Plexamp/Settings/%40Plexamp%3Auser%3Atoken"
if [ -s "$PLEX_TOKEN" ]; then
  ok "Plexamp is installed and signed in"
elif [ -t 0 ]; then
  systemctl stop plexamp 2>/dev/null || true
  echo
  echo "    Plexamp needs to be linked to your Plex account once:"
  echo "      1. On any device, open https://plex.tv/claim and copy the claim code."
  echo "      2. Paste it below, then type the player name ($PLAYER_NAME)."
  echo "      3. When Plexamp says it is ready, press Ctrl+C to continue the install."
  echo
  as_user node /opt/plexamp/js/index.js </dev/tty || true
  [ -s "$PLEX_TOKEN" ] && ok "Plexamp linked" || note "Plexamp isn't linked yet; run this installer again to retry."
else
  note "Plexamp isn't linked to a Plex account yet. Run this installer again from an"
  note "interactive terminal (ssh -t ...) to link it with a claim code from https://plex.tv/claim."
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

# ----------------------------------------------------------- 5b. spotify
step "Spotify Connect receiver"
if [ ! -x /usr/local/bin/go-librespot ] || [ -n "${UPDATE_SPOTIFY:-}" ]; then
  url=$(curl -fsSL https://api.github.com/repos/devgianlu/go-librespot/releases/latest | python3 -c '
import json, sys
assets = json.load(sys.stdin).get("assets", [])
pick = [a["browser_download_url"] for a in assets
        if "linux" in a["name"].lower() and ("arm64" in a["name"] or "aarch64" in a["name"])]
print(pick[0] if pick else "")' || true)
  if [ -n "$url" ]; then
    tmp=$(mktemp -d)
    curl -fsSL "$url" -o "$tmp/pkg"
    case "$url" in
      *.tar.gz|*.tgz) tar -xzf "$tmp/pkg" -C "$tmp" ;;
      *.zip) python3 -m zipfile -e "$tmp/pkg" "$tmp" ;;
      *) cp "$tmp/pkg" "$tmp/go-librespot" ;;
    esac
    bin=$(find "$tmp" -type f -name 'go-librespot*' ! -name '*.tar.gz' ! -name '*.zip' | head -1)
    if [ -n "$bin" ]; then install -m 755 "$bin" /usr/local/bin/go-librespot; ok "go-librespot installed"; fi
    rm -rf "$tmp"
  else
    note "Couldn't find a go-librespot download for this Pi; Spotify Connect stays off."
  fi
fi
if [ -x /usr/local/bin/go-librespot ]; then
  install -d -o "$U" -g "$U" "$UHOME/.config/go-librespot"
  render "$REPO/files/spotify-config.yml" > "$UHOME/.config/go-librespot/config.yml"
  chown "$U:$U" "$UHOME/.config/go-librespot/config.yml"
  ok "Spotify device name: $PLAYER_NAME"
fi

# ---------------------------------------------------------- 6. plexpie files
step "Installing PlexPie"
install -d -m 755 /opt/plexpie /opt/plexpie/www
install -m 755 "$REPO/files/plexpied.py" "$REPO/files/plexpie-overlay.py" "$REPO/files/plexpie-session" /opt/plexpie/
install -m 755 -o root -g root "$REPO/files/plexpie-admin" /opt/plexpie/plexpie-admin
install -m 755 -o root -g root "$REPO/files/plexpie-bt.py" /opt/plexpie/plexpie-bt.py
install -d -m 755 /var/lib/plexpie
echo "$REPO" > /etc/plexpie/repo_dir
install -m 644 "$REPO/files/www/"*.html "$REPO/files/www/"*.png "$REPO/files/www/"*.js /opt/plexpie/www/
install -m 644 "$REPO/docs/PlexPie-Setup-Guide.pdf" /opt/plexpie/www/guide.pdf 2>/dev/null || true
if [ -f /etc/plexpie/plexpie.conf ] && ! cmp -s "$REPO/plexpie.conf" /etc/plexpie/plexpie.conf; then
  cp -p /etc/plexpie/plexpie.conf /etc/plexpie/plexpie.conf.prev
fi
install -m 640 -o root -g "$U" "$REPO/plexpie.conf" /etc/plexpie/plexpie.conf
for unit in plexpie plexpie-airplay plexpie-squeezelite plexpie-spotify plexpie-bluetooth; do
  render "$REPO/files/systemd/$unit.service" > "/etc/systemd/system/$unit.service"
done

# Optional power button between pins 5 and 6 (GPIO3 and ground): press to shut down, press to start.
# The Pi already starts from that button; the overlay adds the shut-down half.
BOOTCFG=$( [ -f /boot/firmware/config.txt ] && echo /boot/firmware/config.txt || echo /boot/config.txt )
if [ "${POWER_BUTTON:-no}" = yes ]; then
  grep -q '^dtoverlay=gpio-shutdown' "$BOOTCFG" || { echo 'dtoverlay=gpio-shutdown' >> "$BOOTCFG"; note "power button enabled (active after a reboot)"; }
else
  sed -i '/^dtoverlay=gpio-shutdown$/d' "$BOOTCFG" 2>/dev/null || true
fi

# Optional rotary knob: A on pin 11 (GPIO17), B on pin 13 (GPIO27), push on pin 15 (GPIO22), ground on pin 9 or 14.
sed -i -e '/^dtoverlay=rotary-encoder,/d' -e '/^dtoverlay=gpio-key,.*PLAYPAUSE/d' "$BOOTCFG" 2>/dev/null || true
if [ "${KNOB:-no}" = yes ]; then
  {
    echo "dtoverlay=rotary-encoder,pin_a=${KNOB_PIN_A:-17},pin_b=${KNOB_PIN_B:-27},relative_axis=1,steps-per-period=${KNOB_STEPS:-2}"
    echo "dtoverlay=gpio-key,gpio=${KNOB_PIN_PUSH:-22},keycode=164,label=\"PLAYPAUSE\",gpio_pull=up"
  } >> "$BOOTCFG"
  note "rotary knob enabled (active after a reboot)"
fi

# Let the daemon restart its own services and reboot, nothing else.
cat > /etc/sudoers.d/plexpie <<EOF
# PlexPie daemon: restart apps, reboot and shut down without a password
$U ALL=(root) NOPASSWD: /usr/bin/systemctl restart plexamp, /usr/sbin/reboot, /usr/sbin/poweroff
$U ALL=(root) NOPASSWD: /opt/plexpie/plexpie-admin
$U ALL=(root) NOPASSWD: /usr/bin/systemctl start plexpie-airplay, /usr/bin/systemctl stop plexpie-airplay, /usr/bin/systemctl restart plexpie-airplay
$U ALL=(root) NOPASSWD: /usr/bin/systemctl start plexpie-squeezelite, /usr/bin/systemctl stop plexpie-squeezelite, /usr/bin/systemctl restart plexpie-squeezelite
$U ALL=(root) NOPASSWD: /usr/bin/systemctl start plexpie-spotify, /usr/bin/systemctl stop plexpie-spotify, /usr/bin/systemctl restart plexpie-spotify
$U ALL=(root) NOPASSWD: /usr/bin/systemctl start plexpie-bluetooth, /usr/bin/systemctl stop plexpie-bluetooth, /usr/bin/systemctl restart plexpie-bluetooth
EOF
chmod 440 /etc/sudoers.d/plexpie
visudo -cf /etc/sudoers.d/plexpie >/dev/null

# Backlight control for the touchscreen
cat > /etc/udev/rules.d/99-plexpie-backlight.rules <<'EOF'
# PlexPie: let the video group turn the touchscreen backlight on and off
SUBSYSTEM=="backlight", ACTION=="add", RUN+="/bin/chgrp video /sys%p/brightness /sys%p/bl_power", RUN+="/bin/chmod g+w /sys%p/brightness /sys%p/bl_power"
EOF
usermod -aG video,input "$U"   # backlight + touchscreen gestures
getent group bluetooth >/dev/null && usermod -aG bluetooth "$U"   # PipeWire receives Bluetooth audio
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
# Optional players are enabled at boot; the plexpie daemon stops the ones switched off in Settings.
systemctl enable plexpie-squeezelite >/dev/null 2>&1
[ -x /usr/local/bin/go-librespot ] && systemctl enable plexpie-spotify >/dev/null 2>&1
if [ -x /usr/sbin/bluetoothd ] || [ -x /usr/libexec/bluetooth/bluetoothd ]; then
  # Show up as a speaker; reconnecting phones that were already paired needs no confirmation
  BTCONF=/etc/bluetooth/main.conf
  if [ -f "$BTCONF" ]; then
    grep -q '^Class *=' "$BTCONF" || sed -i '/^\[General\]/a Class = 0x240414' "$BTCONF"
    grep -q '^JustWorksRepairing *=' "$BTCONF" || sed -i '/^\[General\]/a JustWorksRepairing = always' "$BTCONF"
  fi
  command -v rfkill >/dev/null && rfkill unblock bluetooth 2>/dev/null || true
  systemctl enable bluetooth >/dev/null 2>&1 || true
  systemctl enable plexpie-bluetooth >/dev/null 2>&1
fi

[ -f /opt/plexamp/js/index.js ] && systemctl restart plexamp
systemctl restart plexpie
yes_ "${ENABLE_AIRPLAY:-yes}" && systemctl restart plexpie-airplay
yes_ "${ENABLE_MUSIC_ASSISTANT:-yes}" && systemctl restart plexpie-squeezelite
yes_ "${ENABLE_SPOTIFY:-no}" && [ -x /usr/local/bin/go-librespot ] && systemctl restart plexpie-spotify
yes_ "${ENABLE_BLUETOOTH:-yes}" && systemctl is-enabled --quiet plexpie-bluetooth 2>/dev/null && systemctl restart bluetooth plexpie-bluetooth

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
apt-get clean >/dev/null 2>&1 || true              # downloaded package files aren't needed any more
echo "    free space on the memory card: $(df -Ph / | awk 'NR==2{print $4}')"
for unit in plexamp plexpie plexpie-airplay plexpie-squeezelite plexpie-spotify nqptp; do
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
