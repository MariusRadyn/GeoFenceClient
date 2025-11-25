import json
import paho.mqtt.client as mqtt

# IMPORTANT:
# Use the PI's IP address here, not "0.0.0.0"
BROKER = "192.168.1.114"        # <--- CHANGE TO YOUR RPI IP
PORT = 1883

MQTT_NAME = "GeoBrokerMqtt"
MQTT_TOPIC_REQ = "device/settings/request"
MQTT_TOPIC_RESPONSE = "device/settings/response"
MQTT_TOPIC_CRED ="device/settings/credentials"

class MqttServer:
    def __init__(
            self, 
            client_id, 
            broker_ip="0.0.0.0", 
            port=1883
            ):
        
        self.client_id = client_id
        self.broker_ip = broker_ip
        self.port = port

        # Example settings
        self.settings = {
            "volume": 80,
            "brightness": 50,
            "mode": "auto"
        }

        # Create MQTT client
        self.client = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv5)

        # Bind callbacks
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    # -----------------------------
    # Connect to the broker
    # -----------------------------
    async def connect(self):
        print(f"MQTT Connecting {self.broker_ip}:{self.port} ... ")
        self.client.connect(self.broker_ip, self.port)
        self.client.loop_start()

    # -----------------------------
    # Callback when connected
    # -----------------------------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        #print(f"[{self.client_id}] Connected to broker: {reason_code}")
        print(f"MQTT Connected: {reason_code}")
        
        # Subscribe to request topic
        client.subscribe(MQTT_TOPIC_REQ)
        print(f"MQTT Subscribed: {MQTT_TOPIC_REQ}")

    # -----------------------------
    # Callback when message received
    # -----------------------------
    def on_message(self, client, userdata, msg):
        print(f"MQTT RX on topic: {msg.topic}")
        print(f"MQTT Payload: {msg.payload.decode()}")

        try:
            payload = json.loads(msg.payload.decode())
            requester_id = payload.get("clientId", "default")
        except json.JSONDecodeError:
            print(f"[{self.client_id}] Invalid JSON received")
            return

        # Publish settings to response topic
        response_topic = f"{MQTT_TOPIC_RESPONSE}/{requester_id}"
        client.publish(response_topic, json.dumps(self.settings))
        print(f"MQTT TX to topic: {response_topic}")

    # -----------------------------
    # Callback when disconnected
    # -----------------------------
    def on_disconnect(self, client, userdata, rc):
        #print(f"[{self.client_id}] Disconnected from broker (rc={rc})")
        print(f"MQTT Disconnected: {rc}")

# -----------------------------
# Usage example
# -----------------------------
if __name__ == "__main__":
    server = MqttServer(client_id="raspberry_pi_1", broker_ip="192.168.1.50")
    server.connect()
