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
