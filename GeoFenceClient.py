#Raspberry Pi GeoFence Client

import json
import subprocess
from socket import socket
from unicodedata import name
import firebase_admin
from firebase_admin import credentials, firestore
import datetime
import asyncio
from bleak import BleakScanner, BleakClient
import re
import MqttService
import WifiCredentials
import os   

# Define custom UUIDs
SERVICE_UUID = 'f3a1c2d0-6b4e-4e9a-9f3e-8d2f1c9b7a1e'
CHAR_UUID = 'c7b2e3f4-1a5d-4c3b-8e2f-9a6b1d8c2f3a'

TARGET_PREFIX = "geoserver"
FIRE_IP_ADR = "IPAdress"
FIRE_IP_LAST_CON = "LastConnected"
FIRE_COLLECT_CLIENTS = "clients"

# Initialize Firebase
cred = credentials.Certificate("/home/geoserver/serviceAccountKey.json")
firebase_admin.initialize_app(cred)
dbFire = firestore.client()

def get_bluetooth_name():
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
                return name

        return "Unknown"

    except subprocess.CalledProcessError:
        return "Error: Bluetooth not available"
def get_ip_address(interface="eth0"):
    try:
        # Run ifconfig for the given interface
        result = subprocess.run(
            ["ifconfig", interface],
            capture_output=True,
            text=True,
            check=True
        )

        # Search for the IPv4 address
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout)
        if match:
            return match.group(1)
        else:
            return "0.0.0.0"  # No IP assigned yet

    except subprocess.CalledProcessError:
        return "0.0.0.0"  # Interface not found or ifconfig error
def fire_write_ip_adr():
    clientName = get_bluetooth_name()
    ipAddress = get_ip_address()
    
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(clientName)
        doc_ref.set({
            FIRE_IP_ADR: ipAddress,
            FIRE_IP_LAST_CON: datetime.datetime.now()
        })
        
        print(f"Firestore Write:")
        print(f"Client Name: {clientName}, IP Address: {ipAddress}")

    except Exception as e:
        print(f"Error writing to Firestore: {e}")
def fire_read_ip_adr():
    clientName = get_bluetooth_name()
    ipAddress = get_ip_address()
    
    try:
        doc_ref = dbFire.collection(FIRE_COLLECT_CLIENTS).document(clientName)
        doc = doc_ref.get()

        if doc.exists:
            firestore_ip = doc.to_dict().get(FIRE_IP_ADR, "No IP field")
            print(f"Firestore IP: {firestore_ip}, Actual IP: {ipAddress}")

        else:
            print(f"Firestore Failed to read document: {clientName}")
    
    except Exception as e:
        print(f"Error reading from Firestore: {e}") 

async def bt_discover():
    print("Starting Bluetooth Discovery... ")
    while True:
        devices = await BleakScanner.discover(timeout=3.0)
        targets = [
            d for d in devices
            if d.name and d.name.lower().startswith(TARGET_PREFIX.lower())
        ]

        if not targets:
            print("No BT servers found.")
        else:
            print(f"Found {len(targets)} BT server(s)")

            for d in targets:
                await bt_handshake(d)
    
        await asyncio.sleep(10)   # yield
async def bt_handshake(device):
    print(f"Handshaking to {device.name} ({device.address})")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print("Failed to connect.")
                return

            #print("Connected. Subscribing to notifications...")
            await client.start_notify(CHAR_UUID, bt_notify)

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
async def bt_notify(sender, data):
    try:
        text = data.decode('utf-8', errors='ignore')
    except:
        text = str(data)
    #print(f"[notify] {sender}: {text}")
async def bt_send_credentials():
    print("Sending Credentials... ")
    
    while True:
        devices = await BleakScanner.discover(timeout=3.0)
        targets = [
            d for d in devices
            if d.name and d.name.lower().startswith(TARGET_PREFIX.lower())
        ]

        if not targets:
            print("No BT servers found.")
        else:
            print(f"Found {len(targets)} BT server(s)")

            for d in targets:
                ssid = f"ssid:{WifiCredentials.WIFI_SSID}" 
                pw = f"pw:{WifiCredentials.WIFI_PASSWORD}" 

                async with BleakClient(d) as client:
                    await client.write_gatt_char(CHAR_UUID, ssid.encode(), response=True)
                    await client.write_gatt_char(CHAR_UUID, pw.encode(), response=True)
                    print("Sent")
  
  
async def main():
    WifiCredentials.get_credentials()

    # Write IP Address to Firestore
    # Android will get IP from there
    fire_write_ip_adr()
    
    mqqt = MqttService.MqttServer(
        client_id=get_bluetooth_name(),
        broker_ip=get_ip_address()
    ) 
    #await mqqt.connect()
    
    # Start Bluetooth Dsicovery Task
    asyncio.create_task(bt_discover())
    asyncio.create_task(bt_send_credentials())
    
    while True:
        await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())


