# What's new in PlexPie

Already running PlexPie? **Settings › Updates › PlexPie › Update** brings you to the latest version.

## 2.21 (October 25, 2026)
- **Runs on more than a Pi (beta):** Intel/AMD PCs (mini PCs, NUCs, old laptops) with Debian or Ubuntu, and
  other 64-bit ARM boards (community supported). On PCs the screen uses Google Chrome, so Apple Music, TIDAL
  and the Spotify web player play there too. The start-up screen, rotation and "Exit to the desktop" work
  with a PC's boot menu (GRUB) and desktops like Ubuntu's.
- **Quick setup:** the one-line installer can install with no questions and finish in a **setup wizard on
  the screen** (or your phone): name, link Plexamp with a QR code, choose apps, Home Assistant, your phone.
  The terminal questions are still there if you prefer them.
- **Automated install tests** on Debian 12/13 and Ubuntu 24.04, Intel/AMD and ARM, on every change.
- **Hardware report** form on GitHub for people trying PlexPie on other machines.

Everything on the Raspberry Pi works as before.

## 2.20 (October 25, 2026)
- **Jellyfin:** your Jellyfin server's music on the screen (enter its address).
- **UPnP / DLNA speaker:** play from BubbleUPnP, mconnect, Jellyfin's DLNA plugin or NAS music apps
  (which can also stream TIDAL and Qobuz to it). Track and cover on the screen.
- **Roon:** Roon Bridge, so the player becomes a speaker in Roon.
- **Snapcast:** plays in sync with your Snapcast multi-room system; shows what the group plays.
- **Groups everywhere:** On this screen · From your phone · Multi-room and other systems · Home, in
  Settings › Configuration, in the setup guides, and as tabs on the home screen once there are more than
  eight tiles (it jumps to the tab of whatever starts playing).

The new sources are optional and off unless you choose them.

## 2.19 (October 25, 2026)
- **TIDAL and Qobuz** web players on the screen (optional, Settings › Configuration).
- **Home screen order:** move tiles up or down in Settings › Screen › Home screen order.

TIDAL Connect and Qobuz Connect aren't included: both are only licensed to hardware makers, and the
unofficial versions rely on files copied from commercial devices.

## 2.18 (October 23, 2026)
- **Your own web players:** add any music, radio or audiobook website as a tile on the home screen
  (Settings › Configuration › Your web players, up to six).
- **Network icon** on the home screen: Wi-Fi bars, Ethernet, or a red mark when offline.
- **Update badge:** a red dot on the Settings gear when an update is ready; Settings opens on Updates.
- **12/24-hour clock** setting (Settings › Screen › Clock).

Ideas from starbow777 on r/plexamp.

## 2.17 (October 21, 2026)
- **New logo:** a silver pie cut like a pizza, one amber slice pulled out, hot rod red edges. On the
  start-up screen, the home-screen icon and GitHub (2.17.1).
- **Album covers on the home screen:** the playing tile shows the cover (Plexamp, AirPlay, Spotify,
  Apple Music and the web players), with the track name when there's room.
- **YouTube Music and BBC Sounds** web players on the screen (optional, Settings › Configuration).
- Fix: in portrait, a long track title no longer pushes the home screen wider than the display; it
  glides back and forth instead. The bottom bar fits narrow screens too.

Thanks to starbow777 for the ideas and the bug report.

## 2.16 (October 20, 2026)
- **Admin phone:** make your phone the admin device. New phones and tablets tap *Ask the admin* instead of
  typing the PIN; approve them on your phone or on the player's screen.
- **Device levels:** Admin, Full access (all of Settings) or Controls only (just the remote).
- **One remote for all your players** (`/remote`, add it to your home screen): link your other PlexPie
  players once with their PIN, then play, pause, change volume and switch apps on any of them, and open
  their Settings without the PIN.
- Players find each other on the network automatically (Bonjour/mDNS).

## 2.15 (October 19, 2026)
- **Looks like an appliance:** the PlexPie logo while it starts and shuts down, no Raspberry Pi boot text.
  Switch it off in Settings › System when troubleshooting with a monitor.
- Rotation **Automatic** now matches the Raspberry Pi desktop's setting (it was upside down).
- Diagnostics report: new *playback health* section (power, heat, Wi-Fi signal, stream errors).
- Troubleshooting: what to do when songs skip or stutter.

## 2.14 (October 18, 2026)
- **Screen rotation for every screen**, including the portrait Pi Touch Display 2
  (Settings › Screen › Rotation). Touch and swipes turn with it.

## 2.13 (October 18, 2026)
- Optional **Caldera Music** (Plex Labs) for multi-room with other Caldera speakers.
- Fix: the Home tab no longer shows on touch-only screens.

## 2.12 (October 18, 2026)
- **Exit to the desktop or command line** from Settings › System, and a *Back to PlexPie* icon to return.
- Fix: Update now starts on the first tap.

## 2.11 (October 17–18, 2026)
- **Get around with a mouse, keyboard or screen sharing:** Home tab, Home key, Esc twice, mouse Back button.
- Fills the whole screen on big monitors.

## 2.10 (October 17, 2026)
- **Screen sharing:** see and use the player's screen from a Mac, iPad or iPhone.

## 2.9 (October 17, 2026)
- New **Settings** layout, separate PlexPie and Raspberry Pi OS updates with progress, setup guides
  and PDF, **registered devices** under Remote access.
- **Reset the Plexamp screen** button for after an address change.
- Fixes: updates stopping after a system upgrade, accidental pinch-zoom, blank Plexamp screen.

## 2.0 (October 16, 2026)
- First version anyone can install: setup wizard, installer, Plexamp, AirPlay 2, Spotify Connect,
  Apple Music, Bluetooth, Music Assistant, Home Assistant controls.
