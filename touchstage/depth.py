"""AI depth on the hand only, from the phone and the Mac camera at once (one batched pass on the Mac GPU).

Each camera: crop a square around the hand (with some wall around it) -> Depth Anything V2 Small (metric indoor) ->
'finger off the wall' = wall depth just around the hand - depth at the fingertip pad (mm). Then a median + exponential
smoother, and 'closeness' = where that value sits between YOUR touch level (T) and hover level (H): 0 = touch, 1 = hover.

Model code + weights live in ~/Projects/DepthTouchTest (da2/, ckpt/), downloaded for the 4 Nov depth test.
"""
import collections
import os
import sys
import threading
import time

import cv2
import numpy as np
import torch

ROOT = os.path.expanduser("~/Projects/DepthTouchTest")
MODELS = {"metric": ("depth_anything_v2_metric_hypersim_vits.pth", 20.0),
          "paper": ("depth_anything_v2_vits_d405_finetuned.pth", 0.5)}
DEV = "mps" if torch.backends.mps.is_available() else "cpu"
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
ALPHA = {1: 0.6, 2: 0.45, 3: 0.32, 4: 0.22, 5: 0.14}
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]
INDEX = {(5, 6), (6, 7), (7, 8)}


def load(name="metric"):
    sys.path.insert(0, os.path.join(ROOT, "da2", "metric_depth"))
    from depth_anything_v2.dpt import DepthAnythingV2
    sys.path.pop(0)
    f, md = MODELS[name]
    net = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384], max_depth=md)
    sd = torch.load(os.path.join(ROOT, "ckpt", f), map_location="cpu", weights_only=True)
    for k in ("model", "state_dict"):
        if isinstance(sd, dict) and k in sd and isinstance(sd[k], dict):
            sd = sd[k]
    net.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()}, strict=False)
    return net.to(DEV).eval()


def hand_crop(img, lm, scale=1.9, box=None):
    lm = np.asarray(lm, float)
    if box is None:
        c = (lm.min(0) + lm.max(0)) / 2
        side = max(np.ptp(lm[:, 0]), np.ptp(lm[:, 1]), 40) * scale
    else:
        c, side = box
    h, w = img.shape[:2]
    x0, y0 = int(max(0, c[0] - side / 2)), int(max(0, c[1] - side / 2))
    x1, y1 = int(min(w, c[0] + side / 2)), int(min(h, c[1] + side / 2))
    if x1 - x0 < 30 or y1 - y0 < 30:
        return None, None
    return img[y0:y1, x0:x1], lm - [x0, y0]


def infer(net, crops, size):
    """Batched depth for several crops (metres), each returned at its crop's size."""
    x = np.stack([(cv2.resize(cv2.cvtColor(c, cv2.COLOR_BGR2RGB), (size, size), interpolation=cv2.INTER_CUBIC)
                   .astype(np.float32) / 255 - MEAN) / STD for c in crops]).transpose(0, 3, 1, 2)
    with torch.no_grad():
        d = net(torch.from_numpy(np.ascontiguousarray(x)).to(DEV))
        d = d.float().cpu().numpy()
    return [cv2.resize(d[i], (c.shape[1], c.shape[0]), interpolation=cv2.INTER_LINEAR) for i, c in enumerate(crops)]


def hand_mask(shape, lm):
    h, w = shape[:2]
    m = np.zeros((h, w), np.uint8)
    p = np.asarray(lm, np.int32)
    for a, b in BONES:
        cv2.line(m, tuple(p[a]), tuple(p[b]), 255, max(6, int(0.09 * np.linalg.norm(lm[9] - lm[0]))))
    cv2.fillConvexPoly(m, cv2.convexHull(p[[0, 1, 5, 9, 13, 17]]), 255)
    return m


def off_wall_mm(dep, lm):
    lm = np.asarray(lm, float)
    p = lm[8] + 0.3 * (lm[7] - lm[8])                       # finger pad, not its edge
    L = float(np.linalg.norm(lm[9] - lm[0])) + 1e-6
    h, w = dep.shape
    x, y = int(round(p[0])), int(round(p[1]))
    if not (3 <= x < w - 3 and 3 <= y < h - 3):
        return None
    tip = float(np.median(dep[y - 2:y + 3, x - 2:x + 3]))
    mask = cv2.dilate(hand_mask(dep.shape, lm), np.ones((31, 31), np.uint8))
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - p[0], yy - p[1])
    ring = (rr > 0.6 * L) & (rr < 1.6 * L) & (mask == 0)
    if ring.sum() < 30:
        return None
    return 1000.0 * (float(np.median(dep[ring])) - tip)


def tile(crop, dep, lm, size_wh, rng=None):
    """Camera crop in grey, the hand coloured by depth (red/yellow = nearer you), skeleton, index finger in cyan."""
    w, h = size_wh
    m = hand_mask(crop.shape, lm)
    grey = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    hv = -dep[m > 0]
    lo, hi = (np.percentile(hv, 5), np.percentile(hv, 95)) if hv.size else (0, 1)
    if rng is not None:                                 # running colour range: no colour flashing
        if rng[0] is None:
            rng[:] = [lo, hi]
        rng[0] += 0.15 * (lo - rng[0])
        rng[1] += 0.15 * (hi - rng[1])
        lo, hi = rng
    n = np.clip((-dep - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    col = cv2.applyColorMap(n, cv2.COLORMAP_TURBO)
    out = np.where(m[..., None] > 0, (0.75 * col + 0.25 * crop).astype(np.uint8), (0.55 * grey).astype(np.uint8))
    sx, sy = w / crop.shape[1], h / crop.shape[0]
    out = cv2.resize(out, (w, h))
    P = lambda i: (int(lm[i][0] * sx), int(lm[i][1] * sy))
    for a, b in BONES:
        idx = (a, b) in INDEX
        cv2.line(out, P(a), P(b), (255, 230, 0) if idx else (235, 235, 235), 4 if idx else 1, cv2.LINE_AA)
    cv2.circle(out, P(8), 10, (80, 255, 80), 2, cv2.LINE_AA)
    return out


class Cam:
    def __init__(self, name):
        self.name = name
        self.win = collections.deque(maxlen=11)
        self.value = None                  # smoothed mm off the wall
        self.raw = None
        self.hist = collections.deque(maxlen=200)     # (t, smoothed)
        self.levels = {}
        self.tile = None
        self.t = 0.0
        self.raw_hist = collections.deque(maxlen=200)  # (t, raw)
        self.box = None                                 # smoothed crop (centre, side)
        self.rng = [None, None]
        self.lost_since = None
        self.seen = False

    def feed(self, r, smooth, t):
        self.raw = r
        if r is None:                                   # keep the last value through short dropouts (no flicker)
            self.lost_since = self.lost_since or t
            if t - self.lost_since > 0.5:
                self.win.clear()
                self.value, self.seen, self.box = None, False, None
            return
        self.lost_since, self.seen = None, True
        self.raw_hist.append((t, r))
        self.win.append(r)
        med = float(np.median(list(self.win)[-(1 + 2 * smooth):]))
        prev = self.value if self.value is not None else (self.hist[-1][1] if self.hist else None)
        self.value = med if prev is None else prev + ALPHA[smooth] * (med - prev)
        self.hist.append((t, self.value))
        self.t = t

    def crop_box(self, lm, scale=1.9):
        lm = np.asarray(lm, float)
        c = (lm.min(0) + lm.max(0)) / 2
        side = max(np.ptp(lm[:, 0]), np.ptp(lm[:, 1]), 40) * scale
        if self.box is None or np.linalg.norm(c - self.box[0]) > 0.5 * self.box[1]:
            self.box = (c, side)
        else:
            self.box = (self.box[0] + 0.35 * (c - self.box[0]), self.box[1] + 0.2 * (side - self.box[1]))
        return self.box

    def closeness(self):
        """0 = at your touch level, 1 = at your hover level; None if not set or not seen."""
        if self.value is None or "touch" not in self.levels or "hover" not in self.levels:
            return None
        g = self.levels["hover"] - self.levels["touch"]
        return None if abs(g) < 1e-6 else (self.value - self.levels["touch"]) / g

    def set_level(self, which):
        vals = [v for _, v in list(self.hist)[-8:]]
        if vals:
            self.levels[which] = float(np.median(vals))


class DepthWorker(threading.Thread):
    """Runs continuously on the newest phone + Mac frames the app hands over."""

    def __init__(self, model="metric", size=266, smooth=4):
        super().__init__(daemon=True)
        self.model, self.size, self.smooth = model, size, smooth
        self.net = load(model)
        self.cams = {"phone": Cam("phone"), "mac": Cam("mac")}
        self.lock = threading.Lock()
        self.job = None
        self.fps = 0.0
        self.ms = 0.0
        self.running = True
        self.want_model = model

    def submit(self, phone_img, phone_lm, mac_img, mac_lm):
        with self.lock:
            self.job = (time.time(), phone_img, phone_lm, mac_img, mac_lm)

    def run(self):
        last = time.time()
        while self.running:
            if self.want_model != self.model:
                self.net = load(self.want_model)
                self.model = self.want_model
                for c in self.cams.values():
                    c.levels.clear()
            with self.lock:
                job, self.job = self.job, None
            if job is None:
                time.sleep(0.005)
                continue
            t, pimg, plm, mimg, mlm = job
            crops, lms, who = [], [], []
            for name, img, lm in (("phone", pimg, plm), ("mac", mimg, mlm)):
                if img is not None and lm is not None:
                    c, l = hand_crop(img, lm, box=self.cams[name].crop_box(lm))
                    if c is not None:
                        crops.append(c)
                        lms.append(l)
                        who.append(name)
            t0 = time.time()
            deps = infer(self.net, crops, self.size) if crops else []
            self.ms = 1000 * (time.time() - t0)
            seen = set()
            for name, c, l, d in zip(who, crops, lms, deps):
                self.cams[name].feed(off_wall_mm(d, l), self.smooth, t)
                self.cams[name].tile = tile(c, d, l, (350, 262), self.cams[name].rng)
                seen.add(name)
            for name, cam in self.cams.items():
                if name not in seen:
                    cam.feed(None, self.smooth, t)
                    if not cam.seen:
                        cam.tile = None
            now = time.time()
            self.fps = 0.8 * self.fps + 0.2 / max(1e-3, now - last)
            last = now
