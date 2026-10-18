#!/bin/bash
# PlexPie diagnostics: read-only report of audio, AirPlay and the daemon.
# Plays a short test tone (left/right voice) through the DAC at the end.
# Usage on the Pi: bash ~/plexpie/tools/diagnose.sh
U="${SUDO_USER:-$USER}"
AUID=$(id -u "$U")
export XDG_RUNTIME_DIR="/run/user/$AUID"
sec() { echo; echo "===== $* ====="; }
# Never print sign-in tokens or passwords, so the report is safe to paste into a GitHub issue.
redact() { sed -E 's/(X-Plex-Token=|[Tt]oken["=: ]+|authToken["=: ]+)[A-Za-z0-9_.~-]{6,}/\1(hidden)/g; s/((password|passwd|psk|secret)["=: ]+)[^ ",]+/\1(hidden)/Ig'; }
{

sec VERSIONS
/usr/local/bin/shairport-sync -V 2>&1
nqptp -V 2>&1 | head -1
sec SERVICES
for u in plexamp plexpie plexpie-airplay plexpie-squeezelite plexpie-caldera nqptp; do printf '%-22s %s\n' "$u" "$(systemctl is-active "$u")"; done
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
journalctl -u plexamp -n 120 --no-pager 2>&1
if systemctl cat plexpie-caldera >/dev/null 2>&1; then
sec CALDERA MUSIC
echo "output: $(cat "$HOME/.config/plexpie/caldera-device" 2>/dev/null || echo '(not started yet)')"
echo "signed in: $([ -f "$HOME/.config/plexpie/caldera-linked" ] && echo yes || echo no)"
echo "devices:"; sed 's/^/  /' "$HOME/.config/plexpie/caldera-devices.txt" 2>/dev/null | head -20
ss -ltnpH 2>/dev/null | grep -i caldera
journalctl -u plexpie-caldera -n 40 --no-pager 2>&1
fi
sec PLEXAMP AUDIO SETTINGS
for f in "$HOME"/.local/share/Plexamp/Settings/*; do
  case "$f" in *udio*|*utput*|*evice*|*xclusive*|*ample*|*layback*) echo "$(basename "$f") = $(head -c 300 "$f")";; esac
done
ls "$HOME/.local/share/Plexamp/Settings/" | tr '\n' ' '; echo
fuser -v /dev/snd/* 2>&1 | head -20
sec SHAIRPORT CONFIG
cat /etc/plexpie/shairport-sync.conf
sec AIRPLAY NOW-PLAYING DATA
if [ -f /tmp/plexpie-copl-last.bin ]; then
python3 - <<'PY'
import plistlib
raw = open("/tmp/plexpie-copl-last.bin", "rb").read()
print("bytes:", len(raw), "starts:", raw[:16])
def show(v, ind=0, depth=0):
    if depth > 6: return
    pad = "  " * ind
    if isinstance(v, dict):
        for k, x in list(v.items())[:60]:
            if isinstance(x, (dict, list)):
                print(pad + str(k) + ":"); show(x, ind + 1, depth + 1)
            elif isinstance(x, (bytes, bytearray)):
                print(pad + str(k) + ": <%d bytes %r>" % (len(x), bytes(x[:8])))
            else:
                print(pad + str(k) + ": " + str(x)[:80])
    elif isinstance(v, list):
        for x in v[:20]: show(x, ind, depth + 1)
try:
    show(plistlib.loads(raw))
except Exception as e:
    print("not a plist:", e)
PY
else
  echo "(none saved yet - play something via AirPlay first)"
fi
sec TEST TONE
echo "Playing a 'front left / front right' voice through the default output now."
timeout 15 speaker-test -D default -c 2 -t wav -l 1 2>&1 | tail -5
echo "speaker-test exit: $?"
echo; echo "===== DONE ====="
} 2>&1 | redact
