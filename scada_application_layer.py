import json
import time
import requests
import cv2
import numpy as np
import paho.mqtt.client as mqtt
from threading import Thread, Lock

class ScadaApplication:
    """
    SCADA Application Layer API
    Provides a clean interface for accessing boiler telemetry, stats, and camera stream.
    Can be used by GUI or other automated scripts.

    enable_stream: Set False for consumers that don't need the camera frame feed
                  (e.g. web server). This avoids opening a competing TCP connection
                  to the Pi that would steal bandwidth/CPU from the GUI's real-time stream.
    """
    def __init__(self, enable_stream=True):
        self.broker = self._load_pi_ip()
        while not self.broker:
            print("[APP] Waiting for Raspberry Pi IP from controller_config.json...")
            time.sleep(2)
            self.broker = self._load_pi_ip()

        self.enable_stream = enable_stream

        self.mqtt_topic = "boiler/sensors"
        self.cmd_topic = "boiler/command"
        self.stats_url = f"http://{self.broker}:8080/stats"
        self.stream_url = f"http://{self.broker}:8080"

        self.latest_data = {}
        self.latest_stats = {}
        self.processed_state = {
            "telemetry": {},
            "alarms": {
                "low_level": False,
                "high_level": False,
                "high_pressure": False,
                "any": False
            },
            "system": {
                "mqtt_status": "DISCONNECTED",
                "arduino_status": "DISCONNECTED",
                "sensor_valid": False
            },
            "actuators": {
                "heater": False,
                "motor": False,
                "override": False
            },
            "heartbeat_age": 0
        }
        self.data_lock = Lock()
        
        self.running = True
        
        # MQTT Client
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        
        # Callbacks for data updates
        self.data_callbacks = []
        self.stats_callbacks = []
        self.frame_callbacks = []

        # Start background threads
        self.mqtt_thread = Thread(target=self._mqtt_loop, daemon=True)
        self.stats_thread = Thread(target=self._stats_loop, daemon=True)

        self.mqtt_thread.start()
        self.stats_thread.start()

        # Only start camera stream if caller needs it (GUI yes, web server no)
        if self.enable_stream:
            self.stream_thread = Thread(target=self._stream_loop, daemon=True)
            self.stream_thread.start()
        else:
            self.stream_thread = None
            print("[APP] Stream thread disabled (enable_stream=False)")

    def _load_pi_ip(self):
        try:
            with open("controller_config.json") as f:
                cfg = json.load(f)
            return cfg.get("last_known_ip")
        except:
            return None

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[APP] Connected to MQTT Broker at {self.broker}")
            client.subscribe(self.mqtt_topic)
            with self.data_lock:
                self.processed_state["system"]["mqtt_status"] = "CONNECTED"
            self._notify_data(self.get_latest_data())

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            self._process_payload(payload)
            self._notify_data(self.get_latest_data())
        except Exception as e:
            print(f"[APP] MQTT Message Error: {e}")

    def _process_payload(self, payload):
        with self.data_lock:
            self.latest_data = payload
            
            # Guard against explicit `null` values in payload (e.g. during sensor restart).
            # .get("key", {}) only uses the default when the key is ABSENT.
            # When the Pi sends "arduino": null, .get() returns None — so we need `or {}`.
            arduino  = payload.get("arduino")  or {}
            dht      = payload.get("dht")      or {}
            system   = payload.get("system")   or {}
            actuator = payload.get("actuator") or {}
            
            raw_distance = arduino.get("distance")
            pressure = arduino.get("pressure", 0)
            
            # Update telemetry
            telemetry = {
                "distance": raw_distance,
                "pressure": round(pressure, 2),
                "boiler_temp": round(dht.get("boiler_temp", 0), 2),
                "room_temp": round(dht.get("room_temp", 0), 2),
                "boiler_hum": dht.get("boiler_hum", "--"),
                "room_hum": dht.get("room_hum", "--"),
            }
            
            # Logic Processing
            sensor_valid = raw_distance is not None and raw_distance > 0
            level = 0
            if sensor_valid:
                level = max(0, min(150 - raw_distance, 150))
            
            telemetry["water_level"] = round(level, 2)
            
            # Alarms
            low_level_alarm = sensor_valid and raw_distance > 120
            high_level_alarm = sensor_valid and raw_distance < 20
            high_pressure_alarm = pressure > 9
            low_pressure_alarm = pressure < 1
            
            alarms = {
                "low_level": low_level_alarm,
                "high_level": high_level_alarm,
                "high_pressure": high_pressure_alarm,
                "low_pressure": low_pressure_alarm,
                "any": low_level_alarm or high_level_alarm or high_pressure_alarm or low_pressure_alarm
            }
            
            # System Status
            sys_status = {
                "mqtt_status": self.processed_state["system"]["mqtt_status"],
                "arduino_status": system.get("arduino_status", "DISCONNECTED"),
                "sensor_valid": sensor_valid,
                "dht_status": system.get("dht_status", "--")
            }
            
            # Actuators
            actuators = {
                "heater": actuator.get("heater", False),
                "motor": actuator.get("motor", False),
                "override": actuator.get("override", False)
            }
            
            # Heartbeat (Absolute timestamp from Pi or local arrival time)
            heartbeat_timestamp = payload.get("heartbeat", time.time())
            # heartbeat_age: seconds since last Pi packet — this is what the GUI displays
            heartbeat_age = f"{round(time.time() - heartbeat_timestamp, 1)}s ago"

            self.processed_state = {
                "telemetry": telemetry,
                "alarms": alarms,
                "system": sys_status,
                "actuators": actuators,
                "heartbeat_timestamp": heartbeat_timestamp,
                "heartbeat_age": heartbeat_age,
                "raw": payload # Keep raw for compatibility if needed
            }

    def _mqtt_loop(self):
        while self.running:
            try:
                self.client.connect(self.broker, 1883, 60)
                self.client.loop_forever()
            except Exception as e:
                with self.data_lock:
                    self.processed_state["system"]["mqtt_status"] = "DISCONNECTED"
                print(f"[APP] MQTT Connection Error: {e}")
                time.sleep(3)

    def _stats_loop(self):
        while self.running:
            try:
                r = requests.get(self.stats_url, timeout=2)
                if r.status_code == 200:
                    stats = r.json()
                    with self.data_lock:
                        self.latest_stats = stats
                    self._notify_stats(stats)
            except:
                pass
            time.sleep(2)

    def _stream_loop(self):
        while self.running:
            try:
                stream = requests.get(self.stream_url, stream=True, timeout=(3, 5))
                if stream.status_code != 200:
                    time.sleep(1)
                    continue

                buffer = bytearray()
                for chunk in stream.iter_content(4096):
                    if not self.running:
                        return
                    buffer.extend(chunk)
                    start = buffer.find(b'\xff\xd8')
                    end = buffer.find(b'\xff\xd9')

                    if start != -1 and end != -1:
                        jpg = buffer[start:end+2]
                        buffer = buffer[end+2:]
                        frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if frame is not None:
                            self._notify_frame(frame)
            except Exception as e:
                # print(f"[APP] Stream Error: {e}")
                time.sleep(1)

    # Callback Registration
    def on_data(self, callback):
        self.data_callbacks.append(callback)

    def on_stats(self, callback):
        self.stats_callbacks.append(callback)

    def on_frame(self, callback):
        self.frame_callbacks.append(callback)

    def _notify_data(self, data):
        for cb in self.data_callbacks:
            try: cb(data)
            except: pass

    def _notify_stats(self, stats):
        for cb in self.stats_callbacks:
            try: cb(stats)
            except: pass

    def _notify_frame(self, frame):
        for cb in self.frame_callbacks:
            try: cb(frame)
            except: pass

    # API Methods
    def get_latest_data(self):
        """Returns the processed state in a defined format."""
        with self.data_lock:
            return self.processed_state.copy()

    def get_latest_stats(self):
        with self.data_lock:
            return self.latest_stats.copy()

    def send_command(self, cmd):
        """Send a command to the boiler (e.g., 'heater_on', 'motor_off', 'emergency')"""
        payload = {"cmd": cmd, "time": time.time()}
        try:
            self.client.publish(self.cmd_topic, json.dumps(payload))
            return True
        except Exception as e:
            print(f"[APP] Error sending command: {e}")
            return False

    def shutdown(self):
        self.running = False
        self.client.disconnect()

if __name__ == "__main__":
    # Example usage / Test
    app = ScadaApplication()
    print("SCADA App Layer started. Press Ctrl+C to stop.")
    try:
        while True:
            state = app.get_latest_data()
            if state and state.get("telemetry"):
                print(f"Level: {state['telemetry'].get('water_level')} cm | Alarms: {state['alarms']['any']}")
            time.sleep(2)
    except KeyboardInterrupt:
        app.shutdown()
