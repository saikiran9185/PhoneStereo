"""SurfaceTouch V1 - projector wall touch from one phone (main + ultra-wide) on a Mac.

Wizard (projected full screen):  PLACE -> MARKERS (camera->projector mapping) -> WHITE (photometric reference) -> MENU
MENU:  E  Experiment T (Teach): you hold a mouse button / Shift while touching -> exact labels -> trains the model
       A  old Experiment A: touch vs hover at 0/10/20/30/50 mm (labels depend on guessing heights)
       V  Experiment B: 5x5 touch accuracy grid + hover traps -> error, misses, false touches
       D  Demo wall: buttons, drawing, drag a card, slider (Experiment C)
       L  Live: touches drive the Mac mouse (Keynote / PowerPoint / browser); flick = next / previous slide
       C  recalibrate    R  rotate phone view (PLACE)    N  next display (PLACE)    Q quit
A diagnostics window on the Mac screen shows main + ultra-wide views, the shadow, and the touch engine.
Event protocol (JSON lines, logs/surface_<time>_events.jsonl):
  {"event": "TOUCH_DOWN", "pointer_id": 1, "x": 0.42, "y": 0.67, "confidence": 0.96, "timestamp": ...}
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
import Quartz as Q

import signs as S
import viewer as V
from touch3d import ARUCO, Pair
from touchscreen import ax_ok, displays, key, mouse, tink

from . import contact, depthtest, maccam, pinch, record, replay, shadow, teach, zoom
from .state import TouchStateMachine

S.NUM_HANDS = 2
CONF_DIR = os.path.expanduser("~/.config/phonestereo")
WIN, DIAG = "SurfaceTouch", "SurfaceTouch diagnostics"
POSITIONS = [(0.2, 0.3), (0.5, 0.3), (0.8, 0.3), (0.2, 0.7), (0.5, 0.7), (0.8, 0.7)]
CONDITIONS = [("TOUCH", "TOUCH the dot and hold still"),
              ("H10", "hover 10 mm from the wall (about half a finger width)"),
              ("H20", "hover 20 mm (about one finger width)"),
              ("H30", "hover 30 mm (about two finger widths)"),
              ("H50", "hover 50 mm (about three finger widths)")]
READY_S, RECORD_S = 1.6, 2.0


def grab_display(did):
    """What the Mac is showing on that display (needs Screen Recording permission), BGR, or None."""
    try:
        img = Q.CGDisplayCreateImage(did)
        if img is None:
            return None
        w, h, bpr = Q.CGImageGetWidth(img), Q.CGImageGetHeight(img), Q.CGImageGetBytesPerRow(img)
        data = Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(img))
        return np.frombuffer(data, np.uint8).reshape(h, bpr // 4, 4)[:, :w, :3].copy()
    except Exception:
        return None


class App:
    def __init__(self, a):
        self.a = a
        self.ds = displays()
        ext = [i for i, d in enumerate(self.ds) if not d["builtin"]]
        self.di = a.display if a.display is not None else (
            max(ext, key=lambda i: self.ds[i]["w"] * self.ds[i]["h"]) if ext else 0)
        self.set_display(self.di)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(V.LOG_DIR, exist_ok=True)
        self.base = os.path.join(V.LOG_DIR, f"surface_{stamp}")
        self.log = V.EventLog(self.base + ".log")
        self.evlog = open(self.base + "_events.jsonl", "a", buffering=1)
        self.state, self.t_state = "place", time.time()
        self.Hs = self.white = None
        self.model = contact.ContactModel()
        self.sm = self.new_sm()
        self.render_hist = collections.deque(maxlen=30)
        self.feat = self.dbg = None
        self.p = 0.0
        self.cur_pos = None
        self.false_touches = 0
        self.results = {}
        self.ref = None                 # adaptive wall reference for live mode
        self.no_hand_since = None
        self.demo = {"color": (40, 40, 220), "strokes": [], "card": [0.62, 0.45], "slider": 0.5, "drag": None}
        self.frame_age = collections.deque(maxlen=60)
        self.mac, self.Hm, self.mac_lm, self.mac_seen = None, None, None, 0
        self.crop, self.zoomed, self.calib_id = None, False, 0
        self.flock = replay.FingerLock()
        self.click = a.click                             # "pinch" (thumb + index) or "touch" (finger on the wall)
        self.pcur = pinch.Cursor()
        self.pinch_r = None
        self.vrows = []
        self.load()

    # ---- display + calibration persistence
    def set_display(self, i):
        self.di = i % len(self.ds)
        self.disp = self.ds[self.di]
        self.W, self.H = self.disp["w"], self.disp["h"]
        self.S = 960 / self.W                 # canvas width 960 px
        self.others = [d for j, d in enumerate(self.ds) if j != self.di]

    def cal_path(self):
        return os.path.join(CONF_DIR, f"surfacetouch_{int(self.W)}x{int(self.H)}.json")

    def load(self):
        try:
            c = json.load(open(self.cal_path()))
            self.model = contact.ContactModel.from_json(c.get("model"))
            self.saved = c
        except (OSError, ValueError):
            self.saved = None

    def save(self, **kw):
        c = self.saved or {}
        c.update(kw)
        c["display"] = [self.W, self.H]
        os.makedirs(CONF_DIR, exist_ok=True)
        json.dump(c, open(self.cal_path(), "w"), indent=1)
        self.saved = c

    def wall_mm_per_pt(self):
        cm = self.a.wall_cm or (self.saved or {}).get("wall_width_cm")
        return (cm * 10 / self.W) if cm else None

    # ---- markers
    def marker_layout(self):
        size = min(self.W / 8, self.H / 4.8)
        out = {}
        for r in range(3):
            for c in range(5):
                cx, cy, h = (c + 0.5) * self.W / 5, (r + 0.5) * self.H / 3, size / 2
                out[r * 5 + c] = np.array([[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h], [cx - h, cy + h]])
        return out, size

    def find(self, im):
        det = cv2.aruco.ArucoDetector(ARUCO, cv2.aruco.DetectorParameters())
        c, ids, _ = det.detectMarkers(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY))
        return {} if ids is None else {int(i): q[0] for i, q in zip(ids.ravel(), c)}

    def markers_step(self, fr):
        if time.time() - self.t_state < 0.8:          # let the projection and exposure settle
            return
        lay, _ = self.marker_layout()
        ma, mb = self.find(fr["a"]), self.find(fr["b"])
        self.seen = (len(ma), len(mb))
        ids = [i for i in ma if i in lay]
        if len(ids) < 4:
            return
        A = np.concatenate([ma[i] for i in ids]).astype(np.float32)
        Sc = np.concatenate([lay[i] for i in ids]).astype(np.float32)
        Hs, _ = cv2.findHomography(A, Sc, cv2.RANSAC, 3.0)
        if Hs is None:
            return
        self.Hs = Hs
        both = [i for i in ids if i in mb]
        if len(both) >= 4:
            B = np.concatenate([mb[i] for i in both]).astype(np.float32)
            A2 = np.concatenate([ma[i] for i in both]).astype(np.float32)
            Hp, _ = cv2.findHomography(B, A2, cv2.RANSAC, 1.5)
            self.pair.Hp = Hp
        err = np.linalg.norm(cv2.perspectiveTransform(A[None], Hs)[0] - Sc, axis=1)
        self.cal_info = {"markers_main": len(ids), "markers_both": len(both), "fit_pt_median": round(float(np.median(err)), 2)}
        self.Hm = None
        mf = self.mac.latest() if self.mac else None
        if mf is not None:
            mm = self.find(mf)
            mids = [i for i in mm if i in lay]
            self.mac_seen = len(mids)
            if len(mids) >= 4:
                M = np.concatenate([mm[i] for i in mids]).astype(np.float32)
                Sm = np.concatenate([lay[i] for i in mids]).astype(np.float32)
                Hm, _ = cv2.findHomography(M, Sm, cv2.RANSAC, 4.0)
                if Hm is not None:
                    self.Hm = Hm
                    e2 = np.linalg.norm(cv2.perspectiveTransform(M[None], Hm)[0] - Sm, axis=1)
                    self.cal_info.update(markers_mac=len(mids), fit_mac_pt_median=round(float(np.median(e2)), 2))
            if self.Hm is None:
                self.log(f"Mac camera sees {len(mids)} of 15 markers: turn the laptop so its camera sees the wall "
                         "(phone-only for now)", show=True)
            else:
                self.log(f"Mac camera: {len(mids)} markers, fit {self.cal_info['fit_mac_pt_median']} pt", show=True)
        self.log(f"wall found: {len(ids)} markers (both lenses {len(both)}), fit {self.cal_info['fit_pt_median']} pt", show=True)
        if self.a.zoom and not self.zoomed:
            self.zoomed = True                           # one zoom step per calibration
            raw_h, raw_w = (fr["a"].shape[:2] if self.pair.turn % 2 == 0 else fr["a"].shape[1::-1])
            crop = zoom.crop_for(A, self.pair.turn, raw_w, raw_h, self.crop)
            if crop is not None and crop != self.crop:
                self.log(f"zooming the phone camera {zoom.zoom_factor(crop)}x onto the wall - calibrating again", show=True)
                self.restart_stream(crop)
                self.go("markers")
                return
        self.cal_info["zoom"] = zoom.zoom_factor(self.crop)
        self.cal_info["crop"] = self.crop
        tink()
        self.go("white")

    def restart_stream(self, crop):
        """Restart the phone streamer (USB) with a sensor crop and reconnect."""
        self.pair.running = False
        self.pair.rx.running = False
        try:
            self.pair.rx.sock.close()
        except Exception:
            pass
        self.crop = crop
        zoom.start_streamer(self.a.token, crop, self.a.stream_size, log=self.log)
        turn = self.pair.turn
        self.pair = Pair(self.log, self.a.host, self.a.token, turn, boost=False)
        self.pair.start()
        self.Hs = self.white = None
        self.pair.roi = [None, None]

    def white_step(self, fr):
        el = time.time() - self.t_state
        if el < 0.9:
            return
        if fr["src"]:
            self.log("keep hands away from the wall during the white flash", show=True)
            self.white_buf = []
            self.t_state = time.time()
            return
        g = cv2.GaussianBlur(cv2.cvtColor(fr["a"], cv2.COLOR_BGR2GRAY).astype(np.float32), (5, 5), 0)
        self.white_buf.append(g)
        if len(self.white_buf) >= 8:
            self.white = np.median(self.white_buf, 0).astype(np.float32)
            np.save(os.path.join(CONF_DIR, "surfacetouch_white.npy"), self.white)
            self.save(Hm=None if self.Hm is None else self.Hm.tolist(), Hs=self.Hs.tolist(), Hp=None if self.pair.Hp is None else self.pair.Hp.tolist(),
                      turn=self.pair.turn, calib=self.cal_info, white_mean=round(float(self.white.mean()), 1),
                      time=datetime.datetime.now().isoformat(timespec="seconds"))
            self.log(f"photometric reference saved (wall brightness {self.white.mean():.0f}/255)", show=True)
            if self.white.mean() > 245:
                self.log("the wall is over-exposed in the camera: dim the room lights or lower projector brightness",
                         show=True)
            self.set_rois(fr)
            self.snapshot_calib()
            self.zoomed = False                          # a later recalibration (C) may re-zoom
            tink()
            self.go("menu")

    def snapshot_calib(self):
        """Each calibration is kept with the session, so a replay uses exactly the mapping that was live."""
        if not self.rec:
            return
        self.calib_id += 1
        d = {"id": self.calib_id, "Hs": self.Hs.tolist(), "Hp": None if self.pair.Hp is None else self.pair.Hp.tolist(),
             "Hm": None if self.Hm is None else self.Hm.tolist(), "turn": self.pair.turn, "W": self.W, "H": self.H,
             "roi": self.pair.roi, "mac_roi": self.mac.roi if self.mac else None, "crop": self.crop,
             "info": self.cal_info}
        json.dump(d, open(os.path.join(self.rec.folder, f"calib_{self.calib_id}.json"), "w"), indent=1)
        np.save(os.path.join(self.rec.folder, f"white_{self.calib_id}.npy"), self.white)

    def set_rois(self, fr):
        """Track hands only inside the wall area of each camera: the hand appears bigger to the tracker."""
        self.pair.roi[0] = maccam.wall_roi(self.Hs, self.W, self.H, fr["a"].shape)
        if self.pair.Hp is not None:
            Hb = self.pair.Hp @ np.eye(3)                       # ultra-wide -> main; wall = Hs @ Hp
            self.pair.roi[1] = maccam.wall_roi(self.Hs @ Hb, self.W, self.H, fr["b"].shape)
        if self.mac and self.Hm is not None:
            mf = self.mac.latest()
            if mf is not None:
                self.mac.roi = maccam.wall_roi(self.Hm, self.W, self.H, mf.shape)
        self.log(f"tracking crops: phone {self.pair.roi[0]}, wide {self.pair.roi[1]}, "
                 f"mac {self.mac.roi if self.mac else None}")

    # ---- per frame: features -> probability -> state machine -> events
    def expected_image(self, shape, t_cap):
        """Projected content (0..1) as the camera should see it, from what was rendered ~60 ms before capture."""
        if not self.render_hist:
            return np.ones(shape, np.float32)
        best = self.render_hist[-1]
        for t, g in reversed(self.render_hist):
            if t <= t_cap - 0.06:
                best = (t, g)
                break
        g = best[1]
        sx = g.shape[1] / self.W
        M = np.linalg.inv(self.Hs) @ np.diag([1 / sx, 1 / sx, 1.0])
        return cv2.warpPerspective(g, M, (shape[1], shape[0]), flags=cv2.INTER_LINEAR, borderValue=0)

    def process(self, fr, now):
        self.frame_age.append(now - fr["t_recv"])
        valid = fr["src"] is not None
        self.feat, self.dbg = None, None
        pos = None
        if valid:
            lm = fr["lm_a"]
            if not self.a.no_finger_lock and self.click != "pinch":                # pointing finger, not whatever MediaPipe calls "index"
                lm = replay.with_tip(lm, self.flock.tip(lm, now))
            gray = cv2.GaussianBlur(cv2.cvtColor(fr["a"], cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
            if self.state == "live":
                if self.ref is None:
                    self.ref = gray
                f, d = shadow.features(gray, np.ones_like(gray), self.ref, lm)
            else:
                f, d = shadow.features(gray, self.expected_image(gray.shape, fr["t_recv"]), self.white, lm)
            sh = fr.get("shift")
            f["parallax_ok"] = int(sh is not None and (fr.get("score") or 0) > 0.5)
            f["parallax_v"] = float(sh[0]) if f["parallax_ok"] else 0.0
            f["parallax_y"] = float(sh[1]) if f["parallax_ok"] else 0.0
            mh = self.mac.hands_at(fr["t_recv"] - self.a.phone_lag) if (self.mac and self.Hm is not None) else None
            mf, self.mac_lm = maccam.features(lm, self.Hs, mh, self.Hm, self.W)
            f.update(mf)
            self.feat, self.dbg = f, d
            s = cv2.perspectiveTransform(np.array([[lm[8]]], np.float32), self.Hs)[0, 0]
            pos = (float(s[0] / self.W), float(s[1] / self.H))
            if self.click == "pinch":
                rm = pinch.ratio(self.mac_lm) if (self.mac_lm is not None and f.get("mac_ok") and f["mac_d_n"] < 1.5) else None
                rp = pinch.ratio(lm)
                self.pinch_r = pinch.combined(rp, rm)
                f["pinch_phone"], f["pinch_mac"] = rp, rm
                mid = cv2.perspectiveTransform(np.array([[(lm[4] + lm[8]) / 2]], np.float32), self.Hs)[0, 0]
                c = self.pcur.update(now, self.pinch_r, (pos[0] * self.W, pos[1] * self.H), (mid[0], mid[1]))
                if c is not None:
                    pos = (float(c[0] / self.W), float(c[1] / self.H))
                on_wall = -0.03 <= pos[0] <= 1.03 and -0.03 <= pos[1] <= 1.03
                valid = on_wall
                self.p = pinch.prob(self.pinch_r) if valid else 0.0
            else:
                on_wall = -0.03 <= pos[0] <= 1.03 and -0.03 <= pos[1] <= 1.03 and f["lit"] > 0.15
                valid = on_wall
                self.p = self.model.prob(f) if valid else 0.0
            self.no_hand_since = None
        else:
            self.p = 0.0
            if self.state == "live":           # keep the wall reference fresh while nobody is in front of it
                self.no_hand_since = self.no_hand_since or now
                if now - self.no_hand_since > 0.5:
                    g = cv2.GaussianBlur(cv2.cvtColor(fr["a"], cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
                    self.ref = g if self.ref is None else 0.7 * self.ref + 0.3 * g
        self.cur_pos = pos
        events = self.sm.update(now, valid, self.p, pos)
        for e in events:
            if e["event"] != "HOVER":
                self.evlog.write(json.dumps({"event": e["event"], "pointer_id": 1, "x": e["x"], "y": e["y"],
                                             "confidence": e["confidence"], "timestamp": e["t"], "mode": self.state}) + "\n")
            self.on_event(e, now)
        if self.state == "collect":
            self.collect_record(fr, now, pos)
        if self.state == "teach":
            self.teach_record(fr, now, pos, valid)
        if self.state == "validate" and self.feat is not None and self.vi < len(self.vplan):
            kind, (tx, ty) = self.vplan[self.vi]
            self.vrows.append({"t": round(now - self.t_state, 3), "task": self.vi, "kind": kind, "tx": tx, "ty": ty,
                               "x": None if pos is None else round(pos[0], 4), "y": None if pos is None else round(pos[1], 4),
                               "p": round(self.p, 3), "state": self.sm.state, **self.feat})

    # ---- events -> modes
    def on_event(self, e, now):
        name = e["event"]
        if name == "TOUCH_DOWN" and self.state in ("menu", "demo", "live") and self.state != "validate":
            pass
        if self.state == "validate":
            self.validate_event(e, now)
        elif self.state == "demo":
            self.demo_event(e)
        elif self.state == "live":
            self.live_event(e, now)

    def gx(self, x, y):
        return float(self.disp["x"] + x * self.W), float(self.disp["y"] + y * self.H)

    def live_event(self, e, now):
        n = e["event"]
        if n == "HOVER" and self.click == "pinch" and e["x"] is not None and not getattr(self, "lv", None):
            if now - getattr(self, "_hover_t", 0) > 0.03:          # pointer follows the finger (you see where you aim)
                self._hover_t = now
                mouse("move", *self.gx(e["x"], e["y"]))
            return
        if n == "TOUCH_DOWN":
            self.lv = {"start": (e["x"], e["y"]), "t": now, "pressed": False}
        elif n == "TOUCH_MOVE" and getattr(self, "lv", None):
            if not self.lv["pressed"] and (now - self.lv["t"] > 0.25 or abs(e["x"] - self.lv["start"][0]) > 0.03):
                if abs(e["x"] - self.lv["start"][0]) > 0.08 and now - self.lv["t"] < 0.3:
                    self.lv["flick"] = True
                else:
                    self.lv["pressed"] = True
                    mouse("move", *self.gx(*self.lv["start"]))
                    mouse("down", *self.gx(*self.lv["start"]))
            if self.lv["pressed"]:
                mouse("drag", *self.gx(e["x"], e["y"]))
        elif n in ("TOUCH_UP", "CANCEL") and getattr(self, "lv", None):
            dx = (e["x"] or self.lv["start"][0]) - self.lv["start"][0]
            if self.lv["pressed"]:
                mouse("up", *self.gx(e["x"] or 0, e["y"] or 0))
            elif n == "TOUCH_UP" and (self.lv.get("flick") or abs(dx) > 0.08):
                key(123 if dx > 0 else 124)
            elif n == "TOUCH_UP":
                for k in ("move", "down", "up"):
                    mouse(k, *self.gx(*self.lv["start"]))
            self.lv = None

    # ---- Experiment A: touch vs hover data
    def collect_plan(self):
        plan = []
        order = list(range(len(POSITIONS)))
        random.shuffle(order)
        for pi in order:
            for lab, txt in CONDITIONS:
                plan.append((pi, lab, txt))
        return plan

    def collect_record(self, fr, now, pos):
        if self.ci >= len(self.plan):
            return
        pi, lab, _ = self.plan[self.ci]
        el = now - self.c_t
        if el >= READY_S and fr["src"] and self.feat is not None:
            tx, ty = POSITIONS[pi]
            near = pos is not None and np.hypot(pos[0] - tx, (pos[1] - ty) * self.H / self.W) < 0.15
            self.rows.append({"t": round(now - self.t_state, 3), "pos": pi, "label": lab, "valid": int(bool(near)),
                              "x": None if pos is None else round(pos[0], 4), "y": None if pos is None else round(pos[1], 4),
                              "p_live": round(self.p, 3), **self.feat})

    def collect_tick(self, now):
        if self.ci < len(self.plan) and now - self.c_t >= READY_S + RECORD_S:
            self.ci += 1
            self.c_t = now
            if self.ci < len(self.plan):
                tink()
        if self.ci >= len(self.plan) and self.state == "collect":
            self.finish_collect()

    def finish_collect(self):
        rows = self.rows
        rep = contact.evaluate(rows)
        good = [r for r in rows if r["valid"]]
        if len({r["label"] == "TOUCH" for r in good}) == 2:
            self.model = contact.ContactModel().fit([contact.vec(r) for r in good], [r["label"] == "TOUCH" for r in good])
            self.model.info = {"trained_on": len(good), "session": os.path.basename(self.base), "cv": rep}
            self.save(model=self.model.to_json())
            rep["weights"] = dict(zip(contact.FEATURES, np.round(self.model.w, 2).tolist()))
            rep["touch_threshold"] = round(self.model.thr, 2)
        rep["mac_camera"] = self.Hm is not None
        if rows:
            with open(self.base + "_samples.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        json.dump(rep, open(self.base + "_expA.json", "w"), indent=2)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "surface_expA", "time": os.path.basename(self.base), **rep}) + "\n")
        self.results["A"] = rep
        self.log("EXPERIMENT A " + json.dumps(rep), show=True)
        self.go("menu")

    # ---- Experiment T: button-labelled touch / no-touch (see teach.py)
    def teach_record(self, fr, now, pos, valid):
        if self.ti >= len(self.tplan) or self.t_arm < 2:
            return
        c = self.tplan[self.ti]
        el = now - self.t_t
        r = {"t": round(now - self.t_state, 3), "spot": c["spot"], "cue": c["cue"], "ready": int(el < c.get("ready_s", teach.READY)),
             "tx": round(c["xy"][0], 4), "ty": round(c["xy"][1], 4), "button": int(teach.button_down()),
             "tracked": int(bool(valid and self.feat is not None)),
             "x": None if pos is None else round(pos[0], 4), "y": None if pos is None else round(pos[1], 4),
             "p_live": round(self.p, 3)}
        if self.feat is not None:
            r.update(self.feat)
            la = fr["lm_a"]
            r["palm_phone"] = round(float(np.linalg.norm(la[9] - la[0])), 2)
            if self.mac_lm is not None:
                r["palm_mac"] = round(float(np.linalg.norm(self.mac_lm[9] - self.mac_lm[0])), 2)
            for cam, lm, H in (("phone", fr["lm_a"], self.Hs), ("mac", self.mac_lm, self.Hm)):
                for i, (x, y) in maccam.wall_points(lm, H).items():
                    r[f"{cam}_{teach.TIPS[i]}_x"], r[f"{cam}_{teach.TIPS[i]}_y"] = round(x, 1), round(y, 1)
        self.trows.append(r)

    def teach_tick(self, now):
        self.t_button = teach.button_down()
        if self.t_arm < 2:                 # prove the button reaches the app: press once, release once, then start
            if self.t_arm == 0 and now - self.t_state > 8 and not self.t_button:
                self.t_arm, self.t_cue = 2, True             # no button: label from the cues + stillness
                self.t_t = now
                self.log("no button - labels come from the instructions (hold still!)", show=True)
                tink()
                return
            if self.t_arm == 0 and self.t_button:
                self.t_arm = 1
            elif self.t_arm == 1 and not self.t_button:
                self.t_arm = 2
                self.t_t = now
                self.log("button works - starting", show=True)
                tink()
            return
        if self.ti < len(self.tplan) and now - self.t_t >= self.tplan[self.ti].get("ready_s", teach.READY) + self.tplan[self.ti]["dur"]:
            self.ti += 1
            self.t_t = now
            if self.ti < len(self.tplan):
                tink()
        if self.ti >= len(self.tplan) and self.state == "teach":
            self.finish_teach()

    def finish_teach(self):
        rows = self.trows
        if self.t_kind == "depth":
            return self.finish_depth(rows)
        if rows:
            keys = list(dict.fromkeys(k for r in rows for k in r))
            with open(self.base + "_teach_samples.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)
        if self.t_cue:
            teach.cue_labels(rows, self.W, self.H)
        rep, good = teach.analyse(rows, self.W, self.H, self.wall_mm_per_pt())
        if len({r["button"] for r in good}) == 2:
            self.model = contact.ContactModel().fit([contact.vec(r) for r in good], [bool(r["button"]) for r in good])
            self.model.info = {"trained_on": len(good), "session": os.path.basename(self.base), "labels": "button", "cv": rep}
            self.save(model=self.model.to_json())
            rep["weights"] = dict(zip(contact.FEATURES, np.round(self.model.w, 2).tolist()))
            rep["touch_threshold"] = round(self.model.thr, 2)
        rep["mac_camera"] = self.Hm is not None
        json.dump(rep, open(self.base + "_teach.json", "w"), indent=2)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "surface_teach", "time": os.path.basename(self.base), **rep}) + "\n")
        self.results["T"] = {k: rep.get(k) for k in ("cv_auc", "precision_pct", "recall_pct", "button_held_pct_by_cue")}
        self.log("EXPERIMENT T " + json.dumps(rep), show=True)
        self.go("menu")

    def finish_depth(self, rows):
        if rows:
            keys = list(dict.fromkeys(k for r in rows for k in r))
            with open(self.base + "_depth_samples.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(rows)
        rep, _ = depthtest.analyse(rows, self.W, self.H)
        rep["mac_camera"] = self.Hm is not None
        rep["zoom"] = zoom.zoom_factor(self.crop)
        json.dump(rep, open(self.base + "_depth.json", "w"), indent=2)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "surface_depth", "time": os.path.basename(self.base), **rep}) + "\n")
        self.results["H"] = {"best": rep.get("best_signal"),
                             "rank": {k: v.get("rank_corr") for k, v in rep["signals"].items()}}
        self.log("EXPERIMENT H " + json.dumps(rep), show=True)
        self.go("menu")

    # ---- Experiment B: accuracy grid + hover traps
    def validate_plan(self):
        pts = [("TOUCH", (0.1 + 0.2 * i, 0.12 + 0.19 * j)) for i in range(5) for j in range(5)]
        traps = [("HOVER", (random.uniform(0.2, 0.8), random.uniform(0.25, 0.75))) for _ in range(6)]
        plan = pts + traps
        random.shuffle(plan)
        return plan

    def validate_event(self, e, now):
        if self.vi >= len(self.vplan):
            return
        kind, tgt = self.vplan[self.vi]
        if e["event"] == "TOUCH_DOWN":
            self.vdowns[self.vi].append((now - self.v_t, e["x"], e["y"], e["confidence"], self.sm_cand_latency()))
            if kind == "TOUCH" and self.v_done is None:
                self.v_done = now
        if e["event"] in ("TOUCH_UP", "CANCEL") and kind == "TOUCH" and self.v_done:
            self.v_up = now

    def sm_cand_latency(self):
        return float(np.median(self.frame_age)) + self.sm.confirm_down if self.frame_age else None

    def validate_tick(self, now):
        if self.vi >= len(self.vplan):
            return
        kind, _ = self.vplan[self.vi]
        lim = 6.0 if kind == "TOUCH" else 4.0
        if (self.v_done and now - self.v_done > 0.6 and self.sm.state not in ("DOWN", "DRAG")) or now - self.v_t > lim:
            self.vi += 1
            self.v_t, self.v_done = now, None
            if self.vi >= len(self.vplan):
                self.finish_validate()

    def finish_validate(self):
        mmpp = self.wall_mm_per_pt()
        errs, found, extra, false = [], 0, 0, 0
        rows = []
        for i, (kind, (tx, ty)) in enumerate(self.vplan):
            d = self.vdowns.get(i, [])
            if kind == "TOUCH":
                if d:
                    found += 1
                    e_pt = float(np.hypot((d[0][1] - tx) * self.W, (d[0][2] - ty) * self.H))
                    errs.append(e_pt)
                extra += max(0, len(d) - 1)
            else:
                false += len(d)
            rows.append({"task": i, "kind": kind, "tx": round(tx, 4), "ty": round(ty, 4), "touch_downs": len(d),
                         "first_x": d[0][1] if d else "", "first_y": d[0][2] if d else "",
                         "time_s": round(d[0][0], 3) if d else ""})
        nt = sum(1 for k, _ in self.vplan if k == "TOUCH")
        e = np.array(errs) if errs else None
        rep = {"touch_targets": nt, "hover_traps": len(self.vplan) - nt,
               "touch_detected_pct": round(100 * found / nt, 1), "extra_touches": extra, "false_touches_on_traps": false,
               "error_pct_of_width": None if e is None else {"median": round(100 * float(np.median(e)) / self.W, 2),
                                                             "p95": round(100 * float(np.percentile(e, 95)) / self.W, 2),
                                                             "max": round(100 * float(e.max()) / self.W, 2)},
               "error_mm": None if (e is None or not mmpp) else {"median": round(float(np.median(e)) * mmpp, 1),
                                                                  "p95": round(float(np.percentile(e, 95)) * mmpp, 1),
                                                                  "max": round(float(e.max()) * mmpp, 1)},
               "latency_ms_estimate": round(1000 * self.sm_cand_latency()) if self.sm_cand_latency() else None,
               "phone_fps": round(self.pair.rx.fps(0), 1),
               "model": "trained" if self.model.trained else "untrained rule"}
        with open(self.base + "_expB.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        if self.vrows:
            with open(self.base + "_expB_samples.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(self.vrows[0].keys()))
                w.writeheader()
                w.writerows(self.vrows)
        rep["mac_camera"] = self.Hm is not None
        json.dump(rep, open(self.base + "_expB.json", "w"), indent=2)
        with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
            f.write(json.dumps({"kind": "surface_expB", "time": os.path.basename(self.base), **rep}) + "\n")
        self.results["B"] = rep
        self.log("EXPERIMENT B " + json.dumps(rep), show=True)
        self.go("menu")

    # ---- demo wall (Experiment C)
    BUTTONS = {"RED": ((0.05, 0.05, 0.13, 0.13), (40, 40, 220)), "GREEN": ((0.2, 0.05, 0.13, 0.13), (60, 180, 60)),
               "BLUE": ((0.35, 0.05, 0.13, 0.13), (220, 120, 40)), "CLEAR": ((0.82, 0.05, 0.13, 0.13), (90, 90, 90))}

    def demo_event(self, e):
        d = self.demo
        x, y, n = e["x"], e["y"], e["event"]
        if x is None:
            return
        if n == "CLICK":
            for name, ((bx, by, bw, bh), col) in self.BUTTONS.items():
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    if name == "CLEAR":
                        d["strokes"] = []
                    else:
                        d["color"] = col
                    tink()
        if n == "TOUCH_DOWN":
            cx, cy = d["card"]
            if abs(x - cx) < 0.09 and abs(y - cy) < 0.09:
                d["drag"] = "card"
            elif 0.1 <= x <= 0.9 and 0.86 <= y <= 0.96:
                d["drag"] = "slider"
            elif y > 0.22:
                d["drag"] = "draw"
                d["strokes"].append((d["color"], [(x, y)]))
            else:
                d["drag"] = None
        elif n == "TOUCH_MOVE" and d["drag"]:
            if d["drag"] == "card":
                d["card"] = [x, y]
            elif d["drag"] == "slider":
                d["slider"] = float(np.clip((x - 0.1) / 0.8, 0, 1))
            elif d["drag"] == "draw" and d["strokes"]:
                d["strokes"][-1][1].append((x, y))
        elif n in ("TOUCH_UP", "CANCEL"):
            d["drag"] = None

    # ---- rendering (projected canvas)
    def canvas(self):
        cw, ch = 960, int(round(960 * self.H / self.W))
        img = np.full((ch, cw, 3), 250, np.uint8)
        P = lambda x, y: (int(x * cw), int(y * ch))
        put = lambda t, x, y, sc=0.6, c=(40, 40, 40), th=2: cv2.putText(img, t, (int(x), int(y)),
                                                                         cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        now = time.time()
        st = self.state
        if st == "place":
            img[:] = (40, 30, 25)
            put("SurfaceTouch - set up", 30, 50, 1.0, (0, 220, 255), 2)
            for i, t in enumerate(["Phone below or beside the projected area, back cameras toward the wall.",
                                   "NOT next to the projector lens: the camera must see the finger's shadow.",
                                   "Both views must show the whole projected image (check the Mac window).",
                                   "Dim the room lights.   SPACE = calibrate   R = rotate view   N = next display",
                                   f"projecting on display {self.di}: {self.W:.0f}x{self.H:.0f}"]):
                put(t, 30, 100 + i * 32, 0.55, (220, 220, 220), 1)
        elif st == "markers":
            img[:] = 255
            lay, size = self.marker_layout()
            k = cw / self.W
            for i, q in lay.items():
                m = cv2.aruco.generateImageMarker(ARUCO, i, int(size * k))
                x, y = int(q[0][0] * k), int(q[0][1] * k)
                img[y:y + m.shape[0], x:x + m.shape[1]] = m[..., None]
        elif st == "white":
            img[:] = 255
        elif st == "menu":
            put("SurfaceTouch ready" + ("   (contact model trained)" if self.model.trained else "   (untrained: press E first to train)"),
                30, 50, 0.8, (30, 120, 30))
            put("E  Experiment T: teach (hold mouse button / Shift while touching)    V  Experiment B: accuracy grid", 30, 95)
            put("D  Demo wall: buttons, draw, drag, slider    L  Live: control slides     C  recalibrate   Q  quit", 30, 125)
            put("H  Experiment H: hand depth - fingertip at 0 / 5 / 10 / 20 / 40 cm from the wall", 30, 155)
            put(f"P  click by: {'PINCH (thumb + index) - quick pinch = click, pinch and hold = drag' if self.click == 'pinch' else 'TOUCHING the wall'}",
                30, 215, 0.55, (40, 40, 220) if self.click == "pinch" else (90, 90, 90), 1)
            put("Touch anywhere to try: the ring shows the touch engine.", 30, 185, 0.5, (90, 90, 90), 1)
            y = 210
            for k_, rep in self.results.items():
                txt = json.dumps(rep)[:150]
                put(f"{k_}: {txt}", 30, y, 0.42, (60, 60, 60), 1)
                y += 26
        elif st == "collect" and self.ci < len(self.plan):
            pi, lab, txt = self.plan[self.ci]
            x, y = POSITIONS[pi]
            el = now - self.c_t
            rec = el >= READY_S
            cv2.circle(img, P(x, y), 16, (40, 40, 220) if rec else (0, 170, 230), 4, cv2.LINE_AA)
            cv2.circle(img, P(x, y), 4, (40, 40, 220), -1)
            put(f"A {self.ci + 1}/{len(self.plan)}: {txt}", 30, 40, 0.7, (40, 40, 220) if rec else (20, 120, 200))
            put("RECORDING - hold still" if rec else "get into position...", 30, 72, 0.6, (40, 40, 40), 1)
            cv2.rectangle(img, (0, ch - 8), (int(cw * min(1, el / (READY_S + RECORD_S))), ch), (40, 40, 220), -1)
        elif st == "teach" and self.t_arm < 2:
            put("Experiment T - teach the touch model", 30, 50, 0.8, (40, 40, 220))
            for i, t in enumerate(["Hold a mouse button (or Shift) in your OTHER hand exactly while your finger touches the wall.",
                                   "Release it the moment the finger leaves the wall. The button is the label - be honest.",
                                   ("PRESS AND RELEASE THE BUTTON ONCE NOW TO START  (no button? wait %d s: then just HOLD STILL "
                                    "on each instruction)" % max(0, int(9 - (now - self.t_state)))) if self.t_arm == 0 else "...now release it"]):
                put(t, 30, 100 + i * 34, 0.6, (40, 40, 40) if i < 2 else (40, 40, 220), 1 if i < 2 else 2)
            b = self.t_button
            cv2.rectangle(img, (cw - 230, 16), (cw - 20, 60), (40, 40, 220) if b else (200, 200, 200), -1)
        elif st == "teach" and self.ti < len(self.tplan):
            c = self.tplan[self.ti]
            x, y = c["xy"]
            el = now - self.t_t
            rec = el >= c.get("ready_s", teach.READY)
            col = {"HOLD": (40, 40, 220), "TAP": (40, 40, 220), "D0": (40, 40, 220)}.get(c["cue"], (30, 140, 230))
            if c["cue"] in ("HOLD", "TAP"):
                cv2.circle(img, P(x, y), 14, col, -1, cv2.LINE_AA)
            else:
                cv2.circle(img, P(x, y), 24, col, 3, cv2.LINE_AA)
            put(f"{'H' if self.t_kind == 'depth' else 'T'} {self.ti + 1}/{len(self.tplan)}  {c['cue']}: {c['text']}", 30, 40, 0.62, col)
            put("go" if rec else "get ready...", 30, 72, 0.6, (40, 40, 40), 1)
            b = self.t_button
            if self.t_kind != "depth":
                cv2.rectangle(img, (cw - 230, 16), (cw - 20, 60), (40, 40, 220) if b else (200, 200, 200), -1)
                put("BUTTON: TOUCHING" if b else "button: not touching", cw - 222, 45, 0.5,
                    (255, 255, 255) if b else (60, 60, 60), 1)
            cv2.rectangle(img, (0, ch - 8), (int(cw * min(1, el / (c.get("ready_s", teach.READY) + c["dur"]))), ch), col, -1)
        elif st == "validate" and self.vi < len(self.vplan):
            kind, (x, y) = self.vplan[self.vi]
            if kind == "TOUCH":
                cv2.circle(img, P(x, y), 14, (60, 170, 60), -1, cv2.LINE_AA)
                put(f"B {self.vi + 1}/{len(self.vplan)}: " + ("point at the green dot and PINCH" if self.click == "pinch"
                    else "TOUCH the green dot"), 30, 40, 0.7, (40, 130, 40))
            else:
                cv2.circle(img, P(x, y), 22, (30, 140, 230), 3, cv2.LINE_AA)
                put(f"B {self.vi + 1}/{len(self.vplan)}: " + ("point at the ring - do NOT pinch" if self.click == "pinch"
                    else "HOVER over the ring, 3-5 cm from the wall - do not touch"), 30, 40, 0.7, (30, 120, 220))
        elif st == "demo":
            d = self.demo
            for name, ((bx, by, bw, bh), col) in self.BUTTONS.items():
                cv2.rectangle(img, P(bx, by), P(bx + bw, by + bh), col, -1 if name != "CLEAR" else 3)
                put(name, bx * cw + 10, (by + bh / 2) * ch + 8, 0.6, (255, 255, 255) if name != "CLEAR" else col)
            for col, pts in d["strokes"]:
                if len(pts) > 1:
                    cv2.polylines(img, [np.array([P(*p) for p in pts], np.int32)], False, col, 6, cv2.LINE_AA)
            cx, cy = d["card"]
            cv2.rectangle(img, P(cx - 0.08, cy - 0.08), P(cx + 0.08, cy + 0.08), (180, 120, 255), -1)
            put("drag me", (cx - 0.05) * cw, cy * ch + 6, 0.6, (255, 255, 255))
            cv2.line(img, P(0.1, 0.91), P(0.9, 0.91), (150, 150, 150), 4)
            cv2.circle(img, P(0.1 + 0.8 * d["slider"], 0.91), 14, (40, 40, 220), -1)
            put(f"slider {d['slider']:.2f}", 0.1 * cw, 0.85 * ch, 0.5, (90, 90, 90), 1)
        # touch ring
        if st in ("menu", "demo", "validate") and self.cur_pos:
            c = P(*self.cur_pos)
            touching = self.sm.state in ("DOWN", "DRAG")
            cv2.circle(img, c, 20, (60, 170, 60) if touching else (150, 150, 150), 2 if not touching else -1, cv2.LINE_AA)
        msg, mt = self.log.last
        if msg and now - mt < 6 and st not in ("markers", "white"):
            put(msg[:120], 20, ch - 18, 0.45, (20, 100, 200), 1)
        return img

    # ---- diagnostics window (Mac screen)
    def diag(self, fr):
        D = np.full((900, 980, 3), (32, 16, 11), np.uint8)
        put = lambda t, x, y, sc=0.5, c=(230, 230, 230), th=1: cv2.putText(D, t, (int(x), int(y)),
                                                                            cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        if fr is not None:
            for k, (key_, lab) in enumerate((("a", "main"), ("b", "ultra-wide"))):
                im = fr[key_].copy()
                lm = fr["lm_a"] if key_ == "a" else fr["lm_b"]
                if lm is not None:
                    for p_ in lm.astype(int):
                        cv2.circle(im, tuple(p_), 3, (0, 220, 255), -1)
                    cv2.circle(im, tuple(lm[8].astype(int)), 9, (60, 220, 60), 2)
                if key_ == "a" and self.dbg and "shadow_tip" in self.dbg:
                    cv2.circle(im, tuple(self.dbg["shadow_tip"].astype(int)), 9, (60, 60, 230), 2)
                v = cv2.resize(im, (470, int(470 * im.shape[0] / im.shape[1])))
                v = v[:290]
                D[30:30 + v.shape[0], 10 + k * 485:10 + k * 485 + v.shape[1]] = v
                put(lab + ("  (hand)" if lm is not None else ""), 10 + k * 485, 22)
        if self.dbg and "ratio" in self.dbg:
            r = np.clip(self.dbg["ratio"] * 160, 0, 255).astype(np.uint8)
            r = cv2.cvtColor(r, cv2.COLOR_GRAY2BGR)
            r[self.dbg["shadow"] > 0] = (60, 60, 230)
            r[self.dbg["hand"]] = (r[self.dbg["hand"]] * 0.5 + np.array([0, 110, 0])).astype(np.uint8)
            v = cv2.resize(r, (280, 280), interpolation=cv2.INTER_NEAREST)
            D[330:610, 10:290] = v
            put("shadow view (red = shadow, green = hand)", 10, 325)
        f = self.feat or {}
        x0 = 310
        lines = [f"state  {self.sm.state}     mode {self.state}",
                 f"contact p  {self.p:.2f}   ({'model' if self.model.trained else 'rule'})",
                 f"shadow found {f.get('found', '-')}   gap {f.get('gap_n', '-')}   nearest {f.get('near_n', '-')} (finger lengths)",
                 f"stereo shift {f.get('parallax_v', '-') if f.get('parallax_ok') else '-'} px   sep {f.get('sep_n', '-')}",
                 f"fps {self.pair.rx.fps(0):.0f}   frame age {1000 * np.median(self.frame_age):.0f} ms" if self.frame_age else "fps -",
                 f"false touches (live/demo) {self.false_touches}",
                 f"pinch {self.pinch_r:.2f} (closed < {pinch.ENTER}, open > {pinch.EXIT})" if self.pinch_r is not None else "pinch -"]
        for i, t in enumerate(lines):
            put(t, x0, 350 + i * 28, 0.55)
        bx, by = x0, 530
        cv2.rectangle(D, (bx, by), (bx + 640, by + 24), (80, 80, 80), 1)
        cv2.rectangle(D, (bx, by), (bx + int(640 * self.p), by + 24), (60, 220, 60) if self.p > self.sm.p_down else (0, 200, 255), -1)
        for th in (self.sm.p_up, self.sm.p_down):
            cv2.line(D, (bx + int(640 * th), by - 4), (bx + int(640 * th), by + 28), (230, 230, 230), 1)
        put("contact probability", bx, by + 46)
        if self.pair.error:
            put("phone: " + self.pair.error, 10, 610, 0.55, (60, 60, 230), 2)
        if self.mac:
            mf = self.mac.latest()
            if mf is not None:
                if self.mac_lm is not None:
                    for p_ in self.mac_lm.astype(int):
                        cv2.circle(mf, tuple(p_), 3, (0, 220, 255), -1)
                    cv2.circle(mf, tuple(self.mac_lm[8].astype(int)), 9, (60, 220, 60), 2)
                v = cv2.resize(mf, (420, int(420 * mf.shape[0] / mf.shape[1])))[:250]
                D[640:640 + v.shape[0], 10:10 + v.shape[1]] = v
            st = (f"wall mapped ({self.cal_info.get('markers_mac', 0)} markers)" if self.Hm is not None and hasattr(self, "cal_info")
                  else "wall mapped" if self.Hm is not None else "not mapped yet (calibrate with SPACE / C)")
            put(f"Mac camera  {self.mac.fps:.0f} fps  {st}", 10, 632)
            if self.mac.error:
                put(self.mac.error, 450, 680, 0.5, (60, 60, 230), 2)
            put(f"two-view split  {f.get('mac_d_n', '-') if f.get('mac_ok') else '-'}  finger lengths", 450, 720, 0.55)
            put("(small = finger on the wall, grows with hover)", 450, 748, 0.5, (160, 160, 160))
        return D

    # ---- states
    def new_sm(self):
        if self.click == "pinch":
            return TouchStateMachine(p_down=0.6, p_up=0.15, confirm_down=0.06, confirm_up=0.08)
        t = self.model.thr if self.model.trained else 0.8
        return TouchStateMachine(p_down=t, p_up=max(0.05, 0.6 * t))

    def go(self, st):
        self.state, self.t_state = st, time.time()
        self.sm = self.new_sm()
        if st == "white":
            self.white_buf = []
        if st == "teach":
            self.tplan, self.ti, self.t_t, self.trows, self.t_button = teach.plan(), 0, time.time(), [], False
            self.t_arm, self.t_cue, self.t_kind = 0, False, "teach"
        if st == "collect":
            self.plan, self.ci, self.c_t, self.rows = self.collect_plan(), 0, time.time(), []
        if st == "validate":
            self.vplan, self.vi, self.v_t, self.v_done = self.validate_plan(), 0, time.time(), None
            self.vdowns = collections.defaultdict(list)
            self.vrows = []
        if st == "live":
            self.ref = None
        self.log(f"-> {st}")

    def open_full(self):
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.moveWindow(WIN, int(self.disp["x"]) + 40, int(self.disp["y"]) + 40)
        cv2.resizeWindow(WIN, int(self.W * 0.5), int(self.H * 0.5))
        cv2.waitKey(50)
        cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    def run(self):
        turn = (self.saved or {}).get("turn", self.a.turn)
        if self.a.zoom:
            zoom.start_streamer(self.a.token, None, self.a.stream_size, log=self.log)
        self.pair = Pair(self.log, self.a.host, self.a.token, turn, boost=False)
        self.pair.start()
        self.rec = None
        if not self.a.no_record:
            self.rec = record.Recorder(self.base + "_video", self.log)
            self.rec.start()
        last_canvas = None
        if not self.a.no_mac:
            self.mac = maccam.MacCam(self.a.mac_cam, self.log)
            self.mac.start()
            if self.saved and self.saved.get("Hm"):
                self.Hm = np.array(self.saved["Hm"])
        self.log(f"projector display {self.di}: {self.W:.0f}x{self.H:.0f}")
        cv2.namedWindow(DIAG, cv2.WINDOW_AUTOSIZE)
        o = self.others[0] if self.others else self.disp
        cv2.moveWindow(DIAG, int(o["x"]) + 20, int(o["y"]) + 40)
        self.open_full()
        full, last_t, fr = True, None, None
        try:
            while True:
                now = time.time()
                got = self.pair.get()
                if got is not None and got["t"] != last_t:
                    last_t, fr = got["t"], got
                    if self.state == "markers":
                        self.markers_step(fr)
                    elif self.state == "white":
                        self.white_step(fr)
                    elif self.Hs is not None and self.white is not None:
                        self.process(fr, now)
                if self.state == "collect":
                    self.collect_tick(now)
                if self.state == "validate":
                    self.validate_tick(now)
                if self.state == "teach":
                    self.teach_tick(now)
                if self.state == "live":
                    if full:
                        cv2.destroyWindow(WIN)
                        full = False
                else:
                    if not full:
                        self.open_full()
                        full = True
                    c = self.canvas()
                    last_canvas = c
                    self.render_hist.append((time.time(), cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), (480, int(480 * c.shape[0] / c.shape[1]))).astype(np.float32) / 255))
                    cv2.imshow(WIN, c)
                dimg = self.diag(fr)
                cv2.imshow(DIAG, dimg)
                if self.rec and fr is not None and fr["t"] != getattr(self, "_rec_t", None):
                    self._rec_t = fr["t"]
                    cue, spot, ready, tx, ty = "", "", "", "", ""
                    if self.state == "teach" and getattr(self, "ti", 0) < len(getattr(self, "tplan", [])) and self.t_arm >= 2:
                        c_ = self.tplan[self.ti]
                        cue, spot, (tx, ty) = c_["cue"], c_["spot"], c_["xy"]
                        ready = int(now - self.t_t < c_.get("ready_s", teach.READY))
                    elif self.state == "collect" and self.ci < len(self.plan):
                        cue, spot = self.plan[self.ci][1], self.plan[self.ci][0]
                        tx, ty = POSITIONS[spot]
                        ready = int(now - self.c_t < READY_S)
                    elif self.state == "validate" and self.vi < len(self.vplan):
                        cue, spot, (tx, ty) = self.vplan[self.vi][0], self.vi, self.vplan[self.vi][1]
                    self.rec.add({"main": fr["a"], "wide": fr["b"], "mac": self.mac.latest() if self.mac else None,
                                  "review": record.review_frame(dimg, last_canvas if last_canvas is not None else dimg)},
                                 {"t_recv": round(fr["t_recv"], 3), "t": round(now, 3), "mode": self.state, "cue": cue,
                                  "button": int(teach.button_down()), "p": round(self.p, 3), "touch_state": self.sm.state,
                                  "x": None if self.cur_pos is None else round(self.cur_pos[0], 4),
                                  "y": None if self.cur_pos is None else round(self.cur_pos[1], 4),
                                  "hand": fr["src"] or "", "spot": spot, "ready": ready, "tx": tx, "ty": ty,
                                  "calib": self.calib_id, "click": self.click,
                                  "pinch_r": "" if self.pinch_r is None else round(self.pinch_r, 3), "mac_t": round(self.mac.latest_t, 3) if self.mac else ""})
                k = cv2.waitKey(5) & 0xFF
                if k in (ord("q"), 27):
                    break
                if self.state == "place":
                    if k == ord("r"):
                        self.pair.turn = (self.pair.turn + 1) % 4
                    if k == ord("n"):
                        self.set_display(self.di + 1)
                        cv2.destroyWindow(WIN)
                        self.open_full()
                    if k == ord(" "):
                        self.go("markers")
                if k == ord("c"):
                    self.Hs = self.white = None
                    self.go("markers")
                if self.state == "menu":
                    if k == ord("e"):
                        self.go("teach")
                    if k == ord("a"):
                        self.go("collect")
                    if k == ord("p"):
                        self.click = "touch" if self.click == "pinch" else "pinch"
                        self.sm = self.new_sm()
                        self.log(f"click by {self.click}", show=True)
                    if k == ord("h"):                   # Experiment H: depth of the hand from the wall
                        self.go("teach")
                        self.tplan, self.t_arm, self.t_cue, self.t_kind = depthtest.plan(), 2, True, "depth"
                        self.t_t = time.time()
                    if k == ord("v"):
                        self.go("validate")
                    if k == ord("d"):
                        self.go("demo")
                    if k == ord("l"):
                        if not ax_ok():
                            self.log("Live needs Accessibility for PhoneStereo (System Settings > Privacy & Security)",
                                     show=True)
                        self.go("live")
                elif self.state in ("demo", "live", "collect", "validate", "teach") and k == ord("m"):
                    self.go("menu")
        except Exception:
            self.log("CRASH " + traceback.format_exc().replace("\n", " | "), show=True)
            raise
        finally:
            self.pair.running = False
            self.pair.rx.running = False
            if self.mac:
                self.mac.running = False
            if self.rec:
                self.rec.stop()
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token")
    ap.add_argument("--display", type=int, help="display index for the projector (N cycles in setup)")
    ap.add_argument("--turn", type=int, default=0)
    ap.add_argument("--wall-cm", type=float, help="width of the projected image in cm (for errors in mm)")
    ap.add_argument("--mac-cam", type=int, default=0, help="Mac camera index (second viewpoint of the wall)")
    ap.add_argument("--no-mac", action="store_true", help="phone cameras only")
    ap.add_argument("--no-record", action="store_true", help="don't record session video")
    ap.add_argument("--zoom", action="store_true",
                    help="USB only: crop the phone sensor to the wall after the first calibration (./build_streamer.sh once)")
    ap.add_argument("--no-finger-lock", action="store_true", help="use MediaPipe's index tip as is")
    ap.add_argument("--click", choices=["pinch", "touch"], default="pinch",
                    help="what counts as a click: thumb+index pinch (default) or the finger touching the wall")
    ap.add_argument("--stream-size", default="960x720", help="phone stream size in --zoom mode")
    ap.add_argument("--phone-lag", type=float, default=0.06, help="phone frame delay vs Mac camera, s")
    a = ap.parse_args()
    App(a).run()
