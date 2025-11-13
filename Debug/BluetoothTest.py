
from bless import BlessServer

# Define custom UUIDs
SERVICE_UUID = 'f3a1c2d0-6b4e-4e9a-9f3e-8d2f1c9b7a1e'
CHAR_UUID = 'c7b2e3f4-1a5d-4c3b-8e2f-9a6b1d8c2f3a'

# Create the BLE server
server = BlessServer(name="raspberrypi")

# Define the writable characteristic with a callback
@server.characteristic(uuid=CHAR_UUID, properties=["write"])
def on_write(value):
    try:
        text = value.decode("utf-8")
        print("Received from Flutter:", text)
    except Exception as e:
        print("Error decoding value:", e)

# Start advertising the service
server.add_service(uuid=SERVICE_UUID)
server.start()
