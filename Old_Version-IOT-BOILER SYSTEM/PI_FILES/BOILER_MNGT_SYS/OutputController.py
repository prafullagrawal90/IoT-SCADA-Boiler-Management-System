import RPi.GPIO as GPIO
import json
import os
import time
import paho.mqtt.client as mqtt

STATE_FILE = "outputs_state.json"

HEATER_PIN = 27
MOTOR_PIN = 22

ACTUATOR_TOPIC = "boiler/actuator"

MAX_CMD_AGE = 10   # seconds


class OutputController:

    def __init__(self, log_callback=None):

        self.log = log_callback if log_callback else print

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        GPIO.setup(HEATER_PIN, GPIO.OUT)
        GPIO.setup(MOTOR_PIN, GPIO.OUT)

        self.state = {
            "heater": False,
            "motor": False,
            "override": None
        }

        self.load_state()

        # prevent booting stuck in emergency
        self.state["override"] = None

        self.apply_state()

        # MQTT publisher
        self.mqtt = mqtt.Client()
        self.mqtt.connect("localhost",1883,60)

    # ------------------------------------------------

    def load_state(self):

        if os.path.exists(STATE_FILE):

            try:
                with open(STATE_FILE, "r") as f:
                    self.state = json.load(f)

                self.log("[OUTPUT] State restored")

            except Exception as e:
                self.log(f"[OUTPUT] Load error: {e}")

    # ------------------------------------------------

    def save_state(self):

        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f)

        except Exception as e:
            self.log(f"[OUTPUT] Save error: {e}")

    # ------------------------------------------------

    def publish_state(self):

        try:

            self.mqtt.publish(
                ACTUATOR_TOPIC,
                json.dumps(self.state),
                retain=True
            )

        except Exception as e:
            self.log("[MQTT] publish failed", e)

    # ------------------------------------------------

    def apply_state(self):

        GPIO.output(HEATER_PIN, self.state["heater"])
        GPIO.output(MOTOR_PIN, self.state["motor"])

        self.publish_state()

    # ------------------------------------------------
    # COMMAND DISPATCH
    # ------------------------------------------------

    def process_command(self, cmd):

        command = cmd.get("cmd")
        timestamp = cmd.get("time")

        if not command:
            return

        if timestamp and abs(time.time() - timestamp) > MAX_CMD_AGE:
            self.log("[CMD] stale command ignored")
            return

        # ------------------------------------------------
        # RESET OVERRIDE (ALWAYS ALLOWED)
        # ------------------------------------------------

        if command == "reset_override":

            self.log("[CMD] clearing override")

            self.state["override"] = None

            self.apply_state()
            self.save_state()

            return

        # ------------------------------------------------
        # BLOCK COMMANDS DURING EMERGENCY
        # ------------------------------------------------

        if self.state["override"] == "ssh":
            self.log("[CMD] ignored (SSH override)")
            return

        # ------------------------------------------------

        if command == "heater_on":
            self.state["heater"] = True

        elif command == "heater_off":
            self.state["heater"] = False

        elif command == "motor_on":
            self.state["motor"] = True

        elif command == "motor_off":
            self.state["motor"] = False

        elif command == "emergency":
            self.ssh_emergency()
            return

        else:
            self.log("[CMD] unknown command")
            return

        self.apply_state()
        self.save_state()

    # ------------------------------------------------
    # SSH EMERGENCY
    # ------------------------------------------------

    def ssh_emergency(self):

        self.state["heater"] = False
        self.state["motor"] = False
        self.state["override"] = "ssh"

        self.apply_state()
        self.save_state()

        self.log("[SSH] EMERGENCY MODE")

    # ------------------------------------------------

    def clear_override(self):

        self.state["override"] = None
        self.save_state()

    # ------------------------------------------------

    def shutdown(self):

        GPIO.cleanup()
        self.log("[OUTPUT] GPIO cleaned")
