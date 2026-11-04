"""Experiment T (Teach): touch labels you can trust, without measuring hover heights.

Problem with Experiment A: it asks for hovers at 10 / 20 / 30 / 50 mm. Nobody can hold a finger at a measured
height in the air, so those labels are guesses, and the model learned from guesses.

What a person CAN report exactly is whether their finger is touching the wall: they feel it. So the label comes
from a button held in the other hand (any mouse button, or Shift) for exactly as long as the finger touches.
Cues on the wall only make sure every kind of movement gets covered; the button is the ground truth.
Frames within 150 ms of a button change are dropped (reaction time).

Cues per wall spot:
  HOLD   touch the dot and hold still - button held
  NEAR   hover as close as you can WITHOUT touching - no button (the hardest case: near misses)
  TAP    tap the dot 3 times - press while each tap touches
  WAVE   move around in front of the wall, 1-10 cm, no touching - no button

Because the dot is known, HOLD and TAP frames also measure tracking: which landmark (thumb, index, middle tip)
actually lands on the dot, in each camera. That is the thumb / index confusion, measured.
"""
import random

import numpy as np
import Quartz as Q

from . import contact

SPOTS = 8
CUES = [("HOLD", 3.0, "TOUCH the dot and HOLD the button while touching"),
        ("NEAR", 3.0, "hover as CLOSE as you can WITHOUT touching - no button"),
        ("TAP", 3.5, "TAP the dot 3 times - press the button with each tap"),
        ("WAVE", 3.0, "move your finger around near the wall, never touching - no button")]
READY = 1.2
TRIM = 0.15                      # s around each button change: human reaction time
TIPS = {4: "thumb", 8: "index", 12: "middle"}


def button_down():
    """Any mouse button or Shift held, read from the system state (no permissions needed, works with any focus)."""
    st = Q.kCGEventSourceStateCombinedSessionState
    if any(Q.CGEventSourceButtonState(st, b) for b in range(3)):
        return True
    return bool(Q.CGEventSourceFlagsState(st) & Q.kCGEventFlagMaskShift)


def plan():
    spots = []
    while len(spots) < SPOTS:                     # spread out, not too close to the edges
        p = (random.uniform(0.15, 0.85), random.uniform(0.18, 0.82))
        if all(np.hypot(p[0] - q[0], p[1] - q[1]) > 0.18 for q in spots):
            spots.append(p)
    out = []
    for i, s in enumerate(spots):
        for cue, dur, txt in CUES:
            out.append({"spot": i, "xy": s, "cue": cue, "dur": dur, "text": txt})
    return out


def trim(rows):
    """Drop frames close to a button change; returns rows with a trustworthy 'touch' label."""
    t = np.array([r["t"] for r in rows])
    b = np.array([r["button"] for r in rows])
    ch = t[1:][b[1:] != b[:-1]]
    keep = []
    for r in rows:
        if r["ready"] or r.get("tracked") != 1:
            continue
        if len(ch) and np.min(np.abs(ch - r["t"])) < TRIM:
            continue
        keep.append(r)
    return keep


def analyse(rows, W, H, mm_per_pt=None):
    """rows: per-frame dicts (features + button + cue + spot + target + tip positions). -> report dict."""
    good = trim(rows)
    rep = {"frames": len(rows), "frames_used": len(good),
           "touch_frames": int(sum(r["button"] for r in good)), "no_touch_frames": int(sum(1 - r["button"] for r in good))}
    # honesty check: did the button match the cue?
    cues = {}
    for c, _, _ in CUES:
        cr = [r for r in rows if r["cue"] == c and not r["ready"]]
        if cr:
            cues[c] = round(100 * float(np.mean([r["button"] for r in cr])), 1)
    rep["button_held_pct_by_cue"] = cues
    # contact signals on their own (AUC: 1 = perfect, 0.5 = useless), on trusted labels
    rep["single_feature_auc"] = {}
    for k in ("mac_d_n", "sep_n", "gap_n", "parallax_v", "area_n"):
        use = [r for r in good if (k != "mac_d_n" or r.get("mac_ok")) and (k != "parallax_v" or r.get("parallax_ok"))
               and (k not in ("sep_n", "gap_n", "area_n") or r.get("found"))]
        a = contact.auc([-float(r[k]) for r in use if r["button"]], [-float(r[k]) for r in use if not r["button"]])
        if a is not None:
            rep["single_feature_auc"][k] = {"auc": round(max(a, 1 - a), 3), "n": len(use)}
    rep["shadow_found_pct"] = round(100 * float(np.mean([r.get("found", 0) for r in good])), 1) if good else None
    rep["mac_seen_pct"] = round(100 * float(np.mean([r.get("mac_ok", 0) for r in good])), 1) if good else None
    # cross-validated model, leave one spot out
    spots = sorted({r["spot"] for r in good})
    pred = []
    for s in spots:
        tr = [r for r in good if r["spot"] != s]
        te = [r for r in good if r["spot"] == s]
        if len({r["button"] for r in tr}) < 2 or not te:
            continue
        m = contact.ContactModel().fit([contact.vec(r) for r in tr], [r["button"] for r in tr])
        pred += [(r["button"], r["cue"], m.prob(r) - m.thr + 0.5) for r in te]
    if pred:
        t = [p for b, _, p in pred if b]
        n = [p for b, _, p in pred if not b]
        a = contact.auc(t, n)
        rep["cv_auc"] = None if a is None else round(a, 3)
        if t and n:
            tp, fp = sum(p > 0.5 for p in t), sum(p > 0.5 for p in n)
            rep["precision_pct"] = round(100 * tp / max(1, tp + fp), 1)
            rep["recall_pct"] = round(100 * tp / len(t), 1)
        rep["called_touch_pct_by_cue"] = {c: round(100 * float(np.mean([p > 0.5 for b, cc, p in pred
                                                                          if cc == c and b == (c in ("HOLD", "TAP"))])), 1)
                                          for c, _, _ in CUES if any(cc == c for _, cc, _ in pred)}
    # tracking: which landmark lands on the dot while touching (HOLD frames, button down, still)
    rep["tracking"] = {}
    hold = [r for r in good if r["button"] and r["cue"] == "HOLD"]
    unit = (mm_per_pt, "mm") if mm_per_pt else (100.0 / W, "% of width")
    for cam in ("phone", "mac"):
        errs = {name: [] for name in TIPS.values()}
        closest = {name: 0 for name in TIPS.values()}
        n = 0
        for r in hold:
            tx, ty = r["tx"] * W, r["ty"] * H
            d = {}
            for i, name in TIPS.items():
                x, y = r.get(f"{cam}_{name}_x"), r.get(f"{cam}_{name}_y")
                if x is not None:
                    d[name] = float(np.hypot(x - tx, y - ty))
            if len(d) == len(TIPS):
                n += 1
                for name, v in d.items():
                    errs[name].append(v)
                closest[min(d, key=d.get)] += 1
        if n:
            rep["tracking"][cam] = {
                "frames": n, "unit": unit[1],
                "error_median": {k: round(float(np.median(v)) * unit[0], 1) for k, v in errs.items()},
                "closest_to_dot_pct": {k: round(100 * v / n, 1) for k, v in closest.items()}}
    return rep, good
