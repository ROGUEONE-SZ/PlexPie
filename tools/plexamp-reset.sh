#!/bin/bash
# Shows which player addresses the Plexamp screen has remembered, then (with "fix") clears what the
# screen's browser saved for the Plexamp page and reloads it, so it finds the player at its current
# address. Your Plexamp app, library, playlists and Plex sign-in on the player are not touched.
#   curl -fsSL .../tools/plexamp-reset.sh | ssh USER@PI bash -s fix
python3 - "${1:-}" <<'PY'
import json, os, re, sys, time, urllib.request, websocket
fix, undo = sys.argv[1] == "fix", sys.argv[1] == "undo"
BACKUP = os.path.expanduser("~/.config/plexpie/plexamp-screen-backup.json")
tabs = [t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3))
        if t.get("type") == "page" and ":32500" in t.get("url", "")]
if not tabs:
    print("The Plexamp screen isn't open: tap Plexamp on the screen, then run this again."); sys.exit(1)
w = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=8, suppress_origin=True)
n = 0
def call(method, params=None):
    global n; n += 1; w.send(json.dumps({"id": n, "method": method, "params": params or {}}))
    while True:
        m = json.loads(w.recv())
        if m.get("id") == n:
            return m.get("result", {})
if undo:
    data = open(BACKUP).read()
    call("Runtime.evaluate", {"expression": "(d => { localStorage.clear(); for (const [k, v] of Object.entries(d)) localStorage.setItem(k, v); })(" + data + ")"})
    call("Page.reload", {})
    print("put back the saved Plexamp screen data from", BACKUP); sys.exit(0)
r = call("Runtime.evaluate", {"returnByValue": True, "expression":
    "JSON.stringify(Object.keys(localStorage).map(k => localStorage.getItem(k)))"})
blob = (r.get("result") or {}).get("value") or ""
addrs = sorted(set(re.findall(r"(?:https?://)?(?:\d{1,3}[.-]){3}\d{1,3}(?:\.[0-9a-f]{32}\.plex\.direct)?:\d{2,5}", blob)))
print("player/server addresses the Plexamp screen remembers:")
for a in addrs:
    print("  ", a)
import subprocess
mine = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()
print("this player's address now:", " ".join(a for a in mine if "." in a))
if not fix:
    print("(run again with 'fix' to clear and reload)"); sys.exit(0)
r2 = call("Runtime.evaluate", {"returnByValue": True, "expression": "JSON.stringify(Object.fromEntries(Object.entries(localStorage)))"})
os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
with open(BACKUP, "w") as f:
    f.write((r2.get("result") or {}).get("value") or "{}")
os.chmod(BACKUP, 0o600)
print("saved a copy first (undo with: bash -s undo)")
call("Storage.clearDataForOrigin", {"origin": "http://127.0.0.1:32500", "storageTypes": "local_storage,indexeddb,cache_storage,service_workers"})
call("Page.reload", {"ignoreCache": True})
print("cleared the Plexamp screen's saved data and reloaded it")
PY
