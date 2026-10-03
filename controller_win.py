# Windows output backend: SendInput for back / forward and sideways wheel, pyautogui for the rest.
# Only imported on Windows, controller.make_controller() picks it.

import ctypes
from ctypes import wintypes

import config
from controller import BaseController, dlog

KEYS_SUMMARY = "back/forward = mouse XBUTTON%d / XBUTTON%d, refresh = %s, sideways wheel = SendInput HWHEEL" % (
    config.WIN_NAV_BUTTON_BACK, config.WIN_NAV_BUTTON_FORWARD, config.WIN_REFRESH_KEY)

# Windows SendInput bits for the back / forward mouse buttons.
INPUT_MOUSE = 0
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
# Native sideways wheel. pyautogui.hscroll on Windows sends a vertical wheel instead.
MOUSEEVENTF_HWHEEL = 0x1000

ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


def cursor_pos():
    # Real cursor position straight from Windows.
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def screen_metrics():
    # Screen size as Windows reports it, to compare against pyautogui.
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


def _send_x_button(button):
    # Presses and releases mouse button 4 or 5.
    user32 = ctypes.windll.user32
    name = "XBUTTON%d" % button
    for phase, flag in (("XDOWN", MOUSEEVENTF_XDOWN), ("XUP", MOUSEEVENTF_XUP)):
        mi = MOUSEINPUT(0, 0, button, flag, 0, 0)
        inp = INPUT(INPUT_MOUSE, _InputUnion(mi))
        dlog("[ACT] ctypes.SendInput(nInputs=1, type=MOUSE, mouseData=%d(%s), dwFlags=0x%04X(%s), cbSize=%d)"
             % (button, name, flag, phase, ctypes.sizeof(INPUT)))
        try:
            sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        except Exception as e:
            dlog("[ACT] SendInput RAISED %s: %s" % (type(e).__name__, e))
            return
        if sent != 1:
            err = ctypes.GetLastError()
            dlog("[ACT] SendInput FAILED returned=%d expected=1 GetLastError=%d %s"
                 % (sent, err, ctypes.FormatError(err)))
        else:
            dlog("[ACT] SendInput ok returned=1 (%s %s)" % (name, phase))


def _send_hwheel(delta):
    # Sideways wheel at the cursor, positive scrolls right. Same raw units pyautogui.scroll sends.
    user32 = ctypes.windll.user32
    mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, MOUSEEVENTF_HWHEEL, 0, 0)
    inp = INPUT(INPUT_MOUSE, _InputUnion(mi))
    dlog("[ACT] ctypes.SendInput(type=MOUSE, dwFlags=0x%04X(HWHEEL), mouseData=%+d %s) at cursor %s"
         % (MOUSEEVENTF_HWHEEL, delta, "RIGHT" if delta > 0 else "LEFT", cursor_pos()))
    try:
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    except Exception as e:
        dlog("[ACT] SendInput RAISED %s: %s" % (type(e).__name__, e))
        return
    if sent != 1:
        err = ctypes.GetLastError()
        dlog("[ACT] SendInput FAILED returned=%d expected=1 GetLastError=%d %s"
             % (sent, err, ctypes.FormatError(err)))
    else:
        dlog("[ACT] hscroll ok")


class WindowsController(BaseController):
    # Drives the real mouse.
    def __init__(self):
        import pyautogui
        self.gui = pyautogui
        self.gui.FAILSAFE = config.PYAUTOGUI_FAILSAFE
        self.gui.PAUSE = 0.0

    def describe(self):
        return "WindowsController (REAL MOUSE)"

    def move(self, x, y):
        ix, iy = int(x), int(y)
        verbose = config.ACTION_LOG_MOVES
        if verbose:
            bx, by = cursor_pos()
            dlog("[ACT] BEFORE pyautogui.moveTo(x=%d, y=%d)  cursor was (%d, %d)" % (ix, iy, bx, by))
            if ix <= 0 and iy <= 0:
                dlog("[ACT] WARNING target is the (0,0) failsafe corner, FAILSAFE=%s" % self.gui.FAILSAFE)
        try:
            self.gui.moveTo(ix, iy)
        except self.gui.FailSafeException as e:
            dlog("[ACT] AFTER  moveTo RAISED FailSafeException: %s" % e)
            return
        except Exception as e:
            dlog("[ACT] AFTER  moveTo RAISED %s: %s" % (type(e).__name__, e))
            return
        if verbose:
            ax, ay = cursor_pos()
            ok = "MATCH" if abs(ax - ix) <= 2 and abs(ay - iy) <= 2 else "MISMATCH"
            moved = "MOVED" if (ax, ay) != (bx, by) else "DID NOT MOVE"
            dlog("[ACT] AFTER  moveTo returned, cursor now (%d, %d)  %s  %s" % (ax, ay, ok, moved))

    def click(self):
        dlog("[ACT] pyautogui.click() at cursor %s" % (cursor_pos(),))
        try:
            self.gui.click()
        except self.gui.FailSafeException as e:
            dlog("[ACT] click RAISED FailSafeException: %s" % e)
            return
        except Exception as e:
            dlog("[ACT] click RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] click ok")

    def scroll(self, steps):
        s = int(steps)
        dlog("[ACT] pyautogui.scroll(clicks=%d) at cursor %s" % (s, cursor_pos()))
        try:
            self.gui.scroll(s)
        except self.gui.FailSafeException as e:
            dlog("[ACT] scroll RAISED FailSafeException: %s" % e)
            return
        except Exception as e:
            dlog("[ACT] scroll RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] scroll ok")

    def hscroll(self, steps):
        _send_hwheel(int(steps))

    def nav(self, action):
        dlog("[ACT] nav(%r)" % action)
        if action == "back":
            _send_x_button(config.WIN_NAV_BUTTON_BACK)
        elif action == "forward":
            _send_x_button(config.WIN_NAV_BUTTON_FORWARD)
        else:
            dlog("[ACT] nav unknown action %r, nothing sent" % action)

    def key(self, action):
        # One press and release, never held. Goes to whichever window has keyboard focus.
        if action != "refresh":
            dlog("[ACT] key unknown action %r, nothing sent" % action)
            return
        name = config.WIN_REFRESH_KEY
        dlog("[ACT] pyautogui.press(%r) to the focused window" % name)
        try:
            self.gui.press(name)
        except Exception as e:
            dlog("[ACT] press RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] key ok")

    def release(self):
        # Nothing is held down, but make sure no button is stuck.
        try:
            self.gui.mouseUp()
        except Exception as e:
            dlog("[ACT] release mouseUp RAISED %s: %s" % (type(e).__name__, e))


Controller = WindowsController
