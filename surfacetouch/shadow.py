"""Finger-shadow features from the phone's main camera.

The projector lights the wall from one place and the phone looks from another, so a finger in the air casts a
shadow that the camera sees BESIDE the finger; the gap closes as the finger reaches the wall and is zero (or the
shadow hides behind the finger) on contact.

Photometric model: during calibration the wall is photographed while the projector shows full white
(`white`, camera grey levels). For any projected content I (0..1, warped into the camera picture) the expected
camera brightness is  k * white * I,  where k is a global gain re-estimated every frame from pixels away from
the hand (this absorbs the phone's auto-exposure). A pixel is SHADOW when it is much darker than expected while
the projector is lighting it, and it is not part of the hand.
"""
import cv2
import numpy as np

SHADOW_RATIO = 0.5          # observed / expected below this = shadow
LIT_MIN = 0.3               # only judge pixels the projector lights at least this much
FINGER_CHAINS = [(0, 1, 2, 3, 4), (0, 5, 6, 7, 8), (0, 9, 10, 11, 12), (0, 13, 14, 15, 16), (0, 17, 18, 19, 20)]


def hand_mask(shape, lm, width):
    """Filled mask of the hand from its 21 landmarks (camera pixels)."""
    m = np.zeros(shape[:2], np.uint8)
    pts = np.round(lm).astype(np.int32)
    w = max(3, int(width))
    for ch in FINGER_CHAINS:
        cv2.polylines(m, [pts[list(ch)]], False, 255, w, cv2.LINE_AA)
    cv2.fillConvexPoly(m, cv2.convexHull(pts[[0, 1, 5, 9, 13, 17]]), 255)
    for i in (4, 8, 12, 16, 20):                         # round finger tips
        cv2.circle(m, tuple(pts[i]), w // 2 + 1, 255, -1)
    return m


def features(gray, expected, white, lm):
    """gray: camera grey (float32), expected: projected brightness 0..1 warped into the camera picture,
    white: camera grey of the full-white projection, lm: 21x2 hand landmarks in camera pixels.
    -> (feature dict, debug dict)."""
    tip, pip_ = np.asarray(lm[8], float), np.asarray(lm[6], float)
    L = float(np.linalg.norm(tip - pip_))
    out = {"finger_px": round(L, 1), "found": 0, "gap_n": 3.0, "near_n": 3.0, "sep_n": 3.0, "along_n": 0.0,
           "area_n": 0.0, "lit": 0.0, "dx_n": 0.0, "dy_n": 0.0}
    dbg = {}
    if L < 6:
        return out, dbg
    h, w = gray.shape
    R = int(4.0 * L)          # wide enough that a far-hover shadow is still found (missing != touching)
    x0, y0 = max(0, int(tip[0]) - R), max(0, int(tip[1]) - R)
    x1, y1 = min(w, int(tip[0]) + R + 1), min(h, int(tip[1]) + R + 1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return out, dbg
    sl = (slice(y0, y1), slice(x0, x1))
    hm = hand_mask((h, w), lm, 0.35 * L)[sl] > 0          # index finger is about 0.35 x its tip-to-PIP length wide
    hm_wide = cv2.dilate(hm.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    g, e, wh = gray[sl], expected[sl], white[sl]
    base = wh * e
    lit = (e > LIT_MIN) & (wh > 15)
    ring = lit & ~hm_wide
    out["lit"] = round(float(lit.mean()), 3)
    if ring.sum() < 30:
        return out, dbg
    k = float(np.median(g[ring] / np.maximum(base[ring], 1)))       # auto-exposure gain
    ratio = g / np.maximum(k * base, 1)
    sh = ring & (ratio < SHADOW_RATIO)
    sh = cv2.morphologyEx(sh.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sh, 8)
    t_local = tip - [x0, y0]
    dbg = {"roi": (x0, y0, x1, y1), "shadow": sh, "ratio": ratio, "hand": hm}
    if n <= 1:
        return out, dbg
    ys, xs = np.nonzero(lab)
    d = np.hypot(xs - t_local[0], ys - t_local[1])
    keep = d <= R
    if keep.sum() < max(6, 0.01 * L * L):
        return out, dbg
    xs, ys, d = xs[keep], ys[keep], d[keep]
    comp = lab[ys[np.argmin(d)], xs[np.argmin(d)]]                     # shadow blob closest to the tip
    cm = lab[ys, xs] == comp
    cx, cy = xs[cm], ys[cm]
    u = (tip - pip_) / L                                                # finger direction
    j = int(np.argmax(cx * u[0] + cy * u[1]))                           # shadow point furthest along the finger
    stip = np.array([cx[j], cy[j]], float)
    gap = stip - t_local
    # separation: lit wall between shadow and finger. Touching -> the shadow is attached to the finger (sep ~ 0);
    # hovering -> a strip of lit wall separates them. Measured on shadow pixels within 2 finger lengths of the tip.
    dist_hand = cv2.distanceTransform((~hm).astype(np.uint8), cv2.DIST_L2, 3)
    close = np.hypot(cx - t_local[0], cy - t_local[1]) <= 2 * L
    sep = float(dist_hand[cy[close], cx[close]].min()) if close.any() else float(dist_hand[cy, cx].min())
    out.update(found=1, gap_n=round(float(np.linalg.norm(gap)) / L, 3), near_n=round(float(d.min()) / L, 3),
               sep_n=round(sep / L, 3), along_n=round(float(gap @ u) / L, 3),
               area_n=round(float(cm.sum()) / (L * L), 3), dx_n=round(float(gap[0]) / L, 3), dy_n=round(float(gap[1]) / L, 3))
    dbg["shadow_tip"] = stip + [x0, y0]
    return out, dbg
