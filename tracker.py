# Camera reading and mediapipe hand detection, each on its own thread.

import platform
import threading
import time

import cv2
import numpy as np

import config
import gestures

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
    def __init__(self, frame, landmarks, detect_ms, score=None, brightness=None):
        self.frame = frame
        self.landmarks = landmarks  # numpy (21, 3) normalized, or None
        self.detect_ms = detect_ms
        self.score = score            # handedness score 0..1; mediapipe does not give the hand presence score
        self.brightness = brightness  # mean lightness 0..255 of the frame as captured


def camera_api():
    # DirectShow starts much faster than MSMF on Windows; AVFoundation is the macOS camera backend.
    name = platform.system()
    if name == "Windows":
        return cv2.CAP_DSHOW
    if name == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


# Held while this program has the camera open, so the app and main.py never open it at the same time.
# A second opener on Windows gets one black frame per second from the driver instead of an error.
CAMERA_MUTEX = "Local\\HandGestureCamera"
ERROR_ALREADY_EXISTS = 183


def _claim_camera():
    # Returns a handle to keep until the camera is released, or raises if another HandGesture window has it.
    if platform.system() != "Windows":
        return None
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k32.CreateMutexW(None, False, CAMERA_MUTEX)
    if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        raise RuntimeError("camera is already in use by another HandGesture window (the app or main.py), close that one first")
    return handle


def _release_camera(handle):
    if handle:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle(handle)


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
        self._claim = None
        self._stall_n = 0

    def open(self):
        self._claim = _claim_camera()
        self.cap = cv2.VideoCapture(config.CAM_INDEX, camera_api())
        if not self.cap.isOpened():
            _release_camera(self._claim)
            self._claim = None
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
                self._check_stall(image)
            with self._lock:
                self._count += 1
                self._frame = Frame(image, self._count, now)

    def _check_stall(self, image):
        # One warning when the frame rate stays under CAM_STALL_FPS for CAM_STALL_SECONDS, one line when it recovers.
        if self.grab_fps < config.CAM_STALL_FPS:
            self._stall_n += 1
            if self._stall_n == config.CAM_STALL_SECONDS:
                bright = float(cv2.cvtColor(image[::4, ::4], cv2.COLOR_BGR2GRAY).mean())
                # Tested: a capture opened while another program had the camera stays black at 1 frame/s even
                # after that program lets go, so only reopening helps.
                why = ("frames are black: another program probably has the camera (Camera app, Teams, a browser tab). "
                       "Close it, then turn the camera off and on in the app or restart main.py"
                       if bright < config.CAM_BLACK_LEVEL else "frames are not black: driver or USB problem, or a very long exposure")
                print("[CAM] WARNING camera stalled: %.1f frames/s for %d s (asked for %d), brightness %.0f; %s"
                      % (self.grab_fps, self._stall_n, config.CAM_FPS, bright, why), flush=True)
        else:
            if self._stall_n >= config.CAM_STALL_SECONDS:
                print("[CAM] camera recovered: %.1f frames/s" % self.grab_fps, flush=True)
            self._stall_n = 0

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
        _release_camera(self._claim)
        self._claim = None


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
            score = float(result.handedness[0][0].score) if result.handedness else None
            # Every 4th pixel is enough for an average.
            bright = float(cv2.cvtColor(frame.image[::4, ::4], cv2.COLOR_BGR2GRAY).mean())
            det = Detection(frame, result_to_array(result), detect_ms, score, bright)
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


def confidence_summary():
    # The three mediapipe thresholds, for the boot log.
    return ("detection %.2f (palm detector, only runs when no hand is tracked), presence %.2f (landmark model; "
            "below it the hand is dropped that frame), tracking %.2f (box overlap with the last frame; below it the "
            "palm detector runs again)" % (config.MIN_DETECT_CONF, config.MIN_PRESENCE_CONF, config.MIN_TRACK_CONF))


class TrackStats:
    # Tracking quality log: one [TRACK] line per dropout, plus a summary every TRACK_LOG_INTERVAL_S.
    # Logging only, nothing here changes detection or gestures.
    def __init__(self, log):
        self.log = log
        self._seen = None       # (t, landmarks, fingers, brightness) of the last frame with a hand
        self._before = None     # the hand frame before that, for speed
        self._gone_t = None     # when the hand went missing
        self._gone_n = 0
        self._new_window(None)

    def _new_window(self, t):
        self._w_t0 = t
        self._w_frames = 0
        self._w_hand = 0
        self._w_drops = []
        self._w_bright = []
        self._w_scores = []
        self._w_counts = []
        self._w_fingers = 0
        self._w_near = 0

    def add(self, det):
        t, lm = det.frame.captured_at, det.landmarks
        if self._w_t0 is None:
            self._w_t0 = t
        self._w_frames += 1
        if det.brightness is not None:
            self._w_bright.append(det.brightness)
        if lm is None:
            if self._seen is not None and self._gone_t is None:
                self._gone_t = t
            self._gone_n += 1
            self._w_counts.append(None)
        else:
            count = gestures.fingers_up(lm)
            if self._gone_t is not None:
                self._log_gap(t, lm, count, det.brightness)
            self._before, self._seen = self._seen, (t, lm, count, det.brightness)
            self._gone_t, self._gone_n = None, 0
            self._w_hand += 1
            self._w_counts.append(count)
            if det.score is not None:
                self._w_scores.append(det.score)
            size = max(_dist2(lm[9], lm[0]), 1e-6)
            for up, dt, dp in gestures.fingers_detail(lm):
                self._w_fingers += 1
                if abs(dt - dp) / size < config.TRACK_NEAR_LINE:
                    self._w_near += 1
        if t - self._w_t0 >= config.TRACK_LOG_INTERVAL_S:
            self._summary(t)

    def _log_gap(self, t, lm, count, bright):
        gone = t - self._seen[0]
        st, slm, scount, sbright = self._seen
        slm = np.asarray(slm)
        speed = "?"
        if self._before is not None and st > self._before[0]:
            speed = "%.2f/s" % (_dist2(slm[9], self._before[1][9]) / (st - self._before[0]))
        edge = min(slm[:, 0].min(), slm[:, 1].min(), 1.0 - slm[:, 0].max(), 1.0 - slm[:, 1].max())
        kind = "left the view" if gone > config.TRACK_DROPOUT_MAX_S else "DROPOUT"
        if kind == "DROPOUT":
            self._w_drops.append(gone)
        self.log("[TRACK] %s: hand gone %.0f ms (%d frames). Before: %d fingers, moving %s, closest edge %.3f, "
                 "brightness %s. After: %d fingers, brightness %s"
                 % (kind, gone * 1000.0, self._gone_n, scount, speed, edge, _fmt_b(sbright), count, _fmt_b(bright)))

    def _summary(self, t):
        counts = self._w_counts
        # Short blips: the count changes for 1-2 frames and comes back, not counting taps (to or from 0).
        runs = []
        for c in counts:
            if runs and runs[-1][0] == c:
                runs[-1][1] += 1
            else:
                runs.append([c, 1])
        blips = sum(1 for i in range(1, len(runs) - 1)
                    if runs[i][1] <= 2 and runs[i - 1][0] == runs[i + 1][0]
                    and None not in (runs[i][0], runs[i - 1][0]) and 0 not in (runs[i][0], runs[i - 1][0]))
        b, s = self._w_bright, self._w_scores
        self.log("[TRACK] last %.1f s: %d frames, hand in %d (%.0f%%), dropouts %d%s, brightness %s, "
                 "handedness score %s, finger-count blips %d, fingers near the up/down line %.1f%%"
                 % (t - self._w_t0, self._w_frames, self._w_hand, 100.0 * self._w_hand / max(self._w_frames, 1),
                    len(self._w_drops), " (longest %.0f ms)" % (max(self._w_drops) * 1000.0) if self._w_drops else "",
                    "mean %.0f (min %.0f, max %.0f)" % (sum(b) / len(b), min(b), max(b)) if b else "?",
                    "mean %.2f (min %.2f)" % (sum(s) / len(s), min(s)) if s else "?",
                    blips, 100.0 * self._w_near / max(self._w_fingers, 1)))
        self._new_window(t)


def _dist2(a, b):
    return float(np.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])))


def _fmt_b(b):
    return "?" if b is None else "%.0f" % b


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
