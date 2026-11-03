#!/usr/bin/env python3
"""Sign Cue Test - research test of hand-sign accuracy and speed for three camera setups.

The screen cues one of 6 hand signs (FIST, OPEN PALM, PEACE, POINT, THUMBS UP, HORNS) at random times;
you make it as fast as you can. The app knows exactly when each cue appeared and what the right answer
is, so every setup is scored against the same ground truth, on the very same hand movements:

  mac      Mac camera alone (single camera)
  phone2   phone main + ultra-wide only (finger probabilities averaged; the lenses are 1 cm apart)
  all3     all three: each finger read from the view (Mac or phone) with the best learned trust
  (phone_main / phone_wide alone are scored too, as extra detail)

Per setup:
  recognised %       cues where the setup showed the right sign (2 frames running) within 2 s
  latency_ms         cue -> right sign ready on the Mac (includes your reaction, same for every setup)
  added_delay_ms     latency minus the fastest setup on the same cue (your reaction cancels out)
  settled_acc %      after the hand settled (cue + 1.0 s), share of frames showing the right sign
  per-sign scores and a confusion table (cued sign -> what the setup said)
All times are Mac-clock "result ready" times, so no phone clock assumption is used. No smoothing is
applied, so every setup is compared raw.

Research data (logs/, kept out of git):
  speed_<time>_frames.csv   every camera frame: time, camera, hand found, 5 finger probabilities,
                            sign, 21 landmarks, hand size, brightness, current cue
  speed_<time>_cues.csv     each cue: sign, onset, per-setup latency
  speed_<time>_summary.json all scores + session info; also appended to logs/research_sessions.jsonl
  report.py turns every session into the full research report.
Keys: SPACE start · Q quit.
"""
import argparse
import collections
import csv
import datetime
import json
import os
import random
import threading
import time

import cv2
import numpy as np

import signs as S
import viewer as V

S.NUM_HANDS = 2

WIN = "Sign Cue Test"
CAMS = ["mac", "phone_main", "phone_wide"]
SETUPS = ["mac", "phone2", "all3", "phone_main", "phone_wide"]
VERSION = 3
HINT = {"FIST": "all fingers closed", "OPEN PALM": "all fingers open", "PEACE": "index + middle up",
        "POINT": "index up only", "THUMBS UP": "thumb up, fist", "HORNS": "index + pinky up"}


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = []             # (t_ready, cam, det, brightness, t_capture)
        self.view = {}

    def add(self, cam, t_cap, det, img):
        b = float(cv2.mean(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))[0])
        with self.lock:
            self.rows.append((time.time(), cam, det, b, t_cap))
            self.view[cam] = (img, det)


class MacTimed(threading.Thread):
    def __init__(self, cap, rec):
        super().__init__(daemon=True)
        self.cap, self.rec, self.running = cap, rec, True

    def run(self):
        tr = S.make_tracker()
        last = 0
        while self.running:
            ok, f = self.cap.read()
            t = time.time()
            if not ok:
                time.sleep(0.01)
                continue
            small = S.resize_w(f, S.PW)
            last = max(last + 1, int(t * 1000))
            self.rec.add("mac", t, S.detect(tr, small, last), small)
        self.cap.release()


class PhoneTimed(threading.Thread):
    def __init__(self, log, host, token, rec):
        super().__init__(daemon=True)
        self.rx = V.Receiver(log, host=host, token=token)
        self.rec, self.running, self.error = rec, True, None
        self.camera = "back"

    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        self.rx.start()
        self.rx.header_ready.wait(15)
        if self.rx.error or not self.rx.header:
            self.error = self.rx.error or "no stream"
            return
        trs = [S.make_tracker(), S.make_tracker()]
        pool = ThreadPoolExecutor(2)
        lastts, lastms = [-1, -1], [0, 0]
        h = self.rx.header
        self.camera = "front" if str(h.get("logicalId")) == "1" else "back"
        # the front sensor is mounted the other way round (270 vs 90 degrees): turn its picture upright
        orient = h["cameras"][0].get("sensorOrientation", 90)
        turn = ((orient - 90) // 90) % 4

        def work(cam, f):
            img = S.resize_w(f["img"], S.PW)
            if turn:
                img = np.rot90(img, -turn).copy()
            lastms[cam] = max(lastms[cam] + 1, f["ts"] // 1_000_000)
            self.rec.add(CAMS[1 + cam], f["t_recv"], S.detect(trs[cam], img, lastms[cam]), img)

        while self.running:
            todo = []
            with self.rx.lock:
                for cam in (0, 1):
                    if self.rx.frames[cam] and self.rx.frames[cam][-1]["ts"] > lastts[cam]:
                        todo.append((cam, self.rx.frames[cam][-1]))
                        lastts[cam] = self.rx.frames[cam][-1]["ts"]
            if not todo:
                time.sleep(0.002)
                continue
            for j in [pool.submit(work, c, f) for c, f in todo]:
                j.result()


def setup_series(rows, trust, t0, t1, step=0.02):
    """Sign of every setup on a 20 ms grid, from each camera's latest frame that is < 150 ms old."""
    per = {c: [(r[0], r[2]["probs"] if r[2] else None) for r in rows if r[1] == c] for c in CAMS}
    idx = {c: 0 for c in CAMS}
    out = {s: [] for s in SETUPS}
    for t in np.arange(t0, t1, step):
        cur = {}
        for c, ss in per.items():
            i = idx[c]
            while i + 1 < len(ss) and ss[i + 1][0] <= t:
                i += 1
            idx[c] = i
            if ss and ss[i][0] <= t and t - ss[i][0] < 0.15:
                cur[c] = ss[i][1]
            else:
                cur[c] = None
        ph = [cur[c] for c in ("phone_main", "phone_wide") if cur[c] is not None]
        phone = np.mean(ph, 0) if ph else None
        views = {k: v for k, v in (("mac", cur["mac"]), ("phone", phone)) if v is not None}
        if views:
            f = np.zeros(5)
            for i in range(5):
                best = max(views, key=lambda v: (trust or {}).get(v, [0.5] * 5)[i])
                f[i] = views[best][i]
            all3 = f
        else:
            all3 = None
        sig = lambda p: None if p is None else S.sign_from(p)[0]
        vals = {"mac": sig(cur["mac"]), "phone2": sig(phone), "all3": sig(all3),
                "phone_main": sig(cur["phone_main"]), "phone_wide": sig(cur["phone_wide"])}
        for s in SETUPS:
            out[s].append((float(t), vals[s]))
    return out


def score(series, cues, t_end):
    res, per_cue = {}, {}
    for su, ss in series.items():
        if not any(v for _, v in ss):
            continue
        t = np.array([s[0] for s in ss])
        v = [s[1] for s in ss]
        lat, settled, right = [], 0, 0
        by_sign = collections.defaultdict(lambda: {"cues": 0, "recognised": 0, "frames": 0, "right": 0, "lat": []})
        confusion = collections.defaultdict(collections.Counter)
        no_hand = 0
        for ci, (on, lab) in enumerate(cues):
            end = cues[ci + 1][0] if ci + 1 < len(cues) else t_end
            idx = np.where((t >= on) & (t < end))[0]
            hit = None
            for a, b in zip(idx, idx[1:]):
                if v[a] == lab and v[b] == lab:
                    hit = t[a] - on
                    break
            ok = hit is not None and hit <= 2.0
            bs = by_sign[lab]
            bs["cues"] += 1
            bs["recognised"] += ok
            per_cue.setdefault(ci, {})[su] = round(1000 * hit) if ok else None
            if ok:
                lat.append(hit)
                bs["lat"].append(hit)
            for k in idx:
                if t[k] >= on + 1.0:
                    settled += 1
                    bs["frames"] += 1
                    good = v[k] == lab
                    right += good
                    bs["right"] += good
                    confusion[lab][v[k] or "no hand"] += 1
                    no_hand += v[k] is None
        n = len(cues)
        res[su] = {"recognised_pct": round(100 * sum(b["recognised"] for b in by_sign.values()) / n, 1),
                   "latency_ms_median": round(1000 * float(np.median(lat))) if lat else None,
                   "latency_ms_p90": round(1000 * float(np.percentile(lat, 90))) if lat else None,
                   "settled_acc_pct": round(100 * right / max(1, settled), 1),
                   "settled_no_hand_pct": round(100 * no_hand / max(1, settled), 1),
                   "per_sign": {s: {"recognised_pct": round(100 * b["recognised"] / max(1, b["cues"]), 1),
                                    "settled_acc_pct": round(100 * b["right"] / max(1, b["frames"]), 1),
                                    "latency_ms_median": round(1000 * float(np.median(b["lat"]))) if b["lat"] else None,
                                    "cues": b["cues"]} for s, b in by_sign.items()},
                   "confusion": {k: dict(c) for k, c in confusion.items()}}
    sus = list(res)
    main3 = [s for s in ("mac", "phone2", "all3") if s in res]
    added = {s: [] for s in sus}
    for ci, d in per_cue.items():
        if all(d.get(s) is not None for s in main3):
            best = min(d[s] for s in main3)
            for s in sus:
                if d.get(s) is not None:
                    added[s].append(d[s] - best)
    for s in sus:
        res[s]["added_delay_ms_median"] = round(float(np.median(added[s]))) if added[s] else None
    return res, per_cue


def camera_stats(rows, t0, t1):
    out = {}
    for c in CAMS:
        rr = [r for r in rows if r[1] == c and t0 <= r[0] <= t1]
        if not rr:
            continue
        proc = [r[0] - r[4] for r in rr]
        out[c] = {"fps": round(len(rr) / max(1e-3, t1 - t0), 1),
                  "hand_found_pct": round(100 * np.mean([r[2] is not None for r in rr]), 1),
                  "arrival_to_result_ms_median": round(1000 * float(np.median(proc))),
                  "brightness_mean": round(float(np.mean([r[3] for r in rr])), 1)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host")
    ap.add_argument("--token")
    ap.add_argument("--mac-cam", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=5, help="cues per sign (6 signs)")
    ap.add_argument("--note", default="", help="lighting, distance, person ... saved with the session")
    ap.add_argument("--seed", type=int, help="fixed cue order (repeatable sessions)")
    ap.add_argument("--linger", type=float, default=45)
    a = ap.parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(V.LOG_DIR, f"speed_{stamp}")
    os.makedirs(V.LOG_DIR, exist_ok=True)
    log = V.EventLog(base + ".log")
    rec = Recorder()

    cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
    for _ in range(20):
        if cap.isOpened():
            break
        time.sleep(0.5)
        cap = cv2.VideoCapture(a.mac_cam, cv2.CAP_AVFOUNDATION)
    mac = None
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        mac = MacTimed(cap, rec)
        mac.start()
    phone = PhoneTimed(log, a.host, a.token, rec) if a.host else None
    if phone:
        phone.start()
    link = "none" if not a.host else ("usb" if a.host.startswith("127.") else "wifi")

    rnd = random.Random(a.seed)
    seq = []
    for _ in range(a.rounds):
        block = S.SIGNS[:]
        rnd.shuffle(block)
        if seq and block[0] == seq[-1]:
            block[0], block[-1] = block[-1], block[0]
        seq += block
    gaps = [rnd.uniform(2.2, 3.2) for _ in seq]
    cues = []
    put = lambda img, t, x, y, sc=0.7, c=(235, 235, 235), th=2: cv2.putText(
        img, t, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, sc, c, th, cv2.LINE_AA)
    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    state, t_intro, t_run, t_done, results = "intro", time.time(), None, None, None
    try:
        while True:
            now = time.time()
            img = np.full((660, 1080, 3), (32, 16, 11), np.uint8)
            with rec.lock:
                views = [(c, *rec.view[c]) for c in CAMS if c in rec.view]
            for i, (lab, im, det) in enumerate(views):
                x = 30 + i * 345
                img[360:600, x:x + 320] = cv2.flip(cv2.resize(im, (320, 240)), 1)
                cv2.rectangle(img, (x, 360), (x + 320, 600), (60, 220, 60) if det else (60, 60, 230), 2)
                if lab == "phone_main" and phone and phone.camera == "front":
                    lab = "phone FRONT"
                put(img, f"{lab}: {S.sign_from(det['probs'])[0] if det else 'no hand'}", x, 352, 0.5, (200, 200, 200), 1)
            if state == "intro":
                left = 8 - (now - t_intro)
                put(img, f"SIGN CUE TEST: {len(seq)} cues, 6 signs. Make the sign shown as fast as you can.",
                    30, 60, 0.7, (0, 220, 255))
                put(img, "one hand inside all 3 views, other hand down.  SPACE = start"
                         f"  (auto in {max(0, left):.0f}s)", 30, 100, 0.6)
                put(img, "signs: " + " | ".join(f"{k}: {v}" for k, v in HINT.items()), 30, 140, 0.42, (180, 180, 180), 1)
                if left <= 0:
                    state, t_run = "run", now + 1.0
            elif state == "run":
                k = len(cues)
                if k < len(seq) and now >= t_run + sum(gaps[:k]):
                    cues.append((now, seq[k]))
                    log(f"cue {k} {seq[k]}")
                if cues:
                    lab = cues[-1][1]
                    cv2.rectangle(img, (0, 120), (1080, 320), (70, 50, 20), -1)
                    (tw, _), _ = cv2.getTextSize(lab, cv2.FONT_HERSHEY_SIMPLEX, 2.4, 7)
                    put(img, lab, (1080 - tw) / 2, 230, 2.4, (0, 220, 255), 7)
                    put(img, HINT[lab], 400, 290, 0.8, (220, 220, 220), 2)
                put(img, f"cue {len(cues)}/{len(seq)}", 30, 60, 0.8)
                if len(cues) == len(seq) and now > cues[-1][0] + 3.0:
                    t_end = now
                    with rec.lock:
                        rows = list(rec.rows)
                    trust = S.load_trust()
                    series = setup_series(rows, trust, cues[0][0], t_end)
                    per, per_cue = score(series, cues, t_end)
                    results = {"version": VERSION, "time": stamp, "link": link, "note": a.note, "seed": a.seed,
                               "cues": len(seq), "phone_camera": phone.camera if phone else None,
                               "trust_used": {k: trust.get(k) for k in ("mac", "phone")} if trust else None,
                               "cameras": camera_stats(rows, cues[0][0], t_end), "per_setup": per}
                    with open(base + "_summary.json", "w") as f:
                        json.dump(results, f, indent=2)
                    with open(os.path.join(V.LOG_DIR, "research_sessions.jsonl"), "a") as f:
                        f.write(json.dumps({"kind": "signcue", **results}) + "\n")
                    with open(base + "_cues.csv", "w", newline="") as f:
                        wr = csv.writer(f)
                        wr.writerow(["cue", "sign", "onset_s"] + [f"{s}_latency_ms" for s in SETUPS])
                        for ci, (on, lab) in enumerate(cues):
                            d = per_cue.get(ci, {})
                            wr.writerow([ci, lab, round(on - cues[0][0], 3)] +
                                        ["" if d.get(s) is None else d[s] for s in SETUPS])
                    with open(base + "_frames.csv", "w", newline="") as f:
                        wr = csv.writer(f)
                        wr.writerow(["t_ready_s", "t_capture_s", "camera", "hand", "sign", "p_thumb", "p_index",
                                     "p_middle", "p_ring", "p_pinky", "hand_size", "brightness", "cue"] +
                                    [f"lm{i}{ax}" for i in range(21) for ax in "xy"])
                        t0 = cues[0][0]
                        for t, cam, det, b, tc in rows:
                            cur = next((lab for on, lab in reversed(cues) if on <= t), "")
                            wr.writerow([round(t - t0, 4), round(tc - t0, 4), cam, int(det is not None),
                                         S.sign_from(det["probs"])[0] if det else "",
                                         *([round(p, 3) for p in det["probs"]] if det else [""] * 5),
                                         round(det["size"], 4) if det else "", round(b, 1), cur,
                                         *([round(v, 4) for p in det["lms"] for v in p] if det else [""] * 42)])
                    log("SUMMARY " + json.dumps({k: {m: v[m] for m in ("recognised_pct", "latency_ms_median",
                                                 "settled_acc_pct")} for k, v in per.items()}))
                    state, t_done = "done", now
            else:
                put(img, f"DONE ({link}) - saved.  Q to quit", 30, 50, 0.75, (80, 220, 80))
                put(img, "setup        recognised  latency  +delay  settled acc", 30, 100, 0.55, (0, 220, 255), 1)
                y = 100
                for name, v in results["per_setup"].items():
                    y += 30
                    lt, ad = v["latency_ms_median"], v["added_delay_ms_median"]
                    put(img, f"{name:11s}  {v['recognised_pct']:5.0f}%   {'-' if lt is None else lt:>5} ms"
                             f"  {'-' if ad is None else ad:>4} ms   {v['settled_acc_pct']:5.1f}%",
                        30, y, 0.55, (220, 220, 220), 1)
                if now - t_done > a.linger:
                    break
            cv2.imshow(WIN, img)
            key = cv2.waitKey(10) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" ") and state == "intro":
                state, t_run = "run", time.time() + 1.0
    finally:
        if mac:
            mac.running = False
        if phone:
            phone.running = False
            phone.rx.running = False
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
