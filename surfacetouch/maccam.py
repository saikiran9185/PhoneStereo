"""Mac camera as a second viewpoint of the projected wall.

The phone and the Mac camera each map the fingertip onto the wall with their own homography (both found from
the same 15 projected markers). A finger on the wall lands on the same wall point in both views. A hovering
finger does not: each camera projects it along its own line of sight, so the two wall points split apart by
roughly hover height x (difference of the two viewing angles). With the Mac and phone on opposite sides of
the wall this is a far stronger touch signal than the 1 cm phone lens pair.
"""
import collections
import os
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(HERE, "hand_landmarker.task")
WIDTH = 1280                    # processing width (markers + hand tracking): native 720p


class MacCam(threading.Thread):
    def __init__(self, index=0, log=print):
        super().__init__(daemon=True)
        self.index, self.log = index, log
        self.hist = collections.deque(maxlen=90)     # (time, [hand landmarks 21x2 px]) at processing size
        self.frame = None
        self.lock = threading.Lock()
        self.running = True
        self.error = None
        self.fps = 0.0
        self.latest_t = 0.0
        self.roi = None             # (x0, y0, x1, y1) at processing size: track the hand inside the wall area

    def run(self):
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            cap = cv2.VideoCapture(self.index, cv2.CAP_AVFOUNDATION)
            if not cap.isOpened():
                self.error = "Mac camera not allowed or not found (Privacy & Security > Camera)"
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=MODEL), running_mode=vision.RunningMode.VIDEO,
                num_hands=2, min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
        except Exception as e:                     # never take the phone pipeline down with it
            self.error = f"Mac camera failed: {e}"
            return
        times, fails, last_ms = collections.deque(maxlen=30), 0, 0
        while self.running:
            ok, f = cap.read()
            t = time.time()
            if not ok:
                fails += 1
                if fails > 50:
                    self.error = "Mac camera stopped delivering frames"
                    break
                time.sleep(0.02)
                continue
            fails = 0
            h, w = f.shape[:2]
            small = cv2.resize(f, (WIDTH, int(round(WIDTH * h / w))), interpolation=cv2.INTER_AREA)
            ms = max(last_ms + 1, int(t * 1000))
            last_ms = ms
            x0, y0, x1, y1 = self.roi or (0, 0, small.shape[1], small.shape[0])
            crop = np.ascontiguousarray(small[y0:y1, x0:x1])
            res = hands.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                  data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)), ms)
            sw, sh = crop.shape[1], crop.shape[0]
            lms = [np.array([(x0 + p.x * sw, y0 + p.y * sh) for p in hl], np.float32) for hl in res.hand_landmarks]
            times.append(t)
            self.fps = (len(times) - 1) / max(1e-3, times[-1] - times[0]) if len(times) > 1 else 0.0
            with self.lock:
                self.hist.append((t, lms))
                self.frame = small
                self.latest_t = t
        cap.release()
        try:
            hands.close()
        except Exception:                                 # interpreter already shutting down: nothing to free
            pass

    def latest(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def hands_at(self, t, tol=0.12):
        """Hand landmarks from the Mac frame nearest to time t, or None if none within tol seconds."""
        with self.lock:
            hist = list(self.hist)
        if not hist:
            return None
        tt, lms = min(hist, key=lambda r: abs(r[0] - t))
        return lms if abs(tt - t) < tol else None


def wall_roi(H_cam_to_wall, W, H, shape, margin=0.35):
    """Bounding box (x0, y0, x1, y1) in a camera image of the projected wall plus a margin for the hand."""
    c = np.array([[[0, 0], [W, 0], [W, H], [0, H]]], np.float32)
    q = cv2.perspectiveTransform(c, np.linalg.inv(H_cam_to_wall))[0]
    x0, y0 = q.min(0)
    x1, y1 = q.max(0)
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    h, w = shape[:2]
    x0, y0 = int(max(0, x0 - mx)), int(max(0, y0 - my))
    x1, y1 = int(min(w, x1 + mx)), int(min(h, y1 + my))
    return (x0, y0, x1, y1) if x1 - x0 > 64 and y1 - y0 > 64 else None


def wall_points(lm, H, idx=(4, 8, 12)):
    """Thumb / index / middle tips mapped onto the wall (projector pt)."""
    if lm is None or H is None:
        return {}
    w = cv2.perspectiveTransform(np.asarray(lm, np.float32)[list(idx)][None], H)[0]
    return {i: (float(p[0]), float(p[1])) for i, p in zip(idx, w)}


def features(lm_phone, Hs, mac_hands, Hm, W):
    """Two-view features. lm_phone: phone main-camera landmarks; Hs / Hm: phone / Mac camera -> projector.
    Returns (feature dict, Mac landmarks used or None). Distances are in finger lengths on the wall."""
    off = {"mac_ok": 0, "mac_d_n": 0.0, "mac_dx_n": 0.0, "mac_dy_n": 0.0}
    if Hm is None or not mac_hands:
        return off, None
    pw = cv2.perspectiveTransform(lm_phone[None].astype(np.float32), Hs)[0]
    L = float(np.linalg.norm(pw[8] - pw[6]))          # tip to PIP, on the wall
    if L < 1e-3:
        return off, None
    best = None
    for lm in mac_hands:
        mw = cv2.perspectiveTransform(lm[None].astype(np.float32), Hm)[0]
        d = mw[8] - pw[8]
        dist = float(np.linalg.norm(d))
        if best is None or dist < best[0]:
            best = (dist, d, lm)
    dist, d, lm = best
    if dist > 0.25 * W:                                # a different hand, not this finger
        return off, None
    return {"mac_ok": 1, "mac_d_n": round(dist / L, 3), "mac_dx_n": round(float(d[0]) / L, 3),
            "mac_dy_n": round(float(d[1]) / L, 3)}, lm
