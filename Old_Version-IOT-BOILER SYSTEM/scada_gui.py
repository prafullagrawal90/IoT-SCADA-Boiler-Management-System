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

    border=""

    if color==GREEN:
        border="2px solid #00ff9c"

    if color==RED:
        border="2px solid #ff3b3b"

    return f"""
        background:{color};
        color:white;
        padding:6px;
        border-radius:4px;
        font-weight:bold;
        border:{border};
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
        pen.setWidth(8)
        pen.setCapStyle(Qt.RoundCap)

        green_span = 270 * self.green_limit
        red_span = 270 * (self.red_limit - self.green_limit)
        # glow halo
        painter.setPen(QPen(QColor(80,255,120,80),14))
        painter.drawArc(
            arc_rect,
            int(135 * 16),
            int(-green_span * 16)
)
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

        # neon glow effect

        painter.setPen(QPen(QColor(0,0,0),8))
        painter.drawLine(center.x(), center.y(), int(x), int(y))

        painter.setPen(QPen(self.needle_color,4))
        painter.drawLine(center.x(), center.y(), int(x), int(y))

        # center hub (mechanical pivot)
        painter.setBrush(QColor(0,255,160))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center,5,5)

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

        painter.setPen(QColor(240,240,240))

        # title
        title_font = QFont("Consolas",9)
        painter.setFont(title_font)

        title_rect = QRectF(
            center.x() - radius*0.8,
            center.y() + radius*0.25,
            radius*1.6,
            20
        )

        painter.drawText(title_rect, Qt.AlignCenter, self.title)


        # numeric value
        value_font = QFont("Consolas",13)
        value_font.setBold(True)
        painter.setFont(value_font)

        value_rect = QRectF(
            center.x() - radius*0.8,
            center.y() + radius*0.42,
            radius*1.6,
            25
        )

        painter.drawText(value_rect, Qt.AlignCenter, str(round(self.value,2)))
# ---------------------------------------------------------
# TANK
# ---------------------------------------------------------

class TankWidget(QWidget):

    def __init__(self):

        super().__init__()

        self.level = 0
        self.alarm = False

        self.wave_offset = 0
        self.blink = False
        self.timer = QTimer()
        self.heater_on = False
        self.motor_on = False
        self.motor_angle = 0
        self.timer.timeout.connect(self.animate)
        self.timer.start(60)

    def animate(self):

        self.wave_offset += 4

        if self.motor_on:
            self.motor_angle += 10  

        self.update()

    def update_level(self, level, alarm, blink=False, heater=False, motor=False):
        self.level = level
        self.alarm = alarm
        self.blink = blink
        self.heater_on = heater
        self.motor_on = motor

    def paintEvent(self,e):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = self.rect()

        tank = rect.adjusted(40,20,-40,-20)

        painter.setPen(QPen(QColor(180,180,180),3))
        painter.drawRect(tank)

        h = tank.height()

        water_height = max(2, int((self.level/150)*h))

        water_rect = tank.adjusted(
            0,
            h-water_height,
            0,
            0
        )

        gradient = QLinearGradient(
            water_rect.topLeft(),
            water_rect.bottomLeft()
        )

        if self.alarm:

            if self.blink:
                # 🔴 BRIGHT FLASH (almost neon red)
                gradient.setColorAt(0,QColor(255,0,0))
                gradient.setColorAt(0.5,QColor(255,80,80))
                gradient.setColorAt(1,QColor(180,0,0))

            else:
                # ⚫ DARK OFF PHASE (high contrast)
                gradient.setColorAt(0,QColor(40,0,0))
                gradient.setColorAt(1,QColor(10,0,0))

        else:
            gradient.setColorAt(0,QColor(60,180,255))
            gradient.setColorAt(1,QColor(20,80,150))

        # -----------------------------
        # BASE WATER (DEPTH GRADIENT)
        # -----------------------------

        deep_grad = QLinearGradient(
            water_rect.topLeft(),
            water_rect.bottomLeft()
        )

        deep_grad.setColorAt(0.0, QColor(40,140,220))   # mid blue
        deep_grad.setColorAt(0.5, QColor(20,90,180))
        deep_grad.setColorAt(1.0, QColor(10,40,120))    # deep dark
        # ✅ DRAW BASE WATER FIRST (VERY IMPORTANT)
        if self.alarm:
            painter.setBrush(gradient)
        else:
            painter.setBrush(deep_grad)

        painter.setPen(Qt.NoPen)
        painter.drawRect(water_rect)
        # 🔥 RED GLOW OVERLAY (only in alarm)
        if self.alarm and self.blink:

            glow = QLinearGradient(
                water_rect.topLeft(),
                water_rect.bottomLeft()
            )

            glow.setColorAt(0.0, QColor(255,0,0,120))
            glow.setColorAt(0.5, QColor(255,0,0,60))
            glow.setColorAt(1.0, QColor(255,0,0,20))

            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawRect(water_rect)
        #print("Level:", self.level, "Height:", water_height)
        
        # -----------------------------
        # SURFACE OPACITY GLOW
        # -----------------------------

        surface_grad = QLinearGradient(
            water_rect.topLeft(),
            water_rect.bottomLeft()
        )

        surface_grad.setColorAt(0.0, QColor(255,255,255,40))  # bright surface
        surface_grad.setColorAt(0.2, QColor(255,255,255,30))
        surface_grad.setColorAt(0.5, QColor(255,255,255,0))

        painter.setBrush(surface_grad)
        painter.setPen(Qt.NoPen)
        painter.drawRect(water_rect)
        # -----------------------------
        # HEATER (BOTTOM)
        # -----------------------------

        heater_rect = QRectF(
            tank.left()+10,
            tank.bottom()+5,
            tank.width()-20,
            10
        )

        if self.heater_on:

            if self.blink:
                heater_color = QColor(255,120,0)
            else:
                heater_color = QColor(180,60,0)

        else:
            heater_color = QColor(80,80,80)

        painter.setBrush(heater_color)
        painter.setPen(Qt.NoPen)
        painter.drawRect(heater_rect)

        # -----------------------------
        # BUBBLES (HEATER ON - FULL HEIGHT)
        # -----------------------------
        wave_y = water_rect.top()
        amplitude = 6 if self.motor_on else 3
        if self.heater_on:

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255,255,255,140))

            for i in range(10):

                # horizontal distribution
                bx = tank.left() + 20 + (i * (tank.width()//12))

                # vertical rising motion
                rise = (self.wave_offset*3 + i*20)

                by = tank.bottom() - (rise % (water_rect.height()))

                # ---- CLAMP TO WAVE SURFACE ----
                wave_surface = wave_y + math.sin((bx + self.wave_offset)/15) * amplitude

                if by < wave_surface:
                    by = wave_surface  # stop at surface

                # bubble size variation (more realistic)
                size = 3 + (i % 3)

                painter.drawEllipse(int(bx), int(by), size, size)
                
        # -----------------------------
        # MOTOR (RIGHT SIDE)
        # -----------------------------

        motor_center_x = tank.right() + 25
        motor_center_y = tank.bottom() - 30
        radius = 10

        # motor body
        painter.setBrush(QColor(100,100,100))
        painter.setPen(QPen(QColor(200,200,200),2))
        painter.drawEllipse(int(motor_center_x-radius),
                            int(motor_center_y-radius),
                            radius*2, radius*2)

        # rotating blades
        if self.motor_on:

            painter.setPen(QPen(QColor(0,255,160),2))

            for i in range(6):

                angle = math.radians(self.motor_angle + i*60)

                x = motor_center_x + radius * math.cos(angle)
                y = motor_center_y + radius * math.sin(angle)

                painter.drawLine(int(motor_center_x),
                                int(motor_center_y),
                                int(x), int(y))
        # -----------------------------
        # PIPE FROM TANK TO MOTOR
        # -----------------------------

        pipe_y = tank.bottom() - 25

        painter.setPen(QPen(QColor(120,120,120),6))
        painter.drawLine(tank.right(), pipe_y, motor_center_x-10, pipe_y)

        # FLOW ANIMATION
        if self.motor_on:

            painter.setPen(QPen(QColor(0,255,160),4))

            flow_x = (self.wave_offset*3) % (motor_center_x - tank.right())

            painter.drawLine(
                tank.right() + flow_x,
                pipe_y,
                tank.right() + flow_x + 20,
                pipe_y
            )

        # -----------------------------
        # OUTPUT BOX
        # -----------------------------

        out_rect = QRectF(
            motor_center_x + 20,
            motor_center_y - 10,
            40,
            20
        )

        painter.setPen(QPen(QColor(180,180,180),2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(out_rect)


        # -----------------------------
        # animated wave
        # -----------------------------
        
        wave_thickness = 3 if self.motor_on else 2

        # -----------------------------
        # WAVE + FILLED SURFACE
        # -----------------------------

        points = []
        wave_y = water_rect.top()

        amplitude = 6 if self.motor_on else 3

        for x in range(tank.left(), tank.right() + 4, 4):
            y = wave_y + math.sin((x + self.wave_offset)/15) * amplitude
            points.append((x, int(y)))

        # ✅ FORCE EDGE LOCK (NO GAP)
        points[-1] = (tank.right(), points[-1][1])
        points[0] = (tank.left(), points[0][1])

        # ---- FILL AREA BETWEEN WAVE AND WATER ----
        from PyQt5.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(points[0][0], points[0][1])

        for (x, y) in points:
            path.lineTo(x, y)

        path.lineTo(tank.right()+1, water_rect.bottom())
        path.lineTo(tank.left()-1, water_rect.bottom())
        path.closeSubpath()

        # ✅ MATCH WATER COLOR (NO COLOR BREAK)
        if self.alarm:
            painter.setBrush(gradient)
        else:
            painter.setBrush(deep_grad)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        # crest highlight (gives realism)
        painter.setPen(QPen(QColor(255,255,255,220), 1))

        for i in range(len(points)-1):
            painter.drawLine(points[i][0], points[i][1]-1,
                            points[i+1][0], points[i+1][1]-1)
        # ---- DRAW WAVE LINE ----
        wave_thickness = 3 if self.motor_on else 2
        if self.alarm:
            wave_color = QColor(255,80,80,200) if self.blink else QColor(120,30,30,150)
        else:
            wave_color = QColor(255,255,255,180)

        painter.setPen(QPen(wave_color, wave_thickness))

        for i in range(len(points)-1):
            painter.drawLine(points[i][0], points[i][1],
                            points[i+1][0], points[i+1][1])

# ---------------------------------------------------------
# MAIN WINDOW
# ---------------------------------------------------------

class SCADAWindow(QWidget):

    def __init__(self,stream_url):

        super().__init__()

        self.setWindowTitle("Boiler Monitoring SCADA Console")

        self.setStyleSheet("""
        QWidget{
            background:#111;
            color:#e0e0e0;
            font-family: "Consolas","DejaVu Sans Mono","Courier New";
            font-size:12px;
        }

        QGroupBox{
            border:1px solid #444;
            margin-top:8px;
            padding:8px;
        }

        QGroupBox::title{
            subcontrol-origin:margin;
            subcontrol-position:top left;
            padding:0 6px;
            color:#7fdfff;
        }

        QTextEdit{
            background:#050505;
            border:1px solid #333;
        }

        QPushButton{
            background:#222;
            border:1px solid #555;
            padding:6px;
        }

        QPushButton:hover{
            background:#333;
        }

        QCheckBox{
            spacing:6px;
        }
""")

        self.frame_counter=0
        self.fps_timer=time.time()

        # VIDEO

        self.video=QLabel()
        self.video.setFixedSize(900,500)

        video_box=QGroupBox("Camera")
        video_layout=QVBoxLayout()
        video_layout.addWidget(self.video)
        video_box.setLayout(video_layout)

        # CPU PANEL

        self.cpu_temp=QLabel("CPU Temp: --")
        self.cpu_load=QLabel("CPU Load: --")
        self.encoder_label=QLabel("Encoder Restarts: --")
        self.timestamp_label=QLabel("Stats Time: --")
        self.fps=QLabel("FPS: --")

        cpu=QHBoxLayout()
        cpu.addWidget(self.cpu_temp)
        cpu.addWidget(self.cpu_load)
        cpu.addWidget(self.encoder_label)
        cpu.addWidget(self.timestamp_label)
        cpu.addStretch()
        cpu.addWidget(self.fps)

        cpu_box=QGroupBox("CPU Vitals")
        cpu_box.setLayout(cpu)

        # STATUS

        self.ssh_status=QLabel("SSH")
        self.mqtt_status=QLabel("MQTT")
        self.sensor_status=QLabel("Sensors")
        self.camera_status=QLabel("Camera")

        for s in [self.ssh_status,self.mqtt_status,self.sensor_status,self.camera_status]:
            s.setStyleSheet(status_style(YELLOW))
            s.setAlignment(Qt.AlignCenter)
            s.setMinimumWidth(90)

        self.ssh_status.setStyleSheet(status_style(GREEN))

        status=QHBoxLayout()
        status.addWidget(self.ssh_status)
        status.addWidget(self.mqtt_status)
        status.addWidget(self.sensor_status)
        status.addWidget(self.camera_status)
        status.addStretch()

        status_box=QGroupBox("System Status")
        status_box.setLayout(status)

        # LOG

        self.log=QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)

        log_box=QGroupBox("System Log")
        log_layout=QVBoxLayout()
        log_layout.addWidget(self.log)
        log_box.setLayout(log_layout)

        # CONTROLS

        self.heater_switch=QCheckBox("Heater ON")
        self.motor_switch=QCheckBox("Motor ON")

        self.heater_lamp=IndicatorLamp("")
        self.motor_lamp=IndicatorLamp("")

        heater_row=QHBoxLayout()
        heater_row.addWidget(QLabel("Heater"))
        heater_row.addStretch()
        heater_row.addWidget(self.heater_switch)
        heater_row.addWidget(self.heater_lamp)

        motor_row=QHBoxLayout()
        motor_row.addWidget(QLabel("Motor"))
        motor_row.addStretch()
        motor_row.addWidget(self.motor_switch)
        motor_row.addWidget(self.motor_lamp)

        self.emergency=QPushButton("AZ-5 EMERGENCY STOP")
        self.emergency.setFixedHeight(35)
        self.emergency.setStyleSheet("background:#e74c3c;font-weight:bold")

        self.reset_emergency=QPushButton("RESET EMERGENCY")
        self.reset_emergency.setVisible(False)

        controls=QVBoxLayout()
        controls.addLayout(heater_row)
        controls.addLayout(motor_row)

        # FIXED BUTTON AREA (prevents layout jump)
        btn_container = QVBoxLayout()
        btn_container.addWidget(self.emergency)
        btn_container.addWidget(self.reset_emergency)

        btn_frame = QFrame()
        btn_frame.setLayout(btn_container)
        btn_frame.setFixedHeight(80)   # 🔥 KEY FIX

        controls.addWidget(btn_frame)

        control_box=QGroupBox("Actuator Controls")
        control_box.setLayout(controls)

        # TANK

        self.tank=TankWidget()

        tank_box=QGroupBox("Tank")
        tank_layout=QVBoxLayout()
        tank_layout.addWidget(self.tank)
        tank_box.setLayout(tank_layout)

        # GAUGES

        self.g_level=Gauge("Water Level (cm)",150)
        self.g_pressure=Gauge("Pressure (bar)",15)
        self.g_boiler=Gauge("Boiler Temp (C)",120)
        self.g_room=Gauge("Room Temp (C)",50)

        gauges=QGridLayout()
        gauges.addWidget(self.g_level,0,0)
        gauges.addWidget(self.g_pressure,0,1)
        gauges.addWidget(self.g_boiler,1,0)
        gauges.addWidget(self.g_room,1,1)

        gauge_box=QGroupBox("Process Gauges")
        gauge_box.setLayout(gauges)

        # TELEMETRY TABLE

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

        telemetry_box=QGroupBox("Telemetry")
        telemetry_box.setLayout(table)

        # RIGHT COLUMN

        right=QVBoxLayout()
        right.addWidget(tank_box)
        right.addWidget(gauge_box)
        right.addWidget(telemetry_box)

        # LEFT COLUMN

        left=QVBoxLayout()
        left.addWidget(video_box)
        left.addWidget(cpu_box)
        left.addWidget(status_box)
        left.addWidget(control_box)
        left.addWidget(log_box)

        # SHUTDOWN

        self.shutdown_btn = QPushButton("Shutdown SCADA")
        self.shutdown_btn.clicked.connect(self.shutdown)

        # bottom fixed bar
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.shutdown_btn)

        left.addLayout(bottom_bar)

        # MAIN LAYOUT

        layout=QHBoxLayout()
        layout.addLayout(left,2)
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

        # connections

        self.heater_switch.toggled.connect(self.heater_switch_changed)
        self.motor_switch.toggled.connect(self.motor_switch_changed)

        self.emergency.clicked.connect(lambda:self.send_cmd("emergency"))
        self.reset_emergency.clicked.connect(lambda:self.send_cmd("reset_override"))

        self.command_sent_time=0
        self.prev_alarm_state = False
        self.alarm_active = False
        self.alarm_blink = False
        self.current_low_level = False
        self.current_high_level = False
        self.current_high_pressure = False
        self.sensor_invalid = True
        self.alarm_timer = QTimer()
        self.alarm_timer.timeout.connect(self.toggle_alarm_flash)
        self.alarm_timer.start(500)  # 500ms blink



    # ------------------------------------------------
    # ACTUATOR SWITCH HANDLERS
    # ------------------------------------------------

    def heater_switch_changed(self,state):

        if state:
            self.send_cmd("heater_on")
        else:
            self.send_cmd("heater_off")


    def motor_switch_changed(self,state):

        if state:
            self.send_cmd("motor_on")
        else:
            self.send_cmd("motor_off")


    # ------------------------------------------------

    def send_cmd(self,cmd):

        payload={"cmd":cmd,"time":time.time()}

        try:

            self.command_sent_time=time.time()

            self.mqtt.client.publish(CMD_TOPIC,json.dumps(payload))

            self.log_event(f"Command sent: {cmd}")

        except Exception as e:
            self.log_event(str(e))


    # ------------------------------------------------
    # MQTT DATA UPDATE
    # ------------------------------------------------

    def update_data(self,data):

        arduino=data.get("arduino") or {}
        dht=data.get("dht") or {}
        system=data.get("system") or {}
        actuator=data.get("actuator") or {}

        raw_distance = arduino.get("distance", None)

        # ⚠️ SENSOR INVALID CASE
        if raw_distance is None or raw_distance <= 0:

            if not self.sensor_invalid:
                self.log_event("⚠️ SENSOR INVALID")

            self.sensor_invalid = True

            # STOP ALL ALARMS + RESET VISUALS
            self.alarm_active = False
            self.prev_alarm_state = False

            self.g_level.needle_color = QColor(120,120,120)
            self.g_pressure.needle_color = QColor(120,120,120)

            self.tank.update_level(
                self.g_level.target,
                False,
                False,
                False,
                False
            )

            return

        # ✅ VALID DATA
        distance = round(raw_distance, 2)

        # log recovery
        if self.sensor_invalid:
            self.log_event("✅ SENSOR RESTORED")

        self.sensor_invalid = False
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

            self.emergency.setStyleSheet(
                "background:#ff0000;font-weight:bold;border:3px solid white"
            )

        else:

            self.heater_switch.setEnabled(True)
            self.motor_switch.setEnabled(True)

            self.reset_emergency.setVisible(False)   # ✅ already there

            self.emergency.setStyleSheet(
                "background:#e74c3c;font-weight:bold"
            )


        level=max(0,min(150-distance,150))
        
        # SYSTEM ALARM CONDITIONS
        low_level_alarm = distance > 120    # tank almost empty
        high_level_alarm = distance < 20    # tank almost full
        high_pressure_alarm = pressure > 9
        alarm = low_level_alarm or high_level_alarm or high_pressure_alarm

        # TRACK GLOBAL ALARM STATE
        if not alarm and self.prev_alarm_state:
            self.log_event("✅ ALARM CLEARED")

        # ---------------------------------
        # DETECT NEW INDIVIDUAL ALARMS (FIXED)
        # ---------------------------------

        # LOW LEVEL
        if low_level_alarm and not self.current_low_level:
            self.log_event("🚨 LOW LEVEL ALARM")

        # HIGH LEVEL
        if high_level_alarm and not self.current_high_level:
            self.log_event("🚨 HIGH LEVEL ALARM")

        # HIGH PRESSURE
        if high_pressure_alarm and not self.current_high_pressure:
            self.log_event("🚨 HIGH PRESSURE ALARM")

        # ---------------------------------
        # UPDATE STATES AFTER DETECTION
        # ---------------------------------
        self.current_low_level = low_level_alarm
        self.current_high_level = high_level_alarm
        self.current_high_pressure = high_pressure_alarm


        self.prev_alarm_state = alarm
        self.alarm_active = alarm

        if not alarm:
            # restore normal colors when alarm clears
            self.g_pressure.needle_color = QColor(80,180,80)
            self.g_level.needle_color = QColor(80,180,80)


        # PRESSURE NEEDLE COLOR
        if not self.alarm_active:
            if pressure > 9:
                self.g_pressure.needle_color = QColor(255,50,50)
            else:
                self.g_pressure.needle_color = QColor(80,180,80)

            if distance < 20:
                self.g_level.needle_color = QColor(255,50,50)
            else:
                self.g_level.needle_color = QColor(80,180,80)

        self.g_level.set_value(level)
        self.g_pressure.set_value(pressure)
        self.g_boiler.set_value(boiler)
        self.g_room.set_value(room)
        water_alarm = low_level_alarm or high_level_alarm or high_pressure_alarm

        self.tank.update_level(
            level,
            water_alarm,   
            self.alarm_blink,
            heater_state,
            motor_state
        )

        values={

            "distance": distance if not self.sensor_invalid else "INVALID",
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
    # CAMERA FRAME UPDATE
    # ------------------------------------------------

    def update_frame(self,frame):

        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)

        h,w,ch=rgb.shape

        img=QImage(rgb.data,w,h,ch*w,QImage.Format_RGB888)

        pix=QPixmap.fromImage(img).scaled(900,500,Qt.KeepAspectRatio)

        self.video.setPixmap(pix)

        self.camera_status.setStyleSheet(status_style(GREEN))

        self.frame_counter += 1

        now = time.time()

        if now - self.fps_timer >= 1:

            fps = self.frame_counter / (now - self.fps_timer)

            self.fps.setText(f"FPS: {fps:.1f}")

            self.frame_counter = 0
            self.fps_timer = now


    # ------------------------------------------------
    # PI STATS UPDATE
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

    def toggle_alarm_flash(self):

        if not self.alarm_active:
            return

        self.alarm_blink = not self.alarm_blink

        # DEFAULT NORMAL
        level_color = QColor(80,180,80)
        pressure_color = QColor(80,180,80)

        # APPLY ONLY ACTIVE ALARMS
        if self.current_low_level or self.current_high_level:
            level_color = QColor(255,0,0) if self.alarm_blink else QColor(80,180,80)

        if self.current_high_pressure:
            pressure_color = QColor(255,0,0) if self.alarm_blink else QColor(80,180,80)

        self.g_level.needle_color = level_color
        self.g_pressure.needle_color = pressure_color

        self.g_level.update()
        self.g_pressure.update()
        # update tank WITH NEW BLINK STATE
        water_alarm = (
            self.current_low_level or 
            self.current_high_level or 
            self.current_high_pressure
        )

        self.tank.update_level(
            self.g_level.target,    
            water_alarm,     
            self.alarm_blink,   # ✅ correct
            self.heater_switch.isChecked(),
            self.motor_switch.isChecked()
        )
    




# ---------------------------------------------------------

def main():

    stream_url=f"http://{BROKER}:8080"

    app=QApplication(sys.argv)

    win=SCADAWindow(stream_url)

    win.showMaximized()

    sys.exit(app.exec_())


if __name__=="__main__":
    main()