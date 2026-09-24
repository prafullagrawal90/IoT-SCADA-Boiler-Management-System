import time
import json
import serial
import multiprocessing as mp
import paho.mqtt.client as mqtt
import struct
import os
import psutil

import board
import adafruit_dht

from OutputController import OutputController


MQTT_BROKER = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "boiler/sensors"

ARDUINO_PORT = "/dev/boiler_arduino"
BAUDRATE = 115200

PACKET_STRUCT = "<H I f f H"
PACKET_SIZE = struct.calcsize(PACKET_STRUCT)


# -------------------------------------------------
# CRC16
# -------------------------------------------------

def crc16(data):

    crc = 0xFFFF

    for b in data:

        crc ^= b

        for _ in range(8):

            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1

    return crc


# -------------------------------------------------
# COBS Decode
# -------------------------------------------------

def cobs_decode(data):

    out = bytearray()
    i = 0

    while i < len(data):

        code = data[i]
        i += 1

        for j in range(code - 1):
            out.append(data[i])
            i += 1

        if code < 0xFF and i < len(data):
            out.append(0)

    return out


# -------------------------------------------------
# ARDUINO PROCESS
# -------------------------------------------------

def arduino_process(shared):

    ser = None
    last_seq = None

    packet_counter = 0
    rate_timer = time.time()

    while True:

        try:

            if ser is None:

                try:

                    ser = serial.Serial(ARDUINO_PORT, BAUDRATE, timeout=1)
                    time.sleep(2)

                    print("Arduino connected")

                    shared["system"]["arduino_status"] = "CONNECTED"

                except:

                    shared["system"]["arduino_status"] = "SEARCHING"
                    time.sleep(2)
                    continue

            frame = ser.read_until(b'\x00')

            if not frame:
                continue

            frame = frame[:-1]

            decoded = cobs_decode(frame)

            if len(decoded) != PACKET_SIZE:
                print("invalid packet size",len(decoded))
                continue

            seq, ts, dist, pres, crc = struct.unpack(PACKET_STRUCT, decoded)
            print(f"data-> seq:{seq} dist:{dist:.2f} pressure:{pres:.2f}")
            if crc16(decoded[:-2]) != crc:
                continue

            packet_counter += 1

            if last_seq is not None and seq != (last_seq + 1) & 0xFFFF:
                print("⚠ Packet loss")

            last_seq = seq

            shared["arduino"] = {
                "timestamp": str(ts),
                "sequence": seq,
                "distance": dist,
                "pressure": pres
            }

            if time.time() - rate_timer >= 5:

                rate = packet_counter / 5
                print(f"Packet rate: {rate:.2f} Hz")

                packet_counter = 0
                rate_timer = time.time()

        except serial.SerialException:

            try:
                ser.close()
            except:
                pass

            ser = None
            shared["system"]["arduino_status"] = "ERROR"
            time.sleep(2)


# -------------------------------------------------
# DHT PROCESS
# -------------------------------------------------

def dht_process(shared):

    print("Initializing DHT sensors...")

    room = adafruit_dht.DHT11(board.D4, use_pulseio=False)
    boiler = adafruit_dht.DHT11(board.D17, use_pulseio=False)

    last_room = (None, None)
    last_boiler = (None, None)

    while True:

        try:

            rt = room.temperature
            rh = room.humidity

            if rt is not None and rh is not None:
                last_room = (rt, rh)

            bt = boiler.temperature
            bh = boiler.humidity

            if bt is not None and bh is not None:
                last_boiler = (bt, bh)

            shared["dht"] = {
                "room_temp": last_room[0],
                "room_hum": last_room[1],
                "boiler_temp": last_boiler[0],
                "boiler_hum": last_boiler[1]
            }

            shared["system"]["dht_status"] = "OK"

        except RuntimeError:

            shared["system"]["dht_status"] = "READING"

        except Exception as e:

            print("DHT error:", e)
            shared["system"]["dht_status"] = "ERROR"

        time.sleep(3)


# -------------------------------------------------
# MQTT PROCESS
# -------------------------------------------------
def mqtt_process(shared):

    controller = OutputController()
    client = mqtt.Client()
    client.reconnect_delay_set(min_delay=1, max_delay=5)
    def on_message(client, userdata, msg):

        try:
            cmd = json.loads(msg.payload.decode())

            result = controller.execute(cmd.get("cmd"))

            print("[API RESULT]", result)

        except Exception as e:
            print("Command error:", e)
            
    client.on_message = on_message
    
    while True:
        try:
            # 🔥 CONNECT
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            print("MQTT CONNECTED")
            break
        except Exception as e:
            print("MQTT CONNECTION RETRY:",e)
            time.sleep(2)
            
    client.subscribe("boiler/command")

    # 🔥 START NETWORK LOOP
    client.loop_start()

    shared["system"]["mqtt_status"] = "CONNECTED"

    print("MQTT PROCESS STARTED")

    while True:

        try:
            # ✅ SAFE SHARED ACCESS (NO BLOCKING)
            arduino_data = None
            dht_data = {}
            system_data = {}

            try:
                if shared.get("arduino"):
                    
                    arduino_data = dict(shared["arduino"])
            except Exception as e:
                arduino_data = None
                print("Arduino read error:", e)

            try:
                dht_data = dict(shared.get("dht", {}))
            except Exception as e:
                print("DHT read error:", e)

            try:
                system_data = dict(shared.get("system", {}))
            except Exception as e:
                print("System read error:", e)

            payload = {
                "heartbeat": time.time(),
                "arduino": arduino_data,
                "dht": dht_data,
                "system": system_data,
                "actuator": controller.get_state()
            }

            # ✅ MQTT PUBLISH (RELIABLE)
            client.publish(
                MQTT_TOPIC,
                json.dumps(payload),
                qos=1,
                retain=False
            )

            print("MQTT SENT")  # 🔥 DEBUG
            print("Payload:", payload)  # 🔥 FULL VISIBILITY

        except Exception as e:
            print("MQTT LOOP ERROR:", e)

        time.sleep(1)

# -------------------------------------------------
# MAIN SUPERVISOR
# -------------------------------------------------

def main():

    manager = mp.Manager()

    shared = manager.dict()

    shared["arduino"] = None
    shared["dht"] = manager.dict()

    shared["system"] = manager.dict({
        "arduino_status": "INIT",
        "dht_status": "INIT",
        "mqtt_status": "INIT"
    })

    process_defs = {
        "arduino": (arduino_process, (shared,)),
        "dht": (dht_process, (shared,)),
        "mqtt": (mqtt_process, (shared,))
    }

    procs = {}

    for name, (target, args) in process_defs.items():
        p = mp.Process(target=target, args=args)
        p.start()
        procs[name] = p

    while True:

        for name, p in procs.items():

            if not p.is_alive():

                print("Restarting process:", name)

                target, args = process_defs[name]

                new = mp.Process(target=target, args=args)
                new.start()

                procs[name] = new

        print("\n===== SYSTEM HEALTH =====")

        mem = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

        print("Memory:", round(mem, 2), "MB")
        print(dict(shared["system"]))

        print("=========================")

        time.sleep(5)


if __name__ == "__main__":

    print("\nStarting Sensor Supervisor System\n")

    main()
