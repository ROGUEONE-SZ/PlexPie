#!/bin/bash
# PlexPie setup wizard: asks a few questions and writes plexpie.conf.
# Run it again any time to change your answers (current values become the defaults).
#
# Usage: bash configure.sh            interactive
#        bash configure.sh --defaults write a config with defaults, no questions
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="$DIR/plexpie.conf"
NONINTERACTIVE=no
[ "${1:-}" = "--defaults" ] && NONINTERACTIVE=yes
[ -t 0 ] || NONINTERACTIVE=yes

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }

# current values (re-running keeps earlier answers as defaults)
if [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
fi

ask() {          # ask VAR "Question" "default"
  local var=$1 q=$2 def=$3 ans
  if [ "$NONINTERACTIVE" = yes ]; then printf -v "$var" '%s' "$def"; return; fi
  read -r -p "$q [${def}]: " ans || true
  printf -v "$var" '%s' "${ans:-$def}"
}
ask_secret() {   # ask_secret VAR "Question" "default"
  local var=$1 q=$2 def=$3 ans hint=""
  if [ "$NONINTERACTIVE" = yes ]; then printf -v "$var" '%s' "$def"; return; fi
  [ -n "$def" ] && hint=" [keep current]"
  read -r -s -p "$q${hint}: " ans || true
  echo
  printf -v "$var" '%s' "${ans:-$def}"
}
yesno() {        # yesno VAR "Question" yes|no
  local var=$1 q=$2 def=$3 ans
  if [ "$NONINTERACTIVE" = yes ]; then printf -v "$var" '%s' "$def"; return; fi
  while true; do
    read -r -p "$q [$([ "$def" = yes ] && echo Y/n || echo y/N)]: " ans || true
    case "${ans:-$def}" in
      [Yy]*) printf -v "$var" yes; return ;;
      [Nn]*) printf -v "$var" no; return ;;
    esac
  done
}

echo
bold "PlexPie setup"
dim  "Press Enter to accept the value in [brackets]."
echo

# ------------------------------------------------------------------ basics
DEF_USER="${APPLIANCE_USER:-${SUDO_USER:-$(id -un)}}"
[ "$DEF_USER" = root ] && DEF_USER=$(getent passwd 1000 | cut -d: -f1 || echo pi)
while :; do
  ask APPLIANCE_USER "Linux user that runs the screen (logged in automatically)" "$DEF_USER"
  id "$APPLIANCE_USER" >/dev/null 2>&1 && break
  echo "  There's no user called '$APPLIANCE_USER' on this Pi. Press Enter to use '$DEF_USER' (the one you're logged in as)."
  [ "$NONINTERACTIVE" = yes ] && { APPLIANCE_USER=$DEF_USER; break; }
done
ask PLAYER_NAME "Player name (shown in Plexamp, AirPlay and Music Assistant)" "${PLAYER_NAME:-PlexPie}"
AIRPLAY_NAME="${AIRPLAY_NAME:-$PLAYER_NAME}"

# ------------------------------------------------------------------- audio
echo
bold "Audio output"
if [ -r /proc/asound/cards ]; then
  usb=$(grep -A1 -E '^ ?[0-9]+ \[' /proc/asound/cards | grep -i ' usb' | sed 's/^ *//' | head -3 || true)
  if [ -n "$usb" ]; then
    echo "Found USB audio: $usb"
    echo "It will be used automatically (USB DACs are preferred over HDMI and the headphone jack)."
  else
    echo "No USB DAC found right now. PlexPie will use HDMI or the headphone jack,"
    echo "and switch to a USB DAC automatically if you plug one in later."
  fi
fi

# ----------------------------------------------------------------- sources
echo
bold "Sources"
dim "Plexamp is always included (needs a Plex account; some features need Plex Pass)."
yesno ENABLE_AIRPLAY "Add an AirPlay 2 receiver? (builds from source, about 10 minutes)" "${ENABLE_AIRPLAY:-yes}"
yesno ENABLE_APPLE_MUSIC_WEB "Add the Apple Music web player on the screen? (needs a subscription)" "${ENABLE_APPLE_MUSIC_WEB:-yes}"
yesno ENABLE_SPOTIFY "Add a Spotify Connect receiver? (needs Spotify Premium)" "${ENABLE_SPOTIFY:-no}"
ENABLE_SPOTIFY_WEB="${ENABLE_SPOTIFY_WEB:-no}"
[ "$ENABLE_SPOTIFY" = yes ] && yesno ENABLE_SPOTIFY_WEB "Also add the Spotify web player on the screen?" "$ENABLE_SPOTIFY_WEB"
yesno ENABLE_BLUETOOTH "Let any phone, tablet or computer play to it over Bluetooth?" "${ENABLE_BLUETOOTH:-yes}"
yesno ENABLE_MUSIC_ASSISTANT "Add Music Assistant (needs a Music Assistant server)?" "${ENABLE_MUSIC_ASSISTANT:-no}"
if [ "$ENABLE_MUSIC_ASSISTANT" = yes ]; then
  ask MA_HOST "Music Assistant server address (IP or hostname)" "${MA_HOST:-${HA_HOST:-homeassistant.local}}"
  ask MA_URL "Music Assistant web address" "${MA_URL:-http://$MA_HOST:8095}"
else
  MA_HOST="${MA_HOST:-}"; MA_URL="${MA_URL:-}"
fi
dim "Caldera Music (Plex Labs) plays in sync with other Caldera speakers and shows up in Plexamp's speaker list."
yesno ENABLE_CALDERA "Add Caldera Music (multi-room with other Caldera speakers)?" "${ENABLE_CALDERA:-no}"
yesno ENABLE_YOUTUBE_MUSIC "Add the YouTube Music web player on the screen?" "${ENABLE_YOUTUBE_MUSIC:-no}"
yesno ENABLE_BBC_SOUNDS "Add BBC Sounds (BBC radio and podcasts) on the screen?" "${ENABLE_BBC_SOUNDS:-no}"
yesno ENABLE_TIDAL "Add the TIDAL web player on the screen?" "${ENABLE_TIDAL:-no}"
yesno ENABLE_QOBUZ "Add the Qobuz web player on the screen?" "${ENABLE_QOBUZ:-no}"
echo
dim "Your own server and other systems (skip the ones you don't use):"
yesno ENABLE_JELLYFIN "Add Jellyfin (your Jellyfin server's music on the screen)?" "${ENABLE_JELLYFIN:-no}"
if [ "$ENABLE_JELLYFIN" = yes ]; then
  ask JELLYFIN_URL "Jellyfin server address (e.g. 192.168.1.20 or 192.168.1.20:8096)" "${JELLYFIN_URL:-}"
else
  JELLYFIN_URL="${JELLYFIN_URL:-}"
fi
yesno ENABLE_DLNA "Add a UPnP/DLNA speaker (BubbleUPnP, mconnect, NAS and Jellyfin apps can play to it)?" "${ENABLE_DLNA:-no}"
yesno ENABLE_ROON "Add Roon Bridge (a speaker in Roon; needs a Roon subscription)?" "${ENABLE_ROON:-no}"
yesno ENABLE_SNAPCAST "Add a Snapcast speaker (multi-room with a Snapcast server)?" "${ENABLE_SNAPCAST:-no}"
if [ "$ENABLE_SNAPCAST" = yes ]; then
  ask SNAPCAST_HOST "Snapcast server address (empty = find it on the network)" "${SNAPCAST_HOST:-}"
else
  SNAPCAST_HOST="${SNAPCAST_HOST:-}"
fi

# --------------------------------------------------------- home assistant
echo
bold "Home Assistant (optional)"
dim "With an MQTT broker (e.g. the Mosquitto add-on), the Pi appears in Home Assistant"
dim "as a device: source, now playing, screen, volume, pause and reboot."
DEF_HA=no; [ -n "${MQTT_HOST:-}" ] && DEF_HA=yes
yesno USE_HA "Connect to Home Assistant over MQTT?" "$DEF_HA"
if [ "$USE_HA" = yes ]; then
  ask MQTT_HOST "MQTT broker address" "${MQTT_HOST:-${MA_HOST:-homeassistant.local}}"
  ask MQTT_PORT "MQTT port" "${MQTT_PORT:-1883}"
  ask MQTT_USER "MQTT username (empty if none)" "${MQTT_USER:-}"
  ask_secret MQTT_PASS "MQTT password" "${MQTT_PASS:-}"
  HA_HOST="${HA_HOST:-$MQTT_HOST}"
else
  MQTT_HOST=""; MQTT_PORT="${MQTT_PORT:-1883}"; MQTT_USER="${MQTT_USER:-}"; MQTT_PASS="${MQTT_PASS:-}"
  HA_HOST="${HA_HOST:-}"
fi

# ------------------------------------------------------------ away from home
echo
bold "Away from home (optional)"
dim "Tailscale lets your own phone reach this player from anywhere, with no ports opened on"
dim "your router. Free for personal use; you sign in on the screen or your phone afterwards."
yesno ENABLE_TAILSCALE "Control this player from anywhere with Tailscale?" "${ENABLE_TAILSCALE:-no}"

# ------------------------------------------------------------------ screen
echo
bold "Screen"
ask SCREEN_OFF_AFTER_SECS "Turn the screen off after how many idle seconds? (0 = never)" "${SCREEN_OFF_AFTER_SECS:-60}"

cat > "$CONF" <<EOF
# PlexPie settings, written by configure.sh on $(date '+%F').
# Edit here or run configure.sh again, then: sudo bash install.sh
# Keep comments on their own lines (systemd reads this file too).

# Linux user that runs everything (autologin on the touchscreen)
APPLIANCE_USER="$APPLIANCE_USER"
# First-run wizard on the screen (yes after a quick install; it switches itself off when done)
SETUP_WIZARD="${SETUP_WIZARD:-no}"

# Name shown in Plexamp, AirPlay and Music Assistant
PLAYER_NAME="$PLAYER_NAME"
AIRPLAY_NAME="$AIRPLAY_NAME"

# Sources: yes / no
ENABLE_AIRPLAY="$ENABLE_AIRPLAY"
ENABLE_APPLE_MUSIC_WEB="$ENABLE_APPLE_MUSIC_WEB"
ENABLE_MUSIC_ASSISTANT="$ENABLE_MUSIC_ASSISTANT"
ENABLE_SPOTIFY="$ENABLE_SPOTIFY"
ENABLE_SPOTIFY_WEB="$ENABLE_SPOTIFY_WEB"
# Caldera Music (Plex Labs): multi-room with other Caldera speakers; shows up in Plexamp's speaker list
ENABLE_CALDERA="$ENABLE_CALDERA"
# Web players on the screen, like Apple Music (sign in on the screen)
ENABLE_YOUTUBE_MUSIC="${ENABLE_YOUTUBE_MUSIC:-no}"
ENABLE_BBC_SOUNDS="${ENABLE_BBC_SOUNDS:-no}"
ENABLE_TIDAL="${ENABLE_TIDAL:-no}"
ENABLE_QOBUZ="${ENABLE_QOBUZ:-no}"
# Jellyfin: your server's music on the screen (address like 192.168.1.20 or 192.168.1.20:8096)
ENABLE_JELLYFIN="${ENABLE_JELLYFIN:-no}"
JELLYFIN_URL="${JELLYFIN_URL:-}"
# Speakers for other systems: UPnP/DLNA renderer, Roon Bridge (Roon Ready), Snapcast client
ENABLE_DLNA="${ENABLE_DLNA:-no}"
ENABLE_ROON="${ENABLE_ROON:-no}"
ENABLE_SNAPCAST="${ENABLE_SNAPCAST:-no}"
# Snapcast server address (empty = find it on the network)
SNAPCAST_HOST="${SNAPCAST_HOST:-}"
# Where Caldera plays: auto = shared with the other sources, caldera = Caldera's own pick (often the DAC alone)
CALDERA_DEVICE="${CALDERA_DEVICE:-auto}"
# A push button between pins 5 and 6 (GPIO3 and ground) shuts the Pi down and starts it again
# (leave "no" if a DAC HAT or other add-on board uses those pins)
POWER_BUTTON="${POWER_BUTTON:-no}"
# Bluetooth receiver: phones, tablets and computers pair from the Bluetooth screen ("Pair a device")
ENABLE_BLUETOOTH="$ENABLE_BLUETOOTH"
# Equalizer (Settings -> Sound): off plays music untouched
EQ_ENABLED="${EQ_ENABLED:-no}"
EQ_PRESET="${EQ_PRESET:-flat}"
EQ_BANDS="${EQ_BANDS:-}"
EQ_CUSTOM="${EQ_CUSTOM:-}"
# Rotary knob (KY-040 style): turn for volume, press to pause/play, hold to go home.
# Wiring: A -> pin 11, B -> pin 13, push -> pin 15, + -> pin 1 (3.3 V), GND -> pin 9. Reboot after changing.
KNOB="${KNOB:-no}"
KNOB_STEP="${KNOB_STEP:-2}"
# Visualizer on the now-playing screens: bars, vu or off
VISUALIZER="${VISUALIZER:-bars}"
# Color the screens from the album art
THEME_FROM_ART="${THEME_FROM_ART:-yes}"
# Dim the screen at night (24-hour times) and how bright it is then (percent)
NIGHT_DIM="${NIGHT_DIM:-no}"
NIGHT_START="${NIGHT_START:-22:00}"
NIGHT_END="${NIGHT_END:-07:00}"
NIGHT_BRIGHTNESS="${NIGHT_BRIGHTNESS:-20}"
# Follow a Home Assistant light sensor (send lux to the "Ambient light" entity)
AUTO_BRIGHTNESS="${AUTO_BRIGHTNESS:-no}"
# Home Assistant tile on the home screen (needs HA_HOST or HA_URL)
ENABLE_HA_DASHBOARD="${ENABLE_HA_DASHBOARD:-yes}"
# Which dashboard the tile opens, e.g. "gym-screen" (empty = your main dashboard)
HA_DASHBOARD="${HA_DASHBOARD:-}"
# Workout timer tile on the launcher
ENABLE_TIMER="${ENABLE_TIMER:-yes}"
# Remote access: Settings from any phone, tablet or computer at http://<pi>:8080/settings (PIN shown on the player)
PHONE_ACCESS="${PHONE_ACCESS:-yes}"
# Away from home: Tailscale (only your own devices can reach the player; sign in from Settings -> Remote access)
ENABLE_TAILSCALE="${ENABLE_TAILSCALE:-no}"

# Music Assistant server (Squeezelite connects here) and its web interface
MA_HOST="$MA_HOST"
MA_URL="$MA_URL"

# Home Assistant over MQTT (MQTT_HOST empty = off)
HA_HOST="$HA_HOST"
MQTT_HOST="$MQTT_HOST"
MQTT_PORT="$MQTT_PORT"
MQTT_USER="$MQTT_USER"
MQTT_PASS="$MQTT_PASS"
# Launcher buttons, comma separated (e.g. "Gym lights"): each becomes a switch in Home Assistant
SHORTCUTS="${SHORTCUTS:-}"
# Home Assistant web address, for camera pictures in screen alerts (empty = http://HA_HOST:8123)
HA_URL="${HA_URL:-}"
# Extra topic with payload playing / idle, for older automations (empty = off)
MQTT_LEGACY_TOPIC="${MQTT_LEGACY_TOPIC:-}"

# Screen: off after this many seconds with no touch and nothing playing (0 = never)
SCREEN_OFF_AFTER_SECS="$SCREEN_OFF_AFTER_SECS"
# Brightness in percent
SCREEN_BRIGHTNESS="${SCREEN_BRIGHTNESS:-100}"
# Browser zoom for the apps (1 = normal)
CHROMIUM_SCALE="${CHROMIUM_SCALE:-1}"
# Apple Music web player zoom in percent (75 shows the full desktop layout; 100 = normal)
APPLE_MUSIC_ZOOM="${APPLE_MUSIC_ZOOM:-75}"

# Swipe right from the left edge, or down from the top edge, to go home
SWIPE_HOME="${SWIPE_HOME:-yes}"
# Screen rotation: auto (follow the Raspberry Pi desktop's setting), normal, right, inverted or left.
# The Pi Touch Display 2 is portrait out of the box: right or left makes it landscape. Also in Settings > Screen.
SCREEN_ROTATION="${SCREEN_ROTATION:-auto}"
# Start-up screen: the PlexPie logo instead of Raspberry Pi boot text (no = show the text, for troubleshooting)
BOOT_SPLASH="${BOOT_SPLASH:-yes}"
# Only if swipes still go the wrong way after rotating: extra touch rotation in degrees (0, 90, 180, 270)
TOUCH_ROTATE="${TOUCH_ROTATE:-0}"
# Starting one source pauses the others
PAUSE_OTHERS="${PAUSE_OTHERS:-yes}"
# When something starts playing, bring that app to the screen (plexamp airplay ma apple)
AUTO_SHOW_SOURCES="${AUTO_SHOW_SOURCES:-plexamp airplay ma spotify}"
AUTO_SHOW_AIRPLAY="${AUTO_SHOW_AIRPLAY:-yes}"

# Audio device PipeWire prefers (regex on the PipeWire node name; default: any USB DAC)
DAC_MATCH="${DAC_MATCH:-alsa_output.usb-.*}"
# Plexamp headless version to install when /opt/plexamp is missing
PLEXAMP_VERSION="${PLEXAMP_VERSION:-4.12.4}"
EOF
chmod 600 "$CONF"
[ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER" "$CONF" 2>/dev/null || true

echo
bold "Saved $CONF"
echo "Next: sudo bash $DIR/install.sh"
