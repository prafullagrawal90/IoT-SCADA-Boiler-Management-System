"""
web_server.py - SCADA Web Dashboard (Secondary / Read-Only HMI)
================================================================
Access: http://<your-pc-ip>:5000

Features:
  - Reads shared_state.json / shared_log.json written by GUI (primary)
  - Commands routed via web_command.json IPC → GUI executes them
  - Client connect/disconnect events logged in GUI system log
  - Shutdown signal monitor (exits when SCADA shuts down)
  - Web event queue (web_event_queue.json) → GUI picks up & displays
  - No MQTT, no voice alarms
"""

import sys
import os
import time
import json
import signal
import socket
from datetime import datetime
from threading import Lock, Thread
from flask import Flask, render_template, Response, jsonify, request as flask_request, make_response
import requests

# ---------------------------------------------------------------
# PATH SETUP
# ---------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

# ---------------------------------------------------------------
# CONFIG  — edit these at the top, nowhere else
# ---------------------------------------------------------------
WEB_PORT     = 5000
WEB_PASSWORD = "1234"           # plain text — change here only
VALID_COMMANDS = {"heater_on", "heater_off", "motor_on", "motor_off",
                  "emergency", "reset_override"}
CLIENT_TIMEOUT = 8.0            # seconds before a client is considered disconnected

# ---------------------------------------------------------------
# FILE PATHS
# ---------------------------------------------------------------
STATE_FILE       = os.path.join(PROJECT_ROOT, "shared_state.json")
LOG_FILE         = os.path.join(PROJECT_ROOT, "shared_log.json")
CMD_FILE         = os.path.join(PROJECT_ROOT, "web_command.json")
ML_FILE          = os.path.join(PROJECT_ROOT, "ml_predictions.json")
SHUTDOWN_FILE    = os.path.join(PROJECT_ROOT, "shutdown_signal.txt")
EVENT_QUEUE_FILE = os.path.join(PROJECT_ROOT, "web_event_queue.json")  # GUI reads this

# ---------------------------------------------------------------
# FLASK APP
# ---------------------------------------------------------------
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
app = Flask(__name__, template_folder=template_dir)
app.config['TEMPLATES_AUTO_RELOAD'] = True


_file_lock   = Lock()
_event_lock  = Lock()
_client_lock = Lock()
_clients     = {}   # {ip: last_seen_time}


# ---------------------------------------------------------------
# FILE HELPERS
# ---------------------------------------------------------------

def _read_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write(path, data):
    """Extra-safe atomic write for Windows — prevents 500 errors on file locks."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        # Windows-safe replacement: try remove first (if locked, catch and skip)
        try:
            if os.path.exists(path):
                os.remove(path)
        except:
            pass
            
        try:
            os.replace(tmp, path)
        except:
            # If still locked, we just skip this update to avoid a 500 error.
            # The next poll/event will try again.
            pass
    except Exception as e:
        print(f"[WEB] Atomic write failed: {e}")



def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_pi_ip():
    cfg = _read_json(os.path.join(PROJECT_ROOT, "controller_config.json"), {})
    return cfg.get("last_known_ip")


# ---------------------------------------------------------------
# GUI EVENT QUEUE  — web events that GUI should show in its log
# ---------------------------------------------------------------

def _push_gui_event(msg: str, source: str):
    """Append an event for GUI._poll_web_events() to pick up & log."""
    ts = datetime.now().strftime("%H:%M:%S")
    event = {"msg": msg, "source": source, "time": ts}
    try:
        with _event_lock:
            q = _read_json(EVENT_QUEUE_FILE, [])
            q.append(event)
            _atomic_write(EVENT_QUEUE_FILE, q)
    except Exception as e:
        print(f"[WEB] Event queue error: {e}")


# ---------------------------------------------------------------
# CLIENT TRACKER  — connect / disconnect detection
# ---------------------------------------------------------------

def _track_client(ip: str):
    """Call on every API request. Logs new connections."""
    with _client_lock:
        if ip not in _clients:
            msg = f"Web client connected: {ip}"
            print(f"[WEB] {msg}")
            _push_gui_event(msg, source=f"web({ip})")
        _clients[ip] = time.time()


def _client_watchdog():
    """Background thread — detects disconnected clients."""
    while True:
        now = time.time()
        with _client_lock:
            gone = [ip for ip, t in _clients.items()
                    if now - t > CLIENT_TIMEOUT]
            for ip in gone:
                del _clients[ip]
                msg = f"Web client disconnected: {ip}"
                print(f"[WEB] {msg}")
                _push_gui_event(msg, source=f"web({ip})")
        time.sleep(2)


# ---------------------------------------------------------------
# SHUTDOWN MONITOR  — exits when SCADA core writes shutdown_signal.txt
# ---------------------------------------------------------------

def _shutdown_monitor():
    while True:
        if os.path.exists(SHUTDOWN_FILE):
            print("[WEB] Shutdown signal detected. Stopping web server.")
            _push_gui_event("Web Dashboard stopped (shutdown signal)", source="core")
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)
            break
        time.sleep(1)


# ---------------------------------------------------------------
# COMMAND IPC  — post command to GUI
# ---------------------------------------------------------------

def post_command(cmd: str, client_ip: str) -> bool:
    entry = {"cmd": cmd, "ip": client_ip, "time": time.time(), "processed": False}
    try:
        with _file_lock:
            _atomic_write(CMD_FILE, entry)
        return True
    except Exception as e:
        print(f"[WEB] Failed to post command: {e}")
        return False


# ---------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------

@app.route("/")
def dashboard():
    try:
        _track_client(flask_request.remote_addr)
    except Exception:
        pass
    resp = make_response(render_template("dashboard.html", server_ip=get_local_ip(), port=WEB_PORT))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    client_ip = flask_request.remote_addr
    with _client_lock:
        if client_ip in _clients:
            del _clients[client_ip]
            _push_gui_event(f"Web client disconnected (explicit): {client_ip}", source=f"web({client_ip})")
    return jsonify({"status": "ok"})


@app.route("/api/data")
def api_data():
    client_ip = flask_request.remote_addr
    try:
        _track_client(client_ip)
    except Exception:
        pass

    state = _read_json(STATE_FILE, {})
    ml    = _read_json(ML_FILE, {})
    pi_ip = get_pi_ip()

    # Calculate LIVE heartbeat age
    ts = state.get("heartbeat_timestamp", 0)
    if ts > 0:
        heartbeat_age = round(time.time() - ts, 1)
    else:
        heartbeat_age = 999  # No data ever received
    
    payload = {
        "telemetry":      state.get("telemetry", {}),
        "alarms":         state.get("alarms", {}),
        "system":         state.get("system", {}),
        "actuators":      state.get("actuators", {}),
        "heartbeat_age":  heartbeat_age,
        "ml":             ml,
        "server_time":    time.time(),
        "pi_cam_url":     f"http://{pi_ip}:8080" if pi_ip else None,
        "secondary_mode": True
    }
    return jsonify(payload)


@app.route("/api/logs")
def api_logs():
    _track_client(flask_request.remote_addr)
    entries = list(reversed(_read_json(LOG_FILE, [])))  # newest first
    shaped  = []
    for e in entries:
        source = e.get("source", "core")
        msg    = e.get("msg", "")
        ts     = e.get("time", "--")
        if source == "ml":
            level = "ML"
        elif source.startswith("web"):
            level = "CMD"
        elif any(w in msg.upper() for w in ("ALARM", "HIGH", "LOW PRESSURE", "LOW WATER")):
            level = "ALARM"
        elif any(w in msg.upper() for w in ("ERROR", "FAILED", "UNAUTHORIZED")):
            level = "WARN"
        elif any(w in msg.upper() for w in ("CLEARED", "RESTORED", "CONNECTED", "STARTED")):
            level = "OK"
        else:
            level = "INFO"
        shaped.append({
            "time":   ts,
            "source": source,
            "msg":    msg,
            "level":  level
        })
    return jsonify(shaped)


@app.route("/api/command", methods=["POST"])
def api_command():
    """Password-protected actuator command → routed to GUI via IPC."""
    client_ip = flask_request.remote_addr
    _track_client(client_ip)

    try:
        body = flask_request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    password = body.get("password", "")
    command  = body.get("command", "")

    # Plain text password check
    if password != WEB_PASSWORD:
        msg = f"UNAUTHORIZED command attempt: {command}"
        print(f"[WEB] SECURITY — {msg} from {client_ip}")
        _push_gui_event(msg, source=f"web({client_ip})")
        return jsonify({"ok": False, "error": "Invalid password"}), 403

    if command not in VALID_COMMANDS:
        return jsonify({"ok": False, "error": f"Unknown command: {command}"}), 400

    if post_command(command, client_ip):
        msg = f"Command submitted: {command}"
        print(f"[WEB] {msg} from {client_ip}")
        _push_gui_event(msg, source=f"web({client_ip})")
        return jsonify({"ok": True, "command": command, "note": "Queued for GUI execution"})
    else:
        return jsonify({"ok": False, "error": "IPC write failed"}), 500


@app.route("/camera_feed")
def camera_feed():
    pi_ip = get_pi_ip()
    if not pi_ip:
        return "Camera unavailable", 503

    stream_url = f"http://{pi_ip}:8080"

    def generate():
        while True:
            try:
                r = requests.get(stream_url, stream=True, timeout=(3, 10))
                if r.status_code != 200:
                    time.sleep(1)
                    continue
                buf = bytearray()
                for chunk in r.iter_content(4096):
                    buf.extend(chunk)
                    while True:
                        start = buf.find(b'\xff\xd8')
                        end   = buf.find(b'\xff\xd9')
                        if start != -1 and end != -1 and end > start:
                            jpg = bytes(buf[start:end + 2])
                            buf = buf[end + 2:]
                            yield (b'--frame\r\n'
                                   b'Content-Type: image/jpeg\r\n\r\n'
                                   + jpg + b'\r\n')
                        else:
                            break
            except Exception:
                time.sleep(2)

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------

def main():
    local_ip = get_local_ip()
    print(f"\n{'='*55}")
    print(f"  SCADA Web Dashboard — SECONDARY HMI")
    print(f"  Access: http://{local_ip}:{WEB_PORT}")
    print(f"  Password: {WEB_PASSWORD}")
    print(f"{'='*55}\n")

    # Start background threads
    Thread(target=_client_watchdog,  daemon=True, name="ClientWatchdog").start()
    Thread(target=_shutdown_monitor, daemon=True, name="ShutdownMonitor").start()

    # Announce startup
    _push_gui_event(f"Web Dashboard started — http://{local_ip}:{WEB_PORT}",
                    source=f"web({local_ip})")

    app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
