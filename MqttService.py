import json
import time
from queue import Queue
from queue import Full
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
MQTT_JSON_FROM_DEVICE_ID = "from"
MQTT_JSON_TO_DEVICE_ID = "to"
MQTT_JSON_TOPIC = "topic"
MQTT_JSON_PAYLOAD = "payload"
MQTT_JSON_CMD = "cmd"
MQTT_JSON_WHEEL_DISTANCE = "wheel_distance"

# JSON Settings
SETTING_JSON_TICKS_PER_M = "ticksPerM"
SETTING_JSON_IOT_TYPE = "iotType"
SETTING_JSON_USER_ID = "userId"
SETTING_JSON_DOC_ID = "docId"

#MQTT Commands
MQTT_CMD_REQ_MONITOR = "#REQ_MONITOR"
MQTT_CMD_FOUND_MONITOR = "#FOUND_MONITOR"
MQTT_CMD_CONNECT_MONITOR = "#CONNECT_MONITOR"
MQTT_CMD_DISCONNECT_MONITOR = "#DISCONNECT_MONITOR"
MQTT_CMD_SETTINGS = "#REQ_SETTINGS"
MQTT_CMD_ACK = "#ACK"
MQTT_CMD_DEVICE_ID = "#DEVICE_ID"
MQTT_CMD_MONITOR_DATA = "#MONITOR_DATA"
MQTT_CMD_MEASURE_DATA = "#MEASURE_DATA"

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
        self.queue = Queue(maxsize=50)
        
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
    def decode_payload(self, payload):
        if isinstance(payload, dict):
            self.printMqttComms(f"DECODE: JSON")
            return payload
        if isinstance(payload, str):
            try:
                self.printMqttComms(f"DECODE: STRING")
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload
        return payload
    def loop(self):
        self.client.loop()
    def printMqttComms(self, msg):
        global PRINT_MQTT_COMMS

        if(PRINT_MQTT_COMMS == True): 
            print(msg + '\n')
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
        self.printMqttComms(f"MQTT RX: {msg.payload.decode()}")

        data = json.loads(msg.payload.decode())
        from_id = data.get(MQTT_JSON_FROM_DEVICE_ID, "")
        to_id = data.get(MQTT_JSON_TO_DEVICE_ID, "")
        payload = data.get(MQTT_JSON_PAYLOAD, "")
        command = data.get(MQTT_JSON_CMD, "")
        
        try:
            # From Android
            if(msg.topic == MQTT_TOPIC_FROM_ANDROID):
                #android_id = requester_id

                # Pair - Scan Monitor (Broadcast)
                if(command == MQTT_CMD_REQ_MONITOR):
                    response_topic = f"{MQTT_TOPIC_TO_IOT}"
                    
                    # Broadcast to ALL IOT
                    # Only IOT that has Pair button pressed will respond
                    # Make sure only 1 IOT is in discover mode at a time
                    
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TO_DEVICE_ID: "",         # Broadcast
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: payload,
                        MQTT_JSON_CMD : MQTT_CMD_REQ_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
            
                # Found Monitor
                if(command == MQTT_CMD_FOUND_MONITOR):
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    # Build JSON payload
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TO_DEVICE_ID: to_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: payload,
                        MQTT_JSON_CMD:MQTT_CMD_FOUND_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
                    
                # Request Settings
                if(command == MQTT_CMD_SETTINGS):
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{from_id}"
                    client.publish(response_topic, json.dumps(self.settings))
                    self.printMqttComms(f"MQTT TX: {self.settings} {response_topic}")
        
                # Connect to Monitor
                if(command == MQTT_CMD_CONNECT_MONITOR):
                    
                    iot_type = payload[SETTING_JSON_IOT_TYPE]
                    ticks = payload[SETTING_JSON_TICKS_PER_M]
                    #userId = payload[SETTING_JSON_USER_ID]
                    #docId = payload[SETTING_JSON_DOC_ID]

                    settings = {
                        SETTING_JSON_IOT_TYPE: iot_type,
                        SETTING_JSON_TICKS_PER_M: ticks,
                        #SETTING_JSON_DOC_ID: docId,
                        #SETTING_JSON_USER_ID: userId,
                    }

                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TO_DEVICE_ID: to_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: settings,
                        MQTT_JSON_CMD:MQTT_CMD_CONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")

                # DisConnect to Monitor
                if(command == MQTT_CMD_DISCONNECT_MONITOR): 
                    
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TO_DEVICE_ID: to_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_CMD:MQTT_CMD_DISCONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")

            # From IOT
            if(msg.topic == MQTT_TOPIC_FROM_IOT):
                #iot_id = requester_id

                # Rx Device ID (Subscribe Private Topic)
                if(command == MQTT_CMD_DEVICE_ID):
                    topic = f"{MQTT_TOPIC_FROM_IOT}/{from_id}"
                    client.subscribe(topic)
                    self.printMqttInfo(f"Auto Subscribe: {topic}")

                # IOT Settings 
                if(command == MQTT_CMD_SETTINGS):
                    self.printMqttComms(f"MQTT TX: {self.settings} {response_topic}")

                # Pair - Scan Monitor ID 
                if(command == MQTT_CMD_REQ_MONITOR):
                    recievedMonitor = True
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                    
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: "",
                        MQTT_JSON_CMD:MQTT_CMD_REQ_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
                  
                    # if(showAdvertiseMsg):
                    #     showAdvertiseMsg = False
                    #     self.printMqttInfo(f"iOT Advertising: {iot_id}")
                
                # ACK 
                if(command == MQTT_CMD_ACK):                     
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: "",
                        MQTT_JSON_CMD:MQTT_CMD_ACK
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")
     
                # IOT Data 
                if(command == MQTT_CMD_MONITOR_DATA):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: payload,
                        MQTT_JSON_CMD:MQTT_CMD_MONITOR_DATA
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")

                # Measurement Data (Push to Cloud)
                if(command == MQTT_CMD_MEASURE_DATA):                 
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{from_id}"
                    
                    try:
                        self.queue.put_nowait(payload)
                    except Full:
                        self.queue.get_nowait()   # discard oldest
                        self.queue.put_nowait(payload)
    
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: "",
                        MQTT_JSON_CMD: MQTT_CMD_ACK
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")        

                # Connect  (IOT to Android)
                if(command == MQTT_CMD_CONNECT_MONITOR):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: "",
                        MQTT_JSON_CMD:MQTT_CMD_CONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")

                # DisConnect  (IOT to Android)
                if(command == MQTT_CMD_DISCONNECT_MONITOR):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_JSON_FROM_DEVICE_ID: from_id,
                        MQTT_JSON_TOPIC: response_topic,
                        MQTT_JSON_PAYLOAD: "",
                        MQTT_JSON_CMD:MQTT_CMD_DISCONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printMqttComms(f"MQTT TX: {txPayload}")


        except json.JSONDecodeError:  
            print(f"Invalid JSON received {self.client_id}")
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
