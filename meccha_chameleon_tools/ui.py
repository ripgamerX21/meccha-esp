#!/usr/bin/env python3
"""Qt5 overlay and menu widgets for MECCA CHAMELEON ESP."""
import math
import os
import ctypes
import time
import threading

from PyQt5.QtWidgets import (
    QApplication, QWidget, QCheckBox, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame,
    QSpinBox, QDoubleSpinBox, QListWidget, QStackedWidget, QScrollArea, QSizeGrip,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QBrush

from meccha_chameleon_tools.core import (
    MecchaESP, dist, rp, rfloat, wfloat
)
from meccha_chameleon_tools.config import Config, save_config, load_config
from meccha_chameleon_tools.translations import _tr


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------
def rotation_to_axes(rot):
    pitch, yaw, roll = [math.radians(x) for x in rot]
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    forward = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * cy, cr * cp)
    return forward, right, up


def w2s(world_pos, camera, screen_w, screen_h):
    """Project world pos to screen. Returns None only if behind camera."""
    cam_loc = camera["loc"]
    cam_rot = camera["rot"]
    fov = camera["fov"]
    forward, right, up = rotation_to_axes(cam_rot)
    dx = world_pos[0] - cam_loc[0]
    dy = world_pos[1] - cam_loc[1]
    dz = world_pos[2] - cam_loc[2]
    view_x = dx * forward[0] + dy * forward[1] + dz * forward[2]
    view_y = dx * right[0] + dy * right[1] + dz * right[2]
    view_z = dx * up[0] + dy * up[1] + dz * up[2]
    if view_x <= 0.1:
        return None
    aspect = screen_w / screen_h
    tan_hfov = math.tan(math.radians(fov) / 2.0)
    ndc_x = view_y / (view_x * tan_hfov)
    ndc_y = view_z / (view_x * tan_hfov / aspect)
    screen_x = (1.0 + ndc_x) * screen_w / 2.0
    screen_y = (1.0 - ndc_y) * screen_h / 2.0
    return (screen_x, screen_y)


def clamp_screen(x, y, w, h, margin=10):
    return (max(margin, min(w - margin, x)), max(margin, min(h - margin, y)))


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def draw_2d_box(painter, pos, camera, screen_w, screen_h,
                height_world, half_width_world, rot, color, scale=1.0, thickness=1):
    h = height_world * scale
    hw = half_width_world * scale
    corners_local = [
        (-hw, 0, -hw), (-hw, 0, hw), (hw, 0, hw), (hw, 0, -hw),
        (-hw, h, -hw), (-hw, h, hw), (hw, h, hw), (hw, h, -hw),
    ]
    pitch, yaw, _ = rot if rot else (0, 0, 0)
    yaw_rad = math.radians(yaw)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    pts = []
    for lx, ly, lz in corners_local:
        rx = lx * cy - lz * sy
        rz = lx * sy + lz * cy
        # Shift Z axis up by half height to center on body
        s = w2s((pos[0] + rx, pos[1] + ly, pos[2] + rz + (h / 2)), camera, screen_w, screen_h)
        if s:
            pts.append(s)
    if len(pts) < 4:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    painter.setPen(QPen(QColor(*color), thickness))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)))


def draw_corner_box(painter, pos, camera, screen_w, screen_h,
                    height_world, half_width_world, rot, color,
                    scale=1.0, length_ratio=0.25, thickness=2):
    h = height_world * scale
    hw = half_width_world * scale
    corners_local = [
        (-hw, 0, -hw), (-hw, 0, hw), (hw, 0, hw), (hw, 0, -hw),
        (-hw, h, -hw), (-hw, h, hw), (hw, h, hw), (hw, h, -hw),
    ]
    pitch, yaw, _ = rot if rot else (0, 0, 0)
    yaw_rad = math.radians(yaw)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    pts = []
    for lx, ly, lz in corners_local:
        rx = lx * cy - lz * sy
        rz = lx * sy + lz * cy
        s = w2s((pos[0] + rx, pos[1] + ly, pos[2] + rz + (h / 2)), camera, screen_w, screen_h)
        if s:
            pts.append(s)
    if len(pts) < 4:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = int(min(xs)), int(max(xs))
    min_y, max_y = int(min(ys)), int(max(ys))
    bw, bh = max_x - min_x, max_y - min_y
    if bw < 2 or bh < 2:
        return
    corner = max(4, int(min(bw, bh) * length_ratio))
    painter.setPen(QPen(QColor(*color), thickness))
    painter.drawLine(min_x, min_y, min_x + corner, min_y)
    painter.drawLine(min_x, min_y, min_x, min_y + corner)
    painter.drawLine(max_x - corner, min_y, max_x, min_y)
    painter.drawLine(max_x, min_y, max_x, min_y + corner)
    painter.drawLine(min_x, max_y - corner, min_x, max_y)
    painter.drawLine(min_x, max_y, min_x + corner, max_y)
    painter.drawLine(max_x - corner, max_y, max_x, max_y)
    painter.drawLine(max_x, max_y - corner, max_x, max_y)


def draw_radar(painter, cam, local_pos, players, radar_cx, radar_cy, radar_size, radar_range, color, opacity):
    half = radar_size / 2
    painter.setPen(QPen(QColor(255, 255, 255, opacity), 1))
    painter.setBrush(QBrush(QColor(0, 0, 0, opacity)))
    painter.drawEllipse(int(radar_cx - half), int(radar_cy - half), radar_size, radar_size)
    painter.drawLine(int(radar_cx - half), int(radar_cy), int(radar_cx + half), int(radar_cy))
    painter.drawLine(int(radar_cx), int(radar_cy - half), int(radar_cx), int(radar_cy + half))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 255, 0, 220))
    painter.drawEllipse(int(radar_cx - 2), int(radar_cy - 2), 5, 5)
    cam_yaw = math.radians(cam["rot"][1])
    for p in players:
        pos = p["pos"]
        dx = pos[0] - local_pos[0]
        dz = pos[2] - local_pos[2]
        d2d = math.sqrt(dx * dx + dz * dz)
        if d2d > radar_range or d2d < 1.0:
            continue
        angle = math.atan2(dx, dz) - cam_yaw
        r = (d2d / radar_range) * (half - 8)
        rx = radar_cx + r * math.sin(angle)
        ry = radar_cy - r * math.cos(angle)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*p.get("color", color), 220) if not p["is_local"] else QColor(0, 255, 0, 220))
        painter.drawEllipse(int(rx - 2), int(ry - 2), 5, 5)


# ---------------------------------------------------------------------------
# Key Recorder
# ---------------------------------------------------------------------------
class KeyRecorder:
    def __init__(self, on_record):
        self.on_record = on_record
        self.active = False
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._start_tick = 0

    def start(self):
        self.active = True
        self._start_tick = ctypes.windll.kernel32.GetTickCount()
        self._timer.start(50)

    def stop(self):
        self.active = False
        self._timer.stop()

    def _poll(self):
        elapsed = ctypes.windll.kernel32.GetTickCount() - self._start_tick
        if elapsed < 300:
            return
        for vk in range(1, 0x100):
            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                # Basic mapping for the most common keys
                key_names = {
                    0x01: "LMB", 0x02: "RMB", 0x04: "MMB", 0x05: "MB4", 0x06: "MB5",
                    0x10: "Shift", 0x11: "Ctrl", 0x12: "Alt", 0x20: "Space",
                }
                name = key_names.get(vk, f"VK_{vk:02X}")
                self.stop()
                self.on_record(name)
                return
        if elapsed > 5000:
            self.stop()


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
class Menu(QWidget):
    C = {
        "bg": "#0f1117", "surface": "#171a23", "surface2": "#1d212c",
        "surface3": "#252a37", "border": "#2b3140", "border2": "#353c4f",
        "text": "#d7dbe6", "text_dim": "#8b93a7", "text_faint": "#5b6275",
        "accent": "#5b8cff", "accent_d": "#3f6fe0", "accent2": "#9d7bff",
        "good": "#41d18f", "warn": "#ffb648", "bad": "#ff6b6b", "cyan": "#46d6e0",
    }

    STYLE = """
        QFrame#menuFrame {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #14171f, stop:1 #0d0f15);
            border: 1px solid #2b3140; border-radius: 14px;
        }
        QLabel { color: #d7dbe6; font-size: 11px; background: transparent; }
        QLabel#titleLbl { font-size: 16px; font-weight: bold; color: #aeb9ff;
            padding: 2px 0; letter-spacing: 2px; background: transparent; }
        QCheckBox { color: #c5cad8; font-size: 11px; spacing: 9px; padding: 3px 2px; background: transparent; }
        QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
            border: 1px solid #3a4253; background: #14171f; }
        QCheckBox::indicator:hover { border-color: #5b8cff; }
        QCheckBox::indicator:checked {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #5b8cff, stop:1 #9d7bff);
            border-color: #7d9bff;
        }
        QPushButton { background-color: #222737; color: #d7dbe6;
            border: 1px solid #2b3140; padding: 7px 12px; border-radius: 7px; font-size: 11px; }
        QPushButton:hover { background-color: #2c3349; border-color: #4a5470; }
        QPushButton#primaryBtn {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #5b8cff, stop:1 #3f6fe0);
            color: #fff; border: 1px solid #6d9bff; font-weight: bold; }
        QPushButton#primaryBtn:hover { background: #6d9bff; }
        QPushButton#dangerBtn { background-color: #3a2026; color: #ff9b9b; border-color: #5a2f38; }
        QPushButton#dangerBtn:hover { background-color: #4d2a31; color: #ffb3b3; }
        QSpinBox, QDoubleSpinBox { background-color: #14171f; color: #d7dbe6;
            border: 1px solid #2b3140; padding: 3px 8px; border-radius: 6px;
            font-size: 11px; min-height: 24px; }
        QSpinBox:focus, QDoubleSpinBox:focus { border-color: #5b8cff; }
    """

    def __init__(self, config: Config, esp: MecchaESP, tabs=None):
        super().__init__()
        self.config = config
        self.esp = esp
        self._active_tabs = tabs or ["ESP", "AIMBOT", "RADAR"]
        self.setWindowTitle("Meccha Chameleon ESP")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._container = None
        self._key_recorder = KeyRecorder(self._on_key_recorded)
        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._build_ui()
        self.resize(600, 640)
        self.setMinimumSize(520, 520)

    def _close_app(self):
        QApplication.quit()

    def _on_key_recorded(self, name):
        self.config.aimbot_key = name
        self.lbl_aim_key.setText(_tr("Aim Key: {key}", key=name))
        self.btn_record_key.setEnabled(True)
        self.btn_record_key.setText(_tr("Record Key"))

    def _start_aim_key_record(self):
        self.btn_record_key.setEnabled(False)
        self.btn_record_key.setText(_tr("Press key..."))
        self._key_recorder = KeyRecorder(self._on_key_recorded)
        self._key_recorder.start()

    def _build_ui(self):
        container = QFrame(self)
        container.setObjectName("menuFrame")
        self._container = container
        container.setStyleSheet(self.STYLE)
        outer = QVBoxLayout(container)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        header = QHBoxLayout()
        logo = QLabel("\u25c8")
        logo.setStyleSheet("color: #5b8cff; font-size: 18px;")
        title = QLabel(_tr("MECCA CHAMELEON ESP"))
        title.setObjectName("titleLbl")
        header.addWidget(logo)
        header.addWidget(title)
        header.addStretch()
        grip = QSizeGrip(container)
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("background: transparent;")
        header.addWidget(grip)
        outer.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color: {self.C['border']};")
        outer.addWidget(line)

        body = QHBoxLayout()
        body.setSpacing(10)

        self.tab_list = QListWidget()
        self.tab_list.setFixedWidth(124)
        self.tab_list.setFocusPolicy(Qt.NoFocus)
        self.tab_list.setStyleSheet(f"""
            QListWidget {{ background: {self.C['surface']}; border: 1px solid {self.C['border']};
                border-radius: 10px; padding: 6px; outline: none; }}
            QListWidget::item {{ color: {self.C['text_dim']}; padding: 9px 8px;
                border-radius: 7px; font-size: 11px; font-weight: bold; }}
            QListWidget::item:selected {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2f3a5a, stop:1 #283150);
                color: #aeb9ff; border-left: 3px solid #5b8cff; }}
            QListWidget::item:hover:!selected {{ background: {self.C['surface2']}; color: {self.C['text']}; }}
        """)
        for t in self._active_tabs:
            self.tab_list.addItem(f"{'◈' if t == 'ESP' else '⊕' if t == 'AIMBOT' else '◉'}  {_tr(t)}")
        self.tab_list.currentRowChanged.connect(self._switch_tab)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")

        self._pages = {}
        for tab_name in self._active_tabs:
            page = QWidget()
            page.setStyleSheet("background: transparent;")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet("background: transparent;")
            self._pages[tab_name] = page
            self.stack.addWidget(scroll)

        body.addWidget(self.tab_list)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_save = QPushButton(_tr("Save Config"))
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.clicked.connect(self._save_config)
        self.btn_load = QPushButton(_tr("Load Config"))
        self.btn_load.clicked.connect(self._load_config)
        self.btn_close = QPushButton(_tr("Close"))
        self.btn_close.setObjectName("dangerBtn")
        self.btn_close.clicked.connect(self._close_app)
        hint = QLabel(_tr("Ins/F1 toggle | Drag to move | END = Exit"))
        hint.setStyleSheet(f"color: {self.C['text_faint']}; font-size: 9px;")
        bar.addWidget(self.btn_save)
        bar.addWidget(self.btn_load)
        bar.addWidget(self.btn_close)
        bar.addStretch()
        bar.addWidget(hint)
        outer.addLayout(bar)

        self._outer_layout.addWidget(container)

        if "ESP" in self._active_tabs:
            self._build_esp_tab()
        if "AIMBOT" in self._active_tabs:
            self._build_aimbot_tab()
        if "RADAR" in self._active_tabs:
            self._build_radar_tab()

    def _switch_tab(self, idx):
        if 0 <= idx < len(self._active_tabs):
            self.stack.setCurrentIndex(idx)

    def _section_header(self, text, color=None):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 11px; font-weight: bold; letter-spacing: 1px; "
            f"color: {color or self.C['accent']}; padding: 2px 0;"
        )
        return lbl

    def _card(self, spacing=6, margins=(12, 10, 12, 10)):
        card = QFrame()
        card.setFrameShape(QFrame.StyledPanel)
        card.setStyleSheet(
            f"QFrame {{ background: {self.C['surface']}; border: 1px solid {self.C['border']}; "
            f"border-radius: 10px; }}"
        )
        lo = QVBoxLayout(card)
        lo.setContentsMargins(*margins)
        lo.setSpacing(spacing)
        return card, lo

    def _labeled_row(self, label, widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel(label))
        row.addStretch(1)
        row.addWidget(widget)
        return row

    def _spin(self, value, vmin, vmax, step, on_change, double=False):
        spn = QDoubleSpinBox() if double else QSpinBox()
        spn.setRange(vmin, vmax)
        spn.setSingleStep(step)
        spn.setValue(value)
        spn.valueChanged.connect(on_change)
        return spn

    def _chk(self, text, attr):
        cb = QCheckBox(text)
        cb.setChecked(getattr(self.config, attr))
        cb.stateChanged.connect(lambda s, a=attr: setattr(self.config, a, bool(s)))
        return cb

    def _build_esp_tab(self):
        p = self._pages["ESP"]
        lo = QVBoxLayout(p)
        lo.setContentsMargins(4, 4, 4, 4)
        lo.setSpacing(10)

        self.cb_enabled = self._chk(_tr("ESP Enabled"), "enabled")
        self.cb_enabled.setStyleSheet("font-size: 12px; font-weight: bold; color: #aeb9ff;")
        lo.addWidget(self.cb_enabled)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("DISPLAY MODE")))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self._chk(_tr("Dot"), "dot_esp"))
        row.addWidget(self._chk(_tr("2D Box"), "box_esp"))
        row.addWidget(self._chk(_tr("Corner Box"), "corner_box"))
        clo.addLayout(row)

        dr = QHBoxLayout()
        dr.addWidget(QLabel(_tr("Dot Radius:")))
        self.spn_dot = QSpinBox()
        self.spn_dot.setRange(2, 32)
        self.spn_dot.setValue(self.config.dot_radius)
        self.spn_dot.valueChanged.connect(lambda v: setattr(self.config, "dot_radius", v))
        dr.addWidget(self.spn_dot)
        dr.addStretch(1)
        dr.addWidget(self._chk(_tr("Dist. Scaling"), "distance_scaling"))
        clo.addLayout(dr)
        lo.addWidget(card)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("ELEMENTS")))
        for cfg, label in [
            ("show_local", _tr("Show Local Player")),
            ("show_names", _tr("Show Names")),
            ("show_distance", _tr("Show Distance")),
            ("snap_lines", _tr("Snap Lines")),
            ("enemy_only", _tr("Enemy Only")),
            ("show_roles", _tr("Show Roles")),
            ("team_filter", _tr("Team Filter")),
            ("invincible_detect", _tr("Detect Invincible")),
        ]:
            clo.addWidget(self._chk(label, cfg))
        lo.addWidget(card)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("PER-ROLE VISUALS"), self.C["accent2"]))
        clo.addWidget(self._chk(_tr("Hunter ESP"), "hunter_esp"))
        clo.addWidget(self._chk(_tr("Survivor ESP"), "survivor_esp"))
        lo.addWidget(card)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("APPEARANCE")))
        clo.addLayout(self._labeled_row(
            _tr("Line Thickness:"),
            self._spin(self.config.line_thickness, 1, 8, 1,
                       lambda v: setattr(self.config, "line_thickness", v)),
        ))
        lo.addWidget(card)

        lo.addStretch()

    def _build_aimbot_tab(self):
        p = self._pages["AIMBOT"]
        lo = QVBoxLayout(p)
        lo.setContentsMargins(4, 4, 4, 4)
        lo.setSpacing(10)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("AIMBOT"), self.C["bad"]))
        
        self.cb_aimbot = self._chk(_tr("Aimbot Enabled"), "aimbot_enabled")
        self.cb_aim_fov = self._chk(_tr("Show FOV Circle"), "aimbot_show_fov")
        self.cb_debug = self._chk(_tr("Show Debug Console"), "show_debug")
        
        clo.addWidget(self.cb_aimbot)
        clo.addWidget(self.cb_aim_fov)
        clo.addWidget(self.cb_debug)

        kr_lbl = QLabel(_tr("Aim Key: {key}", key=self.config.aimbot_key))
        kr_btn = QPushButton(_tr("Record Key"))
        kr_btn.clicked.connect(self._start_aim_key_record)
        self.lbl_aim_key = kr_lbl
        self.btn_record_key = kr_btn
        
        kr = QHBoxLayout()
        kr.addWidget(kr_lbl)
        kr.addStretch(1)
        kr.addWidget(kr_btn)
        clo.addLayout(kr)

        clo.addLayout(self._labeled_row(
            _tr("FOV Radius:"),
            self._spin(self.config.aimbot_fov, 10, 600, 1,
                       lambda v: setattr(self.config, "aimbot_fov", v)),
        ))
        
        clo.addLayout(self._labeled_row(
            _tr("Smooth:"),
            self._spin(self.config.aimbot_smooth, 0.01, 1.0, 0.05,
                       lambda v: setattr(self.config, "aimbot_smooth", v), double=True),
        ))
        
        clo.addLayout(self._labeled_row(
            _tr("Target Offset:"),
            self._spin(int(self.config.aimbot_target_offset), -200, 200, 1,
                       lambda v: setattr(self.config, "aimbot_target_offset", float(v))),
        ))
        
        lo.addWidget(card)
        lo.addStretch()

    def _build_radar_tab(self):
        p = self._pages["RADAR"]
        lo = QVBoxLayout(p)
        lo.setContentsMargins(4, 4, 4, 4)
        lo.setSpacing(10)

        card, clo = self._card()
        clo.addWidget(self._section_header(_tr("RADAR"), self.C["cyan"]))
        clo.addWidget(self._chk(_tr("Radar Enabled"), "radar_enabled"))
        clo.addLayout(self._labeled_row(
            _tr("Radar Size:"),
            self._spin(self.config.radar_size, 80, 400, 1,
                       lambda v: setattr(self.config, "radar_size", v)),
        ))
        clo.addLayout(self._labeled_row(
            _tr("Radar Range:"),
            self._spin(int(self.config.radar_range), 1000, 50000, 500,
                       lambda v: setattr(self.config, "radar_range", float(v))),
        ))
        lo.addWidget(card)
        lo.addStretch()

    def _save_config(self):
        if save_config(self.config):
            self.btn_save.setText(_tr("Config Saved!"))
            QTimer.singleShot(1500, lambda: self.btn_save.setText(_tr("Save Config")))
        else:
            self.btn_save.setText(_tr("Save Failed!"))
            QTimer.singleShot(1500, lambda: self.btn_save.setText(_tr("Save Config")))

    def _load_config(self):
        loaded = load_config()
        from dataclasses import fields as dc_fields
        for field in dc_fields(self.config):
            if hasattr(loaded, field.name):
                setattr(self.config, field.name, getattr(loaded, field.name))
        self.btn_load.setText(_tr("Config Loaded!"))
        QTimer.singleShot(1500, lambda: self.btn_load.setText(_tr("Load Config")))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
class Overlay(QWidget):
    PLAYER_GRACE_S = 0.5

    AIM_KEY_VK = {
        "LMB": 0x01, "RMB": 0x02, "MMB": 0x04, "MB4": 0x05, "MB5": 0x06,
        "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10,
        "Ctrl": 0x11, "Alt": 0x12, "Pause": 0x13, "Esc": 0x1B, "Space": 0x20,
        "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24,
        "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
        "Insert": 0x2D, "Delete": 0x2E,
        "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
        "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
        "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
        "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
        "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
        "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
        "Y": 0x59, "Z": 0x5A,
        "Num0": 0x60, "Num1": 0x61, "Num2": 0x62, "Num3": 0x63, "Num4": 0x64,
        "Num5": 0x65, "Num6": 0x66, "Num7": 0x67, "Num8": 0x68, "Num9": 0x69,
        "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
        "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
        "F11": 0x7A, "F12": 0x7B,
        ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
        "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    }

    def __init__(self, esp: MecchaESP, config: Config):
        super().__init__()
        self.esp = esp
        self.config = config
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle("Meccha Chameleon ESP - Overlay")
        self._key_states = {}
        self._cursor_shown = True

        self._snapshot = {"cam": None, "players": [], "actors": [], "ok": False}
        self._snapshot_lock = threading.Lock()
        self._cached_w = 1920
        self._cached_h = 1080
        self._tracked = {}
        self._esp_running = True
        self._esp_thread = threading.Thread(target=self._esp_worker, daemon=True)
        self._esp_thread.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_overlay)
        self.timer.start(16)

        self.game_hwnd = self._find_game_window()
        self._resize_to_game()
        self._resize_counter = 0

        self.key_timer = QTimer(self)
        self.key_timer.timeout.connect(self._poll_keys)
        self.key_timer.start(50)

    def _find_game_window(self):
        try:
            import win32gui
            return win32gui.FindWindow(None, "Chameleon  ")
        except Exception:
            return 0

    def _resize_to_game(self):
        try:
            import win32gui
            if self.game_hwnd:
                rect = win32gui.GetClientRect(self.game_hwnd)
                tl = win32gui.ClientToScreen(self.game_hwnd, (rect[0], rect[1]))
                br = win32gui.ClientToScreen(self.game_hwnd, (rect[2], rect[3]))
                self.setGeometry(tl[0], tl[1], br[0] - tl[0], br[1] - tl[1])
            else:
                self.setGeometry(0, 0, 1920, 1080)
        except Exception:
            self.setGeometry(0, 0, 1920, 1080)

    def update_overlay(self):
        self._cached_w = self.width()
        self._cached_h = self.height()
        self._resize_counter = getattr(self, "_resize_counter", 0) + 1
        if self._resize_counter % 15 == 0:
            self._resize_to_game()
        self.update()

    def closeEvent(self, event):
        self._esp_running = False
        super().closeEvent(event)

    def _poll_keys(self):
        VK_INSERT = 0x2D
        VK_END = 0x23
        for vk, name in [(VK_INSERT, "insert"), (0x70, "f1")]:
            state = ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
            if state and not self._key_states.get(name):
                for w in QApplication.topLevelWidgets():
                    if isinstance(w, Menu):
                        w.setVisible(not w.isVisible())
                        break
            self._key_states[name] = bool(state)
        end_down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_END) & 0x8000)
        if end_down and not self._key_states.get("end"):
            QApplication.quit()
        self._key_states["end"] = end_down
        cursor_should_be = not self.config.show_cursor
        if cursor_should_be != self._cursor_shown:
            while ctypes.windll.user32.ShowCursor(cursor_should_be) >= 0:
                pass
            while ctypes.windll.user32.ShowCursor(not cursor_should_be) < 0:
                pass
            ctypes.windll.user32.ShowCursor(cursor_should_be)
            self._cursor_shown = cursor_should_be

    def _esp_worker(self):
        while self._esp_running:
            try:
                if not self.config.enabled:
                    with self._snapshot_lock:
                        self._snapshot = {"cam": None, "players": [], "actors": [], "ok": False, "off": True}
                    self._tracked.clear()
                    time.sleep(0.05)
                    continue
                snap = self._build_snapshot()
                if snap is not None:
                    with self._snapshot_lock:
                        self._snapshot = snap
                    self._apply_aim(snap)
            except Exception:
                pass
            time.sleep(0.002)

    def _build_snapshot(self):
        cam = self.esp.get_camera()
        if not cam:
            return {"cam": None, "players": [], "actors": [], "ok": False, "off": False}
        cfg = self.config
        now = time.monotonic()
        current = {}
        try:
            for pdata in self.esp.iter_players(
                include_local=False, # Hardcoded to false to prevent aiming at self
                team_filter=cfg.team_filter,
                enemy_only=cfg.enemy_only,
            ):
                is_local = pdata["is_local"]
                actor = pdata["actor"]
                ps = pdata["player_state"]
                entry = {
                    "is_local": is_local, "pos": pdata["pos"], "actor": actor,
                    "player_state": ps, "idx": pdata["idx"],
                    "role": pdata.get("role", "Unknown"),
                    "is_enemy": pdata.get("is_enemy", False),
                    "is_hunter": pdata.get("is_hunter", False),
                    "is_survivor": pdata.get("is_survivor", False),
                    "invincible": False, "rot": None,
                }
                if cfg.invincible_detect and not is_local:
                    entry["invincible"] = self.esp.get_invincible(actor)
                if actor and (cfg.box_esp or cfg.corner_box):
                    entry["rot"] = self.esp.get_actor_root_rotation(actor)
                current[ps] = entry
                self._tracked[ps] = {"data": entry, "last_seen": now}
        except Exception:
            pass

        players = []
        for ps, tracked in list(self._tracked.items()):
            if ps in current:
                players.append(current[ps])
            elif now - tracked["last_seen"] <= self.PLAYER_GRACE_S:
                players.append(tracked["data"])
            else:
                del self._tracked[ps]

        actors = []
        if cfg.draw_all:
            try:
                for adata in self.esp.iter_actors(max_actors=500, class_filter="Collectible"):
                    d = dist(adata["pos"], cam["loc"])
                    if d > cfg.draw_all_max_distance:
                        continue
                    actors.append({"pos": adata["pos"], "class_name": adata.get("class_name", "")})
            except Exception:
                pass

        return {"cam": cam, "players": players, "actors": actors, "ok": True, "off": False}

    def _apply_aim(self, snap):
        cfg = self.config
        if not cfg.aimbot_enabled:
            return
        cam = snap.get("cam")
        if not cam:
            return
        
        w = self._cached_w
        h = self._cached_h
        best_target = self._find_best_target(cam, w, h)
        key_held = self._aim_key_held()
        
        if cfg.show_debug:
            print(f"[AIM-DEBUG] key_held={key_held} best_target={best_target[0] if best_target else None}")
            
        if best_target and key_held:
            self._aim_at(best_target[0], best_target[1])

    def _aim_key_held(self):
        vk = self.AIM_KEY_VK.get(self.config.aimbot_key, 0x06)
        held = bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
        if self.config.show_debug:
            print(f"[AIM-DEBUG] aim_key='{self.config.aimbot_key}' vk={hex(vk)} held={held}")
        return held

    def _find_best_target(self, camera, screen_w, screen_h):
        world = self.esp._get_world()
        local_pc = self.esp._get_local_controller(world) if world else 0
        local_pawn = rp(self.esp.pm, local_pc + self.esp.offsets["APlayerController::AcknowledgedPawn"]) if local_pc else 0
        local_pos = self.esp._actor_position(local_pawn) if local_pawn else None

        cx, cy = screen_w / 2, screen_h / 2
        cam_loc = camera["loc"]

        if not local_pawn:
            if self.config.show_debug:
                print("[AIM-DEBUG] local pawn not resolved; refusing to aim")
            return None

        best_dist = float("inf")
        best_target = None
        count = 0
        
        # Note: Removed players_only=True because core.py doesn't support it
        for pdata in self.esp.iter_players(include_local=False):
            if pdata["is_local"]:
                continue
            pos = pdata["pos"]
            
            if local_pos:
                dself = math.sqrt((pos[0] - local_pos[0]) ** 2 +
                                  (pos[1] - local_pos[1]) ** 2 +
                                  (pos[2] - local_pos[2]) ** 2)
                if dself < 150.0:
                    continue
                    
            dcam = math.sqrt((pos[0] - cam_loc[0]) ** 2 +
                             (pos[1] - cam_loc[1]) ** 2 +
                             (pos[2] - cam_loc[2]) ** 2)
            if dcam < 100.0:
                continue
                
            count += 1
            aim_pos = (pos[0], pos[1], pos[2] + self.config.aimbot_target_offset)
            s = w2s(aim_pos, camera, screen_w, screen_h)
            if not s:
                continue
                
            dx = s[0] - cx
            dy = s[1] - cy
            d = math.sqrt(dx * dx + dy * dy)
            
            if d <= self.config.aimbot_fov and d < best_dist:
                best_dist = d
                best_target = (aim_pos, camera)
                
        if self.config.show_debug:
            print(f"[AIM-DEBUG] candidates={count} best_target={best_target[0] if best_target else None} fov={self.config.aimbot_fov}")
        return best_target

    def _vector_to_rotation(self, vec):
        x, y, z = vec
        length = math.sqrt(x * x + y * y + z * z)
        if length == 0:
            return (0.0, 0.0, 0.0)
        x, y, z = x / length, y / length, z / length
        pitch = -math.degrees(math.asin(z))
        yaw = math.degrees(math.atan2(y, x))
        return (pitch, yaw, 0.0)

    def _read_control_rotation(self):
        world = self.esp._get_world()
        if not world:
            return None
        pc = self.esp._get_local_controller(world)
        if not pc:
            return None
        addr = pc + self.esp.offsets["AController::ControlRotation"]
        rot = (rfloat(self.esp.pm, addr), rfloat(self.esp.pm, addr + 4), rfloat(self.esp.pm, addr + 8))
        if self.config.show_debug:
            print(f"[AIM-DEBUG] ControlRotation={rot} addr={hex(addr)} pc={hex(pc)}")
        return rot

    def _write_control_rotation(self, rot):
        world = self.esp._get_world()
        if not world:
            return False
        pc = self.esp._get_local_controller(world)
        if not pc:
            return False
        addr = pc + self.esp.offsets["AController::ControlRotation"]
        ok = (wfloat(self.esp.pm, addr, rot[0]) and
              wfloat(self.esp.pm, addr + 4, rot[1]) and
              wfloat(self.esp.pm, addr + 8, rot[2]))
        if self.config.show_debug:
            print(f"[AIM-DEBUG] write ControlRotation={rot} ok={ok}")
        return ok

    def _aim_at(self, target_pos, camera):
        if not camera:
            return
        current = self._read_control_rotation()
        if current is None:
            return
        dx = target_pos[0] - camera["loc"][0]
        dy = target_pos[1] - camera["loc"][1]
        dz = target_pos[2] - camera["loc"][2]
        target_rot = self._vector_to_rotation((dx, dy, dz))
        smooth = self.config.aimbot_smooth
        new_pitch = current[0] + (target_rot[0] - current[0]) * smooth
        new_yaw = current[1] + (target_rot[1] - current[1]) * smooth
        self._write_control_rotation((new_pitch, new_yaw, current[2]))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Consolas", 10)
        painter.setFont(font)
        w, h = self.width(), self.height()
        with self._snapshot_lock:
            snap = self._snapshot

        if snap.get("off") or not self.config.enabled:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(10, 20, _tr("ESP OFF"))
            return
        cam = snap.get("cam")
        if not cam:
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.drawText(10, 20, _tr("NO CAMERA"))
            return

        all_players = snap.get("players", [])
        local_pos = None
        for p in all_players:
            if p["is_local"]:
                local_pos = p["pos"]
                break

        for pdata in all_players:
            is_local = pdata["is_local"]
            pos = pdata["pos"]
            idx = pdata["idx"]
            role = pdata.get("role", "Unknown")
            d = dist(pos, cam["loc"])
            scale = 1.0
            if self.config.distance_scaling and d > 0:
                scale = max(0.3, min(self.config.scale_reference_dist / d, 3.0))
            screen_center = w2s(pos, cam, w, h)
            if not screen_center:
                continue
            sx, sy = screen_center
            sy += self.config.box_y_offset

            is_hunter = pdata.get("is_hunter", False)
            is_survivor = pdata.get("is_survivor", False)
            if not is_local:
                if is_hunter and not self.config.hunter_esp:
                    continue
                if is_survivor and not self.config.survivor_esp:
                    continue
            invincible = pdata.get("invincible", False)
            if is_local:
                color = self.config.local_color
            elif invincible:
                color = self.config.invincible_color
            elif is_hunter:
                color = self.config.hunter_visual_color
            elif is_survivor:
                color = self.config.survivor_visual_color
            else:
                color = self.config.enemy_color

            dsx, dsy = clamp_screen(sx, sy, w, h)
            if self.config.dot_esp:
                radius = max(2, int(self.config.dot_radius * scale))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(*color))
                painter.drawEllipse(int(dsx - radius), int(dsy - radius), radius * 2, radius * 2)

            rot = pdata.get("rot")
            hw = self.config.box_height_world / 3.0
            pen_width = max(1, self.config.line_thickness)
            if self.config.box_esp and not self.config.corner_box:
                draw_2d_box(painter, pos, cam, w, h,
                            self.config.box_height_world, hw, rot, color, scale, pen_width)
            if self.config.corner_box:
                draw_corner_box(painter, pos, cam, w, h,
                                self.config.box_height_world, hw, rot, color, scale, 0.25, pen_width)

            if self.config.snap_lines:
                painter.setPen(QPen(QColor(*color), 1))
                painter.drawLine(int(w / 2), int(h / 2), int(sx), int(sy)) # Traces from crosshair

            label_parts = []
            if self.config.show_names:
                label_parts.append(_tr("YOU") if is_local else _tr("Enemy {idx}", idx=idx))
            if self.config.show_roles and role != "Unknown":
                label_parts.append(_tr(role))
            if invincible:
                label_parts.append(_tr("INVINCIBLE"))
            if self.config.show_distance:
                label_parts.append(f"{int(d / 100)}m")
            if label_parts:
                painter.setPen(QPen(QColor(*color)))
                painter.drawText(int(dsx + self.config.dot_radius * scale + 4),
                                 int(dsy), " | ".join(label_parts))

        if self.config.draw_all:
            actor_count = 0
            for adata in snap.get("actors", []):
                s = w2s(adata["pos"], cam, w, h)
                if not s:
                    continue
                actor_count += 1
                painter.setPen(QPen(QColor(100, 255, 100), 1))
                sx_a, sy_a = int(s[0]), int(s[1])
                painter.drawEllipse(sx_a - 2, sy_a - 2, 4, 4)
                if self.config.draw_all_names:
                    painter.drawText(sx_a + 4, sy_a + 4,
                                     (adata["class_name"] or "Actor")[:20])
            if actor_count > 0:
                painter.setPen(QPen(QColor(150, 255, 150)))
                painter.drawText(w - 200, 60, _tr("Items: {count}", count=actor_count))

        non_local = [p for p in all_players if not p["is_local"]]
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(10, 20, _tr("Players: {count}", count=len(non_local)))

        # --- Aimbot FOV Circle ---
        if self.config.aimbot_enabled:
            cx, cy = w / 2, h / 2
            if self.config.aimbot_show_fov:
                painter.setPen(QPen(QColor(255, 255, 255), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(int(cx - self.config.aimbot_fov),
                                    int(cy - self.config.aimbot_fov),
                                    self.config.aimbot_fov * 2,
                                    self.config.aimbot_fov * 2)

        if self.config.radar_enabled and local_pos:
            radar_x = w - self.config.radar_size - 20
            radar_y = 20 + self.config.radar_size // 2
            enemy_list = [p for p in all_players if not p["is_local"]]
            for p in enemy_list:
                p["color"] = self.config.enemy_color
            draw_radar(painter, cam, local_pos, enemy_list,
                       radar_x, radar_y,
                       self.config.radar_size, self.config.radar_range,
                       self.config.radar_color, self.config.radar_opacity)
                       radar_x, radar_y,
                       self.config.radar_size, self.config.radar_range,
                       self.config.radar_color, self.config.radar_opacity)
