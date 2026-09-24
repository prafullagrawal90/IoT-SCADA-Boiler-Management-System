import socket
import requests
import time

# used only in standalone mode
try:
    from ssh_manager import connect_raspberry_pi
except:
    connect_raspberry_pi = None


# ---------------------------------------------------
# Start camera server on Raspberry Pi via SSH
# ---------------------------------------------------
def start_camera_stream(ssh_client):

    print("Checking if camera server is already running...")

    stdin, stdout, stderr = ssh_client.exec_command("pgrep -f mjpeg_server.py")
    existing = stdout.read().decode().strip()

    if existing:
        print("Camera server already running (PID:", existing + ")")
        return

    print("Starting camera server on Raspberry Pi")

    start_script = """
pkill -f mjpeg_server.py || true
sleep 1
nohup /usr/bin/python3 /home/raspberrypi/Desktop/BOILER_MNGT_SYS/mjpeg_server.py > /tmp/camera.log 2>&1 &
"""

    ssh_client.exec_command(f"echo '{start_script}' > /tmp/start_camera.sh")
    ssh_client.exec_command("chmod +x /tmp/start_camera.sh")
    ssh_client.exec_command("bash /tmp/start_camera.sh")

    print("Start command executed")

    time.sleep(2)

    stdin, stdout, stderr = ssh_client.exec_command("pgrep -af mjpeg_server.py")
    output = stdout.read().decode().strip()

    if output:
        print("Camera server started:")
        print(output)
    else:
        print("Camera server still not running")

        stdin, stdout, stderr = ssh_client.exec_command("cat /tmp/camera.log")
        print("\n---- Camera log ----")
        print(stdout.read().decode())
        print("--------------------")


# ---------------------------------------------------
# Check if port 8080 is open
# ---------------------------------------------------
def check_port(ip, port=8080):

    try:
        with socket.create_connection((ip, port), timeout=3):
            return True
    except:
        return False


# ---------------------------------------------------
# Fast health endpoint check
# ---------------------------------------------------
def check_health(ip):

    try:
        r = requests.get(f"http://{ip}:8080/health", timeout=(2, 2))

        if r.status_code == 200:
            return True

    except:
        pass

    return False


# ---------------------------------------------------
# Wait until stream is available
# ---------------------------------------------------
def wait_for_camera_stream(ip, retries=20):

    for attempt in range(retries):

        try:
            r = requests.get(f"http://{ip}:8080/health", timeout=0.5)

            if r.status_code == 200:
                print("Camera server healthy")
                return {"status": "success"}

        except:
            pass

        time.sleep(0.2)

    return {"status": "failed"}


# ---------------------------------------------------
# Stream URL helper
# ---------------------------------------------------
def get_stream_url(ip):

    return f"http://{ip}:8080"


# ---------------------------------------------------
# Standalone test mode
# ---------------------------------------------------
if __name__ == "__main__":

    print("\n=== CAMERA SERVICE STARTED ===\n")

    if connect_raspberry_pi is None:
        print("SSH manager missing")
        exit(1)

    result = connect_raspberry_pi()

    if result is None:
        print("SSH connection failed")
        exit(1)

    ssh, ip = result

    start_camera_stream(ssh)

    print("Waiting for camera stream...")

    diag = wait_for_camera_stream(ip)

    print("Diagnostic Result:", diag)

    if diag["status"] == "success":
        print("Camera ready:", get_stream_url(ip))
    else:
        print("Camera failed to start")

    # KEEP SERVICE ALIVE
    try:
        while True:
            time.sleep(60)

    except KeyboardInterrupt:
        print("Camera service shutting down")
        try:
            ssh.exec_command("pkill -f mjpeg_server.py")
        except:
            pass

# ---------------------------------------------------
# Health check (DO NOT REMOVE)
# http://PI_IP:8080/health
# http://192.168.1.100:8080/health
#
# Video stream
# http://PI_IP:8080/
# http://192.168.1.100:8080/
# ---------------------------------------------------

# ============================================================
# VIDEO_CONTROLLER ARCHITECTURE
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# This module orchestrates the Raspberry Pi camera service.
#
# It does NOT process video frames itself.
# Instead it manages the lifecycle of the remote camera server.
#
# Responsibilities:
#
# 1) Obtain SSH connection from SSH_CONTROLLER
# 2) Check if mjpeg_server.py is already running
# 3) Start the camera server remotely if required
# 4) Verify server health using HTTP endpoint
# 5) Confirm MJPEG stream availability
# 6) Provide stream URL to the GUI/client
#
#
# ============================================================
# SYSTEM ARCHITECTURE OVERVIEW
# ============================================================
#
#            ┌───────────────────────────────┐
#            │            GUI CLIENT          │
#            │   (Desktop Monitoring System)  │
#            └───────────────┬───────────────┘
#                            │
#                            │ MJPEG HTTP Stream
#                            ▼
#                   http://PI_IP:8080/
#
#
#            ┌───────────────────────────────┐
#            │        VIDEO_CONTROLLER        │
#            │      (Service Orchestrator)    │
#            └───────────────┬───────────────┘
#                            │
#                            │ Uses SSH session
#                            ▼
#
#            ┌───────────────────────────────┐
#            │        SSH_CONTROLLER          │
#            │     (Connection Manager)       │
#            └───────────────┬───────────────┘
#                            │
#                            │ Secure Shell
#                            ▼
#
#            ┌───────────────────────────────┐
#            │         Raspberry Pi           │
#            │        mjpeg_server.py         │
#            └───────────────┬───────────────┘
#                            │
#                            │ Frame pipeline
#                            ▼
#
#            ┌───────────────────────────────┐
#            │          rpicam-vid            │
#            │   Hardware MJPEG Encoder       │
#            └───────────────┬───────────────┘
#                            │
#                            ▼
#
#            ┌───────────────────────────────┐
#            │       CSI Camera Sensor        │
#            └───────────────────────────────┘
#
#
# ============================================================
# COMPONENT RESPONSIBILITIES
# ============================================================
#
# 1) SSH_CONTROLLER
# ------------------------------------------------------------
# Establishes secure connection with Raspberry Pi.
#
# Responsibilities:
#
# - Discover Raspberry Pi on the network
# - Verify network reachability
# - Perform SSH authentication
# - Install SSH keys on first login
# - Maintain connection configuration
# - Return active SSH client session
#
#
# Main interface exposed to other modules:
#
#     connect_raspberry_pi()
#
# Returns:
#
#     ssh_client
#     raspberry_pi_ip
#
#
# ============================================================
#
# 2) VIDEO_CONTROLLER
# ------------------------------------------------------------
# Orchestrates the camera service lifecycle.
#
# Functions:
#
# start_camera_stream()
#
#     • Checks if mjpeg_server.py is already running
#     • If not running:
#           - kills stale processes
#           - launches server using nohup
#           - detaches process from SSH session
#
#
# wait_for_camera_stream()
#
#     • Polls /health endpoint
#     • Confirms HTTP service availability
#
#
# validate_stream()
#
#     • Optional fallback verification
#     • Reads MJPEG stream bytes
#     • Confirms JPEG frame boundaries
#
#
# check_health()
#
#     • Fast readiness probe
#
#           http://PI_IP:8080/health
#
#     • HTTP 200 indicates camera server ready
#
#
# get_stream_url()
#
#     • Returns MJPEG endpoint used by GUI
#
#
# ============================================================
# CAMERA SERVER (ON RASPBERRY PI)
# ============================================================
#
# mjpeg_server.py responsibilities:
#
# 1) Launch rpicam-vid hardware encoder
# 2) Capture MJPEG frames from stdout
# 3) Store latest frame in shared buffer
# 4) Serve frames to multiple HTTP clients
#
# Endpoints exposed:
#
#   /          → MJPEG video stream
#   /health    → service health check
#
#
# ============================================================
# STREAM PIPELINE
# ============================================================
#
# Camera Sensor
#      ↓
# rpicam-vid (hardware MJPEG encoder)
#      ↓
# MJPEG frame buffer
#      ↓
# Python HTTP server
#      ↓
# Multiple HTTP clients
#
#
# ============================================================
# MULTI-CLIENT STREAM MODEL
# ============================================================
#
# Only ONE hardware encoder runs:
#
#          rpicam-vid
#               │
#               ▼
#          frame buffer
#               │
#    ┌──────────┼──────────┐
#    ▼          ▼          ▼
# Client 1   Client 2   Client 3
#
# This prevents launching multiple encoders
# and keeps Raspberry Pi CPU usage minimal.
#
#
# ============================================================
# STARTUP SEQUENCE
# ============================================================
#
# GUI / Controller Start
#        ↓
# SSH_CONTROLLER.connect_raspberry_pi()
#        ↓
# SSH connection established
#        ↓
# VIDEO_CONTROLLER.start_camera_stream()
#        ↓
# Check if mjpeg_server.py running
#        ↓
# If NOT running → start camera server
#        ↓
# wait_for_camera_stream()
#        ↓
# /health endpoint returns HTTP 200
#        ↓
# MJPEG stream available
#        ↓
# GUI connects to stream
#
#
# ============================================================
# STREAM ENDPOINTS
# ============================================================
#
# Health Check
#
#     http://PI_IP:8080/health
#
# MJPEG Stream
#
#     http://PI_IP:8080/
# ============================================================