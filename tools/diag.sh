#!/bin/bash
# PlexPie diagnostics: prints what's needed to troubleshoot the screen. Changes nothing.
# Run from another computer:  curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/main/tools/diag.sh | ssh USER@PI bash
echo "== PlexPie $(sed -n 's/^VERSION = "\(.*\)"/\1/p' /opt/plexpie/plexpied.py) on $(hostname), up $(uptime -p)"
echo "== chromium: $(chromium --version 2>/dev/null)"
echo "== screen"; DISPLAY=:0 xrandr 2>/dev/null | grep -E "Screen|\*" | head -4
grep -E "^(CHROMIUM_SCALE|APPLE_MUSIC_ZOOM|TOUCH_ROTATE|SCREEN_ROTATE)" /etc/plexpie/plexpie.conf 2>/dev/null
echo "== chromium window"; DISPLAY=:0 xwininfo -root -tree 2>/dev/null | grep -i chrom | head -3
echo "== pages (url | innerW x innerH | screenW x screenH | dpr)"
python3 - <<'PY'
import json, urllib.request
try:
    import websocket
    tabs = [t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)) if t.get("type") == "page"]
    for t in tabs:
        try:
            w = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=3, suppress_origin=True)
            w.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"returnByValue": True,
                "expression": "[innerWidth, innerHeight, screen.width, screen.height, devicePixelRatio, 'zoom', (window.visualViewport||{}).scale, '|', document.readyState, JSON.stringify(document.title), 'text', (document.body ? document.body.innerText.length : -1), 'nodes', document.getElementsByTagName('*').length].join(' ')"}}))
            while True:
                m = json.loads(w.recv())
                if m.get("id") == 1:
                    break
            print(t["url"][:70], "|", m.get("result", {}).get("result", {}).get("value"))
            w.close()
        except Exception as e:
            print(t["url"][:70], "| error", e)
except Exception as e:
    print("devtools not reachable:", e)
PY
echo "== bluetooth"; cat /run/plexpie/bt.json 2>/dev/null; echo
echo "== packages upgraded today"; grep " upgrade " /var/log/dpkg.log 2>/dev/null | grep "$(date +%F)" | awk '{print $4}' | tr '\n' ' '; echo
echo "== update log"; tail -4 /var/log/plexpie-update.log 2>/dev/null
echo "== plexpie log"; journalctl -u plexpie -n 12 --no-pager -o cat 2>/dev/null | cut -c1-160
echo "== plexamp web"; curl -s -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" --max-time 5 http://127.0.0.1:32500/
echo "== plexamp log"; journalctl -u plexamp -n 25 --no-pager -o cat 2>/dev/null | cut -c1-160
echo "== session log"; grep -av DEPRECATED_ENDPOINT ~/.local/state/plexpie/session.log | tail -12 2>/dev/null
