import sys
import os
import time
import json
import math
import cv2
import numpy as np
from datetime import datetime
from threading import Thread

# Add parent directory to sys.path to find scada_application_layer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scada_application_layer import ScadaApplication
import shared_data_bus as bus

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

from PyQt5.QtCore import pyqtSignal, Qt, QTimer, QRectF, QObject

# ML Prediction file path
ML_PREDICTION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ml_predictions.json"
)

# ---------------------------------------------------------
# COLORS
# ---------------------------------------------------------

GREEN="#27ae60"
DARK_GREEN="#1e8449"
YELLOW="#f1c40f"
RED="#e74c3c"

def status_style(color):
    border=""
    if color==GREEN or color==DARK_GREEN:
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
# GUI BRIDGE (Signals for UI Thread)
# ---------------------------------------------------------

class DataBridge(QObject):
    data_signal = pyqtSignal(dict)
    stats_signal = pyqtSignal(dict)
    frame_signal = pyqtSignal(np.ndarray)
    ml_signal = pyqtSignal(dict)


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

        self.setMinimumSize(170,170)

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
                # \U0001f534 BRIGHT FLASH (almost neon red)
                gradient.setColorAt(0,QColor(255,0,0))
                gradient.setColorAt(0.5,QColor(255,80,80))
                gradient.setColorAt(1,QColor(180,0,0))

            else:
                # \U0001f232 DARK OFF PHASE (high contrast)
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
        # \U0001f205 DRAW BASE WATER FIRST (VERY IMPORTANT)
        if self.alarm:
            painter.setBrush(gradient)
        else:
            painter.setBrush(deep_grad)

        painter.setPen(Qt.NoPen)
        painter.drawRect(water_rect)
        # \U0001f525 RED GLOW OVERLAY (only in alarm)
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

                by = tank.bottom() - (rise % (max(1, water_rect.height())))

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

            flow_x = (self.wave_offset*3) % (max(1, motor_center_x - tank.right()))

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

        # \U0001f205 FORCE EDGE LOCK (NO GAP)
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

        # \U0001f205 MATCH WATER COLOR (NO COLOR BREAK)
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

    def __init__(self):

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
        self.video.setMinimumSize(400,280)
        self.video.setScaledContents(True)

        video_box=QGroupBox("Camera")
        video_layout=QVBoxLayout()
        video_layout.setContentsMargins(2,2,2,2)
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
        self.web_status=QLabel("Online Server")

        for s in [self.ssh_status,self.mqtt_status,self.sensor_status,self.camera_status,self.web_status]:
            s.setStyleSheet(status_style(YELLOW))
            s.setAlignment(Qt.AlignCenter)
            s.setMinimumWidth(90)

        self.ssh_status.setStyleSheet(status_style(GREEN))

        status=QHBoxLayout()
        status.addWidget(self.ssh_status)
        status.addWidget(self.mqtt_status)
        status.addWidget(self.sensor_status)
        status.addWidget(self.camera_status)
        status.addWidget(self.web_status)
        status.addStretch()

        status_box=QGroupBox("System Status")
        status_box.setLayout(status)

        self.log=QTextEdit()
        self.log.setReadOnly(True)

        log_box=QGroupBox("System Log")
        log_layout=QVBoxLayout()
        log_layout.setContentsMargins(4,4,4,4)
        log_layout.setSpacing(2)
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
        self.emergency.setFixedHeight(30)
        self.emergency.setStyleSheet("background:#e74c3c;font-weight:bold")

        self.reset_emergency=QPushButton("RESET EMERGENCY")
        self.reset_emergency.setFixedHeight(28)
        self.reset_emergency.setVisible(False)

        controls=QVBoxLayout()
        controls.setSpacing(2)
        controls.setContentsMargins(4,4,4,4)
        controls.addLayout(heater_row)
        controls.addLayout(motor_row)
        controls.addWidget(self.emergency)
        controls.addWidget(self.reset_emergency)

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

        # =============================================
        # ML INTELLIGENCE PANEL
        # =============================================
        ml_layout = QVBoxLayout()

        # Overall health header
        self.ml_health_label = QLabel("⏳ ML Engine: Initializing...")
        self.ml_health_label.setAlignment(Qt.AlignCenter)
        self.ml_health_label.setStyleSheet("""
            font-size: 14px;
            font-weight: bold;
            padding: 8px;
            border-radius: 4px;
            background: #1a1a2e;
            color: #7fdfff;
            border: 1px solid #333;
        """)
        ml_layout.addWidget(self.ml_health_label)

        # Confidence bar container
        self.ml_confidence_label = QLabel("Overall Confidence: --")
        self.ml_confidence_label.setAlignment(Qt.AlignCenter)
        self.ml_confidence_label.setStyleSheet("font-size: 12px; color: #aaa; padding: 2px;")
        ml_layout.addWidget(self.ml_confidence_label)

        # Per-sensor confidence grid
        self.ml_sensor_labels = {}
        ml_sensor_grid = QGridLayout()
        sensor_display_names = {
            "water_level": "Water Level",
            "pressure": "Pressure",
            "boiler_temp": "Boiler Temp",
            "room_temp": "Room Temp",
            "distance": "Distance"
        }
        ml_row = 0
        for key, display_name in sensor_display_names.items():
            name_lbl = QLabel(display_name)
            name_lbl.setStyleSheet("color: #888; font-size: 11px;")

            status_lbl = QLabel("--")
            status_lbl.setAlignment(Qt.AlignCenter)
            status_lbl.setMinimumWidth(80)
            status_lbl.setStyleSheet("""
                padding: 3px 6px;
                border-radius: 3px;
                background: #1a1a2e;
                color: #aaa;
                font-size: 11px;
                font-weight: bold;
            """)

            conf_lbl = QLabel("--")
            conf_lbl.setAlignment(Qt.AlignRight)
            conf_lbl.setStyleSheet("color: #666; font-size: 11px;")

            trend_lbl = QLabel("--")
            trend_lbl.setAlignment(Qt.AlignCenter)
            trend_lbl.setStyleSheet("color: #666; font-size: 11px;")

            ml_sensor_grid.addWidget(name_lbl, ml_row, 0)
            ml_sensor_grid.addWidget(status_lbl, ml_row, 1)
            ml_sensor_grid.addWidget(conf_lbl, ml_row, 2)
            ml_sensor_grid.addWidget(trend_lbl, ml_row, 3)

            self.ml_sensor_labels[key] = {
                "status": status_lbl,
                "confidence": conf_lbl,
                "trend": trend_lbl
            }
            ml_row += 1

        ml_layout.addLayout(ml_sensor_grid)

        # Diagnostics log
        self.ml_diagnostics = QTextEdit()
        self.ml_diagnostics.setReadOnly(True)
        self.ml_diagnostics.setMaximumHeight(90)
        self.ml_diagnostics.setMinimumHeight(60)
        self.ml_diagnostics.setStyleSheet("""
            background: #050510;
            border: 1px solid #222;
            color: #ff9;
            font-size: 10px;
        """)
        ml_layout.addWidget(QLabel("ML Diagnostics:"))
        ml_layout.addWidget(self.ml_diagnostics)

        # Emergency countdown overlay (hidden by default)
        self.ml_emergency_frame = QFrame()
        self.ml_emergency_frame.setVisible(False)
        self.ml_emergency_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #8b0000, stop:1 #ff0000);
                border: 3px solid #ff4444;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        emergency_layout = QVBoxLayout(self.ml_emergency_frame)

        self.ml_countdown_label = QLabel("⚠️ AUTO-EMERGENCY IN: --s")
        self.ml_countdown_label.setAlignment(Qt.AlignCenter)
        self.ml_countdown_label.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            color: white;
        """)
        emergency_layout.addWidget(self.ml_countdown_label)

        self.ml_ack_btn = QPushButton("✋ ACKNOWLEDGE — I'm monitoring")
        self.ml_ack_btn.setStyleSheet("""
            background: #222;
            color: white;
            font-weight: bold;
            padding: 8px;
            border: 2px solid #fff;
            font-size: 13px;
        """)
        self.ml_ack_btn.clicked.connect(self._ml_acknowledge_emergency)
        emergency_layout.addWidget(self.ml_ack_btn)

        ml_layout.addWidget(self.ml_emergency_frame)

        # Small "operator monitoring" status pill (shown after ACK while danger persists)
        self.ml_monitoring_label = QLabel("👁️ Operator monitoring — ML interrupt suppressed")
        self.ml_monitoring_label.setAlignment(Qt.AlignCenter)
        self.ml_monitoring_label.setVisible(False)
        self.ml_monitoring_label.setStyleSheet("""
            background: #1a1a00;
            color: #ffcc00;
            border: 1px solid #ffcc00;
            border-radius: 4px;
            padding: 6px;
            font-weight: bold;
            font-size: 12px;
        """)
        ml_layout.addWidget(self.ml_monitoring_label)

        ml_box = QGroupBox("🧠 ML Intelligence")
        ml_box.setLayout(ml_layout)
        ml_box.setStyleSheet("""
            QGroupBox {
                border: 1px solid #2a2a4e;
                margin-top: 8px;
                padding: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                color: #c9a0ff;
                font-weight: bold;
            }
        """)

        # =============================================
        # MAIN LAYOUT — 2-ROW GRID (fits on one screen)
        # =============================================
        # Row 0: Camera | Tank+Gauges | ML Intelligence
        # Row 1: CPU+Status+Controls | Telemetry | Log

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4,4,4,4)
        main_layout.setSpacing(4)

        # ---- TOP ROW ----
        top_row = QHBoxLayout()
        top_row.setSpacing(4)

        # Top-Left: Camera
        top_row.addWidget(video_box, 3)

        # Top-Center: Tank + Gauges (stacked)
        center_top = QVBoxLayout()
        center_top.setSpacing(2)
        center_top.addWidget(tank_box, 1)
        center_top.addWidget(gauge_box, 2)
        top_row.addLayout(center_top, 2)

        # Top-Right: ML Intelligence
        top_row.addWidget(ml_box, 2)

        main_layout.addLayout(top_row, 3)

        # ---- BOTTOM ROW ----
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)

        # Bottom-Left: CPU + Status + Controls (stacked vertically)
        bottom_left = QVBoxLayout()
        bottom_left.setSpacing(2)
        bottom_left.addWidget(cpu_box)
        bottom_left.addWidget(status_box)
        bottom_left.addWidget(control_box)
        bottom_row.addLayout(bottom_left, 2)

        # Bottom-Center: Telemetry
        bottom_row.addWidget(telemetry_box, 1)

        # Bottom-Right: System Log + Shutdown
        bottom_right = QVBoxLayout()
        bottom_right.setSpacing(2)
        bottom_right.addWidget(log_box, 1)

        self.shutdown_btn = QPushButton("Shutdown SCADA")
        self.shutdown_btn.clicked.connect(self.shutdown)
        self.shutdown_btn.setFixedHeight(28)
        bottom_right.addWidget(self.shutdown_btn)

        bottom_row.addLayout(bottom_right, 2)

        main_layout.addLayout(bottom_row, 1)

        self.setLayout(main_layout)

        # APPLICATION LAYER
        self.app = ScadaApplication()
        self.bridge = DataBridge()

        # Connect App Layer to Bridge
        self.app.on_data(self.bridge.data_signal.emit)
        self.app.on_stats(self.bridge.stats_signal.emit)
        self.app.on_frame(self.bridge.frame_signal.emit)

        # Connect Bridge to UI methods
        self.bridge.data_signal.connect(self.update_data)
        self.bridge.stats_signal.connect(self.update_stats)
        self.bridge.frame_signal.connect(self.update_frame)
        self.bridge.ml_signal.connect(self.update_ml_predictions)

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
        self.current_low_pressure = False
        self.sensor_invalid = True
        self._ml_ack_active = False   # True after operator ACK, until conditions clear
        
        self.alarm_timer = QTimer()
        self.alarm_timer.timeout.connect(self.toggle_alarm_flash)
        self.alarm_timer.start(500)  # 500ms blink

        # ML Prediction reader timer
        self.ml_timer = QTimer()
        self.ml_timer.timeout.connect(self._read_ml_predictions)
        self.ml_timer.start(500)  # Read every 500ms

        # Web command poller — GUI is the gate for all actuator commands
        self.web_cmd_timer = QTimer()
        self.web_cmd_timer.timeout.connect(self._poll_web_commands)
        self.web_cmd_timer.start(300)  # Poll every 300ms

        # Web event poller — picks up connect/disconnect/cmd events from web server
        self.web_event_timer = QTimer()
        self.web_event_timer.timeout.connect(self._poll_web_events)
        self.web_event_timer.start(800)  # Poll every 800ms
        self._web_client_count = 0      # live web client count

        # Announce GUI started on the shared bus
        bus.log_event("SCADA GUI started — primary controller active", source="core")



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

        self.command_sent_time=time.time()
        
        if self.app.send_command(cmd):
            self.log_event(f"Command sent: {cmd}")
        else:
            self.log_event(f"Failed to send command: {cmd}")


    # ------------------------------------------------
    # MQTT DATA UPDATE
    # ------------------------------------------------

    def _poll_web_events(self):
        """Drain web_event_queue.json, show in log, update Online Server status."""
        queue_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "web_event_queue.json"
        )
        if not os.path.exists(queue_file):
            return
        try:
            with open(queue_file, "r") as f:
                events = json.load(f)
            if not events:
                return
            # Clear queue atomically
            tmp = queue_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump([], f)
            os.replace(tmp, queue_file)
            # Log each event and update web status pill
            for e in events:
                msg = e.get("msg", "")
                self.log_event(msg, source=e.get("source", "web"))
                # Track client count and update pill
                msg_low = msg.lower()
                if "connected" in msg_low and "disconnected" not in msg_low:
                    self._web_client_count = max(0, self._web_client_count + 1)
                    self.web_status.setStyleSheet(status_style(DARK_GREEN))
                elif "disconnected" in msg_low:
                    self._web_client_count = max(0, self._web_client_count - 1)
                    if self._web_client_count == 0:
                        self.web_status.setStyleSheet(status_style(GREEN))
                    else:
                        self.web_status.setStyleSheet(status_style(DARK_GREEN))
                elif "started" in msg_low:
                    self._web_client_count = 0
                    self.web_status.setStyleSheet(status_style(GREEN))
                # Update label text with client count
                n = self._web_client_count
                self.web_status.setText(f"Online Server ({n})")
        except Exception:
            pass

    def _poll_web_commands(self):
        """Poll shared bus for commands submitted by the web dashboard."""
        cmd_entry = bus.pop_web_command()
        if not cmd_entry:
            return
        cmd  = cmd_entry.get("cmd", "")
        ip   = cmd_entry.get("ip", "unknown")
        source = f"web({ip})"
        valid = {"heater_on", "heater_off", "motor_on", "motor_off",
                 "emergency", "reset_override"}
        if cmd not in valid:
            bus.log_event(f"Rejected unknown web command: {cmd}", source=source)
            return
        # Execute via the GUI's own app layer (GUI is primary)
        if self.app.send_command(cmd):
            self.log_event(f"[WEB CMD] {cmd}", source=source)
        else:
            self.log_event(f"[WEB CMD] FAILED: {cmd}", source=source)

    def update_data(self, state):
        """Processes the unified state format from ScadaApplication."""
        
        # Push raw state to shared bus (web dashboard reads this)
        bus.write_state(state)

        telemetry = state.get("telemetry", {})
        alarms = state.get("alarms", {})
        system = state.get("system", {})
        actuators = state.get("actuators", {})
        
        # Sensor Validity
        sensor_valid = system.get("sensor_valid", False)
        
        if not sensor_valid:
            if not self.sensor_invalid:
                self.log_event("⚠️ SENSOR INVALID")
            self.sensor_invalid = True
            
            # Reset Visuals
            self.alarm_active = False
            self.prev_alarm_state = False
            self.g_level.needle_color = QColor(120,120,120)
            self.g_pressure.needle_color = QColor(120,120,120)
            self.tank.update_level(self.g_level.target, False, False, False, False)
            return

        # Valid Data
        if self.sensor_invalid:
            self.log_event("\u2705 SENSOR RESTORED")
        self.sensor_invalid = False

        # Update Gauges
        self.g_level.set_value(telemetry.get("water_level", 0))
        self.g_pressure.set_value(telemetry.get("pressure", 0))
        self.g_boiler.set_value(telemetry.get("boiler_temp", 0))
        self.g_room.set_value(telemetry.get("room_temp", 0))

        # Update Labels
        values = {
            "distance": telemetry.get("distance", "--"),
            "pressure": telemetry.get("pressure", "--"),
            "boiler_temp": telemetry.get("boiler_temp", "--"),
            "boiler_hum": telemetry.get("boiler_hum", "--"),
            "room_temp": telemetry.get("room_temp", "--"),
            "room_hum": telemetry.get("room_hum", "--"),
            "heartbeat": state.get("heartbeat_age", "--"),
            "arduino_status": system.get("arduino_status", "--"),
            "dht_status": system.get("dht_status", "--"),
            "mqtt_status": system.get("mqtt_status", "--")
        }

        for k, v in values.items():
            if k in self.labels:
                self.labels[k].setText(str(v))

        # Update Actuator UI
        heater_state = actuators.get("heater", False)
        motor_state = actuators.get("motor", False)
        override = actuators.get("override", False)

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
            self.emergency.setStyleSheet("background:#ff0000;font-weight:bold;border:3px solid white")
        else:
            self.heater_switch.setEnabled(True)
            self.motor_switch.setEnabled(True)
            self.reset_emergency.setVisible(False)
            self.emergency.setStyleSheet("background:#e74c3c;font-weight:bold")

        # Handle Alarms
        alarm_active = alarms.get("any", False)
        
        if not alarm_active and self.prev_alarm_state:
            self.log_event("\u2705 ALARM CLEARED")

        if alarms.get("low_level") and not self.current_low_level:
            self.log_event("\U0001f6a8 LOW LEVEL ALARM")
        if alarms.get("high_level") and not self.current_high_level:
            self.log_event("\U0001f6a8 HIGH LEVEL ALARM")
        if alarms.get("high_pressure") and not self.current_high_pressure:
            self.log_event("\U0001f6a8 HIGH PRESSURE ALARM")
        if alarms.get("low_pressure") and not self.current_low_pressure:
            self.log_event("\U0001f6a8 LOW PRESSURE ALARM")

        self.current_low_level = alarms.get("low_level")
        self.current_high_level = alarms.get("high_level")
        self.current_high_pressure = alarms.get("high_pressure")
        self.current_low_pressure = alarms.get("low_pressure")
        self.prev_alarm_state = alarm_active
        self.alarm_active = alarm_active

        # If alarm just cleared, immediately snap needles back to green.
        # Don't wait for the next blink tick — the timer returns early
        # when alarm_active is False, so without this the needle stays
        # whatever color it was at the moment the alarm cleared.
        if not alarm_active:
            self.g_level.needle_color = QColor(80,180,80)
            self.g_pressure.needle_color = QColor(80,180,80)
            self.g_level.update()
            self.g_pressure.update()

        # Status indicators
        if system.get("mqtt_status") == "CONNECTED":
            self.mqtt_status.setStyleSheet(status_style(GREEN))
        else:
            self.mqtt_status.setStyleSheet(status_style(YELLOW))

        if system.get("arduino_status") == "CONNECTED":
            self.sensor_status.setStyleSheet(status_style(GREEN))
        else:
            self.sensor_status.setStyleSheet(status_style(YELLOW))

        # Tank update
        self.tank.update_level(
            telemetry.get("water_level", 0),
            alarm_active,
            self.alarm_blink,
            heater_state,
            motor_state
        )

    # ------------------------------------------------
    # CAMERA FRAME UPDATE
    # ------------------------------------------------

    def update_frame(self,frame):

        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)

        h,w,ch=rgb.shape

        img=QImage(rgb.data,w,h,ch*w,QImage.Format_RGB888)

        pix=QPixmap.fromImage(img).scaled(
            self.video.width(), self.video.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

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

        self.cpu_temp.setText(f"CPU Temp: {data.get('temperature','--')}\u00b0C")
        self.cpu_load.setText(f"CPU Load: {data.get('cpu','--')}")
        self.encoder_label.setText(f"Encoder Restarts: {data.get('encoder_restarts','--')}")
        self.timestamp_label.setText(f"Stats Time: {data.get('timestamp','--')}")


    # ------------------------------------------------

    def log_event(self, msg, source="core"):
        """Log to GUI display AND push to shared bus with structured format."""
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}]:[{source}]->[{msg}]"
        self.log.append(formatted)
        bus.log_event(msg, source=source)


    # ------------------------------------------------

    def shutdown(self):

        self.log_event("SCADA shutdown requested")

        open("shutdown_signal.txt","w").close()

        try:
            self.app.shutdown()
        except:
            pass

        QApplication.quit()

    def toggle_alarm_flash(self):

        if not self.alarm_active:
            # Alarm is not active — ensure needles are always green here.
            # Without this, a needle set to red by the last blink cycle would
            # stay red permanently after the alarm clears.
            self.g_level.needle_color = QColor(80,180,80)
            self.g_pressure.needle_color = QColor(80,180,80)
            self.g_level.update()
            self.g_pressure.update()
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
        self.tank.update_level(
            self.g_level.target,    
            self.alarm_active,     
            self.alarm_blink,
            self.heater_switch.isChecked(),
            self.motor_switch.isChecked()
        )
    
    # ------------------------------------------------
    # ML PREDICTION INTEGRATION
    # ------------------------------------------------

    def _read_ml_predictions(self):
        """Reads ML predictions from shared JSON file."""
        try:
            if os.path.exists(ML_PREDICTION_FILE):
                with open(ML_PREDICTION_FILE, "r") as f:
                    data = json.load(f)
                self.bridge.ml_signal.emit(data)
        except (json.JSONDecodeError, IOError):
            pass  # File being written, skip this cycle

    def update_ml_predictions(self, preds):
        """Update ML Intelligence panel with latest predictions."""
        # Overall health
        health = preds.get("overall_health", "UNKNOWN")
        confidence = preds.get("overall_confidence", 0)
        status = preds.get("system_status", "UNKNOWN")

        # Color mapping for health status
        health_colors = {
            "NOMINAL": ("#00ff9c", "#0a2e1a"),   # green text, dark green bg
            "CAUTION": ("#ffcc00", "#2e2a0a"),   # yellow text, dark yellow bg
            "WARNING": ("#ff8800", "#2e1a0a"),   # orange text, dark orange bg
            "CRITICAL": ("#ff0000", "#2e0a0a"),  # red text, dark red bg
            "INITIALIZING": ("#7fdfff", "#1a1a2e"),
        }
        text_color, bg_color = health_colors.get(health, ("#aaa", "#1a1a2e"))

        self.ml_health_label.setText(f"🧠 ML Engine: {health}")
        self.ml_health_label.setStyleSheet(f"""
            font-size: 14px;
            font-weight: bold;
            padding: 8px;
            border-radius: 4px;
            background: {bg_color};
            color: {text_color};
            border: 1px solid {text_color}40;
        """)

        # Overall confidence
        conf_pct = f"{confidence:.1%}"
        self.ml_confidence_label.setText(f"Overall Confidence: {conf_pct}")
        if confidence >= 0.85:
            conf_color = "#00ff9c"
        elif confidence >= 0.6:
            conf_color = "#ffcc00"
        elif confidence >= 0.3:
            conf_color = "#ff8800"
        else:
            conf_color = "#ff0000"
        self.ml_confidence_label.setStyleSheet(
            f"font-size: 12px; color: {conf_color}; padding: 2px; font-weight: bold;"
        )

        # Per-sensor updates
        sensors = preds.get("sensors", {})
        status_colors = {
            "HEALTHY": ("#00ff9c", "#0a2e1a"),
            "WARNING": ("#ffcc00", "#2e2a0a"),
            "DEGRADED": ("#ff8800", "#2e1a0a"),
            "CRITICAL": ("#ff0000", "#2e0a0a"),
            "INSUFFICIENT_DATA": ("#666", "#1a1a2e"),
        }
        trend_icons = {
            "RISING": "📈",
            "FALLING": "📉",
            "STABLE": "➡️",
            "UNKNOWN": "❓"
        }

        for sensor_key, labels in self.ml_sensor_labels.items():
            sensor_data = sensors.get(sensor_key, {})
            s_status = sensor_data.get("status", "--")
            s_conf = sensor_data.get("confidence", 0)
            s_trend = sensor_data.get("trend", "UNKNOWN")

            tc, bc = status_colors.get(s_status, ("#aaa", "#1a1a2e"))

            labels["status"].setText(s_status)
            labels["status"].setStyleSheet(f"""
                padding: 3px 6px;
                border-radius: 3px;
                background: {bc};
                color: {tc};
                font-size: 11px;
                font-weight: bold;
            """)

            labels["confidence"].setText(f"{s_conf:.0%}")
            labels["confidence"].setStyleSheet(f"color: {tc}; font-size: 11px; font-weight: bold;")

            labels["trend"].setText(f"{trend_icons.get(s_trend, '❓')} {s_trend}")
            labels["trend"].setStyleSheet("color: #aaa; font-size: 11px;")

        # Diagnostics
        diagnostics = preds.get("diagnostics", [])
        if diagnostics:
            self.ml_diagnostics.setPlainText("\n".join(diagnostics))
            # Auto-scroll to bottom
            scrollbar = self.ml_diagnostics.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

        # Emergency countdown
        armed = preds.get("auto_emergency_armed", False)
        countdown = preds.get("emergency_countdown")
        tier = preds.get("emergency_tier", "")
        diagnostics_text = "\n".join(preds.get("diagnostics", []))

        # Detect if ML is in "operator suppressed" mode (ack active, danger still present)
        ml_suppressed = (
            not armed
            and countdown is None
            and "ML interrupt suppressed" in diagnostics_text
        )

        # If conditions fully cleared, reset GUI ack flag too
        if not armed and countdown is None and "ML interrupt suppressed" not in diagnostics_text:
            self._ml_ack_active = False

        if armed and countdown is not None:
            # --- Fresh emergency: not yet acknowledged ---
            self._ml_ack_active = False
            self.ml_emergency_frame.setVisible(True)
            self.ml_monitoring_label.setVisible(False)

            # Show tier in the label
            if tier == "COMPOUND":
                tier_text = "⚠️ COMPOUND DANGER (Press+Temp)"
            else:
                tier_text = "⚠️ SINGLE CRITICAL"

            self.ml_countdown_label.setText(
                f"🚨 {tier_text}\nAUTO-EMERGENCY IN: {countdown:.0f}s"
            )

            # Pulse effect based on urgency
            if countdown <= 3:
                border_color = "#ff0000"
                bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8b0000,stop:1 #ff0000)"
            elif countdown <= 10:
                border_color = "#ff4444"
                bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5b0000,stop:1 #cc0000)"
            else:
                border_color = "#ff6666"
                bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3b0000,stop:1 #990000)"

            self.ml_emergency_frame.setStyleSheet(f"""
                QFrame {{
                    background: {bg};
                    border: 3px solid {border_color};
                    border-radius: 6px;
                    padding: 10px;
                }}
            """)

            # Log emergency arming (source = ml)
            self.log_event(f"ML [{tier}]: Auto-emergency countdown: {countdown:.0f}s", source="ml")

        elif self._ml_ack_active or ml_suppressed:
            # --- Operator has acknowledged, ML suppressed for this condition ---
            self._ml_ack_active = True
            self.ml_emergency_frame.setVisible(False)
            self.ml_monitoring_label.setVisible(True)

        else:
            # --- No active emergency ---
            self.ml_emergency_frame.setVisible(False)
            self.ml_monitoring_label.setVisible(False)

    def _ml_acknowledge_emergency(self):
        """User clicked acknowledge — suppress ML re-arming for this condition."""
        self.log_event("ML: Emergency ACKNOWLEDGED by operator — monitoring manually", source="ml")
        # Immediately hide the red frame and show the monitoring pill
        self.ml_emergency_frame.setVisible(False)
        self.ml_monitoring_label.setVisible(True)
        self._ml_ack_active = True
        # Write ack signal file for ML engine
        ack_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ml_ack_signal.txt"
        )
        try:
            with open(ack_file, "w") as f:
                f.write(str(time.time()))
        except:
            pass
        # Also send a user action via command to disarm
        self.send_cmd("ml_ack")



# ---------------------------------------------------------

def main():

    app=QApplication(sys.argv)

    win=SCADAWindow()

    win.showMaximized()

    sys.exit(app.exec_())


if __name__=="__main__":
    main()
