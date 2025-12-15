import json
import time
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

#client.connect("broker_ip", 1883, properties=props)

# IMPORTANT:
# Use the PI's IP address here, not "0.0.0.0"
BROKER = "192.168.1.114"        # <--- CHANGE TO YOUR RPI IP
PORT = 1883

MQTT_NAME = "GeoBrokerMqtt"
MQTT_TOPIC_FROM_IOT = "mqtt/from/iot"
MQTT_TOPIC_TO_IOT = "mqtt/to/iot"
MQTT_TOPIC_FROM_ANDROID = "mqtt/from/android"
MQTT_TOPIC_TO_ANDROID = "mqtt/to/android"
MQTT_TOPIC_ANDROID = "mqtt/android"
MQTT_TOPIC_CRED ="mqtt/credentials"

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
        self.client = mqtt.Client(
            client_id = self.client_id, 
            protocol=mqtt.MQTTv5
            )
        
        # Bind callbacks
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    # -----------------------------
    # Connect to the broker
    # -----------------------------
    async def connect(self):
        print(f"MQTT Connecting {self.broker_ip}:{self.port} ... ")

        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = 60  # in seconds, 0 = never store, max ~4B

        self.client.connect(
            host=self.broker_ip,
            port=self.port,
            keepalive=30,
            clean_start = mqtt.MQTT_CLEAN_START_FIRST_ONLY,  # or mqtt.MQTT_CLEAN_START_TRUE
            properties=props
        )
        self.client.loop_start()
    
    def loop(self):
        self.client.loop()

    # -----------------------------
    # Callbacks
    # -----------------------------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        #print(f"[{self.client_id}] Connected to broker: {reason_code}")
        print(f"MQTT Connected: {reason_code}")
        
        client.subscribe(MQTT_TOPIC_FROM_IOT)
        print(f"MQTT Subscribed: {MQTT_TOPIC_FROM_IOT}")
 
        client.subscribe(MQTT_TOPIC_FROM_ANDROID)
        print(f"MQTT Subscribed: {MQTT_TOPIC_FROM_ANDROID}")
    def on_message(self, client, userdata, msg):
        print(f"MQTT RX: {msg.payload.decode()} {msg.topic}")

        try:
            # From Android
            if(msg.topic == MQTT_TOPIC_FROM_ANDROID):
                response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{requester_id}"
                client.publish(response_topic, json.dumps(self.settings))
                print(f"MQTT TX: {self.settings} {response_topic}")
        
            # From IOT
            if(msg.topic == MQTT_TOPIC_FROM_IOT):
                # JSON Recieved
                payload = json.loads(msg.payload.decode())
                requester_id = payload.get("clientId", "default")
            
                # Publish settings to response topic
                response_topic = f"{MQTT_TOPIC_TO_IOT}/{requester_id}"
                client.publish(response_topic, json.dumps(self.settings))
                print(f"MQTT TX: {self.settings} {response_topic}")

        except json.JSONDecodeError:
            # String Recieved
            
            #print(f"Invalid JSON received {self.client_id}")
            return
    def on_disconnect(self, client, userdata, rc, properties=None):
        print(f"MQTT Disconnected: {rc}")
        showMsg = True

         # AUTO-RECONNECT
        while True:
            try:
                print("Trying reconnect WiFi ...")
                client.reconnect()
                return
            except Exception as e:
                print(f"ERROR: on_disconnect(), {e}")
                time.sleep(2)


# -----------------------------
# Usage example
# -----------------------------
if __name__ == "__main__":
    server = MqttServer(client_id="raspberry_pi_1", broker_ip="192.168.1.50")
    server.connect()
