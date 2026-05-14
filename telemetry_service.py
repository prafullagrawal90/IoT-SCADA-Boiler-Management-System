import paho.mqtt.client as mqtt
import json
import time
import socket
import threading
import atexit
import os
import signal
from ssh_manager import connect_raspberry_pi


TOPIC = "boiler/sensors"
MQTT_PORT = 1883
MAX_SENSOR_RESTARTS = 3


class MQTTMonitor:

    def __init__(self, log_callback=None, data_callback=None):

        self.log = log_callback
        self.data_callback = data_callback

        self.last_heartbeat = 0
        self.last_sequence = None
        self.last_sequence_time = time.time()

        self.sensor_data = {}
        self.running = False

        self.ssh = None
        self.ip = None

        self.sensor_restart_count = 0

        # telemetry rate measurement
        self.packet_counter = 0
        self.rate_timer = time.time()
        self.telemetry_rate = 0.0

        # recovery cooldown (prevents restart storms)
        self.last_recovery = 0
        
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        self.ignore_first_packet = True  # user option

    # --------------------------------------------------
    # LOGGING
    # --------------------------------------------------
    
    def log_event(self, msg):

        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"

        print(line)

        # only send to GUI if it still exists
        try:
            if self.log and self.running:
                self.log(line)
        except:
            pass
        
    # --------------------------------------------------
    # NETWORK CHECK
    # --------------------------------------------------

    def port_open(self, ip, port):

        try:
            s = socket.create_connection((ip, port), 1)
            s.close()
            return True
        except:
            return False

    # --------------------------------------------------
    # MOSQUITTO CONTROL
    # --------------------------------------------------

    def ensure_mqtt_broker(self):

        max_retries = 5

        for attempt in range(max_retries):

            try:
                # 🔍 Check status
                stdin, stdout, stderr = self.ssh.exec_command(
                    "systemctl is-active mosquitto"
                )
                state = stdout.read().decode().strip()

                if state == "active":
                    self.log_event("Mosquitto running")
                    return True

                # 🚀 Try to start
                self.log_event(f"Starting Mosquitto (attempt {attempt+1})")

                self.ssh.exec_command("sudo systemctl start mosquitto")

                time.sleep(2)

            except Exception as e:
                self.log_event(f"Mosquitto check error: {e}")

        # ❌ Final failure
        self.log_event("❌ Mosquitto failed to start after retries")
        return False

    # --------------------------------------------------
    # SENSOR PROCESS CONTROL
    # --------------------------------------------------

    def sensors_running(self):

        stdin, stdout, stderr = self.ssh.exec_command(
            "pgrep -f 'python3.*Sensors.py'"
        )

        return stdout.read().decode().strip() != ""

    def start_sensors(self):

        self.log_event("Killing ALL sensor instances...")

        self.ssh.exec_command("pkill -9 -f Sensors.py")
        time.sleep(2)

        self.log_event("Starting Sensors.py (fresh)")

        self.ssh.exec_command(
            "nohup python3 ~/Desktop/BOILER_MNGT_SYS/Sensors.py > /dev/null 2>&1 &"
        )

        time.sleep(3)

    def handle_signal(self, signum, frame):
        self.log_event("Shutdown signal received")
        self.shutdown()
        exit(0)
        
    def restart_sensors(self):

        self.sensor_restart_count += 1

        self.log_event(
            f"Restarting Sensors (attempt {self.sensor_restart_count})"
        )

        self.ssh.exec_command("pkill -f Sensors.py")

        time.sleep(2)

        self.start_sensors()

    # --------------------------------------------------
    # ARDUINO RESET
    # --------------------------------------------------

    def reset_arduino(self):

        self.log_event("⚠ Resetting Arduino via USB")

        self.ssh.exec_command("stty -F /dev/boiler_arduino hupcl")

        time.sleep(3)

        self.sensor_restart_count = 0

        self.restart_sensors()

    # --------------------------------------------------
    # MQTT CALLBACKS
    # --------------------------------------------------

    def on_connect(self, client, userdata, flags, rc, properties=None):

        if rc == 0:

            self.log_event("MQTT Connected")

            client.subscribe(TOPIC)

        else:

            self.log_event(f"MQTT connection failed: {rc}")

    def on_message(self, client, userdata, msg):

        try:
            data = json.loads(msg.payload.decode())

            print(json.dumps(data, indent=2))

            self.last_heartbeat = data.get("heartbeat", 0)

            arduino = data.get("arduino")

            if arduino:

                seq = arduino.get("sequence")

                if seq is not None:

                    print(f"[DEBUG] seq={seq} last={self.last_sequence}")

                    # 🔥 FIRST PACKET HANDLING
                    if self.last_sequence is None:
                        self.last_sequence = seq
                        self.last_sequence_time = time.time()

                        if self.ignore_first_packet:
                            return  # ignore first packet

                    # 🔥 DUPLICATE PACKET
                    if seq == self.last_sequence:
                        print("⚠ DUPLICATE PACKET")
                        return

                    # 🔥 VALID NEW PACKET
                    self.packet_counter += 1
                    self.last_sequence_time = time.time()

                    # 🔥 JUMP DETECTION
                    jump = seq - self.last_sequence
                    if jump > 20:
                        self.log_event(f"⚠ Large packet jump detected ({jump})")

                    # 🔥 UPDATE SEQUENCE
                    self.last_sequence = seq

                    # 🔥 TELEMETRY RATE CALCULATION
                    if time.time() - self.rate_timer >= 5:

                        self.telemetry_rate = self.packet_counter / 5.0

                        self.log_event(
                            f"Telemetry rate: {self.telemetry_rate:.2f} Hz"
                        )

                        if self.telemetry_rate < 0.5:
                            self.log_event("⚠ Serial telemetry rate low")

                        self.packet_counter = 0
                        self.rate_timer = time.time()

            self.sensor_data = data

            # 🔥 HEARTBEAT FILE UPDATE
            try:
                os.utime("telemetry_heartbeat.txt", None)
            except FileNotFoundError:
                open("telemetry_heartbeat.txt", "w").close()

            # 🔥 CALLBACK
            if self.data_callback:
                self.data_callback(data)

        except Exception as e:
            self.log_event(f"Invalid payload: {e}")
            
                    
    def on_disconnect(self, client, userdata, flags, rc, properties=None):

        if rc != 0:
            self.log_event("⚠ MQTT connection lost")
        else:
            self.log_event("MQTT disconnected")

    # --------------------------------------------------
    # WATCHDOG
    # --------------------------------------------------

    def watchdog(self):

        while self.running:

            time.sleep(2)

            if not self.port_open(self.ip, MQTT_PORT):

                self.log_event("MQTT broker unreachable")

                self.ssh.exec_command("sudo systemctl restart mosquitto")

                time.sleep(3)

                continue

            if self.last_heartbeat == 0:

                self.log_event("Waiting for telemetry heartbeat")

                continue

            heartbeat_delay = time.time() - self.last_heartbeat
            sequence_delay = time.time() - self.last_sequence_time

            if heartbeat_delay > 6 and sequence_delay > 6:

                # prevent restart storm
                if time.time() - self.last_recovery < 15:
                    continue

                self.last_recovery = time.time()

                self.log_event("⚠ Telemetry stalled")

                if self.sensor_restart_count >= MAX_SENSOR_RESTARTS:
                    self.reset_arduino()
                else:
                    self.restart_sensors()

                continue

            self.sensor_restart_count = 0

            self.log_event(
                f"System healthy | Telemetry {self.telemetry_rate:.2f} Hz"
            )

    # --------------------------------------------------
    # SHUTDOWN
    # --------------------------------------------------

    def shutdown(self):

        self.log_event("Stopping telemetry service")

        try:
            if self.ssh:

                self.log_event("Stopping Sensors.py on Raspberry Pi")
                self.ssh.exec_command("pkill -f Sensors.py")

                time.sleep(1)

                self.log_event("Stopping mjpeg_server.py on Raspberry Pi")
                self.ssh.exec_command("pkill -f mjpeg_server.py")

                time.sleep(1)

                self.log_event("Stopping camera pipeline")
                self.ssh.exec_command("pkill -f rpicam")

                time.sleep(1)

                self.log_event("Stopping mosquitto broker")
                self.ssh.exec_command("sudo systemctl stop mosquitto")

                time.sleep(1)

                self.log_event("Closing SSH connection")
                self.ssh.close()

        except Exception as e:
            print("Shutdown error:", e)

        try:
            self.client.loop_stop()
        except:
            pass

        self.running = False


    # --------------------------------------------------
    # START
    # --------------------------------------------------

    def start(self):

        self.running = True

        # register signal handlers first
        signal.signal(signal.SIGINT, self.handle_signal)
        signal.signal(signal.SIGTERM, self.handle_signal)

        atexit.register(self.shutdown)

        self.ssh, self.ip = connect_raspberry_pi()

        self.log_event(f"SSH connected to {self.ip}")

        if not self.ensure_mqtt_broker():
            self.log_event("❌ Cannot proceed without MQTT broker")
            return

        self.start_sensors()

        self.client.connect(self.ip, MQTT_PORT, 60)
        self.client.loop_start()

        threading.Thread(target=self.watchdog, daemon=True).start()

    # --------------------------------------------------


def supervisor():

    monitor = MQTTMonitor()

    try:

        monitor.start()

        while True:
            time.sleep(1)

    except KeyboardInterrupt:

        monitor.log_event("Ctrl+C detected")

        monitor.shutdown()


if __name__ == "__main__":
    supervisor()