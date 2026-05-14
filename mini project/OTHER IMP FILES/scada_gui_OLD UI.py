import sys
import time
import json
import math
import cv2
import numpy as np
import requests
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QTextEdit, QPushButton,
    QGridLayout, QFrame, QGroupBox, QCheckBox
)
from PyQt5.QtGui import (
    QImage, QPixmap, QPainter,
    QColor, QPen, QFont,
    QLinearGradient
)

from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer, QRectF

import paho.mqtt.client as mqtt


# ---------------------------------------------------------
# COLORS
# ---------------------------------------------------------

GREEN="#27ae60"
YELLOW="#f1c40f"
RED="#e74c3c"


def status_style(color):
    return f"""
        background:{color};
        color:white;
        font-weight:bold;
        padding:6px;
        border-radius:4px;
    """


# ---------------------------------------------------------
# LOAD PI IP
# ---------------------------------------------------------

def load_pi_ip():

    try:
        with open("controller_config.json") as f:
            cfg=json.load(f)

        return cfg.get("last_known_ip")

    except:
        return None


BROKER=load_pi_ip()

while not BROKER:
    print("Waiting for Raspberry Pi IP...")
    time.sleep(2)
    BROKER=load_pi_ip()


MQTT_TOPIC="boiler/sensors"
CMD_TOPIC="boiler/command"

# ---------------------------------------------------------
# CAMERA THREAD
# ---------------------------------------------------------

class MJPEGStreamThread(QThread):

    frame_ready=pyqtSignal(np.ndarray)

    def __init__(self,url):

        super().__init__()

        self.url=url
        self.running=True

    def stop(self):

        self.running=False

    def run(self):

        while self.running:

            try:

                stream=requests.get(self.url,stream=True,timeout=(3,5))
                
                if stream.status_code != 200:
                    time.sleep(1)
                    continue

                buffer=bytearray()

                for chunk in stream.iter_content(4096):

                    if not self.running:
                        return

                    buffer.extend(chunk)

                    start=buffer.find(b'\xff\xd8')
                    end=buffer.find(b'\xff\xd9')

                    if start!=-1 and end!=-1:

                        jpg=buffer[start:end+2]
                        buffer=buffer[end+2:]

                        frame=cv2.imdecode(
                            np.frombuffer(jpg,dtype=np.uint8),
                            cv2.IMREAD_COLOR
                        )

                        if frame is not None:

                            self.frame_ready.emit(frame)

            except:

                time.sleep(1)


# ---------------------------------------------------------
# STATS THREAD (/stats endpoint)
# ---------------------------------------------------------

class StatsThread(QThread):

    stats_signal=pyqtSignal(dict)

    def run(self):

        while True:

            try:

                r=requests.get(f"http://{BROKER}:8080/stats",timeout=2)

                if r.status_code==200:

                    self.stats_signal.emit(r.json())

            except:
                pass

            time.sleep(2)


# ---------------------------------------------------------
# MQTT THREAD
# ---------------------------------------------------------

class MQTTClient(QThread):

    data_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()

        # create persistent client object
        self.client = mqtt.Client()

    def run(self):

        def on_connect(client,userdata,flags,rc):

            if rc == 0:
                client.subscribe(MQTT_TOPIC)

                self.data_signal.emit({
                    "system":{
                        "mqtt_status":"CONNECTED"
                    }
                })

        def on_message(client,userdata,msg):

            try:
                payload = json.loads(msg.payload.decode())

                self.data_signal.emit(payload)

            except:
                pass

        self.client.on_connect = on_connect
        self.client.on_message = on_message

        while True:

            try:

                self.client.connect(BROKER,1883,60)

                self.client.loop_forever()

            except:

                time.sleep(3)


# ---------------------------------------------------------
# IndicatorLamp FOR ACTUATIORS
# ---------------------------------------------------------
class IndicatorLamp(QWidget):

    def __init__(self, label):
        super().__init__()

        self.state = False

        layout = QVBoxLayout(self)

        self.title = QLabel(label)
        self.title.setAlignment(Qt.AlignCenter)

        self.lamp = QLabel()
        self.lamp.setFixedSize(24,24)
        self.lamp.setStyleSheet("border-radius:12px;background:#555;")

        layout.addWidget(self.title)
        layout.addWidget(self.lamp, alignment=Qt.AlignCenter)

    def set_state(self, state):

        self.state = state

        if state:
            color = "#2ecc71"
        else:
            color = "#555"

        self.lamp.setStyleSheet(
            f"border-radius:12px;background:{color};"
        )

# ---------------------------------------------------------
# GAUGE
# ---------------------------------------------------------

class Gauge(QWidget):

    def __init__(self,title,max_value):

        super().__init__()

        self.value=0
        self.target=0
        self.max=max_value
        self.title=title
        self.needle_color = QColor(80,180,80)
        self.green_limit = 0.7   # 70% safe by default
        self.red_limit = 1.0
        self.timer=QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(30)

        self.setMinimumSize(220,220)

    def set_value(self,v):

        self.target=v

    def animate(self):

        diff=self.target-self.value

        self.value+=diff*0.15

        self.update()

    def paintEvent(self, e):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()
        center = rect.center()

        radius = min(rect.width(), rect.height()) / 2 - 20

        # -----------------------------
        # OUTER GAUGE CIRCLE
        # -----------------------------

        painter.setPen(QPen(QColor(180,180,180),3))
        painter.drawEllipse(center, radius, radius)

        # -----------------------------
        # ALARM ZONES
        # -----------------------------

        arc_rect = QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2,
            radius * 2
        )

        pen = QPen()
        pen.setWidth(6)

        green_span = 270 * self.green_limit
        red_span = 270 * (self.red_limit - self.green_limit)

        # GREEN ZONE (SAFE)
        pen.setColor(QColor(80,180,80))
        painter.setPen(pen)
        painter.drawArc(
            arc_rect,
            int(135 * 16),
            int(-green_span * 16)
        )

        # RED ZONE (DANGER)
        pen.setColor(QColor(220,60,60))
        painter.setPen(pen)
        painter.drawArc(
            arc_rect,
            int((135 - green_span) * 16),
            int(-red_span * 16)
        )

        # -----------------------------
        # SCALE TICKS
        # -----------------------------

        painter.setPen(QPen(QColor(120,120,120),2))

        for i in range(11):

            angle = -135 + i * (270/10)
            rad = math.radians(angle)

            x1 = center.x() + radius * 0.85 * math.cos(rad)
            y1 = center.y() + radius * 0.85 * math.sin(rad)

            x2 = center.x() + radius * 0.95 * math.cos(rad)
            y2 = center.y() + radius * 0.95 * math.sin(rad)

            painter.drawLine(int(x1), int(y1), int(x2), int(y2))


        # -----------------------------
        # NEEDLE
        # -----------------------------

        angle = (self.value/self.max) * 270 - 135
        rad = math.radians(angle)

        x = center.x() + radius * 0.8 * math.cos(rad)
        y = center.y() + radius * 0.8 * math.sin(rad)

        painter.setPen(QPen(self.needle_color,4))
        painter.drawLine(center.x(), center.y(), int(x), int(y))
        # center hub (mechanical pivot)
        painter.setBrush(QColor(60,60,60))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center,6,6)

        # -----------------------------
        # GLASS REFLECTION EFFECT
        # -----------------------------

        glass = QLinearGradient(
            rect.topLeft(),
            rect.bottomLeft()
        )

        glass.setColorAt(0.0, QColor(255,255,255,120))
        glass.setColorAt(0.3, QColor(255,255,255,40))
        glass.setColorAt(0.6, QColor(255,255,255,0))

        painter.setBrush(glass)
        painter.setPen(Qt.NoPen)

        reflection_rect = QRectF(
            center.x()-radius*0.9,
            center.y()-radius*0.9,
            radius*1.8,
            radius*0.9
        )

        painter.drawEllipse(reflection_rect)

        # -----------------------------
        # LABEL + VALUE
        # -----------------------------

        painter.setFont(QFont("Arial",10))
        painter.setPen(QColor(50,50,50))

        painter.drawText(10,20,self.title)
        painter.drawText(10,40,str(round(self.value,2)))
# ---------------------------------------------------------
# TANK
# ---------------------------------------------------------

class TankWidget(QWidget):

    def __init__(self):

        super().__init__()

        self.level=0
        self.alarm=False
        self.blink=False

        self.timer=QTimer()
        self.timer.timeout.connect(self.toggle)
        self.timer.start(600)

    def toggle(self):

        self.blink=not self.blink

        self.update()

    def update_level(self,level,alarm):

        self.level=level
        self.alarm=alarm

        self.update()

    def paintEvent(self,e):

        painter=QPainter(self)

        rect=self.rect()

        tank=rect.adjusted(40,20,-40,-20)

        painter.setPen(QPen(QColor(180,180,180),4))

        painter.drawRect(tank)

        h=tank.height()

        water_height=int((self.level/150)*h)

        water_rect=tank.adjusted(
            0,
            h-water_height,
            0,
            0
        )

        gradient=QLinearGradient(
            water_rect.topLeft(),
            water_rect.bottomLeft()
        )

        if self.alarm and self.blink:

            gradient.setColorAt(0,QColor(255,0,0))
            gradient.setColorAt(1,QColor(180,0,0))

        else:

            gradient.setColorAt(0,QColor(100,200,255))
            gradient.setColorAt(1,QColor(20,120,200))

        painter.fillRect(water_rect,gradient)


# ---------------------------------------------------------
# MAIN WINDOW
# ---------------------------------------------------------

class SCADAWindow(QWidget):

    def __init__(self,stream_url):

        super().__init__()

        self.setWindowTitle("Boiler Monitoring SCADA Console")

        self.frame_counter = 0
        self.fps_timer = time.time()

        self.video=QLabel()
        self.video.setMinimumSize(900,500)

        # CPU PANEL

        self.cpu_temp=QLabel("CPU Temp: --")
        self.cpu_load=QLabel("CPU Load: --")
        self.encoder_label=QLabel("Encoder Restarts: --")
        self.timestamp_label=QLabel("Stats Time: --")
        self.fps=QLabel("FPS: --")

        cpu_panel=QHBoxLayout()
        cpu_panel.addWidget(self.cpu_temp)
        cpu_panel.addWidget(self.cpu_load)
        cpu_panel.addWidget(self.encoder_label)
        cpu_panel.addWidget(self.timestamp_label)
        cpu_panel.addWidget(self.fps)

        # STATUS BAR

        self.ssh_status=QLabel("SSH")
        self.mqtt_status=QLabel("MQTT")
        self.sensor_status=QLabel("Sensors")
        self.camera_status=QLabel("Camera")

        for s in [self.camera_status,self.mqtt_status,self.ssh_status,self.sensor_status]:
            s.setStyleSheet(status_style(YELLOW))

        status_bar=QHBoxLayout()
        status_bar.addWidget(self.ssh_status)
        status_bar.addWidget(self.mqtt_status)
        status_bar.addWidget(self.sensor_status)
        status_bar.addWidget(self.camera_status)

        self.ssh_status.setStyleSheet(status_style(GREEN))

        # LOG

        self.log=QTextEdit()
        self.log.setReadOnly(True)

        # SHUTDOWN

        self.shutdown_btn=QPushButton("Shutdown SCADA")
        self.shutdown_btn.clicked.connect(self.shutdown)

        # LEFT COLUMN

        left=QVBoxLayout()

        left.addWidget(self.video)
        left.addLayout(cpu_panel)
        left.addLayout(status_bar)
        left.addWidget(self.log)
        left.addWidget(self.shutdown_btn)

        # ------------------------------------------------
        # CONTROL PANEL (NEW)
        # ------------------------------------------------

        control_box=QGroupBox("Actuator Controls")
        control_layout=QVBoxLayout(control_box)

        # HEATER

        heater_row=QHBoxLayout()

        heater_label=QLabel("Heater")

        self.heater_switch=QCheckBox("Heater ON")

        self.heater_lamp=IndicatorLamp("")

        heater_row.addWidget(heater_label)
        heater_row.addWidget(self.heater_switch)
        heater_row.addWidget(self.heater_lamp)

        # MOTOR

        motor_row=QHBoxLayout()

        motor_label=QLabel("Motor")

        self.motor_switch=QCheckBox("Motor ON")

        self.motor_lamp=IndicatorLamp("")

        motor_row.addWidget(motor_label)
        motor_row.addWidget(self.motor_switch)
        motor_row.addWidget(self.motor_lamp)

        # EMERGENCY

        self.emergency=QPushButton("AZ-5 EMERGENCY STOP")
        self.emergency.setStyleSheet("background:#e74c3c;color:white;font-weight:bold")

        # RESET

        self.reset_emergency=QPushButton("RESET EMERGENCY")
        self.reset_emergency.setVisible(False)

        control_layout.addLayout(heater_row)
        control_layout.addLayout(motor_row)
        control_layout.addWidget(self.emergency)
        control_layout.addWidget(self.reset_emergency)
        control_layout.addStretch()

        # connections

        self.heater_switch.toggled.connect(self.heater_switch_changed)
        self.motor_switch.toggled.connect(self.motor_switch_changed)

        self.emergency.clicked.connect(lambda:self.send_cmd("emergency"))
        self.reset_emergency.clicked.connect(lambda:self.send_cmd("reset_override"))

        self.waiting_for_ack=False
        self.command_sent_time=0

        # ------------------------------------------------
        # RIGHT SIDE
        # ------------------------------------------------

        self.tank=TankWidget()

        self.g_level=Gauge("Water Level (cm)",150)
        self.g_pressure=Gauge("Pressure (bar)",15)
        self.g_boiler=Gauge("Boiler Temp (C)",120)
        self.g_room=Gauge("Room Temp (C)",50)

        gauges=QGridLayout()

        gauges.addWidget(self.g_level,0,0)
        gauges.addWidget(self.g_pressure,0,1)
        gauges.addWidget(self.g_boiler,1,0)
        gauges.addWidget(self.g_room,1,1)

        self.g_pressure.green_limit = 9/15
        self.g_boiler.green_limit = 40/120
        self.g_room.green_limit = 40/50
        self.g_level.green_limit = 0.8

        self.labels={}

        fields=[

            ("Distance","distance"),
            ("Pressure","pressure"),
            ("Boiler Temp","boiler_temp"),
            ("Room Temp","room_temp"),
            ("Boiler Hum","boiler_hum"),
            ("Room Hum","room_hum"),
            ("Arduino Status","arduino_status"),
            ("DHT Status","dht_status"),
            ("MQTT Status","mqtt_status"),
            ("Heartbeat","heartbeat")

        ]

        table=QGridLayout()

        row=0
        for name,key in fields:

            l=QLabel(name)
            v=QLabel("--")

            table.addWidget(l,row,0)
            table.addWidget(v,row,1)

            self.labels[key]=v

            row+=1

        right=QVBoxLayout()
        right.addWidget(self.tank)
        right.addLayout(gauges)
        right.addLayout(table)

        layout=QHBoxLayout()

        layout.addLayout(left,2)
        layout.addWidget(control_box,1)
        layout.addLayout(right,1)

        self.setLayout(layout)

        # THREADS

        self.stream=MJPEGStreamThread(stream_url)
        self.stream.frame_ready.connect(self.update_frame)
        self.stream.start()

        self.mqtt=MQTTClient()
        self.mqtt.data_signal.connect(self.update_data)
        self.mqtt.start()

        self.stats=StatsThread()
        self.stats.stats_signal.connect(self.update_stats)
        self.stats.start()

    # ------------------------------------------------

    def heater_switch_changed(self,state):

        if self.waiting_for_ack:
            return

        self.waiting_for_ack=True

        if state:
            self.send_cmd("heater_on")
        else:
            self.send_cmd("heater_off")

    def motor_switch_changed(self,state):

        if self.waiting_for_ack:
            return

        self.waiting_for_ack=True

        if state:
            self.send_cmd("motor_on")
        else:
            self.send_cmd("motor_off")

    def send_cmd(self,cmd):

        payload={"cmd":cmd,"time":time.time()}

        try:

            self.command_sent_time=time.time()

            self.mqtt.client.publish(CMD_TOPIC,json.dumps(payload))

            self.log_event(f"Command sent: {cmd}")

        except Exception as e:

            self.waiting_for_ack=False
            self.log_event(str(e))

    # ------------------------------------------------

    def update_data(self,data):

        arduino=data.get("arduino") or {}
        dht=data.get("dht") or {}
        system=data.get("system") or {}
        actuator=data.get("actuator") or {}

        distance=round(arduino.get("distance",0),2)
        pressure=round(arduino.get("pressure",0),2)

        boiler=round(dht.get("boiler_temp",0),2)
        room=round(dht.get("room_temp",0),2)

        bh=dht.get("boiler_hum","--")
        rh=dht.get("room_hum","--")

        heartbeat=round(time.time()-data.get("heartbeat",0),1)

        heater_state=actuator.get("heater",False)
        motor_state=actuator.get("motor",False)
        override=actuator.get("override")

        self.heater_switch.blockSignals(True)
        self.motor_switch.blockSignals(True)

        self.heater_switch.setChecked(heater_state)
        self.motor_switch.setChecked(motor_state)

        self.heater_switch.blockSignals(False)
        self.motor_switch.blockSignals(False)

        self.heater_lamp.set_state(heater_state)
        self.motor_lamp.set_state(motor_state)

        if override:

            self.heater_switch.setEnabled(False)
            self.motor_switch.setEnabled(False)
            self.reset_emergency.setVisible(True)

        else:

            self.heater_switch.setEnabled(True)
            self.motor_switch.setEnabled(True)
            self.reset_emergency.setVisible(False)

        self.waiting_for_ack=False
        # COMMAND TIMEOUT SAFETY
        if self.waiting_for_ack and (time.time() - self.command_sent_time) > 5:

            self.log_event("Command timeout — restoring controls")

            self.waiting_for_ack=False

            self.heater_switch.setEnabled(True)
            self.motor_switch.setEnabled(True)
        # tank + gauges

        level=max(0,min(150-distance,150))

        alarm=(pressure>9) or (distance<20)

        self.tank.update_level(level,alarm)

        self.g_level.set_value(level)
        self.g_pressure.set_value(pressure)
        self.g_boiler.set_value(boiler)
        self.g_room.set_value(room)

        # WATER LEVEL NEEDLE COLOR
        if distance < 20:
            self.g_level.needle_color = QColor(220,60,60)
        else:
            self.g_level.needle_color = QColor(80,180,80)

        # PRESSURE
        if pressure > 9:
            self.g_pressure.needle_color = QColor(220,60,60)
        else:
            self.g_pressure.needle_color = QColor(80,180,80)

        # BOILER TEMP
        if 20 <= boiler <= 40:
            self.g_boiler.needle_color = QColor(80,180,80)
        else:
            self.g_boiler.needle_color = QColor(220,60,60)

        # ROOM TEMP
        if 20 <= room <= 40:
            self.g_room.needle_color = QColor(80,180,80)
        else:
            self.g_room.needle_color = QColor(220,60,60)

        self.g_level.update()
        self.g_pressure.update()
        self.g_boiler.update()
        self.g_room.update()

        values={

            "distance":distance,
            "pressure":pressure,
            "boiler_temp":boiler,
            "boiler_hum":bh,
            "room_temp":room,
            "room_hum":rh,
            "heartbeat":heartbeat,

            "arduino_status":system.get("arduino_status","--"),
            "dht_status":system.get("dht_status","--"),
            "mqtt_status":system.get("mqtt_status","--")

        }

        for k,v in values.items():

            if k in self.labels:
                self.labels[k].setText(str(v))


        # ------------------------------
        # STATUS INDICATORS (RESTORED)
        # ------------------------------

        mqtt_status = system.get("mqtt_status","--")
        arduino_status = system.get("arduino_status","--")

        if mqtt_status == "CONNECTED":
            self.mqtt_status.setStyleSheet(status_style(GREEN))
        else:
            self.mqtt_status.setStyleSheet(status_style(YELLOW))

        if arduino_status == "CONNECTED":
            self.sensor_status.setStyleSheet(status_style(GREEN))
        else:
            self.sensor_status.setStyleSheet(status_style(YELLOW))

    # ------------------------------------------------

    def update_frame(self,frame):

        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)

        h,w,ch=rgb.shape

        img=QImage(rgb.data,w,h,ch*w,QImage.Format_RGB888)

        pix=QPixmap.fromImage(img).scaled(900,500,Qt.KeepAspectRatio)

        self.video.setPixmap(pix)

        # CAMERA HEALTH
        self.camera_status.setStyleSheet(status_style(GREEN))

        # FPS CALCULATION
        self.frame_counter += 1

        now = time.time()

        if now - self.fps_timer >= 1:

            fps = self.frame_counter / (now - self.fps_timer)

            self.fps.setText(f"FPS: {fps:.1f}")

            self.frame_counter = 0
            self.fps_timer = now

    # ------------------------------------------------

    def update_stats(self,data):

        self.cpu_temp.setText(f"CPU Temp: {data.get('temperature','--')}°C")
        self.cpu_load.setText(f"CPU Load: {data.get('cpu','--')}")
        self.encoder_label.setText(f"Encoder Restarts: {data.get('encoder_restarts','--')}")
        self.timestamp_label.setText(f"Stats Time: {data.get('timestamp','--')}")

    # ------------------------------------------------

    def log_event(self,msg):

        ts=datetime.now().strftime("%H:%M:%S")

        self.log.append(f"[{ts}] {msg}")

    # ------------------------------------------------

    def shutdown(self):

        self.log_event("SCADA shutdown requested")

        open("shutdown_signal.txt","w").close()

        try:
            self.stream.stop()
        except:
            pass

        QApplication.quit()
# ---------------------------------------------------------

def main():

    stream_url=f"http://{BROKER}:8080"

    app=QApplication(sys.argv)

    win=SCADAWindow(stream_url)

    win.showMaximized()

    sys.exit(app.exec_())


if __name__=="__main__":
    main()