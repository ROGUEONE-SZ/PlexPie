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
if [ "$(uname -m)" != "aarch64" ]; then
  echo "PlexPie needs a 64-bit Raspberry Pi OS (aarch64); this system is $(uname -m)."
  exit 1
fi

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
if [ ! -f "$DEST/plexpie.conf" ]; then
  echo "==> Setup"
  if [ -e /dev/tty ]; then
    bash "$DEST/configure.sh" </dev/tty
  else
    bash "$DEST/configure.sh" --defaults
    echo "No terminal: wrote default settings. Edit $DEST/plexpie.conf, then run: sudo bash $DEST/install.sh"
    exit 0
  fi
fi

echo "==> Installing (this takes a while the first time)"
if [ -e /dev/tty ]; then
  sudo bash "$DEST/install.sh" </dev/tty
else
  sudo bash "$DEST/install.sh"
fi
