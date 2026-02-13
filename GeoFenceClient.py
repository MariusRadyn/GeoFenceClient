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
from datetime import datetime, timezone
from queue import Empty
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

# iot Type
IOT_TYPE_VEHICLE = "Vehicle"
IOT_TYPE_MOBILE_MACHINE = "Mobile Machine"
IOT_TYPE_STATIONARY_MACHINE = "Stationary Machine"
IOT_TYPE_WHEEL = "Distance Wheel"

# Payload Settings
JSON_SET_OPERATOR = "operator"
JSON_SET_SUPERVISOR = "supervisor"
JSON_SET_DISTANCE = "distance"
JSON_SET_LINES = "lines"  
JSON_SET_TIMESTAMP = "timestamp"  
JSON_USER_ID = "userId"  
JSON_MONITOR_DOC_ID = "docId"  
JSON_IOT_TYPE = "iotType" 

# Firestore
TARGET_PREFIX = "iOT"
FIRE_COLLECT_CLIENTS = "clients"
FIRE_COLLECT_USERS = "users"
FIRE_COLLECT_MONITORS = "monitors"
FIRE_COLLECT_MONITOR_DATA = "data"

FIRE_SET_IP_ADR = "IPAdress"
FIRE_SET_IP_LAST_CON = "LastConnected"

# (Firsetore) Distance Wheel
FIRE_SET_OPERATOR = "operator"
FIRE_SET_SUPERVISOR = "supervisor"
FIRE_SET_DISTANCE = "distance"
FIRE_SET_LINES = "lines"
FIRE_SET_TIMESTAMP = "timestamp"

cred = credentials.Certificate(os.path.expanduser("~/Secure/ServiceAccountKey.json"))
firebase_admin.initialize_app(cred)
dbFire = firestore.client()

# Debug
PRINT_DEBUG_ENABLED = False

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

    except subprocess.CalledProcessError as e:
        print(f"Error: checkWifiConnection {e}")
    return ssid 
def printDebug(msg):
    if PRINT_DEBUG_ENABLED:
        print(msg)
def sync_ip_address(ipLocal):
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
            FIRE_SET_IP_LAST_CON: datetime.datetime.now().strftime("%d:%m:%Y %H:%M:%S")
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
def fire_write_iot_data(payload):  
    try:
        # Time stamp
        epoch = payload.get("timestamp")  # safe get
        if epoch is None:
            print("Warning: timestamp missing, using current UTC time")
            tStamp = datetime.now(timezone.utc)
        else:
            tStamp = datetime.fromtimestamp(epoch, tz=timezone.utc)

        iotType = payload.get(JSON_IOT_TYPE)
        userId = payload.get(JSON_USER_ID)
        monId = payload.get(JSON_MONITOR_DOC_ID)

        if(iotType == IOT_TYPE_WHEEL):
            doc_ref = dbFire.collection(FIRE_COLLECT_USERS).document(userId)\
                .collection(FIRE_COLLECT_MONITORS).document(monId)\
                .collection(FIRE_COLLECT_MONITOR_DATA).document()
            
            doc_ref.set({
                FIRE_SET_DISTANCE: payload.get(JSON_SET_DISTANCE, 0.0),
                FIRE_SET_OPERATOR: payload.get(JSON_SET_OPERATOR, "none"),
                FIRE_SET_SUPERVISOR: payload.get(JSON_SET_SUPERVISOR, "none"),
                FIRE_SET_LINES: payload.get(JSON_SET_LINES, 0),
                FIRE_SET_TIMESTAMP: tStamp
            }, merge=True)
            
            printDebug(f"Firestore Write: {payload}")
    except Exception as e:
        print(f"ERROR: fire_write_ip_adr(), writing to Firestore: {e}")

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

        printDebug(f"BT Connected Status: {lstBtConnectedDevices}")
                    
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
        date_time = datetime.datetime.now().strftime("%d:%m:%Y %H:%M:%S")

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
    wifiConnected = False
    casePtr = 0

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
                if(sync_ip_address(IP_ADDRESS)):
                    print(f"Update Firestore IP: {IP_ADDRESS}")
                casePtr+=1
            
            # Get Wifi Credenitials
            case 3:
                WIFI_SSID,WIFI_PASSWORD = WifiCredentials.get_credentials(new_creds=args.newcreds, encrypt=args.encrypt)
                print(f"SSID: {WIFI_SSID}")
                print(f"Password: {WIFI_PASSWORD}") 
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
                await mqtt_broker.connectMqtt()
                casePtr+=1
            
            # Idle
            case 6:
                await asyncio.sleep(0.01)

                try:
                    payload = mqtt_broker.queue.get_nowait()
                    fire_write_iot_data(payload)
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
