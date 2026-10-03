# Pure gesture detection. Never touches the mouse, so it can be tested offline.

import math
from collections import deque

import config

WRIST = 0
INDEX_MCP = 5
MIDDLE_MCP = 9

# (tip, middle joint) per finger, thumb ignored on purpose.
FINGERS = [(8, 6), (12, 10), (16, 14), (20, 18)]

MODE_IDLE = "IDLE"
MODE_POINTER = "POINTER"
MODE_PAGE = "PAGE"

# Modes with a tap: the finger count a tap comes back to, and the name its events log under.
TAP_RETURN_FINGERS = {MODE_POINTER: 1, MODE_PAGE: 2}
TAP_EVENT = {MODE_POINTER: "tap", MODE_PAGE: "page_tap"}


class TapCounts:
    # Outcome counters for one kind of tap.
    def __init__(self):
        self.attempts = 0
        self.success = 0
        self.timeout = 0
        self.cancelled = 0
        self.cooldown = 0
        self.uneven_starts = 0    # taps that started after a short in-between reading
        self.uneven_frames = 0    # in-between frames held during a tap instead of cancelling


class Event:
    # One thing the hand asked for, plus the frame time it came from.
    def __init__(self, kind, value, src_t):
        self.kind = kind
        self.value = value
        self.src_t = src_t

    def __repr__(self):
        return "Event(%s, %r)" % (self.kind, self.value)


def _dist(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def hand_size(lm):
    # Wrist to middle knuckle, used to make everything scale free.
    return max(_dist(lm[WRIST], lm[MIDDLE_MCP]), 1e-6)


def palm_center(lm):
    # Mean of the wrist and the four knuckles, steadier than any single joint.
    pts = (lm[WRIST], lm[5], lm[9], lm[13], lm[17])
    return (sum(float(p[0]) for p in pts) / 5.0, sum(float(p[1]) for p in pts) / 5.0)


def tilt_angle(lm):
    # Degrees from vertical of wrist -> midpoint of the index and middle tips, clockwise on screen is positive.
    # Scaled to pixels first, since landmark x and y are fractions of a 4:3 frame.
    mx = (float(lm[8][0]) + float(lm[12][0])) / 2.0
    my = (float(lm[8][1]) + float(lm[12][1])) / 2.0
    vx = (mx - float(lm[WRIST][0])) * config.CAM_WIDTH
    vy = (my - float(lm[WRIST][1])) * config.CAM_HEIGHT
    return math.degrees(math.atan2(vx, -vy))


def _wrap_deg(a):
    # Keeps an angle difference in -180..180.
    return (a + 180.0) % 360.0 - 180.0


def fingers_up(lm):
    # A finger is up when its tip is farther from the wrist than its middle joint.
    wrist = lm[WRIST]
    n = 0
    for tip, pip in FINGERS:
        if _dist(lm[tip], wrist) > _dist(lm[pip], wrist):
            n += 1
    return n


def fingers_detail(lm):
    # Diagnostic only. Same rule as fingers_up, but keeps the margin per finger.
    wrist = lm[WRIST]
    out = []
    for tip, pip in FINGERS:
        dt = _dist(lm[tip], wrist)
        dp = _dist(lm[pip], wrist)
        out.append((dt > dp, dt, dp))
    return out


class LowPass:
    def __init__(self):
        self.y = None

    def __call__(self, x, alpha):
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y


class OneEuroFilter:
    # Heavy smoothing when slow, light when fast.
    def __init__(self, min_cutoff, beta, d_cutoff):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.x_filt = LowPass()
        self.dx_filt = LowPass()
        self.x_prev = None
        self.t_prev = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self):
        self.x_filt = LowPass()
        self.dx_filt = LowPass()
        self.x_prev = None
        self.t_prev = None

    def __call__(self, x, t):
        if self.t_prev is None or t <= self.t_prev:
            self.t_prev = t
            self.x_prev = x
            return self.x_filt(x, 1.0)
        dt = t - self.t_prev
        dx = (x - self.x_prev) / dt
        dx_hat = self.dx_filt(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        out = self.x_filt(x, self._alpha(cutoff, dt))
        self.t_prev = t
        self.x_prev = x
        return out


def apply_sensitivity(sx, sy, screen_w, screen_h, gain):
    # Pushes the mapped point away from the screen centre by gain, then keeps it on screen.
    cx = (screen_w - 1) / 2.0
    cy = (screen_h - 1) / 2.0
    gx = min(max(cx + (sx - cx) * gain, 0.0), screen_w - 1.0)
    gy = min(max(cy + (sy - cy) * gain, 0.0), screen_h - 1.0)
    return gx, gy


def sensitivity_summary(screen_w, screen_h):
    # One log line: the gain, an example point before and after, and the hand range that now covers the screen.
    gain = config.CURSOR_SENSITIVITY
    sx, sy = 0.75 * (screen_w - 1), 0.25 * (screen_h - 1)
    gx, gy = apply_sensitivity(sx, sy, screen_w, screen_h, gain)
    text = ("cursor sensitivity = %.2fx around screen centre (%.0f, %.0f); example active-region point (%.0f, %.0f) -> (%.0f, %.0f); "
            % (gain, (screen_w - 1) / 2.0, (screen_h - 1) / 2.0, sx, sy, gx, gy))
    if gain < 1.0:
        return text + "screen edges NOT reachable below 1.0"
    lo = 0.5 - 0.5 / gain
    x0 = config.ACTIVE_X_MIN + lo * (config.ACTIVE_X_MAX - config.ACTIVE_X_MIN)
    x1 = config.ACTIVE_X_MAX - lo * (config.ACTIVE_X_MAX - config.ACTIVE_X_MIN)
    y0 = config.ACTIVE_Y_MIN + lo * (config.ACTIVE_Y_MAX - config.ACTIVE_Y_MIN)
    y1 = config.ACTIVE_Y_MAX - lo * (config.ACTIVE_Y_MAX - config.ACTIVE_Y_MIN)
    return text + "screen edges reached at camera x %.3f..%.3f y %.3f..%.3f" % (x0, x1, y0, y1)


class GestureEngine:
    # Turns landmarks into events. No side effects.
    def __init__(self, screen_w, screen_h):
        self.screen_w = screen_w
        self.screen_h = screen_h

        self.armed = False
        self.mode = MODE_IDLE
        self.switching = False

        self._cand = None
        self._streak = 0

        self._palm_since = None
        self._palm_toggled = False
        self._palm_pos = None
        self.arm_resets_drift = 0
        self.arm_resets_fingers = 0
        self.arm_resets_lost = 0
        self.arm_hold_max_before_drift = 0.0


        self._fx = OneEuroFilter(config.OE_MIN_CUTOFF, config.OE_BETA, config.OE_D_CUTOFF)
        self._fy = OneEuroFilter(config.OE_MIN_CUTOFF, config.OE_BETA, config.OE_D_CUTOFF)

        self._last_xy = None

        # One tap gate shared by POINTER (click) and PAGE (refresh), counted per mode.
        self._tap_active = False
        self._tap_src = None
        self._tap_start_t = None
        self._tap_frozen_xy = None
        self._tap_lead = 0      # in-between frames just before a possible drop
        self._tap_uneven = 0    # in-between frames in a row during a tap
        self._last_tap = {MODE_POINTER: -1e9, MODE_PAGE: -1e9}
        self.tap_counts = {MODE_POINTER: TapCounts(), MODE_PAGE: TapCounts()}
        self.page_tap_frozen_frames = 0
        self.page_tap_held_frames = 0

        self._anchor = None
        self._axis = None
        self._scroll_acc = 0.0
        self._hscroll_acc = 0.0

        # Tilt baseline lives and dies with the scroll anchor.
        self._tilt_base = None
        self._tilt_streak = 0
        self._tilt_dir = None
        self._tilt_onset = None
        self._tilt_latched = False
        self._tilt_blocked = False
        self._last_tilt = -1e9
        self.tilt_attempts = 0
        self.tilt_fires = 0
        self.tilt_released = 0
        self.tilt_blocked = 0
        self.tilt_watch_frames = 0
        self.scroll_suppressed_frames = 0
        self.scroll_won_frames = 0

        self.dbg_scroll = None
        self.dbg_hscroll = None
        self.dbg_page_reset = None
        self.dbg_anchor_set = None
        self.dbg_page = None
        self.dbg_flicker = None
        self.dbg_tilt = None
        self.dbg_precedence = None
        self.dbg_owner = None

        # PAGE mode stability counters, diagnostic only.
        self.page_frames = 0
        self.page_time = 0.0
        self.page_off_frames = 0
        self.page_flicker_noise = 0
        self.page_flicker_real = 0
        self.page_anchor_resets_mode = 0
        self.page_anchor_resets_no_mode = 0
        self._page_last_t = None
        self._exc_start = None
        self._exc_frames = 0
        self._exc_counts = []
        self._exc_reset = False
        self._exc_tap = False
        self.page_flicker_tap = 0

        self._cand_start_t = None
        self.finger_count = 0
        self.dbg_map = None
        self.dbg_arm = None
        self.dbg_arm_reset = None

    def _reset_page(self, reason=None):
        # Records why the scroll anchor went away, for the scroll direction trace.
        if self._anchor is not None:
            self.dbg_page_reset = (reason or "unspecified", self._anchor[1], self._axis)
        self._anchor = None
        self._axis = None
        self._scroll_acc = 0.0
        self._hscroll_acc = 0.0
        if self._tilt_streak > 0 and not self._tilt_blocked:
            self.tilt_released += 1
        self._tilt_base = None
        self._tilt_streak = 0
        self._tilt_dir = None
        self._tilt_onset = None
        self._tilt_latched = False
        self._tilt_blocked = False

    def _reset_pointer(self):
        self._fx.reset()
        self._fy.reset()

    def _end_tap(self):
        # Clears the tap attempt and lets the cursor pick up the hand again cleanly.
        src = self._tap_src
        self._tap_active = False
        self._tap_src = None
        self._tap_start_t = None
        self._tap_frozen_xy = None
        self._tap_lead = 0
        self._tap_uneven = 0
        self._fx.reset()
        self._fy.reset()
        if src == MODE_PAGE:
            # The curl moves the hand, so the next two-finger frame sets a fresh anchor and tilt baseline.
            self._reset_page("page tap ended")

    def _raw_mode(self, count):
        if count == 1:
            return MODE_POINTER
        if count == 2:
            return MODE_PAGE
        return MODE_IDLE

    def update(self, lm, now):
        # Returns the events for this frame. Event times are when the gesture started.
        events = []
        self.dbg_arm_reset = None
        self.dbg_scroll = None
        self.dbg_hscroll = None
        self.dbg_page_reset = None
        self.dbg_anchor_set = None
        self.dbg_page = None
        self.dbg_flicker = None
        self.dbg_tilt = None
        self.dbg_precedence = None
        self.dbg_owner = None
        mode_before = self.mode

        if lm is None:
            self.finger_count = 0
            if self._palm_since is not None:
                self.dbg_arm_reset = (now - self._palm_since, "hand lost, no landmarks this frame", "lost")
                if not self._palm_toggled:
                    self.arm_resets_lost += 1
            self._palm_since = None
            self._palm_toggled = False
            self._palm_pos = None
            self.dbg_arm = None
            raw, tap_events = self._tap_gate(0, MODE_IDLE, now, False)
            events += tap_events
            self._reset_pointer()
            self._reset_page("hand lost")
            events += self._track_mode(raw, now)
            self._track_page_stability(mode_before, None, now)
            return events

        count = fingers_up(lm)
        self.finger_count = count

        events += self._arm(lm, count, now)
        raw, tap_events = self._tap_gate(count, self._raw_mode(count), now, True)
        events += tap_events
        events += self._track_mode(raw, now)
        self._track_page_stability(mode_before, count, now)

        # Nothing acts while disarmed or mid mode switch.
        if not self.armed or self.switching:
            if self.mode != MODE_PAGE:
                self._reset_page("disarmed or mid mode switch")
            return events

        if self.mode == MODE_POINTER:
            self._reset_page("pointer mode")
            events += self._pointer(lm, now)
        elif self.mode == MODE_PAGE:
            self._reset_pointer()
            events += self._page(lm, now)
        else:
            self._reset_page("idle mode")
        return events

    def set_armed(self, on, now):
        # Same effect as the open-palm toggle, for an arm button outside the gesture path.
        self.armed = bool(on)
        self._reset_pointer()
        self._reset_page("arm toggled")
        return Event("arm", self.armed, now)

    def _track_page_stability(self, mode_before, count, now):
        # Diagnostic only. Splits finger-count noise in PAGE from real exits out of PAGE.
        if mode_before != MODE_PAGE:
            self._exc_start = None
            self._page_last_t = None
            return
        self.page_frames += 1
        if self._page_last_t is not None:
            self.page_time += now - self._page_last_t
        self._page_last_t = now

        reset_now = self.dbg_page_reset is not None

        if self.mode != MODE_PAGE:
            # The excursion lasted long enough to switch modes for real.
            self.page_flicker_real += 1
            if reset_now or self._exc_reset:
                self.page_anchor_resets_mode += 1
            if self._exc_start is not None:
                dur = now - self._exc_start
                frames = self._exc_frames + 1
                seen = self._exc_counts + [count]
            else:
                dur, frames, seen = 0.0, 1, [count]
            self.dbg_flicker = ("REAL MODE CHANGE", dur, frames, seen, self.mode,
                                reset_now or self._exc_reset)
            self._exc_start = None
            self._exc_reset = False
            self._page_last_t = None
            return

        if count != 2:
            self.page_off_frames += 1
            if self._exc_start is None:
                self._exc_start = now
                self._exc_frames = 1
                self._exc_counts = [count]
                self._exc_reset = reset_now
                # The tap gate runs first, so a refresh tap is already active on its first frame.
                self._exc_tap = self._tap_active and self._tap_src == MODE_PAGE
            else:
                self._exc_frames += 1
                self._exc_counts.append(count)
                self._exc_reset = self._exc_reset or reset_now
                # An uneven drop starts the tap a frame or two into the excursion.
                self._exc_tap = self._exc_tap or (self._tap_active and self._tap_src == MODE_PAGE)
        elif self._exc_start is not None:
            # Back to two fingers before a mode switch happened.
            if self._exc_tap:
                # A refresh tap attempt, not noise: its anchor reset is on purpose.
                self.page_flicker_tap += 1
                kind = "PAGE TAP"
            else:
                self.page_flicker_noise += 1
                if self._exc_reset:
                    self.page_anchor_resets_no_mode += 1
                kind = "NOISE"
            self.dbg_flicker = (kind, now - self._exc_start, self._exc_frames,
                                self._exc_counts, MODE_PAGE, self._exc_reset)
            self._exc_start = None
            self._exc_reset = False
            self._exc_tap = False

    def _arm(self, lm, count, now):
        # Open palm held long enough toggles control on or off.
        events = []
        if count >= config.OPEN_PALM_FINGERS:
            pos = palm_center(lm)
            drift = 0.0
            if self._palm_pos is not None:
                drift = math.hypot(pos[0] - self._palm_pos[0], pos[1] - self._palm_pos[1])
            if self._palm_since is not None and drift > config.ARM_HOLD_MAX_DRIFT:
                # A moving palm is not a hold, so the hold starts over from here.
                lost = now - self._palm_since
                self.dbg_arm_reset = (lost, "palm moved %.3f > ARM_HOLD_MAX_DRIFT %.3f" % (drift, config.ARM_HOLD_MAX_DRIFT), "drift")
                if not self._palm_toggled:
                    self.arm_resets_drift += 1
                    self.arm_hold_max_before_drift = max(self.arm_hold_max_before_drift, lost)
                self._palm_since = None
            if self._palm_since is None:
                self._palm_since = now
                self._palm_pos = pos
                self.dbg_arm = (0.0, True, count, self._palm_toggled)
            else:
                elapsed = now - self._palm_since
                self.dbg_arm = (elapsed, False, count, self._palm_toggled)
                if not self._palm_toggled and elapsed >= config.OPEN_PALM_HOLD_S:
                    onset = self._palm_since
                    self.armed = not self.armed
                    self._palm_toggled = True
                    self._reset_pointer()
                    self._reset_page("arm toggled")
                    events.append(Event("arm", self.armed, onset))
        else:
            if self._palm_since is not None:
                self.dbg_arm_reset = (now - self._palm_since,
                                      "fingers=%d fell below %d" % (count, config.OPEN_PALM_FINGERS), "fingers")
                if not self._palm_toggled:
                    self.arm_resets_fingers += 1
            self._palm_since = None
            self._palm_toggled = False
            self._palm_pos = None
            self.dbg_arm = None
        return events

    def _tap_timing(self, src):
        # Window and cooldown in seconds for a tap in this mode, read every frame so the app sliders apply.
        if src == MODE_POINTER:
            return config.CLICK_TAP_WINDOW_MS / 1000.0, config.CLICK_COOLDOWN_S
        return config.PAGE_TAP_WINDOW_MS / 1000.0, config.PAGE_TAP_COOLDOWN_S

    def _tap_action(self, src, onset):
        # What a finished tap does: a left click in POINTER, the refresh key in PAGE.
        if src == MODE_POINTER:
            return Event("click", None, onset)
        return Event("key", "refresh", onset)

    def _tap_edge(self, src):
        # Frames an in-between finger count is let through on either edge of a tap. Only PAGE has one (1 of 2 fingers).
        return config.PAGE_TAP_EDGE_FRAMES if src == MODE_PAGE else 0

    def _between(self, src, count):
        # Some but not all of the tapping fingers up: one finger ahead of the other.
        return 0 < count < TAP_RETURN_FINGERS[src]

    def _tap_gate(self, count, raw, now, hand_ok):
        # Holds off the switch to IDLE so a quick tap can finish:
        # the index in POINTER (click), index + middle together in PAGE (refresh). Same rules for both.
        events = []

        if not self._tap_active:
            # An uneven drop (2 -> 1 -> 0) leaves a short mode switch pending; that alone does not block the tap.
            lead_ok = (self.switching and 0 < self._tap_lead <= self._tap_edge(self.mode)
                       and self._streak == self._tap_lead)
            # The tapping fingers all down starts a possible tap, not a mode switch.
            if (self.mode in TAP_RETURN_FINGERS and self.armed and (not self.switching or lead_ok)
                    and hand_ok and count == 0):
                src = self.mode
                self._tap_active = True
                self._tap_src = src
                self._tap_start_t = now
                self._tap_frozen_xy = self._last_xy if src == MODE_POINTER else None
                self.tap_counts[src].attempts += 1
                if lead_ok:
                    self.tap_counts[src].uneven_starts += 1
                self._tap_lead = 0
                events.append(Event(TAP_EVENT[src], "attempt", now))
                return src, events
            # Counts in-between frames right before a possible drop.
            if self.mode in TAP_RETURN_FINGERS and hand_ok and self._between(self.mode, count):
                self._tap_lead += 1
            else:
                self._tap_lead = 0
            return raw, events

        src = self._tap_src
        tc = self.tap_counts[src]
        kind = TAP_EVENT[src]
        window, cooldown = self._tap_timing(src)
        onset = self._tap_start_t
        elapsed = now - onset

        # Hand lost or control turned off cancels the attempt.
        if not hand_ok or not self.armed:
            self._end_tap()
            tc.cancelled += 1
            events.append(Event(kind, "cancel", onset))
            return raw, events

        # Back to the mode's finger count inside the window is a tap.
        if count == TAP_RETURN_FINGERS[src]:
            self._end_tap()
            if now - self._last_tap[src] >= cooldown:
                self._last_tap[src] = now
                tc.success += 1
                events.append(Event(kind, "success", onset))
                events.append(self._tap_action(src, onset))
            else:
                tc.cooldown += 1
                events.append(Event(kind, "cooldown", onset))
            return src, events

        # Still down, or a short in-between reading on the way back up: keep holding the mode until the window runs out.
        between = self._between(src, count)
        self._tap_uneven = self._tap_uneven + 1 if between else 0
        if count == 0 or (between and self._tap_uneven <= self._tap_edge(src)):
            if elapsed < window:
                if between:
                    tc.uneven_frames += 1
                return src, events
            self._end_tap()
            tc.timeout += 1
            events.append(Event(kind, "timeout", onset))
            return raw, events

        # Any other finger count means this was never a tap.
        self._end_tap()
        tc.cancelled += 1
        events.append(Event(kind, "cancel", onset))
        return raw, events

    def _track_mode(self, raw, now):
        # A mode only changes after the new finger count holds for N frames.
        events = []
        if raw == self.mode:
            self._cand = None
            self._streak = 0
            self._cand_start_t = None
            self.switching = False
            return events
        if raw == self._cand:
            self._streak += 1
        else:
            self._cand = raw
            self._streak = 1
            self._cand_start_t = now
        self.switching = True
        if self._streak >= config.MODE_HOLD_FRAMES:
            onset = self._cand_start_t if self._cand_start_t is not None else now
            self.mode = raw
            self._cand = None
            self._streak = 0
            self._cand_start_t = None
            self.switching = False
            self._reset_pointer()
            self._reset_page("mode changed to %s" % self.mode)
            events.append(Event("mode", self.mode, onset))
        return events

    def _pointer(self, lm, now):
        # Cursor follows the index knuckle, and holds still during a tap.
        events = []
        if self._tap_active:
            if self._tap_frozen_xy is not None:
                events.append(Event("move", self._tap_frozen_xy, now))
            return events

        rx = float(lm[INDEX_MCP][0])
        ry = float(lm[INDEX_MCP][1])
        fx = self._fx(rx, now)
        fy = self._fy(ry, now)

        span_x = config.ACTIVE_X_MAX - config.ACTIVE_X_MIN
        span_y = config.ACTIVE_Y_MAX - config.ACTIVE_Y_MIN
        nx = min(max((fx - config.ACTIVE_X_MIN) / span_x, 0.0), 1.0)
        ny = min(max((fy - config.ACTIVE_Y_MIN) / span_y, 0.0), 1.0)
        sx = nx * (self.screen_w - 1)
        sy = ny * (self.screen_h - 1)
        gx, gy = apply_sensitivity(sx, sy, self.screen_w, self.screen_h, config.CURSOR_SENSITIVITY)
        # Diagnostic only, nothing reads this back.
        self.dbg_map = (rx, ry, fx, fy, nx, ny, sx, sy, gx, gy)
        self._last_xy = (gx, gy)
        events.append(Event("move", (gx, gy), now))
        return events

    def _tilt(self, lm, now):
        # Wrist rotation against the pose-start angle. Fires back / forward plus one sideways wheel notch.
        events = []
        angle = tilt_angle(lm)
        delta = _wrap_deg(angle - self._tilt_base)
        mag = abs(delta)
        side = "left" if delta < 0 else "right"
        thr = config.TILT_ANGLE_THRESHOLD
        if mag >= config.TILT_WATCH_THRESHOLD:
            self.tilt_watch_frames += 1

        if self._tilt_latched:
            # Already fired: it has to come back inside the threshold before it can fire again.
            if mag < thr:
                self._tilt_latched = False
                state = "reset, back inside %.0f deg" % thr
            else:
                state = "fired, waiting to come back inside %.0f deg" % thr
        elif mag >= thr:
            if self._tilt_streak == 0 or side != self._tilt_dir:
                self._tilt_streak = 0
                self._tilt_dir = side
                self._tilt_onset = now
                self._tilt_blocked = False
                self.tilt_attempts += 1
                events.append(Event("tilt", "attempt", now))
            self._tilt_streak += 1
            if self._tilt_streak < config.TILT_HOLD_FRAMES:
                state = "firing, hold %d/%d" % (self._tilt_streak, config.TILT_HOLD_FRAMES)
            elif (now - self._last_tilt) * 1000.0 < config.TILT_COOLDOWN_MS:
                state = "cooldown, %.0f ms left" % (config.TILT_COOLDOWN_MS - (now - self._last_tilt) * 1000.0)
                if not self._tilt_blocked:
                    self._tilt_blocked = True
                    self.tilt_blocked += 1
                    events.append(Event("tilt", "cooldown", now))
            else:
                action = config.TILT_LEFT_ACTION if side == "left" else config.TILT_RIGHT_ACTION
                # Same convention as lateral scroll: tilting right moves content right, flipped by SCROLL_H_INVERT.
                wheel = -config.TILT_HSCROLL_DELTA if side == "right" else config.TILT_HSCROLL_DELTA
                if config.SCROLL_H_INVERT:
                    wheel = -wheel
                onset = self._tilt_onset
                self._last_tilt = now
                self._tilt_latched = True
                self._tilt_streak = 0
                self.tilt_fires += 1
                state = "FIRE %s + eased hscroll %+d" % (action, wheel)
                events.append(Event("tilt", "fire", onset))
                events.append(Event("nav", action, onset))
                # Eased by the output layer; the button above stays instant.
                events.append(Event("hscroll_ease", wheel, onset))
        else:
            if self._tilt_streak > 0 and not self._tilt_blocked:
                self.tilt_released += 1
                events.append(Event("tilt", "released", now))
            self._tilt_streak = 0
            self._tilt_blocked = False
            state = "watching" if mag >= config.TILT_WATCH_THRESHOLD else "neutral"
        self.dbg_tilt = (self._tilt_base, angle, delta, state)
        return events, delta

    def _would_move(self, dx, dy):
        # Would movement scroll (or lock an axis) this frame? Read only, the scroll math decides.
        if self._axis is None:
            return math.hypot(dx, dy) > config.DIR_LOCK_MIN_MOVE
        if self._axis == "v":
            return abs(dy) > config.SCROLL_DEAD_ZONE
        return abs(dx) > config.SCROLL_H_DEAD_ZONE

    def _page(self, lm, now):
        # One owner per frame, in order: a refresh tap in progress, then tilt, then vertical or horizontal scroll.
        events = []
        p = lm[config.HAND_POINT]
        x, y = float(p[0]), float(p[1])

        if self._tap_active:
            # Fingers are curling: anchor and tilt baseline stay as they were before the tap, tilt state is not touched.
            self.page_tap_frozen_frames += 1
            if self._anchor is None:
                self.dbg_page = (x, y, 0.0, 0.0, self._axis, "held, refresh tap in progress")
                self.dbg_owner = ("TAP", None)
                return events
            dx = x - self._anchor[0]
            dy = y - self._anchor[1]
            # Read only, for the log: what the curl would have done without the hold.
            delta = _wrap_deg(tilt_angle(lm) - self._tilt_base)
            if self._would_move(dx, dy) or abs(delta) >= config.TILT_WATCH_THRESHOLD:
                self.page_tap_held_frames += 1
                self.dbg_precedence = ("TAP", delta, dx, dy, self._axis)
            self.dbg_page = (x, y, dx, dy, self._axis, "held, refresh tap in progress, pre-tap anchor kept")
            self.dbg_owner = ("TAP", delta)
            return events

        if self._anchor is None:
            self._anchor = (x, y)
            self._scroll_acc = 0.0
            self._hscroll_acc = 0.0
            self._tilt_base = tilt_angle(lm)
            self.dbg_anchor_set = (y, "page mode pose started")

        dx = x - self._anchor[0]
        dy = y - self._anchor[1]

        tilt_events, delta = self._tilt(lm, now)
        events += tilt_events

        moving = self._would_move(dx, dy)
        # Diagnostic only: which handler owns this frame, for the per-frame precedence log.
        self.dbg_owner = ("TILT" if abs(delta) >= config.TILT_WATCH_THRESHOLD else "SCROLL", delta)

        if abs(delta) >= config.TILT_WATCH_THRESHOLD:
            # Tilting moves the knuckle too, so rotation beats movement for this frame.
            if moving:
                self.scroll_suppressed_frames += 1
                self.dbg_precedence = ("TILT", delta, dx, dy, self._axis)
            self.dbg_page = (x, y, dx, dy, self._axis,
                             "scroll paused, tilt %+.1f deg >= watch %.1f" % (delta, config.TILT_WATCH_THRESHOLD))
            return events
        if moving and abs(delta) >= config.TILT_WATCH_THRESHOLD / 2.0:
            # Some rotation, but under the watch angle: movement wins. Logged so a misfire can be traced.
            self.scroll_won_frames += 1
            self.dbg_precedence = ("SCROLL", delta, dx, dy, self._axis)

        if self._axis is None:
            if math.hypot(dx, dy) > config.DIR_LOCK_MIN_MOVE:
                self._axis = "v" if abs(dy) > abs(dx) else "h"
                self.dbg_page = (x, y, dx, dy, self._axis, "axis just locked to %s" % self._axis)
            else:
                self.dbg_page = (x, y, dx, dy, None, "waiting for axis lock")
            return events

        if self._axis == "v":
            excess = abs(dy) - config.SCROLL_DEAD_ZONE
            if excess > 0.0:
                # Hand above the start point scrolls up. Sign rule unchanged.
                sign = -1.0 if dy < 0 else 1.0
                # Non linear: gentle near the anchor, ramps up further out.
                t = min(excess * config.SCROLL_SENSITIVITY, 1.0)
                shaped = t ** config.SCROLL_CURVE_EXP
                amount = sign * shaped * config.SCROLL_MAX_STEP
                if config.SCROLL_INVERT:
                    amount = -amount
                # Carry the fraction so slow movement still scrolls, just rarely.
                self._scroll_acc += amount
                step = int(self._scroll_acc)
                self._scroll_acc -= step
                self.dbg_scroll = (self._anchor[1], y, dy, sign, t, shaped, amount, step)
                if step != 0:
                    events.append(Event("scroll", step, now))
            else:
                self._scroll_acc = 0.0
            self.dbg_page = (x, y, dx, dy, "v", "scroll" if excess > 0.0 else "in dead zone")
            return events

        # Horizontal: the vertical scroll math on the x axis, with its own constants.
        excess = abs(dx) - config.SCROLL_H_DEAD_ZONE
        if excess > 0.0:
            # Windows' sideways wheel is positive to the right, the vertical wheel positive upward,
            # so hand right sends a left wheel: the content follows the hand on both axes.
            sign = -1.0 if dx > 0 else 1.0
            t = min(excess * config.SCROLL_H_SENSITIVITY, 1.0)
            shaped = t ** config.SCROLL_H_CURVE_EXP
            amount = sign * shaped * config.SCROLL_H_MAX_STEP
            if config.SCROLL_H_INVERT:
                amount = -amount
            # Carry the fraction so slow movement still scrolls, just rarely.
            self._hscroll_acc += amount
            step = int(self._hscroll_acc)
            self._hscroll_acc -= step
            self.dbg_hscroll = (self._anchor[0], x, dx, sign, t, shaped, amount, step)
            if step != 0:
                events.append(Event("hscroll", step, now))
        else:
            self._hscroll_acc = 0.0
        self.dbg_page = (x, y, dx, dy, "h", "hscroll" if excess > 0.0 else "in dead zone")
        return events


class Metrics:
    # Counts gesture quality without needing ground truth labels.
    def __init__(self):
        self.t_start = None
        self.t_end = None
        self.delays = []
        self.mode_changes = []
        self.navs = []
        self.false_triggers = 0
        self.wrong_mode_switches = 0
        self.accidental_nav = 0
        self.n_actions = 0
        self.taps = {"attempt": 0, "success": 0, "timeout": 0, "cancel": 0, "cooldown": 0}
        self.page_taps = {"attempt": 0, "success": 0, "timeout": 0, "cancel": 0, "cooldown": 0}

    def tick(self, now):
        if self.t_start is None:
            self.t_start = now
        self.t_end = now

    def add(self, event, now):
        self.tick(now)
        if event.kind == "mode":
            self._mode_change(event.value, now)
        elif event.kind == "tap":
            if event.value in self.taps:
                self.taps[event.value] += 1
        elif event.kind == "page_tap":
            if event.value in self.page_taps:
                self.page_taps[event.value] += 1
        elif event.kind in ("click", "nav", "key"):
            self.n_actions += 1
            self.delays.append((now - event.src_t) * 1000.0)
            self._check_false_trigger(now)
            if event.kind == "nav":
                self._check_nav(event.value, now)

    def _mode_change(self, mode, now):
        # A -> B -> A inside the flap window means the first switch was wrong.
        if len(self.mode_changes) >= 2:
            t_prev, m_prev = self.mode_changes[-1]
            _, m_prev2 = self.mode_changes[-2]
            if mode == m_prev2 and now - t_prev <= config.MODE_FLAP_WINDOW_S:
                self.wrong_mode_switches += 1
        self.mode_changes.append((now, mode))

    def _check_false_trigger(self, now):
        # An action right after a mode change was probably not meant.
        if self.mode_changes:
            t_last, _ = self.mode_changes[-1]
            if now - t_last <= config.FALSE_TRIGGER_WINDOW_S:
                self.false_triggers += 1

    def _check_nav(self, action, now):
        # A tilt while still settling into page mode, or one that gets undone, is accidental.
        settling = False
        for t, mode in reversed(self.mode_changes):
            if mode == MODE_PAGE:
                settling = (now - t) <= config.TILT_SETTLE_WINDOW_S
                break
        if self.navs:
            t_prev, a_prev = self.navs[-1]
            if a_prev != action and now - t_prev <= config.TILT_REVERSAL_WINDOW_S:
                # Both the undone tilt and this one count.
                self.accidental_nav += 2
                settling = False
        if settling:
            self.accidental_nav += 1
        self.navs.append((now, action))

    def minutes(self):
        if self.t_start is None or self.t_end is None:
            return 1e-9
        return max((self.t_end - self.t_start) / 60.0, 1e-9)

    def _pct(self, p):
        if not self.delays:
            return 0.0
        s = sorted(self.delays)
        i = min(int(round((p / 100.0) * (len(s) - 1))), len(s) - 1)
        return s[i]

    def report(self):
        m = self.minutes()
        mean = sum(self.delays) / len(self.delays) if self.delays else 0.0
        dur = (self.t_end - self.t_start) if self.t_start is not None else 0.0
        att = self.taps["attempt"]
        missed = self.taps["timeout"] + self.taps["cancel"]
        patt = self.page_taps["attempt"]
        pmissed = self.page_taps["timeout"] + self.page_taps["cancel"]
        return {
            "page_tap_attempts": patt,
            "page_tap_refreshes_fired": self.page_taps["success"],
            "page_taps_timed_out_to_mode_switch": self.page_taps["timeout"],
            "page_taps_cancelled": self.page_taps["cancel"],
            "page_taps_blocked_by_cooldown": self.page_taps["cooldown"],
            "page_tap_miss_rate_pct": round(100.0 * pmissed / patt, 1) if patt else 0.0,
            "click_attempts": att,
            "clicks_fired": self.taps["success"],
            "taps_timed_out_to_mode_switch": self.taps["timeout"],
            "taps_cancelled": self.taps["cancel"],
            "taps_blocked_by_cooldown": self.taps["cooldown"],
            "tap_miss_rate_pct": round(100.0 * missed / att, 1) if att else 0.0,
            "duration_s": round(dur, 2),
            "actions_total": self.n_actions,
            "false_triggers_per_min": round(self.false_triggers / m, 2),
            "gesture_to_action_delay_ms_mean": round(mean, 1),
            "gesture_to_action_delay_ms_p95": round(self._pct(95), 1),
            "wrong_mode_switches_per_min": round(self.wrong_mode_switches / m, 2),
            "accidental_back_forward_per_min": round(self.accidental_nav / m, 2),
        }


REPORT_KEYS = [
    "duration_s",
    "actions_total",
    "click_attempts",
    "clicks_fired",
    "taps_timed_out_to_mode_switch",
    "taps_cancelled",
    "taps_blocked_by_cooldown",
    "tap_miss_rate_pct",
    "page_tap_attempts",
    "page_tap_refreshes_fired",
    "page_taps_timed_out_to_mode_switch",
    "page_taps_cancelled",
    "page_taps_blocked_by_cooldown",
    "page_tap_miss_rate_pct",
    "false_triggers_per_min",
    "gesture_to_action_delay_ms_mean",
    "gesture_to_action_delay_ms_p95",
    "wrong_mode_switches_per_min",
    "accidental_back_forward_per_min",
]


def arm_hold_stats(engine):
    # Why arm holds were lost before they could toggle. Holds cut short after a toggle are not counted.
    return [
        ("arm_hold_resets_by_drift", engine.arm_resets_drift),
        ("arm_hold_resets_by_finger_drop", engine.arm_resets_fingers),
        ("arm_hold_resets_by_hand_lost", engine.arm_resets_lost),
        ("arm_hold_longest_progress_before_drift_reset_s", round(engine.arm_hold_max_before_drift, 3)),
    ]


def format_arm_hold_stats(engine, prefix=""):
    return "\n".join("%s%s=%s" % (prefix, k, v) for k, v in arm_hold_stats(engine))


def tilt_stats(engine):
    # Tilt outcomes, and how often tilt and movement competed in the same frame.
    return [
        ("tilt_attempts", engine.tilt_attempts),
        ("tilt_fires", engine.tilt_fires),
        ("tilt_released_before_firing", engine.tilt_released),
        ("tilt_blocked_by_cooldown", engine.tilt_blocked),
        ("tilt_frames_past_watch_angle", engine.tilt_watch_frames),
        ("scroll_suppressed_by_tilt_frames", engine.scroll_suppressed_frames),
        ("scroll_won_over_small_tilt_frames", engine.scroll_won_frames),
    ]


def format_tilt_stats(engine, prefix=""):
    return "\n".join("%s%s=%s" % (prefix, k, v) for k, v in tilt_stats(engine))


def page_stability(engine):
    # Summary of finger-count noise while in PAGE mode.
    secs = max(engine.page_time, 1e-9)
    total = engine.page_flicker_noise + engine.page_flicker_real
    return [
        ("page_seconds", round(engine.page_time, 2)),
        ("page_frames", engine.page_frames),
        ("page_frames_not_2_fingers", engine.page_off_frames),
        ("page_flicker_events_total", total),
        ("page_flicker_noise_only", engine.page_flicker_noise),
        ("page_flicker_became_real_mode_change", engine.page_flicker_real),
        ("page_flicker_events_per_sec", round(total / secs, 2) if engine.page_time > 0 else 0.0),
        ("page_anchor_resets_from_mode_change", engine.page_anchor_resets_mode),
        ("page_anchor_resets_WITHOUT_mode_change", engine.page_anchor_resets_no_mode),
        ("page_flicker_was_page_tap", engine.page_flicker_tap),
        ("page_tap_frames_held", engine.page_tap_frozen_frames),
        ("page_tap_frames_scroll_or_tilt_blocked", engine.page_tap_held_frames),
        ("page_tap_started_after_uneven_drop", engine.tap_counts[MODE_PAGE].uneven_starts),
        ("page_tap_1_finger_frames_let_through", engine.tap_counts[MODE_PAGE].uneven_frames),
    ]


def format_page_stability(engine, prefix=""):
    return "\n".join("%s%s=%s" % (prefix, k, v) for k, v in page_stability(engine))


def format_report(report, prefix=""):
    # Plain key=value lines so runs are easy to compare.
    return "\n".join("%s%s=%s" % (prefix, k, report[k]) for k in REPORT_KEYS)
