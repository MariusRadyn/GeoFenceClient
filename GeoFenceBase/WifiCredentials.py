import json
import os
import subprocess
import sys
from getpass import getpass
from cryptography.fernet import Fernet

import Settings as cfg
from Settings import args


# Variables
SECURE_DIR = os.path.expanduser("~/Secure")
KEY_FILE = f"{SECURE_DIR}/key.key"
DATA_FILE = f"{SECURE_DIR}/wificredentials.enc"


def printDebug(msg, enabled):
    cfg.printDebug(msg, enabled)


def create_secure_dir():
    if not os.path.exists(SECURE_DIR):
        printDebug(f"Creating secure directory: {SECURE_DIR}", cfg.PRINT_DEBUG_GENERAL)
        os.makedirs(SECURE_DIR, exist_ok=True)
        os.chmod(SECURE_DIR, 0o700)
def generate_key():
    if not os.path.exists(KEY_FILE):
        printDebug("Generating encryption key...", cfg.PRINT_DEBUG_GENERAL)
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
    else:
        printDebug("Encryption key already exists.", cfg.PRINT_DEBUG_GENERAL)
def select_wifi():
    # Interactive --newcreds UI: always show (not gated by debug flags)
    printDebug("Scanning saved WiFi connections...", True)
    result = subprocess.run(["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi","list"],
                            capture_output=True, text=True)

    lines = [l for l in result.stdout.splitlines() if l.strip() != ""]

    if not lines:
        printDebug("No Wi-Fi networks found.", cfg.PRINT_DEBUG_ERROR)
        return None

    printDebug("\nSelect WiFi network:", True)
    ssids = []
    index = 0

    for _, line in enumerate(lines):
        ssid = line.split(":")[0]
        if ssid and ssid not in ssids:
            ssids.append(ssid)
            printDebug(f"[{index}] {ssid}", True)
            index += 1

    index = int(input("Enter number: "))
    return ssids[index]
def enter_credentials():
    ssid = select_wifi()
    if ssid is None:
        return None

    password = getpass(f"Enter password for WiFi '{ssid}': ")
    data = json.dumps({"ssid": ssid, "password": password}).encode()
    return data


def verify_wifi_credentials(ssid, password, timeout_s=20, ifname="wlan0", quiet=False):
    """Try joining the network with nmcli; return True only if connect succeeds."""
    ssid = (ssid or "").strip()
    if not ssid:
        if not quiet:
            printDebug("WiFi verify failed: empty SSID", cfg.PRINT_DEBUG_ERROR)
        return False

    if not quiet:
        printDebug(f"Verifying WiFi credentials for '{ssid}'...", cfg.PRINT_DEBUG_GENERAL)
    con_name = f"geofence-verify-{ssid}"[:100]

    # Newer NetworkManager rejects `device wifi connect ... password` with
    # "802-11-wireless-security.key-mgmt: property is missing".
    # Create an explicit profile that includes wifi-sec.key-mgmt.
    add_cmd = [
        "nmcli", "connection", "add",
        "type", "wifi",
        "con-name", con_name,
        "ifname", ifname,
        "ssid", ssid,
    ]
    if password:
        add_cmd.extend([
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", password,
        ])

    try:
        subprocess.run(
            ["nmcli", "connection", "delete", con_name],
            capture_output=True, text=True,
        )

        add = subprocess.run(add_cmd, capture_output=True, text=True, timeout=15)
        if add.returncode != 0:
            err = (add.stderr or add.stdout or "").strip()
            if not quiet:
                printDebug(f"FAIL: {err or f'exit {add.returncode}'}", cfg.PRINT_DEBUG_ERROR)
            return False

        up = subprocess.run(
            ["nmcli", "-w", str(int(timeout_s)), "connection", "up", con_name],
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
        )
        if up.returncode != 0:
            err = (up.stderr or up.stdout or "").strip()
            if not quiet:
                printDebug(f"FAIL: {err or f'exit {up.returncode}'}", cfg.PRINT_DEBUG_ERROR)
            subprocess.run(
                ["nmcli", "connection", "delete", con_name],
                capture_output=True, text=True,
            )
            return False

        # Keep a normal profile name for later reconnects
        subprocess.run(
            ["nmcli", "connection", "modify", con_name, "connection.id", ssid],
            capture_output=True, text=True, timeout=10,
        )
        if not quiet:
            printDebug(f"WiFi OK: connected to '{ssid}'", cfg.PRINT_DEBUG_GENERAL)
        return True
    except subprocess.TimeoutExpired:
        if not quiet:
            printDebug("WiFi verify failed: timed out", cfg.PRINT_DEBUG_ERROR)
        subprocess.run(
            ["nmcli", "connection", "delete", con_name],
            capture_output=True, text=True,
        )
        return False
    except Exception as e:
        if not quiet:
            printDebug(f"WiFi verify error: {e}", cfg.PRINT_DEBUG_ERROR)
        subprocess.run(
            ["nmcli", "connection", "delete", con_name],
            capture_output=True, text=True,
        )
        return False
def encrypt_credentials(data):
    with open(KEY_FILE, "rb") as f:
        key = f.read()

    # JSON data must be in dict format for encryption
    data = json.loads(data)   # convert string → dict
    json_bytes = json.dumps(data, indent=4).encode('utf-8')

    cipher = Fernet(key)
    encrypted = cipher.encrypt(json_bytes)
    return encrypted
def write_credentials_file(data):
    with open(DATA_FILE, "wb") as f:
        encrypted = encrypt_credentials(data)
        f.write(encrypted)
        printDebug(f"Saved: {DATA_FILE} (Encrypted)", cfg.PRINT_DEBUG_GENERAL)

    os.chmod(DATA_FILE, 0o600)
def read_credentials_file():
    creds = {"ssid": "", "password": ""}

    with open(DATA_FILE, "rb") as data_file:
        data = data_file.read()

    with open(KEY_FILE, "rb") as key_file:
        key = key_file.read()

    cipher = Fernet(key)
    data = cipher.decrypt(data)
    printDebug("Decrypted", cfg.PRINT_DEBUG_GENERAL)

    data_dict = json.loads(data.decode())
    creds["ssid"] = str(data_dict.get("ssid", ""))
    creds["password"] = str(data_dict.get("password", ""))

    return creds
def is_interactive() -> bool:
    """False under systemd (no TTY) — cannot prompt for SSID/password."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def get_credentials(new_creds=False):

    creds = {
        "ssid": "",
        "password": ""
    }
    create_secure_dir()

    if new_creds and not is_interactive():
        printDebug(
            "ERROR: --newcreds / \"newcreds\": true needs a terminal (SSID + password).\n"
            "Do not run this from the geofence service.\n"
            "Stop the service, then as geoserver run:\n"
            "  sudo systemctl stop geofence\n"
            "  /home/geoserver/venv312/bin/python /home/geoserver/GeoFenceBase/WifiCredentials.py --newcreds\n"
            "  sudo systemctl start geofence",
            cfg.PRINT_DEBUG_ERROR,
        )
        # One-shot: clear so systemd Restart= does not keep prompting
        cfg.clear_newcreds()
        new_creds = False

    if new_creds:
        # Create new credentials — verify with nmcli before saving
        printDebug("Get new credentials", cfg.PRINT_DEBUG_GENERAL)
        generate_key()

        while True:
            data = enter_credentials()
            if not data:
                printDebug("No WiFi network selected — aborting.", cfg.PRINT_DEBUG_ERROR)
                sys.exit(1)

            parsed = json.loads(data.decode())
            if verify_wifi_credentials(parsed.get("ssid", ""), parsed.get("password", "")):
                write_credentials_file(data)
                creds = parsed
                # One-shot: do not prompt again on next boot/service start
                cfg.clear_newcreds()
                break

            printDebug("Credentials incorrect or WiFi not available — not saved.", cfg.PRINT_DEBUG_ERROR)
            retry = input("Try again? [Y/n]: ").strip().lower()
            if retry == "n":
                printDebug("Aborting: WiFi credentials not verified.", cfg.PRINT_DEBUG_ERROR)
                sys.exit(1)

    else:
        # Read existing credentials
        if not os.path.exists(DATA_FILE):
            printDebug("No WiFi credentials file found. Run with --newcreds from a terminal.", cfg.PRINT_DEBUG_ERROR)
            if getattr(args, "wifi", False):
                sys.exit(1)
            return creds["ssid"], creds["password"]

        printDebug("Restore WiFi credentials", cfg.PRINT_DEBUG_GENERAL)
        creds = read_credentials_file()

        # When logging onto WiFi, require a live connection before continuing
        if getattr(args, "wifi", False):
            if not verify_wifi_credentials(creds.get("ssid", ""), creds.get("password", "")):
                printDebug("WiFi not available or credentials wrong — aborting.", cfg.PRINT_DEBUG_ERROR)
                sys.exit(1)

    printDebug("WiFi Started.", True)
    return creds['ssid'], creds['password']

def main():
    get_credentials(new_creds=args.newcreds)



if __name__ == "__main__":
    main()
