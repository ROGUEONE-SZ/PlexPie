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

VERSION = "2.6.0"
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
SETTINGS_DIR = os.path.join(os.environ.get("HOME", "/tmp"), ".config", "plexpie")
SETTINGS_JSON = os.path.join(SETTINGS_DIR, "settings.json")
SETTINGS_ENV = os.path.join(SETTINGS_DIR, "settings.env")      # read by the systemd units too
# What the Settings page may change, and how
EDITABLE = {
    "ENABLE_APPLE_MUSIC_WEB": "bool", "ENABLE_AIRPLAY": "bool", "ENABLE_SPOTIFY": "bool",
    "ENABLE_SPOTIFY_WEB": "bool", "ENABLE_MUSIC_ASSISTANT": "bool", "ENABLE_TIMER": "bool", "SHORTCUTS": "str",
    "SCREEN_OFF_AFTER_SECS": "int", "SCREEN_BRIGHTNESS": "int", "AUTO_SHOW_SOURCES": "str",
    "SWIPE_HOME": "bool", "MA_HOST": "str", "MA_URL": "str",
    "MQTT_HOST": "str", "MQTT_PORT": "int", "MQTT_USER": "str", "MQTT_PASS": "secret",
    "PHONE_ACCESS": "bool", "PHONE_PIN": "str",
}


def load_overrides():
    try:
        with open(SETTINGS_JSON) as f:
            data = json.load(f)
        return {k: str(v) for k, v in data.items() if k in EDITABLE}
    except (OSError, ValueError):
        return {}


def save_overrides(values):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    cur = load_overrides()
    cur.update({k: str(v) for k, v in values.items() if k in EDITABLE})
    tmp = SETTINGS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS_JSON)
    with open(SETTINGS_ENV, "w") as f:          # same values, for systemd EnvironmentFile
        for k, v in cur.items():
            f.write(f'{k}="{v.replace(chr(34), "")}"\n')
    os.chmod(SETTINGS_ENV, 0o600)


C.update(load_overrides())


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
    "spotify": {"name": "Spotify", "url": f"http://127.0.0.1:{HTTP_PORT}/spotify"},
    "spotify_web": {"name": "Spotify web", "url": "https://open.spotify.com/", "hidden": True},
    "ma": {"name": "Music Assistant", "url": MA_URL + "/"},
    "timer": {"name": "Timer", "url": f"http://127.0.0.1:{HTTP_PORT}/timer"},
    "alert": {"name": "Alert", "url": f"http://127.0.0.1:{HTTP_PORT}/alert", "hidden": True},
    "settings": {"name": "Settings", "url": f"http://127.0.0.1:{HTTP_PORT}/settings", "hidden": True},
}
for key, flag, default in (("airplay", "ENABLE_AIRPLAY", "yes"), ("ma", "ENABLE_MUSIC_ASSISTANT", "yes"),
                           ("apple", "ENABLE_APPLE_MUSIC_WEB", "yes"), ("spotify", "ENABLE_SPOTIFY", "no"),
                           ("spotify_web", "ENABLE_SPOTIFY_WEB", "no"), ("timer", "ENABLE_TIMER", "yes")):
    if not cfg_yes(flag, default):
        SOURCES.pop(key)
if "spotify" not in SOURCES:
    SOURCES.pop("spotify_web", None)
WEB_APPS = ("plexamp", "apple", "ma", "spotify_web", "settings")     # pages that may need the keyboard
SPOTIFY_API = "http://127.0.0.1:3678"

NAME_TO_KEY = {v["name"]: k for k, v in SOURCES.items()}
# Home Assistant buttons on the launcher ("Gym lights, Fan"): each is a switch in Home Assistant
SHORTCUTS = {}
for _n in cfg("SHORTCUTS").split(","):
    _n = _n.strip()[:40]
    _id = re.sub(r"[^a-z0-9]+", "_", _n.lower()).strip("_")
    if _id and _id not in SHORTCUTS and len(SHORTCUTS) < 6:
        SHORTCUTS[_id] = {"name": _n, "on": False}
MA_NETLOC = urllib.parse.urlparse(MA_URL).netloc


def source_for_url(url):
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.netloc == f"127.0.0.1:{HTTP_PORT}":
        for key in ("airplay", "spotify", "settings", "timer", "alert"):
            if u.path.startswith("/" + key):
                return key
        return "home"
    if host == "spotify.com" or host.endswith(".spotify.com"):
        return "spotify_web"
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
        self.spotify_user = ""
        self.text_focus = False
        self.text_y = None
        self.quiet_until = 0.0          # during an alert: no auto-pause, no auto-show
        self.alert = {}
        self.alert_n = 0
        self.sleep_until = 0.0          # sleep timer: pause everything at this time (0 = off)
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
            "sources": {k: v["name"] for k, v in SOURCES.items() if not v.get("hidden")},
            "spotify": dict(ST.np.get("spotify", {}), playing=bool(ST.playing.get("spotify")),
                            web="spotify_web" in SOURCES),
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
            "shortcuts": [{"id": k, **v} for k, v in SHORTCUTS.items()],
            "alert": bool(ST.alert),
            "sleep_left": max(0, round(ST.sleep_until - time.time())) if ST.sleep_until else 0,
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
  // remove the old in-page Home tab from pages loaded by an earlier version
  const oldTab = document.getElementById('plexpie-home'); if (oldTab) oldTab.remove();
  const home = false;
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

WEB_STATE_JS = """(() => {
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  const a = [...document.querySelectorAll('audio,video')];
  return {playing: (navigator.mediaSession && navigator.mediaSession.playbackState === 'playing')
                   || a.some(x => !x.paused),
          title: m ? m.title : '', artist: m ? m.artist : '', album: m ? m.album : ''};
})()"""

WEB_PAUSE_JS = """(() => {
  const b = document.querySelector('[data-testid="control-button-playpause"][aria-label*="Pause"]');
  if (b) b.click(); else document.querySelectorAll('audio,video').forEach(x => x.pause());
  return true;
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
    if time.time() < ST.quiet_until:          # e.g. a doorbell announcement over the music
        log.info("%s started during an alert: leaving the rest alone", src)
        return
    if cfg_yes("PAUSE_OTHERS"):
        with ST.lock:
            others = [s for s, p in ST.playing.items() if p and s != src]
        for other in others:
            pause_source(other)
    # Show the app that just started playing (e.g. Plexamp started from the phone).
    auto = cfg("AUTO_SHOW_SOURCES", "plexamp airplay ma").replace(",", " ").split()
    if src == "airplay" and not cfg_yes("AUTO_SHOW_AIRPLAY"):
        auto = [a for a in auto if a != "airplay"]
    with ST.lock:
        on_screen, typing = ST.source, ST.text_focus
    if src in auto and src in SOURCES and on_screen != src and not typing:
        log.info("%s started playing: showing it", src)
        switch_source(src)


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
    elif src == "spotify":
        try:
            http_get(SPOTIFY_API + "/player/pause", 3, method="POST")
        except Exception as e:
            log.warning("spotify pause failed: %s", e)
    elif src == "spotify_web":
        BROWSER.evaluate("spotify_web", WEB_PAUSE_JS)


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


# ------------------------------------------------------------------------ alerts
CHIME = "/tmp/plexpie-chime.wav"
HA_BASE = (cfg("HA_URL") or (f"http://{cfg('HA_HOST')}:8123" if cfg("HA_HOST") else "")).rstrip("/")


def make_chime():
    import math, struct, wave
    rate, frames = 48000, bytearray()
    for freq, dur in ((784, 0.55), (622, 0.9)):          # ding-dong
        for i in range(int(rate * dur)):
            t = i / rate
            v = math.exp(-3.2 * t) * (math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(4 * math.pi * freq * t))
            frames += struct.pack("<h", int(max(-1, min(1, v * 0.6)) * 32767))
    with wave.open(CHIME, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(bytes(frames))


def play_chime():
    try:
        if not os.path.exists(CHIME):
            make_chime()
        for cmd in (["pw-play", CHIME], ["aplay", "-q", CHIME]):
            try:
                if subprocess.run(cmd, timeout=10, capture_output=True).returncode == 0:
                    return
            except FileNotFoundError:
                continue
    except Exception as e:
        log.warning("chime failed: %s", e)


def show_alert(data):
    """Put a message (and a picture, e.g. a doorbell camera) on the screen for a while."""
    if isinstance(data, str):
        data = {"message": data}
    secs = max(5, min(600, int(data.get("seconds") or 30)))
    image = str(data.get("image") or "")
    if image.startswith("/") and HA_BASE:
        image = HA_BASE + image                  # a Home Assistant entity_picture path
    with ST.lock:
        ST.alert_n += 1
        n = ST.alert_n
        prev = ST.alert.get("prev") if ST.alert else ST.source
        ST.alert = {"id": n, "title": str(data.get("title") or "")[:80],
                    "message": str(data.get("message") or "")[:300], "image": image,
                    "refresh": bool(data.get("refresh", True)), "until": time.time() + secs, "prev": prev}
        ST.quiet_until = time.time() + secs + 15
    log.info("alert: %s", ST.alert["title"] or ST.alert["message"])
    if data.get("sound", True) not in (False, "no", "off", 0):
        spawn(play_chime)
    switch_source("alert")
    time.sleep(secs)
    with ST.lock:
        mine = ST.alert.get("id") == n
    if mine:
        end_alert()


def shutdown():
    """Stop the music, say when it's safe to unplug, then power off."""
    log.info("shutting down")
    pause_all()
    with ST.lock:
        ST.alert_n += 1
        ST.alert = {"id": ST.alert_n, "icon": "power", "title": "Shutting down",
                    "message": "Wait about 15 seconds, until the green light on the Pi stops blinking, then unplug the power.",
                    "image": "", "refresh": False, "until": time.time() + 120, "prev": "home"}
    SCREEN.wake("shutdown")
    switch_source("alert")
    HA.pub(f"{HA.base}/status", "offline")
    time.sleep(4)
    sudo("/usr/sbin/poweroff")


SLEEP_STEPS = (15, 30, 45, 60, 90)
SLEEP_FADE = 60                       # the last minute fades the volume down


def set_sleep(minutes):
    minutes = max(0, min(600, int(minutes)))
    with ST.lock:
        ST.sleep_until = time.time() + minutes * 60 if minutes else 0.0
    log.info("sleep timer %s", f"{minutes} min" if minutes else "off")
    mark_changed()


def cycle_sleep():
    with ST.lock:
        left = (ST.sleep_until - time.time()) / 60 if ST.sleep_until else 0
    nxt = next((m for m in SLEEP_STEPS if m > left + 1), 0)
    set_sleep(nxt)


def sleep_loop():
    fade_from = None
    last_min = None
    while True:
        time.sleep(1)
        try:
            with ST.lock:
                until = ST.sleep_until
            if not until:
                if fade_from is not None:            # cancelled during the fade
                    set_volume(fade_from)
                    fade_from = None
                continue
            left = until - time.time()
            if round(left / 60) != last_min:          # keep Home Assistant's minutes current
                last_min = round(left / 60)
                mark_changed()
            if 0 < left <= SLEEP_FADE:
                if fade_from is None:
                    fade_from = ST.volume if ST.volume is not None else (get_volume() or 50)
                set_volume(round(fade_from * (0.25 + 0.75 * left / SLEEP_FADE)))
            elif left <= 0:
                log.info("sleep timer: stopping the music")
                with ST.lock:
                    ST.sleep_until = 0.0
                pause_all()
                time.sleep(1.5)
                if fade_from is not None:
                    set_volume(fade_from)
                    fade_from = None
                SCREEN.set(False, "sleep timer")
        except Exception as e:
            log.warning("sleep loop: %s", e)


# ---------------------------------------------------------------- backup/restore
REPO_DIR = read_file("/etc/plexpie/repo_dir", "") or os.path.expanduser("~/plexpie")
NOT_RESTORED = {"APPLIANCE_USER"}     # differs per Pi


def conf_keys():
    keys = []
    for line in (read_file(os.path.join(REPO_DIR, "plexpie.conf.example"), "") or "").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m and m.group(1) not in keys:
            keys.append(m.group(1))
    return keys or sorted(k for k in C if re.match(r"^[A-Z][A-Z0-9_]*$", k))


def backup_json():
    keys = [k for k in conf_keys() if k not in NOT_RESTORED]
    return {"plexpie_backup": 1, "version": VERSION, "player_name": PLAYER_NAME,
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "settings": {k: cfg(k) for k in keys if cfg(k) != "" or k in C}}


def restore_backup(data):
    if not isinstance(data, dict) or data.get("plexpie_backup") != 1 or not isinstance(data.get("settings"), dict):
        return False, "This isn't a PlexPie backup file."
    allowed = set(conf_keys()) - NOT_RESTORED
    new = {k: str(v) for k, v in data["settings"].items()
           if k in allowed and "\n" not in str(v) and len(str(v)) < 500}
    if not new:
        return False, "The backup has no settings in it."
    path = os.path.join(REPO_DIR, "plexpie.conf")
    cur = load_conf(path) if os.path.exists(path) else dict(C)
    cur.update(new)
    order = conf_keys() + [k for k in cur if k not in conf_keys()]
    lines = ["# PlexPie settings, restored from a backup on " + time.strftime("%Y-%m-%d %H:%M")]
    lines += [f'{k}="{cur[k].replace(chr(34), "")}"' for k in order if k in cur]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    for f in (SETTINGS_JSON, SETTINGS_ENV):    # the restored file is now the source of truth
        try:
            os.remove(f)
        except OSError:
            pass
    save_overrides({k: v for k, v in new.items() if k in EDITABLE})
    log.info("restored %d settings from a backup, reinstalling", len(new))
    ok, out = admin("apply-run", timeout=20)
    return ok, "" if ok else (out.strip() or "Could not start the installer.")


def end_alert():
    with ST.lock:
        a, ST.alert = ST.alert, {}
        on_screen = ST.source
        ST.quiet_until = min(ST.quiet_until, time.time() + 5)
    mark_changed()
    if a and on_screen == "alert":
        back = a.get("prev")
        switch_source(back if back in SOURCES and back != "alert" else "home")


def set_shortcut(sid, on, publish=True):
    sc = SHORTCUTS.get(sid)
    if not sc:
        return
    on = bool(on)
    if sc["on"] != on:
        sc["on"] = on
        log.info("shortcut %s %s", sc["name"], "on" if on else "off")
        mark_changed()
    if publish:
        HA.pub(f"{HA.base}/shortcut/{sid}", "ON" if on else "OFF")


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
def poll_spotify():
    try:
        raw = http_get(SPOTIFY_API + "/status", 2)
        st = json.loads(raw) if raw else {}
    except Exception:
        st = {}
    tr = st.get("track") or {}
    playing = bool(tr) and not st.get("paused") and not st.get("stopped")
    md = {"title": tr.get("name", ""), "artist": ", ".join(tr.get("artist_names") or []),
          "album": tr.get("album_name", ""), "art_url": tr.get("album_cover_url", "")}
    with ST.lock:
        if ST.np.get("spotify") != md:
            ST.np["spotify"] = md
            mark_changed()
        ST.spotify_user = st.get("username", "")
    set_playing("spotify", playing)


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
            if "spotify" in SOURCES:
                poll_spotify()
            if "spotify_web" in SOURCES and n % 2 == 1:
                exists, val = BROWSER.evaluate("spotify_web", WEB_STATE_JS)
                if exists is False:
                    set_playing("spotify_web", False)
                elif isinstance(val, dict):
                    set_playing("spotify_web", val.get("playing"))
                    with ST.lock:
                        ST.np["spotify_web"] = {k: val.get(k, "") for k in ("title", "artist", "album")}
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
            if src in WEB_APPS:
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


class SwipeTracker:
    """Recognises edge swipes from normalised touch positions (0..1).

    * swipe right that starts at the left edge
    * swipe down that starts at the top edge
    """
    EDGE_X, EDGE_Y = 0.06, 0.07         # how close to the edge a swipe must start
    MIN_TRAVEL = 0.22                   # how far it must go (fraction of the screen)
    MAX_SECS = 1.2

    def __init__(self, on_swipe):
        self.on_swipe = on_swipe
        self.start = None
        self.last = None

    def down(self, x, y, t):
        self.start = (x, y, t)
        self.last = (x, y)

    def move(self, x=None, y=None):
        if self.start is None:
            return
        lx, ly = self.last
        self.last = (lx if x is None else x, ly if y is None else y)

    def up(self, t):
        if self.start is None or self.last is None:
            self.start = None
            return None
        x0, y0, t0 = self.start
        x1, y1 = self.last
        self.start = None
        dx, dy, dt = x1 - x0, y1 - y0, t - t0
        kind = None
        if dt <= self.MAX_SECS:
            if x0 <= self.EDGE_X and dx >= self.MIN_TRAVEL and abs(dy) < dx * 0.8:
                kind = "left-edge"
            elif y0 <= self.EDGE_Y and dy >= self.MIN_TRAVEL and abs(dx) < dy * 0.8:
                kind = "top-edge"
        if kind:
            self.on_swipe(kind)
        return kind


def on_swipe(kind):
    with ST.lock:
        src, screen_on = ST.source, ST.screen_on
    if screen_on and src != "home":
        log.info("swipe from the %s: home", kind)
        switch_source("home")


def find_touchscreen():
    import evdev
    best = None
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities(absinfo=False)
            abs_codes = caps.get(evdev.ecodes.EV_ABS, [])
            keys = caps.get(evdev.ecodes.EV_KEY, [])
            if evdev.ecodes.BTN_TOUCH in keys and (evdev.ecodes.ABS_X in abs_codes
                                                   or evdev.ecodes.ABS_MT_POSITION_X in abs_codes):
                best = dev
                if evdev.ecodes.INPUT_PROP_DIRECT in dev.input_props():
                    return dev            # a real touchscreen, not a touchpad
        except OSError:
            continue
    return best


def gesture_reader():
    """Watch the touchscreen itself, so swipes work in every app."""
    if not cfg_yes("SWIPE_HOME"):
        return
    try:
        import evdev
        from evdev import ecodes as e
    except ImportError:
        log.warning("python3-evdev missing: swipe-to-home is off")
        return
    tracker = SwipeTracker(on_swipe)
    rotate = cfg_int("TOUCH_ROTATE", 0) % 360
    while True:
        dev = find_touchscreen()
        if dev is None:
            time.sleep(10)
            continue
        log.info("touchscreen for gestures: %s", dev.name)
        try:
            caps = dict(dev.capabilities(absinfo=True).get(e.EV_ABS, []))
            use_mt = e.ABS_X not in caps
            cx, cy = (e.ABS_MT_POSITION_X, e.ABS_MT_POSITION_Y) if use_mt else (e.ABS_X, e.ABS_Y)
            ix, iy = caps[cx], caps[cy]
            slot, pos, touching, moved = 0, [None, None], False, False

            def norm():
                x = (pos[0] - ix.min) / max(1, ix.max - ix.min)
                y = (pos[1] - iy.min) / max(1, iy.max - iy.min)
                if rotate == 90:
                    x, y = y, 1 - x
                elif rotate == 180:
                    x, y = 1 - x, 1 - y
                elif rotate == 270:
                    x, y = 1 - y, x
                return x, y

            for ev in dev.read_loop():
                if ev.type == e.EV_ABS:
                    if ev.code == e.ABS_MT_SLOT:
                        slot = ev.value
                    elif slot == 0 or not use_mt:
                        if ev.code == cx:
                            pos[0], moved = ev.value, True
                        elif ev.code == cy:
                            pos[1], moved = ev.value, True
                elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                    touching = ev.value == 1
                    if not touching:
                        tracker.up(time.time())
                    moved = True
                elif ev.type == e.EV_SYN and moved and None not in pos:
                    moved = False
                    x, y = norm()
                    if touching and tracker.start is None:
                        tracker.down(x, y, time.time())
                    elif touching:
                        tracker.move(x, y)
        except OSError as err:
            log.warning("touchscreen read failed (%s), retrying", err)
            time.sleep(3)


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
                    restarted = BROWSER.ws_url is not None
                    BROWSER.ws_url = ws
                    if restarted:                    # tabs are gone: the launcher is showing
                        log.info("browser restarted, back to home")
                        with ST.lock:
                            ST.source = "home"
                        mark_changed()
        except Exception as e:
            log.warning("watchdog: %s", e)


# ------------------------------------------------------------------- web server
# --------------------------------------------------------------------- settings
OPTIONAL_UNITS = {"ENABLE_AIRPLAY": "plexpie-airplay", "ENABLE_SPOTIFY": "plexpie-spotify",
                  "ENABLE_MUSIC_ASSISTANT": "plexpie-squeezelite"}
OPTIONAL_DEFAULTS = {"ENABLE_AIRPLAY": "yes", "ENABLE_SPOTIFY": "no", "ENABLE_MUSIC_ASSISTANT": "yes"}
SESSIONS_FILE = os.path.join(SETTINGS_DIR, "sessions.json")
SESSION_DAYS = 365                # a phone stays signed in for a year (renewed on every visit)


def _load_sessions():
    try:
        with open(SESSIONS_FILE) as f:
            return {k: v for k, v in json.load(f).items() if v > time.time()}
    except (OSError, ValueError):
        return {}


def save_sessions():
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SESSIONS_FILE + ".tmp", "w") as f:
            json.dump(SESSIONS, f)
        os.chmod(SESSIONS_FILE + ".tmp", 0o600)
        os.replace(SESSIONS_FILE + ".tmp", SESSIONS_FILE)
    except OSError as e:
        log.warning("could not save phone sessions: %s", e)


SESSIONS = _load_sessions()       # phone-browser session token -> expiry (survives restarts)
LOGIN_FAILS = {"n": 0, "until": 0.0}


def ensure_pin():
    if not cfg("PHONE_PIN"):
        import secrets
        pin = f"{secrets.randbelow(1000000):06d}"
        save_overrides({"PHONE_PIN": pin})
        C["PHONE_PIN"] = pin


def apply_services():
    """Start the optional players that are switched on, stop the others."""
    for flag, unit in OPTIONAL_UNITS.items():
        if subprocess.run(["systemctl", "cat", unit], capture_output=True).returncode != 0:
            continue                                # not installed on this Pi
        want = cfg_yes(flag, OPTIONAL_DEFAULTS[flag])
        active = subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
        if want != active:
            sudo("/usr/bin/systemctl", "start" if want else "stop", unit)


ADMIN = "/opt/plexpie/plexpie-admin"


def admin(*args, stdin=None, timeout=60):
    """Run one of the root helper's actions; returns (ok, stdout)."""
    try:
        r = subprocess.run(["sudo", "-n", ADMIN, *args], input=stdin, capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            log.warning("admin %s failed: %s", args[0], (r.stderr or r.stdout).strip()[-300:])
        return r.returncode == 0, r.stdout
    except Exception as e:
        log.warning("admin %s failed: %s", args[0], e)
        return False, ""


def _json_lines(text):
    out = []
    for line in (text or "").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({})
    return out


def update_info():
    ok, out = admin("update-status", timeout=10)
    info, run_state = (_json_lines(out) + [{}, {}])[:2]
    if run_state.get("running") and time.time() - run_state.get("time", 0) > 3600:
        run_state["running"] = False               # a stale marker from an interrupted update
    return {**info, "run": run_state}


def update_checker():
    """Look for system and PlexPie updates twice a day."""
    time.sleep(120)
    while True:
        admin("update-check", timeout=600)
        mark_changed()
        time.sleep(12 * 3600)


def lan_address():
    out = run(["hostname", "-I"]).split()
    return next((a for a in out if "." in a), "")


def browser_call(method, params):
    """Run a DevTools command at browser level (e.g. clearing one site's data)."""
    ws_url = BROWSER.version()
    if not ws_url:
        return False
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=4, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        deadline = time.time() + 4
        while time.time() < deadline:
            if json.loads(ws.recv()).get("id") == 1:
                break
        ws.close()
        return True
    except Exception as e:
        log.warning("%s failed: %s", method, e)
        return False


SIGN_OUT_ORIGINS = {
    "apple": ["https://music.apple.com", "https://apple.com", "https://www.apple.com", "https://idmsa.apple.com",
              "https://authorize.music.apple.com"],
    "spotify_web": ["https://open.spotify.com", "https://spotify.com", "https://www.spotify.com",
                    "https://accounts.spotify.com"],
}


def sign_out(src):
    for origin in SIGN_OUT_ORIGINS.get(src, []):
        browser_call("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
    tab = Browser.find(src, BROWSER.tabs() or [])
    if tab:
        try:
            http_get(CDP + "/json/close/" + tab["id"], 3)
        except Exception:
            pass
    log.info("signed out of %s", src)


def accounts():
    settings_dir = os.path.expanduser("~/.local/share/Plexamp/Settings")
    plex_user = (read_file(os.path.join(settings_dir, "%40Plexamp%3Auser%3Aname")) or "").strip('"')
    plex_linked = os.path.exists(os.path.join(settings_dir, "%40Plexamp%3Auser%3Atoken"))
    out = [{"id": "plexamp", "name": "Plexamp",
            "status": (f"Linked to {plex_user}" if plex_user else "Linked") if plex_linked
            else "Not linked: run the installer again to link it", "ok": plex_linked}]
    if "apple" in SOURCES:
        _, auth = BROWSER.evaluate("apple", "(() => { try { return MusicKit.getInstance().isAuthorized } "
                                            "catch (e) { return null } })()")
        out.append({"id": "apple", "name": "Apple Music",
                    "status": "Signed in" if auth else ("Not signed in" if auth is False
                                                        else "Open Apple Music to check"),
                    "ok": None if auth is None else bool(auth), "action": "Sign out"})
    if "spotify" in SOURCES:
        with ST.lock:
            user = ST.spotify_user
        out.append({"id": "spotify", "name": "Spotify Connect",
                    "status": f"In use by {user}" if user else "Pick this player in the Spotify app",
                    "ok": bool(user)})
    if "spotify_web" in SOURCES:
        out.append({"id": "spotify_web", "name": "Spotify web player",
                    "status": "Sign in on the screen", "ok": None, "action": "Sign out"})
    if "ma" in SOURCES:
        out.append({"id": "ma", "name": "Music Assistant",
                    "status": "Reachable" if ST.ma_up else "Not reachable", "ok": ST.ma_up})
    out.append({"id": "ha", "name": "Home Assistant",
                "status": "Connected" if HA.connected else ("Not set up" if not cfg("MQTT_HOST") else "Not connected"),
                "ok": HA.connected})
    return out


def settings_json(local, host=None):
    vals = {}
    for k, kind in EDITABLE.items():
        if kind == "secret":
            vals[k] = ""
            vals[k + "_SET"] = bool(cfg(k))
        elif kind == "bool":
            vals[k] = cfg_yes(k, {"ENABLE_SPOTIFY": "no", "ENABLE_SPOTIFY_WEB": "no",
                                  "PHONE_ACCESS": "yes"}.get(k, "yes"))
        else:
            vals[k] = cfg(k)
    vals["MA_HOST"] = vals.get("MA_HOST") or cfg("HA_HOST")
    if not local:
        vals["PHONE_PIN"] = ""                  # the PIN is only shown on the player itself
    vals["AUTO_SHOW"] = bool(cfg("AUTO_SHOW_SOURCES", "plexamp airplay ma spotify").strip())
    installed = {flag: subprocess.run(["systemctl", "cat", unit], capture_output=True).returncode == 0
                 for flag, unit in OPTIONAL_UNITS.items()}
    addr = lan_address()
    ok, wifi = admin("wifi-status", timeout=10)
    try:
        wifi = json.loads(wifi) if ok else {}
    except ValueError:
        wifi = {}
    ok, prot = admin("protect-status", timeout=10)
    try:
        prot = json.loads(prot) if ok else {}
    except ValueError:
        prot = {}
    return {"values": vals, "wifi": wifi, "protect": prot, "updates": update_info(), "installed": installed, "accounts": accounts(), "player_name": PLAYER_NAME,
            "version": VERSION,
            "phone_url": f"http://{host}/settings" if host else (f"http://{addr}:{HTTP_PORT}/settings" if addr else ""),
            "local": local}


def apply_settings(data):
    clean = {}
    for k, v in data.items():
        if k == "AUTO_SHOW":
            clean["AUTO_SHOW_SOURCES"] = "plexamp airplay ma spotify" if v else ""
            continue
        kind = EDITABLE.get(k)
        if not kind or (kind == "secret" and not v):
            continue                       # empty password field = keep the current one
        if kind == "bool":
            clean[k] = "yes" if v in (True, "yes", "on", "true", 1) else "no"
        elif kind == "int":
            try:
                clean[k] = str(int(v))
            except (TypeError, ValueError):
                continue
        else:
            clean[k] = str(v).strip()
    if clean.get("PHONE_PIN") == "new":
        import secrets
        clean["PHONE_PIN"] = f"{secrets.randbelow(1000000):06d}"
        SESSIONS.clear()                  # a new PIN signs every phone out
        save_sessions()
    save_overrides(clean)
    log.info("settings saved: %s", ", ".join(k for k in clean if k != "MQTT_PASS"))

    def restart():                         # systemd starts us again with the new settings
        time.sleep(0.8)
        if any(k in clean for k in ("MA_HOST", "PLAYER_NAME")):
            sudo("/usr/bin/systemctl", "restart", "plexpie-squeezelite")
        os._exit(0)
    spawn(restart)
    return clean


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

    # Phone browsers reach only the Settings page, and only with the PIN.
    REMOTE_OPEN = ("/settings", "/api/login", "/manifest.json", "/icon.png")
    REMOTE_AUTH = ("/api/settings", "/api/account", "/api/system", "/api/backup", "/api/restore")

    def _local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _authed(self):
        if self._local():
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "plexpie" and SESSIONS.get(v, 0) > time.time():
                if SESSIONS[v] - time.time() < (SESSION_DAYS - 1) * 86400:
                    SESSIONS[v] = time.time() + SESSION_DAYS * 86400
                    save_sessions()
                return True
        return False

    def _gate(self, path):
        if self._local():
            return True
        if not cfg_yes("PHONE_ACCESS"):
            self._send(403, b'{"error":"phone access is off"}')
            return False
        if path in self.REMOTE_OPEN or (path in self.REMOTE_AUTH and self._authed()):
            return True
        self._send(401 if path in self.REMOTE_AUTH else 404, b'{"error":"not allowed"}')
        return False

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._gate(path):
            return
        if path in ("/", "/index.html"):
            return self._file("index.html")
        if path in ("/airplay", "/spotify"):
            return self._file("nowplaying.html")
        if path == "/settings":
            return self._file("settings.html")
        if path == "/timer":
            return self._file("timer.html")
        if path == "/alert":
            return self._file("alert.html")
        if path == "/api/alert":
            with ST.lock:
                a = {k: v for k, v in ST.alert.items() if k != "prev"}
            return self._send(200, json.dumps(a).encode())
        if path == "/manifest.json":
            return self._send(200, json.dumps({
                "name": f"{PLAYER_NAME} settings", "short_name": PLAYER_NAME, "start_url": "/settings",
                "scope": "/", "display": "standalone", "background_color": "#0b0b0f", "theme_color": "#0b0b0f",
                "icons": [{"src": "/icon.png", "sizes": "512x512", "type": "image/png"}]}).encode(),
                "application/manifest+json")
        if path == "/icon.png":
            return self._file("icon.png", "image/png")
        if path == "/api/settings":
            host = None if self._local() else self.headers.get("Host")
            return self._send(200, json.dumps(settings_json(self._local(), host)).encode())
        if path == "/api/state":
            return self._send(200, json.dumps(state_json()).encode())
        if path == "/api/backup":
            body = json.dumps(backup_json(), indent=1).encode()
            name = re.sub(r"[^A-Za-z0-9_-]+", "-", PLAYER_NAME).strip("-") or "plexpie"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="plexpie-{name}-{time.strftime("%Y%m%d")}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
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
        if not self._gate(path):
            return
        ok = True
        if path == "/api/login":
            if time.time() < LOGIN_FAILS["until"]:
                return self._send(429, b'{"error":"too many tries, wait a minute"}')
            if str(data.get("pin", "")) == cfg("PHONE_PIN") and cfg("PHONE_PIN"):
                import secrets
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = time.time() + SESSION_DAYS * 86400
                save_sessions()
                LOGIN_FAILS["n"] = 0
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"plexpie={token}; Max-Age={SESSION_DAYS * 86400}; HttpOnly; SameSite=Lax; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            LOGIN_FAILS["n"] += 1
            if LOGIN_FAILS["n"] >= 5:
                LOGIN_FAILS.update(n=0, until=time.time() + 60)
            return self._send(403, b'{"error":"wrong PIN"}')
        if path == "/api/settings":
            saved = apply_settings(data)
            return self._send(200, json.dumps({"ok": True, "saved": sorted(saved)}).encode())
        if path == "/api/restore":
            ok, msg = restore_backup(data)
            return self._send(200 if ok else 400, json.dumps({"ok": ok, "error": msg}).encode())
        if path == "/api/account":
            if data.get("action") == "signout" and data.get("id") in SIGN_OUT_ORIGINS:
                spawn(sign_out, data["id"])
            return self._send(200, b'{"ok":true}')
        if path == "/api/system":
            what = data.get("action")
            if what == "restart":
                spawn(lambda: (time.sleep(0.5), run(["pkill", "-f", "plexpie-chromium"]), os._exit(0)))
            elif what == "reboot":
                spawn(lambda: (time.sleep(0.5), sudo("/usr/sbin/reboot")))
            elif what == "shutdown":
                spawn(shutdown)
            elif what in ("protect-on", "protect-off"):
                ok, out = admin(what, timeout=180)
                if ok:
                    spawn(lambda: (time.sleep(1), sudo("/usr/sbin/reboot")))
                return self._send(200 if ok else 500, json.dumps({"ok": ok, "error": out.strip()[-200:]}).encode())
            elif what in ("wifi-on", "wifi-off"):
                ok, _ = admin(what, timeout=20)
                return self._send(200 if ok else 500, json.dumps({"ok": ok}).encode())
            elif what == "wifi-scan":
                ok, out = admin("wifi-scan", timeout=40)
                return self._send(200, out.encode() if ok and out.strip() else b"[]")
            elif what == "wifi-connect":
                ok, out = admin("wifi-connect", str(data.get("ssid", ""))[:32],
                                stdin=str(data.get("password", "")) + "\n", timeout=60)
                return self._send(200 if ok else 500, json.dumps({"ok": ok}).encode())
            elif what == "update-check":
                admin("update-check", timeout=600)
                return self._send(200, json.dumps(update_info()).encode())
            elif what == "update-run":
                ok, _ = admin("update-run", timeout=20)
                return self._send(200 if ok else 500, json.dumps({"ok": ok}).encode())
            elif what == "update-status":
                return self._send(200, json.dumps(update_info()).encode())
            return self._send(200, b'{"ok":true}')
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
        elif path == "/api/alert":
            spawn(show_alert, data)
        elif path == "/api/sleep":
            if "minutes" in data:
                set_sleep(data["minutes"])
            else:
                cycle_sleep()
        elif path == "/api/alert/dismiss":
            spawn(end_alert)
        elif path == "/api/shortcut":
            sid = str(data.get("id", ""))
            ok = sid in SHORTCUTS
            if ok:
                set_shortcut(sid, data.get("on", not SHORTCUTS[sid]["on"]))
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
               "manufacturer": "PlexPie", "sw_version": VERSION,
               "model": (read_file("/proc/device-tree/model", "Raspberry Pi") or "Raspberry Pi").strip("\x00")}
        payload = {"unique_id": f"plexpie_{self.node}_{oid}", "availability_topic": f"{self.base}/status",
                   "device": dev, **conf}
        self.client.publish(f"homeassistant/{component}/{self.node}/{oid}/config", json.dumps(payload), retain=True)

    def on_connect(self, client, userdata, flags, rc, *args):
        log.info("MQTT connected (%s)", rc)
        self.connected = True
        b = self.base
        self.disc("select", "source", {"name": "Source", "icon": "mdi:television-guide",
                                        "state_topic": f"{b}/source", "command_topic": f"{b}/source/set",
                                        "options": [v["name"] for v in SOURCES.values()
                                                    if not v.get("hidden")]})
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
                                ("reboot", "Reboot", "mdi:restart-alert"),
                                ("shutdown", "Shut down", "mdi:power")):
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
        self.disc("update", "software", {"name": "Software", "device_class": "firmware",
                                         "state_topic": f"{b}/update", "command_topic": f"{b}/update/set",
                                         "payload_install": "install", "title": "PlexPie + Raspberry Pi OS",
                                         "release_url": "https://github.com/ROGUEONE-SZ/plexamp-raspberry-pi-install"})
        self.disc("sensor", "cpu_temp", {"name": "CPU temperature", "device_class": "temperature",
                                         "unit_of_measurement": "°C", "entity_category": "diagnostic",
                                         "state_topic": f"{b}/cpu_temp"})
        self.disc("notify", "alert", {"name": "Screen alert", "icon": "mdi:message-alert",
                                      "command_topic": f"{b}/alert/set"})
        self.disc("number", "sleep_timer", {"name": "Sleep timer", "icon": "mdi:sleep", "min": 0, "max": 180,
                                            "step": 5, "unit_of_measurement": "min", "mode": "box",
                                            "state_topic": f"{b}/sleep", "command_topic": f"{b}/sleep/set"})
        for sid, sc in SHORTCUTS.items():
            self.disc("switch", f"shortcut_{sid}", {"name": sc["name"], "icon": "mdi:gesture-tap-button",
                                                   "state_topic": f"{b}/shortcut/{sid}",
                                                   "command_topic": f"{b}/shortcut/{sid}/set"})
        client.publish(f"{b}/status", "online", retain=True)
        client.subscribe(f"{b}/+/set")
        client.subscribe(f"{b}/shortcut/#")   # commands, and the retained state from before a restart
        self.last = {}
        self.art_sent = -1
        self.publish_state()

    def on_disconnect(self, *args):
        self.connected = False
        log.warning("MQTT disconnected")

    def on_message(self, client, userdata, msg):
        rel = msg.topic[len(self.base) + 1:]
        val = msg.payload.decode("utf-8", "replace").strip()
        if rel.startswith("shortcut/"):
            parts = rel.split("/")
            if len(parts) == 3 and parts[2] == "set":
                log.info("HA command shortcut %s=%s", parts[1], val)
                set_shortcut(parts[1], val.upper() == "ON")
            elif len(parts) == 2 and msg.retain and parts[1] in SHORTCUTS:
                set_shortcut(parts[1], val.upper() == "ON", publish=False)   # state from before a restart
            return
        key = rel.rsplit("/set", 1)[0]
        log.info("HA command %s=%s", key, val[:120])
        try:
            if key == "alert":
                try:
                    payload = json.loads(val)
                except ValueError:
                    payload = val
                spawn(show_alert, payload if isinstance(payload, (dict, str)) else str(payload))
                return
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
            elif key == "update" and val == "install":
                spawn(admin, "update-run")
            elif key == "reboot":
                sudo("/usr/sbin/reboot")
            elif key == "shutdown":
                spawn(shutdown)
            elif key == "sleep":
                set_sleep(float(val))
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
            "sleep": str(-(-s["sleep_left"] // 60)),
        }
        for k, v in values.items():
            if v != "" and self.last.get(k) != v:
                self.pub(f"{self.base}/{k}", v)
                self.last[k] = v
        try:
            u = update_info()
            pending = int(u.get("os") or 0) + int(u.get("plexpie") or 0)
            upd = json.dumps({"installed_version": VERSION,
                              "latest_version": VERSION if not pending else f"{VERSION} + {pending} updates",
                              "in_progress": bool(u.get("run", {}).get("running")),
                              "update_percentage": u.get("run", {}).get("percent")
                              if u.get("run", {}).get("running") else None})
            if self.last.get("update") != upd:
                self.pub(f"{self.base}/update", upd)
                self.last["update"] = upd
        except Exception:
            pass
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
    ensure_pin()
    bind = "0.0.0.0" if cfg_yes("PHONE_ACCESS") else "127.0.0.1"
    server = Server((bind, HTTP_PORT), Handler)
    tabs = BROWSER.tabs() or []                 # after a settings restart, keep what is on screen
    if tabs and source_for_url(tabs[0].get("url", "")) in SOURCES:
        ST.source = source_for_url(tabs[0]["url"])
    spawn(apply_services)
    spawn(server.serve_forever)
    SCREEN.apply()
    if "airplay" in SOURCES:
        spawn(airplay_reader)
    for fn in (fast_poller, slow_poller, focus_poller, gesture_reader, screen_loop, watchdog, update_checker,
               sleep_loop):
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
