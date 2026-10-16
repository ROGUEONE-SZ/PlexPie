# PlexPie: one screen to rule them all

A Raspberry Pi 4 music endpoint with a 7" touchscreen and a USB DAC. One launcher, five sources, all controllable from Home Assistant.

| Source | What it is |
|---|---|
| **Plexamp** | Plexamp headless, your Plex library on SZVAULT |
| **Apple Music** | music.apple.com running on the Pi's screen (Chromium + Widevine) |
| **AirPlay** | AirPlay 2 receiver; the Pi shows cover art and track info |
| **Music Assistant** | Squeezelite player for Music Assistant, plus MA's own interface on screen |
| **Home** | The launcher itself, with now playing, volume, pause all and screen off |

Starting any source pauses the others. Each app gets a slim frosted Home tab on its left edge, drawn inside the app's own page. A full keyboard slides up only when you tap a text field (including the Apple ID pop-up), and the app shrinks to the space above it, like on a phone. The screen turns off after a minute with no touch and nothing playing. The first tap only wakes it, so it never presses a button by accident.

## Install or update

**Fresh Pi, from GitHub.** This needs Raspberry Pi OS or Debian 13 with Raspberry Pi's package source, a user `sz`, and Plexamp headless already claimed in `/opt/plexamp`:

```bash
git clone https://github.com/ROGUEONE-SZ/plexamp-raspberry-pi-install ~/plexpie
cd ~/plexpie && cp plexpie.conf.example plexpie.conf && nano plexpie.conf   # set MQTT_PASS etc.
sudo bash install.sh
```

`plexpie.conf` holds your MQTT password, so it is git-ignored and never committed.

**Update an installed Pi** (the Pi's `~/plexpie` is a clone of this repo):

```bash
ssh sz@192.168.23.3 'cd ~/plexpie && git pull -q && sudo bash install.sh'
```

**From the Mac**, with this folder at `~/Scripts/plexpie`:

```bash
scp -rq ~/Scripts/plexpie sz@192.168.23.3:~ && ssh -t sz@192.168.23.3 'sudo bash ~/plexpie/install.sh'
```

The first run builds AirPlay 2 from source, which takes about 10 minutes. Later runs skip anything already built, so after editing `plexpie.conf` just run the same line again.

Before it changes anything, the installer saves the old setup to `/var/backups/plexpie/original.tar.gz`. To undo it: `sudo bash ~/plexpie/rollback.sh`.

## Settings

Everything lives in `plexpie.conf`: player name, MQTT, Music Assistant address, screen timeout, Home button corner, which sources are enabled.

## Home Assistant

The Pi appears automatically as an MQTT device named after its hostname, with these entities:

- **Source** (select): what's on the screen
- **Now playing** (sensor, with source/title/artist/album attributes)
- **Playing source** (sensor) and **Playing** (binary sensor)
- **Screen** (switch), **Screen brightness** and **Volume** (numbers)
- **Pause all**, **Restart screen** and **Reboot** (buttons)
- **AirPlay artwork** (image), plus diagnostics for **CPU temperature** and **Music Assistant reachable**

The old `plexamp/state` topic (`playing` / `idle`) is still published, so existing automations keep working.

For full Plexamp transport control in HA, add the official **Plex** integration. `GymMusicPlayer` then shows up as a media player.

## Music Assistant

In Music Assistant, go to **Settings → Providers** and make sure the **Squeezelite** player provider is enabled. The player `GymMusicPlayer` appears within a few seconds. When Music Assistant is stopped, the launcher tile greys out, and the Pi reconnects on its own when MA comes back.

## First-time sign-ins

Plexamp, Apple Music and Music Assistant each ask you to sign in once on the screen. Tap a field and the keyboard appears; **return** submits and **⌄** hides it (tap the field again to bring it back). The sign-ins are kept.

## Troubleshooting

| Problem | Where to look |
|---|---|
| Launcher, screen or Home Assistant | `journalctl -u plexpie -f` |
| Chromium or overlay | `~/.local/state/plexpie/session.log` |
| AirPlay | `journalctl -u plexpie-airplay -f`. If the Pi doesn't appear on the iPhone, the phone is probably on another subnet; enable mDNS repeating (Avahi) on pfSense. |
| Music Assistant | `journalctl -u plexpie-squeezelite -f` |
| Audio devices | `wpctl status` |
| Plex Direct DNS | pfSense → DNS Resolver → Custom options: `private-domain: "plex.direct"` |
| Endless spinner in Plexamp | Remove `~/.local/share/Plexamp/Settings/%40Plexamp%3Adiscovery%3Ahubs` and restart Plexamp |

## Layout

```
install.sh  rollback.sh  plexpie.conf
files/plexpied.py           control daemon (launcher API, tabs, screen, MQTT)
files/plexpie-overlay.py    pop-up keyboard, touch-to-wake shield
tools/diagnose.sh           read-only report of audio, AirPlay and logs
files/plexpie-session       X session: overlay + Chromium kiosk
files/www/                  launcher and AirPlay screens
files/systemd/              service units
files/audio/                PipeWire / ALSA settings
files/shairport-sync.conf   AirPlay receiver settings
```
