# UI-tunable settings: one table of ranges, applied to config live and saved per user (see app_dir).

import json
import os
import sys

import config

APP_DIR_NAME = "HandGesture"


class Spec:
    # One slider setting: its range, grid step and how to show it.
    def __init__(self, key, group, label, lo, hi, step, fmt, tip, as_int=False):
        self.key = key
        self.group = group
        self.label = label
        self.lo = lo
        self.hi = hi
        self.step = step
        self.fmt = fmt
        self.tip = tip
        self.as_int = as_int

    def clamp(self, v):
        v = min(max(float(v), self.lo), self.hi)
        return int(round(v)) if self.as_int else round(v, 4)


def _pct(v):
    return "%.0f %%" % (v * 100.0)


def _pct1(v):
    return "%.1f %%" % (v * 100.0)


SLIDERS = [
    Spec("active_region", "Pointer", "Active region size", 0.30, 0.90, 0.05, _pct,
         "Middle part of the camera frame that maps to the whole screen."),
    Spec("CURSOR_SENSITIVITY", "Pointer", "Cursor sensitivity", 1.0, 3.0, 0.1,
         lambda v: "%.1f×" % v,
         "Cursor gain around the screen centre. Higher needs less hand movement, but also magnifies hand shake."),
    Spec("CLICK_TAP_WINDOW_MS", "Click", "Tap window", 200, 1200, 50,
         lambda v: "%d ms" % v,
         "Index must bend down and come back up inside this. Also delays a real POINTER to IDLE switch.",
         as_int=True),
    Spec("CLICK_COOLDOWN_S", "Click", "Click cooldown", 0.0, 1.0, 0.05,
         lambda v: "%d ms" % round(v * 1000),
         "Minimum time between two clicks."),
    Spec("SCROLL_DEAD_ZONE", "Scroll", "Dead zone", 0.0, 0.10, 0.005, _pct1,
         "Hand movement from the start point that gives no scroll."),
    Spec("SCROLL_SENSITIVITY", "Scroll", "Sensitivity", 1.0, 15.0, 0.05,
         lambda v: "%.2f" % v,
         "How fast scroll speed ramps up past the dead zone."),
    Spec("SCROLL_CURVE_EXP", "Scroll", "Curve shape", 1.0, 4.0, 0.1,
         lambda v: "%.1f" % v,
         "1.0 is linear. Higher is gentler near the start point."),
    Spec("TILT_ANGLE_THRESHOLD", "Tilt", "Tilt angle", 10.0, 40.0, 1.0,
         lambda v: "%.0f°" % v,
         "How far the two-finger hand must rotate from its starting angle to go back or forward."),
    Spec("TILT_COOLDOWN_MS", "Tilt", "Tilt cooldown", 200, 3000, 100,
         lambda v: "%d ms" % v,
         "Minimum time between two tilts.", as_int=True),
]

SPEC_BY_KEY = dict((s.key, s) for s in SLIDERS)
# Settings of removed gestures, dropped quietly instead of reported as bad entries.
RETIRED_KEYS = ("SWIPE_MIN_DIST", "SWIPE_MIN_SPEED", "SWIPE_COOLDOWN_S",
                "PALM_SWIPE_MIN_DISTANCE", "PALM_SWIPE_MIN_SPEED", "PALM_SWIPE_COOLDOWN_S")
# swipe_swap keeps its old saved name; it now swaps what tilt left and right do.
BOOL_KEYS = ("swipe_swap", "always_on_top", "low_light")


def current():
    # Reads today's values out of config, so config.py stays the source of defaults.
    vals = {}
    for s in SLIDERS:
        if s.key == "active_region":
            vals[s.key] = round(config.ACTIVE_X_MAX - config.ACTIVE_X_MIN, 4)
        else:
            vals[s.key] = getattr(config, s.key)
    vals["swipe_swap"] = (config.TILT_LEFT_ACTION == "forward")
    vals["always_on_top"] = bool(config.WINDOW_TOPMOST)
    vals["low_light"] = bool(config.LOW_LIGHT_MODE)
    return vals


def apply(key, value):
    # Writes one setting into config. The engine reads config every frame, so this is live.
    if key == "active_region":
        margin = round((1.0 - value) / 2.0, 4)
        config.ACTIVE_X_MIN = margin
        config.ACTIVE_Y_MIN = margin
        config.ACTIVE_X_MAX = round(1.0 - margin, 4)
        config.ACTIVE_Y_MAX = round(1.0 - margin, 4)
    elif key == "swipe_swap":
        config.TILT_LEFT_ACTION = "forward" if value else "back"
        config.TILT_RIGHT_ACTION = "back" if value else "forward"
    elif key == "always_on_top":
        config.WINDOW_TOPMOST = bool(value)
    elif key == "low_light":
        # The detection thread reads this every frame, so it takes effect on the next frame.
        config.LOW_LIGHT_MODE = bool(value)
    else:
        setattr(config, key, value)


def app_dir():
    # Windows: %LOCALAPPDATA%\HandGesture. macOS: ~/Library/Application Support/HandGesture.
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), APP_DIR_NAME)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def settings_path():
    return os.path.join(app_dir(), "settings.json")


def load():
    # Returns (values, note). Bad or out of range entries fall back to config.py values.
    vals = current()
    path = settings_path()
    if not os.path.exists(path):
        return vals, "no saved settings, using config.py values"
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("top level is not an object")
    except Exception as e:
        return vals, "could not read %s (%s), using config.py values" % (path, e)
    skipped = []
    retired = sorted(k for k in RETIRED_KEYS if k in raw)
    for k in retired:
        raw.pop(k)
    for key, v in raw.items():
        if key in SPEC_BY_KEY and isinstance(v, (int, float)) and not isinstance(v, bool):
            vals[key] = SPEC_BY_KEY[key].clamp(v)
        elif key in BOOL_KEYS and isinstance(v, bool):
            vals[key] = v
        else:
            skipped.append(key)
    note = "loaded %s" % path
    if retired:
        note += ", dropped settings of removed gestures: %s" % ", ".join(retired)
    if skipped:
        note += ", ignored bad entries: %s" % ", ".join(sorted(skipped))
    return vals, note


def save(vals):
    # Writes to a temp file first so a crash mid-write cannot leave a broken file.
    os.makedirs(app_dir(), exist_ok=True)
    path = settings_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vals, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
