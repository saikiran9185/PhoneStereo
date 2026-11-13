"""Live AI depth on the SurfaceTouch phone camera, in real time on the Mac GPU.

SurfaceTouch (running) hands over its newest phone frame + MediaPipe fingertip; this window shows:
  left   camera view with the fingertip
  right  the AI depth map
  bottom 'finger off the wall' signal over the last 10 s + TOUCH / HOVER verdict

Keys:  1 / 2 / 3   model: general DA-V2 / metric indoor DA-V2 / CVPR 2026 paper model
       T           hold your finger ON the wall and press T: remembers the touch level
       H           hold it 5-10 cm OFF the wall and press H: remembers the hover level (threshold = halfway)
       S           input size 518 <-> 364 (smaller = faster)
       + / -       more / less smoothing (default 3 of 5)
       Q           quit
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


def main():
    os.makedirs(LIVE, exist_ok=True)
    open(os.path.join(LIVE, "want"), "w").close()
    name, size = "da2_relative", 364
    net, kind = dt.build(name)
    hist = collections.deque(maxlen=150)          # smoothed signal
    raw = collections.deque(maxlen=150)           # raw signal (thin grey line)
    win = collections.deque(maxlen=9)             # recent raw values for the median
    smooth_lv, sm = 3, None                       # 1 = light ... 5 = heavy
    is_touch, streak = False, 0
    rng = None                                    # running colour range for the depth map (no flicker)
    levels = {}
    last_mt, fps, t_prev = 0, 0.0, time.time()
    cv2.namedWindow("Live AI depth", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Live AI depth", 1280, 640)
    try:
        while True:
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            if k in NAMES and NAMES[k] != name:
                del net
                torch.mps.empty_cache() if dt.DEV == "mps" else None
                name = NAMES[k]
                net, kind = dt.build(name)
                hist.clear()
                levels.clear()
            if k in (ord("+"), ord("=")):
                smooth_lv = min(5, smooth_lv + 1)
            if k == ord("-"):
                smooth_lv = max(1, smooth_lv - 1)
            if k == ord("s"):
                size = 518 if size == 364 else 364
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
                img = cv2.imread(os.path.join(LIVE, "frame.jpg"))
            except Exception:
                continue
            if img is None:
                continue
            with torch.no_grad():
                dep = net.infer_image(img, size)
            now = time.time()
            fps = 0.8 * fps + 0.2 / max(1e-3, now - t_prev)
            t_prev = now
            lm = meta.get("lm")
            r = dt.signal(dep, lm, kind) if lm else None
            sig = None
            if r is not None:
                # 1) median of the last few frames throws away single-frame spikes
                win.append(r)
                med = float(np.median(list(win)[-(1 + 2 * smooth_lv):]))
                # 2) exponential smoothing for a calm, flowing line (heavier level = slower, steadier)
                a = {1: 0.6, 2: 0.45, 3: 0.32, 4: 0.22, 5: 0.14}[smooth_lv]
                sm = med if sm is None else sm + a * (med - sm)
                sig = sm
                raw.append(r)
                hist.append(sm)
            else:
                win.clear()
            if k == ord("t") and hist:
                levels["touch"] = float(np.median(list(hist)[-10:]))
            if k == ord("h") and hist:
                levels["hover"] = float(np.median(list(hist)[-10:]))
            # draw
            cam = img.copy()
            if lm:
                cv2.circle(cam, tuple(int(v) for v in lm[8]), 12, (0, 255, 0), 2)
            lo_d, hi_d = np.percentile(dep, 2), np.percentile(dep, 98)
            rng = (lo_d, hi_d) if rng is None else (0.9 * rng[0] + 0.1 * lo_d, 0.9 * rng[1] + 0.1 * hi_d)
            n = np.clip((dep - rng[0]) / max(rng[1] - rng[0], 1e-6) * 255, 0, 255).astype(np.uint8)
            col = cv2.applyColorMap(n if kind == "relative" else 255 - n, cv2.COLORMAP_INFERNO)
            if lm:
                cv2.circle(col, tuple(int(v) for v in lm[8]), 12, (0, 255, 0), 2)
            top = np.hstack([cv2.resize(cam, (640, 480)), cv2.resize(col, (640, 480))])
            panel = np.full((220, 1280, 3), 25, np.uint8)
            unit = "mm" if kind == "metric" else "(relative)"
            txt = f"{name}  {size}px  {fps:4.1f} fps  smooth {smooth_lv}/5   finger off wall: " + ("-" if sig is None else f"{sig:.1f} {unit}" if kind == "metric" else f"{sig:.3f} {unit}")
            cv2.putText(panel, txt, (16, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
            verdict = ""
            if "touch" in levels and "hover" in levels and sig is not None:
                # hysteresis: must cross 15% past the middle, for 2 frames in a row, to flip (no flicker)
                gap = levels["hover"] - levels["touch"]
                frac = (sig - levels["touch"]) / gap if abs(gap) > 1e-9 else 0.5      # 0 = touch level, 1 = hover level
                want = True if frac < 0.35 else False if frac > 0.65 else is_touch
                streak = streak + 1 if want != is_touch else 0
                if streak >= 2:
                    is_touch, streak = want, 0
                verdict = "TOUCH" if is_touch else "HOVER"
                # closeness bar: full = at the touch level
                bw = int(np.clip(1 - frac, 0, 1) * 300)
                cv2.rectangle(panel, (900, 60), (1200, 80), (70, 70, 70), 1)
                cv2.rectangle(panel, (900, 60), (900 + bw, 80), (60, 220, 60) if is_touch else (60, 160, 230), -1)
                cv2.putText(panel, verdict, (1040, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                            (60, 220, 60) if is_touch else (60, 160, 230), 3)
            else:
                cv2.putText(panel, "finger ON wall + T,  then 5-10 cm OFF + H  ->  live TOUCH / HOVER", (16, 66),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 160, 160), 1)
            cv2.putText(panel, f"SurfaceTouch: {meta.get('mode')} {meta.get('cue') or ''}  state {meta.get('state')}   1/2/3 model  S size  Q quit",
                        (16, 205), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (140, 140, 140), 1)
            if len(hist) > 2:
                h = np.array(hist)
                lo = float(min(h.min(), np.array(raw).min(), *levels.values()))
                hi = float(max(h.max(), np.array(raw).max(), *levels.values()))
                span = max(hi - lo, 1e-6)
                y = lambda v: int(180 - (v - lo) / span * 90)
                rv = np.array(raw)
                pr = np.array([[16 + i * 8, y(v)] for i, v in enumerate(rv)], np.int32)
                cv2.polylines(panel, [pr], False, (90, 90, 90), 1)                   # raw: thin grey
                pts = np.array([[16 + i * 8, y(v)] for i, v in enumerate(h)], np.int32)
                cv2.polylines(panel, [pts], False, (80, 200, 255), 3)               # smoothed: thick
                for lv, c in (("touch", (60, 220, 60)), ("hover", (60, 160, 230))):
                    if lv in levels:
                        cv2.line(panel, (16, y(levels[lv])), (1216, y(levels[lv])), c, 1)
            cv2.imshow("Live AI depth", np.vstack([top, panel]))
    finally:
        try:
            os.remove(os.path.join(LIVE, "want"))
        except OSError:
            pass
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
