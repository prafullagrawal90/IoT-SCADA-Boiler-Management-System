"""
voice_alarm_system.py — SCADA Voice Alarm with Audio NVIC
==========================================================
Audio NVIC (priority controller):
  - Single audio thread — physically impossible to play simultaneously
  - Priority: EMERGENCY(0) > ALARM(1) > ACTUATOR(2)
  - Stale requests (>4s old) dropped automatically
  - Debounce: same alarm not re-submitted within 3s

Singleton: Windows Named Mutex — only ONE instance ever runs.
"""

import sys, os, time, queue, winsound, ctypes
from threading import Thread, Lock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scada_application_layer import ScadaApplication

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------
WEB_PASSWORD       = os.environ.get("WEB_PASSWORD", "1234")
BEEP_ALARM         = (1500, 1000)   # (freq_hz, duration_ms)
BEEP_EMERGENCY     = (2000, 1000)
BEEP_ACTUATOR      = (1000, 200)
REPEAT_PAUSE       = 1.0            # seconds between alarm cycles
DEBOUNCE_SECS      = 3.0            # min seconds between same alarm submissions
REQUEST_TTL        = 4.0            # drop requests older than this

PRI_EMERGENCY      = 0
PRI_ALARM          = 1
PRI_ACTUATOR       = 2

MUTEX_NAME = "Global\\SCADA_VoiceAlarm_Singleton"
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)


# ---------------------------------------------------------------
# SINGLETON MUTEX
# ---------------------------------------------------------------
def _acquire_mutex():
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:
        print("[VOICE] Another instance already running. Exiting.")
        sys.exit(0)
    print(f"[VOICE] Singleton acquired — PID {os.getpid()}")
    return handle


# ---------------------------------------------------------------
# AUDIO NVIC — single drain thread, priority queue
# ---------------------------------------------------------------
class AudioNVIC:
    """
    Hardware-NVIC-inspired audio priority gate.
    ALL winsound calls happen ONLY in _drain_thread.
    Main loop just submits requests — never blocks on audio.
    """
    def __init__(self, assets_dir):
        self.assets_dir = assets_dir
        self._q = queue.PriorityQueue()
        self._thread = Thread(target=self._drain, daemon=True, name="AudioNVIC")
        self._thread.start()

    def submit(self, phrases, priority, beep_freq, beep_dur):
        """Non-blocking. Puts request in priority queue with timestamp."""
        self._q.put((priority, time.time(), phrases, beep_freq, beep_dur))

    def _beep(self, freq, dur):
        winsound.Beep(freq, dur)

    def _play(self, phrase):
        path = os.path.join(self.assets_dir, phrase.replace(" ", "_").lower() + ".wav")
        if os.path.exists(path):
            winsound.PlaySound(path, winsound.SND_FILENAME)

    def _play_seq(self, phrases):
        for i, p in enumerate(phrases):
            self._play(p)
            if i < len(phrases) - 1:
                self._play("and")

    def _drain(self):
        """Single thread — only place winsound is ever called."""
        while True:
            try:
                priority, ts, phrases, freq, dur = self._q.get()

                # Drop stale requests (alarm already cleared)
                if time.time() - ts > REQUEST_TTL:
                    print(f"[NVIC] Dropped stale request (age {time.time()-ts:.1f}s): {phrases}")
                    continue

                print(f"[NVIC] Playing P{priority}: {' + '.join(phrases)}")

                if priority == PRI_ACTUATOR:
                    # Actuator one-shots: BEEP -> VOICE  (once only)
                    self._beep(freq, dur)
                    self._play_seq(phrases)
                else:
                    # Alarms: BEEP -> VOICE -> BEEP -> VOICE  (twice for urgency)
                    self._beep(freq, dur)
                    self._play_seq(phrases)
                    self._beep(freq, dur)
                    self._play_seq(phrases)

            except Exception as e:
                print(f"[NVIC] Error: {e}")
                time.sleep(0.5)



# ---------------------------------------------------------------
# VOICE ALARM SYSTEM
# ---------------------------------------------------------------
class VoiceAlarmSystem:

    def __init__(self):
        self.app = ScadaApplication()
        self.assets_dir = os.path.join(_SCRIPT_DIR, "voice_assets")
        if not os.path.exists(self.assets_dir):
            os.makedirs(self.assets_dir)

        self.nvic = AudioNVIC(self.assets_dir)

        self._last_actuators = None
        # Debounce: track last submission time per alarm key
        self._last_submitted  = {}   # frozenset(alarms) -> time
        # Cleared detection: track last known state of each individual alarm
        self._prev_alarms     = {}   # {alarm_name: bool}
        self.running = True

        self._verify_assets()

    def _verify_assets(self):
        required = [
            "HIGH PRESSURE", "LOW PRESSURE",
            "HIGH WATER LEVEL", "LOW WATER LEVEL",
            "emergency initiated", "emergency reset initiated",
            "heater on", "heater off", "motor on", "motor off",
            "alarm cleared", "and"
        ]
        missing = [p for p in required if not os.path.exists(
            os.path.join(self.assets_dir, p.replace(" ","_").lower()+".wav"))]
        if missing:
            print("[VOICE] Missing assets:", ", ".join(missing))
        else:
            print(f"[VOICE] All {len(required)} assets OK.")

    def _get_alarms(self):
        state = self.app.get_latest_data()
        if not state:
            return [], False, {}
        alarms    = state.get("alarms", {})
        actuators = state.get("actuators", {})
        if actuators.get("override"):
            return ["emergency initiated"], True, actuators
        active = []
        if alarms.get("high_pressure"): active.append("HIGH PRESSURE")
        if alarms.get("low_pressure"):  active.append("LOW PRESSURE")
        if alarms.get("high_level"):    active.append("HIGH WATER LEVEL")
        if alarms.get("low_level"):     active.append("LOW WATER LEVEL")
        return active, False, actuators

    def _get_oneshots(self, current):
        if not current:
            return []
        if self._last_actuators is None:
            self._last_actuators = current.copy()
            return []
        prev = self._last_actuators
        oneshots = []
        if prev.get("override") and not current.get("override"):
            oneshots.append("emergency reset initiated")
        if current.get("heater") != prev.get("heater"):
            oneshots.append("heater on" if current.get("heater") else "heater off")
        if current.get("motor") != prev.get("motor"):
            oneshots.append("motor on" if current.get("motor") else "motor off")
        self._last_actuators = current.copy()
        return oneshots

    def _should_submit(self, key):
        """Debounce: returns True only if this alarm hasn't been submitted recently."""
        now = time.time()
        last = self._last_submitted.get(key, 0)
        if now - last >= DEBOUNCE_SECS:
            self._last_submitted[key] = now
            return True
        return False

    def run(self):
        print("\n=== SCADA VOICE ALARM — AUDIO NVIC ACTIVE ===")
        print("Priority: EMERGENCY(0) > ALARM(1) > ACTUATOR(2)")
        print("Pattern: BEEP->VOICE->BEEP->VOICE per cycle\n")

        while self.running:
            try:
                active, is_emergency, actuators = self._get_alarms()
                oneshots = self._get_oneshots(actuators)

                # ACTUATOR one-shots → NVIC priority 2
                for phrase in oneshots:
                    key = frozenset([phrase])
                    if self._should_submit(key):
                        print(f"[VOICE] Actuator: {phrase}")
                        self.nvic.submit([phrase], PRI_ACTUATOR, *BEEP_ACTUATOR)

                # ALARM → NVIC priority 0 or 1
                if active:
                    key = frozenset(active)
                    if self._should_submit(key):
                        pri  = PRI_EMERGENCY if is_emergency else PRI_ALARM
                        beep = BEEP_EMERGENCY if is_emergency else BEEP_ALARM
                        print(f"[VOICE] Alarm submitted P{pri}: {' + '.join(active)}")
                        self.nvic.submit(active, pri, *beep)

                # ── Cleared detection ─────────────────────────────────
                # Build current alarm dict from the active list
                ALARM_KEYS = ["HIGH PRESSURE", "LOW PRESSURE",
                              "HIGH WATER LEVEL", "LOW WATER LEVEL"]
                current_alarms = {k: (k in active) for k in ALARM_KEYS}

                if self._prev_alarms:          # skip very first poll
                    just_cleared = [
                        k for k in ALARM_KEYS
                        if self._prev_alarms.get(k) and not current_alarms[k]
                    ]
                    if just_cleared:
                        key = frozenset(["alarm cleared"])
                        if self._should_submit(key):
                            print(f"[VOICE] Cleared: {', '.join(just_cleared)} → playing 'alarm cleared'")
                            # Distinct descending beep (800 Hz) + voice, played once
                            self.nvic.submit(["alarm cleared"], PRI_ACTUATOR, 800, 400)

                self._prev_alarms = current_alarms

                if not active:
                    # Reset debounce so the next real alarm plays immediately
                    self._last_submitted = {
                        k: v for k, v in self._last_submitted.items()
                        if k == frozenset(["alarm cleared"])
                    }

                time.sleep(REPEAT_PAUSE)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"[VOICE] Error: {e}")
                time.sleep(1)

        print("[VOICE] Shutdown.")
        try:
            self.app.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    _mutex = _acquire_mutex()
    vas = VoiceAlarmSystem()
    vas.run()
