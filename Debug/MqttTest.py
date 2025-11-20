import json
import paho.mqtt.client as mqtt

# IMPORTANT:
# Use the PI's IP address here, not "0.0.0.0"
BROKER = "192.168.1.114"        # <--- CHANGE TO YOUR RPI IP
PORT = 1883

# Example settings
settings = {
    "volume": 80,
    "brightness": 50,
    "mode": "auto"
}

# MUST specify MQTTv5 or v311 otherwise callbacks break
client = mqtt.Client(client_id="raspberry_pi", protocol=mqtt.MQTTv5)

# MQTT v5 has a different callback signature:
# on_connect(client, userdata, flags, reason_code, properties)
def on_connect(client, userdata, flags, reason_code, properties):
    print("Connected to MQTT broker:", reason_code)
    client.subscribe("device/settings/request")
    print("Subscribed to device/settings/request")

def on_message(client, userdata, msg):
    print(f"Request received on topic {msg.topic}")
    print("Payload:", msg.payload)

    try:
        payload = json.loads(msg.payload.decode())
        client_id = payload.get("clientId", "default")
    except:
        print("Invalid JSON received")
        return

    response_topic = f"device/settings/response/{client_id}"
    print("Sending data to:", response_topic)

    client.publish(response_topic, json.dumps(settings))

client.on_connect = on_connect
client.on_message = on_message

client.connect(BROKER, PORT)
client.loop_forever()
