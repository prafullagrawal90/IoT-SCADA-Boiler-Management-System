import json
import time
import subprocess
import threading
import paho.mqtt.client as mqtt
from OutputController import OutputController

BROKER = "localhost"
PORT = 1883
CMD_TOPIC = "boiler/command"
SENSOR_TOPIC = "boiler/sensors"

# GLOBAL STATE TRACKING
last_state = {"heater": None, "motor": None, "override": None}
mqtt_connected = False


# ------------------------------------------------
# MOSQUITTO MANAGEMENT
# ------------------------------------------------
def ensure_mosquitto():
    try:
        status = subprocess.run(
            ["systemctl", "is-active", "mosquitto"],
            capture_output=True,
            text=True
        ).stdout.strip()

        if status == "active":
            print("[MQTT] Broker running")
            return True

        print("[MQTT] Starting broker...")
        subprocess.run(["sudo", "systemctl", "start", "mosquitto"])
        time.sleep(2)

        return True

    except Exception as e:
        print("[MQTT ERROR]", e)
        return False


# ------------------------------------------------
# SENSORS MANAGEMENT
# ------------------------------------------------
def ensure_sensors():
    try:
        result = subprocess.run(
            ["pgrep", "-f", "Sensors.py"],
            capture_output=True,
            text=True
        )

        if result.stdout.strip():
            print("[SENSORS] Running")
            return True

        print("[SENSORS] Starting Sensors.py...")

        subprocess.Popen(
            ["python3", "Sensors.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        time.sleep(3)
        return True

    except Exception as e:
        print("[SENSORS ERROR]", e)
        return False


# ------------------------------------------------
# MQTT SETUP
# ------------------------------------------------
def setup_mqtt():

    client = mqtt.Client()

    def on_connect(client, userdata, flags, rc):
        global mqtt_connected

        if rc == 0:
            mqtt_connected = True
            print("[MQTT] Connected")
            client.subscribe(SENSOR_TOPIC)
        else:
            print("[MQTT] Connection failed:", rc)

    def on_disconnect(client, userdata, rc):
        global mqtt_connected
        mqtt_connected = False
        print("[MQTT] Disconnected → reconnecting...")

    def on_message(client, userdata, msg):
        global last_state

        try:
            data = json.loads(msg.payload.decode())
            actuator = data.get("actuator", {})

            # print only when changed
            if actuator != last_state:
                print("\n📡 STATE UPDATE:",
                      f"Heater={actuator.get('heater')}",
                      f"Motor={actuator.get('motor')}",
                      f"Override={actuator.get('override')}")
                last_state = actuator

        except Exception:
            pass

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    client.reconnect_delay_set(min_delay=1, max_delay=5)

    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start()
        return client

    except Exception as e:
        print("[MQTT ERROR]", e)
        return None


# ------------------------------------------------
# COMMAND EXECUTION
# ------------------------------------------------
def send_command(mode, ctrl, client, cmd):

    if mode == "1":
        result = ctrl.execute(cmd)
        print("[LOCAL RESULT]", result)

    elif mode == "2":

        if not mqtt_connected:
            print("⚠ MQTT not connected")
            return

        payload = {"cmd": cmd, "time": time.time()}

        try:
            client.publish(CMD_TOPIC, json.dumps(payload), qos=1)
            print("[MQTT SENT]", payload)
        except Exception as e:
            print("[MQTT SEND ERROR]", e)

    elif mode == "3":
        result = ctrl.execute(cmd)
        print("[SSH RESULT]", result)


# ------------------------------------------------
# HEALTH MONITOR
# ------------------------------------------------
def health_monitor():

    while True:
        try:
            status = subprocess.run(
                ["systemctl", "is-active", "mosquitto"],
                capture_output=True,
                text=True
            ).stdout.strip()

            if status != "active":
                print("\n[HEALTH] Restarting Mosquitto...")
                subprocess.run(["sudo", "systemctl", "start", "mosquitto"])

        except:
            pass

        time.sleep(5)


# ------------------------------------------------
# STATE READER (FIXED)
# ------------------------------------------------
def read_actual_state():
    try:
        with open("outputs_state.json", "r") as f:
            return json.load(f)
    except:
        return {"error": "cannot read state"}


# ------------------------------------------------
# MAIN
# ------------------------------------------------
def main():

    ctrl = OutputController()

    print("\nSelect mode:")
    print("1 → LOCAL(UNSTABLE, PLZ USE ONLY WHEN ALL OTHER COMM IS OFF")
    print("2 → MQTT (GUI-like)")
    print("3 → SSH")

    mode = input("Enter mode: ").strip()

    client = None

    if mode == "2":

        if not ensure_mosquitto():
            return

        if not ensure_sensors():
            return

        client = setup_mqtt()

        if client is None:
            return

        threading.Thread(target=health_monitor, daemon=True).start()

    print("\n=== CONTROL PANEL ===")

    mapping = {
        "1": "heater_on",
        "2": "heater_off",
        "3": "motor_on",
        "4": "motor_off",
        "5": "emergency",
        "6": "reset_override"
    }

    while True:

        try:
            print("\n----------------------")
            print("1 → Heater ON")
            print("2 → Heater OFF")
            print("3 → Motor ON")
            print("4 → Motor OFF")
            print("5 → Emergency (LOCK)")
            print("6 → Reset Override")
            print("7 → Show State")
            print("0 → Exit")

            choice = input("Enter: ").strip()

            if choice == "0":
                break

            elif choice == "7":
                print(read_actual_state())  # ✅ FIXED

            elif choice in mapping:
                send_command(mode, ctrl, client, mapping[choice])

            else:
                print("Invalid input")

        except KeyboardInterrupt:
            print("\nExiting...")
            break

        except Exception as e:
            print("[ERROR]", e)


# ------------------------------------------------

if __name__ == "__main__":
    main()
