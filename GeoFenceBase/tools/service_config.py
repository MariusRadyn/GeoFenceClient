#!/usr/bin/env python3
"""
Privileged geofence.conf helper (run via sudo as root only).

Installed at: /opt/geofence-tools/service_config.py

Commands:
  service_config.py --get-verbose
  service_config.py --set-verbose on|off|true|false|1|0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

GEOSERVER_HOME = "/home/geoserver"
CONFIG_FILE = f"{GEOSERVER_HOME}/Secure/geofence.conf"
SERVICE_NAME = "geofence"
DEFAULTS = {
    "wifi": False,
    "verbose": False,
    "mqtt": False,
    "newcreds": False,
}


def _load() -> dict:
    cfg = dict(DEFAULTS)
    if not os.path.exists(CONFIG_FILE):
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in DEFAULTS:
                if key in data:
                    cfg[key] = bool(data[key])
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"read config: {e}"}))
        sys.exit(1)
    return cfg


def _save(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    out = {k: bool(cfg.get(k, DEFAULTS[k])) for k in DEFAULTS}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_FILE)
    try:
        os.chmod(CONFIG_FILE, 0o600)
        # Keep geoserver ownership
        import pwd

        st = pwd.getpwnam("geoserver")
        os.chown(CONFIG_FILE, st.pw_uid, st.pw_gid)
    except Exception:
        pass


def _parse_bool(text: str) -> bool:
    t = (text or "").strip().lower()
    if t in ("1", "true", "on", "yes", "y"):
        return True
    if t in ("0", "false", "off", "no", "n"):
        return False
    raise ValueError(f"expected on/off, got {text!r}")


def _restart() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return False, str(e)
    if r.returncode == 0:
        return True, "restarted"
    return False, (r.stderr or r.stdout or f"exit {r.returncode}").strip()


def main() -> int:
    if os.geteuid() != 0:
        print("ERROR: must run as root (via sudo)", file=sys.stderr)
        return 2

    if "--get-verbose" in sys.argv:
        cfg = _load()
        print(json.dumps({"ok": True, "verbose": bool(cfg.get("verbose"))}))
        return 0

    if "--set-verbose" in sys.argv:
        try:
            idx = sys.argv.index("--set-verbose")
            raw = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
            value = _parse_bool(raw)
        except (ValueError, IndexError) as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        cfg = _load()
        cfg["verbose"] = value
        try:
            _save(cfg)
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"write config: {e}"}))
            return 1
        restart = "--no-restart" not in sys.argv
        restart_ok = True
        restart_msg = "skipped"
        if restart:
            restart_ok, restart_msg = _restart()
        print(
            json.dumps(
                {
                    "ok": True,
                    "verbose": value,
                    "restart_ok": restart_ok,
                    "restart": restart_msg,
                }
            )
        )
        return 0 if restart_ok else 1

    print(
        json.dumps(
            {
                "ok": False,
                "error": "usage: --get-verbose | --set-verbose on|off [--no-restart]",
            }
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
