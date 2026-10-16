#!/usr/bin/env python3
"""PlexPie overlay: the floating Home button and the touch-to-wake shield.

Two small always-on-top X windows drawn over Chromium:
  * Home button  - shown whenever an app other than the launcher is on screen
  * Wake shield  - a black full-screen window while the backlight is off, so
                   the first tap only wakes the screen instead of pressing
                   whatever button happens to be underneath.
"""
import json
import tkinter as tk
import urllib.request

API = "http://127.0.0.1:8080"
SIZE = 60
MARGIN = 12
POLL_MS = 400


def call(path, data=None):
    try:
        req = urllib.request.Request(API + path, method="POST" if data is not None else "GET",
                                     data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


root = tk.Tk()
root.withdraw()
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()


def corner(pos):
    pos = (pos or "bottom-left").lower()
    x = MARGIN if "left" in pos else sw - SIZE - MARGIN
    y = MARGIN if "top" in pos else sh - SIZE - MARGIN
    return x, y


# ---- Home button
home = tk.Toplevel(root)
home.overrideredirect(True)
home.configure(bg="#000000")
canvas = tk.Canvas(home, width=SIZE, height=SIZE, bg="#000000", highlightthickness=0)
canvas.pack()
canvas.create_oval(2, 2, SIZE - 2, SIZE - 2, fill="#1c1c1e", outline="#48484a", width=2)
cx, cy = SIZE / 2, SIZE / 2
canvas.create_polygon(cx - 14, cy - 1, cx, cy - 14, cx + 14, cy - 1, fill="", outline="#f2f2f7", width=3,
                      joinstyle="round")
canvas.create_line(cx - 10, cy - 2, cx - 10, cy + 13, cx + 10, cy + 13, cx + 10, cy - 2, fill="#f2f2f7", width=3,
                   joinstyle="round", capstyle="round")
canvas.bind("<ButtonRelease-1>", lambda e: call("/api/source", {"source": "home"}))
home.withdraw()

# ---- Wake shield
shield = tk.Toplevel(root)
shield.overrideredirect(True)
shield.configure(bg="#000000", cursor="none")
shield.geometry(f"{sw}x{sh}+0+0")
shield.bind("<ButtonPress-1>", lambda e: call("/api/wake", {}))
shield.withdraw()

state = {"pos": None, "home": False, "shield": False, "misses": 0}


def show(win, key, visible):
    if visible and not state[key]:
        win.deiconify()
    elif not visible and state[key]:
        win.withdraw()
    if visible:
        win.lift()
    state[key] = visible


def tick():
    s = call("/api/state")
    if s is None:
        state["misses"] += 1
        if state["misses"] > 10:          # daemon gone: never leave a black screen up
            show(shield, "shield", False)
            show(home, "home", False)
    else:
        state["misses"] = 0
        pos = s.get("home_button")
        if pos != state["pos"]:
            x, y = corner(pos)
            home.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
            state["pos"] = pos
        screen_on = s.get("screen_on", True)
        show(shield, "shield", not screen_on)
        show(home, "home", screen_on and s.get("source") != "home")
    root.after(POLL_MS, tick)


root.after(500, tick)
root.mainloop()
