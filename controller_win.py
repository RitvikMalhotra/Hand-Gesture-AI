# Windows output backend: SendInput for back / forward and sideways wheel, pyautogui for the rest.
# Only imported on Windows, controller.make_controller() picks it.

import ctypes
import queue
import threading
import time
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


_vol_local = threading.local()
COINIT_APARTMENTTHREADED = 0x2
_COM_RESULTS = {
    0x00000000: "S_OK, started",
    0x00000001: "S_FALSE, already started on this thread",
    0x80010106: "RPC_E_CHANGED_MODE, already started in another mode, still usable",
}


def _com_start():
    # Starts COM on the calling thread once and logs the result, so a failure is visible.
    if getattr(_vol_local, "com", None) is None:
        hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) & 0xFFFFFFFF
        _vol_local.com = _COM_RESULTS.get(hr, "0x%08X FAILED" % hr)
        dlog("[VOLUME] COM on thread %r: CoInitializeEx -> %s" % (threading.current_thread().name, _vol_local.com))


def _endpoint(check_default=False):
    # pycaw master volume of the default output, for the calling thread. COM objects belong to the thread that made them.
    # check_default asks Windows for the default output again and switches if it changed (e.g. a headset was plugged in).
    _com_start()
    loc = _vol_local
    if getattr(loc, "enum", None) is None:
        import comtypes
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.pycaw import IMMDeviceEnumerator
        loc.enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
        loc.ep, loc.dev_id, loc.name = None, None, None
    if loc.ep is None or check_default:
        from pycaw.pycaw import AudioUtilities, EDataFlow, ERole
        dev = loc.enum.GetDefaultAudioEndpoint(EDataFlow.eRender.value, ERole.eMultimedia.value)
        dev_id = dev.GetId()
        if loc.ep is None or dev_id != loc.dev_id:
            old = loc.name
            # Reading the device name is slow (about 0.3 s), so it only runs when the device changes.
            device = AudioUtilities.CreateDevice(dev)
            loc.name, loc.ep, loc.dev_id = device.FriendlyName, device.EndpointVolume, dev_id
            dlog("[VOLUME] output on thread %r: %s%s, id %s, level %.0f%%, %s"
                 % (threading.current_thread().name, loc.name, " (was %s)" % old if old else "", dev_id,
                    loc.ep.GetMasterVolumeLevelScalar() * 100.0, "MUTED" if loc.ep.GetMute() else "not muted"))
    return loc.ep


def _uia():
    # UI Automation for the calling thread, made once per thread.
    _com_start()
    if getattr(_vol_local, "uia", None) is None:
        import comtypes.client
        try:
            from comtypes.gen import UIAutomationClient as uia_mod
        except ImportError:
            uia_mod = comtypes.client.GetModule("UIAutomationCore.dll")
        _vol_local.uia_mod = uia_mod
        _vol_local.uia = comtypes.client.CreateObject(uia_mod.CUIAutomation, interface=uia_mod.IUIAutomation)
    return _vol_local.uia, _vol_local.uia_mod


def under_cursor(x, y):
    # Diagnostic only: what Windows says is at this screen point (item name, item type, control, window class).
    try:
        uia, mod = _uia()
        el = uia.ElementFromPoint(mod.tagPOINT(int(x), int(y)))
        name = el.CurrentName or ""
        kind = el.CurrentItemType or ""
        if not kind:
            # Explorer rows keep the type ("File folder", "Text Document") in a child cell instead.
            cell = el.FindFirst(mod.TreeScope_Children, uia.CreatePropertyCondition(
                mod.UIA_AutomationIdPropertyId, "System.ItemTypeText"))
            if cell:
                kind = cell.GetCurrentPropertyValue(mod.UIA_ValueValuePropertyId) or ""
        r = el.CurrentBoundingRectangle
        return "%r%s, %s, class %s, %dx%d px" % (
            name[:60], " (%s)" % kind if kind else "", el.CurrentLocalizedControlType or "?",
            el.CurrentClassName or "?", r.right - r.left, r.bottom - r.top)
    except Exception as e:
        return "unknown (%s: %s)" % (type(e).__name__, e)


class _PointLog:
    # Logs what is under a screen point on its own thread: the first lookup takes about 1 s, later ones about
    # 20 ms, too slow for the thread that moves the mouse. A lookup at start-up takes the slow first call.
    def __init__(self):
        self._q = queue.Queue()
        threading.Thread(target=self._run, name="point-log", daemon=True).start()
        self._q.put((None, 0, 0))

    def request(self, what, x, y):
        self._q.put((what, x, y))

    def _run(self):
        while True:
            what, x, y = self._q.get()
            desc = under_cursor(x, y)
            if what is not None:
                dlog("[ACT] %s at (%d, %d) was on %s" % (what, x, y, desc))


class _ColdCheck:
    # Once the level has been still for VOLUME_VERIFY_DELAY_S, reads it again on its own thread with a fresh
    # GetSpeakers() and a fresh IAudioEndpointVolume, so it shares nothing with the objects that set the level.
    def __init__(self):
        self._q = queue.Queue()
        threading.Thread(target=self._run, name="volume-verify", daemon=True).start()

    def request(self, pycaw_level, name, dev_id):
        # Called after every set with pycaw's own read-back.
        self._q.put((time.perf_counter(), pycaw_level, name, dev_id))

    def _run(self):
        ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        while True:
            job = self._q.get()
            while True:
                try:
                    job = self._q.get(timeout=config.VOLUME_VERIFY_DELAY_S)    # still changing, wait for the newest
                except queue.Empty:
                    break
            t, level, name, dev_id = job
            try:
                from pycaw.pycaw import AudioUtilities
                dev = AudioUtilities.GetSpeakers()
                cold = dev.EndpointVolume.GetMasterVolumeLevelScalar()
            except Exception as e:
                dlog("[VOLUME] verify FAILED %s: %s" % (type(e).__name__, e))
                continue
            ms = (time.perf_counter() - t) * 1000.0
            if not self._q.empty():
                dlog("[VOLUME] verify skipped: the level changed again during the check")
                continue
            same_dev = dev.id == dev_id
            ok = same_dev and abs(cold - level) < 0.005
            dlog("[VOLUME] verify: pycaw read back %.1f%% on %s; fresh query %.0f ms later %.1f%% on %s -> %s"
                 % (level * 100.0, name, ms, cold * 100.0, dev.FriendlyName,
                    "MATCH" if ok else ("MISMATCH, different device" if not same_dev else "MISMATCH")))


class WindowsController(BaseController):
    # Drives the real mouse.
    def __init__(self):
        import pyautogui
        self.gui = pyautogui
        self.gui.FAILSAFE = config.PYAUTOGUI_FAILSAFE
        self.gui.PAUSE = 0.0
        # Volume log window: level at its start, when it started, last change time.
        self._vol_from = None
        self._vol_t0 = 0.0
        self._vol_last = 0.0
        self._vol_now = None
        self._vol_muted = False
        self._vol_dev = (None, None)
        self._verify = _ColdCheck()
        self._points = _PointLog()
        try:
            ep = _endpoint()
            dlog("[ACT] volume control: %s, master volume %.0f%%%s"
                 % (_vol_local.name, ep.GetMasterVolumeLevelScalar() * 100.0, ", MUTED" if ep.GetMute() else ""))
        except Exception as e:
            dlog("[ACT] volume control UNAVAILABLE %s: %s" % (type(e).__name__, e))

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

    def mouse_down(self):
        x, y = cursor_pos()
        dlog("[ACT] pyautogui.mouseDown() at cursor (%d, %d)" % (x, y))
        self._points.request("mouseDown", x, y)
        try:
            self.gui.mouseDown()
        except Exception as e:
            dlog("[ACT] mouseDown RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] mouseDown ok")

    def mouse_up(self):
        x, y = cursor_pos()
        dlog("[ACT] pyautogui.mouseUp() at cursor (%d, %d)" % (x, y))
        self._points.request("mouseUp", x, y)
        try:
            self.gui.mouseUp()
        except Exception as e:
            dlog("[ACT] mouseUp RAISED %s: %s" % (type(e).__name__, e))
            return
        dlog("[ACT] mouseUp ok")

    def volume(self, delta_pct):
        # A gap since the last call means a new volume gesture: check the default output again first.
        fresh = time.perf_counter() - self._vol_last > config.VOLUME_LOG_INTERVAL_S
        if fresh:
            self._flush_volume()    # log the last gesture under its own device before any switch
        try:
            ep = _endpoint(check_default=fresh)
            if fresh:
                self._vol_muted = bool(ep.GetMute())
            self._vol_dev = (_vol_local.name, _vol_local.dev_id)
            before = ep.GetMasterVolumeLevelScalar()
            want = min(max(before + float(delta_pct) / 100.0, 0.0), 1.0)
            after = before
            if want != before:
                ep.SetMasterVolumeLevelScalar(want, None)
                # Read it back, so a set that did not take never looks like success in the log.
                after = ep.GetMasterVolumeLevelScalar()
                if abs(after - want) > 0.005:
                    dlog("[VOLUME] SET DID NOT TAKE on %s: asked %.2f%%, device reads %.2f%%"
                         % (_vol_local.name, want * 100.0, after * 100.0))
        except Exception as e:
            _vol_local.ep = None    # device may have gone, look it up again next time
            dlog("[VOLUME] FAILED %s: %s (output looked up again on the next call)" % (type(e).__name__, e))
            return
        if after != before:
            self._verify.request(after, self._vol_dev[0], self._vol_dev[1])
        self._log_volume(before, after)

    def _log_volume(self, before, after):
        # One [VOLUME] line per VOLUME_LOG_INTERVAL_S while it changes; a pause ends the window.
        now = time.perf_counter()
        if self._vol_from is not None and now - self._vol_last > config.VOLUME_LOG_INTERVAL_S:
            self._flush_volume()
        if self._vol_from is None:
            self._vol_from, self._vol_t0 = before, now
        self._vol_now, self._vol_last = after, now
        if now - self._vol_t0 >= config.VOLUME_LOG_INTERVAL_S:
            self._flush_volume()

    def _flush_volume(self):
        if self._vol_from is None:
            return
        dlog("[VOLUME] %.0f%% -> %.0f%% (%+.1f) over %.2f s, read back from %s%s"
             % (self._vol_from * 100.0, self._vol_now * 100.0, (self._vol_now - self._vol_from) * 100.0,
                self._vol_last - self._vol_t0, self._vol_dev[0],
                ", device is MUTED so nothing is heard" if self._vol_muted else ""))
        self._vol_from = None

    def release(self):
        self._flush_volume()
        # Nothing is held down, but make sure no button is stuck.
        try:
            self.gui.mouseUp()
        except Exception as e:
            dlog("[ACT] release mouseUp RAISED %s: %s" % (type(e).__name__, e))


Controller = WindowsController
