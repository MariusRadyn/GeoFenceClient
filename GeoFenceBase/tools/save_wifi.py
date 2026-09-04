#!/usr/bin/env python3
"""
Privileged WiFi helper — SOURCE COPY for developers.

Runtime install path (trinity / sudo):
  /opt/geofence-tools/save_wifi.py

Installed by SetupTrinityUser.sh. Do NOT rely on this file under
/home/geoserver/GeoFenceBase/tools — trinity cannot read GeoFenceBase.

Commands:
  save_wifi.py --list
  save_wifi.py --show-ssid
  save_wifi.py            # read JSON {"ssid","password"} from stdin
"""

from __future__ import annotations

import json
import os
import sys

# Service home / app (readable by root; not by trinity)
GEOSERVER_HOME = "/home/geoserver"
APP_DIR = f"{GEOSERVER_HOME}/GeoFenceBase"

os.environ["HOME"] = GEOSERVER_HOME
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import WifiCredentials  # noqa: E402


class _StdoutToStderr:
    """WifiCredentials.printDebug writes to stdout — keep stdout JSON-only."""

    def __enter__(self):
        self._out = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *args):
        sys.stdout = self._out


def main() -> int:
    if os.geteuid() != 0:
        print("ERROR: must run as root (via sudo)", file=sys.stderr)
        return 2

    if "--list" in sys.argv:
        try:
            with _StdoutToStderr():
                ssids = WifiCredentials.list_wifi_ssids(rescan=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        print(json.dumps({"ok": True, "ssids": ssids}))
        return 0

    if "--show-ssid" in sys.argv:
        ssid = ""
        try:
            with _StdoutToStderr():
                if os.path.exists(WifiCredentials.DATA_FILE):
                    ssid = WifiCredentials.read_credentials_file().get("ssid", "") or ""
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e), "ssid": ""}))
            return 1
        print(json.dumps({"ok": True, "ssid": ssid}))
        return 0

    # Save: JSON on stdin
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        ssid = (data.get("ssid") or "").strip()
        password = data.get("password") or ""
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"invalid input: {e}"}))
        return 1

    if not ssid:
        print(json.dumps({"ok": False, "error": "empty ssid"}))
        return 1

    try:
        with _StdoutToStderr():
            ok = WifiCredentials.save_new_credentials(ssid, password)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    if not ok:
        print(json.dumps({"ok": False, "error": "verify/join failed — not saved"}))
        return 1

    print(json.dumps({"ok": True, "ssid": ssid}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
