# Background thread for the UI: detection results -> gesture engine -> mouse, plus a snapshot for display.

import queue
import threading
import time

import cv2

import config
import controller as ctrl
import gestures
import tracker
from main import CursorGlide

CAM_OFF = "off"
CAM_STARTING = "starting"
CAM_ON = "on"
CAM_ERROR = "error"


class Snapshot:
    # Everything one repaint needs. Built on the pipeline thread, never changed after publishing.
    def __init__(self):
        self.frame = None
        self.frame_id = -1
        self.cam_state = CAM_OFF
        self.cam_error = ""
        self.cam_detail = ""
        self.cam_size = (0, 0)
        self.armed = False
        self.mode = gestures.MODE_IDLE
        self.switching = False
        self.fingers = 0
        self.hand_seen = False
        self.fps = 0.0
        self.detect_ms = 0.0
        self.latency_ms = 0.0


class Pipeline:
    # Owns the camera, tracker, engine and mouse. Only this thread touches them.
    def __init__(self):
        screen_w, screen_h = ctrl.get_screen_size()
        self.engine = gestures.GestureEngine(screen_w, screen_h)
        self.out = ctrl.make_controller()
        self.glide = CursorGlide()
        self.cam = None
        self.hands = None
        self._requests = queue.Queue()
        self._lock = threading.Lock()
        self._snap = Snapshot()
        self._stop = threading.Event()
        self._thread = None
        self._cam_state = CAM_OFF
        self._cam_error = ""
        self._fps = 0.0
        self._last_det_t = None
        self._opening = None
        self._opened = queue.Queue()
        self._close_when_opened = False
        self._cam_detail = ""
        self._published_detail = ""
        ctrl.log("[APP] screen %dx%d (pyautogui) vs %dx%d (OS)"
                 % ((screen_w, screen_h) + ctrl.screen_metrics()))
        ctrl.log("[APP] output %s: %s" % (self.out.describe(), ctrl.keys_summary()))
        ctrl.log("[APP] %s" % gestures.sensitivity_summary(screen_w, screen_h))

    # ---- called from the GUI thread ----

    def start(self):
        self._thread = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._thread.start()

    def request_camera(self, on):
        self._requests.put(("camera", bool(on)))

    def request_arm(self, on):
        self._requests.put(("arm", bool(on)))

    def latest(self):
        with self._lock:
            return self._snap

    def shutdown(self, timeout=12.0):
        # Stops the loop; the loop itself releases the camera and closes the model.
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                ctrl.log("[APP] WARNING pipeline thread did not stop within %.1fs" % timeout)
            self._thread = None

    # ---- pipeline thread only ----

    def _run(self):
        last_id = -1
        try:
            while not self._stop.is_set():
                self._handle_requests()
                self._adopt_opened()
                if self._cam_detail != self._published_detail:
                    self._publish(None, None, keep_frame=True)
                det = self.hands.latest() if self.hands is not None else None
                if det is not None and det.frame.frame_id != last_id:
                    last_id = det.frame.frame_id
                    self._process(det)
                self._glide_step()
                time.sleep(0.002)
        except Exception as e:
            ctrl.log("[APP] ERROR pipeline crashed: %s: %s" % (type(e).__name__, e))
        finally:
            self._close_when_opened = True
            if self._opening is not None:
                # A camera open is still running; wait for it so its handle can be released.
                self._opening.join(8.0)
                if self._opening.is_alive():
                    ctrl.log("[APP] WARNING camera open still stuck at exit")
                self._adopt_opened()
            self._stop_camera()
            ctrl.log("[APP] pipeline stopped")

    def _handle_requests(self):
        while True:
            try:
                kind, on = self._requests.get_nowait()
            except queue.Empty:
                return
            if kind == "camera":
                if on:
                    self._start_camera()
                else:
                    self._stop_camera()
            elif kind == "arm" and on != self.engine.armed:
                ev = self.engine.set_armed(on, time.perf_counter())
                ctrl.log("[APP] %s from the UI button" % ("ARMED" if ev.value else "DISARMED"))
                self._publish(None, None, keep_frame=True)

    def _start_camera(self):
        if self.cam is not None or self._opening is not None:
            return
        self._cam_state, self._cam_error = CAM_STARTING, ""
        self._close_when_opened = False
        self._publish(None, None)
        # Opening can take seconds, so it runs on its own thread and arm or stop requests keep working.
        self._opening = threading.Thread(target=self._open_worker, name="camera-open", daemon=True)
        self._opening.start()

    def _open_worker(self):
        cam = tracker.Camera()
        hands = tracker.HandTracker(cam)
        t0 = time.perf_counter()
        # The first model load in a process takes several seconds, so say which step is running.
        self._cam_detail = "Loading hand model..."
        try:
            # Model first, so a missing model or DLL is reported even when there is no camera.
            hands.start()
        except Exception as e:
            self._opened.put((None, None, "hand model failed: %s" % e))
            return
        ctrl.log("[APP] hand model loaded in %.2fs from %s" % (time.perf_counter() - t0, config.MODEL_PATH))
        self._cam_detail = "Opening camera..."
        t0 = time.perf_counter()
        try:
            cam.start()
        except Exception as e:
            hands.stop()
            cam.stop()
            self._opened.put((None, None, str(e)))
            return
        ctrl.log("[APP] camera opened in %.2fs" % (time.perf_counter() - t0))
        for line in cam.settings_report:
            ctrl.log("[APP] camera %s" % line)
        self._opened.put((cam, hands, ""))

    def _adopt_opened(self):
        try:
            cam, hands, err = self._opened.get_nowait()
        except queue.Empty:
            return
        self._opening = None
        self._cam_detail = ""
        if err:
            self._cam_state, self._cam_error = CAM_ERROR, err
            ctrl.log("[APP] camera error: %s" % err)
            self._publish(None, None)
            return
        if self._close_when_opened:
            hands.stop()
            cam.stop()
            return
        self.cam, self.hands = cam, hands
        self._cam_state = CAM_ON
        self._fps, self._last_det_t = 0.0, None
        ctrl.log("[APP] camera on %dx%d, negotiated %.0f fps"
                 % (cam.actual_size[0], cam.actual_size[1], cam.negotiated_fps))

    def _stop_camera(self):
        if self._opening is not None:
            # Still opening: close it as soon as it finishes.
            self._close_when_opened = True
        if self.hands is not None:
            self.hands.stop()
        if self.cam is not None:
            self.cam.stop()
            ctrl.log("[APP] camera off, released")
        self.cam, self.hands = None, None
        if self._cam_state != CAM_ERROR:
            self._cam_state = CAM_OFF
        # Tell the engine the hand is gone, so no tap or scroll anchor is left hanging.
        self.engine.update(None, time.perf_counter())
        self.glide.clear()
        self._publish(None, None)

    def _process(self, det):
        # Same per-frame dispatch as main.run_live, without the diagnostic prints.
        eng = self.engine
        events = eng.update(det.landmarks, det.frame.captured_at)
        for e in events:
            if e.kind == "arm":
                ctrl.log("[APP] %s by open palm" % ("ARMED" if e.value else "DISARMED"))
            elif e.kind == "mode":
                ctrl.log("[APP] mode %s" % e.value)
            if eng.armed:
                if e.kind == "move" and config.CURSOR_INTERP:
                    self.glide.set_target(e.value[0], e.value[1])
                else:
                    ctrl.dispatch(self.out, e)

        now = time.perf_counter()
        if self._last_det_t is not None:
            dt = now - self._last_det_t
            if dt > 0:
                inst = 1.0 / dt
                self._fps = inst if self._fps == 0.0 else 0.9 * self._fps + 0.1 * inst
        self._last_det_t = now
        self._publish(det, self._overlay(det))

    def _glide_step(self):
        if not config.CURSOR_INTERP:
            return
        if self.engine.armed and self.engine.mode == gestures.MODE_POINTER and self.cam is not None:
            p = self.glide.step(time.perf_counter())
            if p is not None:
                self.out.move(p[0], p[1])
        else:
            self.glide.clear()

    def _overlay(self, det):
        # Landmarks and the pointer's active region on a copy of the frame. Status goes in Qt, not here.
        img = det.frame.image.copy()
        h, w = img.shape[:2]
        x0, x1 = int(config.ACTIVE_X_MIN * w), int(config.ACTIVE_X_MAX * w)
        y0, y1 = int(config.ACTIVE_Y_MIN * h), int(config.ACTIVE_Y_MAX * h)
        cv2.rectangle(img, (x0, y0), (x1, y1), (150, 150, 150), 1)
        tracker.draw_landmarks(img, det.landmarks)
        return img

    def _publish(self, det, frame, keep_frame=False):
        s = Snapshot()
        with self._lock:
            prev = self._snap
        if keep_frame:
            s.frame, s.frame_id = prev.frame, prev.frame_id
        elif det is not None:
            s.frame, s.frame_id = frame, det.frame.frame_id
        s.cam_state = self._cam_state
        s.cam_error = self._cam_error
        s.cam_detail = self._cam_detail
        self._published_detail = s.cam_detail
        s.cam_size = self.cam.actual_size if self.cam is not None else (0, 0)
        eng = self.engine
        s.armed = eng.armed
        s.mode = eng.mode
        s.switching = eng.switching
        s.fingers = eng.finger_count
        s.hand_seen = det is not None and det.landmarks is not None
        if keep_frame:
            s.hand_seen = prev.hand_seen
        s.fps = self._fps
        s.detect_ms = self.hands.detect_ms_avg if self.hands is not None else 0.0
        if det is not None:
            s.latency_ms = (time.perf_counter() - det.frame.captured_at) * 1000.0
        with self._lock:
            self._snap = s
