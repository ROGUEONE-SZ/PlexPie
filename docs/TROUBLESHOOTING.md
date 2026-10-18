# Troubleshooting PlexPie

Problems people actually ran into while building and using PlexPie, what caused them, and how to fix
them. Start with **[Quick fixes](#quick-fixes)**; if that doesn't do it, find your symptom below.

- [Quick fixes](#quick-fixes)
- [Getting a report from the player](#getting-a-report-from-the-player)
- [Updates](#updates)
- [The screen](#the-screen)
- [Plexamp](#plexamp)
- [Bluetooth, AirPlay and Spotify](#bluetooth-airplay-and-spotify)
- [Home Assistant](#home-assistant)
- [Network, addresses and your router](#network-addresses-and-your-router)
- [Connecting to the Pi from a computer](#connecting-to-the-pi-from-a-computer)
- [Still stuck?](#still-stuck)

---

## Quick fixes

Most problems go away with one of these, in this order:

1. **Wait two minutes after the Pi starts.** Plexamp, AirPlay and Bluetooth take a minute or two
   to be ready after a restart or an update.
2. **Settings › System › Restart PlexPie.** Reloads the screen and every app (a few seconds).
3. **Settings › System › Reboot.** Restarts the whole Raspberry Pi (about a minute).
4. **Unplug the power for 10 seconds**, if the screen doesn't respond at all.
5. **Update.** Settings › Updates: check PlexPie and Raspberry Pi OS. Many problems below are
   already fixed in newer versions.

---

## Getting a report from the player

Two read-only checks collect everything needed to find a problem. They change nothing.

**From another computer** (Mac: Terminal, Windows: PowerShell). Replace `USER` with the username you
chose when you set up the memory card, and the address with your player's (Settings › Remote access):

```
curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/main/tools/diag.sh | ssh USER@192.168.1.42 bash
```

It shows the PlexPie and browser versions, the real screen size against what each page sees, the
Bluetooth status, today's system updates and the last lines of each log.

**On the Pi itself** (connected with `ssh`): `bash ~/plexpie/tools/diagnose.sh` gives a fuller
report and plays a short test tone.

**Plexamp screen blank or broken?** This one reloads the Plexamp page once and lists every error:

```
curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/main/tools/plexamp-check.sh | ssh USER@192.168.1.42 bash
```

---

## Updates

### The update stays at 1%, or "Updating…" never ends

- **Restarted during an update?** A reboot or power cut in the middle leaves the old "Updating"
  marker behind. Since 2.8.3 PlexPie notices and says *"The last update was interrupted"*; tap
  **Update** again to finish.
- **Since 2.9**, updates show the real step, the package being installed and the time so far. The
  first step ("Refreshing the list of updates") can take a minute on a slow connection; the bar
  keeps moving while it works.

### Updates say "done", but the version never changes (2.8.x)

Versions before 2.9.1 had a bug: the updater stopped right after the Raspberry Pi OS step, so the
new PlexPie was never installed. The update log ended with `$!: unbound variable`. Because the
broken updater can't fix itself, update once by hand from a computer:

```
ssh -t USER@192.168.1.42 'cd "$(cat /etc/plexpie/repo_dir)" && git fetch -q && git reset -q --hard @{u} && sudo bash install.sh'
```

It takes a few minutes and ends with a "Status" list; the screen restarts by itself. After that,
updates from Settings and Home Assistant work again.

### "Power-cut protection is on: turn it off to install updates"

Power-cut protection makes the memory card read-only, so nothing can be installed. Turn it off in
Settings › System (the player restarts), update, then turn it back on.

### Raspberry Pi OS keeps showing the same updates

Older versions only ran `apt upgrade`, which skips updates that need new packages (they were "held
back"). Since 2.9.1 PlexPie installs those too. Restart when it asks: some OS updates (the kernel,
firmware) only take effect after a restart.

---

## The screen

### The home screen looks too big and is cut off on the right

**Cause:** the screen got **pinch-zoomed**. The browser treats the touchscreen like a phone, and a
swipe that touches with two fingers (or a palm) can zoom in. Each app keeps its own zoom, so one
screen looks fine while the home screen stays zoomed.

**Fix:** update to **2.9.2 or newer** and reboot. Pinch-zoom is turned off for the whole screen.
On an older version, Settings › System › Restart PlexPie resets the zoom.

### The screen is black

It's probably asleep: tap it. The first tap only wakes it. Still black? Unplug the power for 10
seconds. To change how fast it sleeps: Settings › Screen › Screen sleep.

### Upside down, or touches land in the wrong place

Set `TOUCH_ROTATE` in `~/plexpie/plexpie.conf`, then run `sudo bash ~/plexpie/install.sh`.

### Swiping home doesn't work

Swipe right from the very left edge, or down from the very top edge. If your screen is mounted
rotated, set `TOUCH_ROTATE` (above). The log shows what the touchscreen reports:
`journalctl -u plexpie | grep -i touch`.

---

## Plexamp

### The Plexamp screen is blank, or shows "loading" forever

**Cause:** the Plexamp page loaded at the exact moment Plexamp was restarting (after an update or at
start-up), so its main file failed to download ("connection refused") and the browser never tried
again.

**Fix:** update to **2.9.3 or newer**: PlexPie notices an empty Plexamp page and reloads it within
about 30 seconds. On an older version: Settings › System › Restart PlexPie.

### Album art keeps spinning, music is slow to start, Plexamp can't find your server

**Cause (very common with pfSense, OPNsense and some other routers):** Plex reaches your server
through names like `192-168-1-20.abc123.plex.direct`, which point to an address inside your home.
Many routers refuse to look those up, to protect against an attack called *DNS rebinding*. Plexamp
then falls back to slow routes through the internet, or fails. The Plexamp check above shows
`ERR_NAME_NOT_RESOLVED … plex.direct`.

**Fix:** allow `plex.direct` in your router's DNS settings:

- **pfSense:** Services › DNS Resolver › General Settings › *Custom options*, add
  ```
  server:
  private-domain: "plex.direct"
  ```
  then **Save** and **Apply Changes**.
- **OPNsense:** Services › Unbound DNS › Advanced › *Private Domains*: add `plex.direct`, then Apply.
- **Other routers:** look for "DNS rebind protection" and add `plex.direct` as an exception. On an
  AVM FRITZ!Box: Home Network › Network › Network Settings › DNS Rebind Protection.
- **Pi-hole / AdGuard Home:** usually fine, unless the router behind them filters it.

Then Settings › System › Restart PlexPie.

### Music plays, but the Plexamp screen spins or shows no "now playing"

**Cause:** the Plexamp screen remembers the player by its network address. If the player's address
changed (for example after you gave it a fixed address in your router), the screen keeps trying the
old one and spins, even though your phone finds the player and music plays. Picking the player under
the cast icon doesn't help, because it still uses the old address.

**Fix (2.9.5 and newer):** Settings › Configuration › Plexamp › **Reset the Plexamp screen**, then
sign in to Plex once on the screen. PlexPie also notices when its address changes and points you
there.

**Older versions:** with Plexamp open on the screen, run this from another computer:

```
curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/main/tools/plexamp-reset.sh | ssh USER@192.168.1.42 bash -s fix
```

It lists the addresses the screen remembered, saves a copy, clears what the screen's browser saved
for Plexamp, and reloads it. **Sign in to Plex once on the screen** when it asks; now playing and
album art come back. Your library, playlists and the player itself aren't affected. To undo, run the
same command with `undo` instead of `fix`.

Tip: give the player its fixed address **before** you start using it, so this never comes up.

### See exactly what the screen shows

`tools/screenshot.sh` saves a picture of a screen tab to your computer and prints what Plexamp says
it's playing:

```
curl -fsSL https://raw.githubusercontent.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/main/tools/screenshot.sh | ssh USER@192.168.1.42 bash -s plexamp > plexpie-screen.png
```

### Plexamp says it isn't linked, or is linked to the wrong Plex account

Run the installer again from a computer: `sudo bash ~/plexpie/install.sh`. It asks for a new claim
code from [plex.tv/claim](https://plex.tv/claim). The code expires after a few minutes, so paste it
right away.

### Plexamp keeps spinning even after a restart

Remove Plexamp's saved server list and restart it:
`rm ~/.local/share/Plexamp/Settings/%40Plexamp%3Adiscovery%3Ahubs && sudo systemctl restart plexamp`

---

### With screen sharing or a mouse, I can't get back to the home screen

Swipes only work on the touchscreen itself. Since 2.11 there are other ways:

- a small **Home tab** (a house) on the left edge appears when a screen-sharing viewer or a mouse
  is connected (Settings › Screen › Home button: When needed, Always or Never);
- the **Home** key, or **Esc** pressed twice;
- the mouse's **Back** button.

A mouse pointer shows when you move a mouse and hides again for touch (after a restart).

### Screen sharing won't connect

- Turn it on first: Settings › System › **Screen sharing**, then Save. The row shows the address
  (`vnc://…`) once it's running.
- The password is your **Remote access PIN** (Settings › Remote access). New PIN changes it.
- Your Mac, iPad or iPhone must be on the same home network. It doesn't work over the internet, by design.
- Installed before 2.10? Update PlexPie (it installs the screen-sharing app, `x11vnc`). The
  log is in `~/.config/plexpie/x11vnc.log`.

## Bluetooth, AirPlay and Spotify

### "Bluetooth isn't ready on this player"

The Bluetooth radio is often switched off ("soft-blocked") on a fresh Raspberry Pi OS. Since 2.8.1
PlexPie switches it on by itself. On an older version, update, or run `sudo rfkill unblock bluetooth`
and reboot.

### My phone can't find the player

Tap **Pair a device** first (on the Bluetooth screen or in Settings › Configuration › Bluetooth):
the player is only visible for 2 minutes after that. On the phone, turn Bluetooth off and on.

### The screen jumps to Bluetooth when nobody is playing anything

A paired phone or tablet that comes into range reconnects by itself and opens the audio link for a
moment. Since 2.9.2 the screen only switches after 4 seconds of real Bluetooth playback. To stop a
device from reconnecting at all: Settings › Configuration › Bluetooth › **Forget**.

### Bluetooth sound stutters

On a Raspberry Pi 4, Bluetooth and Wi-Fi share one radio. Use a network cable, or keep the device
closer.

### AirPlay or Spotify doesn't show the player

Your device and the Pi must be on the same network (not a guest Wi-Fi). Some routers block the
discovery messages between Wi-Fi and wired devices: turn on "mDNS", "Bonjour" or "multicast"
forwarding in the router.

---

## Home Assistant

### Every PlexPie entity says "unavailable"

The player isn't talking to Home Assistant right now. Usually it's off, restarting or updating:
wait two minutes. Otherwise check Settings › Configuration › Home Assistant: the **Connection** dot
should be green. If the broker address, username or password changed, enter them again and Save.

### A launcher button I removed is still in Home Assistant

Since 2.8.4 PlexPie removes switches for buttons you take out of Settings. On an older version,
update, or delete the entity in Home Assistant (Settings › Devices & services › MQTT).

### The "Raspberry Pi OS update" entity says "not available"

It appears once the player runs 2.9.0 or newer. Before that there's a single "Software" update
entity for both.

### A device controlled from Home Assistant (receiver, smart plug) stops responding

- Give it a **fixed address** in your router (see below). If its address changed, update it in the
  integration (Settings › Devices & services › the integration › Reconfigure).
- Many AV receivers stop answering the network after a while. Unplug the receiver from the wall for
  a minute, then reload the integration. Turn on the receiver's **Network Standby** so it can be
  turned on over the network.

---

## Network, addresses and your router

### Give the player (and anything Home Assistant controls) a fixed address

Addresses handed out automatically can change after a power cut, which breaks bookmarks, the
phone address and Home Assistant integrations. In your router, add a **static DHCP mapping**
(also called a "DHCP reservation") for each device, using its MAC address.

On **pfSense** the fixed address must be **outside** the range it hands out automatically. If it says
*"The IP address must not be within the DHCP range"*, shrink the range first (Services › DHCP Server,
for example `.2` to `.199`) and put fixed addresses at `.200` and above.

### Wi-Fi off, network cable only

Settings › Network › Wi-Fi off. If the Pi has no network cable, it goes offline until you turn Wi-Fi
back on from the screen.

---

## Connecting to the Pi from a computer

### "Permission denied, please try again"

Wrong username or password. The username is the one you chose in Raspberry Pi Imager (it's often
not `pi`). Your terminal remembers what you used before:
`grep -h "ssh .*@" ~/.zsh_history ~/.bash_history 2>/dev/null | tail -5`

### "REMOTE HOST IDENTIFICATION HAS CHANGED" or "authenticity can't be established"

Normal after the Pi gets a new address or a fresh memory card. If you just changed its address or
card, remove the old entry and connect again: `ssh-keygen -R 192.168.1.42` (the address you use).

### "zsh: event not found"

The Mac's terminal treats `!` specially. Put the command in single quotes, or copy the exact
commands from this page.

### The terminal shows `>` or `heredoc>` and nothing happens

A command wasn't finished (usually a missing quote). Press **Ctrl + C** and paste it again.

---

## Still stuck?

- **Questions, setup help, network or router problems:** ask in
  [Discussions](https://github.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/discussions). Other builders
  can help, and the answer helps the next person.
- **A bug on supported hardware:** run the report from
  [Getting a report from the player](#getting-a-report-from-the-player) and
  [open an issue](https://github.com/ROGUEONE-SZ/plexamp-raspberry-pi-install/issues/new/choose) with its
  output. The reports hide passwords and sign-in tokens, but have a look before you paste.
