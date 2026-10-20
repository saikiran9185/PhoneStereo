#!/usr/bin/env python3
"""PianoTap: a virtual piano "projected" onto the desk mat, played with real depth.

The phone sits low at the far side of the mat looking at you, like a laser-keyboard projector.
1. Corners: rest a fingertip on each corner of the playing area (front-left, front-right,
   back-right, back-left) and hold still. That fixes the mat's own 2D layout: left-right and
   front-back, whatever way the camera sees it.
2. Mat: hands off; the ultra-wide is warped so the mat plane has zero disparity. Anything above the
   mat then shows extra disparity ("plane + parallax"): about f*B*h / (Z * camera height) for a
   point h above the mat, so the lower the phone, the bigger the signal.
3. Touch level: rest a fingertip on a key and hold still once.
4. Play: white keys in front, black keys at the back. Depth decides touched / not touched and how
   fast the finger came down, which sets the loudness.

Chords: every fingertip of both hands is tracked (up to 10), each with its own touch state, so
pressing several keys at once plays them all. Phone cameras only, unless --mac is given.

Third camera (--mac): the phone allows only one camera device at a time (its main + ultra-wide pair counts
as one), so the macro can't join. The Mac's FaceTime camera can: set the corners once and both
cameras learn the mat. Each camera then places the fingertip on the mat; on the mat they agree,
above it they split apart (a baseline of tens of cm, against the phone's 1 cm). That gap and the
phone's own depth are combined into one height.

Mouse: during corners you can also click them.   Keys: K redo corners  R relearn mat
T redo touch level  [ / ] lower / raise the touch line  M mirror camera view  O flip camera view  Q / Esc quit
"""
import argparse
import collections
import csv
import datetime
import json
import os
import sys
import threading
import time

import cv2
import numpy as np

import viewer as V

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "hand_landmarker.task")
KEYS_PATH = os.path.join(HERE, "piano_keys.json")
WIN = "PianoTap - virtual piano on the mat"
PW, PH = 640, 480                 # camera view (processing size)
TW, TH = 400, 200                 # player's top view
MW = 640                          # Mac camera processing width
SH = 220                          # height of the Mac camera strip under the main views
FINGERS = {"thumb": (4, 3), "index": (8, 7), "middle": (12, 11), "ring": (16, 15), "pinky": (20, 19)}
SR = 44100
KEY_COOLDOWN = 0.15               # s; one tap can't play the same key twice
DWELL_S, DWELL_PX = 1.0, 6.0      # fingertip held this still this long = "here"
BLACK_DEPTH = 0.55                # black keys cover the back 55% of the mat, as on a piano
WINDOW_S = 30.0                   # s of fingertip heights the touch line is learned from
MIN_SAMPLES = 150                 # fingertip readings needed before trusting what was learned

WHITE = [("C", 261.63), ("D", 293.66), ("E", 329.63), ("F", 349.23),
         ("G", 392.00), ("A", 440.00), ("B", 493.88), ("C5", 523.25)]
BLACK = [("C#", 277.18, 1), ("D#", 311.13, 2), ("F#", 369.99, 4), ("G#", 415.30, 5), ("A#", 466.16, 6)]
CORNERS = ["FRONT-LEFT (near you, left)", "FRONT-RIGHT (near you, right)",
           "BACK-RIGHT (far side, right)", "BACK-LEFT (far side, left)"]
CORNER_UV = np.float32([[0, 1], [1, 1], [1, 0], [0, 0]])   # u: left->right, v: back(0)->front(1)


def build_keys():
    """Keys as rectangles in mat coordinates (u, v), black keys first so they win overlaps."""
    n = len(WHITE)
    keys = []
    for name, freq, b in BLACK:
        keys.append({"name": name, "freq": freq, "black": True,
                     "u0": (b - 0.3) / n, "u1": (b + 0.3) / n, "v0": 0.0, "v1": BLACK_DEPTH})
    for i, (name, freq) in enumerate(WHITE):
        keys.append({"name": name, "freq": freq, "black": False,
                     "u0": i / n, "u1": (i + 1) / n, "v0": 0.0, "v1": 1.0})
    return keys


KEYS = build_keys()


# ---------------------------------------------------------------- sound
def piano_tone(freq, dur=1.6):
    t = np.arange(int(SR * dur)) / SR
    wave = sum((0.6 ** (n - 1)) * np.sin(2 * np.pi * freq * n * t) * np.exp(-t * (2.2 + 1.2 * n))
               for n in range(1, 7))
    wave = wave * np.minimum(1.0, t / 0.004)
    return (0.5 * wave / np.max(np.abs(wave))).astype(np.float32)


def tick_tone():
    t = np.arange(int(SR * 0.06)) / SR
    return (0.25 * np.sin(2 * np.pi * 1400 * t) * np.exp(-t * 60)).astype(np.float32)


class Synth:
    """Tiny mixer: every note adds a voice; the audio callback sums whatever is still ringing."""

    def __init__(self, device=None):
        import sounddevice as sd
        self.tones = {k["name"]: piano_tone(k["freq"]) for k in KEYS}
        self.tones["tick"] = tick_tone()
        self.voices = []
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=SR, channels=1, dtype="float32", blocksize=256,
                                      latency="low", device=device, callback=self._cb)
        self.stream.start()
        self.latency_ms = self.stream.latency * 1000
        self.device = sd.query_devices(self.stream.device)["name"]

    def _cb(self, out, frames, _t, _status):
        mix = np.zeros(frames, np.float32)
        with self.lock:
            keep = []
            for v in self.voices:
                tone, pos, gain = v
                n = min(frames, len(tone) - pos)
                mix[:n] += gain * tone[pos:pos + n]
                v[1] += n
                if v[1] < len(tone):
                    keep.append(v)
            self.voices = keep
        out[:, 0] = np.clip(mix, -1, 1)

    def play(self, name, gain=1.0):
        with self.lock:
            self.voices.append([self.tones[name], 0, float(gain)])

    def close(self):
        self.stream.stop()
        self.stream.close()


# ---------------------------------------------------------------- touch signals
class Learner:
    """One height-like signal (phone stereo depth in px, or the phone-vs-Mac parallax). Learns from
    the spread of fingertip heights over the last WINDOW_S seconds: fingers spend part of the time on
    the mat and part lifted, so the low end (10th percentile) is the mat and the high end (90th) a
    normal lift, and the touch line sits halfway between, whatever the camera distance.
    Nothing here depends on the current touch line, so a bad start can't lock itself in (learning
    only from taps that crossed the line could, and twitching fingers in chords shrank it)."""

    def __init__(self, name, level=None):
        self.name = name
        self.level = level                 # where fingers rest on the mat
        self.lift = None                   # how high they rise between notes
        self.samples = collections.deque()  # (time, height)
        self.last_fit = 0.0
        self.announced_on = None

    def reset(self, level=None):
        self.__init__(self.name, level)

    def thresholds(self, bias=0.0):
        """Below `on` counts as touching; the finger must rise above `off` before it can play again."""
        if self.lift is None:
            on = self.level + 1.0
            return on + bias, on + bias + 1.0
        stroke = self.lift - self.level
        on = self.level + max(1.0, 0.5 * stroke) + bias
        return on, max(on + 1.0, self.level + 0.7 * stroke + bias)

    def frac(self, x):
        """0 on the mat, 1 at your usual lift; None until learned."""
        if self.lift is None or x is None:
            return None
        return (x - self.level) / (self.lift - self.level)

    def add(self, now, x):
        self.samples.append((now, x))

    def fit(self, now):
        """Refit about once a second; returns a message when the touch line moves noticeably."""
        if now - self.last_fit < 1.0:
            return None
        self.last_fit = now
        while self.samples and now - self.samples[0][0] > WINDOW_S:
            self.samples.popleft()
        if len(self.samples) < MIN_SAMPLES:
            return None
        x = np.array([v for _, v in self.samples])
        level, lift = float(np.percentile(x, 10)), float(np.percentile(x, 90))
        if lift - level < 1.0:
            return None                  # fingers haven't moved up and down enough to tell
        self.level, self.lift = level, lift
        on = self.thresholds()[0]
        if self.announced_on is None or abs(on - self.announced_on) > 0.5:
            self.announced_on = on
            return f"{self.name}: touch line {on:.1f} (mat {level:.1f}, lift {lift:.1f})"
        return None


# ---------------------------------------------------------------- Mac camera
class MacCam(threading.Thread):
    """The Mac's FaceTime camera as a third view: grabs frames and finds index fingertips on its own
    thread, keeping a short history so each phone frame can be matched to the Mac frame taken at the
    same moment."""

    def __init__(self, index, log):
        super().__init__(daemon=True)
        self.index, self.log = index, log
        self.hist = collections.deque(maxlen=150)    # (time, [(x, y), ...]) at MW x MH
        self.frame = None
        self.lock = threading.Lock()
        self.running = True
        self.error = None
        self.ready = threading.Event()
        self.size = (MW, MW * 3 // 4)
        self.fps = 0.0

    def run(self):
        from mediapipe.tasks.python import BaseOptions, vision
        import mediapipe as mp
        cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            self.error = ("not allowed or not found - System Settings > Privacy & Security > Camera > "
                          "turn on Terminal, then restart")
            self.ready.set()
            return
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL), running_mode=vision.RunningMode.VIDEO,
            num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        times = collections.deque(maxlen=30)
        fails = 0
        while self.running:
            ok, f = cap.read()
            t = time.time()
            if not ok:
                fails += 1
                if fails > 50:
                    self.error = "stopped delivering frames"
                    self.ready.set()
                    break
                time.sleep(0.02)
                continue
            fails = 0
            h, w = f.shape[:2]
            self.size = (MW, int(round(MW * h / w)))
            small = cv2.resize(f, self.size, interpolation=cv2.INTER_AREA)
            res = hands.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                  data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), int(t * 1000))
            tips = [(lms[8].x * self.size[0], lms[8].y * self.size[1]) for lms in res.hand_landmarks]
            times.append(t)
            self.fps = (len(times) - 1) / max(1e-3, times[-1] - times[0]) if len(times) > 1 else 0.0
            with self.lock:
                self.hist.append((t, tips))
                self.frame = small
            self.ready.set()
        cap.release()
        hands.close()

    def tips_at(self, t):
        """Fingertips from the Mac frame nearest to time t (None if no frame within 80 ms)."""
        with self.lock:
            hist = list(self.hist)
        if not hist:
            return None
        tt, tips = min(hist, key=lambda r: abs(r[0] - t))
        return tips if abs(tt - t) < 0.08 else None

    def history(self):
        with self.lock:
            return list(self.hist)


# ---------------------------------------------------------------- app
class PianoTap:
    def __init__(self, args):
        os.makedirs(V.LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log = V.EventLog(os.path.join(V.LOG_DIR, f"piano_{stamp}.log"))
        self.csv_path = os.path.join(V.LOG_DIR, f"piano_{stamp}.csv")
        self.args = args
        self.mirror = not args.no_mirror
        self.flipv = args.flip
        self.bias = 0.0                    # [ and ] move the touch line by hand
        self.depth = Learner("depth")      # phone stereo: px of extra disparity above the mat
        self.cross = Learner("2-camera")   # phone vs Mac: how far apart they place the fingertip (% of mat)
        self.fingers = tuple(f.strip() for f in args.fingers.split(",") if f.strip() in FINGERS) or ("index",)
        self.mac = None
        self.mac_corners = None
        self.G2 = None
        self.lag = 0.06                    # s; the phone's frames reach the Mac this much later than its own
        self.last_lag = 0.0
        self.sgbm = V.make_sgbm()
        self.clahe = cv2.createCLAHE(2.0, (8, 8))
        self.corners = self._load_corners()   # camera coords (640x480), order of CORNERS
        self.G = None
        self.clicks, self.mac_clicks = [], []
        self.dwell = collections.deque()
        self.mac_dwell = collections.deque(maxlen=60)
        self.last_capture = None
        self.tvals = []
        self._reset_surface()
        self.phase = "corners" if self.corners is None else "align"
        if self.corners is not None:
            self._set_corners(self.corners, self.mac_corners, save=False)
        self.slots = {}
        self.flash = {k["name"]: 0.0 for k in KEYS}
        self.taps = 0
        self.last_note = "-"
        self.events = collections.deque(maxlen=5)
        self.pair_times = collections.deque(maxlen=30)
        self.proc_ms = collections.deque(maxlen=30)
        self.phone_u = collections.deque(maxlen=120)   # (time, u) of the first fingertip, for syncing
        self.last_ts0 = -1
        self.cur_t = time.time()
        self.t0 = time.time()
        self.last_print = 0
        self.last_save = 0
        self.pairs = 0
        self.view = np.zeros((PH + SH, PW + TW, 3), np.uint8)

    # ---- state
    def _reset_surface(self):
        self.pool, self.prev_small, self.base_frames = [], None, []
        self.H, self.base, self.sign = None, None, None

    def _load_corners(self):
        try:
            with open(KEYS_PATH) as f:
                saved = json.load(f)
            self.depth.level = saved.get("touch_level")   # lift is relearned each session
            m = saved.get("mac_corners")
            self.mac_corners = np.float32(m) if m and len(m) == 4 else None
            c = saved.get("corners")
            return np.float32(c) if c and len(c) == 4 else None
        except Exception:
            return None

    def _save(self):
        with open(KEYS_PATH, "w") as f:
            json.dump({"corners": self.corners.tolist(), "touch_level": self.depth.level,
                       "mac_corners": None if self.mac_corners is None else self.mac_corners.tolist()}, f)

    def _set_corners(self, pts, mac_pts=None, save=True):
        self.corners = np.float32(pts)
        self.G = cv2.getPerspectiveTransform(self.corners, CORNER_UV)
        self.Ginv = np.linalg.inv(self.G)
        self.mac_corners = None if mac_pts is None else np.float32(mac_pts)
        self.G2 = None if mac_pts is None else cv2.getPerspectiveTransform(self.mac_corners, CORNER_UV)
        self.G2inv = None if mac_pts is None else np.linalg.inv(self.G2)
        self.cross.reset()
        if save:
            self._save()

    def say(self, msg):
        self.log(msg, show=True)
        self.events.appendleft(msg)

    # ---- coordinates: camera (as the phone sees) <-> display (flipped) <-> mat (u, v)
    def to_display(self, x, y):
        return (PW - 1 - x if self.mirror else x), (PH - 1 - y if self.flipv else y)

    def flip_image(self, img):
        if self.mirror and self.flipv:
            return cv2.flip(img, -1)
        if self.mirror or self.flipv:
            return cv2.flip(img, 1 if self.mirror else 0)
        return img.copy()

    def to_mat(self, x, y, G=None):
        p = cv2.perspectiveTransform(np.float32([[[x, y]]]), self.G if G is None else G)[0, 0]
        return float(p[0]), float(p[1])

    def mat_to_cam(self, uv, Ginv=None):
        return cv2.perspectiveTransform(np.float32(uv).reshape(-1, 1, 2),
                                        self.Ginv if Ginv is None else Ginv).reshape(-1, 2)

    def key_at(self, u, v):
        if not (0 <= u <= 1 and 0 <= v <= 1):
            return None
        for k in KEYS:
            if k["u0"] <= u < k["u1"] and k["v0"] <= v < k["v1"]:
                return k
        return None

    def mat_mask(self, grow=1.15):
        c = self.corners.mean(axis=0)
        poly = ((self.corners - c) * grow + c).astype(np.int32)
        m = np.zeros((PH, PW), np.uint8)
        cv2.fillPoly(m, [poly], 255)
        return m > 0

    # ---- setup + loop
    def run(self):
        from mediapipe.tasks.python import BaseOptions, vision
        import mediapipe as mp
        self.mp = mp
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL), running_mode=vision.RunningMode.VIDEO,
            num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        self.synth = Synth(self.args.audio)
        self.log(f"audio: {self.synth.device}, output latency {self.synth.latency_ms:.0f} ms")

        a = self.args
        if a.mac:
            self.mac = MacCam(a.mac_cam, self.log)
            self.mac.start()
            self.mac.ready.wait(10)
            if self.mac.error or not self.mac.ready.is_set():
                self.say(f"Mac camera off: {self.mac.error or 'no frames'}")
                self.mac = None
            else:
                self.log(f"Mac camera on ({self.mac.size[0]}x{self.mac.size[1]})")
        self.phone = V.Phone(self.log, argparse.Namespace(camera="4", size="1280x960", quality=80, focus=a.focus))
        self.phone.start()
        self.rx = V.Receiver(self.log)
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            raise RuntimeError(f"no stream from phone: {self.rx.error}")
        h = self.rx.header
        self.W, self.Hh = h["width"], h["height"]
        self.H0 = V.factory_homography(h["cameras"], self.W, self.Hh)
        if self.mac and self.corners is not None and self.mac_corners is None:
            # The saved corners were set with the phone alone; the Mac camera needs them too.
            self.phase, self.G = "corners", None
            self.say(f"set the corners again for both cameras: {CORNERS[0]} first")
        elif self.phase == "corners":
            self.say(f"rest a fingertip on the {CORNERS[0]} corner and hold still")
        else:
            self.say("using saved corners (K to redo) - hands OFF the mat")

        self.csv_f = open(self.csv_path, "w", newline="")
        self.csv = csv.writer(self.csv_f)
        self.csv.writerow(["time", "t_s", "pair", "finger", "cam_x", "cam_y", "u", "v", "delta_px", "mac_u", "mac_v",
                           "cross", "z", "mode", "on", "state", "key", "event", "speed", "gain"])
        if not a.headless:
            cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WIN, self._on_mouse)
        try:
            while True:
                if self.rx.error:
                    self._reconnect()
                    continue
                pair = self._pick()
                if pair is None:
                    if not a.headless and self._key(cv2.waitKey(3)):
                        return
                    time.sleep(0.002)
                    continue
                self._process(*pair)
                if not a.headless:
                    cv2.imshow(WIN, self.view)
                    if self._key(cv2.waitKey(1)):
                        return
                if a.duration and time.time() - self.t0 > a.duration:
                    return
        finally:
            self.rx.running = False
            if self.mac:
                self.mac.running = False
            self.phone.stop()
            self.synth.close()
            self.csv_f.close()
            self.log(f"session end - {self.taps} notes, log {self.csv_path}")
            if not a.headless:
                cv2.destroyAllWindows()

    def _reconnect(self):
        """Another app (face unlock, Camera) took the camera: keep everything learned and retry."""
        self.say(f"camera lost ({self.rx.error}) - reconnecting...")
        banner = self.view.copy()
        cv2.rectangle(banner, (0, PH // 2 - 30), (PW, PH // 2 + 30), (0, 0, 0), -1)
        cv2.putText(banner, "camera taken by another app - reconnecting", (20, PH // 2 + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2, cv2.LINE_AA)
        while True:
            if not self.args.headless:
                cv2.imshow(WIN, banner)
                if self._key(cv2.waitKey(500)):
                    raise KeyboardInterrupt
            else:
                time.sleep(0.5)
            if self.phone.proc.poll() is not None:   # streamer died: start a fresh one
                self.phone.start()
            self.rx = V.Receiver(self.log)
            self.rx.start()
            self.rx.header_ready.wait(5)
            if not self.rx.error and self.rx.header:
                time.sleep(1.0)
                if not self.rx.error:
                    self.last_ts0 = -1
                    self.say("camera back")
                    return

    def _on_mouse(self, event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and self.phase == "corners" and x < PW and y < PH:
            self._corner_captured(*self.to_display(x, y))   # flips are their own inverse

    def _key(self, k):
        if k < 0:
            return False
        ch = chr(k & 0xFF).lower()
        if (k & 0xFF) == 27 or ch == "q":
            return True
        if ch == "k":
            self.phase, self.clicks, self.mac_clicks, self.G = "corners", [], [], None
            self._reset_surface()
            self.say(f"corners: rest a fingertip on the {CORNERS[0]} corner and hold still")
        elif ch == "r" and self.G is not None:
            self.phase = "align"
            self._reset_surface()
            self.say("relearning the mat - hands off")
        elif ch == "t" and self.phase == "play":
            self.phase, self.tvals = "touch", []
            self.say("rest a fingertip on a key and hold still")
        elif ch in "[]" and self.depth.level is not None:
            self.bias = float(np.clip(self.bias + (-0.5 if ch == "[" else 0.5), -4.0, 4.0))
            self.say(f"touch line moved {self.bias:+.1f} by hand")
        elif ch == " " and self.phase == "touch":
            self._finish_touch(1.0, "skipped - using default touch level 1.0 (T to redo)")
        elif ch == "m":
            self.mirror = not self.mirror
        elif ch == "o":
            self.flipv = not self.flipv
        return False

    def _pick(self):
        with self.rx.lock:
            mains, uws = list(self.rx.frames[0]), list(self.rx.frames[1])
        for f0 in reversed(mains):
            if f0["ts"] <= self.last_ts0:
                break
            for f1 in uws:
                if f1["ts"] == f0["ts"]:
                    self.last_ts0 = f0["ts"]
                    return f0, f1
        return None

    # ---- Mac camera
    def mac_tips(self):
        """Mac fingertips (Mac image coords) seen at the same moment as the current phone frame."""
        if self.mac is None:
            return None
        return self.mac.tips_at(self.cur_t - self.lag)

    def _estimate_lag(self, now):
        """Find the delay that best lines up left-right fingertip motion in the two cameras."""
        if self.mac is None or self.G2 is None or now - self.last_lag < 3 or len(self.phone_u) < 40:
            return
        self.last_lag = now
        P = np.array(self.phone_u)
        if np.std(P[:, 1]) < 0.03:
            return                       # the finger hasn't moved enough sideways to tell
        M = [(t, self.to_mat(*tips[0], self.G2)[0]) for t, tips in self.mac.history() if len(tips) == 1]
        if len(M) < 40:
            return
        M = np.array(M)
        best = None
        for lag in np.arange(-0.04, 0.26, 0.01):
            mu = np.interp(P[:, 0] - lag, M[:, 0], M[:, 1], left=np.nan, right=np.nan)
            ok = np.isfinite(mu)
            if ok.sum() < 30:
                continue
            a, b = np.diff(P[ok, 1]), np.diff(mu[ok])
            if a.std() < 1e-6 or b.std() < 1e-6:
                continue
            r = float(np.corrcoef(a, b)[0, 1])
            if best is None or r > best[1]:
                best = (float(lag), r)
        if best and best[1] > 0.6:
            old = self.lag
            self.lag = 0.7 * self.lag + 0.3 * best[0]
            if abs(self.lag - old) > 0.005:
                self.log(f"camera sync: phone is {self.lag * 1000:.0f} ms behind the Mac (match {best[1]:.2f})")

    # ---- corners
    def _corner_captured(self, x, y):
        recent = [(mx, my) for t, mx, my in self.mac_dwell if self.cur_t - t < DWELL_S]
        self.mac_clicks.append(tuple(np.median(np.float32(recent), axis=0)) if len(recent) >= 4 else None)
        self.clicks.append((x, y))
        self.last_capture = (x, y)
        self.dwell.clear()
        self.mac_dwell.clear()
        self.synth.play("tick")
        if len(self.clicks) < 4:
            seen = "" if self.mac is None or self.mac_clicks[-1] is not None else " (Mac camera didn't see it)"
            self.say(f"got it{seen} - now the {CORNERS[len(self.clicks)]} corner")
            return
        mac_ok = self.mac is not None and all(m is not None for m in self.mac_clicks)
        self._set_corners(self.clicks, self.mac_clicks if mac_ok else None)
        if self.mac is not None and not mac_ok:
            missed = sum(m is None for m in self.mac_clicks)
            self.say(f"Mac camera missed {missed} corner(s) - phone only (K to retry)")
        self.clicks, self.mac_clicks = [], []
        self.phase = "align"
        self._reset_surface()
        self.say(("corners set for both cameras" if mac_ok else "corners set") + " - take your hands OFF the mat")

    def _dwell(self, tips, now):
        """A fingertip held still for DWELL_S returns its position (camera coords)."""
        if not tips:
            self.dwell.clear()
            return None
        _, x, y, _ = tips[0]
        self.dwell.append((now, x, y))
        while self.dwell and now - self.dwell[0][0] > DWELL_S:
            self.dwell.popleft()
        pts = np.float32([(p[1], p[2]) for p in self.dwell])
        if now - self.dwell[0][0] < DWELL_S * 0.9 or len(pts) < 5:
            return None
        if np.max(np.linalg.norm(pts - pts.mean(axis=0), axis=1)) > DWELL_PX:
            return None
        return tuple(pts.mean(axis=0))

    def _dwell_progress(self, now):
        if len(self.dwell) < 2:
            return 0.0
        return min(1.0, (now - self.dwell[0][0]) / DWELL_S)

    # ---- mat surface
    def _learn_surface(self, main, uw, main_s):
        """Homography that puts the mat at zero disparity, then the residual disparity of the
        empty mat, so later frames measure only what is above it."""
        if self.phase == "align":
            small = cv2.resize(V.gray(main), (160, 120)).astype(np.float32)
            if self.prev_small is not None and float(np.mean(np.abs(small - self.prev_small))) > 4:
                self.pool = []
            self.prev_small = small
            if self.pairs % 2:
                return
            uw_pre = cv2.warpPerspective(uw, self.H0, (self.W, self.Hh))
            src, dst = V.match_points(main, uw_pre)
            mask = self.mat_mask()
            px = np.clip((dst[:, 0] * PW / self.W).astype(int), 0, PW - 1)
            py = np.clip((dst[:, 1] * PH / self.Hh).astype(int), 0, PH - 1)
            inside = mask[py, px]
            self.pool.append((src[inside], dst[inside]))
            if len(self.pool) < 6:
                return
            src = np.vstack([p[0] for p in self.pool])
            dst = np.vstack([p[1] for p in self.pool])
            self.pool = []
            Hm, m = cv2.findHomography(src, dst, cv2.RANSAC, 1.5) if len(src) >= 30 else (None, None)
            n = 0 if m is None else int(m.sum())
            if Hm is None or n < 30:
                self.say(f"only {n} mat points - keep hands off, light the mat")
                return
            self.H = Hm @ self.H0
            self.say(f"mat plane found ({n} points) - measuring the surface...")
            self.phase = "surface"
            return
        self.base_frames.append(self._disparity(main_s, uw))
        if len(self.base_frames) >= 12:
            stack = np.stack(self.base_frames)
            stack[stack < V.MIN_DISP + 1] = np.nan
            base = np.nanmedian(stack, axis=0)
            cover = np.isfinite(base[self.mat_mask(1.0)]).mean() * 100
            self.base = np.nan_to_num(base, nan=0.0).astype(np.float32)
            self.base_frames = []
            self.depth.reset(self.depth.level)
            if self.depth.level is None:
                self.phase, self.tvals = "touch", []
                self.say(f"mat learned ({cover:.0f}% measured) - rest a fingertip on a key, hold still")
            else:
                self.phase = "play"
                self.say(f"mat learned ({cover:.0f}% measured) - play!")

    def _disparity(self, main_s, uw):
        S = np.diag([PW / self.W, PH / self.Hh, 1.0])
        uw_w = cv2.warpPerspective(uw, S @ self.H, (PW, PH))
        d = self.sgbm.compute(self.clahe.apply(V.gray(main_s)), self.clahe.apply(V.gray(uw_w)))
        d = d.astype(np.float32) / 16.0
        d[(d < V.MIN_DISP + 1) | (d > V.MIN_DISP + V.NUM_DISP - 2)] = V.MIN_DISP - 1
        return d

    # ---- per frame
    def _fingertips(self, main_s, ts, hmap, fingers):
        """Fingertips as (key, x, y, height); key is hand + finger, e.g. "R-index", so each finger
        keeps its own touch state from frame to frame."""
        res = self.hands.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(main_s, cv2.COLOR_BGR2RGB)),
            int(ts // 1_000_000))
        tips, labels = [], []
        for i, lms in enumerate(res.hand_landmarks):
            label = res.handedness[i][0].category_name[:1] if res.handedness else "H"
            if label in labels:           # both hands guessed the same side: keep them apart
                label += str(i)
            labels.append(label)
            if hmap is not None and self.sign is None:
                vals = [hmap[int(l.y * PH), int(l.x * PW)] for l in lms
                        if 0 <= l.x < 1 and 0 <= l.y < 1 and np.isfinite(hmap[int(l.y * PH), int(l.x * PW)])]
                if len(vals) >= 10 and abs(np.median(vals)) > 1.0:
                    # The hand is above the mat, so its disparity tells which sign means "up".
                    self.sign = 1 if np.median(vals) > 0 else -1
                    self.log(f"height direction locked (sign {self.sign})")
            for name in fingers:
                ti, di = FINGERS[name]
                tip, dip = lms[ti], lms[di]
                tx, ty = tip.x * PW, tip.y * PH
                tips.append((f"{label}-{name}", tx, ty, self._depth_at(hmap, tx, ty, dip)))
        return tips

    def _depth_at(self, hmap, tx, ty, dip):
        """Height of the fingertip above the mat in px, or None if the depth there is unreadable."""
        if hmap is None or self.sign is None:
            return None
        # sample a little way back from the very tip, where the finger is solid; a small window so
        # neighbouring fingers and the mat between them don't leak in
        px, py = tx + 0.25 * (dip.x * PW - tx), ty + 0.25 * (dip.y * PH - ty)
        x0, y0 = int(px) - 5, int(py) - 5
        win = hmap[max(0, y0):max(0, y0 + 11), max(0, x0):max(0, x0 + 11)]
        win = win[np.isfinite(win)]
        if win.size < 8:
            return None
        h = self.sign * float(np.median(win))
        return h if -8 < h < 40 else None     # a bad match, not a finger (touches can read a few px below 0)

    def _process(self, f0, f1):
        t_start = time.time()
        now = t_start
        self.cur_t = f0["t_recv"]
        main, uw = f0["img"], f1["img"]
        main_s = cv2.resize(main, (PW, PH), interpolation=cv2.INTER_AREA)
        hmap = None
        mtips = self.mac_tips()
        if self.phase in ("align", "surface"):
            self._learn_surface(main, uw, main_s)
            tips = []
        else:
            if self.phase in ("touch", "play"):
                d = self._disparity(main_s, uw)
                hmap = np.where(d >= V.MIN_DISP + 1, d - self.base, np.nan)
            tips = self._fingertips(main_s, f0["ts"], hmap, self.fingers if self.phase == "play" else ("index",))
            if self.phase == "corners":
                if mtips is not None and len(mtips) == 1:
                    self.mac_dwell.append((self.cur_t, *mtips[0]))
                p = self._dwell(tips, now)
                if p and (self.last_capture is None or np.hypot(p[0] - self.last_capture[0],
                                                                p[1] - self.last_capture[1]) > 40):
                    self._corner_captured(*p)
            elif self.phase == "touch":
                self._touch_calibration(tips, now)
            else:
                self._update(tips, mtips, now)
                self._estimate_lag(now)
        self.pairs += 1
        self.pair_times.append(time.time())
        self.proc_ms.append((time.time() - t_start) * 1000)
        self._draw(main_s, hmap, tips, mtips, now)

    def _touch_calibration(self, tips, now):
        if not hasattr(self, "touch_t0") or self.touch_t0 is None:
            self.touch_t0 = now
        p = self._dwell(tips, now)
        h = tips[0][3] if tips else None
        self.touch_live = h
        if h is not None and len(self.dwell) > 3:
            self.tvals.append(h)
        if p and len(self.tvals) >= 8:
            self._finish_touch(float(np.median(self.tvals[-24:])), None)
        elif now - self.touch_t0 > 12:
            # Depth at the fingertip never came through: play anyway with a sensible default.
            self._finish_touch(1.0, "couldn't read fingertip depth - using default 1.0 (T to retry)")

    def _finish_touch(self, level, note):
        self.depth.reset(level)
        self._save()
        self.touch_t0 = None
        self.dwell.clear()
        self.synth.play("tick")
        self.phase = "play"
        self.say(note or f"touch level {level:.2f} px - play! (harder taps = louder)")

    def _height(self, h, c):
        """Combine the signals into one height: 0 = on the mat, 10 = your usual lift. The two-camera
        parallax counts double: its baseline is tens of cm against the phone's 1 cm.
        Returns (mode, z, on, off, stroke) or None while nothing is learned yet."""
        parts = [(f, w) for f, w in ((self.depth.frac(h), 1.0), (self.cross.frac(c), 2.0)) if f is not None]
        if parts:
            z = 10.0 * sum(f * w for f, w in parts) / sum(w for _, w in parts)
            return "fused", z, 5.0 + self.bias, 7.0 + self.bias, 10.0
        if h is not None and self.depth.level is not None:
            on, off = self.depth.thresholds(self.bias)
            return "depth", h, on, off, 5.0
        return None

    def _update(self, tips, mtips, now):
        mac_uv = [] if mtips is None or self.G2 is None else [self.to_mat(x, y, self.G2) for x, y in mtips]
        seen = set()
        synced = False
        for i, tx, ty, h in tips:
            seen.add(i)
            slot = self.slots.setdefault(i, {"h": None, "armed": False, "low": 0, "state": "?", "mode": None,
                                             "hist": collections.deque(maxlen=10)})
            u, v = self.to_mat(tx, ty)
            index = i.endswith("index")
            if index and not synced:
                self.phone_u.append((self.cur_t, u))
                synced = True
            # The Mac fingertip for this finger: the nearest one left-right (height shifts it mostly
            # front-back). On the mat both cameras put it at the same spot; above it they split.
            m = min(mac_uv, key=lambda p: abs(p[0] - u)) if mac_uv and index else None   # Mac tracks index tips
            m = m if m is not None and abs(m[0] - u) < 0.2 else None
            c = None if m is None else 100.0 * float(np.hypot(m[0] - u, m[1] - v))
            slot["mac_uv"] = m
            on_mat = -0.1 <= u <= 1.1 and -0.1 <= v <= 1.1
            for learner, x in ((self.depth, h), (self.cross, c)):
                if x is not None and on_mat:
                    learner.add(now, x)
            # where the finger lands: between the two cameras' guesses when both see it
            pu, pv = (u, v) if m is None else ((u + m[0]) / 2, (v + m[1]) / 2)
            key = self.key_at(pu, pv)
            event, speed, gain = "", "", ""
            hz = self._height(h, c)
            on = ""
            if hz is not None:
                mode, z, on, off, stroke = hz
                if slot["mode"] != mode:          # units changed: start smoothing afresh
                    slot.update(mode=mode, h=None, low=0)
                    slot["hist"].clear()
                slot["h"] = z if slot["h"] is None else 0.5 * slot["h"] + 0.5 * z
                slot["hist"].append((now, z))
                # Touch is judged on the raw value (two frames in a row) so short taps aren't
                # smoothed away; arming uses the smoothed value so noise can't re-arm a key.
                touching = z < on
                slot["low"] = slot["low"] + 1 if touching else 0
                slot["state"] = "TOUCH" if touching else "hover"
                # Re-arm only when the finger is really up: the middle of the last three readings,
                # so one bad frame (a wrong match in either camera) can't replay the key.
                if len(slot["hist"]) >= 3 and sorted(x for _, x in list(slot["hist"])[-3:])[1] > off:
                    slot["armed"] = True
                elif (slot["armed"] and slot["low"] >= 2 and key is not None
                      and now - self.flash[key["name"]] > KEY_COOLDOWN):
                    slot["armed"] = False
                    # How fast the fingertip came down (strokes per second) -> loudness.
                    recent = [(t, hh) for t, hh in slot["hist"] if now - t < 0.3]
                    t_top, z_top = max(recent, key=lambda p: p[1])
                    speed = max(0.0, (z_top - z) / max(0.04, now - t_top))
                    gain = float(np.clip(0.3 + 0.7 * speed / (8.0 * stroke), 0.3, 1.0))
                    self.synth.play(key["name"], gain)
                    self.taps += 1
                    self.flash[key["name"]] = now
                    self.last_note = key["name"]
                    event = f"TAP {key['name']}"
                    self.say(f"{key['name']}  ({'loud' if gain > 0.75 else 'soft' if gain < 0.45 else 'medium'}"
                             f"{', 2 cameras' if mode == 'fused' and c is not None else ''})")
            slot["uv"] = (pu, pv)
            slot["key"] = None if key is None else key["name"]
            slot["c"] = c
            self.csv.writerow([V.now_iso(), f"{now - self.t0:.3f}", self.pairs, i, f"{tx:.0f}", f"{ty:.0f}",
                               f"{u:.3f}", f"{v:.3f}", "" if h is None else f"{h:.3f}",
                               "" if m is None else f"{m[0]:.3f}", "" if m is None else f"{m[1]:.3f}",
                               "" if c is None else f"{c:.2f}", "" if slot["h"] is None else f"{slot['h']:.2f}",
                               slot["mode"] or "", "" if on == "" else f"{on:.2f}", slot["state"],
                               "" if key is None else key["name"], event,
                               "" if speed == "" else f"{speed:.1f}", "" if gain == "" else f"{gain:.2f}"])
        for learner in (self.depth, self.cross):
            msg = learner.fit(now)
            if msg:
                self.say(msg)
                if learner is self.depth:
                    self._save()
        for i in list(self.slots):
            if i not in seen:
                del self.slots[i]

    # ---- drawing
    def _key_color(self, k, now):
        if now - self.flash[k["name"]] < 0.25:
            return (60, 210, 255)
        return (40, 40, 40) if k["black"] else (235, 235, 235)

    def _ring(self, slot, base):
        """Hover ring radius: shrinks as the finger comes down."""
        z = slot.get("h")
        if z is None or slot.get("state") == "TOUCH":
            return base
        if slot.get("mode") == "fused":
            return int(base + min(30, 2.5 * max(0.0, z)))
        return int(base + min(30, 4 * max(0.0, z - (self.depth.level or 0))))

    def _draw_camera(self, main_s, tips, now):
        view = self.flip_image(main_s)
        if self.G is not None:
            overlay = view.copy()
            for k in sorted(KEYS, key=lambda k: k["black"]):         # whites first, blacks on top
                quad = self.mat_to_cam([[k["u0"], k["v0"]], [k["u1"], k["v0"]], [k["u1"], k["v1"]], [k["u0"], k["v1"]]])
                quad = np.int32([self.to_display(x, y) for x, y in quad])
                cv2.fillPoly(overlay, [quad], self._key_color(k, now))
                cv2.polylines(overlay, [quad], True, (90, 90, 90), 1, cv2.LINE_AA)
            view = cv2.addWeighted(overlay, 0.35, view, 0.65, 0)
        for x, y in self.clicks:
            cv2.circle(view, tuple(int(c) for c in self.to_display(x, y)), 7, (0, 255, 255), -1)
        for i, tx, ty, h in tips:
            slot = self.slots.get(i, {})
            p = tuple(int(c) for c in self.to_display(tx, ty))
            state = slot.get("state", "?")
            col = (0, 255, 0) if state == "TOUCH" else (0, 170, 255)
            if self.phase in ("corners", "touch"):
                prog = self._dwell_progress(now)
                cv2.ellipse(view, p, (16, 16), -90, 0, 360 * prog, (0, 255, 255), 3)
            cv2.circle(view, p, self._ring(slot, 7), col, -1 if state == "TOUCH" else 2)
        if self.phase == "corners":
            msg = f"touch + hold: {CORNERS[len(self.clicks)]}"
            cv2.rectangle(view, (0, 0), (PW, 30), (0, 0, 0), -1)
            cv2.putText(view, msg, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
        return view

    def _draw_top(self, main_s, now):
        """The player's view: the mat seen from above, front edge at the bottom, as you sit."""
        top = np.full((TH, TW, 3), 25, np.uint8)
        if self.G is None:
            cv2.putText(top, "player's view appears", (100, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1)
            cv2.putText(top, "after the 4 corners", (115, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (150, 150, 150), 1)
            return top
        S = np.diag([TW, TH, 1.0])
        top = (cv2.warpPerspective(main_s, S @ self.G, (TW, TH)) * 0.45).astype(np.uint8)
        for k in sorted(KEYS, key=lambda k: k["black"]):
            x0, x1 = int(k["u0"] * TW), int(k["u1"] * TW)
            y0, y1 = int(k["v0"] * TH), int(k["v1"] * TH)
            col = self._key_color(k, now)
            lit = now - self.flash[k["name"]] < 0.25
            if k["black"]:
                cv2.rectangle(top, (x0, y0), (x1, y1), col, -1)
                cv2.rectangle(top, (x0, y0), (x1, y1), (200, 200, 200), 1)
            else:
                ov = top.copy()
                cv2.rectangle(ov, (x0, y0), (x1, y1), col, -1)
                top = cv2.addWeighted(ov, 0.75 if lit else 0.35, top, 0.25 if lit else 0.65, 0)
                cv2.rectangle(top, (x0, y0), (x1, y1), (60, 60, 60), 1)
            cv2.putText(top, k["name"], (x0 + 4, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        (255, 255, 255) if k["black"] else (30, 30, 30), 1, cv2.LINE_AA)
        for slot in self.slots.values():
            if "uv" not in slot:
                continue
            u, v = slot["uv"]
            p = (int(u * TW), int(v * TH))
            touching = slot.get("state") == "TOUCH"
            col = (0, 255, 0) if touching else (0, 170, 255)
            m = slot.get("mac_uv")
            if m is not None:
                # where each camera puts the fingertip: they meet when it is on the mat
                q = (int(m[0] * TW), int(m[1] * TH))
                cv2.line(top, p, q, (255, 200, 120), 1, cv2.LINE_AA)
                cv2.drawMarker(top, q, (255, 200, 120), cv2.MARKER_SQUARE, 8, 2)
            cv2.circle(top, p, self._ring(slot, 5), col, -1 if touching else 2)
        cv2.putText(top, "you are here (front)", (TW // 2 - 70, TH - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (200, 200, 200), 1, cv2.LINE_AA)
        return top

    def _draw_mac(self, mtips, now):
        """The Mac camera's view with the keyboard as it sees it."""
        strip = np.full((SH, PW + TW, 3), 14, np.uint8)
        if self.mac is None:
            cv2.putText(strip, "Mac camera off (no camera permission for Terminal)", (12, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)
            return strip
        with self.mac.lock:
            frame = None if self.mac.frame is None else self.mac.frame.copy()
        if frame is not None:
            if self.G2 is not None:
                overlay = frame.copy()
                for k in sorted(KEYS, key=lambda k: k["black"]):
                    quad = self.mat_to_cam([[k["u0"], k["v0"]], [k["u1"], k["v0"]], [k["u1"], k["v1"]],
                                            [k["u0"], k["v1"]]], self.G2inv)
                    cv2.fillPoly(overlay, [np.int32(quad)], self._key_color(k, now))
                    cv2.polylines(overlay, [np.int32(quad)], True, (90, 90, 90), 1, cv2.LINE_AA)
                frame = cv2.addWeighted(overlay, 0.35, frame, 0.65, 0)
            for mc in self.mac_clicks:
                if mc is not None:
                    cv2.circle(frame, (int(mc[0]), int(mc[1])), 7, (0, 255, 255), -1)
            for x, y in mtips or []:
                cv2.circle(frame, (int(x), int(y)), 9, (255, 200, 120), 2)
            mw = int(round(SH * frame.shape[1] / frame.shape[0]))
            strip[:, :mw] = cv2.resize(frame, (mw, SH))
        else:
            mw = 0
        x0 = mw + 14
        cross = [f"{s['c']:.0f}" for s in self.slots.values() if s.get("c") is not None]
        lines = [("Mac camera (3rd view)", (255, 200, 120)),
                 (f"{self.mac.fps:.0f} fps, sync {self.lag * 1000:.0f} ms", (200, 200, 200)),
                 ("corners: " + ("both cameras" if self.G2 is not None else "phone only - press K"),
                  (130, 230, 130) if self.G2 is not None else (60, 200, 255)),
                 ("2-cam gap: " + (", ".join(cross) if cross else "--") + "  (small = on the mat)", (255, 255, 255)),
                 ("2-cam line: " + (f"{self.cross.thresholds()[0]:.0f} (lift {self.cross.lift:.0f})"
                                   if self.cross.lift is not None
                                   else "learning - play a little"), (210, 210, 210))]
        y = 26
        for text, col in lines:
            cv2.putText(strip, text, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
            y += 26
        cv2.putText(strip, "blue squares (top view) = where the Mac sees your fingertip; they meet your dot on touch",
                    (x0, SH - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (150, 150, 150), 1, cv2.LINE_AA)
        return strip

    def _draw(self, main_s, hmap, tips, mtips, now):
        cam = self._draw_camera(main_s, tips, now)
        top = self._draw_top(main_s, now)
        panel = np.full((PH - TH, TW, 3), 18, np.uint8)
        if hmap is not None:
            n = np.clip(np.nan_to_num((self.sign or 1) * hmap) / 6.0, 0, 1)
            hv = cv2.applyColorMap((n * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
            hv[~np.isfinite(hmap)] = 0
            panel[PH - TH - 120:, TW - 160:] = cv2.resize(self.flip_image(hv), (160, 120))
            cv2.putText(panel, "height map", (TW - 156, PH - TH - 106), cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        (255, 255, 255), 1, cv2.LINE_AA)
        fps = (len(self.pair_times) - 1) / max(1e-3, self.pair_times[-1] - self.pair_times[0]) \
            if len(self.pair_times) > 1 else 0
        step = {"corners": f"1/3 corners: touch + hold {len(self.clicks) + 1}/4",
                "align": "2/3 HANDS OFF - learning mat", "surface": "2/3 HANDS OFF - measuring",
                "touch": "3/3 rest a fingertip on a key (Space = skip)", "play": "PLAY"}[self.phase]
        down = sorted({s["key"] for s in self.slots.values() if s.get("state") == "TOUCH" and s.get("key")})
        seen = sum(1 for s in self.slots.values() if s.get("h") is not None)
        states = [f"{len(self.slots)} fingers ({seen} with depth), down: {' '.join(down) or '--'}"]
        if self.phase == "touch":
            tl = getattr(self, "touch_live", None)
            states = [f"depth at fingertip: {'none' if tl is None else f'{tl:.2f}'}"]
        d = self.depth
        lines = [(step, (60, 200, 255) if self.phase != "play" else (130, 230, 130)),
                 (f"finger: {', '.join(states) if states else '--'}", (255, 255, 255)),
                 ("depth: --" if d.level is None else
                  f"depth line {d.thresholds()[0]:.1f}  " +
                  (f"(mat {d.level:.1f}, lift {d.lift:.1f})" if d.lift is not None
                   else "(learning - play a little)"), (210, 210, 210)),
                 (f"notes {self.taps}   last {self.last_note}", (130, 230, 130)),
                 (f"{fps:.0f} fps {np.mean(self.proc_ms):.0f} ms", (150, 150, 150))]
        y = 22
        for text, col in lines:
            cv2.putText(panel, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.46, col, 1, cv2.LINE_AA)
            y += 21
        for msg in list(self.events)[:4]:
            cv2.putText(panel, msg[:30], (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (150, 150, 150), 1, cv2.LINE_AA)
            y += 16
        cv2.putText(panel, "K corners R mat T touch M/O flip Q", (8, PH - TH - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.33, (120, 120, 120), 1, cv2.LINE_AA)
        self.view = np.hstack([cam, np.vstack([top, panel])])
        if self.mac is not None:
            self.view = np.vstack([self.view, self._draw_mac(mtips, now)])

        if now - self.last_save > 2:
            cv2.imwrite(os.path.join(V.LOG_DIR, "latest_piano.jpg"), self.view)
            self.last_save = now
        if now - self.last_print > 2:
            self.last_print = now
            mac = "" if self.mac is None else f" mac {self.mac.fps:.0f}fps lag {self.lag * 1000:.0f}ms"
            print(f"[{now - self.t0:6.1f}s] {self.phase} {fps:4.1f}fps {np.mean(self.proc_ms):4.0f}ms{mac} "
                  f"notes {self.taps} depth {d.level} fingers {states}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Virtual piano projected onto the desk mat, played with stereo depth")
    ap.add_argument("--focus", type=float, default=4.0, help="main camera focus in diopters (4 = 25 cm)")
    ap.add_argument("--audio", default=None, help="output device name part, e.g. 'MacBook' for the built-in speakers")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--flip", action="store_true", help="start with the camera view upside down")
    ap.add_argument("--mac-cam", type=int, default=0, help="Mac camera index (0 = FaceTime HD)")
    ap.add_argument("--mac", action="store_true", help="add the Mac's FaceTime camera as a third view")
    ap.add_argument("--fingers", default="thumb,index,middle,ring,pinky",
                    help="which fingers play, e.g. 'index' or 'index,middle,ring'")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--duration", type=float, default=0)
    args = ap.parse_args()
    try:
        PianoTap(args).run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
