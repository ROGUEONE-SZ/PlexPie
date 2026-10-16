#!/usr/bin/env python3
"""PlexPie control daemon: one screen, five sources.

Runs as the appliance user (system service plexpie.service). It:
  * serves the touchscreen launcher on 127.0.0.1:8080
  * shows each source in its own Chromium tab (DevTools protocol on 127.0.0.1:9222)
  * turns the DSI backlight on/off (touch or playback wakes it)
  * pauses the other sources when one starts playing
  * reads AirPlay metadata and cover art from shairport-sync
  * exposes the whole device to Home Assistant via MQTT discovery
  * restarts Plexamp if its local UI stops answering
"""
import glob
import http.server
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VERSION = "1.4.0"
CONF_PATH = os.environ.get("PLEXPIE_CONF", "/etc/plexpie/plexpie.conf")
WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
HTTP_PORT = int(os.environ.get("PLEXPIE_HTTP_PORT", "8080"))
CDP = "http://127.0.0.1:9222"
PLEXAMP = "http://127.0.0.1:32500"
META_PIPE = "/tmp/shairport-sync-metadata"

log = logging.getLogger("plexpie")


# --------------------------------------------------------------------- config
def load_conf(path):
    conf = {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    parts = shlex.split(line, comments=True)
                except ValueError:
                    continue
                for p in parts:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        conf[k.strip()] = v
    except FileNotFoundError:
        log.warning("config %s not found, using defaults", path)
    return conf


C = load_conf(CONF_PATH)


def cfg(key, default=""):
    return C.get(key, default)


def cfg_yes(key, default="yes"):
    return cfg(key, default).strip().lower() in ("1", "yes", "true", "on")


def cfg_int(key, default):
    try:
        return int(cfg(key, str(default)))
    except ValueError:
        return default


PLAYER_NAME = cfg("PLAYER_NAME", socket.gethostname())
MA_URL = cfg("MA_URL", "http://homeassistant.local:8095").rstrip("/")

SOURCES = {
    "home": {"name": "Home", "url": f"http://127.0.0.1:{HTTP_PORT}/"},
    "plexamp": {"name": "Plexamp", "url": PLEXAMP + "/"},
    "apple": {"name": "Apple Music", "url": "https://music.apple.com/"},
    "airplay": {"name": "AirPlay", "url": f"http://127.0.0.1:{HTTP_PORT}/airplay"},
    "ma": {"name": "Music Assistant", "url": MA_URL + "/"},
}
if not cfg_yes("ENABLE_AIRPLAY"):
    SOURCES.pop("airplay")
if not cfg_yes("ENABLE_MUSIC_ASSISTANT"):
    SOURCES.pop("ma")
if not cfg_yes("ENABLE_APPLE_MUSIC_WEB"):
    SOURCES.pop("apple")

NAME_TO_KEY = {v["name"]: k for k, v in SOURCES.items()}
MA_NETLOC = urllib.parse.urlparse(MA_URL).netloc


def source_for_url(url):
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.netloc == f"127.0.0.1:{HTTP_PORT}":
        return "airplay" if u.path.startswith("/airplay") else "home"
    if u.netloc == "127.0.0.1:32500":
        return "plexamp"
    if host == "apple.com" or host.endswith(".apple.com"):
        return "apple"
    if MA_NETLOC and u.netloc == MA_NETLOC:
        return "ma"
    return None


# ---------------------------------------------------------------------- helpers
def http_get(url, timeout=3, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def sudo(*args):
    log.info("sudo %s", " ".join(args))
    try:
        subprocess.run(["sudo", "-n", *args], timeout=60, check=False)
    except Exception as e:
        log.warning("sudo %s failed: %s", args, e)


def read_file(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


# ------------------------------------------------------------------------ state
class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.source = "home"            # what is on screen
        self.playing = {}               # source -> bool
        self.order = []                 # sources, most recently started last
        self.np = {}                    # source -> {title, artist, album}
        self.art = None
        self.art_type = "image/jpeg"
        self.art_ver = 0
        self.screen_on = True
        self.last_wake = time.time()
        self.brightness = max(5, min(100, cfg_int("SCREEN_BRIGHTNESS", 100)))
        self.volume = None
        self.ma_up = False
        self.cpu_temp = None
        self.ap_format = {}             # AirPlay stream: styp / sdsc / odsc text
        self.text_focus = False
        self.text_y = None
        self.changed = threading.Event()

    def now_source(self):
        for s in reversed(self.order):
            if self.playing.get(s):
                return s
        return None


ST = State()


def mark_changed():
    ST.changed.set()


def state_json():
    with ST.lock:
        ns = ST.now_source()
        return {
            "version": VERSION,
            "player_name": PLAYER_NAME,
            "source": ST.source,
            "sources": {k: v["name"] for k, v in SOURCES.items()},
            "playing": ns is not None,
            "playing_by_source": {k: bool(ST.playing.get(k)) for k in SOURCES},
            "now_source": ns,
            "now_source_name": SOURCES[ns]["name"] if ns else None,
            "now": dict(ST.np.get(ns, {})) if ns else {},
            "airplay": dict(ST.np.get("airplay", {}), playing=bool(ST.playing.get("airplay")),
                            has_art=ST.art is not None,
                            format=describe_format(ST.ap_format.get("sdsc")),
                            stream_type=ST.ap_format.get("styp", ""),
                            format_raw=dict(ST.ap_format)),
            "art_ver": ST.art_ver,
            "screen_on": ST.screen_on,
            "brightness": ST.brightness,
            "volume": ST.volume,
            "ma_up": ST.ma_up,
            "home_button": cfg("HOME_BUTTON_POSITION", "top-left"),
            "home_autohide": cfg_int("HOME_BUTTON_AUTOHIDE_SECS", 5),
            "text_focus": ST.text_focus,
            "text_y": ST.text_y,
        }


# ---------------------------------------------------------------------- browser
class Browser:
    """Keeps one Chromium tab per source and brings the right one forward."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ws_url = None

    def version(self):
        try:
            return json.loads(http_get(CDP + "/json/version", 2)).get("webSocketDebuggerUrl")
        except Exception:
            return None

    def tabs(self):
        try:
            return [t for t in json.loads(http_get(CDP + "/json/list", 2)) if t.get("type") == "page"]
        except Exception:
            return None

    @staticmethod
    def find(src, tabs):
        cands = [t for t in tabs if source_for_url(t.get("url", "")) == src]
        if src == "apple":
            cands.sort(key=lambda t: 0 if "music.apple.com" in t.get("url", "") else 1)
        return cands[0] if cands else None

    def show(self, src):
        with self.lock:
            tabs = self.tabs()
            if tabs is None:
                log.warning("browser not reachable, cannot show %s", src)
                return False
            tab = self.find(src, tabs)
            if tab is None:
                url = SOURCES[src]["url"]
                try:
                    tab = json.loads(http_get(
                        CDP + "/json/new?" + urllib.parse.quote(url, safe=":/?&=%#"), 5, method="PUT"))
                    log.info("opened tab for %s", src)
                except Exception as e:
                    log.warning("could not open tab for %s: %s", src, e)
                    return False
            try:
                http_get(CDP + "/json/activate/" + tab["id"], 3)
            except Exception as e:
                log.warning("could not activate tab for %s: %s", src, e)
                return False
            return True

    def evaluate(self, src, expr):
        """Run JS in a source's tab. Returns (tab_exists, value)."""
        tabs = self.tabs()
        if tabs is None:
            return (None, None)
        tab = self.find(src, tabs)
        if tab is None:
            return (False, None)
        if "webSocketDebuggerUrl" not in tab:
            return (True, None)
        return (True, self.eval_target(tab["webSocketDebuggerUrl"], expr))

    @staticmethod
    def eval_target(ws_url, expr, timeout=4):
        ws = None
        try:
            import websocket  # python3-websocket
            ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
            deadline = time.time() + timeout
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("result", {}).get("value")
        except Exception as e:
            log.debug("evaluate failed: %s", e)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        return None

    def text_focus(self, src):
        """(text field focused?, its centre y in CSS px or None, Home tab tapped?) for a source's tab."""
        try:
            targets = json.loads(http_get(CDP + "/json/list", 2))
        except Exception:
            return False, None, False
        pages = [t for t in targets if t.get("type") == "page"]
        tab = self.find(src, pages)
        if not tab or "webSocketDebuggerUrl" not in tab:
            return False, None, False
        p = self.eval_target(tab["webSocketDebuggerUrl"], FOCUS_JS, 2)
        if not isinstance(p, dict):
            return False, None, False
        if p.get("home"):
            return False, None, True
        if p.get("text"):
            return True, p.get("y"), False
        if p.get("iframe"):
            # Sign-in pop-ups (Apple ID) live in cross-site iframes, which are separate targets.
            for t in targets:
                if t.get("type") != "iframe" or "webSocketDebuggerUrl" not in t:
                    continue
                if t.get("parentId") not in (None, tab.get("id")):
                    continue
                f = self.eval_target(t["webSocketDebuggerUrl"], FOCUS_JS, 2)
                if isinstance(f, dict) and f.get("text"):
                    y = f.get("y")
                    return True, ((p.get("top") or 0) + y if y is not None else p.get("y")), False
        return False, None, False


BROWSER = Browser()

REVEAL_JS = """(() => {
  let e = document.activeElement;
  while (e && e.shadowRoot && e.shadowRoot.activeElement) e = e.shadowRoot.activeElement;
  if (e && e !== document.body) { e.scrollIntoView({block: 'center', inline: 'nearest'}); return true; }
  return false;
})()"""


class Viewport:
    """Shrinks the app above the on-screen keyboard, like a phone does.

    Uses a DevTools device-metrics override, which only lasts while our session
    stays attached, so closing the session restores the full screen by itself.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.ws = None
        self.tab_id = None
        self.msg_id = 0

    def _send(self, method, params=None, want_reply=False):
        self.msg_id += 1
        mid = self.msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        if not want_reply:
            return None
        deadline = time.time() + 3
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})
        return None

    def _close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws, self.tab_id = None, None

    def shrink(self, height_px):
        with self.lock:
            try:
                import websocket
                with ST.lock:
                    src = ST.source
                tabs = BROWSER.tabs() or []
                tab = Browser.find(src, tabs)
                if not tab or "webSocketDebuggerUrl" not in tab:
                    return False
                if self.ws is not None and self.tab_id != tab["id"]:
                    self._close()
                if self.ws is None:
                    self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=4,
                                                          suppress_origin=True)
                    self.tab_id = tab["id"]
                scale = float(cfg("CHROMIUM_SCALE", "1") or 1)
                r = self._send("Runtime.evaluate", {"expression": "window.screen.width", "returnByValue": True},
                               want_reply=True) or {}
                width = int(r.get("result", {}).get("value") or 800 / scale)
                self._send("Emulation.setDeviceMetricsOverride",
                           {"width": width, "height": max(120, int(height_px / scale)),
                            "deviceScaleFactor": 0, "mobile": False}, want_reply=True)
                time.sleep(0.15)
                self._send("Runtime.evaluate", {"expression": REVEAL_JS}, want_reply=True)
                # sign-in pop-ups in cross-site iframes scroll on their own target
                try:
                    for t in json.loads(http_get(CDP + "/json/list", 2)):
                        if t.get("type") == "iframe" and t.get("parentId") in (None, tab["id"]) \
                                and "webSocketDebuggerUrl" in t:
                            Browser.eval_target(t["webSocketDebuggerUrl"], REVEAL_JS, 2)
                except Exception:
                    pass
                return True
            except Exception as e:
                log.warning("viewport shrink failed: %s", e)
                self._close()
                return False

    def restore(self):
        with self.lock:
            if self.ws is not None:
                try:
                    self._send("Emulation.clearDeviceMetricsOverride", want_reply=True)
                except Exception:
                    pass
            self._close()


VIEWPORT = Viewport()

FOCUS_JS = """(() => {
  // --- PlexPie Home tab, drawn inside the app's own page (re-added after navigation)
  if (window.top === window && document.body && !document.getElementById('plexpie-home')) {
    const ns = 'http://www.w3.org/2000/svg';
    const b = document.createElement('div');
    b.id = 'plexpie-home';
    Object.assign(b.style, {
      position: 'fixed', left: '0', top: '50%', transform: 'translateY(-50%)', width: '26px', height: '72px',
      borderRadius: '0 18px 18px 0', background: 'rgba(28,28,30,.42)', backdropFilter: 'blur(14px) saturate(1.4)',
      webkitBackdropFilter: 'blur(14px) saturate(1.4)', border: '1px solid rgba(255,255,255,.14)', borderLeft: 'none',
      boxShadow: '0 4px 18px rgba(0,0,0,.28)', display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: '2147483647', cursor: 'pointer', touchAction: 'manipulation', userSelect: 'none'});
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', '17'); svg.setAttribute('height', '17');
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', 'M3 11.5 12 4l9 7.5M6 10v9.5h4.5V15h3v4.5H18V10');
    path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'rgba(255,255,255,.92)');
    path.setAttribute('stroke-width', '2.2'); path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path); b.appendChild(svg);
    const stop = e => { e.stopPropagation(); e.preventDefault(); };
    ['pointerdown', 'mousedown', 'touchstart'].forEach(t => b.addEventListener(t, stop, {capture: true, passive: false}));
    b.addEventListener('click', e => { stop(e); window.__plexpieHome = Date.now(); }, true);
    document.body.appendChild(b);
  }
  const home = !!window.__plexpieHome; window.__plexpieHome = 0;
  const TEXT = ['', 'text', 'email', 'password', 'search', 'tel', 'url', 'number'];
  let e = document.activeElement;
  while (e && e.shadowRoot && e.shadowRoot.activeElement) e = e.shadowRoot.activeElement;
  if (!e) return {text: false, home};
  const t = e.tagName;
  const text = (t === 'INPUT' && TEXT.includes((e.getAttribute('type') || '').toLowerCase()) && !e.readOnly && !e.disabled)
    || t === 'TEXTAREA' || e.isContentEditable === true;
  const r = e.getBoundingClientRect();
  return {text: !!text, iframe: t === 'IFRAME', y: (r.top + r.bottom) / 2, top: r.top, home};
})()"""

APPLE_STATE_JS = """(() => {
  try {
    const m = MusicKit.getInstance(); const i = m.nowPlayingItem;
    return {playing: !!m.isPlaying, title: i ? (i.title || '') : '',
            artist: i ? (i.artistName || '') : '', album: i ? (i.albumName || '') : ''};
  } catch (e) {
    const a = [...document.querySelectorAll('audio,video')];
    return {playing: a.some(x => !x.paused), title: '', artist: '', album: ''};
  }
})()"""

APPLE_PAUSE_JS = """(() => {
  try { MusicKit.getInstance().pause(); } catch (e) {}
  document.querySelectorAll('audio,video').forEach(x => x.pause());
  return true;
})()"""


# ---------------------------------------------------------------------- plexamp
class Plexamp:
    _cmd = 0
    HEADERS = {"X-Plex-Client-Identifier": "plexpie", "X-Plex-Device-Name": "PlexPie",
               "X-Plex-Product": "PlexPie"}

    def _q(self, path, timeout=3):
        Plexamp._cmd += 1
        sep = "&" if "?" in path else "?"
        return http_get(f"{PLEXAMP}{path}{sep}commandID={Plexamp._cmd}", timeout, headers=self.HEADERS)

    def poll(self):
        """Returns (playing or None if unknown, metadata or None)."""
        try:
            root = ET.fromstring(self._q("/player/timeline/poll?wait=0&includeMetadata=1&type=music"))
        except Exception:
            return None, None
        for tl in root.iter("Timeline"):
            if tl.get("type") == "music":
                md = None
                tr = tl.find("Track")
                if tr is not None:
                    md = {"title": tr.get("title", ""),
                          "artist": tr.get("originalTitle") or tr.get("grandparentTitle", ""),
                          "album": tr.get("parentTitle", "")}
                return tl.get("state") == "playing", md
        return False, None

    def pause(self):
        try:
            self._q("/player/playback/pause?type=music")
        except Exception as e:
            log.warning("plexamp pause failed: %s", e)

    def healthy(self):
        try:
            http_get(PLEXAMP + "/", 4)
            return True
        except Exception:
            return False


PLEX = Plexamp()


# ----------------------------------------------------------------------- screen
class Screen:
    def __init__(self):
        found = sorted(glob.glob("/sys/class/backlight/*"))
        self.bl = found[0] if found else None
        self.max = int(read_file(f"{self.bl}/max_brightness", "255") or 255) if self.bl else 255
        log.info("backlight: %s (max %s)", self.bl or "none, using DPMS", self.max)

    def _write(self, name, value):
        try:
            with open(f"{self.bl}/{name}", "w") as f:
                f.write(str(value))
        except OSError as e:
            log.warning("backlight %s write failed: %s", name, e)

    def apply(self):
        with ST.lock:
            on, pct = ST.screen_on, ST.brightness
        if self.bl:
            if on:
                if os.path.exists(f"{self.bl}/bl_power"):
                    self._write("bl_power", 0)
                self._write("brightness", max(1, round(self.max * pct / 100)))
            else:
                self._write("brightness", 0)
                if os.path.exists(f"{self.bl}/bl_power"):
                    self._write("bl_power", 4)
        else:
            run(["xset", "dpms", "force", "on" if on else "off"])

    def set(self, on, reason=""):
        with ST.lock:
            changed = ST.screen_on != on
            ST.screen_on = on
            if on:
                ST.last_wake = time.time()
        if changed:
            log.info("screen %s (%s)", "on" if on else "off", reason)
            self.apply()
            mark_changed()

    def wake(self, reason=""):
        self.set(True, reason)

    def set_brightness(self, pct):
        with ST.lock:
            ST.brightness = max(5, min(100, int(pct)))
        self.apply()
        mark_changed()

    @staticmethod
    def idle_secs():
        out = run(["xprintidle"], 2).strip()
        x_idle = int(out) / 1000 if out.isdigit() else float("inf")
        return min(x_idle, time.time() - ST.last_wake)


SCREEN = Screen()


# ------------------------------------------------------------ playback control
def set_playing(src, value):
    value = bool(value)
    with ST.lock:
        was = bool(ST.playing.get(src))
        ST.playing[src] = value
        if value and not was:
            if src in ST.order:
                ST.order.remove(src)
            ST.order.append(src)
    if value != was:
        log.info("%s %s", src, "playing" if value else "stopped")
        mark_changed()
        if value:
            spawn(on_started, src)


def on_started(src):
    SCREEN.wake(f"{src} started")
    if cfg_yes("PAUSE_OTHERS"):
        with ST.lock:
            others = [s for s, p in ST.playing.items() if p and s != src]
        for other in others:
            pause_source(other)
    if src == "airplay" and cfg_yes("AUTO_SHOW_AIRPLAY") and "airplay" in SOURCES:
        switch_source("airplay")


def pause_source(src):
    log.info("pausing %s", src)
    if src == "plexamp":
        PLEX.pause()
    elif src == "apple":
        BROWSER.evaluate("apple", APPLE_PAUSE_JS)
    elif src == "airplay":
        sudo("/usr/bin/systemctl", "restart", "plexpie-airplay")
        set_playing("airplay", False)
    elif src == "ma":
        sudo("/usr/bin/systemctl", "restart", "plexpie-squeezelite")
        set_playing("ma", False)


def pause_all():
    with ST.lock:
        active = [s for s, p in ST.playing.items() if p]
    for s in active:
        pause_source(s)


def switch_source(src):
    if src not in SOURCES:
        return False
    VIEWPORT.restore()
    ok = BROWSER.show(src)
    if ok:
        with ST.lock:
            ST.source = src
        log.info("showing %s", src)
        mark_changed()
    SCREEN.wake("source change")
    return ok


def get_volume():
    m = re.search(r"Volume:\s*([\d.]+)", run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]))
    return round(float(m.group(1)) * 100) if m else None


def set_volume(value):
    value = max(0, min(100, int(value)))
    run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value / 100:.2f}"])
    with ST.lock:
        ST.volume = get_volume()
    mark_changed()


def pw_streams():
    """binary name -> True if it has a running PipeWire output stream."""
    try:
        objs = json.loads(run(["pw-dump"], 6) or "[]")
    except ValueError:
        return {}
    res = {}
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        info = o.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        name = (props.get("application.process.binary") or props.get("application.name") or "").lower()
        res[name] = res.get(name, False) or info.get("state") == "running"
    return res


# ------------------------------------------------------------- AirPlay metadata
ITEM_RE = re.compile(
    rb"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>"
    rb"(?:\s*<data encoding=\"base64\">\s*(.*?)\s*</data>)?\s*</item>", re.S)


AIRPLAY_SEEN = {"n": 0}
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG")


def _plist(raw):
    import plistlib
    try:
        return plistlib.loads(raw)
    except Exception:
        return None


def _unarchive(obj):
    """Turn an NSKeyedArchiver plist into plain dicts/lists (best effort)."""
    import plistlib
    objects = obj.get("$objects") if isinstance(obj, dict) else None
    if not isinstance(objects, list):
        return obj
    seen = set()

    def res(v, depth=0):
        if depth > 40:
            return None
        if isinstance(v, plistlib.UID):
            if v.data in seen:
                return None
            seen.add(v.data)
            try:
                return res(objects[v.data], depth + 1)
            finally:
                seen.discard(v.data)
        if isinstance(v, dict):
            if "NS.keys" in v and "NS.objects" in v:
                return {str(res(k, depth + 1)): res(o, depth + 1) for k, o in zip(v["NS.keys"], v["NS.objects"])}
            if "NS.objects" in v:
                return [res(o, depth + 1) for o in v["NS.objects"]]
            if "NS.string" in v:
                return v["NS.string"]
            if "NS.data" in v:
                return v["NS.data"]
            return {k: res(x, depth + 1) for k, x in v.items() if k != "$class"}
        if isinstance(v, list):
            return [res(x, depth + 1) for x in v]
        return v

    top = obj.get("$top", {})
    return {k: res(v) for k, v in top.items()} if isinstance(top, dict) else top


def now_playing_from_plist(raw):
    """Pull title / artist / album / artwork out of an AirPlay 2 'copl' plist.

    The plist (and plists nested inside it as data blobs) carries the sender's
    MediaRemote now-playing info, e.g. kMRMediaRemoteNowPlayingInfoTitle.
    """
    found = {}

    def walk(v, depth=0):
        if depth > 12:
            return
        if isinstance(v, (bytes, bytearray)):
            b = bytes(v)
            if b.startswith(IMAGE_MAGIC):
                found.setdefault("art", b)
            elif b.startswith(b"bplist") or b.lstrip().startswith(b"<?xml"):
                inner = _plist(b)
                if inner is not None:
                    walk(_unarchive(inner), depth + 1)
            return
        if isinstance(v, dict):
            for k, x in v.items():
                kl = str(k).lower()
                if isinstance(x, str) and x.strip():
                    if kl.endswith("infotitle") or kl in ("title", "itemtitle", "name"):
                        found.setdefault("title", x)
                    elif kl.endswith("infoartist") or kl in ("artist", "artistname", "trackartistname"):
                        found.setdefault("artist", x)
                    elif kl.endswith("infoalbum") or kl in ("album", "albumname"):
                        found.setdefault("album", x)
                elif isinstance(x, (bytes, bytearray)) and "artwork" in kl and bytes(x).startswith(IMAGE_MAGIC):
                    found.setdefault("art", bytes(x))
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)

    top = _plist(raw)
    if top is None:
        return found
    walk(_unarchive(top))
    return found


def describe_format(desc):
    """Turn shairport-sync's stream description (e.g. 'ALAC/48000/S24/2') into words."""
    if not desc:
        return ""
    parts = [x.strip() for x in desc.replace(",", "/").split("/") if x.strip()]
    codec = parts[0].upper() if parts else ""
    lossless = codec in ("ALAC", "LPCM", "PCM", "FLAC")
    out = [codec]
    for x in parts[1:]:
        if x.isdigit() and int(x) >= 8000:
            out.append(f"{int(x) / 1000:g} kHz")
        elif lossless and x[:1] in ("S", "U") and x[1:].isdigit():
            out.append(f"{x[1:]}-bit")      # decoder bit depth means nothing for AAC
    out.append("lossless" if lossless else ("lossy" if codec in ("AAC", "AAC-ELD", "MP3") else ""))
    return " · ".join(x for x in out if x)


def on_airplay_item(typ, code, data):
    import base64
    raw = base64.b64decode(data) if data else b""
    if code == "pbeg":
        AIRPLAY_SEEN["n"] = 0
    if AIRPLAY_SEEN["n"] < 60:                      # first items of each session, for troubleshooting
        AIRPLAY_SEEN["n"] += 1
        log.info("airplay meta %s/%s (%d bytes)", typ, code, len(raw))
    text = raw.decode("utf-8", "replace")
    with ST.lock:
        np = ST.np.setdefault("airplay", {"title": "", "artist": "", "album": ""})
        if typ == "core":
            if code == "minm":
                np["title"] = text
            elif code == "asar":
                np["artist"] = text
            elif code == "asal":
                np["album"] = text
            else:
                return
        elif typ == "ssnc":
            if code == "copl" and raw:
                info = now_playing_from_plist(raw)
                try:  # keep the biggest recent one for troubleshooting (tools/diagnose.sh)
                    p = "/tmp/plexpie-copl-last.bin"
                    if len(raw) >= (os.path.getsize(p) if os.path.exists(p) else 0) or info:
                        with open(p, "wb") as f:
                            f.write(raw[:2_000_000])
                except OSError:
                    pass
                if not info:
                    return
                for k in ("title", "artist", "album"):
                    if info.get(k):
                        np[k] = info[k]
                if info.get("art"):
                    ST.art = info["art"]
                    ST.art_type = "image/png" if info["art"][:4] == b"\x89PNG" else "image/jpeg"
                    ST.art_ver += 1
                log.info("airplay now playing: %s / %s", np.get("title"), np.get("artist"))
            elif code == "PICT" and raw:
                ST.art = raw
                ST.art_type = "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"
                ST.art_ver += 1
            elif code in ("sdsc", "odsc", "styp"):
                text = text.strip().strip("\x00")
                ST.ap_format[code] = text
                log.info("airplay stream %s: %s", {"sdsc": "source", "odsc": "output", "styp": "type"}[code], text)
            elif code == "pend":
                ST.ap_format = {}
                ST.np["airplay"] = {"title": "", "artist": "", "album": ""}
                ST.art = None
                ST.art_ver += 1
            elif code not in ("pbeg", "prsm", "pfls"):
                return
        else:
            return
    if typ == "ssnc" and code in ("pbeg", "prsm"):
        set_playing("airplay", True)
    elif typ == "ssnc" and code in ("pfls", "pend"):
        set_playing("airplay", False)
    mark_changed()


def airplay_reader():
    while True:
        if not os.path.exists(META_PIPE):
            time.sleep(2)
            continue
        try:
            fd = os.open(META_PIPE, os.O_RDONLY)  # blocks until shairport-sync opens it
            buf = b""
            with os.fdopen(fd, "rb", buffering=0) as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        m = ITEM_RE.search(buf)
                        if not m:
                            break
                        buf = buf[m.end():]
                        try:
                            on_airplay_item(bytes.fromhex(m.group(1).decode()).decode("ascii", "replace"),
                                            bytes.fromhex(m.group(2).decode()).decode("ascii", "replace"),
                                            m.group(4))
                        except Exception as e:
                            log.debug("bad airplay item: %s", e)
                    if len(buf) > 8_000_000:
                        buf = b""
        except Exception as e:
            log.warning("airplay metadata reader: %s", e)
        time.sleep(1)


# ---------------------------------------------------------------------- pollers
def fast_poller():
    n = 0
    while True:
        n += 1
        try:
            playing, md = PLEX.poll()
            if playing is not None:
                set_playing("plexamp", playing)
                if md:
                    with ST.lock:
                        if ST.np.get("plexamp") != md:
                            ST.np["plexamp"] = md
                            mark_changed()
            if "ma" in SOURCES:
                set_playing("ma", pw_streams().get("squeezelite", False))
            if "apple" in SOURCES and n % 2 == 0:
                exists, val = BROWSER.evaluate("apple", APPLE_STATE_JS)
                if exists is False:
                    set_playing("apple", False)
                elif isinstance(val, dict):
                    set_playing("apple", val.get("playing"))
                    md = {k: val.get(k, "") for k in ("title", "artist", "album")}
                    with ST.lock:
                        if ST.np.get("apple") != md:
                            ST.np["apple"] = md
                            mark_changed()
        except Exception as e:
            log.warning("poller: %s", e)
        time.sleep(2)


def focus_poller():
    """Tell the overlay when a text field is focused, so the keyboard can pop up."""
    scale = float(cfg("CHROMIUM_SCALE", "1") or 1)
    while True:
        try:
            with ST.lock:
                src = ST.source
            focus, y, home = (False, None, False)
            if src in ("plexamp", "apple", "ma"):
                focus, y, home = BROWSER.text_focus(src)
            if home:
                log.info("home tab tapped in %s", src)
                switch_source("home")
                focus, y = False, None
            y = round(y * scale) if (focus and isinstance(y, (int, float))) else None
            with ST.lock:
                ST.text_focus, ST.text_y = bool(focus), y
        except Exception as e:
            log.debug("focus poller: %s", e)
        time.sleep(0.35)


def slow_poller():
    host = urllib.parse.urlparse(MA_URL).hostname
    port = urllib.parse.urlparse(MA_URL).port or 8095
    while True:
        try:
            vol = get_volume()
            temp = read_file("/sys/class/thermal/thermal_zone0/temp")
            temp = round(int(temp) / 1000, 1) if temp and temp.isdigit() else None
            ma_up = False
            if "ma" in SOURCES and host:
                try:
                    socket.create_connection((host, port), 2).close()
                    ma_up = True
                except OSError:
                    pass
            with ST.lock:
                changed = (vol, ma_up) != (ST.volume, ST.ma_up)
                ST.volume, ST.ma_up = vol, ma_up
                if temp is not None and (ST.cpu_temp is None or abs(temp - ST.cpu_temp) >= 1):
                    ST.cpu_temp = temp
                    changed = True
            if changed:
                mark_changed()
        except Exception as e:
            log.warning("slow poller: %s", e)
        time.sleep(10)


def screen_loop():
    off_after = cfg_int("SCREEN_OFF_AFTER_SECS", 60)
    while True:
        try:
            with ST.lock:
                on = ST.screen_on
                playing = ST.now_source() is not None
            if on and off_after > 0 and not playing and SCREEN.idle_secs() >= off_after:
                SCREEN.set(False, "idle")
        except Exception as e:
            log.warning("screen loop: %s", e)
        time.sleep(1)


def watchdog():
    plex_fail = 0
    browser_down_since = None
    while True:
        time.sleep(15)
        try:
            # Plexamp backend
            if PLEX.healthy():
                plex_fail = 0
            else:
                plex_fail += 1
                if plex_fail >= 3:
                    log.warning("Plexamp UI not answering, restarting plexamp")
                    sudo("/usr/bin/systemctl", "restart", "plexamp")
                    plex_fail = 0
            # Browser restarts: tabs are gone, the launcher is showing again
            ws = BROWSER.version()
            if ws is None:
                browser_down_since = browser_down_since or time.time()
            else:
                browser_down_since = None
                if ws != BROWSER.ws_url:
                    if BROWSER.ws_url is not None:
                        log.info("browser restarted, back to home")
                    BROWSER.ws_url = ws
                    with ST.lock:
                        ST.source = "home"
                    mark_changed()
        except Exception as e:
            log.warning("watchdog: %s", e)


# ------------------------------------------------------------------- web server
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PlexPie/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json", cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype="text/html; charset=utf-8"):
        try:
            with open(os.path.join(WWW, name), "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._send(404, b"not found", "text/plain")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file("index.html")
        if path == "/airplay":
            return self._file("airplay.html")
        if path == "/api/state":
            return self._send(200, json.dumps(state_json()).encode())
        if path == "/api/art":
            with ST.lock:
                art, typ = ST.art, ST.art_type
            if art:
                return self._send(200, art, typ)
            return self._send(404, b"", "text/plain")
        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, TypeError):
            data = {}
        ok = True
        if path == "/api/source":
            ok = switch_source(str(data.get("source", "home")))
        elif path == "/api/wake":
            SCREEN.wake("touch")
        elif path == "/api/screen":
            SCREEN.set(bool(data.get("on", True)), "launcher")
        elif path == "/api/volume":
            if "delta" in data:
                cur = ST.volume if ST.volume is not None else (get_volume() or 0)
                set_volume(cur + int(data["delta"]))
            elif "value" in data:
                set_volume(data["value"])
        elif path == "/api/keyboard":
            if data.get("open"):
                spawn(VIEWPORT.shrink, int(data.get("height") or 228))
            else:
                spawn(VIEWPORT.restore)
        elif path == "/api/pause_all":
            spawn(pause_all)
        else:
            return self._send(404, b'{"error":"not found"}')
        self._send(200 if ok else 503, json.dumps({"ok": ok, "state": state_json()}).encode())


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ------------------------------------------------------- Home Assistant (MQTT)
class HomeAssistant:
    def __init__(self):
        self.node = re.sub(r"[^a-z0-9_]", "_", socket.gethostname().lower())
        self.base = f"plexpie/{self.node}"
        self.client = None
        self.connected = False
        self.last = {}
        self.art_sent = -1

    def start(self):
        host = cfg("MQTT_HOST")
        if not host:
            log.info("MQTT_HOST not set, Home Assistant integration off")
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("python3-paho-mqtt missing, Home Assistant integration off")
            return
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"plexpie-{self.node}")
        except AttributeError:
            c = mqtt.Client(client_id=f"plexpie-{self.node}")
        if cfg("MQTT_USER"):
            c.username_pw_set(cfg("MQTT_USER"), cfg("MQTT_PASS"))
        c.will_set(f"{self.base}/status", "offline", retain=True)
        c.on_connect = self.on_connect
        c.on_disconnect = self.on_disconnect
        c.on_message = self.on_message
        c.reconnect_delay_set(1, 60)
        c.connect_async(host, cfg_int("MQTT_PORT", 1883), keepalive=30)
        c.loop_start()
        self.client = c

    def pub(self, topic, payload, retain=True):
        if self.client and self.connected:
            self.client.publish(topic, payload, retain=retain)

    def disc(self, component, oid, conf):
        dev = {"identifiers": [f"plexpie_{self.node}"], "name": socket.gethostname(),
               "manufacturer": "Raspberry Pi", "model": "Raspberry Pi 4 (PlexPie)", "sw_version": VERSION}
        payload = {"unique_id": f"plexpie_{self.node}_{oid}", "availability_topic": f"{self.base}/status",
                   "device": dev, **conf}
        self.client.publish(f"homeassistant/{component}/{self.node}/{oid}/config", json.dumps(payload), retain=True)

    def on_connect(self, client, userdata, flags, rc, *args):
        log.info("MQTT connected (%s)", rc)
        self.connected = True
        b = self.base
        self.disc("select", "source", {"name": "Source", "icon": "mdi:television-guide",
                                        "state_topic": f"{b}/source", "command_topic": f"{b}/source/set",
                                        "options": [v["name"] for v in SOURCES.values()]})
        self.disc("sensor", "now_playing", {"name": "Now playing", "icon": "mdi:music",
                                             "state_topic": f"{b}/now", "json_attributes_topic": f"{b}/now/attr"})
        self.disc("sensor", "playing_source", {"name": "Playing source", "icon": "mdi:speaker",
                                                "state_topic": f"{b}/now_source"})
        self.disc("binary_sensor", "playing", {"name": "Playing", "icon": "mdi:play-circle",
                                               "state_topic": f"{b}/playing"})
        self.disc("switch", "screen", {"name": "Screen", "icon": "mdi:monitor", "state_topic": f"{b}/screen",
                                       "command_topic": f"{b}/screen/set"})
        self.disc("number", "brightness", {"name": "Screen brightness", "icon": "mdi:brightness-6",
                                           "min": 5, "max": 100, "step": 5, "unit_of_measurement": "%",
                                           "state_topic": f"{b}/brightness", "command_topic": f"{b}/brightness/set"})
        self.disc("number", "volume", {"name": "Volume", "icon": "mdi:volume-high", "min": 0, "max": 100,
                                       "step": 1, "unit_of_measurement": "%", "mode": "slider",
                                       "state_topic": f"{b}/volume", "command_topic": f"{b}/volume/set"})
        for oid, name, icon in (("pause_all", "Pause all", "mdi:pause"),
                                ("restart_screen", "Restart screen", "mdi:restart"),
                                ("reboot", "Reboot", "mdi:power")):
            extra = {"device_class": "restart"} if oid == "reboot" else {}
            self.disc("button", oid, {"name": name, "icon": icon, "command_topic": f"{b}/{oid}/set",
                                      "payload_press": "PRESS", **extra})
        if "airplay" in SOURCES:
            self.disc("image", "airplay_art", {"name": "AirPlay artwork", "image_topic": f"{b}/art",
                                               "content_type": "image/jpeg"})
        if "ma" in SOURCES:
            self.disc("binary_sensor", "ma_reachable", {"name": "Music Assistant reachable",
                                                        "device_class": "connectivity",
                                                        "entity_category": "diagnostic",
                                                        "state_topic": f"{b}/ma_up"})
        self.disc("sensor", "cpu_temp", {"name": "CPU temperature", "device_class": "temperature",
                                         "unit_of_measurement": "°C", "entity_category": "diagnostic",
                                         "state_topic": f"{b}/cpu_temp"})
        client.publish(f"{b}/status", "online", retain=True)
        client.subscribe(f"{b}/+/set")
        self.last = {}
        self.art_sent = -1
        self.publish_state()

    def on_disconnect(self, *args):
        self.connected = False
        log.warning("MQTT disconnected")

    def on_message(self, client, userdata, msg):
        key = msg.topic[len(self.base) + 1:].rsplit("/set", 1)[0]
        val = msg.payload.decode("utf-8", "replace").strip()
        log.info("HA command %s=%s", key, val)
        try:
            if key == "source":
                spawn(switch_source, NAME_TO_KEY.get(val, val))
            elif key == "screen":
                SCREEN.set(val.upper() == "ON", "home assistant")
            elif key == "brightness":
                SCREEN.set_brightness(float(val))
            elif key == "volume":
                set_volume(float(val))
            elif key == "pause_all":
                spawn(pause_all)
            elif key == "restart_screen":
                run(["pkill", "-f", "plexpie-chromium"])
            elif key == "reboot":
                sudo("/usr/sbin/reboot")
        except ValueError:
            log.warning("bad value for %s: %s", key, val)

    def publish_state(self):
        if not (self.client and self.connected):
            return
        s = state_json()
        now = s["now"]
        if s["playing"]:
            label = " — ".join(x for x in (now.get("title"), now.get("artist")) if x) or s["now_source_name"]
        else:
            label = "Idle"
        values = {
            "source": SOURCES[s["source"]]["name"] if s["source"] in SOURCES else "Home",
            "now": label[:250],
            "now/attr": json.dumps({"source": s["now_source_name"], **now,
                                    **({"format": s["airplay"]["format"]} if s["now_source"] == "airplay"
                                       and s["airplay"]["format"] else {})}),
            "now_source": s["now_source_name"] or "None",
            "playing": "ON" if s["playing"] else "OFF",
            "screen": "ON" if s["screen_on"] else "OFF",
            "brightness": str(s["brightness"]),
            "volume": "" if s["volume"] is None else str(s["volume"]),
            "ma_up": "ON" if s["ma_up"] else "OFF",
            "cpu_temp": "" if ST.cpu_temp is None else str(ST.cpu_temp),
        }
        for k, v in values.items():
            if v != "" and self.last.get(k) != v:
                self.pub(f"{self.base}/{k}", v)
                self.last[k] = v
        legacy = cfg("MQTT_LEGACY_TOPIC", "plexamp/state")
        lv = "playing" if s["playing"] else "idle"
        if legacy and self.last.get("_legacy") != lv:
            self.pub(legacy, lv)
            self.last["_legacy"] = lv
        with ST.lock:
            art, ver = ST.art, ST.art_ver
        if "airplay" in SOURCES and art and ver != self.art_sent:
            self.pub(f"{self.base}/art", art)
            self.art_sent = ver


HA = HomeAssistant()


# ------------------------------------------------------------------------- main
def main():
    logging.basicConfig(level=os.environ.get("PLEXPIE_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    log.info("PlexPie %s starting, sources: %s", VERSION, ", ".join(SOURCES))
    server = Server(("127.0.0.1", HTTP_PORT), Handler)
    spawn(server.serve_forever)
    SCREEN.apply()
    if "airplay" in SOURCES:
        spawn(airplay_reader)
    for fn in (fast_poller, slow_poller, focus_poller, screen_loop, watchdog):
        spawn(fn)
    HA.start()
    while True:
        ST.changed.wait(30)
        ST.changed.clear()
        try:
            HA.publish_state()
        except Exception as e:
            log.warning("publish: %s", e)


if __name__ == "__main__":
    main()
