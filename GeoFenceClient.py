#Raspberry Pi GeoFence Client

import argparse
import json
import subprocess
from socket import socket
from unicodedata import name
from xmlrpc import client
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
import asyncio
from bleak import BleakScanner, BleakClient
import re
import MqttService
import WifiCredentials
from WifiCredentials import parser as wifi_parser
import os
import time
import threading
from datetime import datetime, timezone
from queue import Empty
import pickle  # For binary file operations
#import paho.mqtt.client as mqtt


# Create Arguments
# --newcreds : Create new wifi credentials
# --encrypt   : Encrypt wifi credentials
# --wifi : ignore Ethernet Connection, Use
parser = argparse.ArgumentParser(description="GeoFence Client")
parser.add_argument(
    "--wifi", 
    action="store_true",  # This makes it a boolean flag
    help="Ignore Ethernet LAN, use Wifi connection"
)

# Include wifi_parser arguments
for action in wifi_parser._actions:
    parser._add_action(action)

args = parser.parse_args()

# Settings
home_dir = os.path.expanduser("~")
settingsFilePath = os.path.expanduser("~/Secure/settings.json") 
settings = {
    "ipadr": "0.0.0.0",
}
INTERFACE_ETH = "eth0"  # Use Ethernet
INTERFACE_WIFI = "wlan0" # Use WiFi
IP_ADDRESS = ''

#WiFi
WIFI_SSID = ""
WIFI_PASSWORD = ""

# Bluetooth
BT_NAME = ""
BT_CLIENTS = {}
SERVICE_UUID = 'f3a1c2d0-6b4e-4e9a-9f3e-8d2f1c9b7a1e'
CHAR_UUID = 'c7b2e3f4-1a5d-4c3b-8e2f-9a6b1d8c2f3a'
lstBtConnectedDevices = []
CONNECT_ADR = "address"
CONNECT_NAME =  "name"
CONNECT_STATUS =  "connected"
CONNECT_TIME =  "last_seen"

# Commands
CMD_SHARED_WIFI_CREDENTIALS = "wificred" # Format: wificred:ssid>password>ipAdress

# iOT Type
IOT_TYPE_VEHICLE = "Vehicle"
IOT_TYPE_MOBILE_MACHINE = "Mobile Machine"
IOT_TYPE_STATIONARY_MACHINE = "Stationary Machine"
IOT_TYPE_WHEEL = "Distance Wheel"

# Payload Settings
JSON_WHEEL_OPERATOR = "operator"
JSON_WHEEL_SUPERVISOR = "supervisor"
JSON_WHEEL_DISTANCE = "distance"
JSON_WHEEL_LINES = "lines"  
JSON_SET_TIMESTAMP = "timestamp"  
JSON_USER_DOC_ID = "userDocId"  
JSON_MONITOR_DOC_ID = "monDocId"  
JSON_IOT_TYPE = "iotType" 
JSON_IOT_NAME = "iotName" 

# Firestore
TARGET_PREFIX = "iOT"
FIRE_COLLECT_CLIENTS = "clients"
FIRE_COLLECT_USERS = "users"
FIRE_COLLECT_MONITORS = "monitors"
FIRE_COLLECT_IOT_DATA = "iotData"
FIRE_COLLECT_OPERATORS = "operators"

# (Firsetore) Base IP Address
FIRE_SET_IP_ADR = "IPAdress"
FIRE_SET_IP_LAST_CON = "LastConnected"

# (Firsetore) General Settings
FIRE_SETTING_IMAGE = "image"
FIRE_SETTING_MON_TYPE = "iotType"
FIRE_SETTING_MON_NAME = "iotName"
FIRE_SETTING_MON_ID = "monDocId"
FIRE_SETTING_USER_DOC_ID = "userDocId"
FIRE_TIMESTAMP = "timestamp"

# (Firsetore) Distance Wheel
FIRE_WHEEL_OPERATOR = "operator"
FIRE_WHEEL_SUPERVISOR = "supervisor"
FIRE_WHEEL_DISTANCE = "distance"
FIRE_WHEEL_LINES = "lines"
FIRE_WHEEL_LAST_LOG_TIMESTAMP = "lastLogTimestamp"

# (Firsetore) Operators
FIRE_OPERATOR_VERSION = "operatorsVer"
FIRE_OPERATOR_NAME = "name"
FIRE_OPERATOR_SURNAME = "surname"
FIRE_OPERATOR_ACCESS_LEVEL = "accessLevel"
FIRE_OPERATOR_TAG_ID = "tagId"

cred = credentials.Certificate(os.path.expanduser("~/Secure/ServiceAccountKey.json"))
firebase_admin.initialize_app(cred)
dbFire = firestore.client()
operators_version = None
operators_version_watch = None
operators_version_listener_uid = None
operators_version_file = os.path.expanduser("~/Secure/operator_version.bin")
operators_data_file = os.path.expanduser("~/Secure/operators.bin")
uid_data_file = os.path.expanduser("~/Secure/uid.bin")
iot_offline_file = os.path.expanduser("~/Secure/iot_offline_queue.bin")

# Firestore write tuning (kept short so a network blip doesn't freeze the device for 60s)
FIRESTORE_WRITE_TIMEOUT = 15  # seconds, per attempt
FIRESTORE_RETRY_BACKOFF = (1, 3, 9)  # delays between attempts; len = retries after first try
FIRESTORE_OFFLINE_QUEUE_MAX = 5000  # cap so disk doesn't grow forever
  
# Debug
PRINT_DEBUG_ENABLED = False

def on_snapshot(doc_snapshot, changes, read_time):
    global operators_version

    for doc in doc_snapshot:
        data = doc.to_dict()
        current_version = data.get(FIRE_OPERATOR_VERSION)

        # First load: just store value
        if operators_version is None:
            operators_version = current_version
            print("Initial version:", current_version)
            return

        # Only trigger if version increased
        if current_version is not None and current_version > operators_version:
            print(f"Version updated: {operators_version} → {current_version}")

            # ✅ YOUR CUSTOM ACTION HERE
            run_update_logic(current_version)

            # update stored version
            operators_version = current_version
def run_update_logic(new_version):
    print(f"Running update logic for version {new_version}")
    # Put your real work here (reload config, restart process, etc.)

def start_operators_version_listener(uid):
    """Attach Firestore snapshot listener for operatorsVer; safe to call repeatedly (same uid no-op)."""
    global operators_version_watch, operators_version_listener_uid, operators_version

    if uid is None:
        return False
    uid = str(uid).strip()
    if len(uid) < 28:
        print("\nCloud listener not started: No UID. (Connect Android to BASE).")
        return False

    if operators_version_watch is not None and operators_version_listener_uid == uid:
        return False

    try:
        if operators_version_watch is not None:
            operators_version_watch.unsubscribe()
        operators_version_watch = None
        operators_version_listener_uid = None

        operators_version = None
        doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(uid)
        operators_version_watch = doc_ref.on_snapshot(on_snapshot)
        operators_version_listener_uid = uid
        print(f"Cloud listener started on: {uid}")
        return True
    except Exception as e:
        print(f"Operator listener ERROR: {e}")
        operators_version_watch = None
        operators_version_listener_uid = None
        return False


# Methods
def checkWifiConnection():
    try:
        # Get IP address for the specified interface
        result = subprocess.check_output(
            ["iwgetid", "wlan0", "--raw"],
            stderr=subprocess.DEVNULL
        )
        
        ssid = result.decode().strip()  
        if ssid:
            printDebug(f"WIFI Connected: {ssid}")
        else:
            printDebug("No WIFI Connection")
        
        return ssid
    except subprocess.CalledProcessError as e:
        print(f"Error: checkWifiConnection {e}")
        return None
def printDebug(msg):
    if PRINT_DEBUG_ENABLED:
        print(msg)
def fire_sync_ip_address(ipLocal):
    global settings 
    
    # Write IP Address to Firestore
    # Compare to last local IP address (settings)
    # Update firestore when IP changed
    # Android will get IP from firestore
    
    read_settings()
    printDebug(f"IP Address (settings): {settings['ipadr']}")
    printDebug(f"IP Address (Unit): {ipLocal}")
    
    # Update Settings file
    if(ipLocal != settings["ipadr"]):
        printDebug(f"Local IP Address Changed: {ipLocal} -> {settings['ipadr']}"   )
        settings["ipadr"] = ipLocal
        write_local_settings()
   
    # Update Firestore
    ipFire = fire_read_ip_adr(BT_NAME)

    if(ipFire != ipLocal):
        printDebug(f"Update IP Address Firestore: {ipFire} -> {ipLocal}"   )
        fire_write_ip_adr(BT_NAME,IP_ADDRESS)
        return True
    return False  
def get_local_ip_address(interface = INTERFACE_ETH):
    ip = "0.0.0.0"
    try:
        if(interface == INTERFACE_ETH):
            # Get IP address for the specified interface
            result = subprocess.run(
                ["ifconfig", interface],
                capture_output=True,
                text=True,
                check=True
            )

            # Search for the IPv4 address
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
            if match:
                ip = match.group(1)
                print("LAN Connected")
            else:
                print("LAN Connection")

    except subprocess.CalledProcessError:
        print(f"Error: get_local_ip_address(), Could not get IP address for interface {interface}")
    
    # Switch to WiFi if no IP found on Ethernet
    if(interface == INTERFACE_WIFI or ip == "0.0.0.0"):
        try:
            # Try WiFi interface
            result = subprocess.run(
                ["ifconfig", INTERFACE_WIFI],
                capture_output=True,
                text=True,
                check=True
            )

            # Search for the IPv4 address
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
            if match:
                ip = match.group(1)
                print("Wifi Connected")
            else:
                print("No Wifi Connection")
        
        except subprocess.CalledProcessError:
            print(f"ERROR: get_local_ip_address(), Could not get IP address for interface wlan0")
            
    return ip
def read_settings():
    global settings

    if os.path.exists(settingsFilePath):
        with open(settingsFilePath, "r") as f:
            settings = json.load(f)
            printDebug(f"Settings Found: {settings  }")
    else:
        # Create Default Settings file
        printDebug(f"Setings file not found, create default SETTINGS file.")
        settings = {
            "ipadr": "0.0.0.0"
        }
        write_local_settings()        
def write_local_settings():   
    with open(settingsFilePath, "w") as f:
        json.dump(settings, f, indent=4)   
        printDebug(f"Write Local Settings: {settings} to {settingsFilePath}")
def fire_write_ip_adr(bt_name="",ip_address=""):
    
    if bt_name == "":
        print("ERROR: fire_write_ip_adr(), Unknown Bluetooth Name, cannot write to Firestore.")
        return
    
    if ip_address == "0.0.0.0":
        print("ERROR: fire_write_ip_adr(),\nNo IP Address found. Check Nerwork Connection.\nCannot write to Firestore.")
        return
    
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(bt_name)
        doc_ref.set({
            FIRE_SET_IP_ADR: ip_address,
            FIRE_SET_IP_LAST_CON: datetime.now().strftime("%d:%m:%Y %H:%M:%S")
        }, merge=True)
        
        print(f"Firestore Write: {bt_name}@{ip_address}")
    except Exception as e:
        print(f"ERROR: fire_write_ip_adr(), writing to Firestore: {e}")
def fire_read_ip_adr(bt_name=""):
    if bt_name == "":
        print("ERROR: fire_read_ip_adr(), Unknown Bluetooth Name, cannot read from Firestore.")
        return "0.0.0.0"
     
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(bt_name)
        doc = doc_ref.get()

        if doc.exists:
            firestore_ip = doc.to_dict().get(FIRE_SET_IP_ADR, "0.0.0.0")
            printDebug(f"Firestore IP: {firestore_ip}")
            return firestore_ip
        else:
            print(f"Firestore Failed to read document: {bt_name}")
    
    except Exception as e:
        print(f"ERROR: fire_read_ip_adr(), reading from Firestore: {e}") 

    return "0.0.0.0"


# Lock guards the on-disk offline queue against concurrent access.
# Today only the main loop writes to it, but firestore listeners run on
# their own thread, so cheap insurance.
_iot_queue_lock = threading.Lock()


def _iot_queue_load():
    """Return list of pending writes; empty list if file is missing or unreadable."""
    if not os.path.exists(iot_offline_file):
        return []
    try:
        with open(iot_offline_file, 'rb') as f:
            data = pickle.load(f)
        if isinstance(data, list):
            return data
        print(f"Warning: offline queue file has unexpected type {type(data).__name__}, discarding")
    except Exception as e:
        print(f"ERROR: _iot_queue_load: {e}")
    return []


def _iot_queue_save(entries):
    """Atomic write so a power loss mid-save can't corrupt the queue."""
    tmp = iot_offline_file + ".tmp"
    try:
        with open(tmp, 'wb') as f:
            pickle.dump(entries, f)
        os.replace(tmp, iot_offline_file)
    except Exception as e:
        print(f"ERROR: _iot_queue_save: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _iot_queue_append(entry):
    with _iot_queue_lock:
        queue = _iot_queue_load()
        # Cap queue size so a long outage can't fill the disk.
        if len(queue) >= FIRESTORE_OFFLINE_QUEUE_MAX:
            dropped = len(queue) - FIRESTORE_OFFLINE_QUEUE_MAX + 1
            print(f"Warning: offline queue at cap, dropping {dropped} oldest entries")
            queue = queue[dropped:]
        queue.append(entry)
        _iot_queue_save(queue)
        printDebug(f"Offline queue size: {len(queue)}")


def _commit_wheel_write(userDocId, monDocId, doc, tStamp):
    """Build and commit the wheel batch with a short timeout. Returns True on success."""
    batch = dbFire.batch()

    iot_doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userDocId) \
        .collection(FIRE_COLLECT_MONITORS).document(monDocId) \
        .collection(FIRE_COLLECT_IOT_DATA).document()
    batch.set(iot_doc_ref, doc, merge=False)

    mon_doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userDocId) \
        .collection(FIRE_COLLECT_MONITORS).document(monDocId)
    batch.set(mon_doc_ref, {FIRE_WHEEL_LAST_LOG_TIMESTAMP: tStamp}, merge=True)

    batch.commit(timeout=FIRESTORE_WRITE_TIMEOUT)
    return True


def _commit_with_retry(userDocId, monDocId, doc, tStamp):
    """Try the write up to 1 + len(FIRESTORE_RETRY_BACKOFF) times. Returns True on success."""
    attempts = 1 + len(FIRESTORE_RETRY_BACKOFF)
    for i in range(attempts):
        try:
            return _commit_wheel_write(userDocId, monDocId, doc, tStamp)
        except Exception as e:
            is_last = (i == attempts - 1)
            if is_last:
                print(f"ERROR: firestore commit failed after {attempts} attempts: {type(e).__name__}: {e}")
                return False
            delay = FIRESTORE_RETRY_BACKOFF[i]
            print(f"Warning: firestore commit attempt {i + 1}/{attempts} failed ({type(e).__name__}: {e}), retrying in {delay}s")
            time.sleep(delay)
    return False


def _iot_queue_flush():
    """One attempt per queued entry; stop on first failure to avoid a long block."""
    with _iot_queue_lock:
        queue = _iot_queue_load()
        if not queue:
            return

        printDebug(f"Flushing {len(queue)} queued IoT writes")
        remaining = list(queue)
        flushed = 0
        while remaining:
            userDocId, monDocId, doc, tStamp = remaining[0]
            try:
                _commit_wheel_write(userDocId, monDocId, doc, tStamp)
                remaining.pop(0)
                flushed += 1
            except Exception as e:
                print(f"Warning: queue flush stopped at entry {flushed} ({type(e).__name__}: {e}); {len(remaining)} remain")
                break

        if flushed:
            print(f"Flushed {flushed} queued IoT writes; {len(remaining)} remain")
        _iot_queue_save(remaining)


def _resolve_timestamp(epoch):
    """Returns a timezone-aware UTC datetime; falls back to now() on garbage/missing/out-of-range."""
    if epoch is None:
        return datetime.now(timezone.utc)
    try:
        epoch_int = int(str(epoch).strip())
    except (ValueError, TypeError):
        print(f"Warning: invalid timestamp {epoch!r}, using current UTC time")
        return datetime.now(timezone.utc)

    # Auto-detect ms vs s: anything >= 1e12 is treated as milliseconds.
    epoch_sec = epoch_int // 1000 if epoch_int >= 1_000_000_000_000 else epoch_int

    # Valid range: ~2023-11-14 (1700000000) to ~2099-01-01 (4070908800)
    if 1_700_000_000 <= epoch_sec <= 4_070_908_800:
        return datetime.fromtimestamp(epoch_sec, tz=timezone.utc)

    print(f"Warning: timestamp {epoch_int} out of range, using current UTC time")
    return datetime.now(timezone.utc)


def fire_write_iot_data(payload):
    """Synchronous Firestore write. Designed to be called via asyncio.to_thread()."""
    try:
        tStamp = _resolve_timestamp(payload.get(FIRE_TIMESTAMP))

        iotType = payload.get(JSON_IOT_TYPE)
        monDocId = payload.get(JSON_MONITOR_DOC_ID)
        userDocId = payload.get(JSON_USER_DOC_ID)

        if not userDocId or not monDocId:
            print("ERROR: Missing userDocId or monDocId")
            return

        if iotType != IOT_TYPE_WHEEL:
            printDebug(f"Unknown Test Type: {iotType}")
            return

        try:
            distance = float(str(payload.get(JSON_WHEEL_DISTANCE, 0)).strip())
        except (ValueError, TypeError):
            print(f"Warning: invalid distance {payload.get(JSON_WHEEL_DISTANCE)!r}, defaulting to 0.0")
            distance = 0.0

        try:
            lines = int(float(str(payload.get(JSON_WHEEL_LINES, 0)).strip()))
        except (ValueError, TypeError):
            print(f"Warning: invalid lines {payload.get(JSON_WHEEL_LINES)!r}, defaulting to 0")
            lines = 0

        doc = {
            FIRE_WHEEL_DISTANCE: distance,
            FIRE_WHEEL_LINES: lines,
            FIRE_WHEEL_OPERATOR: payload.get(JSON_WHEEL_OPERATOR, "none"),
            FIRE_WHEEL_SUPERVISOR: payload.get(JSON_WHEEL_SUPERVISOR, "none"),
            FIRE_TIMESTAMP: tStamp
        }

        if _commit_with_retry(userDocId, monDocId, doc, tStamp):
            printDebug(f"Firestore Write: {payload}")
            # Opportunistically flush anything that piled up during prior outages.
            _iot_queue_flush()
        else:
            # Capture-time tStamp is preserved so flushed writes keep their original time.
            _iot_queue_append((userDocId, monDocId, doc, tStamp))
            print("Queued IoT write for later retry")

    except Exception as e:
        print(f"ERROR: fire_write_iot_data: {type(e).__name__}: {e}")
def read_user_id_from_file():
    global uid_data_file

    user_id = ""
    if os.path.exists(uid_data_file):
        try:
            with open(uid_data_file, 'rb') as f:
                user_id = pickle.load(f)
                printDebug(f"Read User ID from file: {user_id}")
    
        except Exception as e:
            print(f"read_user_id_from_file(): {e}")
            user_id = ""
    
    return user_id
def write_user_id_to_file(uid):
    global uid_data_file

    try:
        with open(uid_data_file, 'wb') as f:
            pickle.dump(uid, f)
            printDebug(f"Saved User ID to file: {uid}")
    except Exception as e:
        print(f"ERROR: write_uid_to_file(), {e}")

#  Operators 
def read_local_operators_ver_from_file():
    global operators_version_file
    opVer = "0"
    
    if os.path.exists(operators_version_file):
        try:
            with open(operators_version_file, 'rb') as f:
                opVer = pickle.load(f)
    
        except Exception as e:
            print(f"fire_read_operators_version(): {e}")
    
    return opVer
def read_local_operators_from_file():
    global operators_data_file
    operators = []
    if os.path.exists(operators_data_file):
        try:
            with open(operators_data_file, 'rb') as f:
                operators = pickle.load(f)
    
        except Exception as e:
            print(f"read_local_operators_list(): {e}")
            operators = []
    
    return operators    
def fire_read_operators_version():
    try:
        uid = read_user_id_from_file()

        if(len(uid) < 28):  # Firestore User Doc IDs are 28 chars long
            print("Cant sync operator list. No User ID found. Connect Android app to Base Station")
            return "0"
            
        version_doc = dbFire.collection(FIRE_COLLECT_USERS).document(uid)
        user_snapshot = version_doc.get()
        
        version = "0"
        if user_snapshot.exists:
            user_data = user_snapshot.to_dict()
            version = user_data.get(FIRE_OPERATOR_VERSION, 0)
        else:
            dbFire.collection(FIRE_COLLECT_USERS).document(uid).set({FIRE_OPERATOR_VERSION: version})
        
        return version
    
    except Exception as e:
        print(f"fire_read_operators_version(): {e}")
        return "0"
def fire_read_operators(userId=""): 
    try:
        if not userId:
            print("ERROR: fire_read_operators(), Missing userDocId")
            return []
            
        operators_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userId)\
            .collection(FIRE_COLLECT_OPERATORS)
        
        docs = operators_ref.stream()
        
        operators = []
        for doc in docs:
            operators.append(doc.to_dict())
            
        printDebug(f"Firestore Read Operators: {len(operators)} operators found")
        return operators
        
    except Exception as e:
        print(f"ERROR: fire_read_operators(): {e}")
        return []
def fire_sync_operator_list(iot_operators_version = "0"):
    global operators_version_file
    global operators_data_file

    try:
        uid = read_user_id_from_file()

        if(len(uid) < 28):  # Firestore User Doc IDs are 28 chars long
            print("Cant sync operator list. No User ID found. Connect Android app to Base Station")
            return "0"
        
        print(f"Syncing operators for UID: {uid}...")
        
        fire_operator_ver = fire_read_operators_version()
        if(fire_operator_ver == iot_operators_version):
            print("Up to Date.")
            return False
        
        # Get Operators
        operators = fire_read_operators(uid)
    
        if operators:
            operator_data = []
            for op in operators:
                operator_data.append({
                    FIRE_OPERATOR_NAME: op.get(FIRE_OPERATOR_NAME, ""),
                    FIRE_OPERATOR_SURNAME: op.get(FIRE_OPERATOR_SURNAME, ""),
                    FIRE_OPERATOR_ACCESS_LEVEL: op.get(FIRE_OPERATOR_ACCESS_LEVEL, 0),
                    FIRE_OPERATOR_TAG_ID: op.get(FIRE_OPERATOR_TAG_ID, "")
                })
            
            # Save Local Operators List
            with open(operators_data_file, 'wb') as f:
                pickle.dump(operator_data, f)
            
            print(f"Saved {len(operator_data)} operators to {operators_data_file}")
        
        # Create new operators version
        new_version = str(int(datetime.now(timezone.utc).timestamp()))  
        
        # Save Local operators version
        with open(operators_version_file, 'wb') as f:
            pickle.dump(new_version, f)
        
        # Save Firestore operators version
        dbFire.collection(FIRE_COLLECT_USERS)\
            .document(uid)\
            .set({FIRE_OPERATOR_VERSION: new_version}, merge=True )

        print(f"Updated version to: {new_version}")
        return True
    
    except Exception as e:
        print(f"ERROR: sync_operator_list: {e}")
        return False
    
# Bluetooth Methods
async def bt_discover():
    global lstBtConnectedDevices
    showMsg = True
    
    print("\nStarting Bluetooth Discovery... ")
    
    while True:
        # Discover
        devices = await BleakScanner.discover(timeout=3.0)
        targets = [
            d for d in devices
            if d.name and d.name.startswith(TARGET_PREFIX)
        ]
       
        # Connect
        if not targets:
            if(showMsg):
                showMsg = False
                
                for dev in lstBtConnectedDevices:
                    dev['connected'] = False
                
                print("BT: No New iOT devices found.\n")
                
        else:
            showMsg = True
            print(f"BT: Found {len(targets)} iOT devices")
            for dev in targets:
                print(f"{dev.name}")
             
            # track which devices were seen
            seen_addresses = [d.address for d in targets]

            # mark unseen devices offline
            for dev in lstBtConnectedDevices:
                if dev['address'] not in seen_addresses:
                    dev['connected'] = False
            
            for device in targets:
                await bt_connect(device)
                await bt_update_connection_status(device)
                await bt_send_credentials(device)
                    
        await asyncio.sleep(10)   # yield 10s
async def bt_connect(device):
    global BT_CLIENTS
    address = device.address

    # Already have a client?
    if address in BT_CLIENTS and BT_CLIENTS[address].is_connected:
        printDebug(f"Already connected: {device.name} ({address}) ")
        return True

    print(f"Connecting BT : {device.name} ({address}) ... ")
    
    client = BleakClient(device)

    try:
        await client.connect()

        if not client.is_connected:
            print(f"ERROR bt_connect(), FAILED {device.name}")
            return False

        # Register disconnect handler
        # client.set_disconnected_callback(
        #     lambda c: print(f"DISCONNECTED: {device.name} [{address}]")
        # )

        await client.start_notify(CHAR_UUID, bt_notification_handler)

        # Store and reuse this client
        BT_CLIENTS[address] = client

        print(f"SUCCESSFUL")
        return True

    except Exception as e:
        print(f"ERROR: bt_connect(), {device.name}: {e}")
        return False
async def bt_update_connection_status(device):
    global lstBtConnectedDevices

    try:
        addr = device.address
        name = device.name if device.name else "Unknown"
        date_time = datetime.now().strftime("%d:%m:%Y %H:%M:%S")

        # Check if device is already in list
        existing = next((x for x in lstBtConnectedDevices if x[CONNECT_ADR] == addr), None)

        # Add new device if not found
        if existing is None:
            existing = {
                CONNECT_ADR: addr,
                CONNECT_NAME: name,
                CONNECT_STATUS: False,
                CONNECT_TIME: date_time
            }
            lstBtConnectedDevices.append(existing)
            printDebug(f"New Device Found: {name} ({addr})")
        else:
            existing[CONNECT_STATUS] = False

        client = bt_get_client(device)

        if client and client.is_connected:
            printDebug(f"Update Connection status: true")
            existing[CONNECT_STATUS] = True
            existing[CONNECT_TIME] = date_time

    except Exception as e:
        print(f"ERROR: bt_update_connection_status(), {device.name}: {e}")
        return False
    
    printDebug(f"Updating connection status...")
    return True
async def bt_send_credentials(device):
    try:
        printDebug("Sending Credentials... ")
    
        cred = f"{CMD_SHARED_WIFI_CREDENTIALS}:{WIFI_SSID}>{WIFI_PASSWORD}>{IP_ADDRESS}"
        client = bt_get_client(device)
    
        printDebug(f"Get Client: {client.name}")
    
        if client and client.is_connected:
            await client.write_gatt_char(CHAR_UUID, cred.encode(), response=True)
            printDebug("Done\n")
            return True

    except Exception as e:
        print(f"ERROR: bt_send_credentials(), {device.name}: {e}")
        return False    
def bt_get_name():
    printDebug("Getting Bluetooth Name... ")
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            check=True
        )

        for line in result.stdout.splitlines():
            if "Alias:" in line:
                # Extract the name after 'Alias:'
                name = line.split("Alias:")[1].strip()
                printDebug(f"My Name: {name}")
                return name
        printDebug("Bluetooth name not found")
        return ""

    except Exception as e:
        print(f"Error: bt_get_name(), {e}")
        return ""
def bt_get_client(device):
    printDebug(f"Getting BT Client for: {device.name} ({device.address}) ")

    if device.address in BT_CLIENTS and BT_CLIENTS[device.address].is_connected:
        printDebug(f"Already connected: {device.name} ({device.address}) ")
        return  BT_CLIENTS.get(device.address)
    else:
        print(f"No BT Client found for: {device.name} ({device.address}) ")
        return None
async def bt_notification_handler(sender, data):
    printDebug(f"[notify] {sender}: {data}")

async def main():
    global WIFI_SSID
    global WIFI_PASSWORD
    global BT_NAME
    global IP_ADDRESS
    global mqtt_broker
    casePtr = 0

    # Firestore operator-version listener: start if UID already on disk, else after MQTT #CONNECT_BAS
    uid = read_user_id_from_file()
    start_operators_version_listener(uid)

    while True:
        match casePtr:

            # Get Unit Name
            case 0:
                BT_NAME = bt_get_name()
                print(f"\nBluetooth Name: {BT_NAME}")
                casePtr+=1
            
            # Connect LAN / Wifi
            case 1:
                if(args.wifi):
                   IP_ADDRESS = get_local_ip_address(INTERFACE_WIFI)
                   print(f"WIFI IP Address: {IP_ADDRESS}")

                   wifiname = checkWifiConnection()
                   if(wifiname):
                       wifiConnected = True
                       print(f"WIFI Connected: {wifiname}")
                else:
                   IP_ADDRESS = get_local_ip_address(INTERFACE_ETH)
                   print(f"LAN IP Address: {IP_ADDRESS}")
                    
                casePtr+=1
            
            # Save IP to Firestore
            case 2:
                if(fire_sync_ip_address(IP_ADDRESS)):
                    print(f"Update Firestore IP: {IP_ADDRESS}")
                
                casePtr+=1
            
            # Get Wifi Credenitials
            case 3:
                WIFI_SSID,WIFI_PASSWORD = WifiCredentials.get_credentials(new_creds=args.newcreds, encrypt=args.encrypt)
                print(f"SSID: {WIFI_SSID}")
                #print(f"Password: {WIFI_PASSWORD}") 
                casePtr+=1

            # Discover Bluetooth iOT Devices
            # Send Wifi Credentials
            case 4:
                asyncio.create_task(bt_discover())
                casePtr+=1

            # Start MQTT Service
            case 5:
                mqtt_broker = MqttService.MqttServer(
                     client_id = BT_NAME,
                     broker_ip = IP_ADDRESS,
                )
                
                #mqtt_broker.client_id = BT_NAME
                #mqtt_broker.broker_ip = IP_ADDRESS 
                await mqtt_broker.connectMqtt()
                casePtr+=1
            
            # Idle
            case 6:
                await asyncio.sleep(0.1)
                try:
                    if not mqtt_broker.queue.empty():
                        message = mqtt_broker.queue.get_nowait()

                        command = message.get(MqttService.MQTT_SETTING_CMD, "")
                        payload = message.get(MqttService.MQTT_SETTING_PAYLOAD, {})
                        
                        # IOT Data - off-loaded to a worker thread so a Firestore network
                        # blip can't freeze the BLE/MQTT event loop for up to FIRESTORE_WRITE_TIMEOUT
                        # x (1 + len(FIRESTORE_RETRY_BACKOFF)) seconds.
                        if command == MqttService.MQTT_CMD_IOT_DATA:
                            asyncio.create_task(asyncio.to_thread(fire_write_iot_data, payload))
                        
                        # Connect Base
                        elif command == MqttService.MQTT_CMD_CONNECT_BASE:
                            iot_type = payload.get(MqttService.MQTT_SETTING_IOT_TYPE, "")
                            uid = payload.get(MqttService.MQTT_SETTING_USER_ID, "")
                            
                            if iot_type == IOT_TYPE_WHEEL:
                                write_user_id_to_file(uid)
                            if len(str(uid or "").strip()) >= 28:
                                start_operators_version_listener(uid)
                     
                        # Sync IOT
                        elif command == MqttService.MQTT_CMD_SYNC:
                            iot_type = payload.get(MqttService.MQTT_SETTING_IOT_TYPE, "")
                            iot_operator_version = payload.get(MqttService.MQTT_SETTING_OPERATORS_VERSION, "")
                            
                            # Sync Operators
                            if iot_type == IOT_TYPE_WHEEL:
                                if fire_sync_operator_list(iot_operator_version):
                                    operators = read_local_operators_from_file()
                                    if operators:
                                        #new_operator_version = read_local_operators_ver_from_file()
                                        mqtt_broker.sendOperators(operators, operators_version)
                     
                except Empty:
                    pass
    
    
            case _:
                await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())

# OLD
async def xbt_connect(device):
    print(f"Connecting to {device.name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print(f"{device.name} not Connected")
                await client.connect()

                if not client.is_connected:
                    print(f"{device.name} Failed to connect.")
                    return False

                await client.start_notify(CHAR_UUID, bt_notification_handler)
            else:
                print(f"{device.name} Connected")
    except Exception as e:
        print("Error with device:", e)
    
    return True
async def bt_handshake(device):
    print(f"Handshaking to {device.name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                await client.connect()

            if not client.is_connected:
                print("Failed to connect.")
                return

            await client.start_notify(CHAR_UUID, bt_notification_handler)

            # write handshake
            name = device.name or ""
            ackClient = "BT_ACK_FROM_CLIENT_" + name.lower()
            ackServer = "BT_ACK_FROM_SERVER_" + name.lower()
            
            print("TX (Handshake): " + ackClient)
            await client.write_gatt_char(CHAR_UUID, ackClient.encode(), response=True)
            
            # read characteristic directly
            try:
                val = await client.read_gatt_char(CHAR_UUID)
                try:
                    print("RX (Handshake):", val.decode())
                    
                    if(val.decode() == ackServer):
                        print("Handshake PASS");  
                    else:
                        print("Handshake FAIL");  
                except:
                    print("RX (error):", val)
            except Exception as e:
                print("Read failed:", e)

            await client.stop_notify(CHAR_UUID)
            print("Disconnected")

    except Exception as e:
        print("Error with device:", e)

