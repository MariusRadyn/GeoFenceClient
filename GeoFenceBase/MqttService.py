from copyreg import pickle
import json
import os
import time
from queue import Queue
from queue import Full
import MqttCredentials
import paho.mqtt.client as mqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
import Settings as cfg
import MqttCredentials

#client.connect("broker_ip", 1883, properties=props)

BROKER = "0.0.0.0"        
PORT = 1883

#MQTT Topics
MQTT_NAME = "GeoBrokerMqtt"
MQTT_TOPIC_FROM_IOT = "mqtt/from/iot"
MQTT_TOPIC_TO_IOT = "mqtt/to/iot"
MQTT_TOPIC_FROM_ANDROID = "mqtt/from/android"
MQTT_TOPIC_TO_ANDROID = "mqtt/to/android"
MQTT_TOPIC_ANDROID = "mqtt/android"
MQTT_TOPIC_CRED ="mqtt/credentials"

# MQTT Settings
MQTT_SETTING_FROM_DEVICE_ID = "from"
MQTT_SETTING_TO_DEVICE_ID = "to"
MQTT_SETTING_TOPIC = "topic"
MQTT_SETTING_PAYLOAD = "payload"
MQTT_SETTING_OPERATORS_LIST = "operatorsList"
MQTT_SETTING_OPERATORS_VERSION = "operatorsVer"
MQTT_SETTING_CMD = "cmd"
MQTT_SETTING_WHEEL_DISTANCE = "wheel_distance"
MQTT_SETTING_TICKS_PER_M = "ticksPerM"
MQTT_SETTING_IOT_TYPE = "iotType"
MQTT_SETTING_USER_ID = "userId"
MQTT_SETTING_DOC_ID = "docId"
MQTT_SETTING_MQTT_USER = MqttCredentials.MQTT_PAYLOAD_USER
MQTT_SETTING_MQTT_PW = MqttCredentials.MQTT_PAYLOAD_PW

#MQTT Commands
MQTT_CMD_DISCOVERY = "#DISCOVER"
MQTT_CMD_FOUND_MONITOR = "#FOUND_MONITOR"
MQTT_CMD_CALIBRATE = "#CALIBRATE"
MQTT_CMD_CONNECT_MONITOR = "#CONNECT_MONITOR"
MQTT_CMD_CONNECT_BASE = "#CONNECT_BASE"
MQTT_CMD_DISCONNECT_MONITOR = "#DISCONNECT_MONITOR"
MQTT_CMD_PING = "#PING"
MQTT_CMD_FIND = "#FIND"
MQTT_CMD_SETTINGS = "#REQ_SETTINGS"
MQTT_CMD_ACK = "#ACK"
MQTT_CMD_DEVICE_ID = "#DEVICE_ID"
MQTT_CMD_LIVE_MONITOR_DATA = "#MONITOR_DATA"
MQTT_CMD_IOT_DATA = "#IOT_DATA"
MQTT_CMD_TAG_REQ = "#TAG_REQ"
MQTT_CMD_TAG_DATA = "#TAG_DATA"
MQTT_CMD_SYNC = "#SYNC"
MQTT_CMD_OPERATOR_DATA = "#OPERATORS"
MQTT_CMD_NEW_DATA_AVAILABLE = "#NEW_DATA_AVAILABLE"

TAG_REQUESTED_FROM_DEVICE_ID = ""
SYNC_REQUESTED_FROM_DEVICE_ID = ""
SYNC_REQUESTED_FROM_IOT_TYPE = ""

showedReconnectMsg = False
#Debug


class MqttServer:
    
    def __init__(
            self, 
            client_id, 
            broker_ip="0.0.0.0", 
            port=1883,
            mqtt_username=None,
            mqtt_password=None,
            ):
        
        self.client_id = client_id
        self.broker_ip = broker_ip
        self.port = port
        self.mqtt_username = mqtt_username or ""
        self.mqtt_password = mqtt_password or ""
        self.queue = Queue(maxsize=50)
        
        # Example settings (Android can request via #REQ_SETTINGS)
        self.settings = {
            "volume": 80,
            "brightness": 50,
            "mode": "auto",
        }
        self.settings.update(MqttCredentials.get_mqtt_payload_for_role("android"))

        # Create MQTT client
        self.client = mqtt.Client(
            client_id = self.client_id, 
            protocol=mqtt.MQTTv5
            )

        if self.mqtt_username and self.mqtt_password:
            self.client.username_pw_set(self.mqtt_username, self.mqtt_password)
        
        # Bind callbacks
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    # -----------------------------
    # Commands
    # -----------------------------
    async def connectMqtt(self):
        self.printDebug(f"MQTT Connecting {self.broker_ip}:{self.port} ... ", cfg.PRINT_DEBUG_GENERAL)
        if not self.mqtt_username or not self.mqtt_password:
            try:
                import MqttCredentials
                cred_path = MqttCredentials.MQTT_CREDS_FILE
            except Exception:
                cred_path = "~/Secure/mqtt_credentials.json"
            self.printDebug(
                f"ERROR: MQTT credentials missing for user '{self.mqtt_username or '?'}'. "
                f"Expected file: {cred_path}\n"
                "Run as your Pi user (not sudo): python3 MqttCredentials.py --setup",
                cfg.PRINT_DEBUG_ERROR,
            )
            return False

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
        return True
    def decode_payload(self, payload):
        if isinstance(payload, dict):
            self.printDebug(f"DECODE: JSON", cfg.PRINT_DEBUG_GENERAL)
            return payload
        if isinstance(payload, str):
            try:
                self.printDebug(f"DECODE: STRING", cfg.PRINT_DEBUG_GENERAL)
                return json.loads(payload)
            except json.JSONDecodeError:
                return payload
        return payload
    def loop(self):
        self.client.loop()
    def printDebug(self, msg, enabled):
        cfg.printDebug(msg, enabled)
    def _iot_mqtt_payload(self) -> dict:
        return MqttCredentials.get_mqtt_payload_for_role("iot")
    def _android_mqtt_payload(self) -> dict:
        return MqttCredentials.get_mqtt_payload_for_role("android")
    def sendOperators(self, operators_list, operators_version, to_device_id=None):
        global SYNC_REQUESTED_FROM_DEVICE_ID

        # Prefer explicit target (per queued #SYNC). Global is only a legacy fallback
        # and is unsafe when multiple IoTs sync at once after #NEW_DATA_AVAILABLE.
        device_id = (to_device_id or SYNC_REQUESTED_FROM_DEVICE_ID or "").strip()
        if not device_id:
            self.printDebug("No sync requested. Ignoring sync.", cfg.PRINT_DEBUG_GENERAL)
            return
                   
        response_topic = f"{MQTT_TOPIC_TO_IOT}/{device_id}"
        
        payload = {
            MQTT_SETTING_OPERATORS_LIST: operators_list  ,
            MQTT_SETTING_OPERATORS_VERSION: operators_version
        }

        txPayload = {
            MQTT_SETTING_FROM_DEVICE_ID: self.client_id,
            MQTT_SETTING_TOPIC: response_topic,
            MQTT_SETTING_PAYLOAD: payload,
            MQTT_SETTING_CMD: MQTT_CMD_OPERATOR_DATA
        }

        self.client.publish(response_topic, json.dumps(txPayload))
        self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
        if not to_device_id and SYNC_REQUESTED_FROM_DEVICE_ID == device_id:
            SYNC_REQUESTED_FROM_DEVICE_ID = ""
    def broadcastNewDataAvailable(self, iot_type):               
        response_topic = f"{MQTT_TOPIC_TO_IOT}"
        payload = {
             MQTT_SETTING_IOT_TYPE: iot_type 
        }

        txPayload = {
            MQTT_SETTING_FROM_DEVICE_ID: self.client_id,
            MQTT_SETTING_TOPIC: response_topic,
            MQTT_SETTING_PAYLOAD: payload,
            MQTT_SETTING_CMD: MQTT_CMD_NEW_DATA_AVAILABLE
        }

        self.client.publish(response_topic, json.dumps(txPayload))
        self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)  

    # -----------------------------
    # Callbacks
    # -----------------------------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        global TAG_REQUESTED_FROM_DEVICE_ID
        global showedReconnectMsg

        failed = getattr(reason_code, "is_failure", None)
        if failed is True:
            self.printDebug(
                f"ERROR: MQTT connect failed: {reason_code} "
                f"(user={self.mqtt_username!r}). "
                "Check ~/Secure/mqtt_credentials.json matches /etc/mosquitto/passwd — "
                "re-run: python3 MqttCredentials.py --setup",
                cfg.PRINT_DEBUG_ERROR,
            )
            return

        self.printDebug(f"MQTT Connected: {reason_code}", cfg.PRINT_DEBUG_MQTT)
        
        client.subscribe(MQTT_TOPIC_FROM_IOT)
        self.printDebug(f"MQTT Subscribed: {MQTT_TOPIC_FROM_IOT}", cfg.PRINT_DEBUG_MQTT)
 
        client.subscribe(MQTT_TOPIC_FROM_ANDROID)
        self.printDebug(f"MQTT Subscribed: {MQTT_TOPIC_FROM_ANDROID}", cfg.PRINT_DEBUG_MQTT)
        showedReconnectMsg = False
    def on_message(self, client, userdata, msg):
        global TAG_REQUESTED_FROM_DEVICE_ID
        global SYNC_REQUESTED_FROM_DEVICE_ID
        global SYNC_REQUESTED_FROM_IOT_TYPE

        try:
            self.printDebug(f"MQTT RX: {msg.payload.decode()}", cfg.PRINT_MQTT_COMMS)
       
            jsondata = json.loads(msg.payload.decode())
            from_id = jsondata.get(MQTT_SETTING_FROM_DEVICE_ID, "")
            to_id = jsondata.get(MQTT_SETTING_TO_DEVICE_ID, "")
            payload = jsondata.get(MQTT_SETTING_PAYLOAD, "")
            command = jsondata.get(MQTT_SETTING_CMD, "")
            myId = self.client_id
            

            # From Android
            if(msg.topic == MQTT_TOPIC_FROM_ANDROID):
                #android_id = requester_id

                # Discover / Pair - Scan Monitor (Broadcast)
                if(command == MQTT_CMD_DISCOVERY):
                    response_topic = f"{MQTT_TOPIC_TO_IOT}"
                    
                    # Broadcast to ALL IOT
                    # Only IOT that has Pair button pressed will respond
                    # Make sure only 1 IOT is in discover mode at a time
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TO_DEVICE_ID: "",         # Broadcast
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: payload,
                        MQTT_SETTING_CMD : MQTT_CMD_DISCOVERY
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                    # Notify main loop to enter BLE pair mode
                    try:
                        self.queue.put_nowait(jsondata)
                    except Full:
                        self.queue.get_nowait()
                        self.queue.put_nowait(jsondata)
            
                # Found Monitor
                if(command == MQTT_CMD_FOUND_MONITOR):
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"

                    out_payload = payload if isinstance(payload, dict) else {}
                    out_payload.update(self._iot_mqtt_payload())
                    
                    # Build JSON payload
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TO_DEVICE_ID: to_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: out_payload,
                        MQTT_SETTING_CMD:MQTT_CMD_FOUND_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
                    
                # Request Settings
                if(command == MQTT_CMD_SETTINGS):
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{from_id}"
                    settings = dict(self.settings)
                    settings.update(self._android_mqtt_payload())
                    client.publish(response_topic, json.dumps(settings))
                    self.printDebug(f"MQTT TX: {settings} {response_topic}", cfg.PRINT_MQTT_COMMS)
                    # Add BaseStation LIST here

                # Calibrate Monitor
                if(command == MQTT_CMD_CALIBRATE):
                    
                    iot_type = payload[MQTT_SETTING_IOT_TYPE]
                    
                    settings = {
                        MQTT_SETTING_IOT_TYPE: iot_type,
                    }
                    settings.update(self._iot_mqtt_payload())

                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TO_DEVICE_ID: to_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: settings,
                        MQTT_SETTING_CMD:MQTT_CMD_CALIBRATE
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Connect to Monitor
                if(command == MQTT_CMD_CONNECT_MONITOR):
                    
                    iot_type = payload[MQTT_SETTING_IOT_TYPE]
                    ticks = payload[MQTT_SETTING_TICKS_PER_M]
                   
                    settings = {
                        MQTT_SETTING_IOT_TYPE: iot_type,
                        MQTT_SETTING_TICKS_PER_M: ticks,
                    }
                    settings.update(self._iot_mqtt_payload())

                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TO_DEVICE_ID: to_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: settings,
                        MQTT_SETTING_CMD:MQTT_CMD_CONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Disconnect to Monitor
                if(command == MQTT_CMD_DISCONNECT_MONITOR): 
                    
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TO_DEVICE_ID: to_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_CMD:MQTT_CMD_DISCONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Find Monitor (locate: beep + LEDs) — forward to that IoT
                if(command == MQTT_CMD_FIND):
                    # Always print (even without --mqtt) so Find is easy to verify.
                    print(f"FIND from app: to={to_id!r} from={from_id!r}", flush=True)
                    if not to_id:
                        self.printDebug("FIND missing to_id", cfg.PRINT_DEBUG_ERROR)
                    else:
                        response_topic = f"{MQTT_TOPIC_TO_IOT}/{to_id}"
                        out_payload = payload if isinstance(payload, dict) else {}

                        txPayload = {
                            MQTT_SETTING_FROM_DEVICE_ID: from_id,
                            MQTT_SETTING_TO_DEVICE_ID: to_id,
                            MQTT_SETTING_TOPIC: response_topic,
                            MQTT_SETTING_PAYLOAD: out_payload,
                            MQTT_SETTING_CMD: MQTT_CMD_FIND
                        }

                        client.publish(response_topic, json.dumps(txPayload))
                        print(f"FIND → {response_topic}", flush=True)
                        self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # PING (Check if Base is there)
                if(command == MQTT_CMD_PING):         
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{from_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: myId,
                        MQTT_SETTING_TO_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_CMD:MQTT_CMD_PING
                    }

                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Connect Base Station
                #!!!! Payload Has UID for Firestore User !!!!
                if(command == MQTT_CMD_CONNECT_BASE): 
                                    
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{from_id}"

                    out_payload = payload if isinstance(payload, dict) else {}
                    out_payload.update(self._android_mqtt_payload())

                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: myId,
                        MQTT_SETTING_TO_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_CMD:MQTT_CMD_CONNECT_BASE,
                        MQTT_SETTING_PAYLOAD: out_payload
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                    # Start Queue for pushing operator list to firestore
                    try:
                        self.queue.put_nowait(jsondata)
                    except Full:
                        self.queue.get_nowait()   # discard oldest
                        self.queue.put_nowait(jsondata)
    
                # TAG Request 
                if(command == MQTT_CMD_TAG_REQ): 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{from_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: myId,
                        MQTT_SETTING_TO_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_CMD:MQTT_CMD_PING
                    }

                    TAG_REQUESTED_FROM_DEVICE_ID = from_id

                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
            
            # From IOT
            if(msg.topic == MQTT_TOPIC_FROM_IOT):
                 # Pair - Scan Monitor ID 
                if(command == MQTT_CMD_DISCOVERY):
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                    
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_DISCOVERY
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # FROM HERE ONLY PAIRED DEVICES CAN RESPOND
                if not cfg.is_iot_paired(ble_address="", ble_name=from_id):
                    self.printDebug(f"MQTT Block: {from_id} not paired", cfg.PRINT_MQTT_COMMS)
                    
                    # Unpaired IoT PINGing this base → tell it via BLE to stop MQTT
                    if command == MQTT_CMD_PING:
                        try:
                            self.queue.put_nowait({
                                MQTT_SETTING_FROM_DEVICE_ID: from_id,
                                MQTT_SETTING_CMD: cfg.CMD_BLE_SHOESH,
                            })
                        except Full:
                            try:
                                self.queue.get_nowait()
                            except Exception:
                                pass
                            try:
                                self.queue.put_nowait({
                                    MQTT_SETTING_FROM_DEVICE_ID: from_id,
                                    MQTT_SETTING_CMD: cfg.CMD_BLE_SHOESH,
                                })
                            except Full:
                                pass
                    return

                # Rx Device ID (Subscribe Private Topic)
                if(command == MQTT_CMD_DEVICE_ID):
                    topic = f"{MQTT_TOPIC_FROM_IOT}/{from_id}"
                    client.subscribe(topic)
                    self.printDebug(f"Auto Subscribe: {topic}", cfg.PRINT_DEBUG_MQTT)

                # IOT Settings 
                if(command == MQTT_CMD_SETTINGS):
                    self.printDebug(f"MQTT TX: {self.settings} {response_topic}", cfg.PRINT_MQTT_COMMS)
                  
                # ACK 
                if(command == MQTT_CMD_ACK):                     
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_ACK
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
     
                # PING 
                if(command == MQTT_CMD_PING):                     
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{from_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: myId,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_PING
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
     
                # Live Monitor Data
                if(command == MQTT_CMD_LIVE_MONITOR_DATA):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: payload,
                        MQTT_SETTING_CMD:MQTT_CMD_LIVE_MONITOR_DATA
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # IOT Data (Push to Cloud)
                if(command == MQTT_CMD_IOT_DATA):                 
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{from_id}"
                        
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD: MQTT_CMD_ACK
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)    

                    # Queue for processing in main loop (to avoid doing heavy processing in callback)
                    try:
                        self.queue.put_nowait(jsondata) 
                    except Full:
                        self.queue.get_nowait()   # discard oldest
                        self.queue.put_nowait(jsondata)
    
                # Calibrate  (IOT to Android)
                if(command == MQTT_CMD_CALIBRATE):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_CALIBRATE
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Connect  (IOT to Android)
                if(command == MQTT_CMD_CONNECT_MONITOR):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_CONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # DisConnect  (IOT to Android)
                if(command == MQTT_CMD_DISCONNECT_MONITOR):                 
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"
                     
                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD:MQTT_CMD_DISCONNECT_MONITOR
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Find (IOT to Android)
                if(command == MQTT_CMD_FIND):
                    response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{to_id}"

                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: payload if isinstance(payload, dict) else "",
                        MQTT_SETTING_CMD: MQTT_CMD_FIND
                    }

                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)

                # Tag Data (IOT to Android) (Send TAG to enroll Operator)
                if(command == MQTT_CMD_TAG_DATA):    
                    if(TAG_REQUESTED_FROM_DEVICE_ID != ""):

                        response_topic = f"{MQTT_TOPIC_TO_ANDROID}/{TAG_REQUESTED_FROM_DEVICE_ID}"
                        
                        txPayload = {
                            MQTT_SETTING_FROM_DEVICE_ID: from_id,
                            MQTT_SETTING_TOPIC: response_topic,
                            MQTT_SETTING_PAYLOAD: payload,
                            MQTT_SETTING_CMD:MQTT_CMD_TAG_DATA
                        }
            
                        client.publish(response_topic, json.dumps(txPayload))
                        self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)
     
                # Sync 
                if(command == MQTT_CMD_SYNC):    
                    response_topic = f"{MQTT_TOPIC_TO_IOT}/{from_id}"
                    SYNC_REQUESTED_FROM_DEVICE_ID = from_id
                    SYNC_REQUESTED_FROM_IOT_TYPE = payload.get(MQTT_SETTING_IOT_TYPE, "")

                    txPayload = {
                        MQTT_SETTING_FROM_DEVICE_ID: from_id,
                        MQTT_SETTING_TOPIC: response_topic,
                        MQTT_SETTING_PAYLOAD: "",
                        MQTT_SETTING_CMD: MQTT_CMD_SYNC
                    }
        
                    client.publish(response_topic, json.dumps(txPayload))
                    self.printDebug(f"MQTT TX: {txPayload}", cfg.PRINT_MQTT_COMMS)     
    
                    # Queue for processing in main loop (to avoid doing heavy processing in callback)
                    try:
                        self.queue.put_nowait(jsondata) 
                    except Full:
                        self.queue.get_nowait()   # discard oldest
                        self.queue.put_nowait(jsondata)
        
        except Exception as e:
            self.printDebug(f"MQTT Error: {self.client_id}: {e}", cfg.PRINT_DEBUG_ERROR)
            return
        
        except json.JSONDecodeError as e:  
            self.printDebug(f"Invalid JSON received {self.client_id}: {e}", cfg.PRINT_DEBUG_ERROR)
            return
    def on_disconnect(self, client, userdata, rc, properties=None):
        self.printDebug(f"MQTT Disconnected: {rc}", cfg.PRINT_DEBUG_ERROR)
        global showedReconnectMsg

         # AUTO-RECONNECT
        while True:
            try: 
                if(not showedReconnectMsg):
                    self.printDebug("Reconnecting WiFi ...", cfg.PRINT_DEBUG_WIFI)
                    showedReconnectMsg = True
                client.reconnect()
                return
            except Exception as e:
                #self.printDebug(f"ERROR: on_disconnect(), {e}", cfg.PRINT_DEBUG_ERROR)
                time.sleep(2)


# -----------------------------
# Usage example
# -----------------------------
if __name__ == "__main__":
    server = MqttServer(client_id="raspberry_pi_1", broker_ip="192.168.1.50")
    server.connectMqtt()
