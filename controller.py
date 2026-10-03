# Mouse and keyboard output. Swappable so gestures can be tested without a real mouse.
# The real output is one backend per OS: controller_win.py (Windows) or controller_mac.py (macOS).

import platform
import threading
import time

import config

# Diagnostic printing. Every real output call is logged with its arguments.
DEBUG = True

# Output backends exist for these platform.system() names only.
SUPPORTED = {"Windows": "controller_win", "Darwin": "controller_mac"}


def log(msg):
    # flush so the output survives redirection to a file and Ctrl-C.
    print(msg, flush=True)


def dlog(msg):
    if DEBUG:
        log(msg)


class UnsupportedPlatform(RuntimeError):
    pass


def require_supported():
    # Stops early with a plain reason instead of half-working on an OS with no output backend.
    name = platform.system()
    if name not in SUPPORTED:
        raise UnsupportedPlatform(
            "HandGesture can control the mouse and keyboard on Windows and macOS only. This system reports %r. "
            "Nothing was started. (main.py --replay works anywhere, it sends no input.)" % name)
    return name


def backend():
    # The output module for this OS, imported only here so the other OS's libraries are never needed.
    # Plain imports, not importlib, so PyInstaller sees the module and bundles it.
    if require_supported() == "Windows":
        import controller_win as mod
    else:
        import controller_mac as mod
    return mod


def make_controller():
    # Real mouse and keyboard output for this OS.
    return backend().Controller()


def cursor_pos():
    return backend().cursor_pos()


def screen_metrics():
    # Screen size straight from the OS, to compare against pyautogui.
    return backend().screen_metrics()


def keys_summary():
    # One log line naming what back, forward, refresh and the sideways wheel send on this OS.
    return backend().KEYS_SUMMARY


def ease_steps(total, steps):
    # Splits total into ease-out chunks, biggest first, that add up to exactly total.
    out = []
    sent = 0
    for i in range(1, steps + 1):
        target = int(round(total * (1.0 - (1.0 - i / float(steps)) ** 3)))
        out.append(target - sent)
        sent = target
    return out


def get_screen_size():
    # Real screen size, with a safe fallback for replay on a headless run.
    try:
        import pyautogui
        return pyautogui.size()
    except Exception:
        return (1920, 1080)


class BaseController:
    # The output interface every backend implements. Scroll amounts are in Windows wheel units (120 = one notch).
    def describe(self):
        return type(self).__name__

    def move(self, x, y):
        raise NotImplementedError

    def click(self):
        raise NotImplementedError

    def scroll(self, steps):
        raise NotImplementedError

    def hscroll(self, steps):
        raise NotImplementedError

    def hscroll_eased(self, total):
        # Plays the chunks on a short-lived thread so the camera loop never waits on them.
        chunks = ease_steps(int(total), config.TILT_EASE_STEPS)
        gap = config.TILT_EASE_DURATION_MS / 1000.0 / max(len(chunks), 1)
        threading.Thread(target=self._play_ease, args=(chunks, gap), name="tilt-ease", daemon=True).start()

    def _play_ease(self, chunks, gap):
        t0 = time.perf_counter()
        for i, c in enumerate(chunks):
            wait = t0 + i * gap - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            if c:
                self.hscroll(c)

    def nav(self, action):
        # action is "back" or "forward"; each backend picks the button or shortcut.
        raise NotImplementedError

    def key(self, action):
        # action is "refresh"; each backend picks the key.
        raise NotImplementedError

    def release(self):
        pass


class DryRunController(BaseController):
    # Records what would have happened. Moves nothing.
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.actions = []

    def describe(self):
        return "DryRunController (NOTHING IS SENT TO THE OS)"

    def _log(self, kind, value):
        self.actions.append((kind, value))
        if self.verbose:
            log("dry-run %s %r" % (kind, value))

    def move(self, x, y):
        self._log("move", (int(x), int(y)))

    def click(self):
        self._log("click", None)

    def scroll(self, steps):
        self._log("scroll", int(steps))

    def hscroll(self, steps):
        self._log("hscroll", int(steps))

    def hscroll_eased(self, total):
        # Same chunks, recorded at once so replays stay deterministic.
        for c in ease_steps(int(total), config.TILT_EASE_STEPS):
            if c:
                self.hscroll(c)

    def nav(self, action):
        self._log("nav", action)

    def key(self, action):
        self._log("key", action)


def dispatch(controller, event):
    # Sends one gesture event to the output layer.
    if event.kind == "move":
        controller.move(event.value[0], event.value[1])
    elif event.kind == "click":
        controller.click()
    elif event.kind == "scroll":
        controller.scroll(event.value)
    elif event.kind == "hscroll":
        controller.hscroll(event.value)
    elif event.kind == "hscroll_ease":
        controller.hscroll_eased(event.value)
    elif event.kind == "nav":
        controller.nav(event.value)
    elif event.kind == "key":
        controller.key(event.value)
