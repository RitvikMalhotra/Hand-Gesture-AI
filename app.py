# Desktop UI entry point. python main.py stays the diagnostic CLI (--record, --replay, --log).

import os
import sys


def _base_dir():
    # Where bundled files live: the PyInstaller folder when frozen, this folder otherwise.
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _redirect_output():
    # A windowed exe has no console, so prints go to a log file instead of nowhere.
    if sys.stdout is not None and sys.stderr is not None:
        return None
    import settings_store
    os.makedirs(settings_store.app_dir(), exist_ok=True)
    path = os.path.join(settings_store.app_dir(), "app.log")
    f = open(path, "w", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f
    return path


LOG_PATH = _redirect_output()

# mediapipe imports matplotlib; a non-GUI backend keeps it away from Qt.
os.environ.setdefault("MPLBACKEND", "Agg")

import config

# Relative model path only worked when started from the project folder.
config.MODEL_PATH = os.path.join(_base_dir(), "models", "hand_landmarker.task")
# PNG, not .ico: Qt reads PNG natively, .ico needs the qico plugin to be bundled too.
ICON_PATH = os.path.join(_base_dir(), "app_icon_1024.png")

# mediapipe must load before PySide6: its import hook crashes on six, which mediapipe pulls in.
import controller as ctrl
import pipeline
import settings_store

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

import ui_widgets


class MainWindow(QMainWindow):
    def __init__(self, pipe, values):
        super().__init__()
        self.pipe = pipe
        self.values = values
        self._closing = False
        self._last_frame = None
        self._last_cam_state = None

        self.setWindowTitle("Hand Gesture Control")
        self.resize(1200, 740)
        self.setMinimumSize(900, 560)

        self.video = ui_widgets.VideoView()
        self.status = ui_widgets.StatusPanel()

        self.cam_btn = QPushButton("Stop camera")
        self.cam_btn.clicked.connect(self._on_camera)
        self.arm_btn = QPushButton("Arm")
        self.arm_btn.setObjectName("Primary")
        self.arm_btn.clicked.connect(self._on_arm)
        self.top_chk = QCheckBox("Keep window on top")
        self.top_chk.setChecked(bool(values["always_on_top"]))
        self.top_chk.toggled.connect(self._on_top)

        self.settings = ui_widgets.SettingsPanel(values)
        self.settings.changed.connect(self._on_setting)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(360)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 16, 16, 12)
        side.setSpacing(10)
        side.addWidget(self._section("STATUS"))
        side.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cam_btn)
        buttons.addWidget(self.arm_btn)
        side.addLayout(buttons)
        side.addWidget(self.top_chk)
        side.addWidget(self._section("SETTINGS"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.settings)
        side.addWidget(scroll, 1)
        hint = QLabel("Still open palm 0.5 s arms or disarms  ·  two fingers + tilt for back / forward  ·  tap both fingers to refresh  ·  Esc quits")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        side.addWidget(hint)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.video, 1)
        row.addWidget(sidebar)
        self.setCentralWidget(central)

        self._apply_top(self.top_chk.isChecked())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_esc)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self._save_now)

        # The GUI thread only reads the newest snapshot; it never waits on the camera or model.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    def _section(self, text):
        lab = QLabel(text)
        lab.setObjectName("Section")
        return lab

    def _tick(self):
        s = self.pipe.latest()
        if s.cam_state == pipeline.CAM_ON and s.frame is not None:
            if s.frame_id != self._last_frame:
                self._last_frame = s.frame_id
                self.video.set_frame(s.frame)
        elif s.cam_state == pipeline.CAM_STARTING:
            self.video.set_message(s.cam_detail or "Starting camera...")
        elif s.cam_state == pipeline.CAM_ERROR:
            self.video.set_message("Camera unavailable\n%s" % s.cam_error)
        elif s.cam_state == pipeline.CAM_OFF:
            self.video.set_message("Camera off")
        self.status.update_from(s)

        if s.cam_state != self._last_cam_state:
            self._last_cam_state = s.cam_state
            running = s.cam_state in (pipeline.CAM_ON, pipeline.CAM_STARTING)
            self.cam_btn.setText("Stop camera" if running else "Start camera")
            self.cam_btn.setEnabled(s.cam_state != pipeline.CAM_STARTING)
        self.arm_btn.setText("Disarm" if s.armed else "Arm")

    def _on_esc(self):
        ctrl.log("[APP] Esc via shortcut")
        self.close()

    def keyPressEvent(self, event):
        # Backup in case the Esc shortcut does not match; logged so it shows if it ever fires.
        if event.key() == Qt.Key.Key_Escape:
            ctrl.log("[APP] Esc via key event backup")
            self.close()
            return
        super().keyPressEvent(event)

    def _on_camera(self):
        s = self.pipe.latest()
        self.pipe.request_camera(s.cam_state not in (pipeline.CAM_ON, pipeline.CAM_STARTING))

    def _on_arm(self):
        self.pipe.request_arm(not self.pipe.latest().armed)

    def _on_top(self, on):
        self._apply_top(on)
        self._on_setting("always_on_top", bool(on))

    def _apply_top(self, on):
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        if was_visible:
            self.show()

    def _on_setting(self, key, value):
        self.values[key] = value
        settings_store.apply(key, value)
        self.save_timer.start(400)

    def _save_now(self):
        try:
            settings_store.save(self.values)
        except Exception as e:
            ctrl.log("[APP] could not save settings: %s" % e)
            self.statusBar().showMessage("Could not save settings: %s" % e, 8000)

    def closeEvent(self, event):
        # Esc and the close button both end here: stop the UI, then the camera and threads.
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.timer.stop()
        if self.save_timer.isActive():
            self.save_timer.stop()
            self._save_now()
        self.hide()
        self.pipe.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Hand Gesture Control")
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass
    app.setStyleSheet(ui_widgets.THEME_QSS)

    if LOG_PATH:
        ctrl.log("[APP] log file %s" % LOG_PATH)
    # Title bar, Alt-Tab and taskbar use this; the exe file icon comes from the .spec.
    icon = QIcon(ICON_PATH) if os.path.isfile(ICON_PATH) else QIcon()
    app.setWindowIcon(icon)
    ctrl.log("[APP] window icon %s %s" % ("loaded from" if not icon.isNull() else "MISSING at", ICON_PATH))
    values, note = settings_store.load()
    for key, value in values.items():
        settings_store.apply(key, value)
    ctrl.log("[APP] settings: %s" % note)

    try:
        ctrl.require_supported()
    except ctrl.UnsupportedPlatform as e:
        ctrl.log("[APP] ERROR %s" % e)
        QMessageBox.critical(None, "Hand Gesture Control", str(e))
        return 2

    # Created after QApplication so Qt sets DPI awareness before pyautogui loads.
    pipe = pipeline.Pipeline()
    pipe.start()
    pipe.request_camera(True)
    app.aboutToQuit.connect(pipe.shutdown)

    win = MainWindow(pipe, values)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    # At high display scaling a fixed size can fill the screen, and the window stays on top.
    win.resize(min(1200, int(screen.width() * 0.7)), min(740, int(screen.height() * 0.7)))
    win.move(screen.center() - win.rect().center())
    win.show()
    code = app.exec()
    ctrl.log("[APP] exit")
    return code


if __name__ == "__main__":
    sys.exit(main())
