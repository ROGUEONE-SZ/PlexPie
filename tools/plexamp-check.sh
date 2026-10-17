#!/bin/bash
# Reloads the Plexamp page on the screen and reports why it stays blank (errors, failed downloads).
echo "== what PlexPie sees playing"
curl -s --max-time 3 http://127.0.0.1:8080/api/state | python3 -c 'import json,sys; s=json.load(sys.stdin); print("source:", s.get("source"), "| playing:", s.get("playing"), "|", s.get("np", {}).get("plexamp") or s.get("now_playing"))' 2>&1 | cut -c1-300
echo "== Plex server names (should be 192.168.x addresses, not blank)"
for h in $(journalctl -u plexamp -n 400 --no-pager -o cat 2>/dev/null | grep -oE '[0-9a-z-]+\.[0-9a-f]{32}\.plex\.direct' | sort -u | head -4); do
  echo "$h -> $(getent hosts "$h" | awk '{print $1}')"
done
echo "== Plexamp page files"
html=$(curl -s --max-time 5 http://127.0.0.1:32500/)
echo "index: ${#html} bytes"
for src in $(printf '%s' "$html" | grep -oE '(src|href)="[^"]+\.(js|css)"' | cut -d'"' -f2 | head -8); do
  case "$src" in http*) u=$src ;; /*) u="http://127.0.0.1:32500$src" ;; *) u="http://127.0.0.1:32500/$src" ;; esac
  echo "$(curl -s -o /dev/null -w '%{http_code} %{size_download}B' --max-time 10 "$u")  $src"
done
echo "== reloading the Plexamp page and listening for 12 seconds"
python3 - <<'PY'
import json, re, time, urllib.request, websocket
tabs = [t for t in json.load(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3))
        if t.get("type") == "page" and ":32500" in t.get("url", "")]
if not tabs:
    print("no Plexamp tab open"); raise SystemExit
w = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=2, suppress_origin=True)
n = 0
def send(m, p=None):
    global n; n += 1; w.send(json.dumps({"id": n, "method": m, "params": p or {}}))
for m in ("Runtime.enable", "Log.enable", "Network.enable"):
    send(m)
send("Page.reload", {"ignoreCache": True})
imgs = {"ok": 0, "bad": 0}
reqs, seen, end = {}, set(), time.time() + 12
while time.time() < end:
    try:
        m = json.loads(w.recv())
    except Exception:
        continue
    meth, p = m.get("method"), m.get("params", {})
    out = None
    if meth == "Runtime.exceptionThrown":
        d = p["exceptionDetails"]; out = "EXCEPTION " + (d.get("exception", {}).get("description") or d.get("text", ""))[:400]
    elif meth == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning", "assert"):
        out = p["type"].upper() + " " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", []))[:400]
    elif meth == "Log.entryAdded" and p["entry"].get("level") in ("error", "warning"):
        e = p["entry"]; out = "LOG " + e.get("text", "")[:300] + " " + e.get("url", "")[:120]
    elif meth == "Network.requestWillBeSent":
        reqs[p["requestId"]] = p["request"]["url"]
    elif meth == "Network.loadingFailed":
        out = "FAILED " + p.get("errorText", "") + " " + reqs.get(p["requestId"], "")[:150]
    elif meth == "Network.responseReceived" and p.get("type") == "Image":
        imgs["ok" if p["response"].get("status", 200) < 400 else "bad"] += 1
        if p["response"].get("status", 200) >= 400:
            out = f"IMAGE HTTP {p['response']['status']} " + p["response"].get("url", "")[:150]
    elif meth == "Network.responseReceived" and p["response"].get("status", 200) >= 400:
        out = f"HTTP {p['response']['status']} " + p["response"].get("url", "")[:150]
    if out and out not in seen:
        out = re.sub(r"(X-Plex-Token=)[^&\s]+", r"\1(hidden)", out)
        seen.add(out); print(out)
send("Runtime.evaluate", {"expression": "document.body ? document.body.innerText.length + ' chars, ' + document.getElementsByTagName('*').length + ' elements' : 'no body'", "returnByValue": True})
end = time.time() + 3
while time.time() < end:
    m = json.loads(w.recv())
    if m.get("id") == n:
        print("after reload:", m.get("result", {}).get("result", {}).get("value")); break
print(f"({len(reqs)} requests, images loaded: {imgs['ok']}, images failed: {imgs['bad']})")
for u in sorted(set(v for v in reqs.values() if "plex.direct" in v or ":32400" in v))[:6]:
    print("server request:", u.split("?")[0][:140])      # no query strings: they carry the Plex token
PY
echo "== plexamp log"; journalctl -u plexamp -n 40 --no-pager -o cat | cut -c1-200
