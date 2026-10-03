# Entry point: live control, recording, and offline replay.

import argparse
import json
import math
import platform
import sys
import time

import cv2

import config
import controller as ctrl
import gestures
import tracker

ESC = 27


class Recorder:
    # Writes one JSON line per frame of joint data.
    def __init__(self, path, meta):
        self.f = open(path, "w", encoding="utf-8")
        head = {"type": "meta"}
        head.update(meta)
        self.f.write(json.dumps(head) + "\n")
        self.t0 = None
        self.n = 0

    def add(self, frame_id, t, landmarks, extra=None):
        if self.t0 is None:
            self.t0 = t
        row = {"fid": frame_id, "t": round(t - self.t0, 6)}
        row["lm"] = None if landmarks is None else [[round(float(v), 6) for v in p] for p in landmarks]
        if extra:
            row.update(extra)
        self.f.write(json.dumps(row) + "\n")
        self.n += 1

    def close(self):
        self.f.close()


def read_session(path):
    # Returns (meta, rows) from a recorded file.
    meta = {}
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "meta":
                meta = obj
            else:
                rows.append(obj)
    return meta, rows


def draw_hud(image, engine, fps, latency_ms, hand_seen):
    # Draws state, mode, fps and delay on the preview.
    h, w = image.shape[:2]

    x0 = int(config.ACTIVE_X_MIN * w)
    x1 = int(config.ACTIVE_X_MAX * w)
    y0 = int(config.ACTIVE_Y_MIN * h)
    y1 = int(config.ACTIVE_Y_MAX * h)
    cv2.rectangle(image, (x0, y0), (x1, y1), (120, 120, 120), 1)

    cv2.rectangle(image, (0, 0), (w, 58), (30, 30, 30), -1)

    if engine.armed:
        state, color = "ON", (0, 220, 0)
    else:
        state, color = "OFF", (0, 0, 230)
    cv2.putText(image, state, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    mode = engine.mode
    if engine.switching:
        mode = mode + " ..."
    cv2.putText(image, mode, (78, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    info = "fps %.1f  delay %.0f ms  fingers %d" % (fps, latency_ms, engine.finger_count)
    cv2.putText(image, info, (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    if not hand_seen:
        cv2.putText(image, "no hand", (w - 110, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

    cv2.putText(image, "still open palm 0.5s = arm/disarm   2 fingers + tilt = back/fwd   Esc = quit",
                (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)


class CursorGlide:
    # Nudges the cursor toward the newest detected target between detections.
    def __init__(self):
        self.target = None
        self.pos = None
        self.last = None

    def set_target(self, x, y):
        self.target = (x, y)
        if self.pos is None:
            self.pos = (x, y)

    def clear(self):
        self.target = None
        self.pos = None
        self.last = None

    def step(self, now):
        if self.target is None or self.pos is None:
            return None
        if self.last is None:
            self.last = now
            return None
        dt = now - self.last
        if dt < 1.0 / config.CURSOR_INTERP_HZ:
            return None
        self.last = now
        k = 1.0 - math.exp(-config.CURSOR_INTERP_RESPONSE * dt)
        px = self.pos[0] + (self.target[0] - self.pos[0]) * k
        py = self.pos[1] + (self.target[1] - self.pos[1]) * k
        self.pos = (px, py)
        return (px, py)


def print_boot(args, out, screen_w, screen_h):
    # Shows exactly which flags and output path are active.
    log = ctrl.log
    flags = " ".join(sys.argv[1:]) or "(no flags)"
    log("")
    log("[BOOT] ===================== startup =====================")
    log("[BOOT] command line      = %s" % flags)
    log("[BOOT] --dry-run         = %s" % args.dry_run)
    log("[BOOT] --record          = %s" % args.record)
    log("[BOOT] --replay          = %s" % args.replay)
    log("[BOOT] --log             = %s" % args.log)
    log("[BOOT] --verbose         = %s" % args.verbose)
    log("[BOOT] --low-light       = %s" % getattr(args, "low_light", False))
    log("[BOOT] --rules-only      = NOT IMPLEMENTED in phase 1, cannot suppress anything")
    log("[BOOT] output path       = %s" % out.describe())
    if args.dry_run:
        log("[BOOT] *** --dry-run IS ON: nothing will reach the real mouse ***")
    else:
        log("[BOOT] real output is ENABLED")

    try:
        import pyautogui
        log("[BOOT] pyautogui.size()   = %s" % (tuple(pyautogui.size()),))
        log("[BOOT] pyautogui.FAILSAFE = %s (config.PYAUTOGUI_FAILSAFE=%s)"
            % (pyautogui.FAILSAFE, config.PYAUTOGUI_FAILSAFE))
        log("[BOOT] pyautogui.PAUSE    = %s" % pyautogui.PAUSE)
    except Exception as e:
        log("[BOOT] pyautogui UNAVAILABLE: %s: %s" % (type(e).__name__, e))
    log("[BOOT] OS screen size      = %dx%d (%s)" % (ctrl.screen_metrics() + (platform.system(),)))
    log("[BOOT] engine screen size  = %dx%d" % (screen_w, screen_h))
    log("[BOOT] cursor now          = %s" % (ctrl.cursor_pos(),))

    log("[BOOT] --- gates that must pass before anything moves ---")
    log("[BOOT] 1. ARM: %d fingers up held %.2fs (open palm). Disarmed = no actions at all."
        % (config.OPEN_PALM_FINGERS, config.OPEN_PALM_HOLD_S))
    log("[BOOT] 2. MODE: same finger count for %d frames. 1=POINTER 2=PAGE else IDLE."
        % config.MODE_HOLD_FRAMES)
    log("[BOOT] 3. active region x %.2f..%.2f y %.2f..%.2f -> full screen"
        % (config.ACTIVE_X_MIN, config.ACTIVE_X_MAX, config.ACTIVE_Y_MIN, config.ACTIVE_Y_MAX))
    log("[BOOT] %s" % gestures.sensitivity_summary(screen_w, screen_h))
    log("[BOOT] click tap window    = %d ms, cooldown %.2fs"
        % (config.CLICK_TAP_WINDOW_MS, config.CLICK_COOLDOWN_S))
    log("[BOOT] page tap (refresh) = %d ms window, cooldown %.2fs, same tap gate as click"
        % (config.PAGE_TAP_WINDOW_MS, config.PAGE_TAP_COOLDOWN_S))
    log("[BOOT] scroll dead zone    = %.3f (was 0.040), axis lock floor = %.3f"
        % (config.SCROLL_DEAD_ZONE, config.DIR_LOCK_MIN_MOVE))
    log("[BOOT] arm hold max drift  = %.3f (palm centre movement that restarts the %.2fs hold)"
        % (config.ARM_HOLD_MAX_DRIFT, config.OPEN_PALM_HOLD_S))
    log("[BOOT] page mode tilt      = fire %.1f deg for %d frames, watch %.1f deg pauses scroll, cooldown %d ms, left=%s right=%s, + hwheel %d eased over %d ms as %s"
        % (config.TILT_ANGLE_THRESHOLD, config.TILT_HOLD_FRAMES, config.TILT_WATCH_THRESHOLD, config.TILT_COOLDOWN_MS,
           config.TILT_LEFT_ACTION, config.TILT_RIGHT_ACTION, config.TILT_HSCROLL_DELTA, config.TILT_EASE_DURATION_MS,
           ctrl.ease_steps(config.TILT_HSCROLL_DELTA, config.TILT_EASE_STEPS)))
    log("[BOOT] page mode h scroll  = dead zone %.3f, sensitivity %.2f, exp %.2f, max step %d, invert %s"
        % (config.SCROLL_H_DEAD_ZONE, config.SCROLL_H_SENSITIVITY, config.SCROLL_H_CURVE_EXP,
           config.SCROLL_H_MAX_STEP, config.SCROLL_H_INVERT))
    log("[BOOT] output keys         = %s" % ctrl.keys_summary())
    log("[BOOT] ===================================================")
    log("")


def run_live(args):
    screen_w, screen_h = ctrl.get_screen_size()
    engine = gestures.GestureEngine(screen_w, screen_h)
    metrics = gestures.Metrics()

    if args.quiet:
        ctrl.DEBUG = False

    if args.dry_run:
        out = ctrl.DryRunController(verbose=args.verbose)
    else:
        out = ctrl.make_controller()

    print_boot(args, out, screen_w, screen_h)

    cam = tracker.Camera()
    cam.start()
    hands = tracker.HandTracker(cam)
    hands.start()

    recorder = None
    if args.record:
        meta = {
            "version": 1,
            "screen": [screen_w, screen_h],
            "cam": [config.CAM_WIDTH, config.CAM_HEIGHT],
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        recorder = Recorder(args.record, meta)
        print("recording to %s" % args.record)

    cv2.namedWindow(config.WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
    if config.WINDOW_TOPMOST:
        cv2.setWindowProperty(config.WINDOW_NAME, cv2.WND_PROP_TOPMOST, 1)

    last_id = -1
    fps = 0.0
    last_draw = time.perf_counter()
    last_log = time.perf_counter()
    t_start = time.perf_counter()
    prev_state = None
    last_beat = 0.0
    last_perf = time.perf_counter()
    glide = CursorGlide()
    ctrl.log("[BOOT] camera negotiated %dx%d @ %.0f fps requested (CAM_FPS=%d)"
             % (cam.actual_size[0], cam.actual_size[1], cam.negotiated_fps, config.CAM_FPS))
    for line in cam.settings_report:
        ctrl.log("[BOOT] camera %s" % line)
    ctrl.log("[BOOT] low light mode    = %s (CLAHE clip %.1f, grid %d, detection copy only, preview unchanged)"
             % ("ON" if config.LOW_LIGHT_MODE else "off", config.LOW_LIGHT_CLIP_LIMIT, config.LOW_LIGHT_TILE_GRID))
    ctrl.log("[BOOT] cursor glide      = %s, %d Hz, response %.1f"
             % (config.CURSOR_INTERP, config.CURSOR_INTERP_HZ, config.CURSOR_INTERP_RESPONSE))
    ctrl.log("[BOOT] scroll curve      = sensitivity %.2f, exp %.2f, max step %d, top speed at dy=%.3f"
             % (config.SCROLL_SENSITIVITY, config.SCROLL_CURVE_EXP, config.SCROLL_MAX_STEP,
                config.SCROLL_DEAD_ZONE + 1.0 / config.SCROLL_SENSITIVITY))
    first_scroll_logged = False
    first_hscroll_logged = False
    ctrl.log("[BOOT] ready. show an open palm for %.2fs to arm. Esc quits." % config.OPEN_PALM_HOLD_S)

    try:
        while True:
            det = hands.latest()

            if det is not None and det.frame.frame_id != last_id:
                last_id = det.frame.frame_id
                now = det.frame.captured_at
                events = engine.update(det.landmarks, now)
                fired = time.perf_counter()
                metrics.tick(fired)

                # Arm hold tracing: every frame the palm is held, plus any reset.
                if engine.dbg_arm is not None:
                    hold, started, cnt, toggled = engine.dbg_arm
                    ctrl.log("[ARM] fingers=%d hold=%.3fs / %.2fs needed%s%s"
                             % (cnt, hold, config.OPEN_PALM_HOLD_S,
                                "   <- HOLD STARTED" if started else "",
                                "   (already toggled, waiting for palm to drop)" if toggled else ""))
                if engine.dbg_arm_reset is not None:
                    acc, why, kind = engine.dbg_arm_reset
                    tag = {"drift": "RESET BY DRIFT", "fingers": "RESET BY FINGER DROP"}.get(kind, "RESET BY HAND LOST")
                    ctrl.log("[ARM] ARM HOLD %s at %.3fs   reason: %s   totals drift=%d finger_drop=%d hand_lost=%d"
                             % (tag, acc, why, engine.arm_resets_drift, engine.arm_resets_fingers, engine.arm_resets_lost))
                if det.landmarks is not None and engine.finger_count >= config.OPEN_PALM_FINGERS - 1:
                    names = ("index", "middle", "ring", "pinky")
                    parts = []
                    for nm, (up, dtip, dpip) in zip(names, gestures.fingers_detail(det.landmarks)):
                        parts.append("%s=%s(%+.4f)" % (nm, "UP" if up else "dn", dtip - dpip))
                    ctrl.log("[ARM] margins %s" % "  ".join(parts))

                # State line on any change, plus a heartbeat once a second.
                el = fired - t_start
                state = (engine.armed, engine.mode, engine.switching, engine.finger_count)
                if state != prev_state or el - last_beat >= 1.0:
                    prev_state = state
                    last_beat = el
                    ctrl.log("[STATE] t=%6.2f armed=%-3s mode=%-7s switching=%-5s hand=%s fingers=%d cand=%s streak=%d tap=%s"
                             % (el, "ON" if engine.armed else "OFF", engine.mode,
                                engine.switching, det.landmarks is not None,
                                engine.finger_count, engine._cand, engine._streak,
                                engine._tap_active))
                    if not engine.armed:
                        ctrl.log("[GATE] armed=OFF -> pointer/page events are never generated, nothing is sent")
                    elif engine.switching:
                        ctrl.log("[GATE] mode switch in progress -> actions suppressed this frame by design")

                # Unconditional per-frame proof of the POINTER path, armed or not.
                if engine.mode == gestures.MODE_POINTER:
                    mv = None
                    for e in events:
                        if e.kind == "move":
                            mv = e
                            break
                    ctrl.log("[PTR] frame=%d armed=%s switching=%s fingers=%d events=%s move_event=%s will_dispatch=%s xy=%s"
                             % (det.frame.frame_id, "ON" if engine.armed else "OFF",
                                engine.switching, engine.finger_count,
                                [e.kind for e in events], mv is not None,
                                bool(mv is not None and engine.armed),
                                ("(%.1f, %.1f)" % mv.value) if mv is not None else "NONE"))
                    if mv is None:
                        ctrl.log("[PTR] NO move event this frame -> _pointer() was not reached")
                    elif not engine.armed:
                        ctrl.log("[PTR] move event exists but armed=OFF -> dispatch skipped")

                for e in events:
                    metrics.add(e, fired)
                    if e.kind == "move" and engine.dbg_map is not None and not engine._tap_active:
                        rx, ry, fx, fy, nx, ny, sx, sy, gx, gy = engine.dbg_map
                        ctrl.log("[MAP] lm5_raw=(%.3f,%.3f) filt=(%.3f,%.3f) active_norm=(%.3f,%.3f) -> mapped=(%d,%d) x%.2f -> screen=(%d,%d) of %dx%d"
                                 % (rx, ry, fx, fy, nx, ny, sx, sy, config.CURSOR_SENSITIVITY, gx, gy, screen_w, screen_h))
                    elif e.kind == "move":
                        ctrl.log("[MAP] tap in progress, cursor frozen at (%d, %d)"
                                 % (e.value[0], e.value[1]))
                    elif e.kind in ("tap", "page_tap"):
                        tc = engine.tap_counts[gestures.MODE_POINTER if e.kind == "tap" else gestures.MODE_PAGE]
                        ctrl.log("[%s] %-8s totals attempts=%d success=%d timeout=%d cancel=%d cooldown=%d"
                                 % ("TAP" if e.kind == "tap" else "PAGE-TAP", e.value, tc.attempts, tc.success,
                                    tc.timeout, tc.cancelled, tc.cooldown))
                    elif e.kind == "tilt":
                        ctrl.log("[TILT] %-8s totals attempts=%d fires=%d released=%d blocked_by_cooldown=%d"
                                 % (e.value, engine.tilt_attempts, engine.tilt_fires,
                                    engine.tilt_released, engine.tilt_blocked))
                    else:
                        ctrl.log("[EVENT] %s value=%r" % (e.kind, e.value))
                    if e.kind == "arm":
                        ctrl.log("[EVENT] ***** %s *****" % ("ARMED" if e.value else "DISARMED"))
                    if engine.armed:
                        if e.kind == "move" and config.CURSOR_INTERP:
                            glide.set_target(e.value[0], e.value[1])
                        else:
                            ctrl.dispatch(out, e)
                    else:
                        ctrl.log("[GATE] %s NOT dispatched, armed=OFF" % e.kind)

                # Scroll direction trace.
                el_s = fired - t_start
                fc = engine.finger_count if det.landmarks is not None else -1
                if engine.dbg_page_reset is not None:
                    why, was_y, axis = engine.dbg_page_reset
                    ctrl.log("[ANCHOR] t=%6.2f RESET was_y=%.4f axis=%s mode=%s fingers=%d  reason: %s"
                             % (el_s, was_y, axis, engine.mode, fc, why))
                if engine.dbg_anchor_set is not None:
                    first_scroll_logged = False
                    first_hscroll_logged = False
                    ay, why = engine.dbg_anchor_set
                    ctrl.log("[ANCHOR] t=%6.2f SET   y=%.4f mode=%s fingers=%d  reason: %s  (anchor x=%.4f)"
                             % (el_s, ay, engine.mode, fc, why, engine._anchor[0]))
                if engine.dbg_scroll is not None:
                    ay, hy, dy, sign, t, shaped, amount, step = engine.dbg_scroll
                    if dy < 0:
                        where = "hand ABOVE anchor"
                    else:
                        where = "hand BELOW anchor"
                    sent = "UP" if step > 0 else ("DOWN" if step < 0 else "none")
                    excess = abs(dy) - config.SCROLL_DEAD_ZONE
                    ctrl.log("[SCROLL] t=%6.2f fingers=%d anchor_y=%.4f hand_y=%.4f dy=%+.4f (%s) dead_zone=%.3f excess=%.4f sign=%+.0f t=%.3f shaped=%.3f amount=%+.3f step=%+d sent=%s"
                             % (el_s, fc, ay, hy, dy, where, config.SCROLL_DEAD_ZONE, excess,
                                sign, t, shaped, amount, step, sent))
                    if step != 0 and not first_scroll_logged:
                        first_scroll_logged = True
                        ctrl.log("[SCROLL] FIRST CLICK this PAGE session: |dy|=%.4f from anchor, dead_zone=%.3f, excess=%.4f, step=%+d"
                                 % (abs(dy), config.SCROLL_DEAD_ZONE, excess, step))
                # Horizontal scroll trace, same fields as the vertical one.
                if engine.dbg_hscroll is not None:
                    ax, hx, hdx, hsign, ht, hshaped, hamount, hstep = engine.dbg_hscroll
                    where = "hand LEFT of anchor" if hdx < 0 else "hand RIGHT of anchor"
                    hsent = "RIGHT" if hstep > 0 else ("LEFT" if hstep < 0 else "none")
                    content = {"RIGHT": " (content moves left)", "LEFT": " (content moves right)"}.get(hsent, "")
                    hexcess = abs(hdx) - config.SCROLL_H_DEAD_ZONE
                    ctrl.log("[HSCROLL] t=%6.2f fingers=%d anchor_x=%.4f hand_x=%.4f dx=%+.4f (%s) dead_zone=%.3f excess=%.4f sign=%+.0f t=%.3f shaped=%.3f amount=%+.3f step=%+d sent=%s%s"
                             % (el_s, fc, ax, hx, hdx, where, config.SCROLL_H_DEAD_ZONE, hexcess,
                                hsign, ht, hshaped, hamount, hstep, hsent, content))
                    if hstep != 0 and not first_hscroll_logged:
                        first_hscroll_logged = True
                        ctrl.log("[HSCROLL] FIRST CLICK this PAGE session: |dx|=%.4f from anchor, dead_zone=%.3f, excess=%.4f, step=%+d"
                                 % (abs(hdx), config.SCROLL_H_DEAD_ZONE, hexcess, hstep))
                # Tilt trace, every PAGE frame the tilt was measured.
                if engine.dbg_tilt is not None:
                    tbase, tang, tdelta, tstate = engine.dbg_tilt
                    ctrl.log("[TILT] t=%6.2f baseline=%+.1f deg current=%+.1f deg delta=%+.1f (%s) watch=%.1f fire=%.1f -> %s"
                             % (el_s, tbase, tang, tdelta, "left" if tdelta < 0 else "right",
                                config.TILT_WATCH_THRESHOLD, config.TILT_ANGLE_THRESHOLD, tstate))
                # Frames where rotation and movement both mattered, and which one won.
                if engine.dbg_precedence is not None:
                    winner, pdelta, pdx, pdy, paxis = engine.dbg_precedence
                    if winner == "TAP":
                        ctrl.log("[PRECEDENCE] t=%6.2f PAGE TAP WON: curl would have been tilt %+.1f deg / dx=%+.4f dy=%+.4f axis=%s, held at pre-tap anchor   total held=%d"
                                 % (el_s, pdelta, pdx, pdy, paxis, engine.page_tap_held_frames))
                    elif winner == "TILT":
                        ctrl.log("[PRECEDENCE] t=%6.2f TILT WON: tilt %+.1f deg >= watch %.1f, scroll suppressed (dx=%+.4f dy=%+.4f axis=%s)   total suppressed=%d"
                                 % (el_s, pdelta, config.TILT_WATCH_THRESHOLD, pdx, pdy, paxis, engine.scroll_suppressed_frames))
                    else:
                        ctrl.log("[PRECEDENCE] t=%6.2f SCROLL WON: tilt %+.1f deg < watch %.1f, movement dx=%+.4f dy=%+.4f axis=%s scrolls   total=%d"
                                 % (el_s, pdelta, config.TILT_WATCH_THRESHOLD, pdx, pdy, paxis, engine.scroll_won_frames))

                # Every PAGE frame, so silence in the log always has a stated reason.
                if engine.mode == gestures.MODE_PAGE:
                    if engine.dbg_page is not None:
                        px, py, pdx, pdy, paxis, decision = engine.dbg_page
                        ctrl.log("[PAGE] t=%6.2f fingers=%d switching=%s axis=%s hand=(%.4f,%.4f) dx=%+.4f dy=%+.4f -> %s"
                                 % (el_s, fc, engine.switching, paxis, px, py, pdx, pdy, decision))
                    else:
                        if det.landmarks is None:
                            why = "hand lost this frame"
                        elif not engine.armed:
                            why = "disarmed"
                        elif engine.switching:
                            why = "mode switch pending (cand=%s streak=%d), scroll paused, anchor kept" % (engine._cand, engine._streak)
                        else:
                            why = "page logic not reached"
                        ctrl.log("[PAGE] t=%6.2f fingers=%d switching=%s -> NO SCROLL: %s"
                                 % (el_s, fc, engine.switching, why))
                    # Who owned this PAGE frame, and for scroll, whether the movement cleared its threshold.
                    if engine.dbg_owner is None:
                        ctrl.log("[PRECEDENCE] t=%6.2f frame owner = NONE (%s)"
                                 % (el_s, why if engine.dbg_page is None else "page logic returned early"))
                    else:
                        owner, odelta = engine.dbg_owner
                        tilt_txt = "n/a" if odelta is None else "%+.1f" % odelta
                        _, _, odx, ody, oaxis, _ = engine.dbg_page
                        if oaxis == "v":
                            move_txt = "dy=%+.4f |dy| %s dead zone %.3f" % (ody, ">" if abs(ody) > config.SCROLL_DEAD_ZONE else "<=", config.SCROLL_DEAD_ZONE)
                        elif oaxis == "h":
                            move_txt = "dx=%+.4f |dx| %s h dead zone %.3f" % (odx, ">" if abs(odx) > config.SCROLL_H_DEAD_ZONE else "<=", config.SCROLL_H_DEAD_ZONE)
                        else:
                            mv = math.hypot(odx, ody)
                            move_txt = "no axis yet, moved %.4f %s lock floor %.3f (dx=%+.4f dy=%+.4f)" % (mv, ">" if mv > config.DIR_LOCK_MIN_MOVE else "<=", config.DIR_LOCK_MIN_MOVE, odx, ody)
                        ctrl.log("[PRECEDENCE] t=%6.2f frame owner = %-6s tilt delta=%s deg (watch %.1f)  axis=%s  %s"
                                 % (el_s, owner, tilt_txt, config.TILT_WATCH_THRESHOLD, oaxis, move_txt))

                # Finger-count excursions while in PAGE, classified.
                if engine.dbg_flicker is not None:
                    kind, dur, nfr, seen, new_mode, reset = engine.dbg_flicker
                    ctrl.log("[FLICKER] t=%6.2f %s  lasted %.0f ms / %d frames  counts seen=%s  mode now=%s  anchor_reset=%s"
                             % (el_s, kind, dur * 1000.0, nfr,
                                ["lost" if c is None else c for c in seen], new_mode,
                                "YES" if reset else "no"))

                if recorder is not None:
                    recorder.add(det.frame.frame_id, now, det.landmarks)

            # Glide the cursor between detections so movement is not stepped.
            if config.CURSOR_INTERP:
                if engine.armed and engine.mode == gestures.MODE_POINTER:
                    p = glide.step(time.perf_counter())
                    if p is not None:
                        out.move(p[0], p[1])
                else:
                    glide.clear()

            now_p = time.perf_counter()
            if now_p - last_perf >= 2.0:
                last_perf = now_p
                ctrl.log("[PERF] render_fps=%.1f camera_grab_fps=%.1f detect_ms=%.1f low_light_ms=%s capture_to_use_ms=%.1f"
                         % (fps, cam.grab_fps, hands.detect_ms_avg,
                            ("%.2f" % hands.low_light_ms_avg) if config.LOW_LIGHT_MODE else "off",
                            (now_p - det.frame.captured_at) * 1000.0 if det is not None else 0.0))

            if det is not None:
                image = det.frame.image.copy()
                tracker.draw_landmarks(image, det.landmarks)
                latency_ms = (time.perf_counter() - det.frame.captured_at) * 1000.0
                t = time.perf_counter()
                dt = t - last_draw
                last_draw = t
                if dt > 0:
                    fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else 1.0 / dt
                draw_hud(image, engine, fps, latency_ms, det.landmarks is not None)
                cv2.imshow(config.WINDOW_NAME, image)

            if args.log and time.perf_counter() - last_log >= config.LOG_INTERVAL_S:
                last_log = time.perf_counter()
                print(gestures.format_report(metrics.report(), prefix="live."))
                print(gestures.format_page_stability(engine, prefix="live."))
                print(gestures.format_arm_hold_stats(engine, prefix="live."))
                print(gestures.format_tilt_stats(engine, prefix="live."), flush=True)
                print("")

            key = cv2.waitKey(1) & 0xFF
            if key == ESC:
                break
            if cv2.getWindowProperty(config.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        pass
    finally:
        hands.stop()
        cam.stop()
        out.release()
        cv2.destroyAllWindows()
        if recorder is not None:
            recorder.close()
            print("recorded %d frames to %s" % (recorder.n, args.record))

    if args.log:
        print("")
        print(gestures.format_report(metrics.report(), prefix="live."))
        print(gestures.format_page_stability(engine, prefix="live."))
        print(gestures.format_arm_hold_stats(engine, prefix="live."))
        print(gestures.format_tilt_stats(engine, prefix="live."), flush=True)


def run_replay(args):
    meta, rows = read_session(args.replay)
    if not rows:
        print("no frames in %s" % args.replay)
        return

    screen = meta.get("screen") or list(ctrl.get_screen_size())
    engine = gestures.GestureEngine(screen[0], screen[1])
    metrics = gestures.Metrics()
    out = ctrl.DryRunController(verbose=args.verbose)

    t0 = time.perf_counter()
    for row in rows:
        lm = row.get("lm")
        t = float(row["t"])
        events = engine.update(lm, t)
        metrics.tick(t)
        for e in events:
            metrics.add(e, t)
            if engine.armed:
                ctrl.dispatch(out, e)
    cpu_ms = (time.perf_counter() - t0) * 1000.0

    counts = {}
    for kind, _ in out.actions:
        counts[kind] = counts.get(kind, 0) + 1

    print("replay.file=%s" % args.replay)
    print("replay.frames=%d" % len(rows))
    print("replay.gesture_cpu_ms_per_frame=%.3f" % (cpu_ms / max(len(rows), 1)))
    for kind in ("move", "click", "scroll", "hscroll", "nav", "key"):
        print("replay.%s=%d" % (kind, counts.get(kind, 0)))
    if args.log:
        print("")
        print(gestures.format_report(metrics.report(), prefix="replay."))
        print(gestures.format_page_stability(engine, prefix="replay."))
        print(gestures.format_arm_hold_stats(engine, prefix="replay."))
        print(gestures.format_tilt_stats(engine, prefix="replay."))


def build_parser():
    p = argparse.ArgumentParser(description="Hand gesture mouse controller")
    p.add_argument("--record", metavar="FILE", help="save hand joint data per frame")
    p.add_argument("--replay", metavar="FILE", help="run gesture code on a saved file, no camera")
    p.add_argument("--log", action="store_true", help="print measurement numbers")
    p.add_argument("--dry-run", action="store_true", help="do not move the real mouse")
    p.add_argument("--verbose", action="store_true", help="print every action")
    p.add_argument("--quiet", action="store_true", help="turn the per-call diagnostic printing off")
    p.add_argument("--low-light", action="store_true", help="contrast-boost frames before detection, for dim rooms")
    return p


def main():
    args = build_parser().parse_args()
    if not args.replay:
        # Live runs need an output backend and a camera backend for this OS; replay needs neither.
        try:
            ctrl.require_supported()
        except ctrl.UnsupportedPlatform as e:
            print("ERROR: %s" % e, file=sys.stderr)
            return 2
    if args.low_light:
        config.LOW_LIGHT_MODE = True
    if args.replay:
        run_replay(args)
    else:
        run_live(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
