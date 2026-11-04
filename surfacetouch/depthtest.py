"""Experiment H (height): can the cameras tell how far the hand is from the wall, and does tracking hold?

Fingertip held in front of a dot at 0 / 5 / 10 / 20 / 40 cm from the wall (big enough steps to set with a ruler or
tape; even rough distances keep the right ORDER, which is what the rank score tests). Each distance is held still.

Depth signals compared (each against the distance):
  split_pct     phone vs Mac: wall points of the same fingertip. Wide baseline (two separate viewpoints).
  parallax_v    phone main vs ultra-wide shift at the fingertip. 1 cm baseline.
  palm_phone    hand size in the phone (px): the hand gets nearer the phone as it leaves the wall.
  palm_mac      hand size in the Mac camera (px).
Per signal: tracked %, rank correlation with distance (1 = perfect order), whether each step is distinguishable
from the next (AUC), and a cross-validated distance estimate (cm error, trained on other dots).
"""
import random

import numpy as np

from . import contact

DIST = [0, 5, 10, 20, 40]
HINT = {0: "TOUCH the dot", 5: "5 cm from the wall (3 finger widths)", 10: "10 cm (a fist)",
        20: "20 cm (a hand span)", 40: "40 cm (about a forearm)"}
SPOTS = 5
DUR, READY = 3.0, 1.6
STILL_SPEED = 0.03
SIGNALS = ["split_pct", "parallax_v", "palm_phone", "palm_mac"]


def plan():
    spots = []
    while len(spots) < SPOTS:
        p = (random.uniform(0.18, 0.82), random.uniform(0.2, 0.8))
        if all(np.hypot(p[0] - q[0], p[1] - q[1]) > 0.2 for q in spots):
            spots.append(p)
    out = []
    for i, s in enumerate(spots):
        for d in DIST:
            out.append({"spot": i, "xy": s, "cue": f"D{d}", "dur": DUR, "ready_s": READY,
                        "text": f"fingertip in front of the dot, {HINT[d]} - hold still"})
    return out


def add_signals(r, W):
    """split_pct from the logged wall points of both cameras' index tips."""
    if r.get("phone_index_x") not in (None, "") and r.get("mac_index_x") not in (None, ""):
        r["split_pct"] = 100 * float(np.hypot(float(r["phone_index_x"]) - float(r["mac_index_x"]),
                                              float(r["phone_index_y"]) - float(r["mac_index_y"]))) / W
    return r


def ranks(a):
    a = np.asarray(a, float)
    o = np.argsort(a)
    r = np.empty(len(a))
    r[o] = np.arange(len(a))
    for v in np.unique(a):                          # average ties
        m = a == v
        r[m] = r[m].mean()
    return r


def spearman(x, y):
    if len(x) < 5:
        return None
    rx, ry = ranks(x), ranks(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def still_rows(rows, H, W):
    by = {}
    for r in rows:
        by.setdefault((r["spot"], r["cue"]), []).append(r)
    keep = []
    for rs in by.values():
        rs.sort(key=lambda r: r["t"])
        for i, r in enumerate(rs):
            if r.get("ready") or r.get("x") in (None, ""):
                continue
            p = rs[max(0, i - 4)]
            dt = r["t"] - p["t"]
            if dt <= 0 or p.get("x") in (None, ""):
                continue
            v = np.hypot(r["x"] - p["x"], (r["y"] - p["y"]) * H / W) / dt
            if v < STILL_SPEED:
                keep.append(r)
    return keep


def analyse(rows, W, H):
    for r in rows:
        r["dist"] = int(str(r["cue"])[1:])
        add_signals(r, W)
    total = {d: sum(1 for r in rows if r["dist"] == d and not r.get("ready")) for d in DIST}
    good = still_rows(rows, H, W)
    rep = {"frames": len(rows), "still_frames": len(good), "distances_cm": DIST}
    # hand tracking per distance and camera
    rep["tracked_pct"] = {}
    for d in DIST:
        rs = [r for r in rows if r["dist"] == d and not r.get("ready")]
        if rs:
            rep["tracked_pct"][d] = {"phone": round(100 * float(np.mean([r.get("tracked") == 1 for r in rs])), 1),
                                     "mac": round(100 * float(np.mean([r.get("mac_index_x") not in (None, "") for r in rs])), 1),
                                     "frames": total[d]}
    rep["signals"] = {}
    for k in SIGNALS:
        use = [r for r in good if r.get(k) not in (None, "") and (k != "parallax_v" or r.get("parallax_ok"))]
        if len(use) < 10:
            rep["signals"][k] = {"n": len(use), "note": "too few frames"}
            continue
        x = np.array([float(r[k]) for r in use])
        y = np.array([r["dist"] for r in use], float)
        s = {"n": len(use), "rank_corr": None if spearman(x, y) is None else round(spearman(x, y), 3),
             "median_by_cm": {d: round(float(np.median(x[y == d])), 2) for d in DIST if (y == d).any()}}
        steps = {}
        for a, b in zip(DIST, DIST[1:]):
            A = contact.auc(x[y == b], x[y == a]) if (y == a).any() and (y == b).any() else None
            if A is not None:
                steps[f"{a}->{b}"] = round(max(A, 1 - A), 3)
        s["step_auc"] = steps
        # leave-one-dot-out linear distance estimate
        errs = []
        for sp in sorted({r["spot"] for r in use}):
            tr = np.array([r["spot"] != sp for r in use])
            if tr.sum() < 5 or (~tr).sum() == 0 or np.ptp(x[tr]) == 0:
                continue
            a, b = np.polyfit(x[tr], y[tr], 1)
            errs += list(np.abs(np.clip(a * x[~tr] + b, 0, 60) - y[~tr]))
        if errs:
            s["cv_error_cm"] = {"median": round(float(np.median(errs)), 1), "p90": round(float(np.percentile(errs, 90)), 1)}
        rep["signals"][k] = s
    usable = {k: v for k, v in rep["signals"].items() if v.get("rank_corr") is not None}
    if usable:
        best = max(usable, key=lambda k: abs(usable[k]["rank_corr"]))
        rep["best_signal"] = best
    return rep, good
