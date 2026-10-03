# macOS output backend: Quartz events for move, scroll and shortcuts, pyautogui for click.
# Only imported on macOS, controller.make_controller() picks it. Written on Windows and never run on a Mac.
# Every Quartz event needs the app (or the Terminal running it) allowed under
# System Settings > Privacy & Security > Accessibility, or macOS drops the events without an error.

import Quartz

import config
from controller import BaseController, dlog

KEYS_SUMMARY = ("back/forward = Cmd+[ / Cmd+] (keycodes 0x%02X / 0x%02X), refresh = Cmd+R (keycode 0x%02X), "
                "scroll = Quartz pixel wheel, %d px per wheel notch"
                % (config.MAC_BACK_KEYCODE, config.MAC_FORWARD_KEYCODE, config.MAC_REFRESH_KEYCODE,
                   config.MAC_PIXELS_PER_WHEEL_NOTCH))


def cursor_pos():
    # Cursor in global display points, top-left origin, same space the mouse events use.
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (int(loc.x), int(loc.y))


def screen_metrics():
    # Main display size as Quartz reports it, to compare against pyautogui.
    d = Quartz.CGMainDisplayID()
    return (int(Quartz.CGDisplayPixelsWide(d)), int(Quartz.CGDisplayPixelsHigh(d)))


def input_allowed():
    # True/False if macOS says whether this process may post input events, None on macOS older than 10.15.
    check = getattr(Quartz, "CGPreflightPostEventAccess", None)
    return None if check is None else bool(check())


def _post_shortcut(keycode, label):
    # Key down and up with Command set on both events, so no separate Command key press is needed.
    dlog("[ACT] Quartz key %s (keycode 0x%02X + Command)" % (label, keycode))
    try:
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
            Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
    except Exception as e:
        dlog("[ACT] key RAISED %s: %s" % (type(e).__name__, e))
        return
    dlog("[ACT] key posted")


class MacController(BaseController):
    # Drives the real mouse through Quartz.
    def __init__(self):
        import pyautogui
        self.gui = pyautogui
        self.gui.FAILSAFE = config.PYAUTOGUI_FAILSAFE
        self.gui.PAUSE = 0.0
        # Wheel units carry over between frames so small amounts still add up to whole pixels.
        self._carry = {"v": 0.0, "h": 0.0}
        allowed = input_allowed()
        if allowed is False:
            dlog("[ACT] WARNING macOS reports this process may NOT post input events: gestures will do nothing. "
                 "Allow it in System Settings > Privacy & Security > Accessibility, then restart the app.")
        else:
            dlog("[ACT] macOS input permission check: %s" % ("allowed" if allowed else "not available before macOS 10.15"))

    def describe(self):
        return "MacController (REAL MOUSE, Quartz)"

    def move(self, x, y):
        # pyautogui.moveTo sleeps 10 ms after every move on macOS, too slow for the 120 Hz glide, so post directly.
        try:
            ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, (int(x), int(y)), Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        except Exception as e:
            dlog("[ACT] move RAISED %s: %s" % (type(e).__name__, e))
            return
        if config.ACTION_LOG_MOVES:
            dlog("[ACT] Quartz mouse moved to (%d, %d), cursor now %s" % (int(x), int(y), cursor_pos()))

    def click(self):
        # pyautogui's macOS click is Quartz left down + up at the cursor.
        dlog("[ACT] pyautogui.click() at cursor %s" % (cursor_pos(),))
        try:
            self.gui.click()
        except Exception as e:
            dlog("[ACT] click RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] click ok")

    def _wheel(self, axis, units):
        # Windows wheel units (120 = one notch) to whole pixels, keeping the leftover for next time.
        self._carry[axis] += units * config.MAC_PIXELS_PER_WHEEL_NOTCH / 120.0
        px = int(self._carry[axis])
        self._carry[axis] -= px
        if px == 0:
            return
        v = px * config.MAC_VSCROLL_SIGN if axis == "v" else 0
        h = px * config.MAC_HSCROLL_SIGN if axis == "h" else 0
        dlog("[ACT] Quartz scroll pixels vertical=%+d horizontal=%+d (from %+d wheel units) at cursor %s"
             % (v, h, units, cursor_pos()))
        try:
            ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 2, v, h)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        except Exception as e:
            dlog("[ACT] scroll RAISED %s: %s" % (type(e).__name__, e))

    def scroll(self, steps):
        # pyautogui.scroll is not used: on macOS it counts lines, on Windows wheel units, so speeds would differ ~20x.
        self._wheel("v", int(steps))

    def hscroll(self, steps):
        self._wheel("h", int(steps))

    def nav(self, action):
        # macOS has no back / forward mouse button that works in every app; Cmd+[ and Cmd+] do in browsers and Finder.
        if action == "back":
            _post_shortcut(config.MAC_BACK_KEYCODE, "Cmd+[ (back)")
        elif action == "forward":
            _post_shortcut(config.MAC_FORWARD_KEYCODE, "Cmd+] (forward)")
        else:
            dlog("[ACT] nav unknown action %r, nothing sent" % action)

    def key(self, action):
        if action == "refresh":
            _post_shortcut(config.MAC_REFRESH_KEYCODE, "Cmd+R (refresh)")
        else:
            dlog("[ACT] key unknown action %r, nothing sent" % action)

    def release(self):
        # Nothing is held down, but make sure no button is stuck.
        try:
            self.gui.mouseUp()
        except Exception as e:
            dlog("[ACT] release mouseUp RAISED %s: %s" % (type(e).__name__, e))


Controller = MacController
