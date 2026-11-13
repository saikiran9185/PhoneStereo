#!/usr/bin/env python3
"""Air Buttons - press on-screen buttons by pushing your hand toward the screen.

Cursor: your index fingertip, seen by the Mac camera (mirrored, like a mirror).
Depth ("how far you pushed"), two signals:
  * Mac camera hand scale: how big the hand looks. Uses the LARGEST of several hand bones relative to
    their size at rest, so tilting the hand (which shrinks some bones in the picture) doesn't look
    like a push - only moving closer makes every bone bigger.
  * Phone stereo (if the phone is connected): how far the hand shifts between the main and the
    ultra-wide lens.
Both are turned into "push progress" (0 = rest, 1 = pressed) by a two-step calibration (rest, press),
then blended. Press at 0.7, release below 0.35; the cursor freezes while you push so the
push itself doesn't drag it off the button.

Steps: CALIBRATE (rest, then press) -> PLAY freely -> CHECK (12 timed tasks: push a named button or
just hover without pushing) -> results.  Keys: SPACE skip/start · C recalibrate · K start check · Q quit.
Logs: logs/air_<time>.csv + _summary.json (hits, false presses, time to press, and which depth signal
would have done best on the same recording; link fps/lag/MB/s for Wi-Fi vs USB).
"""
import argparse
import collections
import csv
import datetime
import json
import os
import random
import subprocess
import sys
import threading
import time

import cv2
import numpy as np

import signs as S
S.NUM_HANDS = 2
import viewer as V

WIN = "Air Buttons"
CW, CH = 1100, 820
BUTTONS = ["BACK", "NEXT", "PLAY", "BLACKOUT"]
BONES = [(0, 5), (0, 9), (0, 13), (0, 17), (5, 17), (5, 9), (9, 13)]
PRESS, RELEASE = 0.7, 0.35


def bone_lengths(lms, w, h):
    p = np.array([(u * w, v * h) for u, v in lms])
    return np.array([np.linalg.norm(p[a] - p[b]) for a, b in BONES])


class PhoneDepth(threading.Thread):
    """Phone main + ultra-wide: stereo shift of the hand (median over landmarks), at phone rate."""

    def __init__(self, log, host, token):
        super().__init__(daemon=True)
        self.rx = V.Receiver(log, host=host, token=token)
        self.lock = threading.Lock()
        self.latest = None        # (t, shift_vec or None, frame)
        self.error = None
        self.running = True
        self.pairs = 0
        self.t0 = time.time()
        self.views = None

    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            self.error = f"phone: {self.rx.error or 'no stream'}"
            return
        h = self.rx.header
        W, H = h["width"], h["height"]
        H0 = V.factory_homography(h["cameras"], W, H)
        tm, tu = S.make_tracker(), S.make_tracker()
        pool = ThreadPoolExecutor(2)
        last0, lm, lu = -1, 0, 0
        while self.running:
            if self.rx.error:
                self.error = f"phone: {self.rx.error}"
                return
            with self.rx.lock:
                mains, uws = list(self.rx.frames[0]), list(self.rx.frames[1])
            if not mains or not uws or mains[-1]["ts"] <= last0:
                time.sleep(0.003)
                continue
            f0 = mains[-1]
            f1 = min(uws, key=lambda f: abs(f["ts"] - f0["ts"]))
            last0 = f0["ts"]
            a, b = S.resize_w(f0["img"], S.PW), S.resize_w(f1["img"], S.PW)
            lm, lu = max(lm + 1, f0["ts"] // 1_000_000), max(lu + 1, f1["ts"] // 1_000_000)
            ja, jb = pool.submit(S.detect, tm, a, lm), pool.submit(S.detect, tu, b, lu)
            dm, du = ja.result(), jb.result()
            shift = None
            if dm and du:
                pm = np.array([(u * W, v * H) for u, v in dm["lms"]])
                pu = np.array([(u * W, v * H) for u, v in du["lms"]])
                q = np.c_[pu, np.ones(len(pu))] @ H0.T
                shift = np.median(pm - q[:, :2] / q[:, 2:], axis=0)
            self.pairs += 1
            with self.lock:
                self.latest = (time.time(), shift, a, f0["t_recv"])
                self.views = (a, b, dm is not None, du is not None)

    def get(self):
        with self.lock:
            return self.latest

    def link(self):
        el = max(1e-3, time.time() - self.t0)
        return {"phone_cam_fps": round(self.rx.fps(0), 1), "processed_pairs_per_s": round(self.pairs / el, 1),
                "mbps": round(self.rx.mbps(), 2)}


class Air:
    def __init__(self, a):
        self.a = a
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(V.LOG_DIR, exist_ok=True)
        self.base = os.path.join(V.LOG_DIR, f"air_{stamp}")
        self.log = V.EventLog(self.base + ".log")
        self.state, self.t_state = "cal_rest", time.time()
        self.cal = {"rest": [], "press": []}
        self.base_bones = None
        self.mac_press = None             # scale ratio at the press pose
        self.st_rest = self.st_axis = None
        self.st_span = None
        self.cursor = np.array([CW / 2, CH / 2])
        self.frozen = None
        self.pressed = False
        self.flash = {}
        self.counts = collections.Counter()
        self.rows = []
        self.tasks, self.ti = [], -1
        self.results = None
        self.phone = None
        self.stereo_age = None
        self.prog = self.pm = self.ps = self.target = None
        self.det = self.frame = None
        self.hist = collections.deque()     # (t, mac_ratio, stereo_proj) for the self-correcting rest point

    # ---- setup
    def start(self):
        a = self.a
        cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
        for _ in range(20):
            if cap.isOpened():
                break
            time.sleep(0.5)
            cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            raise RuntimeError("Mac camera not allowed - System Settings > Privacy & Security > Camera")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.mac = S.MacCam(cap)
        self.mac.start()
        if a.host:
            self.phone = PhoneDepth(self.log, a.host, a.token)
            self.phone.start()
        self.link_kind = "none" if not a.host else ("usb" if a.host.startswith("127.") else "wifi")
        self.t0 = time.time()
        self.mac.ready.wait(10)
        self.go("cal_rest")               # start the calibration clock once the camera is running
        self.log(f"start - phone link: {self.link_kind}")

    # ---- buttons layout
    def rects(self):
        bw, bh, gap = 330, 190, 60
        x0, y0 = (CW - 2 * bw - gap) // 2, 150
        return {name: (x0 + (i % 2) * (bw + gap), y0 + (i // 2) * (bh + gap), bw, bh) for i, name in enumerate(BUTTONS)}

    def under(self, pt):
        for name, (x, y, w, h) in self.rects().items():
            if x <= pt[0] <= x + w and y <= pt[1] <= y + h:
                return name
        return None

    # ---- depth signals
    def signals(self, det, frame):
        """-> (mac_ratio, stereo_shift_vec or None) for this moment."""
        h, w = frame.shape[:2]
        bones = bone_lengths(det["lms"], w, h)
        mac_ratio = None
        if self.base_bones is not None:
            mac_ratio = float(np.max(bones / self.base_bones))
        shift = None
        if self.phone:
            got = self.phone.get()
            if got and time.time() - got[0] < 0.25:
                shift = got[1]
                self.stereo_age = time.time() - got[3]
        return bones, mac_ratio, shift

    def progress(self, mac_ratio, shift):
        """Push progress 0 (rest) .. 1 (press). The rest point is NOT fixed at calibration: it is the
        20th percentile of the last 6 s, so a bad calibration or a slow drift (leaning back, moving the
        chair) corrects itself; pushes are short, so the low end of the window is the real rest."""
        now = time.time()
        proj = None
        if shift is not None and self.st_axis is not None:
            proj = float((shift - self.st_rest) @ self.st_axis)
        if mac_ratio is not None:
            self.hist.append((now, mac_ratio, proj))
        while self.hist and now - self.hist[0][0] > 6.0:
            self.hist.popleft()
        pm = ps = None
        if mac_ratio is not None and self.mac_press:
            rest = np.percentile([h[1] for h in self.hist], 20) if len(self.hist) > 20 else 1.0
            pm = (mac_ratio / rest - 1.0) / max(1e-3, self.mac_press - 1.0)
        if proj is not None:
            ph = [h[2] for h in self.hist if h[2] is not None]
            rest = np.percentile(ph, 20) if len(ph) > 10 else 0.0
            ps = (proj - rest) / max(1e-3, self.st_span)
        if pm is None and ps is None:
            return None, pm, ps
        if ps is None:
            return pm, pm, ps
        if pm is None:
            return ps, pm, ps
        if self.st_span < 8.0:            # 1 cm phone baseline: below ~8 px of push it is mostly noise
            return pm, pm, ps                 # (3 Nov: stereo alone gave 12 false presses vs 3 for Mac scale)
        return 0.65 * pm + 0.35 * ps, pm, ps

    # ---- calibration
    def calibrate_step(self, det, bones, shift, now):
        el = now - self.t_state
        key = "rest" if self.state == "cal_rest" else "press"
        if el > 1.2:                      # 1.2 s to get into position, then 1.5 s of samples
            self.cal[key].append((bones, shift))
        if el > 2.7 and len(self.cal[key]) >= 8:
            if key == "rest":
                bs = np.array([b for b, _ in self.cal["rest"]])
                if np.median(np.std(bs, 0) / np.maximum(np.median(bs, 0), 1e-3)) > 0.12:
                    self.log("hand moved during REST - hold still", show=True)
                    self.cal["rest"] = []
                    self.t_state = now
                    return
                self.base_bones = np.median(bs, 0)
                self.go("cal_press")
            else:
                ratios = [np.max(b / self.base_bones) for b, _ in self.cal["press"]]
                self.mac_press = float(np.median(ratios))
                rs = [s for _, s in self.cal["rest"] if s is not None]
                ps = [s for _, s in self.cal["press"] if s is not None]
                if len(rs) >= 4 and len(ps) >= 4:
                    r0, p0 = np.median(rs, 0), np.median(ps, 0)
                    d = p0 - r0
                    span = float(np.linalg.norm(d))
                    if span > 1.0:
                        self.st_rest, self.st_axis, self.st_span = r0, d / span, span
                if self.phone and self.st_span is None:
                    self.log("phone could not see the hand in BOTH lenses during calibration - using the Mac "
                             "camera only (press C to retry with the hand inside both phone views)", show=True)
                self.log(f"calibrated: mac press ratio {self.mac_press:.2f}, stereo span "
                         f"{self.st_span if self.st_span else 'n/a'} px")
                if not 1.08 <= self.mac_press <= 3.0:
                    self.log(f"press size {self.mac_press:.2f}x looks wrong (want 1.1-3x) - calibrating again",
                             show=True)
                    self.cal = {"rest": [], "press": []}
                    self.go("cal_rest")
                else:
                    self.go("play")

    def go(self, state):
        self.state, self.t_state = state, time.time()
        if state == "check":
            names = BUTTONS * 2
            random.shuffle(names)
            hov = random.sample(BUTTONS, 4)
            self.tasks = [("PUSH", n) for n in names] + [("HOVER", n) for n in hov]
            random.shuffle(self.tasks)
            self.ti = 0
            self.task_t = time.time()
            self.task_events = []
        self.log(f"-> {state}")

    # ---- per Mac frame
    def update(self):
        got = self.mac.get()
        if not got:
            return False
        t, det, frame = got
        if getattr(self, "last_t", None) == t:
            return False
        self.last_t = t
        self.frame = frame
        self.det = det
        now = time.time()
        prog = pm = ps = None
        target = None
        if det:
            bones, mac_ratio, shift = self.signals(det, frame)
            if self.state in ("cal_rest", "cal_press"):
                self.calibrate_step(det, bones, shift, now)
            else:
                prog, pm, ps = self.progress(mac_ratio, shift)
            tip = np.array([(1 - det["lms"][8][0]) * CW, det["lms"][8][1] * CH])   # mirrored
            if prog is not None and prog > 0.3 and self.frozen is None:
                self.frozen = self.cursor.copy()
            if prog is not None and prog < 0.2:
                self.frozen = None
            if self.frozen is None:
                self.cursor = 0.5 * self.cursor + 0.5 * tip
            target = self.under(self.frozen if self.frozen is not None else self.cursor)
            if prog is not None:
                if not self.pressed and prog > PRESS and target:
                    self.pressed = True
                    self.counts[target] += 1
                    self.flash[target] = now
                    self.event("press", target, now)
                    subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif self.pressed and prog < RELEASE:
                    self.pressed = False
        else:
            self.frozen = None
            self.pressed = False
        self.prog, self.pm, self.ps, self.target = prog, pm, ps, target
        task = self.tasks[self.ti] if self.state == "check" and 0 <= self.ti < len(self.tasks) else None
        self.rows.append({"t": round(now - self.t0, 3), "state": self.state,
                          "task": f"{task[0]} {task[1]}" if task else "", "task_i": self.ti if task else "",
                          "hand": int(det is not None), "target": target or "",
                          "progress": "" if prog is None else round(prog, 3),
                          "mac_prog": "" if pm is None else round(pm, 3),
                          "stereo_prog": "" if ps is None else round(ps, 3), "pressed": int(self.pressed)})
        if self.state == "check":
            self.check_step(now)
        return True

    def event(self, kind, button, now):
        self.log(f"{kind} {button}")
        if self.state == "check":
            self.task_events.append((self.ti, button, now - self.task_t))

    def check_step(self, now):
        if now - self.task_t > 4.0:
            self.ti += 1
            self.task_t = now
            if self.ti >= len(self.tasks):
                self.results = self.analyse()
                self.go("done")

    # ---- results
    def analyse(self):
        out = {"link": self.link_kind, "tasks": len(self.tasks)}
        hits = wrong = false_hover = 0
        times = []
        for i, (kind, name) in enumerate(self.tasks):
            ev = [e for e in self.task_events if e[0] == i]
            if kind == "PUSH":
                good = [e for e in ev if e[1] == name]
                if good:
                    hits += 1
                    times.append(good[0][2])
                wrong += sum(1 for e in ev if e[1] != name)
            else:
                false_hover += len(ev)
        npush = sum(1 for k, _ in self.tasks if k == "PUSH")
        nhov = len(self.tasks) - npush
        out["live"] = {"push_hit_pct": round(100 * hits / max(1, npush), 0), "wrong_button_presses": wrong,
                       "false_presses_while_hovering": false_hover, "hover_tasks": nhov,
                       "median_time_to_press_s": round(float(np.median(times)), 2) if times else None}
        # Replay the same recording with each depth signal alone, same thresholds.
        chk = [r for r in self.rows if r["state"] == "check" and r["task_i"] != ""]
        for col in ("mac_prog", "stereo_prog", "progress"):
            if not any(r[col] != "" for r in chk):
                continue
            press = False
            got = collections.defaultdict(list)
            for r in chk:
                v = r[col]
                if v == "":
                    continue
                if not press and v > PRESS and r["target"]:
                    press = True
                    got[r["task_i"]].append(r["target"])
                elif press and v < RELEASE:
                    press = False
            h = sum(1 for i, (k, n) in enumerate(self.tasks) if k == "PUSH" and n in got.get(i, []))
            fh = sum(len(got.get(i, [])) for i, (k, _) in enumerate(self.tasks) if k == "HOVER")
            out.setdefault("replay_by_signal", {})[col.replace("_prog", "").replace("progress", "fused")] = {
                "push_hit_pct": round(100 * h / max(1, npush), 0), "false_presses_while_hovering": fh}
        out["calibration"] = {"mac_press_ratio": round(self.mac_press, 3) if self.mac_press else None,
                              "stereo_span_px": round(self.st_span, 2) if self.st_span else None}
        out["fps"] = {"mac": round(self.mac.fps, 1)}
        if self.phone:
            out["fps"].update(self.phone.link())
            out["fps"]["stereo_frame_age_ms"] = int(1000 * self.stereo_age) if self.stereo_age else None
        with open(self.base + "_summary.json", "w") as f:
            json.dump(out, f, indent=2)
        with open(self.base + ".csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            w.writeheader()
            w.writerows(self.rows)
        self.log("SUMMARY " + json.dumps(out))
        return out

    # ---- drawing
    def draw(self):
        img = np.full((CH, CW, 3), (32, 16, 11), np.uint8)
        put = lambda t, x, y, sc=0.7, c=(235, 235, 235), th=2: cv2.putText(
            img, t, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        now = time.time()
        el = now - self.t_state
        if self.state == "cal_rest":
            put("CALIBRATE 1/2: hand at a comfortable distance, open, facing the screen", 30, 50, 0.75, (0, 220, 255))
            put("hold still..." if el > 1.2 else "get ready", 30, 90)
        elif self.state == "cal_press":
            put("CALIBRATE 2/2: push the hand toward the screen, as far as a 'press', and hold", 30, 50, 0.75, (0, 220, 255))
            put("hold still..." if el > 1.2 else "push now", 30, 90)
        elif self.state == "play":
            put("PLAY: point at a button, push to press.   K = start check   C = recalibrate", 30, 50, 0.7, (0, 220, 255))
        elif self.state == "check":
            k, n = self.tasks[self.ti]
            msg = f"PUSH  {n}" if k == "PUSH" else f"HOVER over {n} - don't push"
            put(f"CHECK {self.ti + 1}/{len(self.tasks)}:  {msg}", 30, 60, 1.0,
                (80, 220, 80) if k == "PUSH" else (0, 180, 255), 3)
            cv2.rectangle(img, (0, 120), (int(CW * min(1, (now - self.task_t) / 4.0)), 126), (0, 220, 255), -1)
        elif self.state == "done":
            put("CHECK DONE - results saved.  Q to quit", 30, 50, 0.8, (80, 220, 80))
        if self.state in ("play", "check", "done"):
            for name, (x, y, w, h) in self.rects().items():
                hot = name == self.target
                fl = now - self.flash.get(name, 0) < 0.35
                col = (60, 200, 60) if fl else (90, 70, 40) if hot else (55, 40, 25)
                cv2.rectangle(img, (x, y), (x + w, y + h), col, -1)
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 220, 255) if hot else (90, 90, 90), 3 if hot else 1)
                put(name, x + 24, y + 60, 1.1, (255, 255, 255), 3)
                put(f"pressed {self.counts[name]}", x + 24, y + h - 24, 0.6, (180, 180, 180), 1)
        # cursor + depth ring
        if getattr(self, "det", None):
            c = self.frozen if self.frozen is not None else self.cursor
            p = self.prog if self.prog is not None else 0
            r = int(34 - 22 * min(1, max(0, p)))
            cv2.circle(img, tuple(int(v) for v in c), 34, (120, 120, 120), 2, cv2.LINE_AA)
            cv2.circle(img, tuple(int(v) for v in c), max(4, r), (0, 220, 255) if not self.pressed else (60, 220, 60),
                       -1 if self.pressed else 3, cv2.LINE_AA)
        # depth bar
        bx, by, bh = CW - 60, 150, 440
        cv2.rectangle(img, (bx, by), (bx + 24, by + bh), (70, 70, 70), 1)
        for lvl, col in ((PRESS, (60, 220, 60)), (RELEASE, (0, 180, 255))):
            yy = int(by + bh - lvl * bh / 1.4)
            cv2.line(img, (bx - 6, yy), (bx + 30, yy), col, 1)
        if getattr(self, "prog", None) is not None:
            hh = int(min(1.4, max(0, self.prog)) * bh / 1.4)
            cv2.rectangle(img, (bx + 2, by + bh - hh), (bx + 22, by + bh), (0, 220, 255), -1)
        put("push", bx - 8, by + bh + 26, 0.5, (180, 180, 180), 1)
        # bottom status
        pm = "--" if getattr(self, "pm", None) is None else f"{self.pm:.2f}"
        ps = "--" if getattr(self, "ps", None) is None else f"{self.ps:.2f}"
        link = self.phone.link() if self.phone else {}
        put(f"depth  Mac scale {pm}   phone stereo {ps}   ->  {'' if self.prog is None else f'{self.prog:.2f}'}",
            30, CH - 50, 0.6, (200, 200, 200), 1)
        put(f"link {self.link_kind}  mac {self.mac.fps:.0f} fps  phone {link.get('phone_cam_fps', '-')} fps "
            f"{link.get('mbps', '-')} MB/s" + (f"  ({self.phone.error})" if self.phone and self.phone.error else ""),
            30, CH - 20, 0.55, (150, 150, 150), 1)
        if self.state == "done" and self.results:
            lv = self.results["live"]
            put(f"push hits {lv['push_hit_pct']:.0f}%   wrong button {lv['wrong_button_presses']}   "
                f"false presses while hovering {lv['false_presses_while_hovering']}/{lv['hover_tasks']}   "
                f"time {lv['median_time_to_press_s']} s", 30, 100, 0.6, (80, 220, 80), 1)
            rp = self.results.get("replay_by_signal", {})
            put("same recording, each depth signal alone: " + "   ".join(
                f"{k}: {v['push_hit_pct']:.0f}% hits / {v['false_presses_while_hovering']} false" for k, v in rp.items()),
                30, 128, 0.5, (200, 200, 200), 1)
        # camera thumbnails: Mac + both phone lenses, green border = hand seen
        if getattr(self, "frame", None) is not None:
            th = cv2.resize(cv2.flip(self.frame, 1), (192, 108))
            img[CH - 190:CH - 82, 30:222] = th
            cv2.rectangle(img, (30, CH - 190), (222, CH - 82), (60, 220, 60) if self.det else (60, 60, 230), 2)
            put("Mac", 34, CH - 194, 0.45, (200, 200, 200), 1)
        if self.phone and self.phone.views:
            a, b, sa, sb = self.phone.views
            for i, (im, seen, lab) in enumerate(((a, sa, "phone main"), (b, sb, "phone wide"))):
                x = 240 + i * 210
                t2 = cv2.flip(cv2.resize(im, (192, 144))[18:126], 1)    # mirrored like the Mac view
                img[CH - 190:CH - 82, x:x + 192] = t2
                cv2.rectangle(img, (x, CH - 190), (x + 192, CH - 82), (60, 220, 60) if seen else (60, 60, 230), 2)
                put(lab, x + 4, CH - 194, 0.45, (200, 200, 200), 1)
        msg, mt = self.log.last
        if msg and time.time() - mt < 8:
            put(msg[:110], 30, 128 if self.state != "done" else 150, 0.55, (0, 180, 255), 1)
        return img

    def run(self):
        self.start()
        cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
        done_at = None
        try:
            while True:
                if self.update():
                    cv2.imshow(WIN, self.draw())
                k = cv2.waitKey(5) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("c"):
                    self.cal = {"rest": [], "press": []}
                    self.go("cal_rest")
                if k == ord("k") and self.state == "play":
                    self.go("check")
                if self.state == "play" and self.a.auto_check and time.time() - self.t_state > self.a.auto_check:
                    self.go("check")
                if self.state == "done":
                    done_at = done_at or time.time()
                    if time.time() - done_at > self.a.linger:
                        break
        finally:
            if self.state != "done" and self.state == "check" and self.ti > 2:
                self.results = self.analyse()
            self.mac.running = False
            if self.phone:
                self.phone.running = False
                self.phone.rx.running = False
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="phone (PhoneStereo app): Wi-Fi IP, or 127.0.0.1 over USB")
    ap.add_argument("--token", help="pairing code")
    ap.add_argument("--mac-cam", type=int, default=0)
    ap.add_argument("--auto-check", type=float, default=40, help="start the check after N s of play (0 = only K)")
    ap.add_argument("--linger", type=float, default=30)
    a = ap.parse_args()
    try:
        Air(a).run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
