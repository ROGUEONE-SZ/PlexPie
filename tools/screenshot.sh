#!/bin/bash
# Saves a picture of what a PlexPie screen tab shows (PNG on stdout) and prints Plexamp's playback
# state (on stderr). Changes nothing. From another computer:
#   curl -fsSL .../tools/screenshot.sh | ssh USER@PI bash -s plexamp > plexpie-screen.png
# The argument picks the tab: plexamp (default), home, settings, airplay, bluetooth...
python3 - "${1:-plexamp}" <<'PY'
import base64, json, re, sys, urllib.request, websocket
want = sys.argv[1]
def err(*a): print(*a, file=sys.stderr)
# Plexamp's own view of what it's playing
try:
    req = urllib.request.Request("http://127.0.0.1:32500/player/timeline/poll?wait=0&includeMetadata=1&type=music&commandID=1",
                                 headers={"X-Plex-Client-Identifier": "plexpie-check", "X-Plex-Product": "PlexPie"})
    xml = urllib.request.urlopen(req, timeout=4).read().decode(errors="replace")
    xml = re.sub(r'(token|Token|accessToken)="[^"]*"', r'\1="(hidden)"', xml)
    for tag in re.findall(r"<Timeline [^>]*>", xml):
        if 'type="music"' in tag:
            keep = " ".join(m for m in re.findall(r'\b(state|time|duration|machineIdentifier|address|port|protocol)="[^"]*"', tag) if True)
            err("timeline:", " ".join(re.findall(r'\b(?:state|time|duration|address|port|protocol|machineIdentifier)="[^"]*"', tag)))
    t = re.search(r'<Track [^>]*>', xml)
    err("track:", " ".join(re.findall(r'\b(?:title|grandparentTitle|parentTitle|thumb)="[^"]*"', t.group(0))) if t else "none")
except Exception as e:
    err("timeline: error", e)
# picture of the tab
tabs = [t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3)) if t.get("type") == "page"]
match = {"plexamp": ":32500", "home": ":8080/", "settings": "/settings"}.get(want, want)
tab = next((t for t in tabs if match in t.get("url", "") and (want != "home" or t["url"].rstrip("/").endswith(":8080"))), None)
if not tab:
    err("no tab for", want, [t["url"].split("?")[0] for t in tabs]); sys.exit(1)
w = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=8, suppress_origin=True)
w.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
while True:
    m = json.loads(w.recv())
    if m.get("id") == 1:
        break
sys.stdout.buffer.write(base64.b64decode(m["result"]["data"]))
err("saved a picture of", tab["url"].split("?")[0])
PY
