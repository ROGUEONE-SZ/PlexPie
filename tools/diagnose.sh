#!/bin/bash
# PlexPie diagnostics: read-only report of audio, AirPlay and the daemon.
# Plays a short test tone (left/right voice) through the DAC at the end.
# Usage on the Pi: bash ~/plexpie/tools/diagnose.sh
U="${SUDO_USER:-$USER}"
AUID=$(id -u "$U")
export XDG_RUNTIME_DIR="/run/user/$AUID"
sec() { echo; echo "===== $* ====="; }

sec VERSIONS
/usr/local/bin/shairport-sync -V 2>&1
nqptp -V 2>&1 | head -1
sec SERVICES
for u in plexamp plexpie plexpie-airplay plexpie-squeezelite nqptp; do printf '%-22s %s\n' "$u" "$(systemctl is-active "$u")"; done
systemctl --user is-active pipewire pipewire-pulse wireplumber 2>&1 | paste -sd' '
loginctl show-user "$U" -p Linger 2>&1
sec PIPEWIRE
wpctl status 2>&1 | sed -n '/Audio/,/Video/p'
wpctl inspect @DEFAULT_AUDIO_SINK@ 2>&1 | grep -E 'node.name|node.description|api.alsa.card.name'
sec ALSA
cat /etc/asound.conf
aplay -L 2>&1 | grep -E '^(default|pipewire|hw:|plughw:)' | head -20
ls /usr/lib/*/alsa-lib/ 2>/dev/null | grep -i pipewire
sec AIRPLAY LOG
journalctl -u plexpie-airplay -n 60 --no-pager 2>&1
sec NQPTP LOG
journalctl -u nqptp -n 15 --no-pager 2>&1
sec PLEXPIE LOG
journalctl -u plexpie -n 80 --no-pager 2>&1 | grep -v "GET /api"
sec PLEXAMP LOG
journalctl -u plexamp -n 15 --no-pager 2>&1
sec SHAIRPORT CONFIG
cat /etc/plexpie/shairport-sync.conf
sec TEST TONE
echo "Playing a 'front left / front right' voice through the default output now."
timeout 15 speaker-test -D default -c 2 -t wav -l 1 2>&1 | tail -5
echo "speaker-test exit: $?"
echo; echo "===== DONE ====="
