"""Live hand-only AI depth from BOTH cameras, fused into one TOUCH / HOVER decision.

  phone camera  -> WHERE: MediaPipe fingertip mapped onto the projected wall (SurfaceTouch's calibration)
  phone + Mac   -> HOW FAR off the wall: AI depth run only on a crop around the hand (more detail, less work),
                   fingertip depth compared with the wall just around the hand, in each camera
  fused         -> average of the two cameras' 'closeness' (0 = your touch level, 1 = your hover level), smoothed

Window:
  top     phone hand | Mac hand: camera crop, hand coloured by AI depth, skeleton, index finger highlighted
  bottom  per-camera and fused signal over the last 10 s, TOUCH / HOVER, closeness bar, fingertip on the wall

Keys:  T  finger ON the wall, hold still, press T          (touch level, both cameras)
       H  finger 5-10 cm OFF the wall, hold still, press H (hover level)
       F  fuse: both cameras / Mac only / phone only
       1 / 2 / 3  model: general DA-V2 / metric indoor DA-V2 (default) / CVPR 2026 paper
       + / -  smoothing      S  crop size 266 <-> 364      Q  quit
"""
import collections
import json
import os
import time

import cv2
import numpy as np
import torch

import depth_touch_test as dt

LIVE = os.path.expanduser("~/Library/Caches/surfacetouch_live")
NAMES = {ord("1"): "da2_relative", ord("2"): "da2_metric", ord("3"): "paper_d405"}
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]
INDEX = {(5, 6), (6, 7), (7, 8)}
ALPHA = {1: 0.6, 2: 0.45, 3: 0.32, 4: 0.22, 5: 0.14}
TILE = (560, 420)


def hand_crop(img, lm, scale=1.9):
    """Square crop around the hand with some wall around it; returns crop, landmarks in crop coords, offset."""
    lm = np.asarray(lm, float)
    c = (lm.min(0) + lm.max(0)) / 2
    side = max(np.ptp(lm[:, 0]), np.ptp(lm[:, 1]), 40) * scale
    h, w = img.shape[:2]
    x0, y0 = int(max(0, c[0] - side / 2)), int(max(0, c[1] - side / 2))
    x1, y1 = int(min(w, c[0] + side / 2)), int(min(h, c[1] + side / 2))
    if x1 - x0 < 30 or y1 - y0 < 30:
        return None, None, None
    return img[y0:y1, x0:x1], lm - [x0, y0], (x0, y0)


class Cam:
    def __init__(self, label):
        self.label = label
        self.win = collections.deque(maxlen=11)
        self.sm = None
        self.hist = collections.deque(maxlen=150)
        self.levels = {}
        self.tile = np.zeros((TILE[1], TILE[0], 3), np.uint8)
        self.ok = False

    def update(self, net, kind, img, lm, size, smooth):
        self.ok = False
        if img is None or lm is None:
            self.win.clear()
            return None
        crop, clm, _ = hand_crop(img, lm)
        if crop is None:
            return None
        with torch.no_grad():
            dep = net.infer_image(crop, size)
        r = dt.signal(dep, clm, kind)
        self.tile = render(crop, dep, clm, kind, self.label, r)
        if r is None:
            return None
        self.win.append(r)
        med = float(np.median(list(self.win)[-(1 + 2 * smooth):]))
        self.sm = med if self.sm is None else self.sm + ALPHA[smooth] * (med - self.sm)
        self.hist.append(self.sm)
        self.ok = True
        return self.sm

    def frac(self):
        """0 = at the touch level, 1 = at the hover level (None until T and H are set)."""
        if not self.ok or "touch" not in self.levels or "hover" not in self.levels:
            return None
        gap = self.levels["hover"] - self.levels["touch"]
        return None if abs(gap) < 1e-9 else (self.sm - self.levels["touch"]) / gap


def render(crop, dep, lm, kind, label, r):
    """Camera crop in grey, the hand coloured by AI depth, skeleton on top, index finger highlighted."""
    h, w = crop.shape[:2]
    grey = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    mask = np.zeros((h, w), np.uint8)
    pts = np.asarray(lm, np.int32)
    for a, b in BONES:                                 # hand shape: thick bones + palm
        cv2.line(mask, tuple(pts[a]), tuple(pts[b]), 255, max(6, int(0.09 * np.linalg.norm(lm[9] - lm[0]))))
    cv2.fillConvexPoly(mask, cv2.convexHull(pts[[0, 1, 5, 9, 13, 17]]), 255)
    d = dep if kind == "relative" else -dep            # brighter = nearer the camera
    hv = d[mask > 0]
    lo, hi = (np.percentile(hv, 5), np.percentile(hv, 95)) if hv.size else (0, 1)
    n = np.clip((d - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    col = cv2.applyColorMap(n, cv2.COLORMAP_TURBO)
    out = np.where(mask[..., None] > 0, (0.75 * col + 0.25 * crop).astype(np.uint8), (0.6 * grey).astype(np.uint8))
    sx, sy = TILE[0] / w, TILE[1] / h
    out = cv2.resize(out, TILE)
    P = lambda i: (int(lm[i][0] * sx), int(lm[i][1] * sy))
    for a, b in BONES:
        cv2.line(out, P(a), P(b), (0, 255, 255) if (a, b) in INDEX else (230, 230, 230), 4 if (a, b) in INDEX else 1,
                 cv2.LINE_AA)
    cv2.circle(out, P(8), 9, (0, 255, 0), 2, cv2.LINE_AA)
    cv2.putText(out, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    if r is not None:
        cv2.putText(out, f"off wall {r:.0f} mm" if kind == "metric" else f"off wall {r:.3f}", (12, TILE[1] - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return out


def blank(label, why):
    t = np.full((TILE[1], TILE[0], 3), 30, np.uint8)
    cv2.putText(t, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
    cv2.putText(t, why, (12, TILE[1] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (140, 140, 140), 1)
    return t


def main():
    os.makedirs(LIVE, exist_ok=True)
    open(os.path.join(LIVE, "want"), "w").close()
    name, size, smooth = "da2_metric", 266, 3
    net, kind = dt.build(name)
    phone, mac = Cam("PHONE  (where + depth)"), Cam("MAC  (depth)")
    fuse_modes = ["both", "mac", "phone"]
    fuse = 0
    fused_hist = collections.deque(maxlen=150)
    is_touch, streak = False, 0
    last_mt, fps, t_prev = 0, 0.0, time.time()
    cv2.namedWindow("Hand depth - phone + Mac", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Hand depth - phone + Mac", 1120, 660)
    try:
        while True:
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            if k in NAMES and NAMES[k] != name:
                del net
                if dt.DEV == "mps":
                    torch.mps.empty_cache()
                name = NAMES[k]
                net, kind = dt.build(name)
                for c in (phone, mac):
                    c.__init__(c.label)
                fused_hist.clear()
            if k in (ord("+"), ord("=")):
                smooth = min(5, smooth + 1)
            if k == ord("-"):
                smooth = max(1, smooth - 1)
            if k == ord("s"):
                size = 364 if size == 266 else 266
            if k == ord("f"):
                fuse = (fuse + 1) % 3
            mp = os.path.join(LIVE, "meta.json")
            try:
                mt = os.path.getmtime(mp)
            except OSError:
                time.sleep(0.02)
                continue
            if mt == last_mt:
                time.sleep(0.005)
                continue
            last_mt = mt
            try:
                meta = json.load(open(mp))
                pimg = cv2.imread(os.path.join(LIVE, "frame.jpg"))
                mimg = cv2.imread(os.path.join(LIVE, "mac.jpg")) if meta.get("mac_lm") else None
            except Exception:
                continue
            phone.update(net, kind, pimg, meta.get("lm"), size, smooth)
            mac.update(net, kind, mimg, meta.get("mac_lm"), size, smooth)
            now = time.time()
            fps = 0.8 * fps + 0.2 / max(1e-3, now - t_prev)
            t_prev = now
            for c in (phone, mac):
                if k == ord("t") and c.hist:
                    c.levels["touch"] = float(np.median(list(c.hist)[-8:]))
                if k == ord("h") and c.hist:
                    c.levels["hover"] = float(np.median(list(c.hist)[-8:]))
            use = {"both": (phone, mac), "mac": (mac,), "phone": (phone,)}[fuse_modes[fuse]]
            fr = [c.frac() for c in use if c.frac() is not None]
            fused = float(np.mean(fr)) if fr else None
            if fused is not None:
                fused_hist.append(fused)
                want = True if fused < 0.35 else False if fused > 0.65 else is_touch
                streak = streak + 1 if want != is_touch else 0
                if streak >= 2:
                    is_touch, streak = want, 0
            # ---- draw
            top = np.hstack([phone.tile if meta.get("lm") else blank(phone.label, "no hand in the phone view"),
                             mac.tile if meta.get("mac_lm") else blank(mac.label, "no hand in the Mac view")])
            panel = np.full((240, TILE[0] * 2, 3), 25, np.uint8)
            cv2.putText(panel, f"{name}  crop {size}px  {fps:4.1f} fps  smooth {smooth}/5  fuse: {fuse_modes[fuse]}",
                        (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1)
            pos = meta.get("pos")
            cv2.putText(panel, "fingertip on wall (phone): " + (f"x {pos[0]:.3f}  y {pos[1]:.3f}" if pos else "-"),
                        (14, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)
            if fused is None:
                cv2.putText(panel, "finger ON the wall + T,  then 5-10 cm OFF + H", (14, 82),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 200, 255), 1)
            else:
                cv2.putText(panel, "TOUCH" if is_touch else "HOVER", (880, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                            (60, 220, 60) if is_touch else (60, 160, 230), 3)
                bw = int(np.clip(1 - fused, 0, 1) * 260)
                cv2.rectangle(panel, (850, 66), (1110, 84), (70, 70, 70), 1)
                cv2.rectangle(panel, (850, 66), (850 + bw, 84), (60, 220, 60) if is_touch else (60, 160, 230), -1)
            # graph: per-camera closeness (thin) and fused (thick), 0 = touch level (bottom), 1 = hover level (top)
            gy = lambda v: int(222 - np.clip(v, -0.3, 1.3) / 1.6 * 110 - 0.3 / 1.6 * 110)
            for v, c in ((0.0, (60, 220, 60)), (1.0, (60, 160, 230))):
                cv2.line(panel, (14, gy(v)), (TILE[0] * 2 - 14, gy(v)), c, 1)
            for cam, colr in ((phone, (200, 120, 255)), (mac, (255, 200, 90))):
                if "touch" in cam.levels and "hover" in cam.levels and len(cam.hist) > 2:
                    g = cam.levels["hover"] - cam.levels["touch"] or 1e-9
                    pts = np.array([[14 + i * 7, gy((v - cam.levels["touch"]) / g)] for i, v in enumerate(cam.hist)], np.int32)
                    cv2.polylines(panel, [pts], False, colr, 1)
            if len(fused_hist) > 2:
                pts = np.array([[14 + i * 7, gy(v)] for i, v in enumerate(fused_hist)], np.int32)
                cv2.polylines(panel, [pts], False, (255, 255, 255), 3)
            cv2.putText(panel, "purple = phone   yellow = Mac   white = fused   T/H set   F fuse   1/2/3 model   +/- smooth   Q quit",
                        (14, 236), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (140, 140, 140), 1)
            cv2.imshow("Hand depth - phone + Mac", np.vstack([top, panel]))
    finally:
        try:
            os.remove(os.path.join(LIVE, "want"))
        except OSError:
            pass
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
