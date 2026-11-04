"""Replay a recorded SurfaceTouch session offline: re-run tracking + features on the saved video and score them
against the labels that were recorded with it (button in Experiment T, cues elsewhere).

  .venv/bin/python -m surfacetouch.replay logs/surface_20261105_101500            # full report
  .venv/bin/python -m surfacetouch.replay <session> --finger lock --mac-lag 3     # try a variant

Tries, without anyone standing at the wall:
  * Mac vs phone time offset: scans -8..+8 frames and keeps the one where both views agree best on held touches
  * pointer finger choice: 'index' (MediaPipe landmark 8) or 'lock' (most extended finger, held for 0.3 s)
  * every contact signal's separation on button labels, the cross-validated model, thumb/index confusion
Writes <session>_replay.json and <session>_replay_rows.csv.
"""
import argparse
import csv
import glob
import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import signs as S  # noqa: E402
from touch3d import parallax  # noqa: E402

from . import contact, maccam, shadow, teach  # noqa: E402

S.NUM_HANDS = 2


def frames(path):
    cap = cv2.VideoCapture(path)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield f
    cap.release()


def detect(tracker, img, roi, ts):
    if roi:
        x0, y0, x1, y1 = roi
        crop = img[y0:y1, x0:x1]
    else:
        x0, y0, crop = 0, 0, img
    sc = min(1.0, 960 / max(1, crop.shape[1]))
    small = crop if sc == 1.0 else cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
    d = S.detect(tracker, np.ascontiguousarray(small), ts)
    if not d:
        return None
    h, w = crop.shape[:2]
    return np.array([(x0 + u * w, y0 + v * h) for u, v in d["lms"]], np.float32)


class FingerLock:
    """Pick the pointing finger as the most extended one (tip far from the knuckle, relative to palm size) and keep it
    unless another finger is clearly more extended for 0.3 s. Guards against index/thumb swaps."""
    TIPS = {4: 2, 8: 5, 12: 9}           # tip -> knuckle

    def __init__(self):
        self.cur, self.cand, self.since = 8, None, 0.0

    def tip(self, lm, t):
        palm = float(np.linalg.norm(lm[9] - lm[0])) + 1e-6
        ext = {i: float(np.linalg.norm(lm[i] - lm[k])) / palm for i, k in self.TIPS.items()}
        best = max(ext, key=ext.get)
        if best != self.cur and ext[best] > 1.25 * ext[self.cur]:
            if self.cand != best:
                self.cand, self.since = best, t
            elif t - self.since > 0.3:
                self.cur, self.cand = best, None
        else:
            self.cand = None
        return self.cur


def with_tip(lm, i):
    """Landmarks with the chosen fingertip moved into slot 8 (features read the pointer from there)."""
    if i == 8:
        return lm
    out = lm.copy()
    out[8], out[6] = lm[i], lm[i - 2]
    return out


def run(base, finger="index", mac_lag=None, max_frames=None, log=print):
    vdir = base + "_video"
    rows_in = list(csv.DictReader(open(os.path.join(vdir, "frames.csv"))))
    calibs = {}
    for p in glob.glob(os.path.join(vdir, "calib_*.json")):
        c = json.load(open(p))
        calibs[c["id"]] = c
        wp = os.path.join(vdir, f"white_{c['id']}.npy")
        c["white"] = np.load(wp) if os.path.exists(wp) else None
    if not calibs:
        raise SystemExit("no calib_*.json in the session folder: was the session calibrated while recording?")
    have_mac = os.path.exists(os.path.join(vdir, "mac.mp4"))
    have_wide = os.path.exists(os.path.join(vdir, "wide.mp4"))
    tr_main, tr_mac = S.make_tracker(), S.make_tracker()
    g_main = frames(os.path.join(vdir, "main.mp4"))
    g_wide = frames(os.path.join(vdir, "wide.mp4")) if have_wide else None
    g_mac = frames(os.path.join(vdir, "mac.mp4")) if have_mac else None
    lock = FingerLock()
    per = []                      # per frame: dict with phone landmarks, mac landmarks, features (no mac yet)
    for n, r in enumerate(rows_in):
        if max_frames and n >= max_frames:
            break
        try:
            fa = next(g_main)
        except StopIteration:
            break
        fb = next(g_wide) if g_wide else None
        fm = next(g_mac) if g_mac else None
        cid = int(r.get("calib") or 0)
        c = calibs.get(cid)
        t = float(r["t"])
        ts = int(t * 1000)
        rec = {"row": r, "c": c, "lm": None, "mac_lms": None, "t": t}
        if c is not None and r["mode"] in ("teach", "validate", "collect", "demo", "menu", "live"):
            lm = detect(tr_main, fa, c["roi"][0] if c.get("roi") else None, ts)
            if fm is not None and c.get("Hm") is not None:
                m = detect(tr_mac, fm, c.get("mac_roi"), ts)
                rec["mac_lms"] = [m] if m is not None else None
            if lm is not None:
                tip = lock.tip(lm, t) if finger == "lock" else 8
                rec["tip"] = tip
                lm8 = with_tip(lm, tip)
                rec["lm"], rec["lm_raw"] = lm8, lm
                Hs = np.array(c["Hs"])
                gray = cv2.GaussianBlur(cv2.cvtColor(fa, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
                white = c["white"] if c["white"] is not None else gray
                f, _ = shadow.features(gray, np.ones_like(gray), white, lm8)    # projected content not known offline
                f["parallax_ok"], f["parallax_v"], f["parallax_y"] = 0, 0.0, 0.0
                if fb is not None and c.get("Hp") is not None:
                    pr = parallax(gray.astype(np.uint8), cv2.cvtColor(fb, cv2.COLOR_BGR2GRAY), np.array(c["Hp"]), lm8)
                    if pr and pr[1] > 0.5:
                        f["parallax_ok"], f["parallax_v"], f["parallax_y"] = 1, float(pr[0][0]), float(pr[0][1])
                s = cv2.perspectiveTransform(np.array([[lm8[8]]], np.float32), Hs)[0, 0]
                rec["pos"] = (float(s[0] / c["W"]), float(s[1] / c["H"]))
                rec["f"] = f
        per.append(rec)
        if n % 300 == 0:
            log(f"  frame {n}/{len(rows_in)}")

    def build(lag):
        out = []
        for i, rec in enumerate(per):
            r, c = rec["row"], rec["c"]
            if "f" not in rec:
                continue
            j = i + lag
            mac = per[j]["mac_lms"] if 0 <= j < len(per) else None
            f = dict(rec["f"])
            mf, _ = maccam.features(rec["lm"], np.array(c["Hs"]), mac, None if c.get("Hm") is None else np.array(c["Hm"]), c["W"])
            f.update(mf)
            row = {"t": rec["t"], "mode": r["mode"], "cue": r["cue"], "spot": int(r["spot"]) if r["spot"] not in ("", None) else -1,
                   "ready": int(r["ready"] or 0), "tx": float(r["tx"] or 0), "ty": float(r["ty"] or 0),
                   "button": int(r["button"]), "tracked": 1, "tip": rec.get("tip"),
                   "x": round(rec["pos"][0], 4), "y": round(rec["pos"][1], 4), **f}
            for cam, lms, H in (("phone", rec["lm_raw"], c["Hs"]), ("mac", (mac or [None])[0], c.get("Hm"))):
                if lms is not None and H is not None:
                    for k, (x, y) in maccam.wall_points(lms, np.array(H)).items():
                        row[f"{cam}_{teach.TIPS[k]}_x"], row[f"{cam}_{teach.TIPS[k]}_y"] = x, y
            out.append(row)
        return out

    rep = {"session": os.path.basename(base), "finger": finger, "frames": len(per),
           "phone_tracked_pct": round(100 * float(np.mean(["f" in p for p in per])), 1) if per else 0}
    # Mac time offset: the lag where both views put a HELD touch on the same wall point
    lags = {}
    if have_mac:
        for lag in range(-8, 9):
            d = [r["mac_d_n"] for r in build(lag) if r.get("mac_ok") and r["mode"] == "teach" and r["button"] and not r["ready"]]
            if len(d) > 20:
                lags[lag] = round(float(np.median(d)), 3)
    rep["mac_lag_scan"] = lags
    best = mac_lag if mac_lag is not None else (min(lags, key=lags.get) if lags else 0)
    rep["mac_lag_frames"] = best
    rows = build(best)
    teach_rows = [r for r in rows if r["mode"] == "teach"]
    if teach_rows:
        W = next(iter(calibs.values()))["W"]
        H = next(iter(calibs.values()))["H"]
        tr, _ = teach.analyse(teach_rows, W, H)
        rep["teach"] = tr
    if rows:
        with open(base + "_replay_rows.csv", "w", newline="") as f:
            keys = list(dict.fromkeys(k for r in rows for k in r))
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
    json.dump(rep, open(base + "_replay.json", "w"), indent=2)
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="logs/surface_<time> (the folder <session>_video must exist)")
    ap.add_argument("--finger", choices=["index", "lock"], default="index")
    ap.add_argument("--mac-lag", type=int, help="Mac frames offset (default: scan and pick the best)")
    ap.add_argument("--max-frames", type=int)
    a = ap.parse_args()
    base = a.session[:-6] if a.session.endswith("_video") else a.session.rstrip("/")
    rep = run(base, a.finger, a.mac_lag, a.max_frames)
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
