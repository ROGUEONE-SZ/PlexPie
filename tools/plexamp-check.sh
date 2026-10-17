#!/bin/bash
# Reloads the Plexamp page on the screen and reports why it stays blank (errors, failed downloads).
echo "== Plexamp page files"
html=$(curl -s --max-time 5 http://127.0.0.1:32500/)
echo "index: ${#html} bytes"
for src in $(printf '%s' "$html" | grep -oE '(src|href)="[^"]+\.(js|css)"' | cut -d'"' -f2 | head -8); do
  case "$src" in http*) u=$src ;; /*) u="http://127.0.0.1:32500$src" ;; *) u="http://127.0.0.1:32500/$src" ;; esac
  echo "$(curl -s -o /dev/null -w '%{http_code} %{size_download}B' --max-time 10 "$u")  $src"
done
echo "== reloading the Plexamp page and listening for 12 seconds"
python3 - <<'PY'
import json, time, urllib.request, websocket
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
    elif meth == "Network.responseReceived" and p["response"].get("status", 200) >= 400:
        out = f"HTTP {p['response']['status']} " + p["response"].get("url", "")[:150]
    if out and out not in seen:
        seen.add(out); print(out)
send("Runtime.evaluate", {"expression": "document.body ? document.body.innerText.length + ' chars, ' + document.getElementsByTagName('*').length + ' elements' : 'no body'", "returnByValue": True})
end = time.time() + 3
while time.time() < end:
    m = json.loads(w.recv())
    if m.get("id") == n:
        print("after reload:", m.get("result", {}).get("result", {}).get("value")); break
print(f"({len(reqs)} requests)")
PY
echo "== plexamp log"; journalctl -u plexamp -n 40 --no-pager -o cat | cut -c1-200
