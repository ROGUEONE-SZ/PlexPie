#!/bin/bash
# PlexPie one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/PlexPie/main/get.sh | bash
#
# Downloads PlexPie to ~/plexpie (or updates it), runs the setup wizard the
# first time, then runs the installer. Run it as your normal user, not root.
set -euo pipefail

REPO_URL="${PLEXPIE_REPO:-https://github.com/ROGUEONE-SZ/PlexPie}"
BRANCH="${PLEXPIE_BRANCH:-main}"
DEST="${PLEXPIE_DIR:-$HOME/plexpie}"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the user who will use the screen (e.g. 'pi'), not as root."
  echo "It will ask for your password when it needs sudo."
  exit 1
fi
case "$(uname -m)" in
  aarch64) ;;                                                   # Raspberry Pi 4/5 and 64-bit ARM boards
  x86_64)  echo "Intel/AMD PC detected: PC support is in beta (please report how it goes on GitHub)." ;;
  armv7l)  echo "32-bit ARM detected: community supported, and some apps need a 64-bit system." ;;
  *) echo "PlexPie runs on 64-bit Raspberry Pi OS, Debian, Ubuntu or Armbian; this system is $(uname -m)."; exit 1 ;;
esac
command -v apt-get >/dev/null || { echo "PlexPie needs a Debian-based system (Raspberry Pi OS, Debian, Ubuntu, Armbian)."; exit 1; }

echo "==> Getting PlexPie"
if ! command -v git >/dev/null; then
  sudo apt-get update -q && sudo apt-get install -y -q git
fi
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull -q --ff-only
else
  git clone -q --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

# A piped "curl | bash" has no keyboard on stdin; give the wizard the terminal.
QUICK=""
if [ ! -f "$DEST/plexpie.conf" ]; then
  echo "==> Setup"
  if [ -e /dev/tty ]; then
    echo
    echo "  1) Quick setup (recommended): install now, then finish on the screen or your phone"
    echo "  2) Answer the setup questions here in the terminal"
    read -r -p "Choose [1]: " choice </dev/tty || choice=1
    if [ "${choice:-1}" = 2 ]; then
      bash "$DEST/configure.sh" </dev/tty
    else
      bash "$DEST/configure.sh" --defaults
      sed -i 's/^SETUP_WIZARD=.*/SETUP_WIZARD="yes"/' "$DEST/plexpie.conf"
      QUICK=1
    fi
  else
    bash "$DEST/configure.sh" --defaults
    echo "No terminal: wrote default settings. Edit $DEST/plexpie.conf, then run: sudo bash $DEST/install.sh"
    exit 0
  fi
fi

echo "==> Installing (this takes a while the first time)"
if [ -e /dev/tty ]; then
  sudo PLEXPIE_QUICK="$QUICK" bash "$DEST/install.sh" </dev/tty
else
  sudo PLEXPIE_QUICK="$QUICK" bash "$DEST/install.sh"
fi
if [ -n "$QUICK" ]; then
  echo "Done. Finish the setup on the screen, or on your phone at http://$(hostname -I | awk '{print $1}'):8080/welcome"
fi
