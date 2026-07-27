import json
import os
import subprocess
from getpass import getpass
from cryptography.fernet import Fernet

from Settings import args


# Include wifi_parser arguments
#for action in parser._actions:
#    parser._add_action(action)

# Variables
SECURE_DIR = os.path.expanduser("~/Secure")
KEY_FILE = f"{SECURE_DIR}/key.key"
DATA_FILE = f"{SECURE_DIR}/wificredentials.enc"


def create_secure_dir():
    if not os.path.exists(SECURE_DIR):
        print(f"Creating secure directory: {SECURE_DIR}")
        os.makedirs(SECURE_DIR, exist_ok=True)
        os.chmod(SECURE_DIR, 0o700)
def generate_key():
    if not os.path.exists(KEY_FILE):
        print("Generating encryption key...")
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
    else:
        print("Encryption key already exists.")
def select_wifi():
    # Get list of WiFi connections from NetworkManager
    print("Scanning saved WiFi connections...")
    result = subprocess.run(["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi","list"],
                            capture_output=True, text=True)

    lines = [l for l in result.stdout.splitlines() if l.strip() != ""]

    if not lines:
        print("No Wi-Fi networks found.")
        return None
    
    print("\nSelect WiFi network:")
    ssids = []
    index = 0

    for _, line in enumerate(lines):
        ssid = line.split(":")[0]
        if ssid and ssid not in ssids:
            ssids.append(ssid)
            print(f"[{index}] {ssid}")
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


def verify_wifi_credentials(ssid, password, timeout_s=20, ifname="wlan0"):
    """Try joining the network with nmcli; return True only if connect succeeds."""
    ssid = (ssid or "").strip()
    if not ssid:
        print("WiFi verify failed: empty SSID")
        return False

    print(f"Verifying WiFi credentials for '{ssid}'...")
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
            print(f"WiFi verify failed (add): {err or f'exit {add.returncode}'}")
            return False

        up = subprocess.run(
            ["nmcli", "-w", str(int(timeout_s)), "connection", "up", con_name],
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
        )
        if up.returncode != 0:
            err = (up.stderr or up.stdout or "").strip()
            print(f"WiFi verify failed: {err or f'exit {up.returncode}'}")
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
        print(f"WiFi OK: connected to '{ssid}'")
        return True
    except subprocess.TimeoutExpired:
        print("WiFi verify failed: timed out")
        subprocess.run(
            ["nmcli", "connection", "delete", con_name],
            capture_output=True, text=True,
        )
        return False
    except Exception as e:
        print(f"WiFi verify error: {e}")
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
def write_credentials_file(data, encrypt=False):
    with open(DATA_FILE, "wb") as f:
        if encrypt:
            # Encrypted
            encrypted = encrypt_credentials(data)
            f.write(encrypted)
            print(f"Saved: {DATA_FILE} (Encrypted)")
        else:
            # Not Encrypted
            f.write(data) 
            print(f"Saved: {DATA_FILE} (Not Encrypted)")

    os.chmod(DATA_FILE, 0o600)
def read_credentials_file(encrypt=False):
    creds = {"ssid": "", "password": ""}

    if encrypt:
        # Encrypted
        with open(DATA_FILE, "rb") as data_file:
            data = data_file.read()

        with open(KEY_FILE, "rb") as key_file:
            key = key_file.read()

        cipher = Fernet(key)   
        data = cipher.decrypt(data)
        print("Decrypted")

        data_dict = json.loads(data.decode())
        creds["ssid"] = str(data_dict.get("ssid", ""))
        creds["password"] = str(data_dict.get("password", ""))
    else:
        with open(DATA_FILE, "r") as f:
            data_dict = json.load(f)
            # normalize keys
            creds["ssid"] = str(data_dict.get("ssid", ""))
            creds["password"] = str(data_dict.get("password", ""))

    return creds    
def get_credentials(new_creds=False, encrypt=False):
   
    creds = {
        "ssid": "",
        "password": ""
    }
    create_secure_dir()

    if new_creds:
        # Create new credentials — verify with nmcli before saving
        print("Get new credentials")
        generate_key()

        while True:
            data = enter_credentials()
            if not data:
                break

            parsed = json.loads(data.decode())
            if verify_wifi_credentials(parsed.get("ssid", ""), parsed.get("password", "")):
                write_credentials_file(data, encrypt)
                creds = parsed
                break

            print("Credentials incorrect or connection failed — not saved.")
            retry = input("Try again? [Y/n]: ").strip().lower()
            if retry == "n":
                break
    
    else:
        # Read existing credentials
        if os.path.exists(DATA_FILE):
            print("Restore WiFi credentials")
            creds = read_credentials_file(encrypt)
   
    return creds['ssid'], creds['password']

def main():
    get_credentials(new_creds=args.newcreds, encrypt=args.encrypt)
    


if __name__ == "__main__":
    main()
