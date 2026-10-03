# Qt widgets for the desktop UI: video view, status badges, settings panel and theme.

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget)

import gestures
import pipeline
import settings_store

C_BG = "#0f1216"
C_PANEL = "#151a21"
C_CARD = "#1b212b"
C_BORDER = "#262e3a"
C_TEXT = "#e6e9ef"
C_MUTED = "#8b93a1"
C_ACCENT = "#3b82f6"
C_GOOD = "#22c55e"
C_WARN = "#f59e0b"
C_BAD = "#ef4444"

MODE_COLORS = {
    gestures.MODE_IDLE: ("#475569", "#e2e8f0"),
    gestures.MODE_POINTER: ("#2563eb", "#ffffff"),
    gestures.MODE_PAGE: ("#9333ea", "#ffffff"),
}

THEME_QSS = """
QWidget { background: %(bg)s; color: %(text)s; font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget { background: %(bg)s; }
#Sidebar, #Sidebar QWidget { background: %(panel)s; }
#Card { background: %(card)s; border: 1px solid %(border)s; border-radius: 10px; }
#Card QLabel { background: transparent; }
#Muted { color: %(muted)s; }
#Section { color: %(muted)s; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }
QGroupBox { background: %(card)s; border: 1px solid %(border)s; border-radius: 10px;
            margin-top: 18px; padding: 10px 10px 6px 10px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 8px;
                   padding: 0 4px; color: %(muted)s; font-weight: 600; }
QGroupBox QWidget, QGroupBox QLabel { background: transparent; }
QPushButton { background: #232b37; border: 1px solid #2f3947; border-radius: 8px;
              padding: 8px 14px; color: %(text)s; font-weight: 600; }
QPushButton:hover { background: #2a3442; }
QPushButton:pressed { background: #1e2530; }
QPushButton:disabled { color: #5b6371; border-color: #262e3a; }
QPushButton#Primary { background: %(accent)s; border-color: %(accent)s; color: #ffffff; }
QPushButton#Primary:hover { background: #4b8ff8; }
QSlider::groove:horizontal { height: 4px; background: #2a323e; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(text)s; width: 14px; height: 14px;
                             margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:focus { background: #ffffff; border: 2px solid %(accent)s; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
                       border: 1px solid #3a4453; background: #1e252f; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2f3947; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: %(card)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; }
QStatusBar { background: %(panel)s; color: %(muted)s; border-top: 1px solid %(border)s; }
""" % {"bg": C_BG, "panel": C_PANEL, "card": C_CARD, "border": C_BORDER, "text": C_TEXT,
       "muted": C_MUTED, "accent": C_ACCENT}


class VideoView(QWidget):
    # Paints the newest frame scaled to fit, keeping its aspect ratio.
    def __init__(self):
        super().__init__()
        self._img = None
        self._message = "Starting camera..."
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_frame(self, frame):
        h, w = frame.shape[:2]
        # copy() so the QImage owns its pixels and does not point into numpy memory.
        self._img = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self._message = ""
        self.update()

    def set_message(self, text):
        if self._img is None and text == self._message:
            return
        self._img = None
        self._message = text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0d11"))
        if self._img is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            iw, ih = self._img.width(), self._img.height()
            scale = min(self.width() / float(iw), self.height() / float(ih))
            dw, dh = iw * scale, ih * scale
            p.drawImage(QRectF((self.width() - dw) / 2.0, (self.height() - dh) / 2.0, dw, dh), self._img)
        else:
            p.setPen(QColor(C_MUTED))
            f = p.font()
            f.setPointSize(13)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        p.end()


class Badge(QLabel):
    # Color-coded pill. Restyles only when its state actually changes.
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key = None

    def show_state(self, key, text, bg, fg):
        if key == self._key:
            return
        self._key = key
        self.setText(text)
        self.setStyleSheet("QLabel { background: %s; color: %s; border-radius: 12px;"
                           " padding: 4px 14px; font-weight: 700; }" % (bg, fg))


class StatusPanel(QFrame):
    # Same facts as the old HUD: armed, mode, fingers, hand, fps and delay.
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)

        self.camera = QLabel()
        self.armed = Badge()
        self.mode = Badge()
        self.fingers = QLabel()
        self.hand = QLabel()
        self.stats = QLabel()
        self.stats.setObjectName("Muted")

        rows = [("Camera", self.camera), ("Control", self.armed), ("Mode", self.mode),
                ("Fingers", self.fingers), ("Hand", self.hand)]
        for r, (name, w) in enumerate(rows):
            lab = QLabel(name)
            lab.setObjectName("Muted")
            grid.addWidget(lab, r, 0)
            grid.addWidget(w, r, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.stats, len(rows), 0, 1, 2)
        grid.setColumnStretch(1, 1)
        self._last = None

    def update_from(self, s):
        key = (s.cam_state, s.cam_error, s.cam_detail, s.cam_size, s.armed, s.mode, s.switching, s.fingers,
               s.hand_seen, int(s.fps), int(s.detect_ms), int(s.latency_ms / 5))
        if key == self._last:
            return
        self._last = key

        if s.cam_state == pipeline.CAM_ON:
            self.camera.setText("<span style='color:%s'>&#9679;</span> Live %dx%d"
                                % (C_GOOD, s.cam_size[0], s.cam_size[1]))
        elif s.cam_state == pipeline.CAM_STARTING:
            self.camera.setText("<span style='color:%s'>&#9679;</span> %s"
                                % (C_WARN, s.cam_detail or "Starting..."))
        elif s.cam_state == pipeline.CAM_ERROR:
            self.camera.setText("<span style='color:%s'>&#9679;</span> Error" % C_BAD)
        else:
            self.camera.setText("<span style='color:%s'>&#9679;</span> Off" % C_MUTED)

        if s.armed:
            self.armed.show_state("on", "●  ARMED", C_GOOD, "#052e16")
        else:
            self.armed.show_state("off", "●  DISARMED", "#3b1f24", "#fca5a5")

        bg, fg = MODE_COLORS.get(s.mode, MODE_COLORS[gestures.MODE_IDLE])
        text = s.mode + ("  … switching" if s.switching else "")
        self.mode.show_state((s.mode, s.switching), text, bg, fg)

        if s.hand_seen:
            dots = "".join("●" if i < s.fingers else "○" for i in range(4))
            self.fingers.setText("<b>%d</b>&nbsp;&nbsp;<span style='color:%s; letter-spacing:3px'>%s</span>"
                                 % (s.fingers, C_ACCENT, dots))
            self.hand.setText("<span style='color:%s'>Detected</span>" % C_GOOD)
        else:
            self.fingers.setText("<span style='color:%s'>&mdash;&nbsp;&nbsp;○○○○</span>" % C_MUTED)
            self.hand.setText("<span style='color:%s'>No hand</span>" % C_WARN)

        if s.cam_state == pipeline.CAM_ON:
            self.stats.setText("%.0f fps  ·  detect %.0f ms  ·  delay %.0f ms"
                               % (s.fps, s.detect_ms, s.latency_ms))
        else:
            self.stats.setText(s.cam_error if s.cam_state == pipeline.CAM_ERROR else "")


class FocusSlider(QSlider):
    # Ignores the mouse wheel unless clicked first, so scrolling the panel cannot change a value.
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SliderRow(QWidget):
    changed = Signal(str, object)

    def __init__(self, spec, value):
        super().__init__()
        self.spec = spec
        self.setToolTip(spec.tip)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 4)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(QLabel(spec.label))
        top.addStretch(1)
        self.readout = QLabel()
        self.readout.setObjectName("Muted")
        top.addWidget(self.readout)
        v.addLayout(top)
        self.slider = FocusSlider()
        steps = int(round((spec.hi - spec.lo) / spec.step))
        self.slider.setRange(0, steps)
        self.slider.setPageStep(max(1, steps // 10))
        v.addWidget(self.slider)
        self.set_value(value)
        self.slider.valueChanged.connect(self._on_slider)

    def set_value(self, value):
        # Shows the exact stored value, even if it sits between slider steps.
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((value - self.spec.lo) / self.spec.step)))
        self.slider.blockSignals(False)
        self.readout.setText(self.spec.fmt(value))

    def _on_slider(self, idx):
        value = self.spec.clamp(self.spec.lo + idx * self.spec.step)
        self.readout.setText(self.spec.fmt(value))
        self.changed.emit(self.spec.key, value)


class SettingsPanel(QWidget):
    # Grouped sliders built from settings_store.SLIDERS, plus the tilt direction swap.
    changed = Signal(str, object)

    def __init__(self, values):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        groups = {}
        for name in ("Pointer", "Click", "Scroll", "Tilt", "Camera"):
            box = QGroupBox(name)
            lay = QVBoxLayout(box)
            lay.setContentsMargins(10, 4, 10, 8)
            lay.setSpacing(2)
            groups[name] = lay
            outer.addWidget(box)
        for spec in settings_store.SLIDERS:
            row = SliderRow(spec, values[spec.key])
            row.changed.connect(self.changed)
            groups[spec.group].addWidget(row)

        self.swap = QCheckBox("Swap back / forward")
        self.swap.setToolTip("Which two-finger tilt direction means back and which means forward.")
        self.swap.setChecked(bool(values["swipe_swap"]))
        self.swap_note = QLabel()
        self.swap_note.setObjectName("Muted")
        self._show_swap(self.swap.isChecked())
        self.swap.toggled.connect(self._on_swap)
        groups["Tilt"].addSpacing(6)
        groups["Tilt"].addWidget(self.swap)
        groups["Tilt"].addWidget(self.swap_note)

        self.low_light = QCheckBox("Low-light boost")
        self.low_light.setToolTip("Raises local contrast in the copy of each frame used for hand detection.")
        self.low_light.setChecked(bool(values["low_light"]))
        self.low_light.toggled.connect(lambda on: self.changed.emit("low_light", bool(on)))
        low_note = QLabel("For dim rooms only, and it can also make detection worse: toggle it and watch the Hand row. The video here stays as the camera sees it.")
        low_note.setObjectName("Muted")
        low_note.setWordWrap(True)
        groups["Camera"].addWidget(self.low_light)
        groups["Camera"].addWidget(low_note)
        outer.addStretch(1)

    def _show_swap(self, on):
        if on:
            self.swap_note.setText("Tilt left = Forward,  tilt right = Back")
        else:
            self.swap_note.setText("Tilt left = Back,  tilt right = Forward")

    def _on_swap(self, on):
        self._show_swap(on)
        self.changed.emit("swipe_swap", bool(on))
