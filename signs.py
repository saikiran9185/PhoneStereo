#!/usr/bin/env python3
"""Sign Check - a small hand-sign game that scores three cameras against each other.

Cameras: the Mac's FaceTime camera + the phone's main + ultra-wide (logical camera 4, synced pairs).
Each camera runs its own MediaPipe hand tracker and guesses the sign on its own; "fused" is a
confidence-weighted vote of all three. The game shows a sign, you make it, and every camera is scored
on the same moments, so the result shows how much the extra views help.

Then two depth rounds compare the phone's stereo depth with the single-camera guess (hand size):
  PUSH  open palm, slowly toward the phone and back   -> how clean each depth signal is
  TILT  same distance, just tilt/turn the hand         -> depth should NOT change; hand size does

Keys: SPACE start · O rotate phone view · Q / Esc quit.
Logs: logs/signs_<time>.csv (every frame) and logs/signs_<time>_summary.json.
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
import types

import cv2
import numpy as np

import viewer as V

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "hand_landmarker.task")
WIN = "Sign Check - 3 cameras"
TW, TH = 400, 300                       # tile size on screen
PW = 640                                 # processing width for the hand tracker

SIGNS = ["FIST", "OPEN PALM", "PEACE", "POINT", "THUMBS UP", "HORNS"]
HOW = {"FIST": "close all fingers", "OPEN PALM": "all five fingers open",
       "PEACE": "index + middle up", "POINT": "only index up",
       "THUMBS UP": "only thumb up", "HORNS": "index + pinky up, like rock music (not a fist)"}
SOURCES = ["mac", "main", "uw"]
NAMES = {"mac": "Mac camera", "main": "Phone main", "uw": "Phone ultra-wide",
         "phone2": "Phone 2 cams", "vote3": "3 cams, old vote", "fused": "All 3 fused"}
CONNECT = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
           (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]


# ---------------------------------------------------------------- sign classifier
FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
CHAINS = {"index": (5, 6, 7, 8), "middle": (9, 10, 11, 12), "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
# 1 = finger out, 0 = folded, None = either
TEMPLATES = {"FIST": (0, 0, 0, 0, 0), "OPEN PALM": (None, 1, 1, 1, 1), "PEACE": (None, 1, 1, 0, 0),
             "POINT": (None, 1, 0, 0, 0), "THUMBS UP": (1, 0, 0, 0, 0), "HORNS": (None, 1, 0, 0, 1)}


def _ang(a, b):
    c = np.dot(a, b) / max(1e-9, np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def _sig(x):
    return float(1 / (1 + np.exp(-x)))


def finger_probs(world):
    """Chance each finger is out (0..1), from 3D world landmarks (metres, hand-centred), so it doesn't
    depend on where the hand is in the picture or how far away it is.
    Fingers: total bend at the two middle joints (straight ~0-40 deg, folded ~150+ deg).
    Thumb: how far its tip is from the index/middle fingers, relative to palm size."""
    p = np.array([[l.x, l.y, l.z] for l in world])
    palm = max(1e-4, float(np.linalg.norm(p[0] - p[9])))
    out = []
    tip_gap = min(np.linalg.norm(p[4] - p[k]) for k in (5, 6, 9, 10)) / palm
    thumb_bend = _ang(p[3] - p[2], p[4] - p[3])
    out.append(_sig((tip_gap - 0.5) / 0.08) * _sig((60 - thumb_bend) / 15))
    for f in FINGERS[1:]:
        a, b, c, d = CHAINS[f]
        bend = _ang(p[b] - p[a], p[c] - p[b]) + _ang(p[c] - p[b], p[d] - p[c])
        reach = np.linalg.norm(p[d] - p[0]) / max(1e-4, np.linalg.norm(p[a] - p[0]))
        out.append(_sig((90 - bend) / 18) * _sig((reach - 1.25) / 0.1))
    return np.array(out)


def sign_from(probs):
    """(best sign or "?", confidence) - each sign's chance = product over the fingers it cares about."""
    best, bp = "?", 0.0
    for sign, tmpl in TEMPLATES.items():
        q = 1.0
        for pf, want in zip(probs, tmpl):
            if want is not None:
                q *= pf if want else 1 - pf
        if q > bp:
            best, bp = sign, q
    return (best if bp > 0.35 else "?"), bp


def classify(world):
    return sign_from(finger_probs(world))[0]


TRUST_PATH = os.path.join(HERE, "logs", "trust.json")


def learn_trust(log_dir=os.path.join(HERE, "logs")):
    """How often each view (Mac / phone) is right about each finger, learned from every Sign Check game
    played so far: the game knows which sign was asked, so it knows each finger's true state.
    Saved to logs/trust.json and used by the fusion next game."""
    ok = {"mac": np.zeros(5), "phone": np.zeros(5)}
    n = {"mac": np.zeros(5), "phone": np.zeros(5)}
    files = sorted(f for f in os.listdir(log_dir) if f.startswith("signs_") and f.endswith(".csv"))
    used, skipped = [], []
    for name in files:
        with open(os.path.join(log_dir, name)) as f:
            rows = [r for r in csv.DictReader(f) if r.get("state") == "hold" and r.get("target") in TEMPLATES
                    and "mac_p" in r]
        # Only learn from games where the player actually made the signs: if not even one camera got
        # a third of the frames right, the labels are not trustworthy (e.g. the hand stayed open all game).
        hits = sum(any(r.get(f"{v}_sign") == r["target"] for v in SOURCES) for r in rows)
        if not rows or hits < 0.35 * len(rows):
            skipped.append(name)
            continue
        used.append(name)
        for r in rows:
                tmpl = TEMPLATES[r["target"]]
                views = {"mac": [r["mac_p"]], "phone": [r["main_p"], r["uw_p"]]}
                for v, cols in views.items():
                    ps = [np.array([float(x) for x in c.split()]) for c in cols if c]
                    if not ps:
                        continue
                    p = np.mean(ps, 0)
                    for i, want in enumerate(tmpl):
                        if want is not None:
                            n[v][i] += 1
                            ok[v][i] += (p[i] > 0.5) == bool(want)
    trust = {v: ((ok[v] + 1) / (n[v] + 2)).round(3).tolist() for v in ok}
    trust["frames"] = int(n["phone"].max())
    trust["games_used"], trust["games_skipped"] = used, skipped
    with open(TRUST_PATH, "w") as f:
        json.dump(trust, f, indent=2)
    return trust


def load_trust():
    try:
        with open(TRUST_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


NUM_HANDS = 1      # speed test / Air Buttons set 2 and follow the biggest (closest) hand


def make_tracker():
    from mediapipe.tasks.python import BaseOptions, vision
    return vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=MODEL), running_mode=vision.RunningMode.VIDEO,
        num_hands=NUM_HANDS, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))


def detect(tracker, img, ts_ms):
    """-> dict(sign, score, lms[(u,v)] normalised) or None if no hand."""
    import mediapipe as mp
    res = tracker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                            data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), int(ts_ms))
    if not res.hand_landmarks:
        return None
    def span(h):
        return max(max(l.x for l in h) - min(l.x for l in h), max(l.y for l in h) - min(l.y for l in h))
    i = max(range(len(res.hand_landmarks)), key=lambda j: span(res.hand_landmarks[j]))   # closest hand
    score = res.handedness[i][0].score if len(res.handedness) > i else 0.5
    lms = [(l.x, l.y) for l in res.hand_landmarks[i]]
    xs, ys = [u for u, _ in lms], [v for _, v in lms]
    return {"probs": finger_probs(res.hand_world_landmarks[i]), "score": float(score), "lms": lms,
            "size": float(max(max(xs) - min(xs), max(ys) - min(ys)))}   # hand size, share of the frame


def to_unrotated(u, v, k, W, H):
    """Normalised point in a view rotated by viewer.rot(img, k) -> pixel in the original frame."""
    k %= 4
    if k == 0:
        return u * W, v * H
    if k == 1:
        return v * W, (1 - u) * H
    if k == 2:
        return (1 - u) * W, (1 - v) * H
    return (1 - v) * W, u * H


def resize_w(img, w):
    h = int(round(img.shape[0] * w / img.shape[1]))
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- Mac camera
class MacCam(threading.Thread):
    def __init__(self, cap):
        super().__init__(daemon=True)
        self.cap = cap
        self.lock = threading.Lock()
        self.latest = None          # (t, det, frame)
        self.error = None
        self.ready = threading.Event()
        self.running = True
        self.fps = 0.0

    def run(self):
        cap = self.cap
        tr = make_tracker()
        times = collections.deque(maxlen=30)
        last_ms = 0
        while self.running:
            ok, f = cap.read()
            if not ok:
                time.sleep(0.02)
                continue
            t = time.time()
            small = resize_w(f, PW)
            ms = max(last_ms + 1, int(t * 1000))
            last_ms = ms
            det = detect(tr, small, ms)
            if det:
                h, w = small.shape[:2]
                det["palm_px"] = float(np.hypot((det["lms"][0][0] - det["lms"][9][0]) * w,
                                                (det["lms"][0][1] - det["lms"][9][1]) * h))
            times.append(t)
            self.fps = (len(times) - 1) / max(1e-3, times[-1] - times[0]) if len(times) > 1 else 0
            with self.lock:
                self.latest = (t, det, small)
            self.ready.set()
        cap.release()

    def get(self):
        with self.lock:
            return self.latest


# ---------------------------------------------------------------- game
class Game:
    def __init__(self, a):
        self.a = a
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(V.LOG_DIR, exist_ok=True)
        self.base = os.path.join(V.LOG_DIR, f"signs_{stamp}")
        self.log = V.EventLog(self.base + ".log")
        self.rot = a.rot
        self.trials = [s for _ in range(a.rounds) for s in random.sample(SIGNS, len(SIGNS))]
        self.state, self.ti, self.t_state = "intro", -1, time.time()
        self.rows, self.score, self.streak = [], 0, 0
        self.results = None
        self.msg = ""
        self.last = {s: None for s in SOURCES}
        self.uw_last_ms = 0
        self.main_last_ms = 0

    # -- setup
    def start(self):
        a = self.a
        self.mac = None
        if not a.no_mac:
            cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
            for _ in range(20):          # first open may wait for the permission answer
                if cap.isOpened():
                    break
                time.sleep(0.5)
                cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
            if not cap.isOpened():
                self.log("Mac camera not allowed - System Settings > Privacy & Security > Camera", show=True)
                cap = None
            else:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        if not a.no_mac and cap is not None:
            self.mac = MacCam(cap)
            self.mac.start()
            self.mac.ready.wait(10)
            if self.mac.error:
                self.log(self.mac.error)
                self.mac = None
        self.phone = None
        if not a.host:     # adb mode; with --host the PhoneStereo app on the phone is already streaming
            self.phone = V.Phone(self.log, types.SimpleNamespace(camera="4", size=a.size, quality=a.quality,
                                                                 focus=a.focus))
            self.phone.start()
            # On Wi-Fi adb the phone isn't plugged in, so "stay on while on USB" doesn't keep it awake.
            subprocess.run(["adb", "shell", "svc", "power", "stayon", "true"], capture_output=True)
        self.rx = V.Receiver(self.log, host=a.host or "127.0.0.1", token=a.token)
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            raise RuntimeError(f"no stream from phone: {self.rx.error}")
        h = self.rx.header
        self.W, self.H = h["width"], h["height"]
        self.H0 = V.factory_homography(h["cameras"], self.W, self.H)
        self.tr_main, self.tr_uw = make_tracker(), make_tracker()
        # The two phone trackers run side by side (MediaPipe works outside the Python lock).
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(2)
        self.smooth = {}
        self.trust = load_trust()
        if self.trust:
            self.log(f"camera trust per finger (thumb..pinky) from {self.trust.get('frames')} past frames: "
                     f"mac {self.trust['mac']}  phone {self.trust['phone']}")
        self.last_ts0 = -1
        self.t0 = time.time()

    # -- per phone pair
    def pick(self):
        with self.rx.lock:
            mains, uws = list(self.rx.frames[0]), list(self.rx.frames[1])
        if not mains or not uws or mains[-1]["ts"] <= self.last_ts0:
            return None
        f0 = mains[-1]
        f1 = min(uws, key=lambda f: abs(f["ts"] - f0["ts"]))
        if abs(f1["ts"] - f0["ts"]) > 8e6 and time.time() - f0["t_recv"] < 0.1:
            return None
        self.last_ts0 = f0["ts"]
        return f0, f1

    def process(self, f0, f1):
        now = time.time()
        main = resize_w(V.rot(f0["img"], self.rot), PW)
        uw = resize_w(V.rot(f1["img"], self.rot), PW)
        ms = max(self.main_last_ms + 1, f0["ts"] // 1_000_000)
        self.main_last_ms = ms
        ms1 = max(self.uw_last_ms + 1, f1["ts"] // 1_000_000)
        self.uw_last_ms = ms1
        jm = self.pool.submit(detect, self.tr_main, main, ms)
        ju = self.pool.submit(detect, self.tr_uw, uw, ms1)
        dm, du = jm.result(), ju.result()
        dmac, mac_img = None, None
        if self.mac:
            got = self.mac.get()
            if got:
                tm, dmac, mac_img = got
                if now - tm > 0.15:
                    dmac = None
        dets = {"mac": dmac, "main": dm, "uw": du}

        # Depth: stereo shift of the hand between the two phone lenses (median over 21 landmarks)
        shift, palm_px = None, None
        if dm:
            pm = np.array([to_unrotated(u, v, self.rot, self.W, self.H) for u, v in dm["lms"]])
            palm_px = float(np.linalg.norm(pm[0] - pm[9]))
            if du:
                pu = np.array([to_unrotated(u, v, self.rot, self.W, self.H) for u, v in du["lms"]])
                pu_h = np.c_[pu, np.ones(len(pu))] @ self.H0.T
                pu_m = pu_h[:, :2] / pu_h[:, 2:]
                shift = np.median(pm - pu_m, axis=0)

        for s in SOURCES:          # smooth each camera's finger estimates over ~3 frames
            d = dets[s]
            if d is None:
                self.smooth.pop(s, None)
                continue
            prev = self.smooth.get(s)
            d["probs"] = d["probs"] if prev is None else 0.55 * d["probs"] + 0.45 * prev
            self.smooth[s] = d["probs"]
            d["sign"], d["conf"] = sign_from(d["probs"])
        vote3 = self.vote([dets[s] for s in SOURCES])
        phone2 = self.fuse([dm, du], [])
        fused = self.fuse([dm, du], [dmac])
        self.last = dict(dets, fused=fused, phone2=phone2, vote3=vote3)
        self.views = {"mac": mac_img, "main": main, "uw": uw}
        self.step(now)
        target = self.trials[self.ti] if self.state in ("ready", "hold") else ""
        self.rows.append({
            "t": round(now - self.t0, 3), "state": self.state, "trial": self.ti, "target": target,
            **{f"{s}_sign": (dets[s]["sign"] if dets[s] else "-") for s in SOURCES},
            **{f"{s}_score": (round(dets[s]["score"], 3) if dets[s] else "") for s in SOURCES},
            "phone2_sign": phone2, "vote3_sign": vote3, "fused_sign": fused,
            **{f"{s}_p": (" ".join(f"{x:.2f}" for x in dets[s]["probs"]) if dets[s] else "") for s in SOURCES},
            "shift_x": (round(float(shift[0]), 2) if shift is not None else ""),
            "shift_y": (round(float(shift[1]), 2) if shift is not None else ""),
            "palm_px": (round(palm_px, 2) if palm_px else ""),
            "mac_palm_px": (round(dmac["palm_px"], 2) if dmac else ""),
            "main_fps": round(self.rx.fps(0), 1), "mac_fps": round(self.mac.fps, 1) if self.mac else "",
            "frame_age_ms": int((now - f0["t_recv"]) * 1000)})

    def fuse(self, phone, others):
        """Finger-level fusion. The two phone lenses sit 1 cm apart (same viewpoint), so they are averaged
        into ONE view. Then each finger is read from the view that has been right most often about that
        finger in past games (logs/trust.json): e.g. the Mac camera for index-pinky, the phone for the thumb.
        Without a trust file yet, views are blended by tracker score, hand size and certainty."""
        views = {}
        ph = [d for d in phone if d]
        if ph:
            views["phone"] = (np.mean([d["probs"] for d in ph], 0), np.mean([d["score"] for d in ph]),
                              max(d["size"] for d in ph))
        for d in others:
            if d:
                views["mac"] = (d["probs"], d["score"], d["size"])
        if not views:
            return "-"
        if self.trust:
            out = np.zeros(5)
            for i in range(5):
                best = max(views, key=lambda v: self.trust.get(v, [0.5] * 5)[i])
                out[i] = views[best][0][i]
            return sign_from(out)[0]
        num, den = np.zeros(5), np.zeros(5)
        for probs, score, size in views.values():
            w = score * min(1.0, 0.3 + size * 2.5) * (0.15 + np.abs(probs - 0.5) * 2)
            num += w * probs
            den += w
        return sign_from(num / np.maximum(den, 1e-6))[0]

    @staticmethod
    def vote(dets):
        w = collections.Counter()
        for d in dets:
            if d and d["sign"] != "?":
                w[d["sign"]] += d["score"]
        if w:
            return w.most_common(1)[0][0]
        return "?" if any(dets) else "-"

    # -- game flow
    def step(self, now):
        el = now - self.t_state
        if self.state == "intro" and el > self.a.auto_start:
            self.go("ready", now, next_trial=True)
        elif self.state == "ready" and el > 1.5:
            self.go("hold", now)
        elif self.state == "hold" and el > 3.0:
            rs = [r for r in self.rows if r["trial"] == self.ti and r["state"] == "hold"]
            ok = sum(r["fused_sign"] == self.trials[self.ti] for r in rs) > len(rs) / 2 if rs else False
            self.streak = self.streak + 1 if ok else 0
            self.score += (100 + 20 * min(self.streak, 5)) if ok else 0
            self.msg = ("NICE! +%d" % (100 + 20 * min(self.streak, 5))) if ok else "missed"
            if self.ti + 1 < len(self.trials):
                self.go("ready", now, next_trial=True)
            else:
                self.go("push", now)
        elif self.state == "push" and el > 8:
            self.go("tilt", now)
        elif self.state == "tilt" and el > 6:
            self.go("done", now)
            self.results = self.analyse()

    def go(self, state, now, next_trial=False):
        if next_trial:
            self.ti += 1
        self.state, self.t_state = state, now
        self.log(f"-> {state} {self.trials[self.ti] if 0 <= self.ti < len(self.trials) else ''}")

    # -- results
    def analyse(self):
        out = {"trials": len(self.trials), "score": self.score, "per_source": {}}
        hold = [r for r in self.rows if r["state"] == "hold"]
        for s in SOURCES + ["phone2", "vote3", "fused"]:
            key = f"{s}_sign"
            frames = sum(r[key] == r["target"] for r in hold)
            seen = sum(r[key] != "-" for r in hold)
            wins = 0
            ttr = []
            for i, tgt in enumerate(self.trials):
                rs = [r for r in hold if r["trial"] == i]
                if rs and collections.Counter(r[key] for r in rs).most_common(1)[0][0] == tgt:
                    wins += 1
                seq = [r for r in self.rows if r["trial"] == i and r["state"] in ("ready", "hold")]
                run = 0
                for r in seq:
                    run = run + 1 if r[key] == tgt else 0
                    if run == 3:
                        ttr.append(r["t"] - seq[0]["t"])
                        break
            out["per_source"][s] = {
                "frame_accuracy_pct": round(100 * frames / max(1, len(hold)), 1),
                "trials_correct": wins, "hand_seen_pct": round(100 * seen / max(1, len(hold)), 1),
                "median_time_to_recognise_s": round(float(np.median(ttr)), 2) if ttr else None}
        per_sign = {}
        for sign in SIGNS:
            rs = [r for r in hold if r["target"] == sign]
            per_sign[sign] = {s: round(100 * sum(r[f"{s}_sign"] == sign for r in rs) / max(1, len(rs)), 0)
                              for s in SOURCES + ["fused"]}
        out["per_sign_frame_accuracy_pct"] = per_sign
        out["depth"] = self.depth_stats()
        out["fps"] = {"phone_pairs": round(len(self.rows) / max(1e-3, self.rows[-1]["t"] - self.rows[0]["t"]), 1),
                      "mac": round(self.mac.fps, 1) if self.mac else None,
                      "median_frame_age_ms": int(np.median([r["frame_age_ms"] for r in self.rows]))}
        with open(self.base + "_summary.json", "w") as f:
            json.dump(out, f, indent=2)
        with open(self.base + ".csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            w.writeheader()
            w.writerows(self.rows)
        self.log("SUMMARY " + json.dumps(out))
        try:
            t = learn_trust()
            self.log(f"learned camera trust for next game: mac {t['mac']}  phone {t['phone']}")
        except Exception as e:
            self.log(f"trust learning failed: {e}")
        return out

    def depth_stats(self):
        def series(state, col):
            rs = [r for r in self.rows if r["state"] == state and r[col] != ""]
            return rs
        push = [r for r in self.rows if r["state"] == "push" and r["shift_x"] != ""]
        tilt = [r for r in self.rows if r["state"] == "tilt" and r["shift_x"] != ""]
        if len(push) < 15:
            return {"note": "not enough frames with the hand seen by both phone lenses during PUSH"}
        P = np.array([[r["shift_x"], r["shift_y"]] for r in push])
        c = P - P.mean(0)
        axis = np.linalg.svd(c, full_matrices=False)[2][0]
        stereo = P @ axis
        palm = np.array([r["palm_px"] for r in push], float)
        if np.corrcoef(stereo, palm)[0, 1] < 0:      # make "bigger = closer" for both
            axis, stereo = -axis, -stereo

        def quality(x):
            rng = np.percentile(x, 95) - np.percentile(x, 5)
            sm = np.convolve(x, np.ones(5) / 5, mode="same")
            jit = np.median(np.abs(x - sm)[2:-2]) if len(x) > 6 else np.nan
            return float(rng), float(jit)

        out = {}
        for name, x in (("stereo_depth", stereo), ("single_cam_hand_size", palm)):
            rng, jit = quality(x)
            out[name] = {"push_range": round(rng, 2), "jitter": round(jit, 3),
                         "signal_to_noise": round(rng / jit, 1) if jit > 0 else None}
        out["correlation_stereo_vs_hand_size"] = round(float(np.corrcoef(stereo, palm)[0, 1]), 2)
        if len(tilt) >= 10:
            T = np.array([[r["shift_x"], r["shift_y"]] for r in tilt]) @ axis
            tp = np.array([r["palm_px"] for r in tilt], float)
            for name, x, ref in (("stereo_depth", T, stereo), ("single_cam_hand_size", tp, palm)):
                tr = np.percentile(x, 95) - np.percentile(x, 5)
                pr = np.percentile(ref, 95) - np.percentile(ref, 5)
                out[name]["tilt_false_change_pct_of_push"] = round(100 * tr / max(1e-6, pr), 0)
        out["frames"] = {"push": len(push), "tilt": len(tilt)}
        return out

    # -- drawing
    def tile(self, img, det, src):
        if img is None:
            t = np.zeros((TH, TW, 3), np.uint8)
            cv2.putText(t, "no camera", (130, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (90, 90, 90), 2)
        else:
            t, s, ox, oy = V.fit_tile(img, TW, TH)
            if det:
                h, w = img.shape[:2]
                pts = [(int(u * w * s + ox), int(v * h * s + oy)) for u, v in det["lms"]]
                for a, b in CONNECT:
                    cv2.line(t, pts[a], pts[b], (0, 220, 255), 2, cv2.LINE_AA)
                for p in pts:
                    cv2.circle(t, p, 3, (255, 255, 255), -1, cv2.LINE_AA)
        target = self.trials[self.ti] if self.state in ("ready", "hold") else None
        sign = det["sign"] if det else "no hand"
        col = (80, 220, 80) if target and sign == target else (200, 200, 200) if det else (90, 90, 230)
        cv2.rectangle(t, (0, TH - 34), (TW, TH), (20, 20, 20), -1)
        cv2.putText(t, f"{NAMES[src]}: {sign}", (10, TH - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2, cv2.LINE_AA)
        return t

    def draw(self):
        top = np.full((130, TW * 3, 3), 25, np.uint8)
        bottom = np.full((150, TW * 3, 3), 18, np.uint8)
        v = getattr(self, "views", {})
        tiles = np.hstack([self.tile(v.get(s), self.last.get(s), s) for s in SOURCES])
        now = time.time()
        el = now - self.t_state
        put = lambda img, txt, x, y, sc=0.7, c=(235, 235, 235), th=2: cv2.putText(
            img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
        if self.state == "intro":
            put(top, "SIGN CHECK - 3 cameras", 20, 45, 1.1, (0, 220, 255), 3)
            put(top, "Phone on top of the laptop, both facing you. Show one hand in all 3 views.", 20, 85, 0.6)
            put(top, f"SPACE to start (auto in {max(0, self.a.auto_start - el):.0f}s)   O = rotate phone view", 20, 115, 0.6)
        elif self.state in ("ready", "hold"):
            tgt = self.trials[self.ti]
            put(top, f"{self.ti + 1}/{len(self.trials)}", 20, 40, 0.7, (150, 150, 150))
            put(top, tgt, 20, 95, 2.0, (0, 220, 255) if self.state == "hold" else (120, 120, 120), 4)
            put(top, HOW[tgt], 480, 95, 0.75)
            if self.state == "hold":
                frac = min(1, el / 3.0)
                cv2.rectangle(top, (0, 122), (int(TW * 3 * frac), 130), (0, 220, 255), -1)
            else:
                put(top, "get ready...", 480, 50, 0.7, (150, 150, 150))
            put(top, f"score {self.score}  streak {self.streak}  {self.msg}", 760, 40, 0.65, (80, 220, 80))
        elif self.state == "push":
            put(top, "DEPTH 1: PUSH", 20, 50, 1.1, (0, 220, 255), 3)
            put(top, "Open palm. Slowly push it toward the phone and back, twice.", 20, 95, 0.7)
            cv2.rectangle(top, (0, 122), (int(TW * 3 * min(1, el / 8)), 130), (0, 220, 255), -1)
        elif self.state == "tilt":
            put(top, "DEPTH 2: TILT", 20, 50, 1.1, (0, 220, 255), 3)
            put(top, "Keep the hand at the SAME distance - just tilt and turn it.", 20, 95, 0.7)
            cv2.rectangle(top, (0, 122), (int(TW * 3 * min(1, el / 6)), 130), (0, 220, 255), -1)
        elif self.state == "done":
            put(top, f"DONE - score {self.score}", 20, 50, 1.1, (80, 220, 80), 3)
            put(top, "Results saved. Q to quit.", 20, 95, 0.7)
        # bottom: live fused + depth, or results table
        if self.state == "done" and self.results:
            ps = self.results["per_source"]
            put(bottom, "accuracy (frames / trials):", 20, 28, 0.6, (150, 150, 150), 1)
            for i, s in enumerate(["mac", "main", "uw", "phone2", "vote3", "fused"]):
                put(bottom, f"{NAMES[s]}", 20 + i * 198, 60, 0.5, (200, 200, 200), 1)
                put(bottom, f"{ps[s]['frame_accuracy_pct']}%  {ps[s]['trials_correct']}/{len(self.trials)}",
                    20 + i * 198, 92, 0.7, (80, 220, 80) if s == "fused" else (235, 235, 235))
            d = self.results.get("depth", {})
            if "stereo_depth" in d:
                put(bottom, f"depth signal/noise: stereo {d['stereo_depth']['signal_to_noise']}  vs  hand size "
                            f"{d['single_cam_hand_size']['signal_to_noise']}   tilt false change: stereo "
                            f"{d['stereo_depth'].get('tilt_false_change_pct_of_push', '-')}%  vs  hand size "
                            f"{d['single_cam_hand_size'].get('tilt_false_change_pct_of_push', '-')}%",
                    20, 130, 0.5, (200, 200, 200), 1)
        else:
            r = self.rows[-1] if self.rows else {}
            put(bottom, f"FUSED: {self.last.get('fused', '-')}", 20, 50, 1.0, (80, 220, 80), 2)
            put(bottom, f"phone 2 cams: {self.last.get('phone2', '-')}", 20, 90, 0.6, (200, 200, 200), 1)
            sh = (f"stereo shift {np.hypot(r['shift_x'], r['shift_y']):.1f} px" if r.get("shift_x") != "" and r
                  else "stereo shift --")
            put(bottom, sh + f"   hand size {r.get('palm_px', '--')} px", 520, 50, 0.6, (200, 200, 200), 1)
            put(bottom, f"phone {r.get('main_fps', 0)} fps   mac {r.get('mac_fps', '-')} fps   "
                        f"lag {r.get('frame_age_ms', '-')} ms", 520, 90, 0.55, (150, 150, 150), 1)
        return np.vstack([top, tiles, bottom])

    # -- main loop
    def run(self):
        self.start()
        cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
        done_at = None
        try:
            while True:
                if self.rx.error:
                    raise RuntimeError(self.rx.error)
                pair = self.pick()
                if pair:
                    self.process(*pair)
                    cv2.imshow(WIN, self.draw())
                k = cv2.waitKey(1 if pair else 5) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k == ord(" ") and self.state == "intro":
                    self.go("ready", time.time(), next_trial=True)
                if k == ord("o"):
                    self.rot = (self.rot + 1) % 4
                if self.state == "done":
                    done_at = done_at or time.time()
                    if time.time() - done_at > self.a.linger:
                        break
                if not pair:
                    time.sleep(0.002)
        finally:
            if self.state != "done" and len(self.rows) > 30 and self.ti >= 1:
                self.results = self.analyse()
            self.rx.running = False
            if self.mac:
                self.mac.running = False
            if self.phone:
                self.phone.stop()
            cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=2, help="each round shows all 6 signs once")
    ap.add_argument("--size", default="960x720")
    ap.add_argument("--quality", type=int, default=70)
    ap.add_argument("--focus", type=float, default=0, help="diopters; 0 = autofocus")
    ap.add_argument("--rot", type=int, default=0, help="rotate phone views 90deg x N (O key in game)")
    ap.add_argument("--mac-cam", type=int, default=0)
    ap.add_argument("--no-mac", action="store_true")
    ap.add_argument("--host", help="phone IP (PhoneStereo app over Wi-Fi, no adb)")
    ap.add_argument("--token", help="pairing code shown in the phone app")
    ap.add_argument("--auto-start", type=float, default=45, help="start without SPACE after N s")
    ap.add_argument("--linger", type=float, default=25, help="keep the results on screen N s")
    a = ap.parse_args()
    try:
        Game(a).run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
