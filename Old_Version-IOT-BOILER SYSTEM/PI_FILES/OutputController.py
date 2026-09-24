import RPi.GPIO as GPIO
import json
import os
import time

STATE_FILE = "outputs_state.json"

HEATER_PIN = 27
MOTOR_PIN = 22


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

        self._ensure_state_file()
        self.load_state()

        # 🔥 APPLY LAST KNOWN STATE (POWER RECOVERY)
        self.apply_state(verify=True)

    # ------------------------------------------------
    # FILE HANDLING
    # ------------------------------------------------

    def _ensure_state_file(self):

        if not os.path.exists(STATE_FILE):

            self.log("[INIT] Creating default state file")

            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f)

    def load_state(self):

        try:
            with open(STATE_FILE, "r") as f:
                self.state = json.load(f)

            self.log(f"[STATE] Loaded: {self.state}")

        except Exception as e:
            self.log(f"[ERROR] Load failed: {e}")

    def save_state(self):

        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f)

        except Exception as e:
            self.log(f"[ERROR] Save failed: {e}")

    # ------------------------------------------------
    # GPIO APPLY + VERIFY (CLOSED LOOP)
    # ------------------------------------------------

    def apply_state(self, verify=False):

        GPIO.output(HEATER_PIN, self.state["heater"])
        GPIO.output(MOTOR_PIN, self.state["motor"])

        time.sleep(0.1)

        if verify:
            return self.verify_state()

        return True

    def verify_state(self):

        heater_actual = GPIO.input(HEATER_PIN)
        motor_actual = GPIO.input(MOTOR_PIN)

        ok = (
            heater_actual == self.state["heater"] and
            motor_actual == self.state["motor"]
        )

        if ok:
            self.log("[VERIFY] OK")
        else:
            self.log("[VERIFY] FAILED")

        return ok

    # ------------------------------------------------
    # API (MAIN CONTROL INTERFACE)
    # ------------------------------------------------

    def execute(self, command):

        self.log(f"[CMD] Received: {command}")

        # ------------------------------------------------
        # ALWAYS ALLOWED
        # ------------------------------------------------
        if command == "reset_override":

            self.state["override"] = None
            success = self.apply_state(verify=True)

            if success:
                self.save_state()
                return {"status": "ok", "state": self.state}

            return {"status": "fail"}

        # ------------------------------------------------
        # BLOCK IF LOCKED
        # ------------------------------------------------
        if self.state["override"] == "ssh":
            self.log("[LOCK] Command blocked due to override")
            return {"status": "blocked", "reason": "override_active"}

        # ------------------------------------------------
        # COMMANDS
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
            # 🔥 SSH MODE LOCK
            self.state["heater"] = False
            self.state["motor"] = False
            self.state["override"] = "ssh"

        else:
            return {"status": "error", "msg": "unknown command"}

        # ------------------------------------------------
        # APPLY + VERIFY
        # ------------------------------------------------
        success = self.apply_state(verify=True)

        if success:
            self.save_state()
            return {"status": "ok", "state": self.state}
        else:
            return {"status": "fail", "state": self.state}
    # ------------------------------------------------

    def get_state(self):
        return self.state

    # ------------------------------------------------

    def shutdown(self):
        GPIO.cleanup()
        self.log("[GPIO] Cleaned up")
