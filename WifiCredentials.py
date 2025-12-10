import json
import os
import subprocess
from getpass import getpass
from cryptography.fernet import Fernet
import argparse

# Create Arguments
# --new-creds : Create new wifi credentials
# --encrypt   : Encrypt wifi credentials
parser = argparse.ArgumentParser(add_help=False)
parser.add_argument(
    "--new-creds", 
    action="store_true",  # This makes it a boolean flag
    help="Create new wifi credentials"
)
parser.add_argument(
    "--encrypt", 
    action="store_true",  # This makes it a boolean flag
    help="Encrypt wifi credentials"
)
#args = parser.parse_args()

# Variables
SECURE_DIR = os.path.expanduser("~/.secure")
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
def get_credentials(new_creds =False, encrypt=False):
   
    creds = {
        "ssid": "",
        "password": ""
    }
    create_secure_dir()

    if new_creds:
        # Create new credentials
        print("Get new credentials")
        generate_key()
        data = enter_credentials()

        if data:
            write_credentials_file(data, encrypt)
            creds = json.loads(data.decode())
    
    else:
        # Read existing credentials
        if os.path.exists(DATA_FILE):
            print("Restore WiFi credentials")
            creds = read_credentials_file(encrypt)
   
    return creds['ssid'], creds['password']

def main():
    get_credentials(new_creds=args.new_creds, encrypt=args.encrypt)

if __name__ == "__main__":
    main()
