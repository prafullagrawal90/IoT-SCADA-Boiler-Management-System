"""
shared_data_bus.py — SCADA Shared Data Bus
============================================
Single source of truth between GUI (primary) and Web Dashboard (secondary).

The GUI owns the ScadaApplication instance and writes to this bus.
The Web Dashboard reads from this bus — no duplicate MQTT connections.

Files written to project root:
  shared_state.json  — latest processed state (telemetry, alarms, actuators)
  shared_log.json    — rolling 300-entry system event log

Log format: [HH:MM:SS]:[source]->[message]
  source = "core"           (SCADA/GUI events)
  source = "ml"             (ML engine events)
  source = "web(1.2.3.4)"   (Web dashboard events, with client IP)
"""

import json
import os
import time
from collections import deque
from threading import Lock, Thread
from datetime import datetime

# ---------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------
_ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE   = os.path.join(_ROOT, "shared_state.json")
LOG_FILE     = os.path.join(_ROOT, "shared_log.json")
CMD_FILE     = os.path.join(_ROOT, "web_command.json")   # web→GUI IPC

# ---------------------------------------------------------------
# SHARED BUS (in-process singleton)
# ---------------------------------------------------------------
_lock      = Lock()
_state     = {}                    # latest processed state
_log       = deque(maxlen=300)     # rolling log entries
_dirty_state = False
_dirty_log   = False


def write_state(state: dict):
    """Called by GUI's ScadaApplication callback. Updates shared state."""
    global _dirty_state
    with _lock:
        _state.clear()
        _state.update(state)
        _dirty_state = True


def log_event(message: str, source: str = "core"):
    """
    Append a structured log entry.
    source: 'core', 'ml', or 'web(1.2.3.4)'
    Format stored: [HH:MM:SS]:[source]->[message]
    """
    global _dirty_log
    ts = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{ts}]:[{source}]->[{message}]"
    entry = {
        "time": ts,
        "source": source,
        "msg": message,
        "formatted": formatted
    }
    with _lock:
        _log.append(entry)
        _dirty_log = True


def get_state() -> dict:
    with _lock:
        return dict(_state)


def get_log() -> list:
    with _lock:
        return list(_log)


# ---------------------------------------------------------------
# WEB → GUI COMMAND IPC
# ---------------------------------------------------------------

def post_web_command(cmd: str, client_ip: str):
    """Web server writes a pending command for GUI to pick up."""
    entry = {"cmd": cmd, "ip": client_ip, "time": time.time(), "processed": False}
    try:
        _atomic_write(CMD_FILE, entry)
    except Exception as e:
        print(f"[BUS] Failed to write web command: {e}")


def pop_web_command() -> dict | None:
    """GUI polls this. Returns command dict and clears file, or None."""
    if not os.path.exists(CMD_FILE):
        return None
    try:
        with open(CMD_FILE, "r") as f:
            entry = json.load(f)
        if not entry.get("processed"):
            entry["processed"] = True
            _atomic_write(CMD_FILE, entry)
            return entry
    except Exception:
        pass
    return None


# ---------------------------------------------------------------
# DISK WRITER (background thread)
# ---------------------------------------------------------------

def _atomic_write(path: str, data):
    """Safe atomic write for Windows — avoids FileExistsError and 500 errors."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    
    # On Windows, os.replace handles existing files correctly. 
    # We remove the .bak logic to ensure the file is always available for reading.
    try:
        os.replace(tmp, path)
    except Exception as e:
        # Fallback if replace fails due to transient lock
        try:
            if os.path.exists(path): os.remove(path)
            os.replace(tmp, path)
        except:
            pass


def _writer_loop():
    global _dirty_state, _dirty_log
    while True:
        try:
            with _lock:
                need_state = _dirty_state
                need_log   = _dirty_log
                state_snap = dict(_state) if need_state else None
                log_snap   = list(_log)   if need_log   else None
                _dirty_state = False
                _dirty_log   = False

            if need_state and state_snap is not None:
                _atomic_write(STATE_FILE, state_snap)

            if need_log and log_snap is not None:
                _atomic_write(LOG_FILE, log_snap)

        except Exception as e:
            print(f"[BUS] Writer error: {e}")

        time.sleep(0.4)   # flush at ~2.5 Hz


# Start writer thread automatically on import
_writer_thread = Thread(target=_writer_loop, daemon=True, name="SharedBusWriter")
_writer_thread.start()
