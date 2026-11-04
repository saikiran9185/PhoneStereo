"""Touch Stage - draw on the projected wall with your finger.

  phone camera  -> WHERE your fingertip is on the wall (MediaPipe + the projected marker calibration)
  phone + Mac   -> HOW FAR it is off the wall (AI depth on the hand only, both cameras in one GPU pass)
  fused         -> TOUCH / HOVER with your own touch + hover levels, smoothed, no flicker

Start: it calibrates the wall by itself (markers flash for ~2 s). Then:
  T  finger ON the wall, hold still, press T      H  finger 5-10 cm OFF the wall, hold still, press H
  touch the wall = draw    touch CLEAR (top right of the wall) or X = clear
  F  use both cameras / Mac only / phone only     M  model: metric indoor / CVPR 2026 paper
  + / -  smoothing     C  recalibrate the wall     Q  quit
"""
import argparse
import collections
import json
import os
import time

import cv2
import numpy as np

from surfacetouch.app import App, CONF_DIR, WIN
from surfacetouch import maccam, zoom
from touch3d import Pair
from touchscreen import tink

from .depth import BONES as BONES_, DepthWorker

DASH = "Touch Stage"
ENTER, EXIT, HOLD_S = 0.35, 0.65, 0.20          # closeness to flip to TOUCH / back to HOVER, and for how long
LEVELS = os.path.join(CONF_DIR, "touchstage_levels.json")
BG, PANEL, TXT, DIM = (28, 24, 22), (44, 38, 34), (235, 235, 235), (150, 150, 150)
GREEN, BLUE, AMBER, GREY = (90, 210, 90), (230, 160, 60), (40, 180, 240), (120, 120, 120)
PURPLE, YELLOW = (230, 120, 200), (80, 210, 250)
F = cv2.FONT_HERSHEY_DUPLEX


def put(img, t, x, y, sc=0.55, c=TXT, th=1):
    cv2.putText(img, t, (int(x), int(y)), F, sc, c, th, cv2.LINE_AA)


class Stage(App):
    def __init__(self, a):
        super().__init__(a)
        self.click = "touch"
        self.worker = DepthWorker(model="metric", size=266, smooth=3)
        self.fuse = "both"
        self.touch, self.flip_since = False, None
        self.strokes, self.cur_stroke = [], None
        self.pos_s = None
        self.fused = None
        self.fhist = collections.deque(maxlen=400)       # (t, fused, touching)
        self.ftimes = collections.deque(maxlen=30)
        self.last_fr = None
        self.cal_msg = "finding the wall markers..."
        self.load_levels()

    # ---- levels (your touch / hover values) persist per model
    def load_levels(self):
        try:
            d = json.load(open(LEVELS)).get(self.worker.model, {})
            for k, v in d.items():
                self.worker.cams[k].levels = dict(v)
        except Exception:
            pass

    def save_levels(self):
        try:
            d = json.load(open(LEVELS)) if os.path.exists(LEVELS) else {}
        except Exception:
            d = {}
        d[self.worker.model] = {k: c.levels for k, c in self.worker.cams.items()}
        json.dump(d, open(LEVELS, "w"), indent=1)

    def side_markers(self, fr, lay, phone_seen):
        """Touch Stage has no side mode: keep looking for the markers and show both views so you can aim them."""
        _, n, _ = self.mac_wall(lay)
        self.cal_msg = f"phone sees {phone_seen} of 15 markers, Mac sees {n} - point both cameras at the whole projected image"

    def go(self, st):
        super().go("stage" if st == "menu" else st)

    # ---- per phone frame
    def stage_frame(self, fr, now):
        lm = fr["lm_a"]
        pos = None
        if lm is not None and self.Hs is not None:
            s = cv2.perspectiveTransform(np.array([[lm[8]]], np.float32), self.Hs)[0, 0]
            p = np.array([s[0] / self.W, s[1] / self.H])
            if -0.05 <= p[0] <= 1.05 and -0.05 <= p[1] <= 1.05:
                self.pos_s = p if self.pos_s is None else self.pos_s + 0.4 * (p - self.pos_s)
                pos = tuple(self.pos_s)
                self.pos_t = now
        if pos is None:
            if self.pos_s is not None and now - getattr(self, "pos_t", 0) < 0.4:
                pos = tuple(self.pos_s)                   # hand missed for a moment: keep the last point
            else:
                self.pos_s = None
        self.cur_pos = pos
        mf, mlm = None, None
        if self.mac is not None:
            with self.mac.lock:
                if self.mac.frame is not None and self.mac.hist:
                    mf = self.mac.frame.copy()
                    hands = self.mac.hist[-1][1]
                    mlm = hands[0] if hands else None
        self.worker.submit(fr["a"], lm, mf, mlm)

    def decide(self, now):
        cams = self.worker.cams
        use = {"both": ("phone", "mac"), "mac": ("mac",), "phone": ("phone",)}[self.fuse]
        cl = [cams[c].closeness() for c in use]
        cl = [c for c in cl if c is not None]
        self.fused = float(np.mean(cl)) if cl else None
        on_wall = self.cur_pos is not None
        want = self.touch
        if self.fused is None or not on_wall:
            want = False
        elif self.fused < ENTER:
            want = True
        elif self.fused > EXIT:
            want = False
        if want != self.touch:
            if self.flip_since is None:
                self.flip_since = now
            if now - self.flip_since >= HOLD_S or not on_wall:
                self.touch, self.flip_since = want, None
                if self.touch:
                    tink()
                    if self.cur_pos[0] > 0.86 and self.cur_pos[1] < 0.1:
                        self.strokes = []
                    else:
                        self.cur_stroke = []
                        self.strokes.append(self.cur_stroke)
                else:
                    self.cur_stroke = None
        else:
            self.flip_since = None
        if self.touch and self.cur_stroke is not None and self.cur_pos is not None:
            if not self.cur_stroke or np.hypot(*(np.subtract(self.cur_pos, self.cur_stroke[-1]))) > 0.002:
                self.cur_stroke.append(self.cur_pos)
        if self.fused is not None:
            self.fhist.append((now, self.fused, self.touch))

    # ---- projector picture
    def canvas(self):
        if self.state != "stage":
            return super().canvas()
        cw, ch = 960, int(round(960 * self.H / self.W))
        img = np.full((ch, cw, 3), 248, np.uint8)
        P = lambda p: (int(p[0] * cw), int(p[1] * ch))
        for s in self.strokes:
            if len(s) > 1:
                cv2.polylines(img, [np.array([P(p) for p in s], np.int32)], False, (150, 60, 20), 5, cv2.LINE_AA)
        cv2.rectangle(img, (int(0.86 * cw), 8), (cw - 8, int(0.1 * ch)), (60, 60, 200), 2)
        put(img, "CLEAR", int(0.88 * cw), int(0.07 * ch), 0.6, (60, 60, 200))
        cams = self.worker.cams
        if not all("touch" in c.levels and "hover" in c.levels for c in cams.values()):
            put(img, "Set up: finger ON the wall + T,  then 5-10 cm OFF + H  (Mac keyboard)", 20, ch - 20, 0.6, (40, 120, 200))
        if self.cur_pos is not None:
            c = P(self.cur_pos)
            if self.touch:
                cv2.circle(img, c, 10, (60, 170, 60), -1, cv2.LINE_AA)
            else:
                r = 12 + int(30 * np.clip(self.fused if self.fused is not None else 1, 0, 1))
                cv2.circle(img, c, r, (40, 140, 230), 2, cv2.LINE_AA)
        return img

    # ---- Mac dashboard: every camera feed + every live number
    def cam_view(self, img, H, lm, size, label, col, seen_n=None):
        """Full camera frame, the projected wall outlined, the hand skeleton and fingertip."""
        w, h = size
        if img is None:
            v = np.full((h, w, 3), PANEL, np.uint8)
            put(v, "no frames", w // 2 - 45, h // 2, 0.55, DIM)
        else:
            v = img.copy()
            if H is not None:
                q = cv2.perspectiveTransform(np.array([[[0, 0], [self.W, 0], [self.W, self.H], [0, self.H]]], np.float32),
                                             np.linalg.inv(H))[0]
                cv2.polylines(v, [q.astype(np.int32)], True, (90, 210, 90), max(2, v.shape[1] // 300), cv2.LINE_AA)
            if lm is not None:
                p = np.asarray(lm, np.int32)
                for a, b in BONES_:
                    cv2.line(v, tuple(p[a]), tuple(p[b]), (255, 230, 0) if b in (6, 7, 8) and a in (5, 6, 7) else (240, 240, 240),
                             max(2, v.shape[1] // 320), cv2.LINE_AA)
                cv2.circle(v, tuple(p[8]), max(6, v.shape[1] // 90), (80, 255, 80), 2, cv2.LINE_AA)
            v = cv2.resize(v, (w, h))
        cv2.rectangle(v, (0, 0), (w, 24), (0, 0, 0), -1)
        put(v, label, 8, 17, 0.48, col)
        if seen_n is not None:
            put(v, seen_n, w - 120, 17, 0.45, TXT)
        return v

    def dashboard(self):
        W_, H_ = 1470, 880
        img = np.full((H_, W_, 3), BG, np.uint8)
        w = self.worker
        cams = w.cams
        now = time.time()
        # ---- header chips
        put(img, "TOUCH STAGE", 18, 36, 0.9, TXT, 2)
        pf = (len(self.ftimes) - 1) / max(1e-3, self.ftimes[-1] - self.ftimes[0]) if len(self.ftimes) > 1 else 0
        chips = [(f"phone {pf:.0f} fps", TXT), (f"Mac {self.mac.fps:.0f} fps" if self.mac else "Mac off", TXT),
                 (f"AI depth {w.fps:.0f} fps  {w.ms:.0f} ms", TXT), (f"model {w.model}", TXT),
                 ("wall calibrated" if self.state == "stage" else f"calibrating ({self.state})",
                  GREEN if self.state == "stage" else AMBER)]
        x = 260
        for c, col in chips:
            tw = cv2.getTextSize(c, F, 0.5, 1)[0][0] + 20
            cv2.rectangle(img, (x, 14), (x + tw, 42), PANEL, -1)
            put(img, c, x + 10, 34, 0.5, col)
            x += tw + 8
        # ---- row 1: the four views
        fr = self.last_fr
        mf, mlm = None, None
        if self.mac is not None:
            with self.mac.lock:
                if self.mac.frame is not None:
                    mf = self.mac.frame.copy()
                    mlm = self.mac.hist[-1][1][0] if self.mac.hist and self.mac.hist[-1][1] else None
        y0, tw_, th_ = 56, 350, 262
        seen_p = f"markers {self.seen[0]}/15" if self.state != "stage" and getattr(self, "seen", None) else None
        seen_m = f"markers {self.mac_seen}/15" if self.state != "stage" else None
        views = [self.cam_view(None if fr is None else fr["a"], self.Hs, None if fr is None else fr["lm_a"], (tw_, th_),
                               "1  PHONE camera (finds WHERE)", PURPLE, seen_p),
                 self.cam_view(mf, self.Hm, mlm, (tw_, th_), "2  MAC camera", YELLOW, seen_m)]
        for name, label, col in (("phone", "3  PHONE hand - AI depth", PURPLE), ("mac", "4  MAC hand - AI depth", YELLOW)):
            t = cams[name].tile
            if t is None:
                t = np.full((th_, tw_, 3), PANEL, np.uint8)
                put(t, "no hand seen", tw_ // 2 - 60, th_ // 2, 0.55, DIM)
            else:
                t = t.copy()
            cv2.rectangle(t, (0, 0), (tw_, 24), (0, 0, 0), -1)
            put(t, label, 8, 17, 0.48, col)
            v = cams[name].value
            cv2.rectangle(t, (0, th_ - 26), (tw_, th_), (0, 0, 0), -1)
            put(t, "finger off wall: " + ("-" if v is None else f"{v:.0f} mm"), 8, th_ - 8, 0.5, TXT)
            views.append(t)
        for i, v in enumerate(views):
            x0 = 18 + i * (tw_ + 10)
            img[y0:y0 + th_, x0:x0 + tw_] = v
        put(img, "green outline = projected wall    cyan = index finger    colour on the hand: red/yellow = nearer the camera, blue = farther",
            18, y0 + th_ + 20, 0.45, DIM)
        # ---- row 2 left: verdict, bars, numbers
        x0, y1 = 18, y0 + th_ + 36
        ready = all("touch" in c.levels and "hover" in c.levels for c in cams.values())
        if self.state != "stage":
            vt, vc = "CALIBRATING", AMBER
        elif not ready:
            vt, vc = "SET T + H", AMBER
        elif self.cur_pos is None:
            vt, vc = "NO HAND", GREY
        else:
            vt, vc = ("TOUCH", GREEN) if self.touch else ("HOVER", BLUE)
        cv2.rectangle(img, (x0, y1), (x0 + 520, y1 + 80), vc, -1)
        tw = cv2.getTextSize(vt, F, 1.5, 3)[0][0]
        put(img, vt, x0 + 260 - tw // 2, y1 + 55, 1.5, (20, 20, 20), 3)
        if self.state != "stage":
            put(img, self.cal_msg[:70], x0, y1 + 100, 0.45, AMBER)
        elif not ready:
            put(img, "finger ON the wall + T,  then 5-10 cm OFF + H", x0, y1 + 100, 0.48, AMBER)
        yb = y1 + 118
        put(img, "closeness to the wall  (full bar = your touch level; marks = switch points)", x0, yb, 0.43, DIM)
        for j, (lab, val, col) in enumerate((("fused", self.fused, TXT), ("phone", cams["phone"].closeness(), PURPLE),
                                             ("Mac", cams["mac"].closeness(), YELLOW))):
            yy = yb + 14 + j * 30
            put(img, lab, x0, yy + 15, 0.5, col)
            bx, bw_ = x0 + 70, 450
            cv2.rectangle(img, (bx, yy), (bx + bw_, yy + 20), PANEL, -1)
            if val is not None:
                fill = int(np.clip(1 - val, 0, 1) * bw_)
                cv2.rectangle(img, (bx, yy), (bx + fill, yy + 20), GREEN if (lab == "fused" and self.touch) else col, -1)
            for th in (ENTER, EXIT):
                xt = bx + int((1 - th) * bw_)
                cv2.line(img, (xt, yy - 3), (xt, yy + 23), TXT, 1)
        # numbers table
        yt = yb + 112
        hdr = ["", "seen", "raw mm", "smooth mm", "touch lvl", "hover lvl", "closeness"]
        cx = [x0, x0 + 70, x0 + 130, x0 + 205, x0 + 295, x0 + 375, x0 + 455]
        for c, t in zip(cx, hdr):
            put(img, t, c, yt, 0.42, DIM)
        f1 = lambda v: "-" if v is None else f"{v:.0f}"
        for j, (name, col) in enumerate((("phone", PURPLE), ("mac", YELLOW))):
            c = cams[name]
            cl = c.closeness()
            vals = [name, "yes" if c.seen else "no", f1(c.raw), f1(c.value), f1(c.levels.get("touch")),
                    f1(c.levels.get("hover")), "-" if cl is None else f"{cl:.2f}"]
            for cc, t in zip(cx, vals):
                put(img, t, cc, yt + 22 + j * 22, 0.46, col if cc == x0 else TXT)
        rows = [("fingertip on wall", "-" if self.cur_pos is None else f"x {self.cur_pos[0]:.3f}   y {self.cur_pos[1]:.3f}"),
                ("fused closeness", "-" if self.fused is None else f"{self.fused:.2f}   (TOUCH below {ENTER}, HOVER above {EXIT}, held {HOLD_S:.2f} s)"),
                ("cameras used / smoothing", f"{self.fuse}   /   {w.smooth} of 5"),
                ("strokes drawn", str(len(self.strokes)))]
        for j, (k, v) in enumerate(rows):
            put(img, k, x0, yt + 76 + j * 22, 0.44, DIM)
            put(img, v, x0 + 200, yt + 76 + j * 22, 0.44, TXT)
        # ---- row 2 right: graph, last 10 s
        gx0, gy0, gw, gh = 560, y1, W_ - 578, H_ - y1 - 40
        cv2.rectangle(img, (gx0, gy0), (gx0 + gw, gy0 + gh), PANEL, -1)
        put(img, "last 10 s     bottom line = your touch level    top line = your hover level", gx0 + 10, gy0 + 20, 0.45, DIM)
        Y = lambda v: int(gy0 + gh - 40 - (np.clip(v, -0.3, 1.3) + 0.3) / 1.6 * (gh - 70))
        X = lambda t: int(gx0 + gw - 10 - (now - t) / 10.0 * (gw - 20))
        ov = img.copy()
        cv2.rectangle(ov, (gx0 + 10, Y(EXIT)), (gx0 + gw - 10, Y(ENTER)), (75, 66, 58), -1)
        for t, f, tch in self.fhist:
            if tch and now - t < 10:
                cv2.line(ov, (X(t), gy0 + gh - 30), (X(t), gy0 + gh - 16), GREEN, 3)
        cv2.addWeighted(ov, 0.75, img, 0.25, 0, img)
        cv2.line(img, (gx0 + 10, Y(0)), (gx0 + gw - 10, Y(0)), GREEN, 1)
        cv2.line(img, (gx0 + 10, Y(1)), (gx0 + gw - 10, Y(1)), BLUE, 1)
        put(img, "touch", gx0 + gw - 58, Y(0) - 6, 0.42, GREEN)
        put(img, "hover", gx0 + gw - 58, Y(1) - 6, 0.42, BLUE)
        for name, col in (("phone", PURPLE), ("mac", YELLOW)):
            c = cams[name]
            if "touch" in c.levels and "hover" in c.levels:
                g = (c.levels["hover"] - c.levels["touch"]) or 1e-6
                raw = [(X(t), Y((v - c.levels["touch"]) / g)) for t, v in c.raw_hist if now - t < 10]
                if len(raw) > 1:
                    for p in raw:
                        cv2.circle(img, p, 1, tuple(int(0.55 * v) for v in col), -1)
                pts = [(X(t), Y((v - c.levels["touch"]) / g)) for t, v in c.hist if now - t < 10]
                if len(pts) > 1:
                    cv2.polylines(img, [np.array(pts, np.int32)], False, col, 1, cv2.LINE_AA)
        pts = [(X(t), Y(f)) for t, f, _ in self.fhist if now - t < 10]
        if len(pts) > 1:
            cv2.polylines(img, [np.array(pts, np.int32)], False, TXT, 3, cv2.LINE_AA)
        put(img, "white = fused (decides)   purple / yellow lines = phone / Mac smoothed   dots = raw frames   green ticks = touching",
            gx0 + 10, gy0 + gh - 4, 0.4, DIM)
        put(img, "T touch level   H hover level   X clear   F cameras   M model   +/- smoothing   C recalibrate   Q quit",
            18, H_ - 14, 0.5, DIM)
        return img

    # ---- main loop (replaces the old wizard / experiments)
    def run(self):
        turn = (self.saved or {}).get("turn", self.a.turn)
        if self.a.zoom:
            zoom.start_streamer(self.a.token, None, self.a.stream_size, log=self.log)
        self.pair = Pair(self.log, self.a.host, self.a.token, turn, boost=False)
        self.pair.start()
        self.rec = None
        if not self.a.no_mac:
            self.mac = maccam.MacCam(self.a.mac_cam, self.log)
            self.mac.start()
        self.worker.start()
        cv2.namedWindow(DASH, cv2.WINDOW_AUTOSIZE)
        o = self.others[0] if self.others else self.disp
        cv2.moveWindow(DASH, int(o["x"]) + 10, int(o["y"]) + 30)
        self.open_full()
        last_t = None
        self.go("markers")
        try:
            while True:
                now = time.time()
                got = self.pair.get()
                if got is not None and got["t"] != last_t:
                    last_t, fr = got["t"], got
                    self.ftimes.append(now)
                    self.last_fr = fr
                    if self.state == "markers":
                        self.markers_step(fr)
                    elif self.state == "white":
                        self.white_step(fr)
                    elif self.state == "stage":
                        self.stage_frame(fr, now)
                if self.state == "stage":
                    self.decide(now)
                cv2.imshow(WIN, self.canvas())
                cv2.imshow(DASH, self.dashboard())
                k = cv2.waitKey(5) & 0xFF
                if k in (ord("q"), 27):
                    break
                cams = self.worker.cams
                if k in (ord("t"), ord("h")):
                    for c in cams.values():
                        c.set_level("touch" if k == ord("t") else "hover")
                    self.save_levels()
                    tink()
                if k == ord("x"):
                    self.strokes = []
                if k == ord("f"):
                    self.fuse = {"both": "mac", "mac": "phone", "phone": "both"}[self.fuse]
                if k == ord("m"):
                    self.worker.want_model = "paper" if self.worker.model == "metric" else "metric"
                if k in (ord("+"), ord("=")):
                    self.worker.smooth = min(5, self.worker.smooth + 1)
                if k == ord("-"):
                    self.worker.smooth = max(1, self.worker.smooth - 1)
                if k == ord("c"):
                    self.Hs = self.white = None
                    self.go("markers")
        finally:
            self.worker.running = False
            self.pair.running = False
            self.pair.rx.running = False
            if self.mac:
                self.mac.running = False
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token")
    ap.add_argument("--display", type=int, help="projector display index")
    ap.add_argument("--turn", type=int, default=0)
    ap.add_argument("--mac-cam", type=int, default=0)
    ap.add_argument("--no-mac", action="store_true")
    ap.add_argument("--zoom", action="store_true", help="USB: crop the phone sensor onto the wall")
    ap.add_argument("--stream-size", default="960x720")
    ap.add_argument("--phone-lag", type=float, default=0.06)
    a = ap.parse_args()
    # settings the shared calibration engine expects
    a.click, a.side, a.no_record, a.no_finger_lock, a.wall_cm = "touch", False, True, True, None
    Stage(a).run()
