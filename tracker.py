# Camera reading and mediapipe hand detection, each on its own thread.

import platform
import threading
import time

import cv2
import numpy as np

import config

# mediapipe 1.x removed mp.solutions, so this uses the Tasks HandLandmarker.
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision


# Bone list for drawing, since Tasks has no drawing_utils.
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


class Frame:
    # One captured frame plus when it was grabbed.
    def __init__(self, image, frame_id, captured_at):
        self.image = image
        self.frame_id = frame_id
        self.captured_at = captured_at


class Detection:
    # One detection result tied back to its source frame.
    def __init__(self, frame, landmarks, detect_ms):
        self.frame = frame
        self.landmarks = landmarks  # numpy (21, 3) normalized, or None
        self.detect_ms = detect_ms


def camera_api():
    # DirectShow starts much faster than MSMF on Windows; AVFoundation is the macOS camera backend.
    name = platform.system()
    if name == "Windows":
        return cv2.CAP_DSHOW
    if name == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


class Camera:
    # Reads the camera in a thread and keeps only the newest frame.
    def __init__(self):
        self.cap = None
        self._lock = threading.Lock()
        self._frame = None
        self._count = 0
        self._stop = threading.Event()
        self._thread = None
        self.negotiated_fps = 0.0
        self.actual_size = (0, 0)
        self._rate_t0 = None
        self._rate_n = 0
        self.grab_fps = 0.0
        self.settings_report = []
        self._manual_exposure = False
        self._restore = []

    def open(self):
        self.cap = cv2.VideoCapture(config.CAM_INDEX, camera_api())
        if not self.cap.isOpened():
            raise RuntimeError("Could not open camera %d" % config.CAM_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAM_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAM_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, config.CAM_FPS)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.settings_report = [self._set_prop("exposure", cv2.CAP_PROP_EXPOSURE, config.CAMERA_EXPOSURE),
                                self._set_prop("gain", cv2.CAP_PROP_GAIN, config.CAMERA_GAIN)]
        self.negotiated_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.actual_size = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                            int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))

    def _set_prop(self, name, prop, value):
        # "auto" never touches the camera. A number is sent as-is and the report says what came back.
        before = self.cap.get(prop)
        if value == "auto":
            return "%s=auto, not touched (camera reports %s)" % (name, before)
        ok = self.cap.set(prop, float(value))
        if ok and prop == cv2.CAP_PROP_EXPOSURE:
            self._manual_exposure = True
        elif ok:
            self._restore.append((prop, before))
        return "%s requested %s: set() returned %s, camera now reports %s (was %s)" % (name, value, ok, self.cap.get(prop), before)

    def start(self):
        self.open()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            ok, image = self.cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            if config.MIRROR:
                image = cv2.flip(image, 1)
            now = time.perf_counter()
            # Rolling measure of how fast frames really arrive.
            if self._rate_t0 is None:
                self._rate_t0 = now
            self._rate_n += 1
            span = now - self._rate_t0
            if span >= 1.0:
                self.grab_fps = self._rate_n / span
                self._rate_t0 = now
                self._rate_n = 0
            with self._lock:
                self._count += 1
                self._frame = Frame(image, self._count, now)

    def latest(self):
        # Newest frame only, old ones are dropped.
        with self._lock:
            return self._frame

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self.cap is not None:
            # The driver keeps these after the app exits, so put back what was there.
            if self._manual_exposure:
                self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
            for prop, value in self._restore:
                self.cap.set(prop, value)
            self.cap.release()


_clahe = {}


def low_light_rgb(image):
    # CLAHE on the lightness channel only, so skin colour survives. Returns a new RGB array for detection.
    key = (float(config.LOW_LIGHT_CLIP_LIMIT), int(config.LOW_LIGHT_TILE_GRID))
    clahe = _clahe.get(key)
    if clahe is None:
        clahe = _clahe[key] = cv2.createCLAHE(clipLimit=key[0], tileGridSize=(key[1], key[1]))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def make_landmarker():
    # Builds a VIDEO-mode HandLandmarker.
    opts = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=config.MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=config.NUM_HANDS,
        min_hand_detection_confidence=config.MIN_DETECT_CONF,
        min_hand_presence_confidence=config.MIN_PRESENCE_CONF,
        min_tracking_confidence=config.MIN_TRACK_CONF,
    )
    return vision.HandLandmarker.create_from_options(opts)


def result_to_array(result):
    # Turns a HandLandmarkerResult into a (21, 3) array, or None.
    if not result.hand_landmarks:
        return None
    hand = result.hand_landmarks[0]
    return np.array([[p.x, p.y, p.z] for p in hand], dtype=np.float32)


class HandTracker:
    # Pulls the newest frame and runs detection on its own thread.
    def __init__(self, camera):
        self.camera = camera
        self._lock = threading.Lock()
        self._detection = None
        self._stop = threading.Event()
        self._thread = None
        self._landmarker = None
        self.detect_ms_avg = 0.0
        self.low_light_ms_avg = 0.0

    def start(self):
        self._landmarker = make_landmarker()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        last_id = -1
        base = time.perf_counter()
        while not self._stop.is_set():
            frame = self.camera.latest()
            if frame is None or frame.frame_id == last_id:
                time.sleep(0.002)
                continue
            last_id = frame.frame_id
            if config.LOW_LIGHT_MODE:
                # Detection sees a boosted copy; frame.image, which the preview draws, is left as captured.
                t_e = time.perf_counter()
                rgb = low_light_rgb(frame.image)
                ms = (time.perf_counter() - t_e) * 1000.0
                self.low_light_ms_avg = ms if self.low_light_ms_avg == 0.0 else 0.9 * self.low_light_ms_avg + 0.1 * ms
            else:
                rgb = cv2.cvtColor(frame.image, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts_ms = int((frame.captured_at - base) * 1000.0)
            t0 = time.perf_counter()
            try:
                result = self._landmarker.detect_for_video(image, ts_ms)
            except Exception:
                continue
            detect_ms = (time.perf_counter() - t0) * 1000.0
            if self.detect_ms_avg == 0.0:
                self.detect_ms_avg = detect_ms
            else:
                self.detect_ms_avg = 0.9 * self.detect_ms_avg + 0.1 * detect_ms
            det = Detection(frame, result_to_array(result), detect_ms)
            with self._lock:
                self._detection = det

    def latest(self):
        with self._lock:
            return self._detection

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._landmarker is not None:
            self._landmarker.close()


def draw_landmarks(image, landmarks):
    # Draws bones and joints on the preview.
    if landmarks is None:
        return
    h, w = image.shape[:2]
    pts = [(int(p[0] * w), int(p[1] * h)) for p in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(image, pts[a], pts[b], (0, 200, 0), 2)
    for i, p in enumerate(pts):
        color = (0, 0, 255) if i in (4, 5, 8, 9) else (255, 200, 0)
        cv2.circle(image, p, 4, color, -1)
