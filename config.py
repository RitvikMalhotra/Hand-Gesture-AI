# All thresholds and settings live here.

# --- camera ---
CAM_INDEX = 0
CAM_WIDTH = 640
CAM_HEIGHT = 480
CAM_FPS = 60   # a request only, the camera negotiates down and the app prints what it got
MIRROR = True  # flips left-right only, it never changes the y axis

# --- camera exposure / gain: "auto" leaves the camera exactly as it is ---
# Not standard across webcams or OS camera backends, the log prints what the camera did with the value.
# Numbers were only ever tried with Windows DirectShow; on macOS leave these on "auto".
# This machine's camera (DirectShow): exposure clamps to -10..-4 but no value changed brightness,
# and setting one switches its auto exposure off until the app restores it on close. Gain is refused.
CAMERA_EXPOSURE = "auto"    # or a number sent to CAP_PROP_EXPOSURE as-is
CAMERA_GAIN = "auto"        # or a number sent to CAP_PROP_GAIN as-is

# --- low light: contrast boost on a copy of each frame before detection, the preview stays as captured ---
# Works on the lightness channel only: plain grayscale stopped the detector finding a hand at all in testing.
# On the one real hand photo tested, the boost lowered detection (clip 2/grid 8: 12 of 216 frames vs 108 without);
# these gentler values hurt least (68 of 216). Check it in your own room before relying on it.
LOW_LIGHT_MODE = False      # dim rooms only, it cannot add detail the camera did not capture
LOW_LIGHT_CLIP_LIMIT = 1.0  # CLAHE contrast limit, higher boosts more and brings up more noise
LOW_LIGHT_TILE_GRID = 4     # CLAHE tiles per side

# --- model ---
MODEL_PATH = "models/hand_landmarker.task"
NUM_HANDS = 1
MIN_DETECT_CONF = 0.5
MIN_PRESENCE_CONF = 0.5
MIN_TRACK_CONF = 0.5

# --- one euro filter (cursor smoothing) ---
OE_MIN_CUTOFF = 1.0   # lower = heavier smoothing when the hand is slow
OE_BETA = 0.7         # higher = lighter smoothing when the hand is fast
OE_D_CUTOFF = 1.0

# --- cursor glide between detections ---
CURSOR_INTERP = True          # nudge the cursor between detections so it looks smooth
CURSOR_INTERP_HZ = 120        # how often the cursor is nudged, detection stays at camera rate
CURSOR_INTERP_RESPONSE = 22.0 # higher catches up faster, lower is smoother but laggier

# --- arm / disarm ---
OPEN_PALM_FINGERS = 4       # fingers up that count as an open palm
OPEN_PALM_HOLD_S = 0.5      # hold this long to toggle control
ARM_HOLD_MAX_DRIFT = 0.04   # palm centre may move this far (frame units) during the hold, more restarts it

# --- mode switching ---
MODE_HOLD_FRAMES = 5        # new finger count must hold this many frames
# POINTER -> IDLE first waits out CLICK_TAP_WINDOW_MS, so it is that much slower
# PAGE -> IDLE first waits out PAGE_TAP_WINDOW_MS the same way

# --- active camera region mapped to the full screen ---
ACTIVE_X_MIN = 0.1
ACTIVE_X_MAX = 0.9
ACTIVE_Y_MIN = 0.1
ACTIVE_Y_MAX = 0.9

# --- pointer sensitivity (applied after the active region mapping) ---
CURSOR_SENSITIVITY = 2.5    # cursor gain around the screen centre, 1.0 = no gain, also multiplies hand jitter

# --- pointer mode click (index finger tap) ---
CLICK_TAP_WINDOW_MS = 600   # index bends down and comes back up inside this
# this window also delays a real POINTER -> IDLE switch by up to the same amount
CLICK_COOLDOWN_S = 0.3

# --- page mode refresh (index + middle tap), same rules as the click tap, own values ---
PAGE_TAP_WINDOW_MS = 600    # both fingers bend down together and come back up inside this
# this window also delays a real PAGE -> IDLE switch by up to the same amount
PAGE_TAP_COOLDOWN_S = 0.3
PAGE_TAP_EDGE_FRAMES = 2    # a 1-finger reading this many frames long is let through going down or coming back up
# the key it sends differs per OS: WIN_REFRESH_KEY / MAC_REFRESH_KEYCODE below

# --- page mode scroll (full camera frame units, independent of ACTIVE_*) ---
SCROLL_DEAD_ZONE = 0.01     # hand movement from the anchor that gives no scroll
SCROLL_SENSITIVITY = 4.55   # speed ramp past the dead zone, same ramp as before, higher reaches top speed sooner
SCROLL_CURVE_EXP = 2.2      # curve shape, 1.0 is linear, higher is gentler near the anchor
SCROLL_MAX_STEP = 6         # scroll clicks per frame at top speed
SCROLL_INVERT = False
# DIR_LOCK_MIN_MOVE below is the real floor, nothing scrolls until the axis locks

# --- page mode horizontal scroll, same curve as vertical, own values ---
SCROLL_H_DEAD_ZONE = 0.01   # sideways hand movement from the anchor that gives no scroll
SCROLL_H_SENSITIVITY = 4.55 # speed ramp past the dead zone
SCROLL_H_CURVE_EXP = 2.2    # curve shape, 1.0 is linear
SCROLL_H_MAX_STEP = 6       # wheel units per frame at top speed, same units as vertical
SCROLL_H_INVERT = False     # False: page content follows the hand, like vertical does

# --- page mode tilt (back / forward): wrist-to-fingertips angle against the pose-start angle ---
TILT_ANGLE_THRESHOLD = 18.0     # degrees of tilt that fire
TILT_WATCH_THRESHOLD = 8.0      # degrees of tilt that pause both scrolls, tilt wins over movement
TILT_HOLD_FRAMES = 3            # frames past TILT_ANGLE_THRESHOLD before it fires
TILT_COOLDOWN_MS = 1000
TILT_HSCROLL_DELTA = 120        # sideways wheel sent with each tilt, 120 = one wheel notch, direction follows SCROLL_H_INVERT
TILT_EASE_DURATION_MS = 180     # that notch is spread over this long on an ease-out curve
TILT_EASE_STEPS = 8             # wheel events it is split into, the biggest goes out at fire time
TILT_LEFT_ACTION = "back"       # tilt left (counter-clockwise on the mirrored preview) does this
TILT_RIGHT_ACTION = "forward"   # tilt right (clockwise) does this

# --- direction lock ---
DIR_LOCK_MIN_MOVE = 0.03    # movement from start before an axis is locked

# --- landmark used as the hand reference point in page mode ---
HAND_POINT = 9              # middle finger knuckle

# --- mouse / keyboard output ---
PYAUTOGUI_FAILSAFE = False  # corner-of-screen abort, off so gestures cannot crash it
ACTION_LOG_MOVES = False    # per-move logging, off because the glide runs at CURSOR_INTERP_HZ

# Windows: what back, forward and the refresh tap send
WIN_NAV_BUTTON_BACK = 1     # XBUTTON1, mouse button 4
WIN_NAV_BUTTON_FORWARD = 2  # XBUTTON2, mouse button 5
WIN_REFRESH_KEY = "f5"      # pyautogui key name, pressed once, goes to the focused window

# macOS: no mouse button 4/5 back/forward that works in every app, so Command shortcuts instead
# Key codes are key positions on a US keyboard (kVK_ANSI_*); on other layouts the same key can type another character.
MAC_BACK_KEYCODE = 0x21     # kVK_ANSI_LeftBracket, sent with Command: Cmd+[ = back
MAC_FORWARD_KEYCODE = 0x1E  # kVK_ANSI_RightBracket, Cmd+] = forward
MAC_REFRESH_KEYCODE = 0x0F  # kVK_ANSI_R, Cmd+R = reload
# Scroll amounts are Windows wheel units (120 = one notch); macOS gets pixels. Not tuned on a real Mac.
MAC_PIXELS_PER_WHEEL_NOTCH = 100  # about what one notch scrolls in Chrome on Windows
MAC_VSCROLL_SIGN = 1        # flip to -1 if vertical scroll runs the wrong way on a Mac
MAC_HSCROLL_SIGN = -1       # Windows sideways wheel is positive to the right; assumed opposite on macOS, flip if wrong

# --- measurement windows (used by --log) ---
FALSE_TRIGGER_WINDOW_S = 0.4    # action this soon after a mode change = false trigger
MODE_FLAP_WINDOW_S = 1.0        # mode A->B->A this fast = one wrong mode switch
TILT_SETTLE_WINDOW_S = 0.5      # tilt fire this soon after entering page mode = accidental
TILT_REVERSAL_WINDOW_S = 1.5    # opposite tilt this soon = both accidental
LOG_INTERVAL_S = 10.0           # live --log prints a line this often

# --- preview window ---
WINDOW_NAME = "Hand Gesture Control"
WINDOW_TOPMOST = True
