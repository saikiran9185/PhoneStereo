#!/usr/bin/env python3
"""StickyBeat Studio: a loop studio on your table, played with sticky notes and paper, phone cameras only.

./studio.sh starts it and opens http://localhost:8770.
- Channel rack: one channel per sound, 16 steps, patterns A-D, volume / mute / solo per channel,
  BPM, swing, metronome, master volume and a DJ filter (left = low-pass, right = high-pass).
- Sticky notes / boxes: tap them to play live. With REC on, each tap lands on the nearest step and loops.
- Paper pattern: print the sheet from the Paper card, fill boxes with a dark marker and the loop plays
  what you drew (a dot plays soft, a filled box loud). Draw while it plays.
- Record audio: everything you hear is saved to recordings/*.wav.
The camera side (notes, touch, scanning) is sticky.py's; this adds the sequencer, the sheet and the web UI.
"""
import argparse
import collections
import csv
import datetime
import http.server
import io
import json
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import wave

import cv2
import numpy as np

import sticky as S
import viewer as V
from sticky import PH, PW, SR, StickyBeat, inside, label, order_quad, parse_sound

PORT = 8770
PROJECT = os.path.join(S.HERE, "studio_project.json")
UI = os.path.join(S.HERE, "studio.html")
REC_DIR = os.path.join(S.HERE, "recordings")
STEPS = 16
PATTERNS = "ABCD"
TAP_OFFSET_MS = 80        # tap -> frame on the Mac: exposure + encode + USB, plus the 2-frame touch rule
CHORD_CHOICES = ["C", "Cm", "D", "Dm", "E", "Em", "F", "Fm", "G", "Gm", "A", "Am", "B", "Bm",
                 "C7", "D7", "E7", "G7", "A7", "Cmaj7", "Fmaj7", "Am7", "Dm7", "Em7"]
NOTE_CHOICES = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]


def sound_choices():
    return [{"group": "Drums", "items": list(S.DRUM_WORDS)},
            {"group": "Chords", "items": CHORD_CHOICES},
            {"group": "Notes", "items": NOTE_CHOICES},
            {"group": "Your sounds", "items": ["file:" + k for k in sorted(S.samples_available())]}]


def click(freq, dur=0.03):
    t = np.arange(int(SR * dur)) / SR
    return (np.sin(2 * np.pi * freq * t) * np.exp(-t * 120)).astype(np.float32)


def soft_clip(x):
    """Linear up to 0.8, then rounds off towards 1 instead of cracking."""
    a = np.abs(x)
    return np.where(a < 0.8, x, np.sign(x) * (0.8 + 0.2 * np.tanh((a - 0.8) / 0.2))).astype(np.float32)


# ---------------------------------------------------------------- sequencer
class Sequencer:
    """Step sequencer and live-hit mixer in one audio callback, so loop steps land on the exact sample."""

    def __init__(self, device=None):
        self.lock = threading.RLock()
        self.cache = {}
        self.voices = []                  # [tone, position, gain, samples to wait]
        self.tracks = []                  # channels: sound, vol, mute, solo, steps per pattern
        self.next_id = 1
        self.bpm, self.swing, self.master, self.filter = 100.0, 0.0, 0.9, 0.0
        self.playing = self.rec = self.metro = False
        self.pat, self.queued = "A", None
        self.step, self.last_step_at, self.next_step_at = -1, 0.0, 0.0
        self.pos = 0                      # samples rendered so far
        self.clock = (time.time(), 0)     # (when sample `pos` will be heard, pos), refreshed every callback
        self.capture_t = None             # when the camera frame being processed was taken
        self.skip = {}                    # (track id, step) -> sample: a tap recorded just before its step
        self.recording, self.rec_started = None, 0.0
        self.level = 0.0
        self.lp = [0.0, 0.0]              # DJ filter state
        self.dirty = False
        self.click_hi, self.click_lo = click(2400), click(1600)
        self.stream, self.device, self.latency_ms = None, "", 0.0
        self.open(device)

    def open(self, device=None):
        import sounddevice as sd
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=256,
                                      latency="low", device=device or None, callback=self._cb)
        self.stream.start()
        self.latency_ms = self.stream.latency * 1000
        self.device = sd.query_devices(self.stream.device)["name"]

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    # ---- sounds + channels
    def get(self, name):
        if name not in self.cache:
            self.cache[name] = S.render(name)
        return self.cache[name]

    def by_id(self, tid):
        return next((t for t in self.tracks if t["id"] == tid), None)

    def add_track(self, sound, steps=None, vol=0.8, mute=False, solo=False):
        self.get(sound)                                  # render now, never inside the audio callback
        t = {"id": self.next_id, "sound": sound, "vol": vol, "mute": mute, "solo": solo,
             "steps": {p: list((steps or {}).get(p, [0.0] * STEPS)) for p in PATTERNS}}
        with self.lock:
            self.tracks.append(t)
            self.next_id += 1
        self.dirty = True
        return t

    def track_for(self, sound):
        return next((t for t in self.tracks if t["sound"] == sound), None) or self.add_track(sound)

    # ---- transport
    def step_len(self):
        return SR * 60.0 / self.bpm / 4

    def start(self):
        with self.lock:
            if not self.playing:
                self.playing, self.step = True, -1
                self.next_step_at = self.pos + 0.03 * SR
                self.last_step_at = self.next_step_at - self.step_len()

    def stop(self):
        with self.lock:
            self.playing, self.step = False, -1
            self.skip.clear()
            if self.queued:
                self.pat, self.queued = self.queued, None

    def heard_pos(self, t):
        ct, cp = self.clock
        return cp + (t - ct) * SR

    def heard_step(self):
        """The step you are hearing right now (Bluetooth can be two steps behind the one just played)."""
        if not self.playing or self.step < 0:
            return -1
        k = (self.heard_pos(time.time()) - self.last_step_at) / self.step_len()
        return (self.step + math.floor(k)) % STEPS

    def play(self, name, gain=1.0, record=True):
        """A tap on a note: play it now, and with REC on also write it into the loop at the nearest step."""
        t = self.track_for(name)
        with self.lock:
            if not t["mute"]:
                self.voices.append([self.cache[name], 0, gain * t["vol"], 0])
                t["_lit"] = time.time() + self.latency_ms / 1000
            if not (record and self.rec and self.playing and self.step >= 0):
                return
            now = time.time()
            when = self.capture_t if self.capture_t and now - self.capture_t < 0.5 else now
            # where in the loop was the sound you were hearing when your finger landed?
            r = round((self.heard_pos(when) - self.last_step_at) / self.step_len())
            s = (self.step + r) % STEPS
            t["steps"][self.pat][s] = max(t["steps"][self.pat][s], round(float(gain), 2))
            if r >= 1:                       # that step hasn't played yet: don't play the tap twice
                self.skip[(t["id"], s)] = self.last_step_at + r * self.step_len()
            self.dirty = True

    def _fire(self, off):
        self.step = (self.step + 1) % STEPS
        if self.step == 0 and self.queued:
            self.pat, self.queued = self.queued, None
        self.last_step_at = self.next_step_at
        when = time.time() + self.latency_ms / 1000 + off / SR
        solo = any(t["solo"] for t in self.tracks)
        for t in self.tracks:
            vel = t["steps"][self.pat][self.step]
            if vel <= 0 or t["mute"] or (solo and not t["solo"]):
                continue
            at = self.skip.pop((t["id"], self.step), None)
            if at is not None and abs(at - self.last_step_at) < self.step_len() / 2:
                continue
            tone = self.cache.get(t["sound"])
            if tone is not None:
                self.voices.append([tone, 0, vel * t["vol"], off])
                t["_lit"] = when
        if self.metro and self.step % 4 == 0:
            self.voices.append([self.click_hi if self.step == 0 else self.click_lo, 0, 0.5, off])
        L = self.step_len()
        sw = self.swing * L / 3                          # swing pushes every second 16th later
        self.next_step_at += L + (sw if self.step % 2 == 0 else -sw)

    def _filter(self, x):
        f = self.filter
        fc = 20000 * 0.01 ** (-f) if f < 0 else 20 * 125 ** f      # 20 kHz..200 Hz low-pass, 20 Hz..2.5 kHz high-pass
        a = 1 - math.exp(-2 * math.pi * fc / SR)
        y1, y2 = self.lp
        out = np.empty_like(x)
        for i, v in enumerate(x.tolist()):
            y1 += a * (v - y1)
            y2 += a * (y1 - y2)
            out[i] = y2
        self.lp = [y1, y2]
        return out if f < 0 else x - out

    def _cb(self, out, frames, _t, _status):
        mix = np.zeros(frames, np.float32)
        with self.lock:
            if self.playing:
                while self.next_step_at < self.pos + frames:
                    self._fire(int(max(0, self.next_step_at - self.pos)))
            keep = []
            for v in self.voices:
                tone, p, gain, wait = v
                if wait >= frames:
                    v[3] -= frames
                    keep.append(v)
                    continue
                n = min(frames - wait, len(tone) - p)
                mix[wait:wait + n] += gain * tone[p:p + n]
                v[1] += n
                v[3] = 0
                if v[1] < len(tone):
                    keep.append(v)
            self.voices = keep[-64:]
            self.clock = (time.time() + self.latency_ms / 1000, self.pos)
            self.pos += frames
        if abs(self.filter) > 0.01:
            mix = self._filter(mix)
        y = soft_clip(mix * self.master)
        self.level = max(float(np.abs(y).max()), self.level * 0.85)
        if self.recording is not None:
            self.recording.append(y.copy())
        out[:, 0] = y
        out[:, 1] = y

    # ---- audio recording
    def start_recording(self):
        self.rec_started = time.time()
        self.recording = []

    def stop_recording(self):
        blocks, self.recording = self.recording, None
        if not blocks:
            return None
        os.makedirs(REC_DIR, exist_ok=True)
        name = datetime.datetime.now().strftime("stickybeat_%Y%m%d_%H%M%S.wav")
        data = (np.clip(np.concatenate(blocks), -1, 1) * 32767).astype(np.int16)
        with wave.open(os.path.join(REC_DIR, name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data.tobytes())
        return name

    # ---- project file
    def to_json(self):
        with self.lock:
            return {"bpm": self.bpm, "swing": self.swing, "master": self.master, "metro": self.metro,
                    "pat": self.pat, "tracks": [{k: t[k] for k in ("sound", "vol", "mute", "solo", "steps")}
                                                for t in self.tracks]}

    def load_json(self, d):
        self.bpm, self.swing = float(d.get("bpm", 100)), float(d.get("swing", 0))
        self.master, self.metro = float(d.get("master", 0.9)), bool(d.get("metro", False))
        self.pat = d.get("pat", "A")
        for t in d.get("tracks", []):
            try:
                self.add_track(t["sound"], t.get("steps"), t.get("vol", 0.8), t.get("mute", False), t.get("solo", False))
            except (KeyError, ValueError) as e:
                print(f"skipping channel {t.get('sound')}: {e}", file=sys.stderr)

    def starter(self):
        """A beat to press play on: four on the floor, claps on 2 and 4, off-beat hats."""
        on = lambda steps, v=0.85: {"A": [v if i in steps else 0.0 for i in range(STEPS)]}
        self.add_track("kick", on({0, 4, 8, 12}, 0.95))
        self.add_track("snare", on(set()))
        self.add_track("hat", on({2, 6, 10, 14}, 0.6), vol=0.6)
        self.add_track("clap", on({4, 12}, 0.8))


# ---------------------------------------------------------------- paper pattern sheet
ARUCO = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
SHEET_W, SHEET_H = 1754, 1240                    # A4 landscape at 150 dpi
MK, MARGIN = 150, 60                             # corner marker size, page margin
CORNERS = [(MARGIN, MARGIN), (SHEET_W - MARGIN - MK, MARGIN),
           (SHEET_W - MARGIN - MK, SHEET_H - MARGIN - MK), (MARGIN, SHEET_H - MARGIN - MK)]
GRID = (MARGIN, MARGIN + MK + 60, SHEET_W - MARGIN, SHEET_H - MARGIN - MK - 40)   # x0, y0, x1, y1
LABEL_W = 260
MIN_ROWS, MAX_ROWS = 4, 8


def marker_id(rows, corner):
    """The corner markers also say how many rows the sheet has: ids 0-3 = 4 rows, 4-7 = 5 rows..."""
    return 4 * (rows - MIN_ROWS) + corner


def cell_rects(rows):
    x0, y0, x1, y1 = GRID
    cw, rh = (x1 - x0 - LABEL_W) / STEPS, (y1 - y0) / rows
    return [[(x0 + LABEL_W + s * cw, y0 + r * rh, x0 + LABEL_W + (s + 1) * cw, y0 + (r + 1) * rh)
             for s in range(STEPS)] for r in range(rows)]


def _font(size, bold=False):
    from PIL import ImageFont
    for path in (["/System/Library/Fonts/Supplemental/Arial Bold.ttf"] if bold else []) + \
            ["/System/Library/Fonts/Supplemental/Arial.ttf"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def make_sheet(sounds):
    """Printable A4 sheet: one row per sound, 16 boxes per row, a marker in each corner. -> (png, pdf) bytes"""
    from PIL import Image, ImageDraw
    rows = len(sounds)
    img = Image.new("L", (SHEET_W, SHEET_H), 255)
    d = ImageDraw.Draw(img)
    for k, (x, y) in enumerate(CORNERS):
        img.paste(Image.fromarray(cv2.aruco.generateImageMarker(ARUCO, marker_id(rows, k), MK)), (x, y))
    d.text((SHEET_W / 2, MARGIN + 50), "StickyBeat pattern", fill=0, font=_font(56, True), anchor="mm")
    d.text((SHEET_W / 2, MARGIN + 112), "Fill a box with a dark marker to play that step.  A dot plays soft, a filled box loud.",
           fill=90, font=_font(28), anchor="mm")
    cells = cell_rects(rows)
    x0, y0, _, _ = GRID
    for s in range(STEPS):
        cx = (cells[0][s][0] + cells[0][s][2]) / 2
        beat = s % 4 == 0
        d.text((cx, y0 - 26), str(s // 4 + 1) if beat else "·", fill=40 if beat else 150,
               font=_font(32 if beat else 30, beat), anchor="mm")
    for r, snd in enumerate(sounds):
        top, bottom = cells[r][0][1], cells[r][0][3]
        d.rounded_rectangle([x0, top + 4, x0 + LABEL_W - 18, bottom - 4], radius=10, outline=120, width=3)
        d.text((x0 + (LABEL_W - 18) / 2, (top + bottom) / 2), label(snd) if snd else "", fill=0,
               font=_font(46 if len(label(snd or "")) < 7 else 34, True), anchor="mm")
        for s in range(STEPS):
            a, b, c, e = cells[r][s]
            d.rectangle([a + 4, b + 4, c - 4, e - 4], fill=244 if (s // 4) % 2 else 255, outline=165, width=2)
    d.text((SHEET_W / 2, SHEET_H - MARGIN - MK / 2), "Keep all four corner squares where the phone can see them.",
           fill=110, font=_font(26), anchor="mm")
    png, pdf = io.BytesIO(), io.BytesIO()
    img.save(png, "PNG")
    img.convert("RGB").save(pdf, "PDF", resolution=150)
    return png.getvalue(), pdf.getvalue()


class SheetReader:
    """Finds the printed sheet by its corner markers and reads which boxes have ink in them."""

    def __init__(self):
        p = cv2.aruco.DetectorParameters()
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self.det = cv2.aruco.ArucoDetector(ARUCO, p)
        self.lock = threading.Lock()
        self.H = None                 # camera (full size) -> sheet
        self.rows, self.markers, self.seen_at = 0, 0, 0.0
        self.grid, self.pending = None, None
        self.cells_px, self.quad = None, None

    def visible(self):
        return self.H is not None and time.time() - self.seen_at < 1.5

    def read(self, img, hand=None):
        """img: full-size camera frame; hand: mask of hands + their shadows, same size. True if the grid changed."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = self.det.detectMarkers(gray)
        src, dst, rows = [], [], []
        for c, i in zip(corners, [] if ids is None else ids.ravel()):
            i = int(i)
            if i >= 4 * (MAX_ROWS - MIN_ROWS + 1):
                continue
            rows.append(MIN_ROWS + i // 4)
            x, y = CORNERS[i % 4]
            src += c.reshape(4, 2).tolist()
            dst += [(x, y), (x + MK, y), (x + MK, y + MK), (x, y + MK)]
        n = len(src) // 4
        if n >= 3:
            H, _ = cv2.findHomography(np.float32(src), np.float32(dst), 0)
        elif n >= 1 and self.visible():
            # a hand covers some corners: keep the old fit while the corners still seen agree with it
            proj = cv2.perspectiveTransform(np.float32(src).reshape(-1, 1, 2), self.H).reshape(-1, 2)
            if np.abs(proj - np.float32(dst)).max() > 15:
                return False
            H = self.H
        else:
            return False
        if H is None:
            return False
        nrows = max(set(rows), key=rows.count)
        Sc = np.diag([0.5, 0.5, 1.0]) @ H                 # read at half the sheet's print resolution
        size = (SHEET_W // 2, SHEET_H // 2)
        warp = cv2.warpPerspective(gray, Sc, size)
        valid = cv2.warpPerspective(np.full(gray.shape, 255, np.uint8), Sc, size, flags=cv2.INTER_NEAREST)
        covered = None if hand is None else cv2.warpPerspective(hand, Sc, size, flags=cv2.INTER_NEAREST)
        x0, y0, x1, y1 = [v // 2 for v in GRID]
        area = warp[y0:y1, x0:x1][valid[y0:y1, x0:x1] > 0]
        if area.size < 1000:
            return False
        white = float(np.percentile(area, 90))
        cells = cell_rects(nrows)
        prev = self.grid if self.grid is not None and len(self.grid) == nrows else [[0.0] * STEPS for _ in range(nrows)]
        grid = []
        for r in range(nrows):
            row = []
            for s in range(STEPS):
                a, b, c, e = [v / 2 for v in cells[r][s]]
                ix, iy = (c - a) * 0.22, (e - b) * 0.22          # stay clear of the printed box lines
                sl = (slice(int(b + iy), int(e - iy)), slice(int(a + ix), int(c - ix)))
                if (valid[sl] == 0).any() or (covered is not None and covered[sl].any()):
                    row.append(prev[r][s])                      # can't see it now: keep what it was
                    continue
                # Judge the box against the white gap printed around it, not the whole page: a shadow
                # darkens both alike, ink only the box.
                ai, bi, ci, ei = int(a), int(b), min(int(c), size[0] - 2), min(int(e), size[1] - 2)
                gap = np.concatenate([warp[bi:ei, max(0, ai - 1):ai + 2].max(axis=1), warp[bi:ei, ci - 1:ci + 2].max(axis=1),
                                      warp[max(0, bi - 1):bi + 2, ai:ci].max(axis=0), warp[ei - 1:ei + 2, ai:ci].max(axis=0)])
                g10, g90 = np.percentile(gap, [10, 90])
                if g10 < 0.75 * g90:                            # a shadow's edge crosses this box: can't tell
                    row.append(prev[r][s])
                    continue
                local = min(white, float(np.median(gap)))
                ink = float((warp[sl] < 0.6 * local).mean())
                row.append(0.0 if ink < 0.12 else 0.5 if ink < 0.3 else 1.0)      # dot = soft, more = loud
            grid.append(row)
        Hinv = np.linalg.inv(H)
        centres = np.float32([[((a + c) / 2, (b + e) / 2) for a, b, c, e in row] for row in cells])
        cells_px = cv2.perspectiveTransform(centres.reshape(-1, 1, 2), Hinv).reshape(nrows, STEPS, 2)
        quad = cv2.perspectiveTransform(np.float32([[0, 0], [SHEET_W, 0], [SHEET_W, SHEET_H], [0, SHEET_H]])
                                        .reshape(-1, 1, 2), Hinv).reshape(4, 2)
        with self.lock:
            first = self.grid is None or len(self.grid) != nrows
            self.H, self.rows, self.markers, self.seen_at = H, nrows, n, time.time()
            self.cells_px, self.quad = cells_px, quad
            changed = first or (grid == self.pending and grid != self.grid)   # same reading twice: take it
            if changed:
                self.grid = grid
            self.pending = grid
        return changed


# ---------------------------------------------------------------- the app
class Studio(StickyBeat):
    def __init__(self, args, seq, cfg):
        args.headless = True                             # the browser is the window
        super().__init__(args)
        self.mixer = seq
        self.cfg = cfg
        self.events = collections.deque(maxlen=40)
        self.cmds = queue.Queue()
        self.cam_on = threading.Event()
        self.quit = False
        self.cam_state = "off"
        self.latest, self.last_hands = None, []
        self.sheet = SheetReader()
        self.flat = None
        self.W, self.Hh = 1280, 960
        self.view_seq = 0
        self.jpeg = self._placeholder("Camera off - press Start camera")
        self.save_at = 0.0

    def _placeholder(self, text):
        img = np.full((PH, PW, 3), 24, np.uint8)
        size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
        cv2.putText(img, text, ((PW - size[0]) // 2, PH // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 150, 150), 2, cv2.LINE_AA)
        self.view_seq = getattr(self, "view_seq", 0) + 1
        return cv2.imencode(".jpg", img)[1].tobytes()

    # ---- main loop: commands always, camera when switched on
    def run(self):
        from mediapipe.tasks.python import BaseOptions, vision
        import mediapipe as mp
        self.mp = mp
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=S.MODEL), running_mode=vision.RunningMode.VIDEO,
            num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        threading.Thread(target=self._sheet_loop, daemon=True).start()
        while not self.quit:
            self._housekeeping()
            if self.cam_on.is_set():
                try:
                    self._camera_session()
                except RuntimeError as e:
                    self.say(str(e))
                    self.cam_on.clear()
                    self.cam_state = "off"
                    self.jpeg = self._placeholder("No camera - plug in the phone, then Start camera")
            else:
                time.sleep(0.05)

    def _housekeeping(self):
        while True:
            try:
                fn = self.cmds.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as e:
                self.say(f"error: {e}")
        for p in list(self.pads):                        # every named note has a channel in the rack
            if p.get("name"):
                self.mixer.track_for(p["name"])
        if self.mixer.dirty and time.time() - self.save_at > 2:
            self.save()

    def _camera_session(self):
        a = self.args
        self.cam_state = "starting"
        self.jpeg = self._placeholder("Starting the phone camera...")
        self.phone = V.Phone(self.log, argparse.Namespace(camera="4", size="1280x960", quality=80, focus=a.focus))
        self.phone.start()
        self.rx = V.Receiver(self.log)
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            self.rx.running = False
            self.phone.stop()
            raise RuntimeError(f"no stream from the phone ({self.rx.error}) - is it plugged in and unlocked?")
        h = self.rx.header
        self.W, self.Hh = h["width"], h["height"]
        self.H0 = V.factory_homography(h["cameras"], self.W, self.Hh)
        self.csv_f = open(self.csv_path, "a", newline="")
        self.csv = csv.writer(self.csv_f)
        self._reset_scan(True)
        self.cam_state = "on"
        self.say("camera on - hands OFF the table while it scans")
        try:
            while self.cam_on.is_set() and not self.quit:
                self._housekeeping()
                if self.rx.error:
                    self._reconnect()
                    continue
                pair = self._pick()
                if pair is None:
                    time.sleep(0.003)
                    continue
                self._process(*pair)
        finally:
            self.rx.running = False
            self.phone.stop()
            self.csv_f.close()
            self.cam_state = "off"
            self.jpeg = self._placeholder("Camera off - press Start camera")
            self.log(f"camera off - {self.taps} hits so far")

    # ---- hooks into the StickyBeat pipeline
    def _detect(self, main_s, ts):
        self.last_hands = super()._detect(main_s, ts)
        return self.last_hands

    def _process(self, f0, f1):
        # When the finger landed, roughly: the frame left the phone a little before it reached the Mac.
        self.mixer.capture_t = f0.get("t_recv", time.time()) - self.cfg["tap_offset_ms"] / 1000
        super()._process(f0, f1)
        self.latest = (f0["img"], self.last_hands)
        self.jpeg = cv2.imencode(".jpg", self.view[:, :PW], [cv2.IMWRITE_JPEG_QUALITY, 72])[1].tobytes()
        self.view_seq += 1

    def _set_pads(self, found):
        q = self._sheet_quad()
        if q is not None:                                 # the paper sheet is not a giant sticky note
            found = [p for p in found if not inside(p["poly"], q)]
        super()._set_pads(found)
        self.flat = float(np.median([np.divide(*sorted(cv2.minAreaRect(p["poly"])[1])) for p in found])) if found else None

    def _sheet_quad(self):
        sh = self.sheet
        if not sh.visible() or sh.quad is None:
            return None
        return sh.quad * [PW / self.W, PH / self.Hh]

    def _draw(self, main_s, hands, tips, now):
        super()._draw(main_s, hands, tips, now)
        q = self._sheet_quad()
        if q is None:
            return
        view = self.view[:, :PW]
        sx, sy = PW / self.W, PH / self.Hh
        cv2.polylines(view, [np.int32([self.to_display(x, y) for x, y in q])], True, (38, 155, 255), 2, cv2.LINE_AA)
        with self.sheet.lock:
            grid, cells = self.sheet.grid, self.sheet.cells_px
        playing = self.mixer.heard_step()
        if grid is None or cells is None:
            return
        for r, row in enumerate(grid):
            for s, vel in enumerate(row):
                x, y = self.to_display(cells[r, s, 0] * sx, cells[r, s, 1] * sy)
                p = (int(x), int(y))
                if vel > 0:
                    cv2.circle(view, p, 3 + int(3 * vel), (90, 230, 120), -1, cv2.LINE_AA)
                if s == playing:
                    cv2.circle(view, p, 8, (255, 255, 255), 1, cv2.LINE_AA)

    # ---- the paper sheet, read beside the camera loop
    def _sheet_loop(self):
        while not self.quit:
            time.sleep(0.25)
            got, self.latest = self.latest, None
            if got is None:
                continue
            img, hands = got
            hand = None
            if hands:
                m = cv2.dilate(self._hand_mask(hands), np.ones((41, 41), np.uint8))   # hands and their shadows
                hand = cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
            try:
                if self.sheet.read(img, hand) and self.cfg["follow_paper"]:
                    self._apply_paper()
            except Exception as e:
                self.log(f"sheet: {e}")

    def _apply_paper(self):
        grid, seq = self.sheet.grid, self.mixer
        if grid is None:
            return
        for r, sound in enumerate(self.cfg["sheet_rows"][:len(grid)]):
            if not sound:
                continue
            t = seq.track_for(sound)
            with seq.lock:
                t["steps"][seq.pat] = list(grid[r])
                t["paper"] = True
        seq.dirty = True

    # ---- web side
    def valid_sound(self, name):
        try:
            self.mixer.get(name)
            return True
        except (KeyError, ValueError, OSError):
            return False

    def _sound_from(self, d):
        name = d.get("sound") or parse_sound(d.get("text", "")) or ""
        if not self.valid_sound(name):
            raise ValueError(f"unknown sound '{d.get('text') or name}' - try kick, snare, Am, G7, C4")
        return name

    def command(self, d):
        op, seq, res = d.get("op"), self.mixer, {}
        try:
            if op == "play":
                seq.stop() if seq.playing else seq.start()
            elif op == "rec":
                seq.rec = not seq.rec
                if seq.rec and not seq.playing:
                    seq.start()
            elif op == "metro":
                seq.metro = not seq.metro
            elif op == "bpm":
                seq.bpm = float(np.clip(float(d["value"]), 40, 220))
            elif op == "swing":
                seq.swing = float(np.clip(float(d["value"]), 0, 1))
            elif op == "master":
                seq.master = float(np.clip(float(d["value"]), 0, 1.5))
            elif op == "filter":
                seq.filter = float(np.clip(float(d["value"]), -1, 1))
            elif op == "pattern":
                p = d["value"]
                if p in PATTERNS:
                    with seq.lock:
                        if seq.playing and p != seq.pat:
                            seq.queued = p                      # switches at the end of the bar, like a DJ
                        else:
                            seq.pat, seq.queued = p, None
            elif op == "clear_pattern":
                with seq.lock:
                    for t in seq.tracks:
                        t["steps"][seq.pat] = [0.0] * STEPS
                        t["paper"] = False
            elif op == "copy_pattern":
                to = d["to"]
                with seq.lock:
                    for t in seq.tracks:
                        t["steps"][to] = list(t["steps"][seq.pat])
            elif op == "add_track":
                name = self._sound_from(d)
                if not any(t["sound"] == name for t in seq.tracks):
                    seq.add_track(name)
            elif op == "track":
                t = seq.by_id(int(d["id"]))
                field = d["field"]
                if t is None:
                    raise ValueError("no such channel")
                if field == "sound":
                    old, new = t["sound"], self._sound_from(d)
                    t["sound"] = new
                    self.cmds.put(lambda: self._rename_pads(old, new))
                elif field == "vol":
                    t["vol"] = float(np.clip(float(d["value"]), 0, 1.5))
                elif field in ("mute", "solo"):
                    t[field] = not t[field]
                elif field == "clear":
                    t["steps"][seq.pat] = [0.0] * STEPS
                    t["paper"] = False
                elif field == "remove":
                    with seq.lock:
                        seq.tracks.remove(t)
            elif op == "step":
                t = seq.by_id(int(d["id"]))
                t["steps"][seq.pat][int(d["step"])] = float(np.clip(float(d["value"]), 0, 1))
            elif op == "rec_audio":
                if seq.recording is None:
                    seq.start_recording()
                    self.say("recording audio...")
                else:
                    res["saved"] = seq.stop_recording()
                    self.say(f"saved recordings/{res['saved']}")
            elif op == "device":
                seq.open(d["value"])
                self.cfg["device"] = d["value"]
                self.say(f"audio: {seq.device} ({seq.latency_ms:.0f} ms)")
            elif op == "camera":
                (self.cam_on.set if d["value"] else self.cam_on.clear)()
            elif op == "rescan":
                self.cmds.put(lambda: (self._reset_scan(True), self.say("rescanning - hands OFF the table")))
            elif op == "relearn":
                self.cmds.put(lambda: (self._reset_scan(False), self.say("relearning the table - hands OFF")))
            elif op == "mirror":
                self.mirror = not self.mirror
            elif op == "flip":
                self.flipv = not self.flipv
            elif op == "bias":
                self.bias = float(np.clip(float(d["value"]), -4, 4))
            elif op == "tap_offset":
                self.cfg["tap_offset_ms"] = float(np.clip(float(d["value"]), 0, 300))
            elif op == "pad_click":
                x, y = self.to_display(float(d["x"]), float(d["y"]))
                res["pad"] = self.pad_at(x, y, 4)
            elif op == "pad_sound":
                i, name = int(d["i"]), self._sound_from(d)
                self.cmds.put(lambda: self._set_pad_sound(i, name))
            elif op == "pad_remove":
                i = int(d["i"])
                self.cmds.put(lambda: self._remove_pad(i))
            elif op == "pad_box":
                pts = [self.to_display(float(x), float(y)) for x, y in d["pts"]]
                self.cmds.put(lambda: self._add_box(pts))
            elif op == "sheet_rows":
                rows = [r if self.valid_sound(r) else "" for r in d["value"]][:MAX_ROWS]
                rows += [""] * (MIN_ROWS - len(rows))
                self.cfg["sheet_rows"] = rows
                if self.cfg["follow_paper"] and self.sheet.visible():
                    self._apply_paper()
            elif op == "follow_paper":
                self.cfg["follow_paper"] = bool(d["value"])
                if self.cfg["follow_paper"] and self.sheet.visible():
                    self._apply_paper()
            else:
                raise ValueError(f"unknown command {op}")
        except (KeyError, ValueError, TypeError) as e:
            res["error"] = str(e)
        seq.dirty = True
        self.save()
        return res

    def _rename_pads(self, old, new):
        for p in self.pads:
            if p.get("name") == old:
                p["name"] = new
        self._save_pads()

    def _set_pad_sound(self, i, name):
        if 0 <= i < len(self.pads):
            self.pads[i]["name"] = name
            self._save_pads()
            self.mixer.track_for(name)
            self.mixer.play(name, record=False)
            self.say(f"note {i + 1} = {label(name)}")

    def _remove_pad(self, i):
        if 0 <= i < len(self.pads):
            self.say(f"removed {label(self.pads[i].get('name') or '?')}")
            del self.pads[i]
            self._save_pads()

    def _add_box(self, pts):
        (x0, y0), (x1, y1) = pts
        if abs(x1 - x0) < 12 or abs(y1 - y0) < 12:
            return
        self.pads.append({"poly": order_quad([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]), "kind": "box",
                          "color": [200, 200, 200], "name": self.next_default()})
        self._save_pads()
        self.say(f"box added as {label(self.pads[-1]['name'])}")

    def upload(self, filename, data):
        stem = re.sub(r"[^a-z0-9]+", "", os.path.splitext(os.path.basename(filename))[0].lower())[:24] or "sound"
        os.makedirs(S.SOUNDS_DIR, exist_ok=True)
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in" + (os.path.splitext(filename)[1].lower() or ".wav"))
            with open(src, "wb") as f:
                f.write(data)
            dst = os.path.join(S.SOUNDS_DIR, stem + ".wav")
            r = subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", "-c", "1", src, dst],
                               capture_output=True, text=True)
            if r.returncode:
                return {"error": f"couldn't read {filename}: {r.stderr.strip() or 'unknown format'}"}
        name = "file:" + stem
        self.mixer.cache.pop(name, None)
        self.mixer.get(name)
        self.say(f"added sound {stem} - write '{stem}' on a note or pick it in a channel")
        return {"sound": name}

    def save(self):
        self.save_at = time.time()
        self.mixer.dirty = False
        tmp = PROJECT + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"seq": self.mixer.to_json(), "cfg": self.cfg}, f)
        os.replace(tmp, PROJECT)

    def state(self):
        seq, now = self.mixer, time.time()
        with seq.lock:
            tracks = [{"id": t["id"], "sound": t["sound"], "label": label(t["sound"]), "vol": t["vol"],
                       "mute": t["mute"], "solo": t["solo"], "steps": t["steps"][seq.pat],
                       "lit": 0 <= now - t.get("_lit", -9) < 0.12, "paper": t.get("paper", False)}
                      for t in seq.tracks]
            used = {p: any(any(v > 0 for v in t["steps"][p]) for t in seq.tracks) for p in PATTERNS}
            transport = {"playing": seq.playing, "rec": seq.rec, "metro": seq.metro, "bpm": seq.bpm,
                         "swing": seq.swing, "master": seq.master, "filter": seq.filter, "pat": seq.pat,
                         "queued": seq.queued, "step": seq.heard_step(), "level": seq.level, "used": used,
                         "recording": None if seq.recording is None else now - seq.rec_started}
        pads = list(self.pads)
        counts = {}
        for p in pads:
            counts[p.get("name")] = counts.get(p.get("name"), 0) + 1
        for t in tracks:
            t["pads"] = counts.get(t["sound"], 0)
        fps = 0.0
        if self.cam_state == "on" and len(self.pair_times) > 1 and now - self.pair_times[-1] < 1:
            fps = (len(self.pair_times) - 1) / max(1e-3, self.pair_times[-1] - self.pair_times[0])
        learned = [n for n, l in (("depth", self.depth), ("shadow", self.shadow)) if l.lift is not None]
        cam_label = {"off": "off", "starting": "starting"}.get(self.cam_state) or \
            {"scan": "scanning - hands off", "surface": "measuring the table"}.get(self.phase) or \
            ("ready" if learned else "learning your touch")
        sh = self.sheet
        with sh.lock:
            sheet = {"seen": sh.visible(), "markers": sh.markers if sh.visible() else 0, "rows": sh.rows,
                     "grid": sh.grid, "follow": self.cfg["follow_paper"], "sounds": self.cfg["sheet_rows"]}
        return {
            "transport": transport, "tracks": tracks,
            "camera": {"on": self.cam_on.is_set(), "state": self.cam_state, "label": cam_label, "phase": self.phase,
                       "fps": round(fps, 1), "learned": learned, "flat": self.flat, "bias": self.bias,
                       "mirror": self.mirror, "flip": self.flipv, "tap_offset": self.cfg["tap_offset_ms"]},
            "pads": [{"i": i, "sound": p.get("name"), "label": label(p["name"]) if p.get("name") else "reading...",
                      "kind": p.get("kind"), "hits": p.get("_hits", 0),
                      "color": "#%02x%02x%02x" % tuple(int(c) for c in p["color"][::-1])} for i, p in enumerate(pads)],
            "sheet": sheet,
            "audio": {"device": seq.device, "latency_ms": round(seq.latency_ms), "devices": output_devices()},
            "sounds": sound_choices(), "events": list(self.events)[:12], "recordings": recordings(),
        }


def output_devices():
    import sounddevice as sd
    return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]


def recordings():
    out = []
    try:
        names = sorted((f for f in os.listdir(REC_DIR) if f.endswith(".wav")), reverse=True)[:12]
    except FileNotFoundError:
        return out
    for n in names:
        try:
            with wave.open(os.path.join(REC_DIR, n)) as w:
                out.append({"name": n, "secs": round(w.getnframes() / w.getframerate(), 1)})
        except (wave.Error, OSError):
            pass
    return out


# ---------------------------------------------------------------- http
class Handler(http.server.BaseHTTPRequestHandler):
    studio = None

    def log_message(self, *_):
        pass

    def _send(self, code, body, ctype="application/json", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st, path = self.studio, urllib.parse.urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                with open(UI, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/state":
                self._send(200, json.dumps(st.state()).encode())
            elif path == "/video":
                self._mjpeg()
            elif path in ("/sheet.pdf", "/sheet.png"):
                png, pdf = make_sheet(st.cfg["sheet_rows"])
                if path.endswith(".pdf"):
                    self._send(200, pdf, "application/pdf", [("Content-Disposition", 'inline; filename="stickybeat-sheet.pdf"')])
                else:
                    self._send(200, png, "image/png")
            elif path.startswith("/rec/"):
                name = os.path.basename(urllib.parse.unquote(path[5:]))
                with open(os.path.join(REC_DIR, name), "rb") as f:
                    self._send(200, f.read(), "audio/wav")
            else:
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        st, u = self.studio, urllib.parse.urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if u.path == "/cmd":
            res = st.command(json.loads(body or b"{}"))
        elif u.path == "/upload":
            name = urllib.parse.parse_qs(u.query).get("name", ["sound.wav"])[0]
            res = st.upload(name, body) if len(body) < 60_000_000 else {"error": "that file is too big (60 MB max)"}
        else:
            res = {"error": "not found"}
        self._send(200, json.dumps(res).encode())

    def _mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last = -1
        while not self.studio.quit:
            if self.studio.view_seq == last:
                time.sleep(0.02)
                continue
            last, jpg = self.studio.view_seq, self.studio.jpeg
            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode()
                             + b"\r\n\r\n" + jpg + b"\r\n")


def load_project():
    try:
        with open(PROJECT) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="StickyBeat Studio: loop studio played with sticky notes and paper")
    ap.add_argument("--focus", type=float, default=2.5, help="main camera focus in diopters (2.5 = 40 cm)")
    ap.add_argument("--audio", default=None, help="output device name part, e.g. 'MacBook' (default: last used)")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--flip", action="store_true")
    ap.add_argument("--no-camera", action="store_true", help="don't start the phone camera until asked")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    proj = load_project()
    cfg = {"tap_offset_ms": TAP_OFFSET_MS, "follow_paper": True, "sheet_rows": ["kick", "snare", "hat", "clap", "tom", "cowbell"],
           "device": None, **proj.get("cfg", {})}
    device = args.audio or cfg["device"]
    try:
        seq = Sequencer(device)
    except Exception as e:
        print(f"audio device '{device}' failed ({e}) - using the default output", file=sys.stderr)
        seq = Sequencer(None)
    cfg["device"] = args.audio or cfg["device"]
    if proj.get("seq"):
        seq.load_json(proj["seq"])
    if not seq.tracks:
        seq.starter()

    studio = Studio(args, seq, cfg)
    studio.log(f"audio: {seq.device}, output latency {seq.latency_ms:.0f} ms")
    Handler.studio = studio
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{args.port}"
    print(f"StickyBeat Studio on {url}", flush=True)
    if not args.no_browser:
        subprocess.Popen(["open", url])
    if not args.no_camera:
        studio.cam_on.set()
    try:
        studio.run()
    except KeyboardInterrupt:
        pass
    finally:
        studio.quit = True
        studio.cam_on.clear()
        if seq.recording is not None:
            print(f"saved recordings/{seq.stop_recording()}")
        studio.save()
        seq.close()
        server.shutdown()


if __name__ == "__main__":
    main()
