#!/usr/bin/env python3
"""StickyBeat: tap sticky notes on any table to play sounds, with only the phone's two cameras.

Lean the phone against the wall so it looks at the table. Stick notes (or paper squares) anywhere and
write a sound on each: kick, snare, hat, clap, tom, rim, cowbell, shaker; a chord like C, Am, G7, Fmaj7;
a single note like C4 or F#5; or the name of any .wav in sounds/ (e.g. write "dhol" for sounds/dhol.wav).
1. Scan: hands off. Learns the table (stereo plane + how it looks without hands, for shadows),
   finds the notes and reads what's written on them.
2. Tap: whatever you tap plays; harder taps are louder. Every fingertip of both hands counts.

Touch = two signals that learn their own touch line from your tapping:
- depth: the phone's two lenses see a fingertip above the table shifted against the table
- shadow: a fingertip touches the table exactly where it meets its own shadow; above it they part
When both say "down" a tap plays on the first frame, which keeps rhythm tight.
A lamp to one side gives the clearest shadows.

Mouse: click a note, type a sound, Enter (Esc cancels) | drag on empty table = add a box | right-click = remove
Keys: S rescan notes  R relearn the table  [ / ] lower / raise the touch line  M mirror  O flip  Q quit
"""
import argparse
import collections
import csv
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
import wave

import cv2
import numpy as np

import viewer as V
from piano import Learner, piano_tone

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "hand_landmarker.task")
PADS_PATH = os.path.join(HERE, "sticky_pads.json")
OCR = os.path.join(HERE, "build_ocr", "ocr")
SOUNDS_DIR = os.path.join(HERE, "sounds")
WIN = "StickyBeat - tap the notes"
PW, PH = 640, 480                 # processing size
SW = 320                          # side panel width
SR = 44100
PAD_COOLDOWN = 0.10               # s; one tap can't play the same note twice
SCAN_FRAMES = 8                   # still, hand-free frames for the table background and the notes
FINGERS = {"thumb": (4, 3), "index": (8, 7), "middle": (12, 11), "ring": (16, 15), "pinky": (20, 19)}
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
SHADOW_CAP = 40.0                 # px; "no shadow near the fingertip"
DEFAULT_ORDER = ["kick", "snare", "hat", "clap", "tom", "cowbell", "rim", "shaker", "C", "G", "Am", "F"]

# ---------------------------------------------------------------- sounds
DRUM_WORDS = {"kick": ["kick", "bass", "bassdrum", "boom", "dhum"], "snare": ["snare", "share", "snar"],
              "hat": ["hat", "hihat", "hh", "tss"], "clap": ["clap"], "tom": ["tom"], "rim": ["rim", "click"],
              "cowbell": ["cowbell", "bell", "cow"], "shaker": ["shaker", "shake"]}
NOTE_INDEX = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
CHORDS = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10),
          "dim": (0, 3, 6), "sus2": (0, 2, 7), "sus4": (0, 5, 7)}
RNG = np.random.default_rng(7)


def samples_available():
    try:
        return {os.path.splitext(f)[0].lower(): os.path.join(SOUNDS_DIR, f)
                for f in os.listdir(SOUNDS_DIR) if f.lower().endswith(".wav")}
    except FileNotFoundError:
        return {}


def parse_sound(text):
    """What's written on a note -> sound name (kick, Am, C4, file:dhol...) or None."""
    t = re.sub(r"[^A-Za-z0-9#]", "", text.replace("♯", "#").replace("♭", "b"))
    if not t:
        return None
    if t.lower() in samples_available():
        return "file:" + t.lower()
    m = re.fullmatch(r"([A-Ga-g])([#b]?)([1-6])", t)     # a note; "G7" is the chord
    if m:
        return m.group(1).upper() + m.group(2) + m.group(3)
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(maj7|m7|m|7|dim|sus2|sus4)?", t)
    if m:
        return m.group(1).upper() + m.group(2) + (m.group(3) or "")
    words = {w: d for d, ws in DRUM_WORDS.items() for w in ws}
    best = difflib.get_close_matches(t.lower(), list(words), n=1, cutoff=0.7)
    return words[best[0]] if best else None


def label(name):
    return name[5:] if name.startswith("file:") else name


def _norm(x, peak=0.7):
    return (peak * x / (np.max(np.abs(x)) + 1e-9)).astype(np.float32)


def _t(dur):
    return np.arange(int(SR * dur)) / SR


def _bright(noise, passes=2):
    """Crude high-pass: differences keep the hiss and drop the rumble."""
    for _ in range(passes):
        noise = np.diff(noise, prepend=0.0)
    return noise


def drum(name):
    if name == "kick":
        t = _t(0.45)
        f = 45 + 95 * np.exp(-t * 30)                        # the pitch drop is the thump
        return _norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
                     + 0.25 * RNG.standard_normal(t.size) * np.exp(-t * 400), 0.95)
    if name == "snare":
        t = _t(0.25)
        return _norm(0.8 * _bright(RNG.standard_normal(t.size), 1) * np.exp(-t * 18)
                     + 0.5 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 25))
    if name == "hat":
        t = _t(0.08)
        return _norm(_bright(RNG.standard_normal(t.size), 3) * np.exp(-t * 60), 0.45)
    if name == "clap":
        t = _t(0.3)
        env = sum(np.where(t >= d, np.exp(-(t - d) * 120), 0) for d in (0.0, 0.011, 0.023)) + \
            0.6 * np.where(t >= 0.03, np.exp(-(t - 0.03) * 14), 0)
        return _norm(_bright(RNG.standard_normal(t.size), 2) * env, 0.6)
    if name == "tom":
        t = _t(0.4)
        f = 105 + 70 * np.exp(-t * 18)
        return _norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9), 0.85)
    if name == "rim":
        t = _t(0.06)
        return _norm((np.sin(2 * np.pi * 1700 * t) + 0.6 * np.sin(2 * np.pi * 820 * t)) * np.exp(-t * 90), 0.5)
    if name == "cowbell":
        t = _t(0.35)
        sq = np.sign(np.sin(2 * np.pi * 540 * t)) + np.sign(np.sin(2 * np.pi * 800 * t))
        return _norm(sq * np.exp(-t * 12), 0.4)
    if name == "shaker":
        t = _t(0.15)
        return _norm(_bright(RNG.standard_normal(t.size), 3) * np.minimum(1, t / 0.015) * np.exp(-t * 30), 0.4)
    raise KeyError(name)


def midi_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def render(name):
    """Audio for a sound name; raises KeyError/ValueError for names it can't make."""
    if name in DRUM_WORDS:
        return drum(name)
    if name.startswith("file:"):
        path = samples_available()[name[5:]]
        with wave.open(path) as w:
            raw = w.readframes(w.getnframes())
            if w.getsampwidth() != 2:
                raise ValueError(f"{os.path.basename(path)}: only 16-bit WAV files work")
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
            if w.getframerate() != SR:
                n = int(len(x) * SR / w.getframerate())
                x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        return _norm(x, 0.8)
    m = re.fullmatch(r"([A-G])([#b]?)([1-6])?(maj7|m7|m|7|dim|sus2|sus4)?", name)
    if not m:
        raise KeyError(name)
    root = NOTE_INDEX[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    if m.group(3):                                         # single note, e.g. C4
        return piano_tone(midi_freq(12 * (int(m.group(3)) + 1) + root))
    notes = [48 + root] + [60 + root + i for i in CHORDS[m.group(4) or ""]]   # bass + chord
    out = np.zeros(int(SR * 1.8), np.float32)
    for k, n in enumerate(notes):                          # a quick strum
        tone = piano_tone(midi_freq(n))
        start = int(SR * 0.012 * k)
        out[start:start + len(tone)] += tone[:len(out) - start]
    return _norm(out, 0.7)


class Mixer:
    """Every hit adds a voice; the audio callback sums whatever is still ringing."""

    def __init__(self, device=None):
        import sounddevice as sd
        self.cache = {}
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
            self.voices = keep[-24:]
        out[:, 0] = np.clip(mix, -1, 1)

    def get(self, name):
        if name not in self.cache:
            self.cache[name] = render(name)
        return self.cache[name]

    def play(self, name, gain=1.0):
        tone = self.get(name)
        with self.lock:
            self.voices.append([tone, 0, float(gain)])

    def close(self):
        self.stream.stop()
        self.stream.close()


# ---------------------------------------------------------------- finding notes
def order_quad(q):
    """Corners clockwise from the top-left, so warps come out upright (before any 180 turn)."""
    q = np.float32(q)
    c = q.mean(axis=0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]
    return np.roll(q, -int(np.argmin(q.sum(axis=1))), axis=0)


def find_notes(img):
    """Sticky notes and paper squares: bright, evenly coloured, solid four-sided shapes that don't touch
    the image edge (walls and backgrounds usually do)."""
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    ycc = cv2.cvtColor(blur, cv2.COLOR_BGR2YCrCb)
    # Faces, necks and arms are one even colour too. Skin is orange-red (hue <= 20); pale yellow notes
    # share its Cr/Cb but not its hue.
    hue = hsv[..., 0]
    skin = ((ycc[..., 1] >= 130) & (ycc[..., 1] <= 175) & (ycc[..., 2] >= 95) & (ycc[..., 2] <= 130)
            & ((hue <= 20) | (hue >= 170)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.dilate(cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 20, 60), np.ones((2, 2), np.uint8)) > 0
    found = []

    def pad_from(c, m, kind):
        area = cv2.contourArea(c)
        if not 0.0015 * PW * PH < area < 0.12 * PW * PH:
            return None
        x, y, w, h = cv2.boundingRect(c)
        if x <= 2 or y <= 2 or x + w >= PW - 2 or y + h >= PH - 2:
            return None
        hull = cv2.convexHull(c)
        if area / max(1.0, cv2.contourArea(hull)) < 0.75:          # curled corners are fine
            return None
        short, long_ = sorted(cv2.minAreaRect(c)[1])
        if short < 8 or long_ > 7.5 * short:                       # window frames, table edges, cables
            return None
        region = np.zeros((PH, PW), np.uint8)
        cv2.drawContours(region, [c], -1, 255, -1)
        # A plain note is one colour; judge only its own pixels, so the writing on it doesn't count.
        own_px = (region > 0) & (m > 0)
        own = hsv[own_px]
        if kind == "note":
            angle = own[:, 0].astype(np.float32) * (np.pi / 90)
            if np.hypot(np.cos(angle).mean(), np.sin(angle).mean()) < 0.9:
                return None                                        # several hues: printed or patterned
            if skin[own_px].mean() > 0.5:
                return None
        elif own[:, 2].std() > 25:
            return None
        approx = cv2.approxPolyDP(hull, 0.04 * cv2.arcLength(hull, True), True).reshape(-1, 2)
        quad = approx if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(c))
        return {"poly": order_quad(quad), "kind": kind,
                "color": [int(v) for v in cv2.mean(img, mask=region)[:3]], "area": area}

    for mask, kind in (((s > 55) & (v > 120), "note"), ((s < 45) & (v > 185), "paper")):
        m = cv2.morphologyEx(mask.astype(np.uint8) * 255, cv2.MORPH_OPEN, k)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
        for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            pad = pad_from(c, m, kind)
            if pad:
                found.append(pad)
                continue
            if cv2.contourArea(c) < 0.003 * PW * PH:
                continue
            # Notes that touch or overlap merge into one blob: split it along the edges between them.
            region = np.zeros((PH, PW), np.uint8)
            cv2.drawContours(region, [c], -1, 255, -1)
            sub = ((region > 0) & (m > 0) & ~edges).astype(np.uint8) * 255
            sub = cv2.morphologyEx(sub, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            for c2 in cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                pad = pad_from(c2, m, kind)
                if pad:
                    found.append(pad)
    # a paper square inside a coloured note (or the other way round) is the same pad
    found.sort(key=lambda p: -p["area"])
    pads = []
    for p in found:
        c = p["poly"].mean(axis=0)
        if not any(cv2.pointPolygonTest(q["poly"].reshape(-1, 1, 2), (float(c[0]), float(c[1])), False) >= 0
                   for q in pads):
            pads.append(p)
    pads.sort(key=lambda p: float(p["poly"][:, 0].mean()))
    for p in pads:
        p.pop("area")
        p["name"] = None
    return pads


def inside(a, b):
    """Is the centre of polygon a inside polygon b?"""
    c = np.asarray(a, np.float32).mean(axis=0)
    return cv2.pointPolygonTest(np.asarray(b, np.float32).reshape(-1, 1, 2), (float(c[0]), float(c[1])), False) >= 0


# ---------------------------------------------------------------- app
class StickyBeat:
    def __init__(self, args):
        os.makedirs(V.LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log = V.EventLog(os.path.join(V.LOG_DIR, f"sticky_{stamp}.log"))
        self.csv_path = os.path.join(V.LOG_DIR, f"sticky_{stamp}.csv")
        self.args = args
        self.mirror = not args.no_mirror
        self.flipv = args.flip
        self.bias = 0.0
        self.depth = Learner("depth")      # px of extra disparity above the table
        self.shadow = Learner("shadow")    # px from the fingertip to its shadow
        self.pads = self._load_pads()
        self.selected, self.typed = None, ""
        self.drag = None
        # A hand above the table is always on the camera's side of it, so "up" has the same sign every
        # session (+1 on this phone). Hands resting on the table read ~0, so one frame can't decide.
        self.sign, self.sign_votes = 1, collections.deque(maxlen=60)
        self.sgbm = V.make_sgbm()
        self.clahe = cv2.createCLAHE(2.0, (8, 8))
        self.bg = None                     # the table without hands (float BGR) for shadows
        self.region = None                 # where the notes are; only fingertips here teach the touch line
        self._reset_scan()
        self.slots = {}
        self.taps = 0
        self.last_hit = "-"
        self.events = collections.deque(maxlen=5)
        self.pair_times = collections.deque(maxlen=30)
        self.proc_ms = collections.deque(maxlen=30)
        self.last_ts0 = -1
        self.t0 = time.time()
        self.last_print = self.last_save = self.last_nag = 0.0
        self.pairs = 0
        self.ocr_busy = False
        self.shadow_view = None
        self.view = np.zeros((PH, PW + SW, 3), np.uint8)

    # ---- pads on disk
    def _load_pads(self):
        try:
            with open(PADS_PATH) as f:
                pads = json.load(f)["pads"]
            for p in pads:
                p["poly"] = np.float32(p["poly"])
            return pads
        except Exception:
            return []

    def _save_pads(self):
        with open(PADS_PATH, "w") as f:
            json.dump({"pads": [{**{k: v for k, v in p.items() if not k.startswith("_")},
                                 "poly": np.asarray(p["poly"]).tolist()} for p in self.pads]}, f)

    def _reset_scan(self, rescan_pads=True):
        self.phase = "scan"
        self.rescan_pads = rescan_pads
        self.scan_frames, self.pool, self.prev_small, self.base_frames = [], [], None, []
        self.H, self.base, self.plane_fails = None, None, 0

    def say(self, msg):
        self.log(msg, show=True)
        self.events.appendleft(msg)

    # ---- coordinates: camera (as the phone sees) <-> display (flipped)
    def to_display(self, x, y):
        return (PW - 1 - x if self.mirror else x), (PH - 1 - y if self.flipv else y)

    def flip_image(self, img):
        if self.mirror and self.flipv:
            return cv2.flip(img, -1)
        if self.mirror or self.flipv:
            return cv2.flip(img, 1 if self.mirror else 0)
        return img.copy()

    def pad_at(self, x, y, margin=6.0):
        for i, p in enumerate(self.pads):
            if cv2.pointPolygonTest(p["poly"].reshape(-1, 1, 2), (float(x), float(y)), True) >= -margin:
                return i
        return None

    def next_default(self):
        used = {p["name"] for p in self.pads}
        return next((n for n in DEFAULT_ORDER if n not in used), DEFAULT_ORDER[len(self.pads) % len(DEFAULT_ORDER)])

    # ---- setup + loop
    def run(self):
        from mediapipe.tasks.python import BaseOptions, vision
        import mediapipe as mp
        self.mp = mp
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL), running_mode=vision.RunningMode.VIDEO,
            num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        self.mixer = Mixer(self.args.audio)
        self.log(f"audio: {self.mixer.device}, output latency {self.mixer.latency_ms:.0f} ms")
        if self.mixer.latency_ms > 80:
            self.say(f"audio lags {self.mixer.latency_ms:.0f} ms - for tight rhythm use --audio MacBook")
        for name in DRUM_WORDS:                       # render ahead so the first hits aren't late
            self.mixer.get(name)

        a = self.args
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
        self.say("hands OFF the table - scanning the notes")

        self.csv_f = open(self.csv_path, "w", newline="")
        self.csv = csv.writer(self.csv_f)
        self.csv.writerow(["time", "t_s", "pair", "finger", "tip_x", "tip_y", "depth_px", "shadow_px", "z",
                           "state", "pad", "sound", "event", "gain"])
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
            self.phone.stop()
            self.mixer.close()
            self.csv_f.close()
            self.log(f"session end - {self.taps} hits, log {self.csv_path}")
            if not a.headless:
                cv2.destroyAllWindows()

    def _reconnect(self):
        """Another app (face unlock, Camera) took the camera: keep everything learned and retry."""
        self.say(f"camera lost ({self.rx.error}) - reconnecting...")
        while True:
            if not self.args.headless:
                if self._key(cv2.waitKey(500)):
                    raise KeyboardInterrupt
            else:
                time.sleep(0.5)
            if self.phone.proc.poll() is not None:
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

    # ---- mouse + keys
    def _on_mouse(self, event, x, y, *_):
        if x >= PW or y >= PH:
            return
        cx, cy = self.to_display(x, y)               # flips are their own inverse
        if event == cv2.EVENT_LBUTTONDOWN:
            i = self.pad_at(cx, cy, 0)
            if i is not None:
                self.selected, self.typed = i, ""
            else:
                self.selected, self.drag = None, [(cx, cy), (cx, cy)]
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            self.drag[1] = (cx, cy)
        elif event == cv2.EVENT_LBUTTONUP and self.drag:
            (x0, y0), (x1, y1) = self.drag
            self.drag = None
            if abs(x1 - x0) > 15 and abs(y1 - y0) > 15:
                quad = order_quad([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
                self.pads.append({"poly": quad, "kind": "box", "color": [200, 200, 200], "name": None})
                self.pads[-1]["name"] = self.next_default()
                self.selected, self.typed = len(self.pads) - 1, ""
                self._save_pads()
                self.say(f"box added as {label(self.pads[-1]['name'])} - type a sound + Enter to change")
        elif event == cv2.EVENT_RBUTTONDOWN:
            i = self.pad_at(cx, cy, 0)
            if i is not None:
                self.say(f"removed {label(self.pads[i]['name'] or '?')}")
                del self.pads[i]
                self.selected = None
                self._save_pads()

    def _key(self, k):
        if k < 0:
            return False
        c = k & 0xFF
        if self.selected is not None:                # typing a sound for the clicked note
            if c in (13, 10):
                name = parse_sound(self.typed)
                if name:
                    try:
                        self.mixer.get(name)
                        self.pads[self.selected]["name"] = name
                        self._save_pads()
                        self.mixer.play(name)
                        self.say(f"note {self.selected + 1} = {label(name)}")
                    except (KeyError, ValueError) as e:
                        self.say(f"can't play {self.typed}: {e}")
                elif self.typed:
                    self.say(f"unknown sound '{self.typed}' - try kick, snare, Am, C4")
                self.selected = None
            elif c == 27:
                self.selected = None
            elif c in (8, 127):
                self.typed = self.typed[:-1]
            elif 32 < c < 127:
                self.typed += chr(c)
            return False
        ch = chr(c).lower()
        if c == 27 or ch == "q":
            return True
        if ch == "s":
            self._reset_scan(True)
            self.say("rescanning - hands OFF the table")
        elif ch == "r":
            self._reset_scan(False)
            self.say("relearning the table - hands OFF")
        elif ch in "[]":
            self.bias = float(np.clip(self.bias + (-0.5 if ch == "[" else 0.5), -4.0, 4.0))
            self.say(f"touch line moved {self.bias:+.1f} by hand")
        elif ch == "m":
            self.mirror = not self.mirror
        elif ch == "o":
            self.flipv = not self.flipv
        return False

    # ---- scanning: background, notes, the table plane, its leftover disparity
    def _scan(self, main, uw, main_s, hands_seen):
        if self.phase == "scan":
            small = cv2.resize(V.gray(main), (160, 120)).astype(np.float32)
            moving = self.prev_small is not None and float(np.mean(np.abs(small - self.prev_small))) > 4
            self.prev_small = small
            if moving or hands_seen:
                self.scan_frames, self.pool = [], []
                if hands_seen and time.time() - self.last_nag > 3:
                    self.last_nag = time.time()
                    self.say("hands OFF the table please")
                return
            self.scan_frames.append(main_s)
            self.scan_full = main
            if len(self.scan_frames) % 2 == 0:
                uw_pre = cv2.warpPerspective(uw, self.H0, (self.W, self.Hh))
                self.pool.append(V.match_points(main, uw_pre))
            if len(self.scan_frames) < SCAN_FRAMES:
                return
            self.bg = np.median(np.stack(self.scan_frames), axis=0).astype(np.float32)
            cv2.imwrite(os.path.join(V.LOG_DIR, "sticky_scan.png"), self.bg.astype(np.uint8))
            if self.rescan_pads:
                self._set_pads(find_notes(self.bg.astype(np.uint8)))
            self._fit_plane()
            self.phase = "surface"
            return
        self.base_frames.append(self._disparity(main_s, uw))
        if len(self.base_frames) >= 12:
            stack = np.stack(self.base_frames)
            stack[stack < V.MIN_DISP + 1] = np.nan
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)     # pixels never matched stay 0
                self.base = np.nan_to_num(np.nanmedian(stack, axis=0), nan=0.0).astype(np.float32)
            self.base_frames = []
            self.depth.reset()
            self.shadow.reset()
            self.phase = "play"
            self.say(f"ready - {len(self.pads)} notes. Tap a few times so it learns your touch")

    def _set_pads(self, found):
        """New scan: keep names of notes that didn't move, read the writing on the rest."""
        old = self.pads
        for p in found:
            c = p["poly"].mean(axis=0)
            near = [q for q in old if q.get("name") and np.linalg.norm(q["poly"].mean(axis=0) - c) < 30]
            if near:
                p["name"] = near[0]["name"]
        boxes = []
        for q in old:
            if q.get("kind") != "box":
                continue
            # a box drawn over a note the scan now sees: the note takes the box's sound, the box goes
            under = next((p for p in found if inside(q["poly"], p["poly"]) or inside(p["poly"], q["poly"])), None)
            if under is None:
                boxes.append(q)
            elif not under.get("name"):
                under["name"] = q.get("name")
        self.pads = found + boxes
        self.say(f"found {len(found)} notes" + (f" + {len(boxes)} boxes" if boxes else ""))
        if not found and not boxes:
            self.say("no notes seen - put them fully in view, or drag boxes with the mouse")
        elif found:
            # A square note seen from above stays square; seen from low down it squashes into a strip.
            flat = np.median([np.divide(*sorted(cv2.minAreaRect(p["poly"])[1])) for p in found])
            if flat < 0.4:
                self.say("notes look flat: lean the phone more (bottom away from the wall) to look down on them")
        unread = [p for p in self.pads if not p.get("name")]
        if unread and os.path.exists(OCR):
            threading.Thread(target=self._read_notes, args=(unread, self.scan_full.copy()), daemon=True).start()
        else:
            for p in unread:
                p["name"] = self.next_default()
            self._save_pads()
        r = np.zeros((PH, PW), np.uint8)
        if self.pads:
            pts = np.vstack([p["poly"] for p in self.pads])
            hull = cv2.convexHull(pts.astype(np.float32))
            c = hull.reshape(-1, 2).mean(axis=0)
            cv2.fillPoly(r, [((hull.reshape(-1, 2) - c) * 1.4 + c).astype(np.int32)], 255)
            r = cv2.dilate(r, np.ones((41, 41), np.uint8))
        else:
            r[PH // 3:] = 255
        self.region = r > 0

    def _read_notes(self, pads, full):
        """Straighten each note from the full-size frame and read it at all four turns (the text faces
        you, so the phone usually sees it upside down)."""
        self.ocr_busy = True
        try:
            d = os.path.join(V.LOG_DIR, "notes")
            os.makedirs(d, exist_ok=True)
            sx, sy = full.shape[1] / PW, full.shape[0] / PH
            paths = []
            for i, p in enumerate(pads):
                src = (p["poly"] * [sx, sy]).astype(np.float32)
                M = cv2.getPerspectiveTransform(src, np.float32([[0, 0], [255, 0], [255, 255], [0, 255]]))
                img = cv2.warpPerspective(full, M, (256, 256))
                variants = [img]
                # Also the writing alone, cropped and enlarged: a lone letter (a chord like "G") is only
                # recognised when it fills the picture.
                g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                ys, xs = np.where(g < np.median(g) - 50)
                if len(xs) > 30:
                    crop = img[max(0, ys.min() - 12):ys.max() + 12, max(0, xs.min() - 12):xs.max() + 12]
                    crop = cv2.resize(crop, None, fx=96 / crop.shape[0], fy=96 / crop.shape[0])
                    fill = [int(v) for v in np.median(img.reshape(-1, 3), axis=0)]
                    variants.append(cv2.copyMakeBorder(crop, 40, 40, 80, 80, cv2.BORDER_CONSTANT, value=fill))
                for k, v in enumerate(variants):
                    for r in range(4):
                        path = os.path.join(d, f"note{i}_v{k}_r{r}.png")
                        cv2.imwrite(path, np.rot90(v, r))
                        paths.append(path)
            words = sorted({w for ws in DRUM_WORDS.values() for w in ws} | set(samples_available())
                           | {"Am", "Em", "Dm", "G7", "C", "G", "F", "D", "E", "A"})
            out = subprocess.run([OCR, ",".join(words), *paths], capture_output=True, text=True, timeout=60).stdout
            reads = {}
            for line in out.splitlines():
                parts = line.split("\t")
                for cand in parts[1:]:
                    text, _, conf = cand.rpartition("|")
                    name = parse_sound(text)
                    i = int(re.search(r"note(\d+)_", parts[0]).group(1))
                    if name and float(conf) > reads.get(i, ("", -1))[1]:
                        reads[i] = (name, float(conf), text)
            for i, p in enumerate(pads):
                if i in reads:
                    p["name"] = reads[i][0]
                    self.say(f"read '{reads[i][2]}' -> {label(reads[i][0])}")
            for i, p in enumerate(pads):                 # defaults only once every readable name is known
                if i not in reads:
                    p["name"] = self.next_default()
                    self.say(f"couldn't read a note - it plays {label(p['name'])} (click it to change)")
            for p in pads:
                try:
                    self.mixer.get(p["name"])
                except (KeyError, ValueError) as e:
                    self.say(f"{label(p['name'])}: {e}")
                    p["name"] = self.next_default()
            self._save_pads()
        except Exception as e:
            self.say(f"reading notes failed: {e}")
            for p in pads:
                p["name"] = p.get("name") or self.next_default()
        finally:
            self.ocr_busy = False

    def _fit_plane(self):
        """Homography that lines the table up in both cameras, from matches near the notes. If the table
        has too little texture the factory alignment is used: the leftover disparity of the empty table
        is subtracted afterwards anyway, so only the precision suffers, not the result."""
        src = np.vstack([p[0] for p in self.pool]) if self.pool else np.zeros((0, 2), np.float32)
        dst = np.vstack([p[1] for p in self.pool]) if self.pool else np.zeros((0, 2), np.float32)
        if len(dst):
            px = np.clip((dst[:, 0] * PW / self.W).astype(int), 0, PW - 1)
            py = np.clip((dst[:, 1] * PH / self.Hh).astype(int), 0, PH - 1)
            keep = self.region[py, px]
            src, dst = src[keep], dst[keep]
        Hm, m = cv2.findHomography(src, dst, cv2.RANSAC, 1.5) if len(src) >= 30 else (None, None)
        n = 0 if m is None else int(m.sum())
        if Hm is not None and n >= 30:
            self.H = Hm @ self.H0
            self.log(f"table plane from {n} points")
        else:
            self.H = self.H0
            self.log(f"table has little texture ({n} points) - using factory alignment")

    def _disparity(self, main_s, uw):
        S = np.diag([PW / self.W, PH / self.Hh, 1.0])
        uw_w = cv2.warpPerspective(uw, S @ self.H, (PW, PH))
        d = self.sgbm.compute(self.clahe.apply(V.gray(main_s)), self.clahe.apply(V.gray(uw_w)))
        d = d.astype(np.float32) / 16.0
        d[(d < V.MIN_DISP + 1) | (d > V.MIN_DISP + V.NUM_DISP - 2)] = V.MIN_DISP - 1
        return d

    # ---- per frame
    def _detect(self, main_s, ts):
        res = self.hands.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(main_s, cv2.COLOR_BGR2RGB)),
            int(ts // 1_000_000))
        hands = []
        labels = []
        for i, lms in enumerate(res.hand_landmarks):
            lab = res.handedness[i][0].category_name[:1] if res.handedness else "H"
            if lab in labels:
                lab += str(i)
            labels.append(lab)
            hands.append((lab, np.float32([(l.x * PW, l.y * PH) for l in lms])))
        return hands

    def _hand_mask(self, hands):
        """Rough silhouette of the hands: thick bones plus the palm."""
        m = np.zeros((PH, PW), np.uint8)
        for _, P in hands:
            w = max(6, int(0.3 * np.linalg.norm(P[5] - P[17])))
            for a, b in BONES:
                cv2.line(m, tuple(int(v) for v in P[a]), tuple(int(v) for v in P[b]), 255, w)
            cv2.fillPoly(m, [P[[0, 1, 5, 9, 13, 17]].astype(np.int32)], 255)
            for ti, _ in FINGERS.values():
                cv2.circle(m, tuple(int(v) for v in P[ti]), w // 2 + 1, 255, -1)
        return m

    def _shadow_dist(self, main_s, hand):
        """Distance from every pixel to the nearest shadow: darker than the empty table but the same
        colour (a shadow dims a surface, skin changes its colour)."""
        f = main_s.astype(np.float32) + 1
        b = self.bg + 1
        ratio = f.sum(axis=2) / b.sum(axis=2)
        chroma = np.abs(f / f.sum(axis=2, keepdims=True) - b / b.sum(axis=2, keepdims=True)).sum(axis=2)
        sh = (ratio < 0.75) & (ratio > 0.12) & (chroma < 0.08) & (hand == 0)
        if self.region is not None:
            sh &= self.region                  # people moving in the background aren't shadows on the table
        sh = sh.astype(np.uint8)
        sh = cv2.morphologyEx(sh, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        self.shadow_view = sh
        return cv2.distanceTransform(1 - sh, cv2.DIST_L2, 3)

    def _process(self, f0, f1):
        t_start = now = time.time()
        main, uw = f0["img"], f1["img"]
        main_s = cv2.resize(main, (PW, PH), interpolation=cv2.INTER_AREA)
        hands = self._detect(main_s, f0["ts"])
        tips = []
        if self.phase in ("scan", "surface"):
            self._scan(main, uw, main_s, bool(hands) and self.phase == "scan")
        else:
            d = self._disparity(main_s, uw)
            hmap = np.where(d >= V.MIN_DISP + 1, d - self.base, np.nan)
            hand = self._hand_mask(hands)
            dist = self._shadow_dist(main_s, hand)
            if self.sign_votes is not None:
                self._vote_sign(hands, hmap)
            for lab, P in hands:
                w = max(4.0, 0.3 * float(np.linalg.norm(P[5] - P[17])))
                for name, (ti, di) in FINGERS.items():
                    tip, dip = P[ti], P[di]
                    tips.append((f"{lab}-{name}", float(tip[0]), float(tip[1]),
                                 self._depth_at(hmap, tip, dip), self._shadow_at(dist, tip, dip, w)))
            self._update(tips, now)
            if self.pairs % 5 == 0:
                # let the background follow slow light changes away from the hands and their shadows
                near = cv2.dilate(cv2.resize(hand, (160, 120), interpolation=cv2.INTER_NEAREST), np.ones((15, 15), np.uint8))
                far = cv2.resize(near, (PW, PH), interpolation=cv2.INTER_NEAREST) == 0
                self.bg[far] = 0.95 * self.bg[far] + 0.05 * main_s[far]
        self.pairs += 1
        self.pair_times.append(time.time())
        self.proc_ms.append((time.time() - t_start) * 1000)
        self._draw(main_s, hands, tips, now)

    def _vote_sign(self, hands, hmap):
        for _, P in hands:
            vals = [hmap[int(y), int(x)] for x, y in P if 0 <= x < PW and 0 <= y < PH and np.isfinite(hmap[int(y), int(x)])]
            if len(vals) >= 10:
                self.sign_votes.append(float(np.median(vals)))
        if len(self.sign_votes) == self.sign_votes.maxlen:
            m = float(np.median(self.sign_votes))
            if m < -2:
                self.sign = -1
            self.log(f"height direction checked over {len(self.sign_votes)} frames: median {m:+.1f} -> sign {self.sign}")
            self.sign_votes = None

    def _depth_at(self, hmap, tip, dip):
        p = tip + 0.25 * (dip - tip)                   # a little back from the tip, where the finger is solid
        x0, y0 = int(p[0]) - 5, int(p[1]) - 5
        win = hmap[max(0, y0):max(0, y0 + 11), max(0, x0):max(0, x0 + 11)]
        win = win[np.isfinite(win)]
        if win.size < 8:
            return None
        h = self.sign * float(np.median(win))
        return h if -8 < h < 40 else None

    def _shadow_at(self, dist, tip, dip, w):
        """How far the shadow is from the fingertip: measured just beyond the tip, on the table."""
        d = tip - dip
        p = tip + d / (np.linalg.norm(d) + 1e-6) * (0.5 * w + 2)
        x, y = int(p[0]), int(p[1])
        if not (0 <= x < PW and 0 <= y < PH):
            return None
        return float(min(SHADOW_CAP, dist[y, x]))

    def _height(self, h, s):
        """Both signals as one height: 0 = on the table, 10 = your usual lift. None while learning."""
        fd, fs = self.depth.frac(h), self.shadow.frac(s)
        parts = [(f, w) for f, w in ((fd, 1.0), (fs, 1.5)) if f is not None]
        if not parts:
            return None
        z = 10.0 * sum(f * w for f, w in parts) / sum(w for _, w in parts)
        both_down = fd is not None and fs is not None and fd < 0.35 and fs < 0.35
        return z, both_down

    def _update(self, tips, now):
        on, off = 5.0 + self.bias, 7.0 + self.bias
        seen = set()
        for key, tx, ty, h, s in tips:
            seen.add(key)
            slot = self.slots.setdefault(key, {"z": None, "armed": False, "low": 0, "state": "?",
                                               "hist": collections.deque(maxlen=10)})
            inside = self.region is None or (0 <= int(ty) < PH and 0 <= int(tx) < PW and self.region[int(ty), int(tx)])
            if inside:
                if h is not None:
                    self.depth.add(now, h)
                if s is not None:
                    self.shadow.add(now, s)
            pad = self.pad_at(tx, ty)
            event, gain = "", ""
            hz = self._height(h, s)
            if hz is not None:
                z, both_down = hz
                slot["z"] = z if slot["z"] is None else 0.5 * slot["z"] + 0.5 * z
                slot["hist"].append((now, z))
                touching = z < on
                slot["low"] = slot["low"] + 1 if touching else 0
                slot["state"] = "TOUCH" if touching else "hover"
                # Re-arm only when the finger is really up: the middle of the last three readings.
                if len(slot["hist"]) >= 3 and sorted(x for _, x in list(slot["hist"])[-3:])[1] > off:
                    slot["armed"] = True
                elif (slot["armed"] and touching and (slot["low"] >= 2 or both_down) and pad is not None
                      and self.pads[pad].get("name") and now - self.pads[pad].get("_flash", 0) > PAD_COOLDOWN):
                    slot["armed"] = False
                    recent = [(t, v) for t, v in slot["hist"] if now - t < 0.3]
                    t_top, z_top = max(recent, key=lambda p: p[1])
                    speed = max(0.0, (z_top - z) / max(0.04, now - t_top))
                    gain = float(np.clip(0.35 + 0.65 * speed / 80.0, 0.35, 1.0))
                    name = self.pads[pad]["name"]
                    self.mixer.play(name, gain)
                    self.taps += 1
                    self.pads[pad]["_hits"] = self.pads[pad].get("_hits", 0) + 1
                    self.pads[pad]["_flash"] = now
                    self.last_hit = label(name)
                    event = f"HIT {label(name)}" + (" fast" if both_down and slot["low"] < 2 else "")
            slot["pos"] = (tx, ty)
            sound = self.pads[pad].get("name") if pad is not None else None
            self.csv.writerow([V.now_iso(), f"{now - self.t0:.3f}", self.pairs, key, f"{tx:.0f}", f"{ty:.0f}",
                               "" if h is None else f"{h:.2f}", "" if s is None else f"{s:.1f}",
                               "" if slot["z"] is None else f"{slot['z']:.2f}", slot["state"],
                               "" if pad is None else pad + 1, "" if sound is None else label(sound), event, gain])
        for learner in (self.depth, self.shadow):
            msg = learner.fit(now)
            if msg:
                self.say(msg)
        for k in list(self.slots):
            if k not in seen:
                del self.slots[k]

    # ---- drawing
    def _draw(self, main_s, hands, tips, now):
        view = self.flip_image(main_s)
        overlay = view.copy()
        for i, p in enumerate(self.pads):
            quad = np.int32([self.to_display(x, y) for x, y in p["poly"]])
            lit = now - p.get("_flash", 0) < 0.15
            col = (255, 255, 255) if lit else tuple(int(c) for c in p["color"])
            cv2.fillPoly(overlay, [quad], col)
        view = cv2.addWeighted(overlay, 0.45, view, 0.55, 0)
        for i, p in enumerate(self.pads):
            quad = np.int32([self.to_display(x, y) for x, y in p["poly"]])
            sel = i == self.selected
            cv2.polylines(view, [quad], True, (0, 255, 255) if sel else (40, 40, 40), 3 if sel else 1, cv2.LINE_AA)
            text = (self.typed + "_") if sel else ("reading..." if not p.get("name") else label(p["name"]))
            c = quad.mean(axis=0).astype(int)
            size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
            org = (int(c[0] - size[0] / 2), int(c[1] + size[1] / 2))
            cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(view, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        if self.drag:
            a, b = (self.to_display(*self.drag[0]), self.to_display(*self.drag[1]))
            cv2.rectangle(view, tuple(int(v) for v in a), tuple(int(v) for v in b), (0, 255, 255), 2)
        for key, tx, ty, h, s in tips:
            slot = self.slots.get(key, {})
            p = tuple(int(v) for v in self.to_display(tx, ty))
            touching = slot.get("state") == "TOUCH"
            z = slot.get("z")
            r = 5 if z is None or touching else int(5 + min(24, 2.0 * max(0.0, z)))
            cv2.circle(view, p, r, (0, 255, 0) if touching else (0, 170, 255), -1 if touching else 2)
        if self.phase != "play":
            msg = "HANDS OFF - scanning the table" if self.phase == "scan" else "measuring the table..."
            cv2.rectangle(view, (0, 0), (PW, 32), (0, 0, 0), -1)
            cv2.putText(view, msg, (12, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)

        panel = np.full((PH, SW, 3), 18, np.uint8)
        fps = (len(self.pair_times) - 1) / max(1e-3, self.pair_times[-1] - self.pair_times[0]) \
            if len(self.pair_times) > 1 else 0
        learned = [n for n, l in (("depth", self.depth), ("shadow", self.shadow)) if l.lift is not None]
        step = {"scan": "1/2 HANDS OFF - scanning", "surface": "1/2 measuring the table",
                "play": "TAP!" if learned else "2/2 tap a few times to learn"}[self.phase]
        lines = [("StickyBeat", (255, 220, 120), 0.7),
                 (step, (130, 230, 130) if learned else (60, 200, 255), 0.5),
                 (f"touch: {' + '.join(learned) if learned else 'learning...'}", (220, 220, 220), 0.45),
                 (f"hits {self.taps}   last {self.last_hit}", (130, 230, 130), 0.45),
                 (f"{fps:.0f} fps  {np.mean(self.proc_ms) if self.proc_ms else 0:.0f} ms  audio "
                  f"{self.mixer.latency_ms:.0f} ms", (150, 150, 150), 0.4)]
        y = 26
        for text, col, sc in lines:
            cv2.putText(panel, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, sc, col, 1, cv2.LINE_AA)
            y += 22
        y += 4
        for i, p in enumerate(self.pads[:9]):
            cv2.rectangle(panel, (10, y - 11), (24, y + 3), tuple(int(c) for c in p["color"]), -1)
            name = label(p["name"]) if p.get("name") else "reading..."
            cv2.putText(panel, f"{i + 1}. {name}  x{p.get('_hits', 0)}", (32, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        (230, 230, 230), 1, cv2.LINE_AA)
            y += 19
        if len(self.pads) > 9:
            cv2.putText(panel, f"... {len(self.pads) - 9} more", (32, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (150, 150, 150), 1, cv2.LINE_AA)
            y += 19
        for msg in list(self.events)[:3]:
            cv2.putText(panel, msg[:44], (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (150, 150, 150), 1, cv2.LINE_AA)
            y += 15
        if self.shadow_view is not None:
            sv = cv2.cvtColor(cv2.resize(cv2.cvtColor(main_s, cv2.COLOR_BGR2GRAY), (160, 120)), cv2.COLOR_GRAY2BGR) // 2
            sv[cv2.resize(self.shadow_view, (160, 120)) > 0] = (255, 120, 60)
            panel[PH - 150:PH - 30, SW - 166:SW - 6] = self.flip_image(sv)
            cv2.putText(panel, "shadows", (SW - 162, PH - 136), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1)
        cv2.putText(panel, "click note+type | drag=box | right-click=remove", (8, PH - 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.33, (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(panel, "S rescan  R table  [ ] line  M/O flip  Q quit", (8, PH - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.33, (120, 120, 120), 1, cv2.LINE_AA)
        self.view = np.hstack([view, panel])
        if now - self.last_save > 2:
            cv2.imwrite(os.path.join(V.LOG_DIR, "latest_sticky.jpg"), self.view)
            self.last_save = now
        if now - self.last_print > 2:
            self.last_print = now
            print(f"[{now - self.t0:6.1f}s] {self.phase} {fps:4.1f}fps pads {len(self.pads)} hits {self.taps} "
                  f"learned {learned} fingers {len(self.slots)}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Tap sticky notes on any table to play sounds (phone cameras only)")
    ap.add_argument("--focus", type=float, default=2.5, help="main camera focus in diopters (2.5 = 40 cm)")
    ap.add_argument("--audio", default=None, help="output device name part, e.g. 'MacBook' for the built-in speakers")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--flip", action="store_true", help="start with the view upside down")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--duration", type=float, default=0)
    args = ap.parse_args()
    try:
        StickyBeat(args).run()
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
