#!/usr/bin/env python3
"""Touch Screen - turn a monitor (later: a projected wall) into a touch screen, plus hand-sign slide control.

TOUCH (phone camera): the phone sits at a bottom corner of the screen with its lens 2-3 cm in front of the
glass, looking across the surface. The screen then looks like a thin slanted strip. A fingertip only lands
inside that strip when it touches (or is within ~2 cm of) the glass; a hovering finger appears outside it.
A 9-point touch calibration fits the exact plane mapping (homography) from the phone picture to screen
points, so no monitor model or measurement is needed.

GESTURES (Mac camera, facing you): hold a sign still for 0.6 s
    POINT -> next slide (Right arrow)    PEACE -> previous slide (Left arrow)    OPEN PALM -> blank screen (B)
Gestures are ignored while the phone sees your hand near the screen, so reaching to touch never flips slides.

In LIVE mode touches become real mouse clicks / drags on that display (works with Keynote, PowerPoint,
Google Slides, a browser...) and a quick horizontal flick on the screen changes slides.
TEST mode measures it: 16 touch targets + 6 "hover, don't touch" targets -> hit rate, error in mm, false
touches, phone fps -> logs/touch_<time>_summary.json (+ csv).

Keys (in the fullscreen or status window): SPACE next step · C recalibrate · T test · L live · R rotate phone view
· Q quit.   Live mode needs Accessibility permission for PhoneStereo.app (to post mouse/keyboard events).
"""
import argparse
import collections
import csv
import datetime
import json
import math
import os
import random
import subprocess
import threading
import time

import cv2
import numpy as np
import Quartz as Q

import signs as S
import viewer as V

S.NUM_HANDS = 2
CAL_PATH = os.path.expanduser("~/.config/phonestereo/touch_calib.json")
WIN, STATUS = "Touch Screen", "Touch Screen status"
GAMMA = np.array([255 * (i / 255) ** 0.5 for i in range(256)], np.uint8)
GESTURE_KEYS = {"POINT": (124, "next slide"), "PEACE": (123, "previous slide"), "OPEN PALM": (11, "blank screen")}


def ax_ok():
    """True if this process (PhoneStereo.app) may post mouse/keyboard events (Accessibility)."""
    import ctypes
    try:
        lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except OSError:
        return True


def tink():
    subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- displays + events
def displays():
    err, ids, n = Q.CGGetActiveDisplayList(8, None, None)
    out = []
    for d in ids[:n]:
        b = Q.CGDisplayBounds(d)
        mm = Q.CGDisplayScreenSize(d)
        out.append({"id": int(d), "x": b.origin.x, "y": b.origin.y, "w": b.size.width, "h": b.size.height,
                    "mm_w": mm.width, "builtin": bool(Q.CGDisplayIsBuiltin(d)), "main": bool(Q.CGDisplayIsMain(d))})
    return out


def mouse(kind, x, y):
    t = {"down": Q.kCGEventLeftMouseDown, "up": Q.kCGEventLeftMouseUp,
         "drag": Q.kCGEventLeftMouseDragged, "move": Q.kCGEventMouseMoved}[kind]
    ev = Q.CGEventCreateMouseEvent(None, t, (x, y), Q.kCGMouseButtonLeft)
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def key(code):
    for down in (True, False):
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateKeyboardEvent(None, code, down))


class OneEuro:
    def __init__(self, mincut=1.0, beta=0.004):
        self.mincut, self.beta, self.x, self.dx, self.t = mincut, beta, None, 0.0, None

    def __call__(self, x, t):
        x = np.asarray(x, float)
        if self.x is None:
            self.x, self.t = x, t
            return x
        dt = max(1e-3, t - self.t)
        a = lambda cut: 1 / (1 + 1 / (2 * math.pi * cut * dt))
        dx = (x - self.x) / dt
        self.dx = self.dx + a(1.0) * (dx - self.dx)
        cut = self.mincut + self.beta * float(np.linalg.norm(self.dx))
        self.x = self.x + a(cut) * (x - self.x)
        self.t = t
        return self.x


# ---------------------------------------------------------------- phone: fingertip tracker
class PhoneTip(threading.Thread):
    def __init__(self, log, host, token, turn):
        super().__init__(daemon=True)
        self.rx = V.Receiver(log, host=host, token=token)
        self.turn, self.running, self.error = turn, True, None
        self.lock = threading.Lock()
        self.latest = None            # (t_ready, t_recv, tip_px or None, img)
        self.camera = "?"
        self.n = 0
        self.proc = collections.deque(maxlen=60)

    def run(self):
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            self.error = self.rx.error or "no stream from the phone"
            return
        self.camera = str(self.rx.header.get("logicalId"))
        tr = S.make_tracker()
        last_ts, last_ms = -1, 0
        while self.running:
            with self.rx.lock:
                f = self.rx.frames[0][-1] if self.rx.frames[0] else None
            if f is None or f["ts"] <= last_ts:
                time.sleep(0.002)
                continue
            last_ts = f["ts"]
            img = S.resize_w(f["img"], S.PW)
            if self.turn:
                img = np.ascontiguousarray(np.rot90(img, -self.turn))
            if cv2.mean(img)[0] < 90:
                img = cv2.LUT(img, GAMMA)
            last_ms = max(last_ms + 1, f["ts"] // 1_000_000)
            det = S.detect(tr, img, last_ms)
            h, w = img.shape[:2]
            tip = np.array([det["lms"][8][0] * w, det["lms"][8][1] * h]) if det else None
            now = time.time()
            self.proc.append(now - f["t_recv"])
            self.n += 1
            with self.lock:
                self.latest = (now, f["t_recv"], tip, img)

    def get(self):
        with self.lock:
            return self.latest


# ---------------------------------------------------------------- app
class App:
    def __init__(self, a):
        self.a = a
        ds = displays()
        if a.display is not None:
            self.disp = ds[a.display]
        else:      # default: the biggest non-built-in display, else the main one
            ext = [d for d in ds if not d["builtin"]]
            self.disp = max(ext, key=lambda d: d["w"] * d["h"]) if ext else next(d for d in ds if d["main"])
        self.others = [d for d in ds if d["id"] != self.disp["id"]]
        self.W, self.H = self.disp["w"], self.disp["h"]
        self.pt_per_mm = self.W / self.disp["mm_w"] if self.disp["mm_w"] else 3.8
        self.S = 0.5                                   # canvas = half the display size (scaled up fullscreen)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(V.LOG_DIR, exist_ok=True)
        self.base = os.path.join(V.LOG_DIR, f"touch_{stamp}")
        self.log = V.EventLog(self.base + ".log")
        self.cal = self.load_cal()
        self.Hm = np.array(self.cal["H"]) if self.cal else None
        self.state = "place"
        self.t_state = time.time()
        self.cal_pts, self.cal_i, self.cal_buf, self.wait_release = [], 0, [], False
        # touch state
        self.down, self.in_n, self.out_n = False, 0, 0
        self.pos, self.raw = None, None
        self.euro = OneEuro()
        self.touch_t0, self.touch_start, self.mode_touch = 0, None, None
        self.last_phone_hand = 0
        # gestures
        self.ema, self.sign_since, self.sign_cur, self.cool = None, 0, "?", 0
        self.palm_hist = collections.deque(maxlen=20)
        self.gesture_msg = ("", 0)
        # test
        self.tasks, self.ti, self.trows, self.results = [], -1, [], None
        self.events = []

    # ---- calibration file
    def load_cal(self):
        try:
            c = json.load(open(CAL_PATH))
            if c.get("display") == [self.W, self.H]:
                return c
        except (OSError, ValueError):
            pass
        return None

    def cal_targets(self):
        fx, fy = (0.08, 0.5, 0.92), (0.1, 0.5, 0.9)
        return [(x * self.W, y * self.H) for y in fy for x in fx]

    # ---- per phone frame
    def phone_step(self, now):
        got = self.phone.get()
        if not got or got[0] == getattr(self, "_last_pt", None):
            return False
        self._last_pt = got[0]
        _, _, tip, img = got
        self.phone_img, self.tip = img, tip
        if tip is not None:
            self.last_phone_hand = now
        if self.state == "cal":
            self.cal_step(tip, now)
        elif self.Hm is not None:
            self.touch_step(tip, now)
        return True

    def cal_step(self, tip, now):
        targets = self.cal_targets()
        if self.wait_release:
            if tip is None or np.linalg.norm(tip - self.cal_pts[-1][0]) > 40:
                self.wait_release = False
                self.cal_buf = []
                if self.cal_i >= len(targets):
                    self.finish_cal()
            return
        if tip is None:
            self.cal_buf = []
            return
        self.cal_buf.append(tip)
        self.cal_buf = self.cal_buf[-14:]
        if len(self.cal_buf) >= 12 and np.max(np.std(self.cal_buf, 0)) < 4.0:
            p = np.median(self.cal_buf, 0)
            self.cal_pts.append((p, targets[self.cal_i]))
            self.log(f"cal point {self.cal_i}: phone {p.round(1).tolist()} -> screen {targets[self.cal_i]}")
            tink()
            self.cal_i += 1
            self.wait_release = True

    def finish_cal(self):
        src = np.array([p for p, _ in self.cal_pts], np.float32)
        dst = np.array([s for _, s in self.cal_pts], np.float32)
        Hm, _ = cv2.findHomography(src, dst, 0)
        if Hm is None:
            self.log("calibration failed - the 9 points were in a line. Raise the phone lens 2-3 cm off the glass "
                     "and try again", show=True)
            self.go("place")
            return
        proj = cv2.perspectiveTransform(src[None], Hm)[0]
        err_mm = np.linalg.norm(proj - dst, axis=1) / self.pt_per_mm
        self.Hm = Hm
        q = cv2.perspectiveTransform(np.array([[[0, 0], [self.W, 0], [self.W, self.H], [0, self.H]]], np.float32),
                                     np.linalg.inv(Hm))[0]
        flat = float(4 * np.pi * cv2.contourArea(q) / max(1e-6, cv2.arcLength(q, True) ** 2))
        self.cal = {"H": Hm.tolist(), "flatness": round(flat, 3), "display": [self.W, self.H], "turn": self.phone.turn, "camera": self.phone.camera,
                    "fit_error_mm_median": round(float(np.median(err_mm)), 1), "fit_error_mm_max": round(float(err_mm.max()), 1),
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                    "points": [[p.tolist(), list(s)] for p, s in self.cal_pts]}
        os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
        json.dump(self.cal, open(CAL_PATH, "w"), indent=1)
        if flat > 0.3:
            self.log(f"PHONE SEES THE SCREEN FROM THE FRONT (flatness {flat:.2f}, need < 0.2): hovering will count as "
                     "touching. Move the lens 2-3 cm from the glass, looking ACROSS it, then press C", show=True)
        self.log(f"calibrated: flatness {flat:.2f}, fit error median {self.cal['fit_error_mm_median']} mm, max {self.cal['fit_error_mm_max']} mm",
                 show=True)
        self.go("menu")

    def to_screen(self, tip):
        return cv2.perspectiveTransform(np.array([[tip]], np.float32), self.Hm)[0, 0]

    def touch_step(self, tip, now):
        inside = False
        if tip is not None:
            s = self.to_screen(tip)
            self.raw = s
            m = (0.05 if self.down else 0.02)
            inside = -m * self.W <= s[0] <= (1 + m) * self.W and -m * self.H <= s[1] <= (1 + m) * self.H
        else:
            self.raw = None
        if inside:
            self.in_n, self.out_n = self.in_n + 1, 0
        else:
            self.out_n, self.in_n = self.out_n + 1, 0
        if not self.down and self.in_n >= 2:
            self.down = True
            self.euro = OneEuro()
            self.pos = self.euro(np.clip(self.raw, 0, [self.W, self.H]), now)
            self.touch_t0, self.touch_start, self.mode_touch = now, self.pos.copy(), None
            self.on_down(now)
        elif self.down and inside:
            self.pos = self.euro(np.clip(self.raw, 0, [self.W, self.H]), now)
            self.on_move(now)
        elif self.down and self.out_n >= 3:
            self.down = False
            self.on_up(now)

    # ---- touch -> actions
    def gx(self, p):
        return float(self.disp["x"] + p[0]), float(self.disp["y"] + p[1])

    def on_down(self, now):
        self.events.append(("down", now, self.pos.copy()))
        self.log(f"touch down at {self.pos.round(0).tolist()}")
        if self.state == "test":
            self.test_touch(now)

    def on_move(self, now):
        if self.state != "live":
            return
        dx = self.pos[0] - self.touch_start[0]
        if self.mode_touch is None:
            if abs(dx) > 0.08 * self.W and now - self.touch_t0 < 0.3:
                self.mode_touch = "swipe"
            elif now - self.touch_t0 >= 0.3 or np.linalg.norm(self.pos - self.touch_start) > 0.03 * self.W:
                self.mode_touch = "press"
                mouse("move", *self.gx(self.touch_start))
                mouse("down", *self.gx(self.touch_start))
        if self.mode_touch == "press":
            mouse("drag", *self.gx(self.pos))

    def on_up(self, now):
        self.events.append(("up", now, None if self.pos is None else self.pos.copy()))
        self.log("touch up")
        if self.state != "live":
            return
        dx = self.pos[0] - self.touch_start[0]
        if self.mode_touch == "swipe" or (self.mode_touch is None and abs(dx) > 0.08 * self.W):
            key(123 if dx > 0 else 124)       # flick right = back (like paging), left = next
            self.gesture_msg = (f"flick {'->' if dx > 0 else '<-'} : {'previous' if dx > 0 else 'next'} slide", now)
        elif self.mode_touch is None:          # short tap
            mouse("move", *self.gx(self.touch_start))
            mouse("down", *self.gx(self.touch_start))
            mouse("up", *self.gx(self.touch_start))
        else:
            mouse("up", *self.gx(self.pos))

    # ---- Mac camera gestures
    def mac_step(self, now):
        got = self.mac.get()
        if not got or got[0] == getattr(self, "_last_mt", None):
            return
        self._last_mt = got[0]
        _, det, frame = got
        self.mac_frame = frame
        if not det:
            self.ema, self.sign_cur = None, "?"
            return
        p = np.array(det["probs"])
        self.ema = p if self.ema is None else 0.5 * p + 0.5 * self.ema
        sign, conf = S.sign_from(self.ema)
        palm = np.array(det["lms"][9])
        self.palm_hist.append((now, palm))
        if sign != self.sign_cur:
            self.sign_cur, self.sign_since = sign, now
        still = len(self.palm_hist) > 5 and np.ptp([h[1] for h in self.palm_hist if now - h[0] < 0.6], 0).max() < 0.04
        busy = self.down or now - self.last_phone_hand < 0.8
        if (sign in GESTURE_KEYS and now - self.sign_since > 0.6 and still and not busy and now > self.cool):
            code, what = GESTURE_KEYS[sign]
            self.cool = now + 1.5
            self.sign_since = now + 99            # one action per hold
            self.events.append(("gesture", now, sign))
            self.gesture_msg = (f"{sign}: {what}", now)
            self.log(f"gesture {sign} -> {what}")
            if self.state == "live":
                key(code)

    # ---- test
    def go(self, st):
        self.state, self.t_state = st, time.time()
        if st == "cal":
            self.cal_pts, self.cal_i, self.cal_buf, self.wait_release = [], 0, [], False
        if st == "test":
            rnd = random.Random()
            ts = [("TOUCH", (rnd.uniform(0.08, 0.92) * self.W, rnd.uniform(0.1, 0.9) * self.H)) for _ in range(16)]
            ts += [("HOVER", (rnd.uniform(0.15, 0.85) * self.W, rnd.uniform(0.2, 0.8) * self.H)) for _ in range(6)]
            rnd.shuffle(ts)
            self.tasks, self.ti, self.task_t = ts, 0, time.time()
            self.task_downs = collections.defaultdict(list)
            self.task_done_at = None
        self.log(f"-> {st}")

    def test_touch(self, now):
        if not (0 <= self.ti < len(self.tasks)):
            return
        self.task_downs[self.ti].append((now - self.task_t, self.pos.copy()))
        kind, _ = self.tasks[self.ti]
        if kind == "TOUCH" and self.task_done_at is None:
            self.task_done_at = now

    def test_step(self, now):
        if not (0 <= self.ti < len(self.tasks)):
            return
        kind, _ = self.tasks[self.ti]
        limit = 5.0 if kind == "TOUCH" else 3.5
        if (self.task_done_at and now - self.task_done_at > 0.7 and not self.down) or now - self.task_t > limit:
            self.ti += 1
            self.task_t, self.task_done_at = now, None
            if self.ti >= len(self.tasks):
                self.results = self.analyse()
                self.go("menu")

    def analyse(self):
        r = 45.0                                     # target radius, points
        rows, hits, errs, times, extra, false = [], 0, [], [], 0, 0
        for i, (kind, tgt) in enumerate(self.tasks):
            downs = self.task_downs.get(i, [])
            first = downs[0] if downs else None
            e = None if not first else float(np.linalg.norm(first[1] - np.array(tgt))) / self.pt_per_mm
            if kind == "TOUCH":
                if first:
                    errs.append(e)
                    times.append(first[0])
                    hits += e <= r / self.pt_per_mm
                extra += max(0, len(downs) - 1)
            else:
                false += len(downs)
            rows.append({"task": i, "kind": kind, "target_x": round(tgt[0]), "target_y": round(tgt[1]),
                         "touches": len(downs), "first_t_s": "" if not first else round(first[0], 3),
                         "first_x": "" if not first else round(float(first[1][0])),
                         "first_y": "" if not first else round(float(first[1][1])), "error_mm": "" if e is None else round(e, 1)})
        nt = sum(1 for k, _ in self.tasks if k == "TOUCH")
        nh = len(self.tasks) - nt
        proc = list(self.phone.proc)
        out = {"display": {"w_pt": self.W, "h_pt": self.H, "width_mm": round(self.disp["mm_w"])},
               "target_radius_mm": round(r / self.pt_per_mm, 1), "calibration": {k: self.cal.get(k) for k in
                                                                                  ("flatness", "fit_error_mm_median", "fit_error_mm_max", "camera", "turn")},
               "longest_touch_s": round(max([b[1] - a[1] for a, b in zip(self.events, self.events[1:])
                                            if a[0] == "down" and b[0] == "up"] or [0]), 1),
               "touch_tasks": nt, "hover_tasks": nh,
               "hit_pct": round(100 * hits / max(1, nt), 1),
               "touch_detected_pct": round(100 * len(errs) / max(1, nt), 1),
               "error_mm_median": round(float(np.median(errs)), 1) if errs else None,
               "error_mm_p90": round(float(np.percentile(errs, 90)), 1) if errs else None,
               "time_to_touch_s_median": round(float(np.median(times)), 2) if times else None,
               "extra_touches": extra, "false_touches_while_hovering": false,
               "phone_fps": round(self.phone.rx.fps(0), 1),
               "phone_processing_ms_median": round(1000 * float(np.median(proc))) if proc else None,
               "gestures_seen": sum(1 for e in self.events if e[0] == "gesture")}
        with open(self.base + "_summary.json", "w") as f:
            json.dump(out, f, indent=2)
        with open(self.base + ".csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "touch", "time": os.path.basename(self.base), **out}) + "\n")
        self.log("SUMMARY " + json.dumps(out), show=True)
        return out

    # ---- drawing
    def canvas(self):
        s = self.S
        img = np.full((int(self.H * s), int(self.W * s), 3), (32, 16, 11), np.uint8)
        put = lambda t, x, y, sc=1.0, c=(235, 235, 235), th=2: cv2.putText(
            img, t, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        now = time.time()
        if self.state == "place":
            put("TOUCH SCREEN - place the phone", 40, 70, 1.3, (0, 220, 255), 3)
            for i, t in enumerate([
                    "1. Put the phone at a BOTTOM CORNER of this screen, lens 2-3 cm in front of the glass,",
                    "   looking ACROSS the screen (almost along the glass), NOT at it from the front.",
                    "   The whole screen should show as a thin slanted strip in the phone view.",
                    "2. Touch the screen: the dot on your fingertip (right) should be green.",
                    "3. Keep the phone still from now on.   SPACE = calibrate" +
                    ("   ·   L = live / T = test with the saved calibration" if self.Hm is not None else ""),
                    "R = rotate the phone view   Q = quit"]):
                put(t, 40, 130 + i * 40, 0.7, (220, 220, 220), 1)
            self.draw_phone(img, (int(img.shape[1] * 0.45), 360), int(img.shape[1] * 0.5))
        elif self.state == "cal":
            tg = self.cal_targets()
            for i, (x, y) in enumerate(tg):
                c = (60, 220, 60) if i < self.cal_i else (0, 220, 255) if i == self.cal_i else (90, 90, 90)
                cv2.circle(img, (int(x * s), int(y * s)), 22 if i == self.cal_i else 10, c, -1 if i < self.cal_i else 3, cv2.LINE_AA)
                if i == self.cal_i:
                    cv2.circle(img, (int(x * s), int(y * s)), 4, c, -1)
            msg = ("lift your finger off the screen" if self.wait_release else
                   f"touch the yellow dot with your fingertip and hold still  ({self.cal_i + 1}/9)")
            put(msg, 40, 60, 0.8, (0, 220, 255), 2)
            self.draw_phone(img, (40, int(img.shape[0] - 230)), 280)
        elif self.state == "menu":
            fl = (self.cal or {}).get("flatness")
            good = fl is not None and fl < 0.2
            ok = fl is not None and fl <= 0.3
            put("Calibrated." if good else "Calibrated, but placement is weak" if ok else
                "Placement problem: the phone sees the screen from the front", 40, 70, 1.1,
                (80, 220, 80) if good else (0, 180, 255) if ok else (60, 60, 230), 3)
            if fl is not None:
                put(f"flatness {fl:.2f} (need < 0.20: the screen should look like a thin strip to the phone)",
                    40, 255, 0.65, (200, 200, 200), 1)
            if self.cal:
                put(f"fit error {self.cal['fit_error_mm_median']} mm median, {self.cal['fit_error_mm_max']} mm worst",
                    40, 115, 0.7, (200, 200, 200), 1)
            put("T = accuracy test   L = live (control this screen)   C = recalibrate   Q = quit", 40, 170, 0.8)
            put("Try touching: a dot follows your finger while you touch.", 40, 215, 0.65, (200, 200, 200), 1)
            if self.results:
                r = self.results
                put(f"TEST: hits {r['hit_pct']:.0f}%  (touch found {r['touch_detected_pct']:.0f}%)   error {r['error_mm_median']} mm"
                    f" median / {r['error_mm_p90']} mm p90   false touches while hovering {r['false_touches_while_hovering']}/{r['hover_tasks']}"
                    f"   extra {r['extra_touches']}", 40, 280, 0.62, (80, 220, 80), 2)
            self.draw_phone(img, (40, int(img.shape[0] - 230)), 280)
        elif self.state == "test":
            if 0 <= self.ti < len(self.tasks):
                kind, (x, y) = self.tasks[self.ti]
                col = (80, 220, 80) if kind == "TOUCH" else (0, 160, 255)
                cv2.circle(img, (int(x * s), int(y * s)), int(45 * s), col, -1 if kind == "TOUCH" else 4, cv2.LINE_AA)
                put(f"{self.ti + 1}/{len(self.tasks)}  " + ("TOUCH the green circle" if kind == "TOUCH" else
                    "HOVER over the ring, 2-5 cm from the glass - do NOT touch"), 40, 60, 0.85, col, 2)
                lim = 5.0 if kind == "TOUCH" else 3.5
                cv2.rectangle(img, (0, 80), (int(img.shape[1] * min(1, (now - self.task_t) / lim)), 86), col, -1)
        # touch dot
        if self.down and self.pos is not None and self.state in ("menu", "test", "place"):
            cv2.circle(img, (int(self.pos[0] * s), int(self.pos[1] * s)), 14, (255, 255, 255), -1, cv2.LINE_AA)
        msg, mt = self.log.last
        if msg and now - mt < 6:
            put(msg[:140], 40, img.shape[0] - 30, 0.6, (0, 180, 255), 1)
        return img

    def draw_phone(self, img, at, width):
        pi = getattr(self, "phone_img", None)
        if pi is None:
            cv2.putText(img, "waiting for the phone..." + (f" ({self.phone.error})" if self.phone.error else ""),
                        at, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 180, 255), 2)
            return
        h = int(pi.shape[0] * width / pi.shape[1])
        x, y = at
        h = min(h, img.shape[0] - y - 10)
        w = int(pi.shape[1] * h / pi.shape[0])
        v = cv2.resize(pi, (w, h))
        k = w / pi.shape[1]
        if self.cal and self.Hm is not None:     # outline of the screen as the phone sees it
            inv = np.linalg.inv(self.Hm)
            c = np.array([[[0, 0], [self.W, 0], [self.W, self.H], [0, self.H]]], np.float32)
            q = cv2.perspectiveTransform(c, inv)[0] * k
            cv2.polylines(v, [q.astype(np.int32)], True, (255, 200, 0), 2)
        if getattr(self, "tip", None) is not None:
            cv2.circle(v, tuple((self.tip * k).astype(int)), 7, (60, 220, 60) if self.down else (60, 60, 230), -1)
        img[y:y + h, x:x + w] = v
        cv2.rectangle(img, (x, y), (x + w, y + h), (120, 120, 120), 1)

    def status(self):
        img = np.full((300, 520, 3), (32, 16, 11), np.uint8)
        put = lambda t, x, y, sc=0.55, c=(230, 230, 230), th=1: cv2.putText(
            img, t, (x, y), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        put("LIVE - touch the screen (phone camera)", 12, 24, 0.55, (80, 220, 80), 1)
        put("tap = click   drag = drag   quick flick = next / previous slide", 12, 48, 0.45, (200, 200, 200), 1)
        put(f"touch: {'DOWN' if self.down else 'up'}" + (f"   Mac sign: {self.sign_cur}" if self.mac else ""), 12, 74)
        m, t = self.gesture_msg
        if m and time.time() - t < 3:
            put(m, 12, 100, 0.65, (0, 220, 255), 2)
        put("Q quit  M menu/test", 12, 288, 0.45, (150, 150, 150), 1)
        pi = getattr(self, "phone_img", None)
        if pi is not None:
            h = 170
            w = int(pi.shape[1] * h / pi.shape[0])
            w = min(w, 250)
            img[110:110 + h, 12:12 + w] = cv2.resize(pi, (w, h))
        mf = getattr(self, "mac_frame", None)
        if mf is not None:
            img[110:110 + 140, 270:270 + 240] = cv2.resize(cv2.flip(mf, 1), (240, 140))
        return img

    # ---- main loop
    def run(self):
        a = self.a
        self.mac = None
        if a.with_mac:                     # hand-sign slide control from the Mac camera (off for now)
            cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
            for _ in range(20):
                if cap.isOpened():
                    break
                time.sleep(0.5)
                cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            self.mac = S.MacCam(cap)
            self.mac.start()
        turn = (self.cal or {}).get("turn", a.turn)
        self.phone = PhoneTip(self.log, a.host, a.token, turn)
        self.phone.start()
        self.log(f"touch display {self.disp['w']:.0f}x{self.disp['h']:.0f} pt, {self.disp['mm_w']:.0f} mm wide"
                 + (", saved calibration found" if self.cal else ""))
        win_open = False

        def open_full():
            cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
            cv2.moveWindow(WIN, int(self.disp["x"]) + 40, int(self.disp["y"]) + 40)
            cv2.resizeWindow(WIN, int(self.W * 0.6), int(self.H * 0.6))
            cv2.waitKey(50)
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        def open_status():
            cv2.namedWindow(STATUS, cv2.WINDOW_AUTOSIZE)
            o = self.others[0] if self.others else self.disp
            cv2.moveWindow(STATUS, int(o["x"] + o["w"] - 540), int(o["y"] + 40))

        open_full()
        win_open = True
        import traceback
        try:
            while True:
                now = time.time()
                self.phone_step(now)
                if self.mac:
                    self.mac_step(now)
                if self.state == "test":
                    self.test_step(now)
                if self.state == "live":
                    if win_open:
                        cv2.destroyWindow(WIN)
                        win_open = False
                        open_status()
                    cv2.imshow(STATUS, self.status())
                else:
                    if not win_open:
                        cv2.destroyWindow(STATUS)
                        open_full()
                        win_open = True
                    cv2.imshow(WIN, self.canvas())
                k = cv2.waitKey(8) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("r"):
                    self.phone.turn = (self.phone.turn + 1) % 4
                    if self.cal:
                        self.log("phone view rotated - calibrate again (C)", show=True)
                        self.cal, self.Hm = None, None
                if k == ord(" ") and self.state == "place":
                    self.go("cal")
                if k == ord("c"):
                    self.go("cal")
                if k == ord("t") and self.Hm is not None and self.state in ("menu", "place"):
                    self.go("test")
                if k == ord("l") and self.Hm is not None and self.state in ("menu", "place"):
                    if not ax_ok():
                        self.log("Live needs Accessibility: System Settings > Privacy & Security > Accessibility > "
                                 "PhoneStereo ON", show=True)
                    self.go("live")
                if k == ord("m") and self.state == "live":
                    self.go("menu")
        except Exception:
            self.log("CRASH " + traceback.format_exc().replace("\n", " | "), show=True)
            raise
        finally:
            if self.mac:
                self.mac.running = False
            self.phone.running = False
            self.phone.rx.running = False
            if self.down and self.state == "live":
                mouse("up", *self.gx(self.pos))
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token")
    ap.add_argument("--display", type=int, help="index into the display list (default: biggest external)")
    ap.add_argument("--mac-cam", type=int, default=0)
    ap.add_argument("--with-mac", action="store_true", help="also use the Mac camera for hand-sign slide control")
    ap.add_argument("--turn", type=int, default=0, help="phone view quarter turns (R key changes it)")
    a = ap.parse_args()
    App(a).run()


if __name__ == "__main__":
    main()
