#!/usr/bin/env python3
"""PlexPie overlay: Home button, on-screen keyboard and touch-to-wake shield.

Small always-on-top X windows drawn over Chromium:
  * Home button      - shown whenever an app other than the launcher is on screen
  * Keyboard button  - next to Home; opens the on-screen keyboard
  * Keyboard         - types into whatever field is focused in the app (real
                       key presses via xdotool, so it works in sign-in pop-ups too)
  * Wake shield      - a black full-screen window while the backlight is off, so
                       the first tap only wakes the screen instead of pressing
                       whatever button happens to be underneath.
"""
import json
import subprocess
import tkinter as tk
import urllib.request

API = "http://127.0.0.1:8080"
SIZE = 60
MARGIN = 12
POLL_MS = 400

BG = "#1c1c1e"
KEY = "#3a3a3c"
KEY_DOWN = "#636366"
KEY_SPECIAL = "#2c2c2e"
FG = "#f2f2f7"
ACCENT = "#0a84ff"


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


root = tk.Tk()
root.withdraw()
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()


# ------------------------------------------------------------- typing target
def chromium_window():
    """The full-screen Chromium window id, or None."""
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


# ------------------------------------------------------------ round buttons
def round_button(icon_draw, on_tap):
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.configure(bg="#000000")
    c = tk.Canvas(win, width=SIZE, height=SIZE, bg="#000000", highlightthickness=0)
    c.pack()
    c.create_oval(2, 2, SIZE - 2, SIZE - 2, fill=BG, outline="#48484a", width=2)
    icon_draw(c, SIZE / 2, SIZE / 2)
    c.bind("<ButtonRelease-1>", lambda e: on_tap())
    win.withdraw()
    return win


def draw_home(c, cx, cy):
    c.create_polygon(cx - 14, cy - 1, cx, cy - 14, cx + 14, cy - 1, fill="", outline=FG, width=3, joinstyle="round")
    c.create_line(cx - 10, cy - 2, cx - 10, cy + 13, cx + 10, cy + 13, cx + 10, cy - 2, fill=FG, width=3,
                  joinstyle="round", capstyle="round")


def draw_keyboard(c, cx, cy):
    c.create_rectangle(cx - 16, cy - 10, cx + 16, cy + 11, outline=FG, width=2)
    for row, y in enumerate((cy - 5, cy + 1)):
        for i in range(5):
            x = cx - 12 + i * 6
            c.create_rectangle(x, y, x + 2, y + 2, fill=FG, outline=FG)
    c.create_line(cx - 8, cy + 7, cx + 8, cy + 7, fill=FG, width=2)


def go_home():
    hide_keyboard()
    call("/api/source", {"source": "home"})


def toggle_keyboard():
    if state["kb"]:
        hide_keyboard()
    else:
        show_keyboard()


home = round_button(draw_home, go_home)
kbd_btn = round_button(draw_keyboard, toggle_keyboard)

# ---------------------------------------------------------------- keyboard
KB_H = 236
LETTERS = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"]
SYMBOLS = ["1234567890", "@#$%&*-+=/", "!?:;'\"()_", ".,~^<>[]"]

kb = tk.Toplevel(root)
kb.overrideredirect(True)
kb.configure(bg=BG)
kb.withdraw()
kb_state = {"shift": False, "symbols": False, "top": False}
kb_frame = tk.Frame(kb, bg=BG)
kb_frame.pack(fill="both", expand=True, padx=4, pady=4)


def key_label(parent, text, cmd, width=1, bg=KEY, font_size=17):
    lbl = tk.Label(parent, text=text, bg=bg, fg=FG, font=("DejaVu Sans", font_size, "bold"), bd=0,
                   relief="flat", padx=0, pady=0)
    lbl.pack(side="left", fill="both", expand=True, padx=3, pady=3, ipadx=width * 2)

    def press(e):
        lbl.configure(bg=KEY_DOWN)

    def release(e):
        lbl.configure(bg=bg)
        cmd()

    lbl.bind("<ButtonPress-1>", press)
    lbl.bind("<ButtonRelease-1>", release)
    return lbl


def type_char(ch):
    if kb_state["shift"] and not kb_state["symbols"]:
        ch = ch.upper()
        kb_state["shift"] = False
        build_keys()
    send_text(ch)


def toggle_shift():
    kb_state["shift"] = not kb_state["shift"]
    build_keys()


def toggle_symbols():
    kb_state["symbols"] = not kb_state["symbols"]
    kb_state["shift"] = False
    build_keys()


def toggle_side():
    kb_state["top"] = not kb_state["top"]
    place_keyboard()


def build_keys():
    for w in kb_frame.winfo_children():
        w.destroy()
    rows = SYMBOLS if kb_state["symbols"] else LETTERS
    for i, row in enumerate(rows):
        fr = tk.Frame(kb_frame, bg=BG)
        fr.pack(fill="both", expand=True)
        if i == 3 and not kb_state["symbols"]:
            key_label(fr, "⇧", toggle_shift, bg=ACCENT if kb_state["shift"] else KEY_SPECIAL)
        for ch in row:
            shown = ch.upper() if (kb_state["shift"] and not kb_state["symbols"]) else ch
            key_label(fr, shown, lambda c=ch: type_char(c))
        if i == 3:
            key_label(fr, "⌫", lambda: send_key("BackSpace"), bg=KEY_SPECIAL)
    fr = tk.Frame(kb_frame, bg=BG)
    fr.pack(fill="both", expand=True)
    key_label(fr, "abc" if kb_state["symbols"] else "123", toggle_symbols, bg=KEY_SPECIAL, font_size=14)
    key_label(fr, "@", lambda: send_text("@"), bg=KEY_SPECIAL)
    key_label(fr, "space", lambda: send_text(" "), width=12, font_size=14)
    key_label(fr, ".", lambda: send_text("."), bg=KEY_SPECIAL)
    key_label(fr, ".com", lambda: send_text(".com"), bg=KEY_SPECIAL, font_size=13)
    key_label(fr, "tab", lambda: send_key("Tab"), bg=KEY_SPECIAL, font_size=13)
    key_label(fr, "⏎", lambda: send_key("Return"), bg=ACCENT)
    key_label(fr, "⇅", toggle_side, bg=KEY_SPECIAL)
    key_label(fr, "✕", hide_keyboard, bg=KEY_SPECIAL)


def place_keyboard():
    y = 0 if kb_state["top"] else sh - KB_H
    kb.geometry(f"{sw}x{KB_H}+0+{y}")
    place_buttons()


def show_keyboard():
    build_keys()
    place_keyboard()
    kb.deiconify()
    kb.lift()
    state["kb"] = True
    focus_app()


def hide_keyboard():
    kb.withdraw()
    state["kb"] = False
    place_buttons()


# ------------------------------------------------------------------ shield
shield = tk.Toplevel(root)
shield.overrideredirect(True)
shield.configure(bg="#000000", cursor="none")
shield.geometry(f"{sw}x{sh}+0+0")
shield.bind("<ButtonPress-1>", lambda e: call("/api/wake", {}))
shield.withdraw()

state = {"pos": "bottom-left", "home": False, "kbd_btn": False, "shield": False, "kb": False, "misses": 0}


def place_buttons():
    pos = (state["pos"] or "bottom-left").lower()
    left = "left" in pos
    top = "top" in pos
    # keep the buttons clear of the keyboard when it is open on their side
    if state["kb"] and top == kb_state["top"]:
        top = not top
    x = MARGIN if left else sw - SIZE - MARGIN
    x2 = x + SIZE + 10 if left else x - SIZE - 10
    y = MARGIN if top else sh - SIZE - MARGIN
    home.geometry(f"{SIZE}x{SIZE}+{x}+{y}")
    kbd_btn.geometry(f"{SIZE}x{SIZE}+{x2}+{y}")


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
            show(kbd_btn, "kbd_btn", False)
    else:
        state["misses"] = 0
        pos = s.get("home_button")
        if pos != state["pos"]:
            state["pos"] = pos
            place_buttons()
        screen_on = s.get("screen_on", True)
        in_app = screen_on and s.get("source") != "home"
        if not in_app and state["kb"]:
            hide_keyboard()
        if state["kb"]:
            kb.lift()
        show(home, "home", in_app)
        show(kbd_btn, "kbd_btn", in_app)
        show(shield, "shield", not screen_on)
    root.after(POLL_MS, tick)


place_buttons()
root.after(500, tick)
root.mainloop()
