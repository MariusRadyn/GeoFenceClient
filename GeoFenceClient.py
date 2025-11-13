import asyncio
from bleak import BleakScanner, BleakClient

# Define custom UUIDs
SERVICE_UUID = 'f3a1c2d0-6b4e-4e9a-9f3e-8d2f1c9b7a1e'
CHAR_UUID = 'c7b2e3f4-1a5d-4c3b-8e2f-9a6b1d8c2f3a'

TARGET_PREFIX = "GeoServer"

async def handle_notifications(sender, data):
    try:
        text = data.decode('utf-8', errors='ignore')
    except:
        text = str(data)
    print(f"[notify] {sender}: {text}")

async def connect_and_handshake(device):
    print(f"Connecting to {device.name} ({device.address})")
    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print("Failed to connect.")
                return

            print("Connected. Subscribing to notifications...")
            await client.start_notify(CHAR_UUID, handle_notifications)

            # write handshake
            ack = "ACK_FROM_CLIENT"; 
            print("TX (Handshake): " + ack)
            await client.write_gatt_char(CHAR_UUID, ack.encode(), response=True)
            
            # wait for reply via notification or read (give it a few seconds)
            try:
                print("Wait RX (Handshake)")
                await asyncio.wait_for(asyncio.sleep(1.0), timeout=6.0)
            except asyncio.TimeoutError:
                pass

            # optionally, read characteristic directly
            try:
                val = await client.read_gatt_char(CHAR_UUID)
                try:
                    print("RX (Handshake):", val.decode())
                except:
                    print("RX (raw):", val)
            except Exception as e:
                print("Read failed:", e)

            await client.stop_notify(CHAR_UUID)
            print("Disconnected")

    except Exception as e:
        print("Error with device:", e)

async def main():
    print("Scanning for Servers (5s)...")
    devices = await BleakScanner.discover(timeout=5.0)
    targets = [d for d in devices if (d.name and d.name.startswith(TARGET_PREFIX))]

    if not targets:
        print("No Servers found.")
        return

    print(f"Found {len(targets)} device(s). Connecting sequentially...")
    for d in targets:
        await connect_and_handshake(d)
        await asyncio.sleep(0.5)  # small gap before next connect

if __name__ == "__main__":
    asyncio.run(main())


