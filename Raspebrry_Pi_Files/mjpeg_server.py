import subprocess
import threading
import time
import json
import os
import signal
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = 8080

latest_frame = None
frame_condition = threading.Condition()

encoder_proc = None
encoder_lock = threading.Lock()

frame_timestamp = 0
encoder_restarts = 0

cached_temp = "0"
cached_cpu = "0"
last_stat_time = 0


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- SYSTEM STATS ----------------

def get_system_stats():

    global cached_temp, cached_cpu, last_stat_time

    now = time.time()

    if now - last_stat_time > 1:

        try:
            cached_temp = subprocess.check_output(
                ["vcgencmd", "measure_temp"]
            ).decode().split("=")[1].replace("'C\n", "")
        except:
            cached_temp = "0"

        try:
            cpu_line = subprocess.check_output(
                "top -bn1 | grep 'Cpu(s)'",
                shell=True
            ).decode()

            # Example: %Cpu(s):  1.5 us,  0.5 sy,  0.0 ni, 98.0 id...
            # Split by ',' and extract 'id' or just sum 'us' and 'sy'
            # Here we just grab the first number (user CPU) and format it as %
            parts = cpu_line.replace('%Cpu(s):', '').split(',')
            user_cpu = parts[0].replace('us', '').strip()
            cached_cpu = f"{user_cpu}%"
        except:
            cached_cpu = "0%"

        last_stat_time = now

    return {
        "temperature": cached_temp,
        "cpu": cached_cpu,
        "encoder_restarts": encoder_restarts,
        "timestamp": now
    }


# ---------------- ENCODER CONTROL ----------------

def start_encoder():

    global encoder_proc
    global encoder_restarts

    log("Starting encoder")

    encoder_proc = subprocess.Popen(
        [
            "rpicam-vid",
            "-t","0",
            "--width","640",
            "--height","480",
            "--framerate","30",
            "--codec","mjpeg",
            "--nopreview",
            "--flush",
            "-o","-"
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        preexec_fn=os.setsid
    )

    encoder_restarts += 1


def stop_encoder():

    global encoder_proc

    if encoder_proc:
        try:
            os.killpg(os.getpgid(encoder_proc.pid), signal.SIGKILL)
        except:
            pass

        encoder_proc = None


# ---------------- FRAME CAPTURE ----------------

def capture_thread():

    global latest_frame
    global frame_timestamp

    buffer = bytearray()

    while True:

        chunk = encoder_proc.stdout.read(8192)

        if not chunk:
            time.sleep(0.002)
            continue

        buffer.extend(chunk)

        start = buffer.find(b'\xff\xd8')
        end = buffer.find(b'\xff\xd9', start+2)

        if start != -1 and end != -1:

            frame = bytes(buffer[start:end+2])
            del buffer[:end+2]

            frame_timestamp = time.time()

            with frame_condition:
                latest_frame = frame
                frame_condition.notify_all()


# ---------------- HTTP SERVER ----------------

class MJPEGHandler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        return

    def do_GET(self):

        if self.path == "/health":

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if self.path == "/stats":

            stats = get_system_stats()
            data = json.dumps(stats).encode()

            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()

            self.wfile.write(data)
            return

        if self.path != "/":
            self.send_error(404)
            return

        log(f"Viewer connected {self.client_address}")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=frame"
        )
        self.end_headers()

        self.connection.setsockopt(
            socket.IPPROTO_TCP,
            socket.TCP_NODELAY,
            1
        )

        try:

            while True:

                with frame_condition:
                    frame_condition.wait()
                    frame = latest_frame

                if frame is None:
                    continue

                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")

        except:
            log("Viewer disconnected")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------- START ----------------

log("Starting camera system")

start_encoder()

threading.Thread(
    target=capture_thread,
    daemon=True
).start()

server = ThreadedHTTPServer(("",PORT),MJPEGHandler)

try:
    server.serve_forever()

finally:

    log("Shutdown")

    stop_encoder()
