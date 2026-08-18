# Debug
import argparse
import json
import os
import threading
from typing import List

# Config file for boot/service options (CLI flags override when passed)
CONFIG_FILE = os.path.expanduser("~/Secure/geofence.conf")
CONFIG_DEFAULTS = {
    "wifi": False,
    "verbose": False,
    "mqtt": False,
    "newcreds": False,
}


def load_config(path: str = CONFIG_FILE) -> dict:
    """Load boolean options from geofence.conf; missing file → defaults."""
    cfg = dict(CONFIG_DEFAULTS)
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in CONFIG_DEFAULTS:
                if key in data:
                    cfg[key] = bool(data[key])
    except Exception as e:
        # Flags not defined yet; reported after PRINT_DEBUG_* below
        cfg["_load_error"] = f"WARNING: could not read {path}: {e}"
    return cfg


def update_config(updates: dict, path: str = None) -> bool:
    """Merge keys into geofence.conf and write it back. Returns True on success."""
    path = path or CONFIG_FILE
    cfg = load_config(path)
    cfg.pop("_load_error", None)
    for key, value in updates.items():
        if key in CONFIG_DEFAULTS:
            cfg[key] = bool(value)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: cfg[k] for k in CONFIG_DEFAULTS}, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return True
    except Exception as e:
        printDebug(f"WARNING: could not update {path}: {e}", PRINT_DEBUG_ERROR)
        return False


def clear_newcreds(path: str = None) -> bool:
    """One-shot: turn newcreds off in config after credentials were created."""
    ok = update_config({"newcreds": False}, path=path or getattr(args, "config", CONFIG_FILE))
    args.newcreds = False
    if ok:
        printDebug("Cleared newcreds in config (one-time flag)", PRINT_DEBUG_GENERAL)
    return ok


# ----- Create Arguments ------
# --newcreds : Create new wifi credentials
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument(
    "--newcreds",
    action="store_true",
    help="Create new wifi credentials (one-shot; clears newcreds in geofence.conf after success)",
)

# --wifi : ignore Ethernet Connection, Use
parser.add_argument(
    "--wifi",
    action="store_true",
    help="Ignore Ethernet LAN, use Wifi connection",
)

# --verbose : enable all debug print flags
parser.add_argument(
    "--verbose",
    action="store_true",
    help="Enable all PRINT_DEBUG flags",
)

# --mqtt : print MQTT RX/TX traffic
parser.add_argument(
    "--mqtt",
    action="store_true",
    help="Enable PRINT_MQTT_COMMS (MQTT RX/TX logging)",
)

# --config : optional path to geofence.conf
parser.add_argument(
    "--config",
    default=CONFIG_FILE,
    help=f"Path to config file (default: {CONFIG_FILE})",
)

_cli_args, _ = parser.parse_known_args()
_file_cfg = load_config(_cli_args.config)

# Config sets defaults for service/boot; CLI --flags still force True when passed
class _Args:
    newcreds = bool(_cli_args.newcreds or _file_cfg["newcreds"])
    wifi = bool(_cli_args.wifi or _file_cfg["wifi"])
    verbose = bool(_cli_args.verbose or _file_cfg["verbose"])
    mqtt = bool(_cli_args.mqtt or _file_cfg["mqtt"])
    config = _cli_args.config


args = _Args()


PRINT_DEBUG_ERROR = True
PRINT_DEBUG_GENERAL = False
PRINT_DEBUG_IP_INFO = False
PRINT_DEBUG_WIFI = False
PRINT_DEBUG_BT = False
PRINT_DEBUG_FIRESTORE = False
PRINT_DEBUG_MONITOR = False
PRINT_DEBUG_OPERATOR = False
PRINT_MQTT_COMMS = False
PRINT_DEBUG_MQTT = False
PRINT_DEBUG_MQTT_CREDS = False

if args.mqtt:
    PRINT_MQTT_COMMS = True

if args.verbose:
    PRINT_DEBUG_GENERAL = True
    PRINT_DEBUG_IP_INFO = True
    PRINT_DEBUG_ERROR = True
    PRINT_DEBUG_WIFI = True
    PRINT_DEBUG_BT = True
    PRINT_DEBUG_FIRESTORE = True
    PRINT_DEBUG_MONITOR = True
    PRINT_DEBUG_OPERATOR = True
    PRINT_MQTT_COMMS = True
    PRINT_DEBUG_MQTT = True
    PRINT_DEBUG_MQTT_CREDS = True


def printDebug(msg, enabled):
    if not enabled:
        return
    print(msg)
    # journald omits truly empty lines; a space keeps a visible blank when verbose
    if args.verbose:
        print(" ")


if _file_cfg.get("_load_error"):
    printDebug(_file_cfg["_load_error"], PRINT_DEBUG_ERROR)

# BLE Commands
CMD_BLE_PAIRING = "PAIRING"  # IoT in pair mode — base may send WiFi creds
CMD_BLE_IDLE = "IDLE"        # IoT left pair mode / timeout
CMD_BLE_WIFI_OK = "WIFI_OK"  # IoT accepted creds — stop BLE resends (frees RF for MQTT)
CMD_BLE_SHOESH = "SHOESH"  # Reject: not paired to this base — IoT stops MQTT to us

# Each entry: { "ble_address", "ble_name", "paired_at" }
PAIRED_IOTS: List[dict] = []
# BLE pairing / reject:
# 1) Android #DISCOVER opens a 60s pair window on the base.
# 2) IoT sends "PAIRING" over BLE → if paired or pair window open, send WiFi/MQTT creds.
# 3) IoT sends "WIFI_OK" when creds accepted → base stops BLE resends (frees RF for MQTT).
# 4) IoT sends "IDLE" when pair mode ends → base clears allow for that device.
# 5) Unpaired IoT MQTT #PING → base sends "SHOESH" over BLE so IoT stops MQTT to this base.
# Re-provision: IoT must send PAIRING again (do not spam paired devices every scan).
PAIR_MODE_SECONDS = 60
pair_mode_until = 0.0  # monotonic deadline; 0 = inactive
# ble address/name -> monotonic deadline (IoT sent PAIRING over BLE)
iot_pair_allowed: dict = {}
paired_iots_file = os.path.expanduser("~/Secure/paired_iots.json")

#MQTT callbacks and the BLE/async main loop can touch that data from different threads. 
# Helpers like is_iot_paired() and allow_iot_pair() use with _paired_iots_lock: 
# so only one thread updates or reads that data at a time, avoiding races.
_paired_iots_lock = threading.Lock()


def is_iot_paired(ble_address: str = "", ble_name: str = "") -> bool:
    addr = (ble_address or "").strip().upper()
    name = (ble_name or "").strip()

    with _paired_iots_lock:
        for entry in PAIRED_IOTS:
            if addr and entry.get("ble_address", "").upper() == addr:
                return True
            if name and entry.get("ble_name", "") == name:
                return True
            
    return False
def allow_iot_pair(device_key: str, seconds: int = PAIR_MODE_SECONDS) -> bool:
    """Allow BLE WiFi-cred send for this IoT (address or BLE name) for a limited time."""
    key = (device_key or "").strip()
    if not key:
        return False
    import time
    deadline = time.monotonic() + max(1, int(seconds))
    with _paired_iots_lock:
        iot_pair_allowed[key] = deadline
        iot_pair_allowed[key.upper()] = deadline
    return True
def clear_iot_pair(device_key: str = "", ble_address: str = "", ble_name: str = "") -> bool:
    """Clear PAIRING allow for this IoT (IDLE received)."""
    keys = set()
    for c in (device_key, ble_address, ble_name):
        c = (c or "").strip()
        if c:
            keys.add(c)
            keys.add(c.upper())
    if not keys:
        return False
    removed = False
    with _paired_iots_lock:
        for k in list(iot_pair_allowed.keys()):
            if k in keys or k.upper() in keys:
                del iot_pair_allowed[k]
                removed = True
    return removed
def is_iot_pair_allowed(ble_address: str = "", ble_name: str = "", device_id: str = "") -> bool:
    """True if this IoT sent PAIRING over BLE and allow has not expired."""
    import time
    now = time.monotonic()
    candidates = []
    for c in (ble_address, ble_name, device_id):
        c = (c or "").strip()
        if c:
            candidates.append(c)
            candidates.append(c.upper())
    if not candidates:
        return False
    with _paired_iots_lock:
        expired = [k for k, t in iot_pair_allowed.items() if t < now]
        for k in expired:
            del iot_pair_allowed[k]
        for c in candidates:
            if c in iot_pair_allowed and iot_pair_allowed[c] >= now:
                return True
    return False