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

#MQTT Topics
MQTT_NAME = "GeoBrokerMqtt"
MQTT_TOPIC_FROM_IOT = "mqtt/from/iot"
MQTT_TOPIC_TO_IOT = "mqtt/to/iot"
MQTT_TOPIC_FROM_ANDROID = "mqtt/from/android"
MQTT_TOPIC_TO_ANDROID = "mqtt/to/android"
MQTT_TOPIC_ANDROID = "mqtt/android"
MQTT_TOPIC_CRED ="mqtt/credentials"

# MQTT JSON
MQTT_JSON_DEVICE_ID = "device_id"
MQTT_JSON_TOPIC = "topic"
MQTT_JSON_PAYLOAD = "payload"
MQTT_JSON_CMD = "command"

#MQTT Commands
MQTT_CMD_REQ_MONITOR = "#REQ_MONITOR"
MQTT_CMD_FOUND_MONITOR = "#FOUND_MONITOR"
MQTT_CMD_SETTINGS = "#REQ_SETTINGS"
MQTT_CMD_ACK = "#ACK"
MQTT_CMD_DEVICE_ID = "#DEVICE_ID"

recievedMonitor = False
showAdvertiseMsg = True
android_id = ''
iot_id = ''

#Debug
PRINT_MQTT_COMMS = True
PRINT_MQTT_INFO = True

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
    # Commands
    # -----------------------------
    async def connectMqtt(self):
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

    def printMqttComms(self, msg):
        global PRINT_MQTT_COMMS

        if(PRINT_MQTT_COMMS == True): 
            print(msg)

    def printMqttInfo(self, msg):
        global PRINT_MQTT_INFO

        if(PRINT_MQTT_INFO == True): 
            print(msg)
        
    # -----------------------------
    # Callbacks
    # -----------------------------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        #print(f"[{self.client_id}] Connected to broker: {reason_code}")
        self.printMqttInfo(f"MQTT Connected: {reason_code}")
        
        client.subscribe(MQTT_TOPIC_FROM_IOT)
        self.printMqttInfo(f"MQTT Subscribed: {MQTT_TOPIC_FROM_IOT}")
 
        client.subscribe(MQTT_TOPIC_FROM_ANDROID)
        self.printMqttInfo(f"MQTT Subscribed: {MQTT_TOPIC_FROM_ANDROID}")
    def on_message(self, client, userdata, msg):
        global recievedMonitor
        global showAdvertiseMsg
        global android_id
        global iot_id
        
        self.printMqttComms(f"MQTT RX: {msg.payload.decode()}")

        data = json.loads(msg.payload.decode())
        requester_id = data.get(MQTT_JSON_DEVICE_ID, "default")
        payload = data.get(MQTT_JSON_PAYLOAD, "default")
        command = data.get(MQTT_JSON_CMD, "default")
        
        try:
            # From Android
            if(msg.topic == MQTT_TOPIC_FROM_ANDROID):
                android_id = requester_id

                # Scan Monitor
                if(command == MQTT_CMD_REQ_MONITOR):
                    #Pass message to IOT
                    
                    #if (recievedMonitor == False):
                    #    self.printMqttInfo(f"Android Scan Monitor .... Found: None")
                    #else:

                    # Broadcast to ALL IOT
                    # Only IOT that has Pair button pressed will respond
                    # Make sure ony 1 IOT is in discover mode at a time
                    response_topic = f"{MQTT_TOPIC_TO_IOT}"
                    
                    # Build JSON payload
                    txPayload = {
                        MQTT_JSON_DEVICE_ID: requester_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: iot_id,
                        MQTT_JSON_CMD:MQTT_CMD_REQ_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
                    #self.printMqttInfo(f"Android Scan Monitor .... Found: {monitorName}")
                    self.printMqttInfo(f"Android to IOT: Request Monitors")
            
                # Found Monitor
                if(command == MQTT_CMD_FOUND_MONITOR):
                    recievedMonitor == False   
                    showAdvertiseMsg = True
                    iot_id = payload    
                    response_topic = f"{MQTT_TOPIC_TO_IOT}"
                    
                    # Build JSON payload
                    txPayload = {
                        MQTT_JSON_DEVICE_ID: requester_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: iot_id,
                        MQTT_JSON_CMD:MQTT_CMD_FOUND_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
                    self.printMqttInfo(f"Android to IOT: Found Monitor: {iot_id}")
                    
                # Request Settings
                if(command == MQTT_CMD_SETTINGS):
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{requester_id}"
                    client.publish(response_topic, json.dumps(self.settings))
                    self.printMqttComms(f"MQTT TX: {self.settings} {response_topic}")
        
            # From IOT
            if(msg.topic == MQTT_TOPIC_FROM_IOT):
                iot_id = requester_id

                # Rx Device ID (Subscribe Private Topic)
                if(command == MQTT_CMD_DEVICE_ID):
                    topic = f"{MQTT_TOPIC_FROM_IOT}/{requester_id}"
                    client.subscribe(topic)
                    self.printMqttInfo(f"Auto Subscribe: {topic}")

                # Rx Settings 
                if(command == MQTT_CMD_SETTINGS):
                    self.printMqttComms(f"MQTT TX: {self.settings} {response_topic}")

                # Rx Monitor ID 
                if(command == MQTT_CMD_REQ_MONITOR):
                    recievedMonitor = True
                    iot_id = payload
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{android_id}"
                    
                    # Build JSON payload
                    txPayload = {
                        MQTT_JSON_DEVICE_ID: requester_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: iot_id,
                        MQTT_JSON_CMD:MQTT_CMD_REQ_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
                    self.printMqttInfo(f"IOT to Android: iOT ID: {iot_id}")
                  
                
                    if(showAdvertiseMsg):
                        showAdvertiseMsg = False
                        self.printMqttInfo(f"iOT Advertising: {iot_id}")
                
                # iOT Monitor Found 
                if(command == MQTT_CMD_FOUND_MONITOR):                 
                    iot_id = payload
                    showAdvertiseMsg = True
                    self.printMqttInfo(f"iOT Monitor Found: {iot_id}")
     
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
    server.connectMqtt()
