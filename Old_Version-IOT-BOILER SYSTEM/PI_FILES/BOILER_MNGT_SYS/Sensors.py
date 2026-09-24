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
                continue

            seq, ts, dist, pres, crc = struct.unpack(PACKET_STRUCT, decoded)

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

    def on_message(client, userdata, msg):

        try:
            cmd = json.loads(msg.payload.decode())

            # emergency must always win
            if cmd.get("cmd") == "emergency":
                controller.ssh_emergency()
                return

            controller.process_command(cmd)

        except Exception as e:
            print("Command error:", e)

    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe("boiler/command")

    shared["system"]["mqtt_status"] = "CONNECTED"

    # continuous MQTT thread
    client.loop_start()

    while True:

        payload = {
            "heartbeat": time.time(),
            "arduino": shared.get("arduino"),
            "dht": dict(shared.get("dht", {})),
            "system": dict(shared.get("system", {})),
            "actuator": controller.state
        }

        client.publish(MQTT_TOPIC, json.dumps(payload), retain=True)

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

    procs = {
        "arduino": mp.Process(target=arduino_process, args=(shared,)),
        "dht": mp.Process(target=dht_process, args=(shared,)),
        "mqtt": mp.Process(target=mqtt_process, args=(shared,))
    }

    for p in procs.values():
        p.start()

    while True:

        for name, p in procs.items():

            if not p.is_alive():

                print("Restarting process:", name)

                new = mp.Process(target=p._target, args=p._args)
                procs[name] = new
                new.start()

        print("\n===== SYSTEM HEALTH =====")

        mem = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)

        print("Memory:", round(mem, 2), "MB")
        print(dict(shared["system"]))

        print("=========================")

        time.sleep(5)


if __name__ == "__main__":

    print("\nStarting Sensor Supervisor System\n")

    main()
