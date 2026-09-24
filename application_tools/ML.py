"""
ML.py — SCADA Machine Learning Prediction Engine
=================================================
Runs alongside the GUI and Voice Alarm as a managed service.

Architecture:
    - Reads live data from ScadaApplication (same API as GUI)
    - Maintains rolling history of sensor readings
    - Runs anomaly detection algorithms per-sensor
    - Produces confidence scores and diagnostics
    - Writes predictions to ml_predictions.json (GUI reads this)
    - Auto-triggers emergency shutdown on critical conditions
      if no user response within 10 seconds

Detection Methods:
    1. Range Check       — value outside safe operating envelope
    2. Rate-of-Change    — sudden spike/drop (sensor shock)
    3. Flatline          — sensor stuck (possible failure)
    4. Drift             — slow creep toward danger zone
    5. Correlation Check — cross-sensor sanity (e.g. heater ON but temp flat)
"""

import sys
import os
import time
import json
import math
import statistics
from threading import Thread, Lock, Event
from collections import deque

# Add parent directory to sys.path to find scada_application_layer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scada_application_layer import ScadaApplication
import shared_data_bus as bus


# -----------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------

# How many samples to keep in rolling history per sensor
HISTORY_SIZE = 60  # ~60 readings at 1Hz = 1 minute of data

# Emergency auto-trigger countdown (seconds)
EMERGENCY_COUNTDOWN_COMPOUND = 10   # High pressure + High temp (BOTH) → 10s
EMERGENCY_COUNTDOWN_SINGLE = 20     # Any single critical condition → 20s

# Prediction output file (shared with GUI)
PREDICTION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ml_predictions.json"
)

# Event queue file — ML appends events here, GUI drains and logs them
ML_EVENT_QUEUE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ml_event_queue.json"
)

# GUI acknowledgement signal file (GUI writes this to disarm auto-emergency)
ACK_SIGNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ml_ack_signal.txt"
)

# -----------------------------------------------------------
# SENSOR OPERATING ENVELOPES
# -----------------------------------------------------------
# NOTE:
# water_level = 150 - distance
#
# distance is retained mainly for GUI/reference purposes.
# water_level is the PRIMARY process variable used for control.
# -----------------------------------------------------------

SENSOR_LIMITS = {

    # -------------------------------------------------------
    # WATER LEVEL
    # -------------------------------------------------------
    "water_level": {
        "min": 10,              # Warning below this level
        "max": 125,             # Warning above this level

        "unit": "cm",

        "critical_low": 5,     # Tank nearly empty
        "critical_high": 135,   # Near overflow / sensor blind-zone risk

        "max_rate": 12          # cm per reading
    },

    # -------------------------------------------------------
    # PRESSURE
    # -------------------------------------------------------
    "pressure": {
        "min": 2.0,
        "max": 7.5,

        "unit": "bar",

        "critical_low": 1.0,
        "critical_high": 8.5,

        "max_rate": 1.5         # bar per reading
    },

    # -------------------------------------------------------
    # BOILER TEMPERATURE
    # -------------------------------------------------------
    "boiler_temp": {
        "min": 25,
        "max": 95,

        "unit": "°C",

        "critical_low": 10,
        "critical_high": 105,

        "max_rate": 6           # °C per reading
    },

    # -------------------------------------------------------
    # ROOM TEMPERATURE
    # -------------------------------------------------------
    "room_temp": {
        "min": 15,
        "max": 40,

        "unit": "°C",

        "critical_low": 5,
        "critical_high": 50,

        "max_rate": 3
    },

    # -------------------------------------------------------
    # DISTANCE SENSOR
    #
    # Inverse of water level:
    # distance = 150 - water_level
    #
    # Smaller distance = fuller tank
    # Larger distance = emptier tank
    # -------------------------------------------------------
    "distance": {
        "min": 15,              # Tank getting too full
        "max": 138,             # Tank getting too empty

        "unit": "cm",

        "critical_low": 8,      # Sensor blind-zone danger
        "critical_high": 145,   # Tank nearly empty

        "max_rate": 12
    }
}

# Flatline detection: if std dev < threshold for N readings, sensor stuck
FLATLINE_WINDOW = 15
FLATLINE_THRESHOLD = 0.05

# Drift detection: sustained movement toward danger
DRIFT_WINDOW = 20
DRIFT_THRESHOLD = 0.6  # 60% of readings trending same direction


# -----------------------------------------------------------
# ML PREDICTION ENGINE
# -----------------------------------------------------------

class MLPredictionEngine:
    """
    The brain of the SCADA system.
    Continuously analyzes sensor data and produces predictions,
    confidence scores, and diagnostics for the GUI overlay.
    """

    def __init__(self):
        print("[ML] Initializing ML Prediction Engine...")

        self.app = ScadaApplication()
        self.running = True
        self.data_lock = Lock()

        # Rolling history per sensor
        self.history = {
            sensor: deque(maxlen=HISTORY_SIZE)
            for sensor in SENSOR_LIMITS
        }

        # Timestamps for each sample
        self.timestamps = deque(maxlen=HISTORY_SIZE)

        # Current predictions state
        self.predictions = {
            "timestamp": 0,
            "overall_health": "INITIALIZING",
            "overall_confidence": 0.0,
            "sensors": {},
            "diagnostics": [],
            "emergency_countdown": None,
            "auto_emergency_armed": False,
            "correlation_warnings": [],
            "system_status": "STARTING"
        }

        # Emergency auto-trigger state
        self._emergency_armed = False
        self._emergency_arm_time = 0
        self._emergency_triggered = False
        self._emergency_tier = None          # "COMPOUND" or "SINGLE"
        self._emergency_countdown = 0        # Active countdown duration
        self._last_user_action_time = time.time()

        # Operator acknowledgement suppression:
        # After the operator clicks "I Acknowledge", ML will NOT re-arm the countdown
        # for the same ongoing condition. It re-enables only AFTER conditions fully clear.
        self._operator_acked = False          # True = operator has acknowledged, suppress re-arm
        self._conditions_were_clear = True    # Tracks if conditions were last seen as clear

        # Danger persistence counter: require N consecutive danger cycles before arming.
        # This prevents a single noisy sensor reading from triggering the countdown.
        self._danger_persist_count = 0
        DANGER_PERSIST_REQUIRED = 2           # Must see danger for 2 cycles (~2s) before arming

        # Actuator state tracking (for correlation checks)
        self._last_actuators = {"heater": False, "motor": False, "override": False}

        # Register for live data callbacks
        self.app.on_data(self._on_data_received)

        # Analysis thread
        self._analysis_thread = Thread(target=self._analysis_loop, daemon=True)
        self._analysis_thread.start()

        # Emergency watchdog thread
        self._watchdog_thread = Thread(target=self._emergency_watchdog, daemon=True)
        self._watchdog_thread.start()

        # File writer thread
        self._writer_thread = Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

        print("[ML] Engine started. Waiting for data...")

    # -----------------------------------------------------------
    # DATA INGESTION
    # -----------------------------------------------------------

    def _on_data_received(self, state):
        """Called by ScadaApplication on every new MQTT message."""
        telemetry = state.get("telemetry", {})
        actuators = state.get("actuators", {})
        system = state.get("system", {})

        if not system.get("sensor_valid", False):
            return

        with self.data_lock:
            now = time.time()
            self.timestamps.append(now)

            for sensor in SENSOR_LIMITS:
                val = telemetry.get(sensor)
                if val is not None and val != "--":
                    try:
                        self.history[sensor].append(float(val))
                    except (ValueError, TypeError):
                        pass

            # Track actuator changes (user interaction = reset emergency timer)
            if actuators != self._last_actuators:
                self._last_user_action_time = time.time()
                self._last_actuators = actuators.copy()

    # -----------------------------------------------------------
    # ANALYSIS ENGINE (runs every second)
    # -----------------------------------------------------------

    def _analysis_loop(self):
        """Main analysis loop — runs all detection algorithms."""
        # Wait for initial data
        time.sleep(3)

        while self.running:
            try:
                self._run_analysis()
            except Exception as e:
                print(f"[ML] Analysis error: {e}")
            time.sleep(1)

    def _run_analysis(self):
        """Execute all detection algorithms and update predictions."""
        with self.data_lock:
            if not self.timestamps:
                return

            sensor_results = {}
            diagnostics = []
            confidence_scores = []
            critical_detected = False

            for sensor, limits in SENSOR_LIMITS.items():
                readings = list(self.history[sensor])

                if len(readings) < 3:
                    sensor_results[sensor] = {
                        "status": "INSUFFICIENT_DATA",
                        "confidence": 0.0,
                        "value": readings[-1] if readings else None,
                        "issues": [],
                        "trend": "UNKNOWN"
                    }
                    continue

                current = readings[-1]
                issues = []
                penalties = []  # Each penalty reduces confidence

                # ----- 1. RANGE CHECK -----
                range_result = self._check_range(current, limits)
                if range_result["critical"]:
                    issues.append(range_result["message"])
                    penalties.append(0.4)
                    critical_detected = True
                elif range_result["warning"]:
                    issues.append(range_result["message"])
                    penalties.append(0.15)

                # ----- 2. RATE-OF-CHANGE -----
                roc_result = self._check_rate_of_change(readings, limits)
                if roc_result["issue"]:
                    issues.append(roc_result["message"])
                    penalties.append(roc_result["penalty"])
                    if roc_result["critical"]:
                        critical_detected = True

                # ----- 3. FLATLINE DETECTION -----
                flat_result = self._check_flatline(readings)
                if flat_result["issue"]:
                    issues.append(flat_result["message"])
                    penalties.append(flat_result["penalty"])

                # ----- 4. DRIFT DETECTION -----
                drift_result = self._check_drift(readings, limits)
                if drift_result["issue"]:
                    issues.append(drift_result["message"])
                    penalties.append(drift_result["penalty"])

                # ----- CALCULATE CONFIDENCE -----
                base_confidence = 1.0
                for p in penalties:
                    base_confidence -= p
                confidence = max(0.0, min(1.0, base_confidence))
                confidence_scores.append(confidence)

                # ----- DETERMINE STATUS -----
                if confidence >= 0.85:
                    status = "HEALTHY"
                elif confidence >= 0.6:
                    status = "WARNING"
                elif confidence >= 0.3:
                    status = "DEGRADED"
                else:
                    status = "CRITICAL"

                # ----- TREND -----
                trend = self._calculate_trend(readings)

                sensor_results[sensor] = {
                    "status": status,
                    "confidence": round(confidence, 3),
                    "value": round(current, 2),
                    "issues": issues,
                    "trend": trend,
                    "readings_count": len(readings),
                    "mean": round(statistics.mean(readings), 2) if len(readings) >= 2 else current,
                    "std_dev": round(statistics.stdev(readings), 3) if len(readings) >= 2 else 0
                }

                if issues:
                    for issue in issues:
                        diagnostics.append(f"[{sensor.upper()}] {issue}")

            # ----- 5. CORRELATION CHECKS -----
            correlation_warnings = self._check_correlations()
            if correlation_warnings:
                diagnostics.extend(correlation_warnings)

            # ----- OVERALL HEALTH -----
            if confidence_scores:
                overall_conf = statistics.mean(confidence_scores)
            else:
                overall_conf = 0

            if overall_conf >= 0.85:
                overall_health = "NOMINAL"
            elif overall_conf >= 0.6:
                overall_health = "CAUTION"
            elif overall_conf >= 0.3:
                overall_health = "WARNING"
            else:
                overall_health = "CRITICAL"

            # ----- EMERGENCY ARMING -----
            state = self.app.get_latest_data()
            alarms = state.get("alarms", {})
            actuators = state.get("actuators", {})
            override_active = actuators.get("override", False)

            # Determine emergency tier
            hp = alarms.get("high_pressure", False)
            hl = alarms.get("high_level", False)

            # Check boiler temp critical (>= 110°C)
            boiler_temp_critical = False
            boiler_sensor = sensor_results.get("boiler_temp", {})
            if boiler_sensor.get("status") == "CRITICAL":
                boiler_temp_critical = True

            # COMPOUND: high pressure + high temp (both active)
            compound_danger = hp and boiler_temp_critical

            # SINGLE: ML detects critical OR system's hard alarms are tripped.
            # NOTE: overall_conf is intentionally NOT used as a gate here — when sensors
            # are most critical, confidence is at its LOWEST (lots of penalties applied),
            # so gating on confidence would prevent arming exactly when it's most needed.
            single_danger = critical_detected or hp or hl

            # Pick tier and countdown
            if compound_danger:
                tier = "COMPOUND"
                countdown_duration = EMERGENCY_COUNTDOWN_COMPOUND
            elif single_danger:
                tier = "SINGLE"
                countdown_duration = EMERGENCY_COUNTDOWN_SINGLE
            else:
                tier = None
                countdown_duration = 0

            # ----- DANGER PERSISTENCE (anti-flap) -----
            # Require the danger condition to persist for DANGER_PERSIST_REQUIRED
            # consecutive analysis cycles before we arm. Resets immediately when safe.
            DANGER_PERSIST_REQUIRED = 2
            if tier is not None:
                self._danger_persist_count = min(
                    self._danger_persist_count + 1, DANGER_PERSIST_REQUIRED
                )
            else:
                self._danger_persist_count = 0  # Reset instantly when conditions clear

            danger_confirmed = (self._danger_persist_count >= DANGER_PERSIST_REQUIRED)

            # ----- CONDITION CLEAR TRACKING (for ack suppression reset) -----
            conditions_now_clear = (tier is None) and (self._danger_persist_count == 0)
            if conditions_now_clear and not self._conditions_were_clear:
                # Conditions have just fully cleared — reset ack suppression so
                # ML can re-arm if danger returns
                self._conditions_were_clear = True
                if self._operator_acked:
                    self._operator_acked = False
                    print("[ML] ✅ Conditions cleared — operator ack suppression LIFTED.")
            elif not conditions_now_clear:
                self._conditions_were_clear = False

            should_arm = (
                danger_confirmed                  # danger confirmed for 2 consecutive cycles
                and tier is not None
                and not override_active
                and not self._emergency_triggered
                and not self._operator_acked      # suppressed after acknowledge
            )

            emergency_countdown = None

            if should_arm and not self._emergency_armed:
                # Fresh arm
                self._emergency_armed = True
                self._emergency_arm_time = time.time()
                self._emergency_tier = tier
                self._emergency_countdown = countdown_duration
                diagnostics.append(f"⚠️ CRITICAL [{tier}]: Auto-emergency armed! "
                                   f"Triggering in {countdown_duration}s if no user action.")
                print(f"[ML] 🚨 AUTO-EMERGENCY ARMED [{tier}] — {countdown_duration}s countdown!")

            elif should_arm and self._emergency_armed:
                # Already armed — check if tier escalated (single → compound)
                if tier == "COMPOUND" and self._emergency_tier == "SINGLE":
                    self._emergency_tier = "COMPOUND"
                    self._emergency_countdown = EMERGENCY_COUNTDOWN_COMPOUND
                    self._emergency_arm_time = time.time()  # Reset timer for new shorter countdown
                    diagnostics.append("🚨 ESCALATED: Compound danger detected! "
                                       f"Countdown shortened to {EMERGENCY_COUNTDOWN_COMPOUND}s!")
                    print(f"[ML] 🚨 ESCALATED to COMPOUND — {EMERGENCY_COUNTDOWN_COMPOUND}s countdown!")

            if self._operator_acked and tier is not None:
                # Operator has acknowledged — show a passive warning (no countdown)
                diagnostics.append(f"👁️ [{tier}] Operator monitoring — ML interrupt suppressed.")

            if self._emergency_armed:
                elapsed = time.time() - self._emergency_arm_time
                remaining = max(0, self._emergency_countdown - elapsed)
                emergency_countdown = round(remaining, 1)

                # Disarm if conditions cleared
                if conditions_now_clear:
                    self._emergency_armed = False
                    self._emergency_triggered = False
                    self._emergency_tier = None
                    diagnostics.append("✅ Critical conditions cleared — auto-emergency disarmed.")
                    print("[ML] ✅ Auto-emergency DISARMED — conditions normalized.")
                    emergency_countdown = None

                # Disarm if user took action
                elif self._last_user_action_time > self._emergency_arm_time:
                    self._emergency_armed = False
                    self._emergency_tier = None
                    diagnostics.append("✅ User action detected — auto-emergency disarmed.")
                    print("[ML] ✅ Auto-emergency DISARMED — user responded.")
                    emergency_countdown = None

            # ----- UPDATE PREDICTIONS -----
            self.predictions = {
                "timestamp": time.time(),
                "overall_health": overall_health,
                "overall_confidence": round(overall_conf, 3),
                "sensors": sensor_results,
                "diagnostics": diagnostics[-20:],  # Keep last 20
                "emergency_countdown": emergency_countdown,
                "auto_emergency_armed": self._emergency_armed,
                "emergency_tier": self._emergency_tier,
                "correlation_warnings": correlation_warnings,
                "system_status": "ACTIVE"
            }

    # -----------------------------------------------------------
    # DETECTION ALGORITHMS
    # -----------------------------------------------------------

    def _check_range(self, value, limits):
        """Check if value is within safe operating range."""
        result = {"warning": False, "critical": False, "message": ""}

        if value <= limits["critical_low"]:
            result["critical"] = True
            result["message"] = f"CRITICAL LOW: {value} {limits['unit']} (limit: {limits['critical_low']})"
        elif value >= limits["critical_high"]:
            result["critical"] = True
            result["message"] = f"CRITICAL HIGH: {value} {limits['unit']} (limit: {limits['critical_high']})"
        elif value <= limits["min"]:
            result["warning"] = True
            result["message"] = f"Below safe range: {value} {limits['unit']} (min: {limits['min']})"
        elif value >= limits["max"]:
            result["warning"] = True
            result["message"] = f"Above safe range: {value} {limits['unit']} (max: {limits['max']})"

        return result

    def _check_rate_of_change(self, readings, limits):
        """Detect sudden spikes or drops."""
        result = {"issue": False, "critical": False, "penalty": 0, "message": ""}

        if len(readings) < 2:
            return result

        # Check last few deltas
        recent = readings[-5:] if len(readings) >= 5 else readings
        deltas = [abs(recent[i] - recent[i-1]) for i in range(1, len(recent))]

        if not deltas:
            return result

        max_delta = max(deltas)
        max_rate = limits["max_rate"]

        if max_delta > max_rate * 2:
            result["issue"] = True
            result["critical"] = True
            result["penalty"] = 0.35
            result["message"] = (f"EXTREME rate-of-change: Δ{max_delta:.2f} "
                                 f"(limit: {max_rate} {limits['unit']}/s) — possible sensor shock")
        elif max_delta > max_rate:
            result["issue"] = True
            result["penalty"] = 0.15
            result["message"] = (f"High rate-of-change: Δ{max_delta:.2f} "
                                 f"(limit: {max_rate} {limits['unit']}/s)")

        return result

    def _check_flatline(self, readings):
        """Detect if sensor is stuck (producing identical/near-identical values)."""
        result = {"issue": False, "penalty": 0, "message": ""}

        window = readings[-FLATLINE_WINDOW:] if len(readings) >= FLATLINE_WINDOW else readings
        if len(window) < FLATLINE_WINDOW:
            return result

        std = statistics.stdev(window)
        if std < FLATLINE_THRESHOLD:
            result["issue"] = True
            result["penalty"] = 0.25
            result["message"] = (f"FLATLINE detected: σ={std:.4f} over {FLATLINE_WINDOW} readings "
                                 f"— possible sensor failure or disconnection")

        return result

    def _check_drift(self, readings, limits):
        """Detect slow, sustained movement toward danger zone."""
        result = {"issue": False, "penalty": 0, "message": ""}

        window = readings[-DRIFT_WINDOW:] if len(readings) >= DRIFT_WINDOW else readings
        if len(window) < DRIFT_WINDOW:
            return result

        # Count how many consecutive readings are trending in one direction
        increasing = sum(1 for i in range(1, len(window)) if window[i] > window[i-1])
        decreasing = sum(1 for i in range(1, len(window)) if window[i] < window[i-1])
        total = len(window) - 1

        if total == 0:
            return result

        # Check if drifting toward critical zone
        current = window[-1]
        drift_ratio = max(increasing, decreasing) / total

        if drift_ratio >= DRIFT_THRESHOLD:
            direction = "RISING" if increasing > decreasing else "FALLING"
            toward_danger = False

            if direction == "RISING" and current > limits["max"] * 0.7:
                toward_danger = True
            elif direction == "FALLING" and current < limits["min"] * 1.3:
                toward_danger = True

            if toward_danger:
                result["issue"] = True
                result["penalty"] = 0.15
                result["message"] = (f"DRIFT: Sustained {direction} trend "
                                     f"({drift_ratio:.0%} of readings) toward danger zone")

        return result

    def _calculate_trend(self, readings):
        """Calculate simple trend direction."""
        if len(readings) < 5:
            return "STABLE"

        recent = readings[-10:] if len(readings) >= 10 else readings
        first_half = statistics.mean(recent[:len(recent)//2])
        second_half = statistics.mean(recent[len(recent)//2:])
        diff = second_half - first_half

        if abs(diff) < 0.5:
            return "STABLE"
        elif diff > 0:
            return "RISING"
        else:
            return "FALLING"

    def _check_correlations(self):
        """Cross-sensor sanity checks."""
        warnings = []

        state = self.app.get_latest_data()
        if not state:
            return warnings

        actuators = state.get("actuators", {})
        telemetry = state.get("telemetry", {})

        # Check: Heater ON but boiler temp flatline
        if actuators.get("heater"):
            temp_readings = list(self.history.get("boiler_temp", []))
            if len(temp_readings) >= 20:
                recent = temp_readings[-20:]
                std = statistics.stdev(recent)
                if std < 0.1:
                    warnings.append(
                        "[CORRELATION] Heater ON but boiler temp flatline "
                        "— possible heating element failure or sensor issue"
                    )

        # Check: Motor ON but water level not changing
        if actuators.get("motor"):
            level_readings = list(self.history.get("water_level", []))
            if len(level_readings) >= 15:
                recent = level_readings[-15:]
                std = statistics.stdev(recent)
                if std < 0.1:
                    warnings.append(
                        "[CORRELATION] Motor ON but water level static "
                        "— possible pump failure or blockage"
                    )

        # Check: Pressure rising while water level dropping (dangerous)
        pressure_readings = list(self.history.get("pressure", []))
        level_readings = list(self.history.get("water_level", []))
        if len(pressure_readings) >= 10 and len(level_readings) >= 10:
            p_trend = self._calculate_trend(pressure_readings)
            l_trend = self._calculate_trend(level_readings)
            if p_trend == "RISING" and l_trend == "FALLING":
                warnings.append(
                    "[CORRELATION] ⚠️ Pressure RISING while water level FALLING "
                    "— potential boil-dry condition!"
                )

        return warnings

    # -----------------------------------------------------------
    # EMERGENCY WATCHDOG
    # -----------------------------------------------------------

    def _emergency_watchdog(self):
        """
        Monitors the armed emergency state.
        If armed and no user action within EMERGENCY_COUNTDOWN seconds,
        automatically triggers emergency shutdown.
        Also monitors the ACK signal file from GUI for cross-process acknowledgement.
        """
        while self.running:
            try:
                # Check for GUI acknowledgement signal file
                if os.path.exists(ACK_SIGNAL_FILE):
                    try:
                        ack_mtime = os.path.getmtime(ACK_SIGNAL_FILE)
                        if ack_mtime > self._emergency_arm_time or self._emergency_armed:
                            # Disarm current countdown AND suppress re-arming
                            # for this same ongoing condition
                            self._emergency_armed = False
                            self._operator_acked = True
                            self._last_user_action_time = time.time()
                            print("[ML] ✅ Watchdog: Operator acknowledged — "
                                  "countdown disarmed, re-arming SUPPRESSED until conditions clear.")
                        os.remove(ACK_SIGNAL_FILE)
                    except:
                        pass

                if self._emergency_armed and not self._emergency_triggered:
                    elapsed = time.time() - self._emergency_arm_time

                    # Check if user acted since arming
                    if self._last_user_action_time > self._emergency_arm_time:
                        self._emergency_armed = False
                        self._emergency_tier = None
                        print("[ML] ✅ Watchdog: User responded, disarming.")
                    elif elapsed >= self._emergency_countdown:
                        # NO USER RESPONSE — AUTO TRIGGER EMERGENCY
                        tier = self._emergency_tier or "UNKNOWN"
                        print(f"[ML] \U0001f6a8\U0001f6a8\U0001f6a8 AUTO-EMERGENCY TRIGGERED [{tier}] \u2014 No user response!")
                        self.app.send_command("emergency")
                        self._emergency_triggered = True
                        self._emergency_armed = False
                        self._emergency_tier = None
                        # ---- WRITE TO ml_event_queue.json (GUI polls this for its log panel) ----
                        # bus.log_event() only reaches the web dashboard (different process).
                        # The GUI drains ml_event_queue.json to show entries in its System Log.
                        from datetime import datetime as _dt
                        _ts = _dt.now().strftime("%H:%M:%S")
                        _event = {
                            "time": _ts,
                            "source": "ml",
                            "msg": (
                                f"\U0001f6a8 ML AUTO-EMERGENCY DEPLOYED [{tier}] \u2014 "
                                f"No operator response within {self._emergency_countdown:.0f}s. "
                                "Emergency shutdown command sent."
                            )
                        }
                        try:
                            # Read existing queue, append, write back
                            _q = []
                            if os.path.exists(ML_EVENT_QUEUE_FILE):
                                with open(ML_EVENT_QUEUE_FILE, "r") as _f:
                                    _q = json.load(_f)
                            _q.append(_event)
                            _tmp = ML_EVENT_QUEUE_FILE + ".tmp"
                            with open(_tmp, "w") as _f:
                                json.dump(_q, _f)
                            os.replace(_tmp, ML_EVENT_QUEUE_FILE)
                        except Exception as _eq:
                            print(f"[ML] Event queue write error: {_eq}")
                        # Also log to shared bus for web dashboard
                        bus.log_event(_event["msg"], source="ml")
                    else:
                        remaining = self._emergency_countdown - elapsed
                        if int(remaining) != int(remaining + 1):
                            tier = self._emergency_tier or "?"
                            print(f"[ML] ⏳ [{tier}] Emergency countdown: {remaining:.0f}s remaining...")

            except Exception as e:
                print(f"[ML] Watchdog error: {e}")

            time.sleep(0.5)

    # -----------------------------------------------------------
    # FILE WRITER (shares predictions with GUI)
    # -----------------------------------------------------------

    def _writer_loop(self):
        """Writes predictions to disk for GUI consumption."""
        while self.running:
            try:
                with self.data_lock:
                    data = self.predictions.copy()

                # Write atomically (write to temp, then rename)
                temp_path = PREDICTION_FILE + ".tmp"
                with open(temp_path, "w") as f:
                    json.dump(data, f, indent=2, default=str)

                # Atomic replace
                if os.path.exists(PREDICTION_FILE):
                    os.remove(PREDICTION_FILE)
                os.rename(temp_path, PREDICTION_FILE)

            except Exception as e:
                print(f"[ML] Writer error: {e}")

            time.sleep(0.5)

    # -----------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------

    def get_predictions(self):
        """Returns current predictions (for in-process consumers)."""
        with self.data_lock:
            return self.predictions.copy()

    def acknowledge_emergency(self):
        """User acknowledged the emergency warning — disarm and suppress re-arming."""
        self._last_user_action_time = time.time()
        self._emergency_armed = False
        self._operator_acked = True   # Suppress re-arming until conditions clear
        print("[ML] ✅ Emergency acknowledged by user — ML interrupt suppressed.")

    def shutdown(self):
        """Gracefully stop the ML engine."""
        self.running = False
        self.app.shutdown()

        # Clean up prediction file
        try:
            if os.path.exists(PREDICTION_FILE):
                os.remove(PREDICTION_FILE)
        except:
            pass

        print("[ML] Engine stopped.")

    # -----------------------------------------------------------
    # MAIN
    # -----------------------------------------------------------

    def run(self):
        """Main blocking loop — keeps the engine alive."""
        print("\n=== SCADA ML PREDICTION ENGINE ACTIVE ===\n")
        try:
            while self.running:
                # Periodic status report
                preds = self.get_predictions()
                health = preds.get("overall_health", "UNKNOWN")
                conf = preds.get("overall_confidence", 0)
                armed = preds.get("auto_emergency_armed", False)
                countdown = preds.get("emergency_countdown")

                status_parts = [
                    f"Health: {health}",
                    f"Confidence: {conf:.1%}"
                ]
                if armed and countdown is not None:
                    status_parts.append(f"⚠️ EMERGENCY IN {countdown:.0f}s")

                print(f"[ML] {' | '.join(status_parts)}")

                time.sleep(5)

        except KeyboardInterrupt:
            self.shutdown()


if __name__ == "__main__":
    engine = MLPredictionEngine()
    engine.run()
