"""Can AI depth from one flat phone image tell a fingertip TOUCHING the wall from one hovering 5-40 cm off it?

Data: SurfaceTouch Experiment H (4 Nov 2026, 13:18): fingertip held still at 0 / 5 / 10 / 20 / 40 cm, 5 spots,
phone main camera (zoomed 1.89x, 960x720). Fingertip from MediaPipe (tips.csv, made with the PhoneStereo venv).

Models (all ViT-Small, run on the Mac GPU via Metal/MPS):
  A  da2_relative   Depth Anything V2 Small, general (relative inverse depth)        - test 1 "the AI"
  B  da2_metric     Depth Anything V2 Small, metric indoor (Hypersim, metres)         - test 1 "the AI"
  C  paper_d405     CVPR 2026 fingertip-contact model: DA-V2 Small fine-tuned on RealSense D405 hand/surface
                    depth at 25-45 cm (max_depth 0.5 m)                               - test 2 "the paper"

Per frame: finger-off-wall signal = depth of the wall around the fingertip minus depth AT the fingertip
(metric models, mm) or the relative disparity difference (relative model). Scores:
  rank_corr   does the signal grow with true distance (1 = perfect order, 0 = none)
  auc_touch   touching (0 cm) vs any hover (5-40 cm): 1.0 perfect, 0.5 coin flip
  auc_0_5     touching vs 5 cm - the hard, useful one
  cv_acc      touch / no-touch accuracy with a threshold learned on the OTHER spots (leave-one-spot-out)
"""
import csv
import json
import os
import sys
import time

import cv2
import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "da2", "metric_depth"))
from depth_anything_v2.dpt import DepthAnythingV2 as Metric  # noqa: E402

import importlib  # noqa: E402

DEV = "mps" if torch.backends.mps.is_available() else "cpu"
CFG = {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]}


def load_state(path):
    sd = torch.load(path, map_location="cpu", weights_only=True)
    for k in ("model", "state_dict"):
        if isinstance(sd, dict) and k in sd and isinstance(sd[k], dict):
            sd = sd[k]
    return {k.replace("module.", ""): v for k, v in sd.items()}


def build(name):
    if name == "da2_relative":
        # the general (relative) model lives in the base repo under the same package name: swap it in
        saved = {k: v for k, v in sys.modules.items() if k.startswith("depth_anything_v2")}
        for k in saved:
            del sys.modules[k]
        sys.path.insert(0, os.path.join(HERE, "da2"))
        m = importlib.import_module("depth_anything_v2.dpt")
        sys.path.pop(0)
        for k in [k for k in sys.modules if k.startswith("depth_anything_v2")]:
            del sys.modules[k]
        sys.modules.update(saved)
        net = m.DepthAnythingV2(**CFG)
        net.load_state_dict(load_state(os.path.join(HERE, "ckpt", "depth_anything_v2_vits.pth")))
        kind = "relative"
    elif name == "da2_metric":
        net = Metric(**CFG, max_depth=20.0)
        net.load_state_dict(load_state(os.path.join(HERE, "ckpt", "depth_anything_v2_metric_hypersim_vits.pth")))
        kind = "metric"
    else:
        net = Metric(**CFG, max_depth=0.5)
        sd = load_state(os.path.join(HERE, "ckpt", "depth_anything_v2_vits_d405_finetuned.pth"))
        missing, unexpected = net.load_state_dict(sd, strict=False)
        if missing:
            print(f"  {name}: {len(missing)} missing keys, {len(unexpected)} unexpected", flush=True)
        kind = "metric"
    return net.to(DEV).eval(), kind


def auc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if len(pos) == 0 or len(neg) == 0:
        return None
    allv = np.concatenate([pos, neg])
    r = allv.argsort().argsort().astype(float) + 1
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def spearman(x, y):
    rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
    return float(np.corrcoef(rx, ry)[0, 1])


def signal(depth, lm, kind):
    lm = np.asarray(lm, float)
    p = lm[8] + 0.3 * (lm[7] - lm[8])                  # inside the finger pad, not on its edge
    L = float(np.linalg.norm(lm[9] - lm[0])) + 1e-6
    h, w = depth.shape
    x, y = int(round(p[0])), int(round(p[1]))
    if not (3 <= x < w - 3 and 3 <= y < h - 3):
        return None
    tip = float(np.median(depth[y - 2:y + 3, x - 2:x + 3]))
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(lm.astype(np.int32)), 1)
    mask = cv2.dilate(mask, np.ones((31, 31), np.uint8))
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.hypot(xx - p[0], yy - p[1])
    ring = (rr > 0.6 * L) & (rr < 1.6 * L) & (mask == 0)
    if ring.sum() < 30:
        return None
    wall = float(np.median(depth[ring]))
    if kind == "metric":
        return 1000.0 * (wall - tip)                    # mm the fingertip sits in front of the wall
    return (tip - wall) / max(abs(wall), 1e-6)          # relative disparity: closer = larger


def score(rows, key):
    use = [r for r in rows if r.get(key) is not None]
    s = np.array([r[key] for r in use])
    d = np.array([r["dist"] for r in use])
    spots = np.array([r["spot"] for r in use])
    out = {"n": len(use), "rank_corr": round(spearman(s, d), 3),
           "median_by_cm": {int(c): round(float(np.median(s[d == c])), 3) for c in sorted(set(d))},
           "auc_touch": round(auc(s[d > 0], s[d == 0]), 3), "auc_0_5": round(auc(s[d == 5], s[d == 0]), 3),
           "auc_0_10": round(auc(s[d == 10], s[d == 0]), 3)}
    # leave-one-spot-out touch threshold
    correct = total = 0
    for sp in sorted(set(spots)):
        tr, te = spots != sp, spots == sp
        cands = np.unique(s[tr])
        best, bt = -1, 0
        for t in cands[:: max(1, len(cands) // 200)]:
            acc = np.mean((s[tr] <= t) == (d[tr] == 0)) * 0.5 + 0.5 * np.mean(((s[tr] <= t) == (d[tr] == 0))[d[tr] == 0])
            if acc > best:
                best, bt = acc, t
        correct += int(np.sum((s[te] <= bt) == (d[te] == 0)))
        total += int(te.sum())
    out["cv_acc_pct"] = round(100 * correct / max(1, total), 1)
    return out


def main():
    rows = list(csv.DictReader(open(os.path.join(HERE, "tips.csv"))))
    for r in rows:
        r["dist"], r["spot"], r["lm"] = int(r["dist"]), int(r["spot"]), json.loads(r["lm"])
    print(f"{len(rows)} frames on {DEV}", flush=True)
    rep = {"frames": len(rows), "device": DEV}
    viz = {}
    for name in ("da2_relative", "da2_metric", "paper_d405"):
        net, kind = build(name)
        t0, n = time.time(), 0
        with torch.no_grad():
            for i, r in enumerate(rows):
                img = cv2.imread(os.path.join(HERE, r["file"]))
                dep = net.infer_image(img, 518)
                if kind == "relative":
                    pass
                r[name] = signal(dep, r["lm"], kind)
                n += 1
                if r["dist"] in (0, 20) and r["spot"] == 1 and (name, r["dist"]) not in viz:
                    viz[(name, r["dist"])] = (img, dep, r["lm"])
                if i % 200 == 0:
                    print(f"  {name} {i}/{len(rows)}", flush=True)
        if DEV == "mps":
            torch.mps.synchronize()
        fps = n / (time.time() - t0)
        rep[name] = {"kind": kind, "fps_mac_gpu": round(fps, 1), **score(rows, name)}
        print(name, json.dumps(rep[name]), flush=True)
        del net
        if DEV == "mps":
            torch.mps.empty_cache()
    json.dump(rep, open(os.path.join(HERE, "results.json"), "w"), indent=2)
    # picture: per model, depth map at touch vs 20 cm, same spot
    tiles = []
    for name in ("da2_relative", "da2_metric", "paper_d405"):
        row = []
        for d in (0, 20):
            if (name, d) not in viz:
                continue
            img, dep, lm = viz[(name, d)]
            n = cv2.normalize(dep, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            col = cv2.applyColorMap(n if name == "da2_relative" else 255 - n, cv2.COLORMAP_INFERNO)
            p = np.asarray(lm[8], int)
            cv2.circle(col, tuple(p), 10, (0, 255, 0), 2)
            cv2.putText(col, f"{name}  {d} cm", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            row.append(cv2.resize(col, (480, 360)))
        if len(row) == 2:
            tiles.append(np.hstack(row))
    if tiles:
        cv2.imwrite(os.path.join(HERE, "depth_maps.jpg"), np.vstack(tiles))


if __name__ == "__main__":
    main()
