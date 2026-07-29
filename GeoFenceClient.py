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
import MqttCredentials
import Settings as cfg
import WifiCredentials
from Settings import args
import os
import time
import threading
import uuid
from datetime import datetime, timezone
from queue import Empty
import pickle  # For binary file operations
from dataclasses import dataclass
from typing import List, Optional
#import paho.mqtt.client as mqtt


# Create Arguments
# --newcreds : Create new wifi credentials
# --encrypt   : Encrypt wifi credentials
# --wifi : ignore Ethernet Connection, Use
# parser = argparse.ArgumentParser(description="GeoFence Client")
# parser.add_argument(
#     "--wifi", 
#     action="store_true",  # This makes it a boolean flag
#     help="Ignore Ethernet LAN, use Wifi connection"
# )

# # Include wifi_parser arguments
# for action in wifi_parser._actions:
#     parser._add_action(action)

#args = parser.parse_args()

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
CMD_SHARED_WIFI_CREDENTIALS = "wificred" # Format: wificred:ssid>password>ipAdress>mqttUser>mqttPw

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
JSON_WHEEL_TICKS = "ticks"  
JSON_SET_TIMESTAMP = "timestamp"  
JSON_USER_DOC_ID = "userDocId"  
JSON_MONITOR_DOC_ID = "monDocId"
JSON_MONITOR_DEVICE_ID = "monitorId"
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
FIRE_SET_MQTT_USER = MqttCredentials.MQTT_PAYLOAD_USER
FIRE_SET_MQTT_PW = MqttCredentials.MQTT_PAYLOAD_PW

# (Firsetore) General Settings
FIRE_SETTING_IMAGE = "image"
FIRE_SETTING_MON_TYPE = "type"
FIRE_SETTING_MON_NAME = "iotName"
FIRE_SETTING_MON_ID = "monDocId"
FIRE_SETTING_USER_DOC_ID = "userDocId"
FIRE_TIMESTAMP = "timestamp"
# Client-generated id; used as the Firestore document id under iotData so retries cannot duplicate rows.
FIRE_IOT_WRITE_ID = "iotWriteId"
FIRE_DOC_ID = "docId"

# (Firsetore) Distance Wheel
FIRE_WHEEL_OPERATOR = "operator"
FIRE_WHEEL_OPERATOR_DOC_ID = "operatorDocId"
FIRE_WHEEL_SUPERVISOR = "supervisor"
FIRE_WHEEL_SUPERVISOR_DOC_ID = "supervisorDocId"
FIRE_WHEEL_DISTANCE = "distance"
FIRE_WHEEL_LINES = "lines"
FIRE_WHEEL_TICKS = "ticks"
FIRE_WHEEL_LAST_LOG_TIMESTAMP = "lastLogTimestamp"


# (Firsetore) Operators
FIRE_OPERATOR_VERSION = "operatorsVer"
FIRE_OPERATOR_NAME = "name"
FIRE_OPERATOR_SURNAME = "surname"
FIRE_OPERATOR_ACCESS_LEVEL = "accessLevel"
FIRE_OPERATOR_TAG_ID = "tagId"


# (Firsetore) Monitors
FIRE_MONITOR_NAME = "name"
FIRE_MONITOR_IMAGE_URL = "imageURL"
FIRE_MONITOR_IMAGE_FILENAME = "imageFilename"
FIRE_MONITOR_ID = "monitorId"
FIRE_MONITOR_MARKED_FOR_DELETE = "markedToDelete"

_shoesh_last_sent: dict = {}  # ble_name -> monotonic time
_SHOESH_COOLDOWN_S = 10

# Lock guards the on-disk offline queue against concurrent access.
# Today only the main loop writes to it, but firestore listeners run on
# their own thread, so cheap insurance.
_iot_queue_lock = threading.Lock()

@dataclass
class MONITOR_DATA:
    mon_doc_id: str = ""
    mon_device_id: str = ""
    mon_name: str = ""
    image_url: str = ""
    image_filename: str = ""
    mon_: str = ""
    mon_type: str = ""

MONITOR_DATA_LIST: List[MONITOR_DATA] = []

cred = credentials.Certificate(os.path.expanduser("~/Secure/ServiceAccountKey.json"))
firebase_admin.initialize_app(cred)
dbFire = firestore.client()
operators_version = None
operators_version_doc_ref = None
operators_version_listener_uid = None
operators_version_file = os.path.expanduser("~/Secure/operator_version.bin")
operators_data_file = os.path.expanduser("~/Secure/operators.bin")
uid_data_file = os.path.expanduser("~/Secure/uid.bin")
iot_offline_file = os.path.expanduser("~/Secure/iot_offline_queue.bin")

monitors_listener_uid = None
monitors_doc_ref = None
new_operator_data_available = False

# Firestore write tuning (kept short so a network blip doesn't freeze the device for 60s)
FIRESTORE_WRITE_TIMEOUT = 15  # seconds, per attempt
FIRESTORE_RETRY_BACKOFF = (1, 3, 9)  # delays between attempts; len = retries after first try
FIRESTORE_OFFLINE_QUEUE_MAX = 5000  # cap so disk doesn't grow forever

# Listeners
def on_snapshot_operator(doc_snapshot, changes, read_time):
    global operators_version
    global new_operator_data_available

    for doc in doc_snapshot:
        data = doc.to_dict()
        current_version = data.get(FIRE_OPERATOR_VERSION)

        # First load: just store value
        if operators_version is None:
            operators_version = current_version
            printDebug(f"Initial Operators Version: {current_version}",cfg.PRINT_DEBUG_OPERATOR)
            return

        # Only trigger if version increased
        if current_version is not None and current_version > operators_version:
            printDebug(f"Operators Version updated: {operators_version} → {current_version}",cfg.PRINT_DEBUG_OPERATOR)

            # update stored version
            operators_version = current_version
            new_operator_data_available = True
def start_operators_version_listener(uid):
    """Attach Firestore snapshot listener for operatorsVer; safe to call repeatedly (same uid no-op)."""
    global operators_version_doc_ref, operators_version_listener_uid, operators_version
    
    printDebug(f"\nStarting operators listener ...",cfg.PRINT_DEBUG_GENERAL)
    
    if uid is None:
        printDebug("\nCloud listener not started: No UID. (Connect Android to BASE).",cfg.PRINT_DEBUG_ERROR)
        return False
    uid = str(uid).strip()
    if len(uid) < 28:
        printDebug("\nCloud listener not started: No UID. (Connect Android to BASE).",cfg.PRINT_DEBUG_ERROR)
        return False

    if operators_version_doc_ref is not None and operators_version_listener_uid == uid:
        return False

    try:
        if operators_version_doc_ref is not None:
            operators_version_doc_ref.unsubscribe()
        operators_version_doc_ref = None
        operators_version_listener_uid = None

        operators_version = None
        doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(uid)
        operators_version_doc_ref = doc_ref.on_snapshot(on_snapshot_operator)
        operators_version_listener_uid = uid
        printDebug(f"Operator Cloud listener started on: {uid}",cfg.PRINT_DEBUG_GENERAL)
        return True
    except Exception as e:
        printDebug(f"Operator listener ERROR: {e}",cfg.PRINT_DEBUG_ERROR)
        operators_version_doc_ref = None
        operators_version_listener_uid = None
        return False
def on_snapshot_monitors(doc_snapshot, changes, read_time):
    global MONITOR_DATA_LIST

    MONITOR_DATA_LIST.clear()
    to_delete = []  # (mon_doc_id, mon_device_id, mon_name)

    for doc in doc_snapshot:
        if not doc.exists:
            continue
        data = doc.to_dict()
        if not data:
            continue

        mon_id = doc.id
        mon_device_id = data.get(FIRE_MONITOR_ID) or ""
        mon_name = data.get(FIRE_MONITOR_NAME) or ""
        mon_image_url = data.get(FIRE_MONITOR_IMAGE_URL)
        mon_image_filename = data.get(FIRE_MONITOR_IMAGE_FILENAME)
        mon_type = data.get(FIRE_SETTING_MON_TYPE)

        if data.get(FIRE_MONITOR_MARKED_FOR_DELETE) is True:
            to_delete.append((mon_id, mon_device_id, mon_name))
            printDebug(
                f"Monitor markedForDelete: doc={mon_id} device={mon_device_id}",
                cfg.PRINT_DEBUG_MONITOR,
            )
            continue

        MONITOR_DATA_LIST.append(
            MONITOR_DATA(
                mon_doc_id=mon_id,
                mon_device_id=mon_device_id,
                mon_name=mon_name,
                image_url=mon_image_url or "",
                image_filename=mon_image_filename or "",
                mon_type=mon_type or "",
            )
        )

    printDebug(f"\nMonitor Data: {MONITOR_DATA_LIST}\n", cfg.PRINT_DEBUG_MONITOR)

    for mon_id, mon_device_id, mon_name in to_delete:
        _delete_marked_monitor(mon_id, mon_device_id, mon_name)
def start_monitors_listener(uid):
    """Attach Firestore snapshot listener for monitors; Watch Monitor Name, imageURL, imageFilename."""
    global MONITOR_DATA_LIST, monitors_doc_ref, monitors_listener_uid

    if uid is None:
        printDebug("Cloud monitors listener not started: No UID. (Connect Android to BASE).", cfg.PRINT_DEBUG_ERROR)
        return False
    uid = str(uid).strip()
    if len(uid) < 28:
        printDebug(f"Cloud monitors listener not started: UID too short ({len(uid)} chars). Connect Android to BASE.", cfg.PRINT_DEBUG_ERROR)
        return False

    if monitors_doc_ref is not None and monitors_listener_uid == uid:
        printDebug(f"Monitors listener already running for uid={uid}", cfg.PRINT_DEBUG_GENERAL)
        return False

    try:
        if monitors_doc_ref is not None:
            monitors_doc_ref.unsubscribe()
        monitors_doc_ref  = None
        monitors_listener_uid = None

        MONITOR_DATA_LIST = []
        doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(uid).collection(FIRE_COLLECT_MONITORS)
        monitors_doc_ref = doc_ref.on_snapshot(on_snapshot_monitors)
        monitors_listener_uid = uid
        printDebug(f"Monitors Cloud listener started on: {uid}", cfg.PRINT_DEBUG_GENERAL)
        return True

    except Exception as e:
        printDebug(f"Monitors listener ERROR: {e}", cfg.PRINT_DEBUG_ERROR)
        monitors_doc_ref = None
        monitors_listener_uid = None
        return False

# Methods
def _resolve_uid_from_payload(payload) -> str:
    """Accept userId or userDocId from Android CONNECT_BASE payload."""
    if not isinstance(payload, dict):
        if isinstance(payload, str) and payload.strip():
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return ""
        else:
            return ""
    uid = (
        payload.get(MqttService.MQTT_SETTING_USER_ID)
        or payload.get(JSON_USER_DOC_ID)
        or payload.get(FIRE_SETTING_USER_DOC_ID)
        or ""
    )
    return str(uid).strip()
def _delete_marked_monitor(mon_doc_id: str, mon_device_id: str = "", mon_name: str = ""):
    """Remove from paired list, then delete the Firestore monitor document."""
    uid = monitors_listener_uid
    if not uid:
        printDebug("ERROR: cannot delete monitor — no monitors listener uid", cfg.PRINT_DEBUG_ERROR)
        return

    remove_paired_iot(ble_name=mon_device_id or mon_name)
    printDebug(
        f"Removed from paired list: device_id={mon_device_id!r} name={mon_name!r}",
        cfg.PRINT_DEBUG_MONITOR,
    )

    try:
        dbFire.collection(FIRE_COLLECT_USERS).document(uid) \
            .collection(FIRE_COLLECT_MONITORS).document(mon_doc_id) \
            .delete()
        printDebug(f"Deleted Firestore monitor: {mon_doc_id}", cfg.PRINT_DEBUG_FIRESTORE)
    except Exception as e:
        printDebug(f"ERROR: delete monitor {mon_doc_id}: {e}", cfg.PRINT_DEBUG_ERROR)
def get_monitor_data_by_device_id(iot_device_id: str) -> Optional[MONITOR_DATA]:
    """Return the monitor whose mon_device_id matches the IoT device id, or None."""
    if not iot_device_id:
        return None
    device_id = str(iot_device_id).strip()
    if not device_id:
        return None
    for mon in MONITOR_DATA_LIST:
        if mon.mon_device_id == device_id:
            return mon
    return None
def checkWifiConnection():
    try:
        # Get IP address for the specified interface
        result = subprocess.check_output(
            ["iwgetid", "wlan0", "--raw"],
            stderr=subprocess.DEVNULL
        )
        
        ssid = result.decode().strip()  
        if ssid:
            printDebug(f"WIFI Connected: {ssid}", cfg.PRINT_DEBUG_WIFI)
        else:
            printDebug("No WIFI Connection", cfg.PRINT_DEBUG_WIFI)
        
        return ssid
    except subprocess.CalledProcessError as e:
        printDebug(f"Error: checkWifiConnection {e}",cfg.PRINT_DEBUG_ERROR)
        return None
def printDebug(msg, enabled):
    if enabled:
        print(msg)
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
                printDebug("LAN Connected",cfg.PRINT_DEBUG_GENERAL)
            else:
                printDebug("LAN Connection",cfg.PRINT_DEBUG_GENERAL)

    except subprocess.CalledProcessError:
        printDebug(f"Error: get_local_ip_address(), Could not get IP address for interface {interface}",cfg.PRINT_DEBUG_ERROR)
    
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
            else:
                printDebug("No Wifi Connection",cfg.PRINT_DEBUG_WIFI)
        
        except subprocess.CalledProcessError:
            printDebug(f"ERROR: get_local_ip_address(), Could not get IP address for interface wlan0",cfg.PRINT_DEBUG_ERROR)
            
    return ip
def read_settings():
    global settings

    if os.path.exists(settingsFilePath):
        with open(settingsFilePath, "r") as f:
            settings = json.load(f)
            printDebug(f"Settings Found: {settings  }", cfg.PRINT_DEBUG_GENERAL)
    else:
        # Create Default Settings file
        printDebug(f"Setings file not found, create default SETTINGS file.", cfg.PRINT_DEBUG_ERROR)
        settings = {
            "ipadr": "0.0.0.0"
        }
        write_local_settings()        
def write_local_settings():   
    with open(settingsFilePath, "w") as f:
        json.dump(settings, f, indent=4)   
        printDebug(f"Write Local Settings: {settings} to {settingsFilePath}", cfg.PRINT_DEBUG_GENERAL)
def _resolve_timestamp(epoch):
    """Returns a timezone-aware UTC datetime; falls back to now() on garbage/missing/out-of-range."""
    if epoch is None:
        return datetime.now(timezone.utc)
    try:
        epoch_int = int(str(epoch).strip())
    except (ValueError, TypeError):
        printDebug(f"Warning: invalid timestamp {epoch!r}, using current UTC time",cfg.PRINT_DEBUG_ERROR)
        return datetime.now(timezone.utc)

    # Auto-detect ms vs s: anything >= 1e12 is treated as milliseconds.
    epoch_sec = epoch_int // 1000 if epoch_int >= 1_000_000_000_000 else epoch_int

    # Valid range: ~2023-11-14 (1700000000) to ~2099-01-01 (4070908800)
    if 1_700_000_000 <= epoch_sec <= 4_070_908_800:
        return datetime.fromtimestamp(epoch_sec, tz=timezone.utc)

    printDebug(f"Warning: timestamp {epoch_int} out of range, using current UTC time",cfg.PRINT_DEBUG_ERROR)
    return datetime.now(timezone.utc)
def _iot_queue_load():
    """Return list of pending writes; empty list if file is missing or unreadable."""
    if not os.path.exists(iot_offline_file):
        return []
    try:
        with open(iot_offline_file, 'rb') as f:
            data = pickle.load(f)
        if isinstance(data, list):
            return data
        printDebug(f"Warning: offline queue file has unexpected type {type(data).__name__}, discarding",cfg.PRINT_DEBUG_ERROR)
    except Exception as e:
        printDebug(f"ERROR: _iot_queue_load: {e}",cfg.PRINT_DEBUG_ERROR)
    return []
def _iot_queue_save(entries):
    """Atomic write so a power loss mid-save can't corrupt the queue."""
    tmp = iot_offline_file + ".tmp"
    try:
        with open(tmp, 'wb') as f:
            pickle.dump(entries, f)
        os.replace(tmp, iot_offline_file)
    except Exception as e:
        printDebug(f"ERROR: _iot_queue_save: {e}",cfg.PRINT_DEBUG_ERROR)
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
            printDebug(f"Warning: Offline queue Overflow, dropping {dropped} oldest entries", cfg.PRINT_DEBUG_GENERAL)
            queue = queue[dropped:]
        queue.append(entry)
        _iot_queue_save(queue)
        printDebug(f"Offline queue size: {len(queue)}", cfg.PRINT_DEBUG_GENERAL)
def _sanitize_iot_write_id(raw):
    """Return a Firestore-safe document id fragment, or None if unusable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or len(s) > 128:
        return None
    for ch in s:
        if ch.isalnum() or ch in ("_", "-"):
            continue
        return None
    return s
def _ensure_iot_write_id(doc):
    """
    Guarantee doc[FIRE_IOT_WRITE_ID] is set and return it.
    Used as the iotData document id so ambiguous failures + retries upsert instead of duplicating.
    """
    existing = doc.get(FIRE_IOT_WRITE_ID)
    cleaned = _sanitize_iot_write_id(existing)
    if cleaned:
        doc[FIRE_IOT_WRITE_ID] = cleaned
        return cleaned
    nid = uuid.uuid4().hex
    doc[FIRE_IOT_WRITE_ID] = nid
    return nid
def _commit_wheel_write(userDocId, monDocId, doc, tStamp):
    """Build and commit the wheel batch with a short timeout. Returns True on success."""
    iot_write_id = _ensure_iot_write_id(doc)
    batch = dbFire.batch()

    iot_doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userDocId) \
        .collection(FIRE_COLLECT_MONITORS).document(monDocId) \
        .collection(FIRE_COLLECT_IOT_DATA).document(iot_write_id)
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
                printDebug(f"ERROR: firestore commit failed after {attempts} attempts: {type(e).__name__}: {e}",cfg.PRINT_DEBUG_ERROR)
                return False
            delay = FIRESTORE_RETRY_BACKOFF[i]
            printDebug(f"Warning: firestore commit attempt {i + 1}/{attempts} failed ({type(e).__name__}: {e}), retrying in {delay}s",cfg.PRINT_DEBUG_ERROR)
            time.sleep(delay)
    return False
def _iot_queue_flush():
    """One attempt per queued entry; stop on first failure to avoid a long block."""
    with _iot_queue_lock:
        queue = _iot_queue_load()
        if not queue:
            return

        printDebug(f"Flushing {len(queue)} queued IoT writes", cfg.PRINT_DEBUG_GENERAL)
        remaining = list(queue)
        flushed = 0
        while remaining:
            userDocId, monDocId, doc, tStamp = remaining[0]
            try:
                _commit_wheel_write(userDocId, monDocId, doc, tStamp)
                remaining.pop(0)
                flushed += 1
            except Exception as e:
                printDebug(f"Warning: queue flush stopped at entry {flushed} ({type(e).__name__}: {e}); {len(remaining)} remain",cfg.PRINT_DEBUG_ERROR)
                break

        if flushed:
            printDebug(f"Flushed {flushed} queued IoT writes; {len(remaining)} remain", cfg.PRINT_DEBUG_FIRESTORE)
        _iot_queue_save(remaining)
def read_user_id_from_file():
    global uid_data_file

    user_id = ""
    if os.path.exists(uid_data_file):
        try:
            with open(uid_data_file, 'rb') as f:
                user_id = pickle.load(f)
                printDebug(f"Read User ID from file: {user_id}",cfg.PRINT_DEBUG_GENERAL)
    
        except Exception as e:
            printDebug(f"read_user_id_from_file(): {e}", cfg.PRINT_DEBUG_ERROR)
            user_id = ""
    return user_id
def write_user_id_to_file(uid):
    global uid_data_file

    try:
        with open(uid_data_file, 'wb') as f:
            pickle.dump(uid, f)
            printDebug(f"Saved User ID to file: {uid}",cfg.PRINT_DEBUG_GENERAL)
    except Exception as e:
        printDebug(f"ERROR: write_uid_to_file(), {e}",cfg.PRINT_DEBUG_ERROR)

# firestore
def fire_write_iot_data(payload, iot_device_id=None):
    """Synchronous Firestore write. Designed to be called via asyncio.to_thread()."""
    try:
        tStamp = _resolve_timestamp(payload.get(FIRE_TIMESTAMP))

        iotType = payload.get(JSON_IOT_TYPE)
        monDocId = payload.get(JSON_MONITOR_DOC_ID)
        userDocId = payload.get(JSON_USER_DOC_ID)
        operatorDocId = payload.get(FIRE_WHEEL_OPERATOR_DOC_ID)
        supervisorDocId = payload.get(FIRE_WHEEL_SUPERVISOR_DOC_ID)
        iot_device_id = payload.get(JSON_MONITOR_DEVICE_ID)
        iot_name = ""
        img_url = ""
        img_file = ""
        iot_type = ""
        
        mon_data = get_monitor_data_by_device_id(iot_device_id)
        if mon_data:
            monDocId = monDocId or mon_data.mon_doc_id
            iot_name = mon_data.mon_name or payload.get(JSON_IOT_NAME, "")
            img_url = mon_data.image_url
            img_file = mon_data.image_filename
            iot_type = mon_data.mon_type
        else:
            printDebug(f"fire_write_iot_data(): Monitor: ({iot_device_id}) not found", cfg.PRINT_DEBUG_ERROR)
            return

        if not userDocId or not monDocId:
            printDebug("ERROR: Missing userDocId or monDocId",cfg.PRINT_DEBUG_ERROR)
            return

        if iotType != IOT_TYPE_WHEEL:
            printDebug(f"fire_write_iot_data, Unknown Test Type: {iotType}",cfg.PRINT_DEBUG_ERROR)
            return

        try:
            distance = float(str(payload.get(JSON_WHEEL_DISTANCE, 0)).strip())
        except (ValueError, TypeError):
            printDebug(f"Warning: invalid distance {payload.get(JSON_WHEEL_DISTANCE)!r}, defaulting to 0.0", cfg.PRINT_DEBUG_ERROR)
            distance = 0.0

        try:
            lines = int(float(str(payload.get(JSON_WHEEL_LINES, 0)).strip()))
        except (ValueError, TypeError):
            printDebug(f"Warning: invalid lines {payload.get(JSON_WHEEL_LINES)!r}, defaulting to 0", cfg.PRINT_DEBUG_ERROR)
            lines = 0

        try:
            ticks = int(float(str(payload.get(JSON_WHEEL_TICKS, 0)).strip()))
        except (ValueError, TypeError):
            printDebug(f"Warning: invalid lines {payload.get(JSON_WHEEL_TICKS)!r}, defaulting to 0", cfg.PRINT_DEBUG_ERROR)
            ticks = 0

        doc = {
            FIRE_SETTING_MON_TYPE: iot_type,
            FIRE_SETTING_MON_NAME: iot_name,
            FIRE_MONITOR_ID: iot_device_id,
            FIRE_MONITOR_IMAGE_URL: img_url,
            FIRE_MONITOR_IMAGE_FILENAME: img_file,
            FIRE_SETTING_USER_DOC_ID: userDocId,
            FIRE_SETTING_MON_ID: monDocId,
            FIRE_WHEEL_OPERATOR_DOC_ID: operatorDocId,
            FIRE_WHEEL_SUPERVISOR_DOC_ID: supervisorDocId,
            FIRE_WHEEL_LINES: lines,
            FIRE_WHEEL_TICKS: ticks,
            FIRE_TIMESTAMP: tStamp
        }

        supplied = _sanitize_iot_write_id(payload.get(FIRE_IOT_WRITE_ID))
        if supplied:
            doc[FIRE_IOT_WRITE_ID] = supplied
        else:
            doc[FIRE_IOT_WRITE_ID] = uuid.uuid4().hex

        if _commit_with_retry(userDocId, monDocId, doc, tStamp):
            printDebug(f"Firestore Write: {payload}",cfg.PRINT_DEBUG_FIRESTORE)
            # flush anything that piled up during prior outages.
            _iot_queue_flush()
        else:
            # Capture-time tStamp is preserved so flushed writes keep their original time.
            _iot_queue_append((userDocId, monDocId, doc, tStamp))
            printDebug("Queued IoT write for later retry", cfg.PRINT_DEBUG_ERROR)

    except Exception as e:
        printDebug(f"ERROR: fire_write_iot_data: {type(e).__name__}: {e}", cfg.PRINT_DEBUG_ERROR)
def fire_sync_ip_address(ipLocal):
    global settings 
    
    # Write IP Address to Firestore
    # Compare to last local IP address (settings)
    # Update firestore when IP changed
    # Android will get IP from firestore

    ipLocal = (ipLocal or "").strip()
    if not ipLocal or ipLocal == "0.0.0.0":
        printDebug(
            "Skip Firestore IP sync: no valid IP (0.0.0.0)",
            cfg.PRINT_DEBUG_ERROR,
        )
        return False
    
    read_settings()
    printDebug(f"IP Address (settings): {settings['ipadr']}", cfg.PRINT_DEBUG_IP_INFO)
    printDebug(f"IP Address (Unit): {ipLocal}", cfg.PRINT_DEBUG_IP_INFO)
    
    # Update Settings file
    if(ipLocal != settings["ipadr"]):
        printDebug(f"Local IP Address Changed: {ipLocal} -> {settings['ipadr']}", cfg.PRINT_DEBUG_IP_INFO)
        settings["ipadr"] = ipLocal
        write_local_settings()
   
    # Update Firestore
    ipFire = fire_read_ip_adr(BT_NAME)

    if(ipFire != ipLocal):
        printDebug(f"Update IP Address Firestore: {ipFire} -> {ipLocal}", cfg.PRINT_DEBUG_IP_INFO)
        fire_write_ip_adr(BT_NAME, ipLocal)
        return True

    # IP unchanged — still ensure MQTT creds are on the client doc if configured
    fire_write_mqtt_creds(BT_NAME)
    return False
def fire_write_ip_adr(bt_name="",ip_address=""):
    
    if bt_name == "":
        printDebug("ERROR: fire_write_ip_adr(), Unknown Bluetooth Name, cannot write to Firestore.",cfg.PRINT_DEBUG_ERROR)
        return
    
    ip_address = (ip_address or "").strip()
    if not ip_address or ip_address == "0.0.0.0":
        printDebug(
            "ERROR: fire_write_ip_adr(), No valid IP Address. Cannot write 0.0.0.0 to Firestore.",
            cfg.PRINT_DEBUG_ERROR,
        )
        return
    
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(bt_name)
        fields = {
            FIRE_SET_IP_ADR: ip_address,
            FIRE_SET_IP_LAST_CON: datetime.now().strftime("%d:%m:%Y %H:%M:%S"),
        }
        fields.update(MqttCredentials.get_firestore_mqtt_fields())
        doc_ref.set(fields, merge=True)
        
        printDebug(f"Firestore Write: {bt_name}@{ip_address}",cfg.PRINT_DEBUG_FIRESTORE)
    except Exception as e:
        printDebug(f"ERROR: fire_write_ip_adr(), writing to Firestore: {e}",cfg.PRINT_DEBUG_ERROR)
def fire_write_mqtt_creds(bt_name=""):
    """Push mqttUser/mqttPw to clients/{bt_name} (merge). Same doc as IP."""
    if bt_name == "":
        printDebug("ERROR: fire_write_mqtt_creds(), Unknown Bluetooth Name.", cfg.PRINT_DEBUG_ERROR)
        return False
    if MqttCredentials.push_credentials_to_firestore(bt_name):
        printDebug(f"Firestore MQTT creds: clients/{bt_name}", cfg.PRINT_DEBUG_FIRESTORE)
        return True
    return False
def fire_read_ip_adr(bt_name=""):
    if bt_name == "":
        printDebug("ERROR: fire_read_ip_adr(), Unknown Bluetooth Name, cannot read from Firestore.",cfg.PRINT_DEBUG_ERROR)
        return "0.0.0.0"
     
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(bt_name)
        doc = doc_ref.get()

        if doc.exists:
            firestore_ip = doc.to_dict().get(FIRE_SET_IP_ADR, "0.0.0.0")
            printDebug(f"Firestore IP: {firestore_ip}", cfg.PRINT_DEBUG_IP_INFO)
            return firestore_ip
        else:
            printDebug(f"Firestore Failed to read document: {bt_name}",cfg.PRINT_DEBUG_ERROR)
    
    except Exception as e:
        printDebug(f"ERROR: fire_read_ip_adr(), reading from Firestore: {e}",cfg.PRINT_DEBUG_ERROR) 

    return "0.0.0.0"

#  Operators 
def read_local_operators_ver_from_file():
    global operators_version_file
    opVer = "0"
    
    if os.path.exists(operators_version_file):
        try:
            with open(operators_version_file, 'rb') as f:
                opVer = pickle.load(f)
    
        except Exception as e:
            printDebug(f"fire_read_operators_version(): {e}",cfg.PRINT_DEBUG_ERROR)
    
    return opVer
def read_local_operators_from_file():
    global operators_data_file
    operators = []
    if os.path.exists(operators_data_file):
        try:
            with open(operators_data_file, 'rb') as f:
                operators = pickle.load(f)
    
        except Exception as e:
            printDebug(f"read_local_operators_list(): {e}",cfg.PRINT_DEBUG_ERROR)
            operators = []
    
    return operators    
def fire_read_operators_version():
    try:
        uid = read_user_id_from_file()

        if(len(uid) < 28):  # Firestore User Doc IDs are 28 chars long
            printDebug("Cant sync operator list. No User ID found. Connect Android app to Base Station",cfg.PRINT_DEBUG_ERROR)
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
        printDebug(f"fire_read_operators_version(): {e}",cfg.PRINT_DEBUG_ERROR)
        return "0"
def fire_read_operators(userId=""): 
    try:
        if not userId:
            printDebug("ERROR: fire_read_operators(), Missing userDocId",cfg.PRINT_DEBUG_ERROR)
            return []
            
        operators_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userId)\
            .collection(FIRE_COLLECT_OPERATORS)
        
        docs = operators_ref.stream()
        
        operators = []
        for doc in docs:
            operators.append(doc.to_dict())
            
        printDebug(f"Firestore Read Operators: {len(operators)} operators found",cfg.PRINT_DEBUG_OPERATOR)
        return operators
        
    except Exception as e:
        printDebug(f"ERROR: fire_read_operators(): {e}",cfg.PRINT_DEBUG_ERROR)
        return []
def fire_sync_operator_list(iot_operators_version = "0"):
    global operators_version_file
    global operators_data_file

    try:
        uid = read_user_id_from_file()
        if(len(uid) < 28):  # Firestore User Doc IDs are 28 chars long
            printDebug("Cant sync operator list. No User ID found. Connect Android app to Base Station",cfg.PRINT_DEBUG_ERROR)
            return "0"
        
        printDebug(f"Syncing operators for UID: {uid}...",cfg.PRINT_DEBUG_GENERAL)
        
        fire_operator_ver = fire_read_operators_version()
        printDebug(f"fire_operator_ver: {fire_operator_ver}...",cfg.PRINT_DEBUG_OPERATOR)
        printDebug(f"iot_operator_ver: {iot_operators_version}...",cfg.PRINT_DEBUG_OPERATOR)
        
        if(fire_operator_ver == iot_operators_version):
            printDebug("Operators Up to Date.",cfg.PRINT_DEBUG_OPERATOR)
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
                    FIRE_OPERATOR_TAG_ID: op.get(FIRE_OPERATOR_TAG_ID, ""),
                    FIRE_DOC_ID: op.get(FIRE_DOC_ID, "")
                })
            
            # Save Local Operators List
            with open(operators_data_file, 'wb') as f:
                pickle.dump(operator_data, f)
            
            printDebug(f"Saved {len(operator_data)} operators to {operators_data_file}",cfg.PRINT_DEBUG_OPERATOR)
        
        # Save Local operators version
        with open(operators_version_file, 'wb') as f:
            pickle.dump(fire_operator_ver, f)
        
        return True
    
    except Exception as e:
        printDebug(f"ERROR: sync_operator_list: {e}",cfg.PRINT_DEBUG_ERROR)
        return False


# Bluetooth 
def _load_paired_iots():
    """Load paired IoT list from disk into Settings.PAIRED_IOTS."""
    with cfg._paired_iots_lock:
        if not os.path.exists(cfg.paired_iots_file):
            cfg.PAIRED_IOTS = []
            printDebug(f"No paired IoT(s) found", cfg.PRINT_DEBUG_MONITOR)
            return
        try:
            with open(cfg.paired_iots_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.PAIRED_IOTS = data if isinstance(data, list) else []
            printDebug(f"Loaded {len(cfg.PAIRED_IOTS)} paired IoT(s)", True)

            names = [e.get("ble_name", "") for e in cfg.PAIRED_IOTS if e.get("ble_name")]
            if names:
                printDebug("\n".join(f"   {n}" for n in names), True)
        except Exception as e:
            printDebug(f"ERROR: load paired IoTs: {e}", cfg.PRINT_DEBUG_ERROR)
            cfg.PAIRED_IOTS = []
def _save_paired_iots_unlocked():
    """Caller must hold _paired_iots_lock."""
    try:
        os.makedirs(os.path.dirname(cfg.paired_iots_file), exist_ok=True)
        tmp = cfg.paired_iots_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg.PAIRED_IOTS, f, indent=2)
        os.replace(tmp, cfg.paired_iots_file)
        os.chmod(cfg.paired_iots_file, 0o600)
    except Exception as e:
        printDebug(f"ERROR: save paired IoTs: {e}", cfg.PRINT_DEBUG_ERROR)
def _save_paired_iots():
    with cfg._paired_iots_lock:
        _save_paired_iots_unlocked()
def enter_pair_mode(seconds: int = cfg.PAIR_MODE_SECONDS):
    """Allow provisioning unknown BLE iOTs until deadline (Android started discover)."""
    cfg.pair_mode_until = time.monotonic() + max(1, int(seconds))
    printDebug(f"Pair mode ON (all unknown iOT) for {seconds}s", cfg.PRINT_DEBUG_MONITOR)
def is_pair_mode_active() -> bool:
    return time.monotonic() < cfg.pair_mode_until
def allow_iot_pair_for_ble(device_id: str = "", ble_name: str = "", seconds: int = cfg.PAIR_MODE_SECONDS):
    """IoT announced pair mode — allow BLE WiFi creds for that device only."""
    keys = []
    if device_id:
        keys.append(device_id)
    if ble_name:
        keys.append(ble_name)
    if not keys:
        return False
    for key in keys:
        cfg.allow_iot_pair(key, seconds)
    printDebug(
        f"IoT pair allowed for BLE creds: device_id={device_id!r} ble_name={ble_name!r} ({seconds}s)",
        cfg.PRINT_DEBUG_MONITOR,
    )
    return True
def add_paired_iot(ble_address: str = "", ble_name: str = "") -> bool:
    """Add or update a paired IoT. Returns True if list changed."""
    addr = (ble_address or "").strip().upper()
    name = (ble_name or "").strip()
    if not addr and not name:
        return False

    with cfg._paired_iots_lock:
        for entry in cfg.PAIRED_IOTS:
            same = (
                (addr and entry.get("ble_address", "").upper() == addr)
                or (name and entry.get("ble_name", "") == name)
            )
            if same:
                if addr:
                    entry["ble_address"] = addr
                if name:
                    entry["ble_name"] = name
               
                _save_paired_iots_unlocked()
                return True

        cfg.PAIRED_IOTS.append({
            "ble_address": addr,
            "ble_name": name,
            "paired_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_paired_iots_unlocked()
    printDebug(f"Paired IoT added: name={name!r} addr={addr!r}", cfg.PRINT_DEBUG_MONITOR)
    return True
def remove_paired_iot(ble_address: str = "", ble_name: str = "") -> bool:
    addr = (ble_address or "").strip().upper()
    name = (ble_name or "").strip()
    removed = False

    with cfg._paired_iots_lock:
        keep = []
        for entry in cfg.PAIRED_IOTS:
            match = (
                (addr and entry.get("ble_address", "").upper() == addr)
                or (name and entry.get("ble_name", "") == name)
            )
            if match:
                removed = True
            else:
                keep.append(entry)
        cfg.PAIRED_IOTS[:] = keep
        if removed:
            _save_paired_iots_unlocked()
    if removed:
        printDebug(f"Paired IoT removed: name={name!r}", cfg.PRINT_DEBUG_MONITOR)
    return removed
def should_send_ble_credentials(device) -> bool:
    """
    Send WiFi/MQTT creds over BLE only when IoT sent PAIRING
    (iot_pair_allowed). Do not re-send just because the device is
    already paired — that floods BT and breaks MQTT during connect.
    """
    addr = getattr(device, "address", "") or ""
    name = getattr(device, "name", "") or ""
    if cfg.is_iot_pair_allowed(ble_address=addr, ble_name=name):
        return True
    printDebug(
        f"Skip BLE creds for {name} ({addr}): waiting for PAIRING",
        cfg.PRINT_DEBUG_BT,
    )
    return False
async def bt_discover():
    global lstBtConnectedDevices
    reported_no_devices = False
    known_scan_addresses = set()
    
    printDebug("\nStarting Bluetooth Discovery... ",cfg.PRINT_DEBUG_GENERAL)
    
    while True:
        # Discover
        devices = await BleakScanner.discover(timeout=3.0)
        targets = [
            d for d in devices
            if d.name and d.name.startswith(TARGET_PREFIX)
        ]
        seen_addresses = {(d.address or "").upper() for d in targets}
       
        if not targets:
            if not reported_no_devices:
                reported_no_devices = True
                for dev in lstBtConnectedDevices:
                    dev[CONNECT_STATUS] = False
                printDebug("BLE: No iOT devices in range.\n", cfg.PRINT_DEBUG_BT)
        else:
            reported_no_devices = False

            # Only announce devices we have not seen before this session
            new_targets = [
                d for d in targets
                if (d.address or "").upper() not in known_scan_addresses
            ]
            if new_targets:
                printDebug(
                    f"BLE: {len(new_targets)} new iOT device(s)",
                    cfg.PRINT_DEBUG_BT,
                )
                for dev in new_targets:
                    printDebug(f"  {dev.name}", cfg.PRINT_DEBUG_BT)
                    known_scan_addresses.add((dev.address or "").upper())

            # mark unseen devices offline
            for dev in lstBtConnectedDevices:
                if (dev.get(CONNECT_ADR) or "").upper() not in seen_addresses:
                    dev[CONNECT_STATUS] = False
            
            for device in targets:
                # Connect + subscribe to notifies so we can receive PAIRING / WIFI_OK / IDLE
                await bt_connect(device)
                await bt_update_connection_status(device)
                # Credentials are sent only on BLE PAIRING notify (see bt_on_iot_notify)

        await asyncio.sleep(10)   # yield 10s
async def bt_connect(device):
    global BT_CLIENTS
    address = device.address

    # Already have a client?
    if address in BT_CLIENTS and BT_CLIENTS[address].is_connected:
        #printDebug(f"Already connected: {device.name} ({address}) ",cfg.PRINT_DEBUG_BT)
        return True

    printDebug(f"Connecting BT : {device.name} ({address}) ... ",cfg.PRINT_DEBUG_GENERAL)
    
    client = BleakClient(device)

    try:
        await client.connect()

        if not client.is_connected:
            printDebug(f"ERROR bt_connect(), FAILED {device.name}",cfg.PRINT_DEBUG_ERROR)
            return False

        # Per-device notify handler so PAIRING/IDLE know which IoT spoke
        async def _on_notify(sender, data, _device=device):
            await bt_on_iot_notify(_device, data)

        await client.start_notify(CHAR_UUID, _on_notify)

        # Store and reuse this client
        BT_CLIENTS[address] = client

        printDebug(f"SUCCESSFUL",cfg.PRINT_DEBUG_GENERAL)
        return True

    except Exception as e:
        printDebug(f"ERROR: bt_connect(), {device.name}: {e}", cfg.PRINT_DEBUG_ERROR)
        return False
async def bt_update_connection_status(device):
    global lstBtConnectedDevices

    try:
        addr = device.address
        name = device.name if device.name else "Unknown"
        date_time = datetime.now().strftime("%d:%m:%Y %H:%M:%S")
        addr_key = (addr or "").upper()

        # Check if device is already in list (case-insensitive address)
        existing = next(
            (x for x in lstBtConnectedDevices if (x.get(CONNECT_ADR) or "").upper() == addr_key),
            None,
        )

        # Add new device if not found
        if existing is None:
            existing = {
                CONNECT_ADR: addr,
                CONNECT_NAME: name,
                CONNECT_STATUS: False,
                CONNECT_TIME: date_time
            }
            lstBtConnectedDevices.append(existing)
            #printDebug(f"BLE: New Device Found ({name})", cfg.PRINT_DEBUG_BT)

        client = bt_get_client(device)

        if client and client.is_connected:
            existing[CONNECT_STATUS] = True
            existing[CONNECT_TIME] = date_time
        else:
            existing[CONNECT_STATUS] = False

    except Exception as e:
        printDebug(f"ERROR: bt_update_connection_status() {device.name}: {e}", cfg.PRINT_DEBUG_ERROR)
        return False
    
    return True
async def bt_send_shoesh(device) -> bool:
    """Tell IoT to stop MQTT to this base (not paired / wrong base)."""
    try:
        client = bt_get_client(device)
        name = getattr(device, "name", "") or "?"
        if client is None or not client.is_connected:
            printDebug(f"ERROR: bt_send_shoesh(), no client for {name}", cfg.PRINT_DEBUG_ERROR)
            return False
        await client.write_gatt_char(CHAR_UUID, cfg.CMD_BLE_SHOESH.encode(), response=True)
        printDebug(f"BLE ({name}): sent {cfg.CMD_BLE_SHOESH}", cfg.PRINT_DEBUG_BT)
        return True
    except Exception as e:
        printDebug(f"ERROR: bt_send_shoesh() {getattr(device, 'name', '?')}: {e}", cfg.PRINT_DEBUG_BT)
        return False
async def bt_send_shoesh_to_iot(ble_name: str) -> bool:
    """Find a connected BLE IoT by name (MQTT from id) and send SHOESH."""
    name = (ble_name or "").strip()
    if not name:
        return False

    now = time.monotonic()
    last = _shoesh_last_sent.get(name, 0.0)
    if now - last < _SHOESH_COOLDOWN_S:
        return False

    addr = None
    for entry in lstBtConnectedDevices:
        en = (entry.get(CONNECT_NAME) or "").strip()
        if en == name or en.upper() == name.upper():
            addr = entry.get(CONNECT_ADR)
            break

    if not addr:
        printDebug(f"SHOESH: no BLE device for {name!r}", cfg.PRINT_DEBUG_BT)
        return False

    client = BT_CLIENTS.get(addr)
    if client is None or not client.is_connected:
        printDebug(f"SHOESH: BLE not connected for {name!r}", cfg.PRINT_DEBUG_BT)
        return False

    class _Dev:
        pass

    dev = _Dev()
    dev.address = addr
    dev.name = name
    if await bt_send_shoesh(dev):
        _shoesh_last_sent[name] = now
        return True
    return False
async def bt_send_credentials(device):
    try:
        if not should_send_ble_credentials(device):
            return False

        printDebug("Sending Credentials... ", cfg.PRINT_DEBUG_GENERAL)
    
        cred = f"{CMD_SHARED_WIFI_CREDENTIALS}:{WIFI_SSID}>{WIFI_PASSWORD}>{IP_ADDRESS}"
        mqtt_user, mqtt_pass = MqttCredentials.get_role_credentials("iot")
        if mqtt_user and mqtt_pass:
            cred = f"{cred}>{mqtt_user}>{mqtt_pass}"
        client = bt_get_client(device)
    
        if client is None:
            printDebug(f"ERROR: bt_send_credentials(), no client for {device.name}", cfg.PRINT_DEBUG_ERROR)
            return False

        printDebug(f"BT Name: {getattr(client, 'name', device.name)}", cfg.PRINT_DEBUG_GENERAL)
    
        if client and client.is_connected:
            await client.write_gatt_char(CHAR_UUID, cred.encode(), response=True)
            printDebug("Done", cfg.PRINT_DEBUG_GENERAL)
            return True

    except Exception as e:
        printDebug(f"ERROR: bt_send_credentials() {device.name}: {e}", cfg.PRINT_DEBUG_BT)
        return False
    return False 
def bt_get_name():
    printDebug("Getting Bluetooth Name... ",cfg.PRINT_DEBUG_GENERAL)
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
                printDebug(f"My Name: {name}",cfg.PRINT_DEBUG_GENERAL)
                return name
        printDebug("Bluetooth name not found",cfg.PRINT_DEBUG_GENERAL)
        return ""

    except Exception as e:
        printDebug(f"Error: bt_get_name(), {e}",cfg.PRINT_DEBUG_ERROR)
        return ""
def bt_get_client(device):
    if device.address in BT_CLIENTS and BT_CLIENTS[device.address].is_connected:
        return BT_CLIENTS.get(device.address)
    return None
async def bt_notification_handler(sender, data):
    """Legacy unused; prefer per-device handler from bt_connect."""
    printDebug(f"[notify] {sender}: {data}", cfg.PRINT_DEBUG_GENERAL)
async def bt_on_iot_notify(device, data):
    """
    IoT → Base over BLE:
      PAIRING  — if paired or Android pair window open: send WiFi/MQTT creds;
                 else send SHOESH (IoT must stop MQTT to this base)
      WIFI_OK  — IoT accepted creds; stop BLE resends (frees RF for MQTT ping)
      IDLE     — IoT left pair mode; stop allowing creds for this device
    """
    try:
        text = data.decode("utf-8", errors="ignore").strip()
    except Exception:
        printDebug(f"BLE RX decode error from {getattr(device, 'name', '?')}", cfg.PRINT_DEBUG_BT)
        return

    if not text:
        return

    name = getattr(device, "name", "") or ""
    addr = getattr(device, "address", "") or ""
    cmd = text.split(":", 1)[0].strip().upper()

    printDebug(f"BLE RX ({name}): {text}", cfg.PRINT_DEBUG_BT)

    if cmd == cfg.CMD_BLE_PAIRING:
        enter_pair_mode(cfg.PAIR_MODE_SECONDS)

        # Remaining time in the Android pair window
        remaining = max(1, int(cfg.pair_mode_until - time.monotonic()))
        cfg.allow_iot_pair(addr, remaining)
        cfg.allow_iot_pair(name, remaining)
        printDebug(f"BLE ({name}): PAIRING", True)

        if await bt_send_credentials(device):
            add_paired_iot(ble_address=addr, ble_name=name)
            printDebug(f"BLE ({name}): Sent Credentials (Waiting for WIFI_OK)", True)
        return

    if cmd == cfg.CMD_BLE_WIFI_OK:
        # Stop further BLE credential writes so IoT can receive MQTT #PING
        cfg.clear_iot_pair(ble_address=addr, ble_name=name)
        printDebug(f"BLE ({name}): WIFI_OK", True)
        return

    if cmd == cfg.CMD_BLE_IDLE:
        cfg.clear_iot_pair(ble_address=addr, ble_name=name)
        printDebug(f"BLE ({name}): IDLE", True)
        return

async def main():
    global WIFI_SSID
    global WIFI_PASSWORD
    global BT_NAME
    global IP_ADDRESS
    global mqtt_broker
    global new_operator_data_available
    casePtr = 0

    while True:
        match casePtr:

            # Get Unit Name
            case 0:
                BT_NAME = bt_get_name()
                printDebug(f"\nBLE ID: {BT_NAME}",cfg.PRINT_DEBUG_BT)
                _load_paired_iots()
                casePtr+=1
            
            # Connect LAN / Wifi
            case 1:
                if(args.wifi):
                    # Load + verify WiFi first; get_credentials exits if join fails
                    WIFI_SSID, WIFI_PASSWORD = WifiCredentials.get_credentials(
                        new_creds=args.newcreds,
                        dont_encrypt=args.dont_encrypt,
                    )
                    IP_ADDRESS = get_local_ip_address(INTERFACE_WIFI)
                    printDebug(f"WIFI IP Address: {IP_ADDRESS}", cfg.PRINT_DEBUG_WIFI)

                    wifiname = checkWifiConnection()
                    if not wifiname or not IP_ADDRESS or IP_ADDRESS == "0.0.0.0":
                        printDebug(
                            "WiFi not available — will not continue until connected",
                            cfg.PRINT_DEBUG_ERROR,
                        )
                        await asyncio.sleep(5)
                        # Stay on case 1
                    else:
                        casePtr += 1
                else:
                   IP_ADDRESS = get_local_ip_address(INTERFACE_ETH)
                   printDebug(f"LAN IP Address: {IP_ADDRESS}",cfg.PRINT_DEBUG_WIFI)
                   casePtr+=1
            
            # Save IP to Firestore + start cloud listeners (needs network)
            case 2:
                printDebug("case 2: sync IP + start cloud listeners", cfg.PRINT_DEBUG_GENERAL)
                try:
                    if(fire_sync_ip_address(IP_ADDRESS)):
                        printDebug(f"Update Firestore IP: {IP_ADDRESS}",cfg.PRINT_DEBUG_GENERAL)
                except Exception as e:
                    printDebug(f"case 2: fire_sync_ip_address failed: {e}", cfg.PRINT_DEBUG_ERROR)

                # Start listeners after network is up (UID from prior CONNECT_BASE, if any)
                user_id = read_user_id_from_file()
                printDebug(f"case 2: calling start_monitors_listener with user_id={user_id!r}", cfg.PRINT_DEBUG_GENERAL)
                start_operators_version_listener(user_id)
                start_monitors_listener(user_id)
                casePtr+=1
            
            # Get Wifi Credentials (for BLE share to IoTs; already done in case 1 if --wifi)
            case 3:
                if not WIFI_SSID:
                    WIFI_SSID, WIFI_PASSWORD = WifiCredentials.get_credentials(
                        new_creds=args.newcreds,
                        dont_encrypt=args.dont_encrypt,
                    )
                
                printDebug(f"SSID: {WIFI_SSID}",cfg.PRINT_DEBUG_WIFI)
                #print(f"Password: {WIFI_PASSWORD}") 
                casePtr+=1

            # Discover Bluetooth iOT Devices
            # Send Wifi Credentials
            case 4:
                asyncio.create_task(bt_discover())
                casePtr+=1

            # Start MQTT Service
            case 5:
                mqtt_user, mqtt_pass = MqttCredentials.get_role_credentials("base")
                mqtt_broker = MqttService.MqttServer(
                     client_id = BT_NAME,
                     broker_ip = IP_ADDRESS,
                     mqtt_username = mqtt_user,
                     mqtt_password = mqtt_pass,
                )
                
                await mqtt_broker.connectMqtt()
                casePtr+=1
            
            # Idle
            case 6:
                await asyncio.sleep(0.1)

                # New Operator Data Available
                if new_operator_data_available:
                    new_operator_data_available = False
                    mqtt_broker.broadcastNewDataAvailable(IOT_TYPE_WHEEL)
                    printDebug("New data available",cfg.PRINT_DEBUG_GENERAL)

                try:
                    if not mqtt_broker.queue.empty():
                        message = mqtt_broker.queue.get_nowait()

                        command = message.get(MqttService.MQTT_SETTING_CMD, "")
                        payload = message.get(MqttService.MQTT_SETTING_PAYLOAD, {})
                        
                        # IOT Data
                        if command == MqttService.MQTT_CMD_IOT_DATA:
                            iot_device_id = message.get(MqttService.MQTT_SETTING_FROM_DEVICE_ID, "")
                            asyncio.create_task(
                                asyncio.to_thread(fire_write_iot_data, payload, iot_device_id)
                            )

                        # Unpaired IoT #PING → SHOESH over BLE (stop MQTT to this base)
                        elif command == cfg.CMD_BLE_SHOESH:
                            iot_id = message.get(MqttService.MQTT_SETTING_FROM_DEVICE_ID, "")
                            printDebug(
                                f"MQTT #PING from unpaired {iot_id!r} — sending BLE {cfg.CMD_BLE_SHOESH}",
                                True,
                            )
                            asyncio.create_task(bt_send_shoesh_to_iot(iot_id))

                        # PAIR — open 60s BLE pair window
                        # IoT must then send PAIRING over BLE to receive WiFi creds
                        elif command == MqttService.MQTT_CMD_DISCOVERY:
                            enter_pair_mode(cfg.PAIR_MODE_SECONDS)

                        # Connect Base
                        elif command == MqttService.MQTT_CMD_CONNECT_BASE:
                            iot_type = payload.get(MqttService.MQTT_SETTING_IOT_TYPE, "")
                            uid = _resolve_uid_from_payload(payload)
                            print(f"CONNECT_BASE: iotType={iot_type!r} uid={uid!r} (len={len(uid)})")

                            if len(uid) >= 28:
                                write_user_id_to_file(uid)
                                start_operators_version_listener(uid)
                                start_monitors_listener(uid)
                            else:
                                print(
                                    "CONNECT_BASE: no valid userId/userDocId in payload — "
                                    "monitors listener not started"
                                )
                     
                        # Sync IOT
                        elif command == MqttService.MQTT_CMD_SYNC:
                            from_id = message.get(MqttService.MQTT_SETTING_FROM_DEVICE_ID, "")
                            iot_type = payload.get(MqttService.MQTT_SETTING_IOT_TYPE, "")
                            iot_operator_version = payload.get(MqttService.MQTT_SETTING_OPERATORS_VERSION, "")
                            
                            # Sync Operators — send to this IoT only (not a shared global target)
                            if iot_type == IOT_TYPE_WHEEL and from_id:
                                if fire_sync_operator_list(iot_operator_version):
                                    operators = read_local_operators_from_file()
                                    if operators:
                                        ver = read_local_operators_ver_from_file()
                                        if ver is None:
                                            ver = operators_version
                                        mqtt_broker.sendOperators(
                                            operators, ver, to_device_id=from_id
                                        )
                     
                except Empty:
                    pass
    
            case _:
                await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())


