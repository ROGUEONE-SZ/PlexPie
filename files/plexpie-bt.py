#!/usr/bin/env python3
"""PlexPie Bluetooth receiver helper (runs as root).

- Lets phones pair only while pairing is open (Settings or the Bluetooth screen: "Pair a phone").
  No PIN: the phone just confirms. Paired phones reconnect later without asking.
- Writes what's connected and playing to /run/plexpie/bt.json for the PlexPie daemon.
- PipeWire (in the user session) receives the audio itself.
"""
import json
import os
import sys
import time

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

BUS = "org.bluez"
AGENT_PATH = "/plexpie/agent"
RUN = "/run/plexpie"
PAIR_FLAG = os.path.join(RUN, "bt-pair-until")
STATE = os.path.join(RUN, "bt.json")
NAME = sys.argv[1] if len(sys.argv) > 1 else "PlexPie"
CODECS = {0: "SBC", 1: "MP3", 2: "AAC", 0xFF: "aptX / LDAC"}

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"


def pairing_open():
    try:
        with open(PAIR_FLAG) as f:
            return float(f.read().strip() or 0) > time.time()
    except (OSError, ValueError):
        return False


def objects():
    om = dbus.Interface(bus.get_object(BUS, "/"), "org.freedesktop.DBus.ObjectManager")
    return om.GetManagedObjects()


def set_prop(path, iface, name, value):
    dbus.Interface(bus.get_object(BUS, path), "org.freedesktop.DBus.Properties").Set(iface, name, value)


def unblock_rfkill():
    """Bluetooth is often 'soft-blocked' on a fresh Raspberry Pi OS; unblock it (no rfkill tool needed)."""
    import glob
    for d in glob.glob("/sys/class/rfkill/rfkill*"):
        try:
            if open(os.path.join(d, "type")).read().strip() == "bluetooth" and \
                    open(os.path.join(d, "soft")).read().strip() == "1":
                with open(os.path.join(d, "soft"), "w") as f:
                    f.write("0")
        except OSError:
            pass


def try_set(path, name, value, errors):
    try:
        set_prop(path, "org.bluez.Adapter1", name, value)
    except dbus.DBusException as e:
        errors.append(f"{name}: {e.get_dbus_message() or e.get_dbus_name()}")


def trust(device):
    try:
        set_prop(device, "org.bluez.Device1", "Trusted", dbus.Boolean(True))
    except dbus.DBusException:
        pass


class Agent(dbus.service.Object):
    def _allow(self, device):
        if not pairing_open():
            raise Rejected("pairing is closed")
        trust(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Release(self):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        props = objects().get(device, {}).get("org.bluez.Device1", {})
        if not props.get("Trusted"):
            self._allow(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        self._allow(device)
        return "0000"

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        self._allow(device)
        return dbus.UInt32(0)

    @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        pass

    @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        self._allow(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        self._allow(device)

    @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
    def Cancel(self):
        pass


def adapter_path(objs):
    for path, ifaces in objs.items():
        if "org.bluez.Adapter1" in ifaces:
            return path
    return None


last = {"json": None, "discoverable": None}


def tick():
    try:
        objs = objects()
        ad = adapter_path(objs)
        if not ad:
            write({"available": False})
            return True
        a = objs[ad]["org.bluez.Adapter1"]
        errors = []
        if not a.get("Powered"):
            unblock_rfkill()
            try_set(ad, "Powered", dbus.Boolean(True), errors)
            time.sleep(0.5)
            a = objects()[ad]["org.bluez.Adapter1"]
            if not a.get("Powered"):
                write({"available": False, "error": "; ".join(errors) or "the Bluetooth radio won't switch on"})
                return True
        if str(a.get("Alias")) != NAME:
            try_set(ad, "Alias", NAME, errors)
        want = pairing_open()
        if bool(a.get("Discoverable")) != want or last["discoverable"] is None:
            try_set(ad, "DiscoverableTimeout", dbus.UInt32(0), errors)
            try_set(ad, "Pairable", dbus.Boolean(want), errors)
            try_set(ad, "Discoverable", dbus.Boolean(want), errors)
            last["discoverable"] = want
        devices, playing, track, codec, player, connected = [], False, {}, "", "", ""
        for path, ifaces in objs.items():
            d = ifaces.get("org.bluez.Device1")
            if d and d.get("Paired"):
                if want and not d.get("Trusted"):
                    trust(path)
                devices.append({"address": str(d.get("Address")), "name": str(d.get("Alias") or d.get("Name") or "Phone"),
                                "connected": bool(d.get("Connected"))})
                if d.get("Connected"):
                    connected = str(d.get("Alias") or d.get("Name") or "Phone")
            t = ifaces.get("org.bluez.MediaTransport1")
            if t:
                codec = CODECS.get(int(t.get("Codec", 0)), "Bluetooth")
                if str(t.get("State")) == "active":
                    playing = True
            p = ifaces.get("org.bluez.MediaPlayer1")
            if p:
                player = path
                if str(p.get("Status")) == "playing":
                    playing = True
                tr = p.get("Track") or {}
                track = {"title": str(tr.get("Title", "")), "artist": str(tr.get("Artist", "")),
                         "album": str(tr.get("Album", ""))}
        left = 0
        if want:
            try:
                left = max(0, round(float(open(PAIR_FLAG).read()) - time.time()))
            except (OSError, ValueError):
                pass
        write({"available": True, "name": NAME, "pairing_left": left, "devices": devices, "warning": "; ".join(errors),
               "connected": connected, "playing": playing, "codec": codec, "player": player, **track})
    except dbus.DBusException as e:
        write({"available": False, "error": str(e)[:200]})
    return True


def write(state):
    data = json.dumps(state, sort_keys=True)
    if data == last["json"]:
        return
    last["json"] = data
    os.makedirs(RUN, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATE)


def main():
    os.makedirs(RUN, exist_ok=True)
    os.chmod(RUN, 0o755)
    try:
        os.remove(PAIR_FLAG)
    except OSError:
        pass
    Agent(bus, AGENT_PATH)
    mgr = dbus.Interface(bus.get_object(BUS, "/org/bluez"), "org.bluez.AgentManager1")
    mgr.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    mgr.RequestDefaultAgent(AGENT_PATH)
    tick()
    GLib.timeout_add(1000, tick)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
