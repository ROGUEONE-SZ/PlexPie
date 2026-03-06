# Plexamp Raspberry Pi Appliance
  Turn a Raspberry Pi into a dedicated Plexamp music streamer.

Features:
  - Raspberry Pi OS 64-bit
  - Plexamp backend
  - Chromium kiosk interface
  - USB DAC support
  - Automatic Plex login persistence
  - Auto-restart watchdog
  - Appliance-style boot
  - Control from phone/tablet

Boot flow:
  Pi Boot → Plexamp service starts → Chromium kiosk launches → Plexamp UI appears  
  
---

Instructions for the install. 

- Plexamp backend
	- Chromium kiosk UI
	- USB DAC auto-detection
	- persistent Plex login
	- Avahi `.local` access
	- desktop autologin
	- kiosk watchdog
	- USB autosuspend disabled

Requirements
	- Raspberry Pi OS 64-bit
	- Raspberry Pi 4 or 5
	- USB DAC - tested with SMSL D1 (usb c)
	- HDMI display (optional) - tested with a $35 dollar screen from Amazon.
	- Internet access during install
---------------
First boot:

On first boot:
- Chromium opens Plexamp automatically
- Sign into Plex once
- Your session persists for future boots

Access:
- ssh pi@PlexPie.local
- http://PlexPie.local:32500

Notes
- Very Important: Plug in your USB DAC before running the installer if you want auto-detection.
- The installer writes /etc/asound.conf automatically if a USB DAC is found.
- If no DAC is detected, you can configure ALSA later.

What the installer does
- updates the OS
- installs dependencies
- installs Plexamp
- creates plexamp.service
- detects USB DAC
- sets up Chromium kiosk
- enables desktop autologin
- disables USB autosuspend


Additional Notes:
What this script does not do automatically:
It does not do the Plex claim/login step, because that still needs your Plex account session once on first launch.
So after reboot:
- Plexamp opens in kiosk
- You sign into Plex once
- It stays signed in because Chromium uses a persistent profile
Best install order
On a fresh Pi:
- Plug in USB DAC first
- Boot Pi OS 64-bit
- Run the script
- Reboot
- Sign into Plex once


Installation Options:
## Option 1 — direct installer
	curl -fsSL https://raw.githubusercontent.com/Satya121999/plexamp-raspberry-pi-install/main/install-plexamp-appliance.sh | sudo bash
	sudo reboot

## Option 2 - Clone the repo
	git clone https://github.com/Satya121999/plexamp-raspberry-pi-install.git
	cd plexamp-raspberry-pi-install
	chmod +x install-plexamp-appliance.sh
	sudo ./install-plexamp-appliance.sh
	sudo reboot


