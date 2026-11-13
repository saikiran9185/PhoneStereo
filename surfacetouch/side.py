"""Side mode: the Mac faces the wall and gives POSITION; the phone looks along the wall from the side and judges TOUCH.

The phone can't read the wall markers at a grazing angle, so it learns the wall from your finger: you touch 6 dots,
and each touch pairs the phone's fingertip (image px) with the Mac's fingertip on the wall. A homography from those
pairs (phone image <-> wall) says where a fingertip ON THE WALL at the Mac's position would appear to the phone.

  side_gap_n = | phone fingertip  -  where it would be if it touched the wall |  /  finger length (phone px)

Touching: ~0 (landmark noise). Hovering: the phone sees the finger lifted off the wall almost at full size, because
it looks along the wall (the two views are ~90 degrees apart instead of the ~10 degrees of the front setup).
"""
import cv2
import numpy as np

DOTS = [(0.2, 0.3), (0.5, 0.3), (0.8, 0.3), (0.2, 0.7), (0.5, 0.7), (0.8, 0.7)]
READY, HOLD = 1.6, 2.0
MIN_FRAMES = 5


def mac_tip_wall(mac_lms, Hm):
    """All Mac hands' index tips on the wall -> list of (wall_xy, landmarks)."""
    out = []
    for lm in mac_lms or []:
        w = cv2.perspectiveTransform(np.asarray(lm, np.float32)[[8]][None], Hm)[0, 0]
        out.append((w, lm))
    return out


def fit(phone_pts, wall_pts):
    """Homography wall -> phone image from >= 4 touches, and its fit error (phone px)."""
    P, Wl = np.asarray(phone_pts, np.float32), np.asarray(wall_pts, np.float32)
    if len(P) < 4:
        return None, None
    H, _ = cv2.findHomography(Wl, P, 0)
    if H is None:
        return None, None
    err = np.linalg.norm(cv2.perspectiveTransform(Wl[None], H)[0] - P, axis=1)
    return H, float(np.median(err))


def features(lm_phone, mac_hands, Hm, Hside):
    """-> (feature dict, chosen Mac landmarks, wall position of the Mac fingertip or None)."""
    off = {"side_ok": 0, "side_gap_n": 0.0, "side_dx_n": 0.0, "side_dy_n": 0.0}
    if lm_phone is None or Hm is None or Hside is None or not mac_hands:
        return off, None, None
    tip = np.asarray(lm_phone[8], float)
    L = float(np.linalg.norm(tip - np.asarray(lm_phone[6], float)))
    if L < 3:
        return off, None, None
    best = None
    for w, lm in mac_tip_wall(mac_hands, Hm):
        pred = cv2.perspectiveTransform(np.array([[w]], np.float32), Hside)[0, 0]
        d = tip - pred
        if best is None or np.linalg.norm(d) < np.linalg.norm(best[0]):
            best = (d, lm, w)
    d, lm, w = best
    return ({"side_ok": 1, "side_gap_n": round(float(np.linalg.norm(d)) / L, 3),
             "side_dx_n": round(float(d[0]) / L, 3), "side_dy_n": round(float(d[1]) / L, 3)}, lm, w)


def heuristic(f):
    """Until Experiment T trains a model: close to the predicted wall point = touch."""
    return float(1 / (1 + np.exp(-(0.45 - f["side_gap_n"]) / 0.1)))
