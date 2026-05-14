import subprocess
import time
import sys
import os
import signal
import socket

PYTHON = sys.executable

services = {
    "TELEMETRY": ["telemetry_service.py"],
    "CAMERA": ["camera_service.py"],
    "GUI": ["application_tools/GUI.py"],
    "VOICE_ALARM": ["application_tools/voice_alarm_system.py"],
    "ML_ENGINE": ["application_tools/ML.py"],
    "WEB_DASHBOARD": ["application_tools/web_dashboard/web_server.py"],
}


processes = {}
startup_time = time.time()
last_telemetry = time.time()
restart_times = {}   # tracks last restart timestamp per service name
RESTART_COOLDOWN = 10  # minimum seconds between restarts of the same service

# ---------------------------------------------------
# Internet Check (REAL connectivity, not LAN)
# ---------------------------------------------------
def has_internet():
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        return True
    except:
        return False


def cleanup_pi(ssh):

    print("[CORE] Cleaning remote processes...")

    commands = [
        "pkill -f Sensors.py",
        "pkill -f mjpeg_server.py"
    ]

    for cmd in commands:
        try:
            ssh.exec_command(cmd)
        except Exception as e:
            print("[CORE] Cleanup error:", e)

    # 🔥 restart mosquitto clean
    try:
        print("[CORE] Restarting Mosquitto...")
        ssh.exec_command("sudo systemctl restart mosquitto")
        time.sleep(2)
    except Exception as e:
        print("[CORE] Mosquitto restart error:", e)


def start_service(name, cmd):

    print(f"[CORE] Starting {name}")

    proc = subprocess.Popen([PYTHON] + cmd)

    return proc


def update_heartbeat():

    global last_telemetry

    hb_file = "telemetry_heartbeat.txt"

    if os.path.exists(hb_file):
        last_telemetry = os.path.getmtime(hb_file)


def shutdown_all():

    print("[CORE] Shutdown signal received")

    shutdown_order = ["WEB_DASHBOARD", "GUI", "ML_ENGINE", "VOICE_ALARM", "CAMERA", "TELEMETRY"]

    for name in shutdown_order:

        proc = processes.get(name)

        if proc and proc.poll() is None:

            print(f"[CORE] Stopping {name}")

            try:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=6)
            except:
                proc.kill()

    # FINAL SAFETY KILL
    for proc in processes.values():
        try:
            if proc.poll() is None:
                proc.kill()
        except:
            pass

    # FORCE REMOTE SHUTDOWN ON RASPBERRY PI
    try:
        from ssh_manager import connect_raspberry_pi

        ssh, ip = connect_raspberry_pi()
        cleanup_pi(ssh)
        print("[CORE] Forcing shutdown of remote services")
        time.sleep(1)
        ssh.exec_command("pkill -f Sensors.py")
        ssh.exec_command("pkill -f mjpeg_server.py")
        ssh.exec_command("pkill -f rpicam")
        ssh.exec_command("sudo -n systemctl stop mosquitto")
        #ssh.exec_command( "pkill -f Sensors.py; pkill -f mjpeg_server.py; pkill -f rpicam; sudo -n systemctl stop mosquitto")
        ssh.close()

    except Exception as e:
        print("[CORE] Remote shutdown failed:", e)

    if os.path.exists("shutdown_signal.txt"):
        os.remove("shutdown_signal.txt")

    print("[CORE] All services stopped")

    sys.exit(0)


def monitor():

    internet_lost_time = None
    global last_telemetry

    while True:

        # -----------------------------
        # SHUTDOWN SIGNAL
        # -----------------------------
        if os.path.exists("shutdown_signal.txt"):
            print("[DEBUG] shutdown_signal.txt detected")
            shutdown_all()

        # -----------------------------
        # RUNTIME INTERNET MONITOR (MOVE UP)
        # -----------------------------
        if not has_internet():

            if internet_lost_time is None:
                internet_lost_time = time.time()
                print("[CORE] ⚠️ Internet connection LOST")

            elif time.time() - internet_lost_time > 10:
                print("[CORE] ❌ Internet down for too long → SAFE SHUTDOWN")
                shutdown_all()

        else:
            if internet_lost_time is not None:
                print("[CORE] 🌐 Internet RESTORED")
                internet_lost_time = None

        # -----------------------------
        # PROCESS MONITORING
        # -----------------------------
        for name, proc in list(processes.items()):

            status = proc.poll()

            if status is not None:

                print(f"[CORE] {name} exited with code {status}")

                # Enforce cooldown — don't restart if we just did so recently
                now = time.time()
                last = restart_times.get(name, 0)
                if now - last < RESTART_COOLDOWN:
                    remaining = RESTART_COOLDOWN - (now - last)
                    print(f"[CORE] {name} restart suppressed (cooldown {remaining:.0f}s remaining)")
                    continue

                # Kill any lingering OS process before spawning a new one
                try:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=3)
                except Exception:
                    pass

                print(f"[CORE] Restarting {name}...")
                restart_times[name] = now
                processes[name] = start_service(name, services[name])

        # -----------------------------
        # TELEMETRY HEARTBEAT
        # -----------------------------
        heartbeat_file = "telemetry_heartbeat.txt"

        if os.path.exists(heartbeat_file):
            last_update = os.path.getmtime(heartbeat_file)

            if time.time() - startup_time > 20 and time.time() - last_update > 15:
                print("[CORE] ❌ Telemetry stalled → restarting")

                processes["TELEMETRY"].terminate()
                processes["TELEMETRY"] = start_service("TELEMETRY", services["TELEMETRY"])
        # -----------------------------
        # LOOP DELAY (LAST)
        # -----------------------------
        time.sleep(2)


def main():

    print("\n=== SCADA CORE SUPERVISOR ===\n")

    retries = 5 #INTERNET RETRIES
    for i in range(retries):
        if has_internet():
            break
        print(f"[CORE] 🌐 Waiting for internet... ({i+1}/{retries})")
        time.sleep(2)
    else:
        print("[CORE] ❌ Internet not available after retries. Exiting.")
        sys.exit(1)

    print("[CORE] ✅ Internet Connected - Starting system")

    # 🔥 CRITICAL FIX: remove stale shutdown signal
    if os.path.exists("shutdown_signal.txt"):
        os.remove("shutdown_signal.txt")

    # 🔥 CONNECT TO PI FIRST
    from ssh_manager import connect_raspberry_pi

    ssh, ip = connect_raspberry_pi()
    print(f"[CORE] Connected to Pi: {ip}")

    # 🔥 CLEAN ALL OLD PROCESSES
    cleanup_pi(ssh)
    ssh.close()
    # 🔥 STEP 1: START TELEMETRY FIRST
    processes["TELEMETRY"] = start_service("TELEMETRY", services["TELEMETRY"])

    print("[CORE] Waiting for telemetry bootstrap...")
    time.sleep(6)  # allow SSH + Mosquitto + Sensors to start

    # 🔥 STEP 2: START CAMERA AFTER SENSORS ARE READY
    processes["CAMERA"] = start_service("CAMERA", services["CAMERA"])

    time.sleep(2)

    processes["GUI"] = start_service("GUI", services["GUI"])
    processes["VOICE_ALARM"] = start_service("VOICE_ALARM", services["VOICE_ALARM"])
    processes["ML_ENGINE"] = start_service("ML_ENGINE", services["ML_ENGINE"])
    processes["WEB_DASHBOARD"] = start_service("WEB_DASHBOARD", services["WEB_DASHBOARD"])

    monitor()


if __name__ == "__main__":
    main()