import paramiko
import socket
import subprocess
import json
import time
import getpass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

CONFIG_FILE = "controller_config.json"


# ---------------------------------------------------
# Timestamp helper
# ---------------------------------------------------
def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------
# Logging
# ---------------------------------------------------
def log(level, msg):
    print(f"[{now()}][{level}] {msg}")

def log_info(msg): log("INFO", msg)
def log_warn(msg): log("WARN", msg)
def log_error(msg): log("ERROR", msg)


# ---------------------------------------------------
# Load / Save config
# ---------------------------------------------------
def load_config():

    default_cfg = {
        "hostname": "raspberrypi",
        "username": "raspberrypi",
        "ssh_port": 22,
        "last_known_ip": None,
        "auth_method": None,
        "ssh_key_installed": False,
        "last_connection": None,
        "last_discovery": None
    }

    try:

        with open(CONFIG_FILE) as f:
            cfg = json.load(f)

        for k,v in default_cfg.items():
            if k not in cfg:
                cfg[k] = v

        return cfg

    except:

        log_warn("Config file missing. Creating default.")

        with open(CONFIG_FILE,"w") as f:
            json.dump(default_cfg,f,indent=2)

        return default_cfg


def save_config():
    with open(CONFIG_FILE,"w") as f:
        json.dump(CONFIG,f,indent=2)


CONFIG = load_config()

HOSTNAME = CONFIG["hostname"]
USERNAME = CONFIG["username"]
SSH_PORT = CONFIG["ssh_port"]


# ---------------------------------------------------
# Ping
# ---------------------------------------------------
def ping_host(ip):

    try:
        subprocess.check_output(
            ["ping","-n","1","-w","300",ip],
            stderr=subprocess.DEVNULL
        )
        return True
    except:
        return False


# ---------------------------------------------------
# SSH port check
# ---------------------------------------------------
def port_open(ip,timeout=0.25):

    try:
        s = socket.create_connection((ip,SSH_PORT),timeout)
        s.close()
        return True
    except:
        return False


# ---------------------------------------------------
# Detect subnet
# ---------------------------------------------------
def get_local_subnet():

    s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8",80))
        ip = s.getsockname()[0]
    finally:
        s.close()

    subnet=".".join(ip.split(".")[:3])

    log_info(f"Local IP {ip}")
    log_info(f"Subnet {subnet}.0/24")

    return subnet


# ---------------------------------------------------
# Cached IP attempt
# ---------------------------------------------------
def try_cached_ip():

    ip = CONFIG["last_known_ip"]

    if not ip:
        log_info("No cached IP")
        return None

    log_info(f"Trying cached IP {ip}")

    # Check if SSH port is reachable
    if not port_open(ip):
        log_warn("SSH port not reachable")
        log_warn("Possible reasons:")
        log_warn(" • Raspberry Pi is offline")
        log_warn(" • Network changed")
        log_warn(" • SSH service stopped")
        log_warn("Try running on Pi: sudo systemctl restart ssh")
        return None

    log_info("Cached IP valid")

    return ip

# ---------------------------------------------------
# mDNS
# ---------------------------------------------------
def try_mdns():

    name=HOSTNAME+".local"

    log_info(f"Trying mDNS {name}")

    try:

        ip=socket.gethostbyname(name)

        log_info(f"Resolved to {ip}")

        if port_open(ip):

            CONFIG["last_discovery"]=now()
            save_config()

            return ip

    except:

        log_warn("mDNS not found")

    return None


# ---------------------------------------------------
# Subnet scan
# ---------------------------------------------------
def scan_subnet(subnet):

    log_info("Scanning subnet for SSH")

    ips=[f"{subnet}.{i}" for i in range(1,255)]
    found=[]

    def probe(ip):
        if port_open(ip,0.3):
            return ip
        return None

    with ThreadPoolExecutor(max_workers=60) as ex:

        for r in ex.map(probe,ips):
            if r:
                found.append(r)

    return found


# ---------------------------------------------------
# Discovery
# ---------------------------------------------------
def discover():

    ip=try_mdns()

    if ip:
        return ip

    subnet=get_local_subnet()

    hosts=scan_subnet(subnet)

    log_info(f"SSH hosts {hosts}")

    if hosts:

        CONFIG["last_discovery"]=now()
        save_config()

        return hosts[0]

    return None


# ---------------------------------------------------
# Install SSH key
# ---------------------------------------------------
def install_ssh_key(ssh):

    try:

        import os

        key_path = os.path.expanduser("~/.ssh/id_ed25519.pub")

        # fallback if ed25519 not present
        if not os.path.exists(key_path):
            key_path = os.path.expanduser("~/.ssh/id_rsa.pub")

        if not os.path.exists(key_path):
            log_warn("No public SSH key found (~/.ssh/id_ed25519.pub or id_rsa.pub)")
            return

        with open(key_path, "r") as f:
            pubkey = f.read().strip()

        cmd = (
            'mkdir -p ~/.ssh && '
            'chmod 700 ~/.ssh && '
            f'echo "{pubkey}" >> ~/.ssh/authorized_keys && '
            'chmod 600 ~/.ssh/authorized_keys'
        )

        _stdin, _stdout, stderr_ch = ssh.exec_command(cmd)

        time.sleep(1)

        err = stderr_ch.read().decode().strip()
        if err:
            log_warn(f"SSH key install command stderr: {err}")
            return

        CONFIG["ssh_key_installed"] = True
        CONFIG["auth_method"] = "key"

        save_config()

        log_info("SSH key installed")

    except Exception as e:

        log_warn("SSH key install failed")
        log_warn(str(e))
        
# ---------------------------------------------------
# verify key is added
# ---------------------------------------------------
def verify_key_login(ip):

    log_info("Verifying SSH key login")

    test = paramiko.SSHClient()
    test.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:

        test.connect(
            hostname=ip,
            username=USERNAME,
            timeout=5,
            allow_agent=True,
            look_for_keys=True
        )

        log_info("SSH key verified successfully")

        test.close()

        CONFIG["ssh_key_installed"] = True
        CONFIG["auth_method"] = "key"
        save_config()

        return True

    except Exception as e:

        log_warn("SSH key verification failed")

        log_warn(str(e))

        return False
    
# ---------------------------------------------------
# SSH connect
# ---------------------------------------------------
def connect(ip):

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # --- Attempt key-based login first ---
    for attempt in range(3):

        try:

            log_info(f"Connect attempt {attempt+1} (key)")

            ssh.connect(
                hostname=ip,
                username=USERNAME,
                timeout=5,
                allow_agent=True,
                look_for_keys=True
            )

            CONFIG["auth_method"] = "key"
            CONFIG["last_connection"] = now()
            save_config()

            log_info("Logged in using SSH key")

            return ssh

        except Exception:

            log_warn(f"Key login attempt {attempt+1} failed")
            time.sleep(1)

    # --- Key login exhausted: fall back to password once ---
    log_warn("Key login failed after 3 attempts. Falling back to password.")

    password = getpass.getpass("Enter Pi password: ")

    try:

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        ssh.connect(
            hostname=ip,
            username=USERNAME,
            password=password,
            timeout=5
        )

        CONFIG["auth_method"] = "password"
        CONFIG["last_connection"] = now()
        save_config()

        log_info("Logged in using password")

        install_ssh_key(ssh)

        # verify the key was accepted before closing
        key_ok = verify_key_login(ip)

        ssh.close()

        if key_ok:
            # reconnect using the newly installed key
            try:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=ip,
                    username=USERNAME,
                    timeout=5,
                    allow_agent=True,
                    look_for_keys=True
                )
                log_info("Reconnected using installed SSH key")
                return ssh
            except Exception as e:
                log_warn(f"Key reconnect failed: {e}. Re-connecting with password.")

        # key not verified or reconnect failed — connect again with password
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=ip,
            username=USERNAME,
            password=password,
            timeout=5
        )
        log_info("Session continued with password")
        return ssh

    except Exception as e:

        log_error(f"Password login failed: {e}")

    return None



# ---------------------------------------------------
# API INTERFACE:
# ---------------------------------------------------
def connect_raspberry_pi():

    log_info("Connecting to Raspberry Pi")

    ip = try_cached_ip()

    if not ip:

        log_info("Cached IP failed. Starting discovery.")

        ip = discover()

    if not ip:

        log_warn("Auto discovery failed")
        ip = input("Enter Pi IP: ")

    log_info(f"Target {ip}")

    ssh = connect(ip)

    if ssh is None:

        log_error("Connection failed")
        return None, None

    CONFIG["last_known_ip"] = ip
    save_config()

    return ssh, ip

# ---------------------------------------------------
# Main
# ---------------------------------------------------
if __name__=="__main__":

    print("\n=== Raspberry Pi SSH Controller ===\n")

    ip=try_cached_ip()

    if not ip:

        log_info("Starting discovery")

        ip=discover()

    if not ip:

        log_warn("Auto discovery failed")

        ip=input("Enter Pi IP: ")

    log_info(f"Target {ip}")

    ssh=connect(ip)

    if ssh:

        stdin,stdout,stderr=ssh.exec_command("hostname")

        log_info(f"Connected to {stdout.read().decode().strip()}")

        CONFIG["last_known_ip"]=ip
        save_config()

        ssh.close()

    else:

        log_error("Connection failed")

# ==========================================================
# RASPBERRY PI SSH CONTROLLER - SYSTEM ARCHITECTURE (MANDATORY KEEP IT)
# ==========================================================
#
# PURPOSE
# -------
# Automatically discover and connect to a Raspberry Pi over SSH.
# Provides robust connection management with fallback mechanisms
# and exposes a simple API for other system modules.
#
#
# ==========================================================
# PUBLIC API (FOR OTHER MODULES)
# ==========================================================
#
# Main interface exposed by this controller:
#
#     connect_raspberry_pi()
#
#
# Function behavior:
#
#     ssh_client, ip = connect_raspberry_pi()
#
#
# Returns:
#
#     ssh_client  → active Paramiko SSH client
#     ip          → detected Raspberry Pi IP address
#
#
# This API hides all connection complexity from other modules.
#
# Modules such as:
#
#     VIDEO_CONTROLLER
#     SENSOR_CONTROLLER
#     GUI_CLIENT
#
# simply call the API instead of handling discovery themselves.
#
#
# ==========================================================
# CONTROLLER STATE FLOW
# ==========================================================
#
# START
#   │
#   │
#   ├── Load configuration (controller_config.json)
#   │       hostname
#   │       username
#   │       ssh_port
#   │       last_known_ip
#   │       auth_method
#   │
#   │
#   ├── Try Cached IP
#   │       │
#   │       ├── SSH port reachable → connect
#   │       │
#   │       └── Fail → start discovery
#   │
#   │
#   ├── mDNS Discovery
#   │       hostname.local
#   │       │
#   │       ├── Found → connect
#   │       │
#   │       └── Not found → continue
#   │
#   │
#   ├── Subnet Detection
#   │       determine active network
#   │       example:
#   │       10.38.248.0/24
#   │
#   │
#   ├── Parallel SSH Port Scan
#   │       scan subnet for port 22
#   │       identify SSH-enabled hosts
#   │
#   │
#   ├── Candidate Host Found
#   │       connect to first valid SSH host
#   │
#   │
#   ├── Authentication Phase
#   │       │
#   │       ├── SSH key authentication
#   │       │       success → connected
#   │       │
#   │       └── Password authentication
#   │               success → install SSH key
#   │               verify key login
#   │
#   │
#   ├── Connection Established
#   │       execute test command
#   │       example: hostname
#   │
#   │
#   ├── Update Controller State
#   │       save:
#   │       last_known_ip
#   │       last_connection timestamp
#   │       auth_method
#   │
#   │
#   └── Return SSH session via API
#
#
# ==========================================================
# FAILURE HANDLING
# ==========================================================
#
# Device unreachable
#       → network issue or Pi powered off
#
# SSH port closed
#       → SSH service stopped
#
# Authentication failure
#       → wrong username or password
#
# Discovery failure
#       → manual IP input fallback
#
#
# ==========================================================
# DISCOVERY STRATEGY
# ==========================================================
#
# 1) Cached IP (fastest)
# 2) mDNS hostname resolution
# 3) Subnet scanning
# 4) Manual IP input
#
#
# ==========================================================
# SECURITY MODEL
# ==========================================================
#
# First connection:
#
#     password authentication
#
# After SSH key installation:
#
#     SSH public key authentication
#
#
# ==========================================================
# SYSTEM DESIGN PRINCIPLE
# ==========================================================
#
# Progressive discovery:
#
# memory → self-advertised identity → active probing → manual override
#
#
# ==========================================================
# SYSTEM INTEGRATION MODEL
# ==========================================================
#
#                GUI_CLIENT
#                     │
#                     ▼
#             VIDEO_CONTROLLER
#                     │
#                     ▼
#             SSH_CONTROLLER
#                     │
#                     ▼
#                Raspberry Pi
#
#
# SSH_CONTROLLER acts as the connection backbone of the system.
#
# All other modules obtain their SSH session through the
# connect_raspberry_pi() API instead of implementing their own
# discovery or authentication logic.
#
#
# This separation ensures:
#
# • Centralized connection management
# • Reduced duplicated network logic
# • Easier debugging and maintenance
# • Scalable architecture for future modules
#
# ==========================================================