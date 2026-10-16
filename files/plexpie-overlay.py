#!/usr/bin/env python3
"""PlexPie overlay: Home pill, pop-up keyboard and touch-to-wake shield.

Small always-on-top X windows drawn over Chromium:
  * Home pill    - a rounded Home button that appears when you touch the screen
                   inside an app and fades out again after a few seconds idle.
                   It has a small keyboard button as a manual fallback.
  * Keyboard     - pops up by itself when a text field is focused (the daemon
                   watches the page). It always sits at the bottom and the app
                   shrinks to the space above it with the field scrolled into
                   view. Types with real key presses (xdotool), so it works in
                   sign-in pop-ups too.
  * Wake shield  - black full-screen window while the backlight is off, so the
                   first tap only wakes the screen.
"""
import json
import math
import subprocess
import threading
import time
import tkinter as tk
import urllib.request

API = "http://127.0.0.1:8080"
POLL_MS = 300

PANEL = "#151517"
KEY = "#3a3a3d"
KEY_SPECIAL = "#262629"
KEY_DOWN = "#6b6b70"
FG = "#f5f5f7"
FG_DIM = "#a1a1a6"
ACCENT = "#0a84ff"
PILL = "#232326"
FONT = "DejaVu Sans"


def call(path, data=None):
    try:
        req = urllib.request.Request(API + path, method="POST" if data is not None else "GET",
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


def xdo(*args):
    try:
        return subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def x_idle_secs():
    try:
        out = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2).stdout.strip()
        return int(out) / 1000 if out.isdigit() else 0.0
    except Exception:
        return 0.0


root = tk.Tk()
root.withdraw()
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()


# ------------------------------------------------- rounded (shaped) windows
try:
    from Xlib import display as xdisplay
    from Xlib.ext import shape as xshape
    XD = xdisplay.Display()
    HAS_SHAPE = XD.has_extension("SHAPE")
except Exception:
    XD, HAS_SHAPE = None, False


def round_shape(win, w, h, radius):
    """Clip a window to a rounded rectangle so no square corners show."""
    if not HAS_SHAPE:
        return
    try:
        win.update_idletasks()
        xid = int(win.wm_frame(), 16)
        rects = []
        for y in range(h):
            dy = 0.0
            if y < radius:
                dy = radius - y - 0.5
            elif y >= h - radius:
                dy = y - (h - radius) + 0.5
            inset = 0 if dy <= 0 else radius - math.sqrt(max(0.0, radius * radius - dy * dy))
            x0 = int(round(inset))
            rects.append((x0, y, w - 2 * x0, 1))
        XD.create_resource_object("window", xid).shape_rectangles(
            xshape.SO.Set, xshape.SK.Bounding, 0, 0, 0, rects)
        XD.flush()
    except Exception:
        pass


def rounded_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# ------------------------------------------------------------ typing target
def chromium_window():
    for wid in xdo("search", "--onlyvisible", "--class", "[Cc]hromium").split():
        geo = xdo("getwindowgeometry", "--shell", wid)
        width = next((int(l.split("=")[1]) for l in geo.splitlines() if l.startswith("WIDTH=")), 0)
        if width >= sw * 0.9:
            return wid
    return None


def focus_app():
    wid = chromium_window()
    if wid:
        xdo("windowfocus", "--sync", wid)


def send_text(text):
    focus_app()
    xdo("type", "--delay", "0", "--", text)


def send_key(name):
    focus_app()
    xdo("key", "--clearmodifiers", name)


# ---------------------------------------------------------------- home pill
PILL_W, PILL_H, MARGIN = 132, 52, 14
pill = tk.Toplevel(root)
pill.overrideredirect(True)
pill.configure(bg=PILL)
pc = tk.Canvas(pill, width=PILL_W, height=PILL_H, bg=PILL, highlightthickness=0)
pc.pack()
# home icon (left half)
hx, hy = PILL_W * 0.27, PILL_H / 2
pc.create_line(hx - 12, hy + 1, hx, hy - 11, hx + 12, hy + 1, fill=FG, width=2.5, capstyle="round", joinstyle="round")
pc.create_line(hx - 8, hy - 2, hx - 8, hy + 11, hx + 8, hy + 11, hx + 8, hy - 2, fill=FG, width=2.5,
               capstyle="round", joinstyle="round")
pc.create_line(PILL_W / 2, 14, PILL_W / 2, PILL_H - 14, fill="#3f3f44", width=1)
# keyboard icon (right half)
kx, ky = PILL_W * 0.73, PILL_H / 2
rounded_rect(pc, kx - 15, ky - 10, kx + 15, ky + 10, 4, fill="", outline=FG, width=2)
for row in (-4, 1):
    for i in range(5):
        x = kx - 10 + i * 5
        pc.create_rectangle(x, ky + row, x + 1.5, ky + row + 1.5, fill=FG, outline=FG)
pc.create_line(kx - 7, ky + 6, kx + 7, ky + 6, fill=FG, width=2, capstyle="round")


def pill_tap(e):
    if e.x < PILL_W / 2:
        hide_keyboard()
        call("/api/source", {"source": "home"})
    else:
        if state["kb"]:
            hide_keyboard(user=True)
        else:
            show_keyboard(auto=False)


pc.bind("<ButtonRelease-1>", pill_tap)
pill.withdraw()

# ----------------------------------------------------------------- keyboard
KB_H = 252
PAD, GAP = 8, 6
ROWS_LETTERS = [
    [("1",), ("2",), ("3",), ("4",), ("5",), ("6",), ("7",), ("8",), ("9",), ("0",)],
    [(c,) for c in "qwertyuiop"],
    [(c,) for c in "asdfghjkl"],
    [("⇧", 1.5, "shift")] + [(c,) for c in "zxcvbnm"] + [("⌫", 1.5, "back")],
    [("123", 1.5, "symbols"), ("@",), ("space", 4.0, "space"), (".",), ("return", 1.5, "enter"),
     ("⌄", 1.0, "hide")],
]
ROWS_SYMBOLS = [
    [(c,) for c in "1234567890"],
    [(c,) for c in "-/:;()$&@\""],
    [(c,) for c in ".,?!'#%*+"],
    [("tab", 1.5, "tab")] + [(c,) for c in "_=<>[]~"] + [("⌫", 1.5, "back")],
    [("abc", 1.5, "symbols"), (".com", 1.0, "dotcom"), ("space", 4.0, "space"), ("\\",),
     ("return", 1.5, "enter"), ("⌄", 1.0, "hide")],
]

kb = tk.Toplevel(root)
kb.overrideredirect(True)
kb.configure(bg=PANEL)
kc = tk.Canvas(kb, width=sw, height=KB_H, bg=PANEL, highlightthickness=0)
kc.pack()
kb.withdraw()
kb_state = {"shift": False, "symbols": False, "top": False, "keys": [], "pressed": None, "repeat": None}


def layout():
    kc.delete("all")
    keys = []
    rows = ROWS_SYMBOLS if kb_state["symbols"] else ROWS_LETTERS
    row_h = (KB_H - 2 * PAD - GAP * (len(rows) - 1)) / len(rows)
    unit = (sw - 2 * PAD - GAP * 9) / 10 + GAP          # width of a 1-unit key incl. gap
    kc.create_line(0, 0.5, sw, 0.5, fill="#2c2c2e")
    for r, row in enumerate(rows):
        units = sum(k[1] if len(k) > 1 else 1 for k in row)
        x = PAD + (10 - units) * unit / 2                  # centre short rows
        y0 = PAD + r * (row_h + GAP)
        for k in row:
            label = k[0]
            w_units = k[1] if len(k) > 1 else 1
            action = k[2] if len(k) > 2 else "char"
            w = w_units * unit - GAP
            special = action not in ("char", "space")
            fill = ACCENT if action == "enter" else (KEY_SPECIAL if special else KEY)
            if action == "shift" and kb_state["shift"]:
                fill = FG
            shown = label.upper() if (action == "char" and kb_state["shift"]) else label
            body = rounded_rect(kc, x, y0, x + w, y0 + row_h, 9, fill=fill, outline="")
            size = -24 if (action == "char" and len(label) == 1) else -17
            color = FG if (not special or action == "enter") else FG_DIM
            if action in ("back", "shift", "hide"):
                color = PANEL if (action == "shift" and kb_state["shift"]) else FG
            draw_key_face(x, y0, w, row_h, action, shown, color, size)
            keys.append({"box": (x, y0, x + w, y0 + row_h), "body": body, "fill": fill, "label": label,
                         "action": action})
            x += w + GAP
    kb_state["keys"] = keys


def draw_key_face(x, y, w, h, action, label, color, size):
    """Key label; shift, delete and hide are drawn as icons (no font glyphs needed)."""
    cx, cy = x + w / 2, y + h / 2
    if action == "shift":
        kc.create_polygon(cx, cy - 11, cx + 10, cy, cx + 5, cy, cx + 5, cy + 9, cx - 5, cy + 9, cx - 5, cy,
                          cx - 10, cy, fill="" if color != PANEL else PANEL, outline=color, width=2,
                          joinstyle="round")
    elif action == "back":
        kc.create_polygon(cx - 15, cy, cx - 7, cy - 9, cx + 14, cy - 9, cx + 14, cy + 9, cx - 7, cy + 9,
                          fill="", outline=color, width=2, joinstyle="round")
        kc.create_line(cx - 2, cy - 4, cx + 6, cy + 4, fill=color, width=2, capstyle="round")
        kc.create_line(cx + 6, cy - 4, cx - 2, cy + 4, fill=color, width=2, capstyle="round")
    elif action == "hide":
        kc.create_line(cx - 9, cy - 4, cx, cy + 5, cx + 9, cy - 4, fill=color, width=2.5,
                       capstyle="round", joinstyle="round")
    else:
        kc.create_text(cx, cy, text=label, fill=color, font=(FONT, size))


def key_at(x, y):
    for k in kb_state["keys"]:
        x0, y0, x1, y1 = k["box"]
        if x0 - GAP / 2 <= x <= x1 + GAP / 2 and y0 - GAP / 2 <= y <= y1 + GAP / 2:
            return k
    return None


def fire(k):
    a = k["action"]
    if a == "char":
        ch = k["label"].upper() if kb_state["shift"] else k["label"]
        send_text(ch)
        if kb_state["shift"]:
            kb_state["shift"] = False
            layout()
    elif a == "space":
        send_text(" ")
    elif a == "dotcom":
        send_text(".com")
    elif a == "back":
        send_key("BackSpace")
    elif a == "enter":
        send_key("Return")
    elif a == "tab":
        send_key("Tab")
    elif a == "shift":
        kb_state["shift"] = not kb_state["shift"]
        layout()
    elif a == "symbols":
        kb_state["symbols"] = not kb_state["symbols"]
        kb_state["shift"] = False
        layout()
    elif a == "hide":
        hide_keyboard(user=True)


def repeat_back():
    send_key("BackSpace")
    kb_state["repeat"] = root.after(70, repeat_back)


def kb_press(e):
    k = key_at(e.x, e.y)
    kb_state["pressed"] = k
    if k:
        kc.itemconfigure(k["body"], fill=KEY_DOWN)
        if k["action"] == "back":
            kb_state["repeat"] = root.after(450, repeat_back)


def kb_release(e):
    k = kb_state["pressed"]
    if kb_state["repeat"]:
        root.after_cancel(kb_state["repeat"])
        kb_state["repeat"] = None
    kb_state["pressed"] = None
    if not k:
        return
    try:
        kc.itemconfigure(k["body"], fill=k["fill"])
    except tk.TclError:
        pass
    if key_at(e.x, e.y) is k:
        fire(k)


kc.bind("<ButtonPress-1>", kb_press)
kc.bind("<ButtonRelease-1>", kb_release)


def place_keyboard(field_y=None):
    kb.geometry(f"{sw}x{KB_H}+0+{sh - KB_H}")         # always at the bottom, like a phone
    place_pill()


def api_async(path, data):
    threading.Thread(target=call, args=(path, data), daemon=True).start()


def show_keyboard(auto=True, field_y=None):
    was_open = state["kb"]
    state["kb"] = True
    state["kb_auto"] = auto
    layout()
    place_keyboard(field_y)
    if not was_open:
        kb.deiconify()
        api_async("/api/keyboard", {"open": True, "height": sh - KB_H})   # app shrinks above the keys
    kb.lift()
    pill.lift()
    focus_app()


def hide_keyboard(user=False):
    if state["kb"]:
        kb.withdraw()
        api_async("/api/keyboard", {"open": False})
    state["kb"] = False
    if user:
        state["dismissed"] = True        # stay closed until the field loses focus
    place_pill()


# -------------------------------------------------------------------- shield
shield = tk.Toplevel(root)
shield.overrideredirect(True)
shield.configure(bg="#000000", cursor="none")
shield.geometry(f"{sw}x{sh}+0+0")
shield.bind("<ButtonPress-1>", lambda e: call("/api/wake", {}))
shield.withdraw()

state = {"pos": "top-left", "pill": False, "shield": False, "kb": False, "kb_auto": False,
         "dismissed": False, "misses": 0, "no_focus": 0, "autohide": 5, "shown_at": 0.0}


def place_pill():
    pos = (state["pos"] or "bottom-left").lower()
    top = "top" in pos
    if state["kb"] and top == kb_state["top"]:
        top = not top                                    # keep clear of the keyboard
    x = MARGIN if "left" in pos else sw - PILL_W - MARGIN
    y = MARGIN if top else sh - PILL_H - MARGIN
    pill.geometry(f"{PILL_W}x{PILL_H}+{x}+{y}")


def show(win, key, visible):
    if visible and not state[key]:
        win.deiconify()
        if win is pill:
            round_shape(pill, PILL_W, PILL_H, PILL_H // 2)
    elif not visible and state[key]:
        win.withdraw()
    if visible:
        win.lift()
    state[key] = visible


def tick():
    s = call("/api/state")
    if s is None:
        state["misses"] += 1
        if state["misses"] > 10:                         # daemon gone: never leave a black screen up
            show(shield, "shield", False)
            show(pill, "pill", False)
            hide_keyboard()
        root.after(POLL_MS, tick)
        return
    state["misses"] = 0
    state["autohide"] = s.get("home_autohide", 5)
    if s.get("home_button") != state["pos"]:
        state["pos"] = s.get("home_button")
        place_pill()
    screen_on = s.get("screen_on", True)
    in_app = screen_on and s.get("source") != "home"

    # keyboard follows text-field focus in the app
    focused = in_app and s.get("text_focus")
    if focused:
        state["no_focus"] = 0
        if not state["kb"] and not state["dismissed"]:
            show_keyboard(auto=True, field_y=s.get("text_y"))
    else:
        state["no_focus"] += 1
        if state["no_focus"] >= 3:                       # ~1 s without a focused field
            state["dismissed"] = False
            if state["kb"] and state["kb_auto"]:
                hide_keyboard()
    if not in_app and state["kb"]:
        hide_keyboard()
    if state["kb"]:
        kb.lift()

    # Home pill: on touch, then fades out; always there while the keyboard is open
    autohide = state["autohide"]
    recently_touched = x_idle_secs() < (autohide if autohide > 0 else 1e9)
    show(pill, "pill", in_app and (recently_touched or state["kb"]))
    show(shield, "shield", not screen_on)
    root.after(POLL_MS, tick)


place_pill()
root.after(500, tick)
root.mainloop()
