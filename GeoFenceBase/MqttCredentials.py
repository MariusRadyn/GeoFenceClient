"""
MQTT broker credentials for this base station.

Creates three local Mosquitto users (one per base):
  - base    : GeoFenceClient / MqttServer on the Pi
  - iot     : wheel / IoT devices
  - android : Android app

Credentials are stored in ~/Secure/mqtt_credentials.json (mode 0600).
Run with --setup (sudo) to write /etc/mosquitto/passwd and restart Mosquitto.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import Settings as set

def _resolve_secure_dir() -> str:
    """~/Secure for the real user, even when setup was invoked via sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return os.path.join("/home", sudo_user, "Secure")
    return os.path.join(os.path.expanduser("~"), "Secure")

SECURE_DIR = _resolve_secure_dir()
MQTT_CREDS_FILE = os.path.join(SECURE_DIR, "mqtt_credentials.json")
MOSQUITTO_PASSWD = "/etc/mosquitto/passwd"
MOSQUITTO_ACL = "/etc/mosquitto/acl"
MOSQUITTO_CONF_D = "/etc/mosquitto/conf.d/geofence.conf"

MQTT_USER_BASE = "base"
MQTT_USER_IOT = "iot"
MQTT_USER_ANDROID = "android"

# JSON field names sent to Android / IoT in MQTT payloads and Firestore clients doc
MQTT_PAYLOAD_USER = "mqttUser"
MQTT_PAYLOAD_PW = "mqttPw"

FIRE_COLLECT_CLIENTS = "clients"
SERVICE_ACCOUNT_KEY = os.path.join(_resolve_secure_dir(), "ServiceAccountKey.json")

MOSQUITTO_CONF = """# GeoFence — TCP for Android/IoT/Pi, WebSockets for Flutter web
# Do not add another listener 1883 / 9001 elsewhere

# MQTT over TCP (Android app, IoT devices, GeoFenceClient on Pi)
listener 1883 0.0.0.0
protocol mqtt

# MQTT over WebSockets (Flutter web — browsers cannot use TCP 1883)
listener 9001 0.0.0.0
protocol websockets

allow_anonymous false
password_file /etc/mosquitto/passwd
acl_file /etc/mosquitto/acl
"""

MOSQUITTO_CONF_DISABLED_SUFFIX = ".geofence-disabled"
MOSQUITTO_ACL_CONTENT = """# GeoFence topic ACLs (per user)

user base
topic read mqtt/from/#
topic write mqtt/to/#

user iot
topic read mqtt/to/iot
topic read mqtt/to/iot/#
topic write mqtt/from/iot
topic write mqtt/from/iot/#

user android
topic read mqtt/to/android/#
topic write mqtt/from/android
"""

def printDebug(msg, enabled):
    if enabled:
        print(msg, file=sys.stderr)

def _random_password() -> str:
    return secrets.token_urlsafe(24)
def _ensure_secure_dir():
    os.makedirs(SECURE_DIR, exist_ok=True)
    os.chmod(SECURE_DIR, 0o700)
def load_credentials() -> dict:
    if not os.path.exists(MQTT_CREDS_FILE):
        return {}
    with open(MQTT_CREDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
def ensure_credentials(force: bool = False) -> dict:
    """Create credential file if missing; return full creds dict."""
    _ensure_secure_dir()
    creds = load_credentials()
    if creds and not force:
        return creds

    creds = {
        "base": {"username": MQTT_USER_BASE, "password": _random_password()},
        "iot": {"username": MQTT_USER_IOT, "password": _random_password()},
        "android": {"username": MQTT_USER_ANDROID, "password": _random_password()},
    }
    tmp = MQTT_CREDS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2)
    os.replace(tmp, MQTT_CREDS_FILE)
    os.chmod(MQTT_CREDS_FILE, 0o600)
    return creds
def get_role_credentials(role: str) -> tuple[str, str]:
    """role: 'base' | 'iot' | 'android'. Returns ('', '') if not configured."""
    creds = load_credentials()
    if not creds:
        return "", ""
    entry = creds.get(role, {})
    return entry.get("username", ""), entry.get("password", "")
def get_mqtt_payload_for_role(role: str) -> dict:
    """Return {mqttUser, mqttPw} for the given role, or {} if not configured."""
    user, pw = get_role_credentials(role)
    if not user or not pw:
        return {}
    return {MQTT_PAYLOAD_USER: user, MQTT_PAYLOAD_PW: pw}
def get_firestore_mqtt_fields() -> dict:
    """Fields to merge into clients/{bt_name} alongside IP address."""
    creds = load_credentials()
    if not creds:
        return {}

    fields = {}
    android = creds.get("android", {})
    if android.get("username") and android.get("password"):
        fields[MQTT_PAYLOAD_USER] = android["username"]
        fields[MQTT_PAYLOAD_PW] = android["password"]
    return fields
def _get_bluetooth_alias() -> str:
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            if "Alias:" in line:
                return line.split("Alias:")[1].strip()
    except Exception:
        pass
    return ""
def push_credentials_to_firestore(bt_name: str = "") -> bool:
    """
    Merge mqttUser/mqttPw (android) and mqttUserIot/mqttPwIot (iot) into
    clients/{bt_name} — same document as IPAdress.
    """
    printDebug(f"Pushing Firestore Creds", set.PRINT_DEBUG_FIRESTORE)

    fields = get_firestore_mqtt_fields()
    if not fields:
        printDebug("WARNING: No MQTT credentials to push to Firestore", set.PRINT_DEBUG_FIRESTORE)
        return False

    if not bt_name:
        bt_name = _get_bluetooth_alias()
    if not bt_name:
        printDebug("WARNING: No Bluetooth name; cannot push MQTT creds to Firestore", set.PRINT_DEBUG_FIRESTORE)
        return False

    if not os.path.exists(SERVICE_ACCOUNT_KEY):
        printDebug(f"WARNING: Firebase key not found: {SERVICE_ACCOUNT_KEY}", set.PRINT_DEBUG_FIRESTORE)
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(SERVICE_ACCOUNT_KEY))
        db = firestore.client()
        db.collection(FIRE_COLLECT_CLIENTS).document(bt_name).set(fields, merge=True)
        printDebug(f"Firestore MQTT creds written: clients/{bt_name}", set.PRINT_DEBUG_FIRESTORE)
        return True
    except Exception as e:
        printDebug(f"ERROR: Firestore MQTT creds write failed: {e}", set.PRINT_DEBUG_FIRESTORE)
        return False
def _run_mosquitto_passwd(username: str, password: str, create: bool = False):
    cmd = ["sudo", "mosquitto_passwd", "-b"]
    if create:
        cmd.append("-c")  # required when creating a new passwd file
    cmd.extend([MOSQUITTO_PASSWD, username, password])
    subprocess.run(cmd, check=True)
def _sudo_run(cmd: list, **kwargs):
    return subprocess.run(["sudo"] + cmd, **kwargs)
def _disable_conflicting_mosquitto_snippets():
    """Rename other conf.d snippets that also declare listener 1883."""
    result = _sudo_run(["ls", "/etc/mosquitto/conf.d"], capture_output=True, text=True)
    if result.returncode != 0:
        return
    for name in result.stdout.splitlines():
        name = name.strip()
        if not name.endswith(".conf") or name == "geofence.conf":
            continue
        path = f"/etc/mosquitto/conf.d/{name}"
        disabled = path + MOSQUITTO_CONF_DISABLED_SUFFIX
        _sudo_run(["mv", path, disabled], check=False)
def _fix_mosquitto_file_permissions():
    # Broker runs as user mosquitto and must read these files
    for path in (MOSQUITTO_PASSWD, MOSQUITTO_ACL):
        _sudo_run(["chown", "mosquitto:mosquitto", path], check=False)
        _sudo_run(["chmod", "640", path], check=False)
def _validate_mosquitto_config() -> bool:
    # Stop service so a test bind on 1883 is possible
    _sudo_run(["systemctl", "stop", "mosquitto"], check=False)
    test = _sudo_run(
        ["timeout", "2", "mosquitto", "-c", "/etc/mosquitto/mosquitto.conf"],
        capture_output=True,
        text=True,
    )
    # 124 = timeout (broker stayed up) = config OK
    if test.returncode in (0, 124):
        return True
    printDebug("ERROR: Mosquitto config test failed:", set.PRINT_DEBUG_FIRESTORE)
    if test.stderr:
        printDebug(test.stderr, set.PRINT_DEBUG_FIRESTORE)
    if test.stdout:
        printDebug(test.stdout, set.PRINT_DEBUG_FIRESTORE)
    return False
def setup_mosquitto(force_creds: bool = False):
    """Write Mosquitto passwd/acl/conf and restart broker. Requires sudo."""
    if subprocess.run(["which", "mosquitto_passwd"], capture_output=True).returncode != 0:
        printDebug("ERROR: mosquitto_passwd not found. Install: sudo apt install mosquitto", set.PRINT_DEBUG_FIRESTORE)
        sys.exit(1)

    creds = ensure_credentials(force=force_creds)

    # Fresh passwd file (-c on first user creates the file)
    _sudo_run(["rm", "-f", MOSQUITTO_PASSWD], check=False)
    for i, role in enumerate(("base", "iot", "android")):
        user = creds[role]["username"]
        pwd = creds[role]["password"]
        _run_mosquitto_passwd(user, pwd, create=(i == 0))

    _sudo_run(
        ["tee", MOSQUITTO_ACL],
        input=MOSQUITTO_ACL_CONTENT.encode("utf-8"),
        check=True,
    )

    # Remove legacy insecure lines from main config (old install script appended these)
    _sudo_run(
        [
            "sed", "-i",
            r"/^listener 1883/d; /^allow_anonymous true$/d",
            "/etc/mosquitto/mosquitto.conf",
        ],
        check=False,
    )

    _disable_conflicting_mosquitto_snippets()

    _sudo_run(
        ["tee", MOSQUITTO_CONF_D],
        input=MOSQUITTO_CONF.encode("utf-8"),
        check=True,
    )
    _fix_mosquitto_file_permissions()

    if not _validate_mosquitto_config():
        sys.exit(1)

    restart = _sudo_run(["systemctl", "start", "mosquitto"], capture_output=True, text=True)
    if restart.returncode != 0:
        printDebug("ERROR: systemctl restart mosquitto failed", set.PRINT_DEBUG_FIRESTORE)
        if restart.stderr:
            printDebug(restart.stderr, set.PRINT_DEBUG_FIRESTORE)
        _sudo_run(
            ["journalctl", "-u", "mosquitto", "-n", "30", "--no-pager"],
            check=False,
        )
        sys.exit(1)

    printDebug(f"Mosquitto auth enabled. Credentials: {MQTT_CREDS_FILE}",set.PRINT_DEBUG_MQTT_CREDS)
    printDebug("Roles: base (Pi), iot (devices), android (app/web)", set.PRINT_DEBUG_MQTT_CREDS)
    printDebug("Listeners: TCP 1883 (native), WebSockets 9001 (Flutter web)", set.PRINT_DEBUG_MQTT_CREDS)
    push_credentials_to_firestore()

def main():
    parser = argparse.ArgumentParser(description="GeoFence MQTT credentials")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Generate creds and configure Mosquitto (run on the Pi, uses sudo)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate passwords (invalidates existing IoT/Android clients until re-paired)",
    )
    parser.add_argument(
        "--show-base",
        action="store_true",
        help="Print base MQTT username (password not shown)",
    )
    args = parser.parse_args()

    if args.setup:
        setup_mosquitto(force_creds=args.force)
        return

    creds = ensure_credentials()
    if args.show_base:
        printDebug(creds["base"]["username"], set.PRINT_DEBUG_FIRESTORE)
        return

    if not creds:
        printDebug("No credentials. Run: python MqttCredentials.py --setup", set.PRINT_DEBUG_FIRESTORE)
        sys.exit(1)


if __name__ == "__main__":
    main()
