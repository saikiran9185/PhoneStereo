#!/usr/bin/env python3
"""Touch 3D - touch screen from the phone's two back lenses (main + ultra-wide), phone placed low in front of
the screen like a projector, looking at the screen and the person.

1. SCREEN CALIBRATION (no touching): the screen shows ArUco markers. Both lenses find them, which gives
   H_screen  main-lens pixel -> screen point, and
   H_plane   ultra-wide pixel -> main-lens pixel for anything lying ON the screen surface.
2. FINGER DEPTH (plane + parallax): the ultra-wide picture is warped onto the main one with H_plane, so the
   screen surface lines up exactly. A small patch of the finger (just behind the tip) from the main lens is
   searched for in the warped ultra-wide picture (normalised cross-correlation, sub-pixel). A finger ON the
   glass lines up (shift ~ 0); a finger in the air shifts along the lens baseline.
3. DEPTH CALIBRATION: touch the centre dot, then hold the fingertip ~3 cm in front of it. The shift between
   the two sets the direction and scale: depth score v = 0 at touch, 1 at 3 cm. Touch when v < 0.35.
4. HAND RECOGNITION from the low angle: MediaPipe runs on both lenses; if the main lens loses the hand, the
   ultra-wide one stands in (its tip is mapped into the main picture). Dark frames are brightened.

TEST: 16 touch + 6 hover targets -> hit rate, error mm, false touches, detection rate per lens, and how well
the depth score separates touching from hovering (also replayed WITHOUT stereo, to show what it adds).
LIVE: touches become mouse clicks / drags; a quick flick changes slides.
Keys: SPACE next step · C recalibrate · T test · L live · R rotate phone view · Q quit.
Logs: logs/touch3d_<time>_summary.json, _frames.csv.
"""
import argparse
import collections
import csv
import datetime
import json
import os
import random
import threading
import time
import traceback

import cv2
import numpy as np

import signs as S
import viewer as V
from touchscreen import OneEuro, ax_ok, displays, key, mouse, tink

S.NUM_HANDS = 2
WIN, STATUS = "Touch 3D", "Touch 3D status"
CAL_PATH = os.path.expanduser("~/.config/phonestereo/touch3d_calib.json")
GAMMA = np.array([255 * (i / 255) ** 0.5 for i in range(256)], np.uint8)
ARUCO = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
MCOLS, MROWS = 5, 3
DOWN_V, UP_V = 0.35, 0.6


def parallax(ga, gb, Hp, lm):
    """Shift (px, in the main picture) of the finger patch between main (ga) and the plane-aligned
    ultra-wide (gb warped by Hp). -> (shift[2], score) or None."""
    tip, dip = np.array(lm[8]), np.array(lm[7])
    L = float(np.linalg.norm(tip - dip))
    if L < 5:
        return None
    c = 0.6 * tip + 0.4 * dip
    h = int(max(6, min(22, 0.5 * L)))
    R = 16
    cx, cy = int(round(c[0])), int(round(c[1]))
    H, W = ga.shape
    if cx - h - R < 0 or cy - h - R < 0 or cx + h + R >= W or cy + h + R >= H:
        return None
    tpl = ga[cy - h:cy + h + 1, cx - h:cx + h + 1]
    if tpl.std() < 4:
        return None
    x0, y0 = cx - h - R, cy - h - R
    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], float) @ Hp
    n = 2 * (h + R) + 1
    reg = cv2.warpPerspective(gb, T, (n, n), flags=cv2.INTER_LINEAR)
    res = cv2.matchTemplate(reg, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, (lx, ly) = cv2.minMaxLoc(res)

    def sub(v_m, v0, v_p):
        d = v_m - 2 * v0 + v_p
        return 0.0 if abs(d) < 1e-9 else 0.5 * (v_m - v_p) / d

    dx = sub(res[ly, lx - 1], res[ly, lx], res[ly, lx + 1]) if 0 < lx < res.shape[1] - 1 else 0
    dy = sub(res[ly - 1, lx], res[ly, lx], res[ly + 1, lx]) if 0 < ly < res.shape[0] - 1 else 0
    return np.array([lx + dx - R, ly + dy - R]), float(mx)


class Pair(threading.Thread):
    """Synced main + ultra-wide frames -> hand landmarks in both, fingertip parallax."""

    def __init__(self, log, host, token, turn):
        super().__init__(daemon=True)
        self.rx = V.Receiver(log, host=host, token=token)
        self.turn, self.running, self.error = turn, True, None
        self.lock = threading.Lock()
        self.latest = None
        self.Hp = None
        self.n = 0
        self.proc = collections.deque(maxlen=90)

    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            self.error = self.rx.error or "no stream"
            return
        if len(self.rx.header["cameras"]) < 2:
            self.error = "phone streams ONE camera - set Camera: BACK pair in the phone app"
            return
        trs = [S.make_tracker(), S.make_tracker()]
        pool = ThreadPoolExecutor(2)
        last, ms = -1, [0, 0]
        while self.running:
            with self.rx.lock:
                mains, uws = list(self.rx.frames[0]), list(self.rx.frames[1])
            pair = None
            for m in reversed(mains):
                if m["ts"] <= last or not uws:
                    break
                u = min(uws, key=lambda f: abs(f["ts"] - m["ts"]))
                if abs(u["ts"] - m["ts"]) < 3_000_000:
                    pair = (m, u)
                    break
            if pair is None:
                time.sleep(0.002)
                continue
            f0, f1 = pair
            last = f0["ts"]
            imgs = []
            for f in (f0, f1):
                im = f["img"]
                if self.turn:
                    im = np.ascontiguousarray(np.rot90(im, -self.turn))
                if cv2.mean(im)[0] < 90:
                    im = cv2.LUT(im, GAMMA)
                imgs.append(im)

            def det(k):
                small = S.resize_w(imgs[k], S.PW)
                ms[k] = max(ms[k] + 1, f0["ts"] // 1_000_000)
                return S.detect(trs[k], small, ms[k])

            da, db = [j.result() for j in [pool.submit(det, 0), pool.submit(det, 1)]]
            out = {"t": time.time(), "t_recv": f0["t_recv"], "a": imgs[0], "b": imgs[1],
                   "lm_a": None, "lm_b": None, "src": None, "shift": None, "score": None}
            for key_, d, im in (("lm_a", da, imgs[0]), ("lm_b", db, imgs[1])):
                if d:
                    h, w = im.shape[:2]
                    out[key_] = np.array(d["lms"]) * [w, h]
            Hp = self.Hp
            if out["lm_a"] is not None:
                out["src"] = "main"
            elif out["lm_b"] is not None and Hp is not None:      # main lost the hand: use the ultra-wide
                out["lm_a"] = cv2.perspectiveTransform(out["lm_b"][None].astype(np.float32), Hp)[0]
                out["src"] = "wide"
            if out["src"] and Hp is not None:
                ga = cv2.cvtColor(imgs[0], cv2.COLOR_BGR2GRAY)
                gb = cv2.cvtColor(imgs[1], cv2.COLOR_BGR2GRAY)
                r = parallax(ga, gb, Hp, out["lm_a"])
                if r:
                    out["shift"], out["score"] = r
            self.proc.append(time.time() - f0["t_recv"])
            self.n += 1
            with self.lock:
                self.latest = out

    def get(self):
        with self.lock:
            return self.latest


class App:
    def __init__(self, a):
        self.a = a
        ds = displays()
        ext = [d for d in ds if not d["builtin"]]
        self.disp = ds[a.display] if a.display is not None else (
            max(ext, key=lambda d: d["w"] * d["h"]) if ext else next(d for d in ds if d["main"]))
        self.others = [d for d in ds if d["id"] != self.disp["id"]]
        self.W, self.H = self.disp["w"], self.disp["h"]
        self.ppmm = self.W / self.disp["mm_w"] if self.disp["mm_w"] else 3.8
        self.S = 0.5
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(V.LOG_DIR, exist_ok=True)
        self.base = os.path.join(V.LOG_DIR, f"touch3d_{stamp}")
        self.log = V.EventLog(self.base + ".log")
        self.cal = None
        self.state, self.t_state = "place", time.time()
        self.buf = []
        self.down, self.v_ema, self.pos, self.euro = False, None, None, OneEuro()
        self.touch_t0 = self.touch_start = self.mode_touch = None
        self.frames, self.events = [], []
        self.tasks, self.ti, self.results = [], -1, None
        self.cur = None

    # ---- screen markers
    def marker_layout(self):
        """-> {id: 4x2 screen corners}, marker size in points."""
        size = min(self.W / (MCOLS * 1.6), self.H / (MROWS * 1.6))
        out = {}
        for r in range(MROWS):
            for c in range(MCOLS):
                cx = (c + 0.5) * self.W / MCOLS
                cy = (r + 0.5) * self.H / MROWS
                h = size / 2
                out[r * MCOLS + c] = np.array([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]])
        return out, size

    def draw_markers(self, img):
        img[:] = 255
        lay, size = self.marker_layout()
        s = self.S
        for i, q in lay.items():
            m = cv2.aruco.generateImageMarker(ARUCO, i, int(size * s))
            x, y = int(q[0][0] * s), int(q[0][1] * s)
            img[y:y + m.shape[0], x:x + m.shape[1]] = m[..., None]

    def find_markers(self, im):
        det = cv2.aruco.ArucoDetector(ARUCO, cv2.aruco.DetectorParameters())
        corners, ids, _ = det.detectMarkers(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
        if ids is None:
            return {}
        return {int(i): c[0] for i, c in zip(ids.ravel(), corners)}

    def pattern_step(self, fr):
        ma, mb = self.find_markers(fr["a"]), self.find_markers(fr["b"])
        self.pattern_seen = (len(ma), len(mb))
        lay, _ = self.marker_layout()
        both = [i for i in ma if i in mb and i in lay]
        if len(both) < 4:
            return
        A = np.concatenate([ma[i] for i in both]).astype(np.float32)
        B = np.concatenate([mb[i] for i in both]).astype(np.float32)
        Sc = np.concatenate([lay[i] for i in both]).astype(np.float32)
        Hs, _ = cv2.findHomography(A, Sc, cv2.RANSAC, 3.0)
        Hp, _ = cv2.findHomography(B, A, cv2.RANSAC, 1.5)
        if Hs is None or Hp is None:
            return
        res_p = np.linalg.norm(cv2.perspectiveTransform(B[None], Hp)[0] - A, axis=1)
        res_s = np.linalg.norm(cv2.perspectiveTransform(A[None], Hs)[0] - Sc, axis=1) / self.ppmm
        self.cal = {"Hs": Hs.tolist(), "Hp": Hp.tolist(), "markers_used": len(both),
                    "plane_align_px_median": round(float(np.median(res_p)), 2),
                    "screen_fit_mm_median": round(float(np.median(res_s)), 2), "turn": self.pair.turn,
                    "display": [self.W, self.H]}
        self.pair.Hp = Hp
        self.Hs = Hs
        self.log(f"screen found by both lenses: {len(both)} markers, lenses aligned to {self.cal['plane_align_px_median']} px,"
                 f" screen fit {self.cal['screen_fit_mm_median']} mm", show=True)
        tink()
        self.go("touchcal")

    # ---- depth calibration (touch the dot, then hover 3 cm in front)
    def depthcal_step(self, fr, now):
        if fr["shift"] is None or fr["score"] < 0.5:
            self.buf = []
            return
        tip = fr["lm_a"][8]
        self.buf.append((tip, fr["shift"]))
        self.buf = self.buf[-14:]
        tips = np.array([b[0] for b in self.buf])
        if len(self.buf) >= 12 and np.max(np.std(tips, 0)) < 3.5:
            m = np.median([b[1] for b in self.buf], 0)
            if self.state == "touchcal":
                self.cal["touch_shift"] = m.tolist()
                self.log(f"touch shift {m.round(2).tolist()} px", show=True)
                tink()
                self.buf = []
                self.go("hovercal")
            elif now - self.t_state > 1.5:
                t0 = np.array(self.cal["touch_shift"])
                d = m - t0
                mag = float(np.linalg.norm(d))
                self.cal["hover_shift"] = m.tolist()
                self.cal["depth_px_per_3cm"] = round(mag, 2)
                self.cal["dir"] = (d / max(mag, 1e-6)).tolist()
                noise = float(np.std([(b[1] - t0) @ (d / max(mag, 1e-6)) for b in self.buf]))
                self.cal["depth_noise_px"] = round(noise, 3)
                os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
                json.dump(self.cal, open(CAL_PATH, "w"), indent=1)
                self.log(f"depth: 3 cm = {mag:.2f} px shift, noise {noise:.2f} px -> "
                         + ("good" if mag > 6 * noise and mag > 0.6 else "WEAK: move the phone closer to the screen"),
                         show=True)
                tink()
                self.go("menu")

    # ---- touch from depth
    def depth_v(self, fr):
        if fr["shift"] is None or not self.cal or "dir" not in self.cal or fr["score"] < 0.5:
            return None
        d = np.array(self.cal["dir"])
        return float((fr["shift"] - np.array(self.cal["touch_shift"])) @ d / max(0.2, self.cal["depth_px_per_3cm"]))

    def to_screen(self, p):
        return cv2.perspectiveTransform(np.array([[p]], np.float32), self.Hs)[0, 0]

    def touch_step(self, fr, now):
        v = self.depth_v(fr)
        s = self.to_screen(fr["lm_a"][8]) if fr["src"] else None
        inside = s is not None and -0.03 * self.W <= s[0] <= 1.03 * self.W and -0.03 * self.H <= s[1] <= 1.03 * self.H
        if v is not None:
            self.v_ema = v if self.v_ema is None else 0.6 * v + 0.4 * self.v_ema
        elif not fr["src"]:
            self.v_ema = None
        touching_now = inside and self.v_ema is not None and self.v_ema < (UP_V if self.down else DOWN_V)
        self.cur = {"v": v, "v_ema": self.v_ema, "screen": s, "inside": inside}
        if touching_now and not self.down:
            self.down = True
            self.euro = OneEuro()
            self.pos = self.euro(np.clip(s, 0, [self.W, self.H]), now)
            self.touch_t0, self.touch_start, self.mode_touch = now, self.pos.copy(), None
            self.on_down(now)
        elif touching_now and self.down:
            self.pos = self.euro(np.clip(s, 0, [self.W, self.H]), now)
            self.on_move(now)
        elif self.down and not touching_now:
            self.down = False
            self.on_up(now)
        if self.state == "test" and 0 <= self.ti < len(self.tasks):
            kind, tgt = self.tasks[self.ti]
            self.frames.append({"t": round(now - self.t0, 3), "task": self.ti, "kind": kind,
                                "tx": round(tgt[0]), "ty": round(tgt[1]),
                                "main_hand": int(fr["src"] == "main"), "wide_hand": int(fr["lm_b"] is not None),
                                "src": fr["src"] or "", "sx": "" if s is None else round(float(s[0]), 1),
                                "sy": "" if s is None else round(float(s[1]), 1), "inside": int(inside),
                                "v": "" if v is None else round(v, 3),
                                "score": "" if fr["score"] is None else round(fr["score"], 3), "down": int(self.down)})

    def gx(self, p):
        return float(self.disp["x"] + p[0]), float(self.disp["y"] + p[1])

    def on_down(self, now):
        self.events.append(("down", now))
        self.log(f"touch down at {self.pos.round(0).tolist()}")
        if self.state == "test" and 0 <= self.ti < len(self.tasks):
            self.task_downs[self.ti].append((now - self.task_t, self.pos.copy()))
            if self.tasks[self.ti][0] == "TOUCH" and self.task_done_at is None:
                self.task_done_at = now

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
        self.events.append(("up", now))
        self.log("touch up")
        if self.state != "live":
            return
        dx = self.pos[0] - self.touch_start[0]
        if self.mode_touch == "swipe" or (self.mode_touch is None and abs(dx) > 0.08 * self.W):
            key(123 if dx > 0 else 124)
        elif self.mode_touch is None:
            for k in ("move", "down", "up"):
                mouse(k, *self.gx(self.touch_start))
        else:
            mouse("up", *self.gx(self.pos))

    # ---- test
    def go(self, st):
        self.state, self.t_state = st, time.time()
        self.buf = []
        if st == "test":
            rnd = random.Random()
            ts = [("TOUCH", (rnd.uniform(0.08, 0.92) * self.W, rnd.uniform(0.1, 0.9) * self.H)) for _ in range(16)]
            ts += [("HOVER", (rnd.uniform(0.15, 0.85) * self.W, rnd.uniform(0.2, 0.8) * self.H)) for _ in range(6)]
            rnd.shuffle(ts)
            self.tasks, self.ti, self.task_t, self.task_done_at = ts, 0, time.time(), None
            self.task_downs = collections.defaultdict(list)
            self.frames, self.t0 = [], time.time()
        self.log(f"-> {st}")

    def test_step(self, now):
        if not (0 <= self.ti < len(self.tasks)):
            return
        kind, _ = self.tasks[self.ti]
        lim = 5.0 if kind == "TOUCH" else 3.5
        if (self.task_done_at and now - self.task_done_at > 0.7 and not self.down) or now - self.task_t > lim:
            self.ti += 1
            self.task_t, self.task_done_at = now, None
            if self.ti >= len(self.tasks):
                self.results = self.analyse()
                self.go("menu")

    def analyse(self):
        r_mm = 45 / self.ppmm
        hits, errs, false, extra = 0, [], 0, 0
        for i, (kind, tgt) in enumerate(self.tasks):
            dn = self.task_downs.get(i, [])
            if kind == "TOUCH":
                if dn:
                    e = float(np.linalg.norm(dn[0][1] - np.array(tgt))) / self.ppmm
                    errs.append(e)
                    hits += e <= r_mm
                extra += max(0, len(dn) - 1)
            else:
                false += len(dn)
        fr = self.frames
        nt = sum(1 for k, _ in self.tasks if k == "TOUCH")
        nh = len(self.tasks) - nt

        def near(f, mm):
            return f["sx"] != "" and np.hypot(f["sx"] - f["tx"], f["sy"] - f["ty"]) / self.ppmm < mm

        # labelled frames for separability: TOUCH tasks with the tip on target and still (touching);
        # HOVER tasks with the tip over the ring (hovering)
        tv = [f["v"] for f in fr if f["kind"] == "TOUCH" and f["v"] != "" and near(f, 15)]
        hv = [f["v"] for f in fr if f["kind"] == "HOVER" and f["v"] != "" and near(f, 40)]
        auc = None
        if tv and hv:
            ta, ha = np.array(tv), np.array(hv)
            auc = round(float(np.mean(ta[:, None] < ha[None, :]) + 0.5 * np.mean(ta[:, None] == ha[None, :])), 3)
        hover_inside = [f["inside"] for f in fr if f["kind"] == "HOVER" and f["sx"] != "" and near(f, 40)]
        seen = [f for f in fr]
        out = {"placement": "front-low (projector style), stereo main + ultra-wide",
               "calibration": {k: self.cal.get(k) for k in ("markers_used", "plane_align_px_median", "screen_fit_mm_median",
                                                              "depth_px_per_3cm", "depth_noise_px")},
               "touch_tasks": nt, "hover_tasks": nh, "target_radius_mm": round(r_mm, 1),
               "hit_pct": round(100 * hits / max(1, nt), 1), "touch_detected_pct": round(100 * len(errs) / max(1, nt), 1),
               "error_mm_median": round(float(np.median(errs)), 1) if errs else None,
               "error_mm_p90": round(float(np.percentile(errs, 90)), 1) if errs else None,
               "false_touches_while_hovering": false, "extra_touches": extra,
               "depth_score": {"touching_median": round(float(np.median(tv)), 2) if tv else None,
                               "hovering_median": round(float(np.median(hv)), 2) if hv else None,
                               "touch_vs_hover_AUC": auc, "frames": [len(tv), len(hv)]},
               "without_stereo_hover_counts_as_touch_pct": round(100 * float(np.mean(hover_inside)), 1) if hover_inside else None,
               "hand_found_pct": {"main": round(100 * np.mean([f["main_hand"] for f in seen]), 1) if seen else None,
                                  "wide": round(100 * np.mean([f["wide_hand"] for f in seen]), 1) if seen else None,
                                  "either": round(100 * np.mean([f["src"] != "" for f in seen]), 1) if seen else None},
               "phone_fps": round(self.pair.rx.fps(0), 1),
               "processing_ms_median": round(1000 * float(np.median(self.pair.proc))) if self.pair.proc else None}
        json.dump(out, open(self.base + "_summary.json", "w"), indent=2)
        if fr:
            with open(self.base + "_frames.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(fr[0].keys()))
                w.writeheader()
                w.writerows(fr)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "touch3d", "time": os.path.basename(self.base), **out}) + "\n")
        self.log("SUMMARY " + json.dumps(out), show=True)
        return out

    # ---- drawing
    def canvas(self, fr):
        s = self.S
        img = np.full((int(self.H * s), int(self.W * s), 3), (32, 16, 11), np.uint8)
        put = lambda t, x, y, sc=0.8, c=(235, 235, 235), th=2: cv2.putText(
            img, t, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        now = time.time()
        if self.state == "pattern":
            self.draw_markers(img)
            sa, sb = getattr(self, "pattern_seen", (0, 0))
            put(f"finding the screen... main lens sees {sa}/15, ultra-wide {sb}/15 markers", 30, img.shape[0] - 20, 0.7,
                (0, 0, 200), 2)
            return img
        cx, cy = self.W / 2 * s, self.H / 2 * s
        if self.state == "place":
            put("TOUCH 3D - place the phone", 40, 60, 1.2, (0, 220, 255), 3)
            for i, t in enumerate(["Phone LOW in front of the screen (like a projector), back cameras facing the screen.",
                                   "Both views below must show the WHOLE screen. Hand visible in both = green border.",
                                   "SPACE = find the screen (markers)    R = rotate view    Q = quit"]):
                put(t, 40, 110 + i * 36, 0.65, (220, 220, 220), 1)
        elif self.state in ("touchcal", "hovercal"):
            cv2.circle(img, (int(cx), int(cy)), 26, (0, 220, 255), -1 if self.state == "touchcal" else 4, cv2.LINE_AA)
            put("TOUCH the dot and hold still" if self.state == "touchcal" else
                "now hold the fingertip ~3 cm IN FRONT of the dot (not touching), still", 40, 60, 0.9, (0, 220, 255), 2)
        elif self.state == "menu":
            c = self.cal or {}
            put("Ready." if c.get("dir") else "", 40, 60, 1.1, (80, 220, 80), 3)
            put(f"screen fit {c.get('screen_fit_mm_median')} mm · lenses aligned {c.get('plane_align_px_median')} px · "
                f"3 cm = {c.get('depth_px_per_3cm')} px shift (noise {c.get('depth_noise_px')} px)", 40, 100, 0.6, (200, 200, 200), 1)
            put("T = test   L = live   C = recalibrate   Q = quit     (touch anywhere: a dot follows a touch)", 40, 140, 0.7)
            if self.results:
                r = self.results
                ds = r["depth_score"]
                put(f"TEST: hits {r['hit_pct']:.0f}% (found {r['touch_detected_pct']:.0f}%)  error {r['error_mm_median']} mm  "
                    f"false touches {r['false_touches_while_hovering']}/{r['hover_tasks']}  depth AUC {ds['touch_vs_hover_AUC']}",
                    40, 190, 0.62, (80, 220, 80), 2)
                hf = r["hand_found_pct"]
                put(f"hand found: main {hf['main']}%  wide {hf['wide']}%  either {hf['either']}%   "
                    f"without stereo a hover counts as touch {r['without_stereo_hover_counts_as_touch_pct']}%",
                    40, 222, 0.55, (200, 200, 200), 1)
        elif self.state == "test" and 0 <= self.ti < len(self.tasks):
            kind, (x, y) = self.tasks[self.ti]
            col = (80, 220, 80) if kind == "TOUCH" else (0, 160, 255)
            cv2.circle(img, (int(x * s), int(y * s)), int(45 * s), col, -1 if kind == "TOUCH" else 4, cv2.LINE_AA)
            put(f"{self.ti + 1}/{len(self.tasks)}  " + ("TOUCH the green circle" if kind == "TOUCH" else
                "HOVER over the ring 2-5 cm from the glass - do NOT touch"), 40, 50, 0.8, col, 2)
            lim = 5.0 if kind == "TOUCH" else 3.5
            cv2.rectangle(img, (0, 66), (int(img.shape[1] * min(1, (now - self.task_t) / lim)), 72), col, -1)
        # depth meter + touch dot
        if self.cur and self.cur.get("v_ema") is not None:
            v = float(np.clip(self.cur["v_ema"], -0.5, 1.5))
            x0, y0, hh = img.shape[1] - 50, 120, 300
            cv2.rectangle(img, (x0, y0), (x0 + 20, y0 + hh), (90, 90, 90), 1)
            yy = int(y0 + hh * (1.5 - v) / 2.0)
            cv2.rectangle(img, (x0, yy), (x0 + 20, y0 + hh), (60, 220, 60) if self.down else (0, 220, 255), -1)
            for lv in (DOWN_V, UP_V):
                ly = int(y0 + hh * (1.5 - lv) / 2.0)
                cv2.line(img, (x0 - 6, ly), (x0 + 26, ly), (200, 200, 200), 1)
            put("in air", x0 - 30, y0 - 10, 0.45, (180, 180, 180), 1)
            put("touch", x0 - 20, y0 + hh + 20, 0.45, (180, 180, 180), 1)
        if self.down and self.pos is not None:
            cv2.circle(img, (int(self.pos[0] * s), int(self.pos[1] * s)), 14, (255, 255, 255), -1, cv2.LINE_AA)
        if fr is not None and self.state != "test":
            for k, (key_, lab) in enumerate((("a", "main"), ("b", "ultra-wide"))):
                im = fr[key_]
                h = 200 if self.state != "place" else 300
                w = int(im.shape[1] * h / im.shape[0])
                v = cv2.resize(im, (w, h))
                lm = fr["lm_a"] if key_ == "a" else fr["lm_b"]
                if lm is not None:
                    q = (lm[8] * w / im.shape[1]).astype(int)
                    cv2.circle(v, tuple(q), 6, (60, 220, 60), -1)
                x, y = 40 + k * (w + 20), img.shape[0] - h - 50
                if x + w < img.shape[1]:
                    img[y:y + h, x:x + w] = v
                    cv2.rectangle(img, (x, y), (x + w, y + h), (60, 220, 60) if lm is not None else (60, 60, 230), 2)
                    put(lab, x, y - 8, 0.5, (200, 200, 200), 1)
        msg, mt = self.log.last
        if msg and now - mt < 7:
            put(msg[:150], 40, img.shape[0] - 18, 0.55, (0, 180, 255), 1)
        if self.pair.error:
            put("phone: " + self.pair.error, 40, img.shape[0] - 18, 0.6, (60, 60, 230), 2)
        return img

    def status(self, fr):
        img = np.full((220, 460, 3), (32, 16, 11), np.uint8)
        cv2.putText(img, "LIVE touch - M menu, Q quit", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 220, 80), 1)
        v = None if not self.cur else self.cur.get("v_ema")
        cv2.putText(img, f"touch {'DOWN' if self.down else 'up'}   depth {'-' if v is None else f'{v:.2f}'}",
                    (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
        if fr is not None:
            img[70:210, 12:12 + 186] = cv2.resize(fr["a"], (186, 140))
        return img

    def run(self):
        self.pair = Pair(self.log, self.a.host, self.a.token, self.a.turn)
        self.pair.start()
        self.log(f"touch display {self.W:.0f}x{self.H:.0f} pt, {self.disp['mm_w']:.0f} mm wide")

        def open_full():
            cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
            cv2.moveWindow(WIN, int(self.disp["x"]) + 40, int(self.disp["y"]) + 40)
            cv2.resizeWindow(WIN, int(self.W * 0.6), int(self.H * 0.6))
            cv2.waitKey(50)
            cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        open_full()
        full = True
        last_t = None
        fr = None
        try:
            while True:
                now = time.time()
                got = self.pair.get()
                if got is not None and got["t"] != last_t:
                    last_t, fr = got["t"], got
                    if self.state == "pattern":
                        self.pattern_step(fr)
                    elif self.state in ("touchcal", "hovercal"):
                        self.depthcal_step(fr, now)
                    elif self.cal and "dir" in self.cal:
                        self.touch_step(fr, now)
                if self.state == "test":
                    self.test_step(now)
                if self.state == "live":
                    if full:
                        cv2.destroyWindow(WIN)
                        full = False
                        cv2.namedWindow(STATUS, cv2.WINDOW_AUTOSIZE)
                        o = self.others[0] if self.others else self.disp
                        cv2.moveWindow(STATUS, int(o["x"] + o["w"] - 480), int(o["y"] + 40))
                    cv2.imshow(STATUS, self.status(fr))
                else:
                    if not full:
                        cv2.destroyWindow(STATUS)
                        open_full()
                        full = True
                    cv2.imshow(WIN, self.canvas(fr))
                k = cv2.waitKey(8) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord("r") and self.state == "place":
                    self.pair.turn = (self.pair.turn + 1) % 4
                if k == ord(" ") and self.state == "place":
                    self.go("pattern")
                if k == ord("c"):
                    self.cal, self.pair.Hp = None, None
                    self.go("pattern")
                if k == ord("t") and self.state == "menu":
                    self.go("test")
                if k == ord("l") and self.state == "menu":
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
            self.pair.running = False
            self.pair.rx.running = False
            if self.down and self.state == "live":
                mouse("up", *self.gx(self.pos))
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token")
    ap.add_argument("--display", type=int)
    ap.add_argument("--turn", type=int, default=0)
    a = ap.parse_args()
    App(a).run()


if __name__ == "__main__":
    main()
