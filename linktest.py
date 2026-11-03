#!/usr/bin/env python3
"""Link test: how well frames get from the phone app to the Mac (Wi-Fi or USB).

Connects for N seconds and reports per-camera fps, how many main/ultra-wide pairs share a timestamp,
frame lag (phone capture clock is not the Mac's, so lag = time between frames arriving vs being
sent is approximated by arrival jitter), throughput and gaps. Prints JSON and appends it to
logs/linktest.jsonl so Wi-Fi and USB runs can be compared.
  .venv/bin/python linktest.py --host 172.16.163.67 --token 0328          (Wi-Fi)
  .venv/bin/python linktest.py --host 127.0.0.1 --token 0328              (USB, after adb forward)
"""
import argparse
import datetime
import json
import os
import time

import numpy as np

import viewer as V

ap = argparse.ArgumentParser()
ap.add_argument("--host", required=True)
ap.add_argument("--token")
ap.add_argument("--seconds", type=float, default=15)
a = ap.parse_args()

log = lambda m, show=False: print(m, flush=True)
rx = V.Receiver(log, host=a.host, token=a.token)
rx.start()
rx.header_ready.wait(15)
if rx.error or not rx.header:
    raise SystemExit(f"no stream: {rx.error}")
arr = {0: [], 1: []}
ts = {0: [], 1: []}
nbytes = 0
seen = {0: 0, 1: 0}
t0 = time.time()
while time.time() - t0 < a.seconds:
    with rx.lock:
        for cam in (0, 1):
            for f in rx.frames[cam]:
                if f["ts"] > seen[cam]:
                    seen[cam] = f["ts"]
                    arr[cam].append(f["t_recv"])
                    ts[cam].append(f["ts"])
        nb = sum(b for _, b in rx.bytes)
    time.sleep(0.01)
el = time.time() - t0
mb = rx.mbps()
rx.running = False
out = {"time": datetime.datetime.now().isoformat(timespec="seconds"),
       "link": "usb" if a.host.startswith("127.") else "wifi", "seconds": round(el, 1)}
for cam, name in ((0, "main"), (1, "ultrawide")):
    t = np.array(arr[cam])
    d = np.diff(t) * 1000 if len(t) > 2 else np.array([0])
    out[name] = {"fps": round(len(t) / el, 1), "gap_ms_median": round(float(np.median(d)), 1),
                 "gap_ms_p95": round(float(np.percentile(d, 95)), 1), "longest_stall_ms": round(float(d.max()), 0)}
    # Sensor clock vs arrival clock: spread of (arrival - capture) = how much the network delays vary.
    if len(t) > 5:
        off = t - np.array(ts[cam]) / 1e9
        off -= off.min()
        out[name]["delay_jitter_ms_p95"] = round(float(np.percentile(off, 95) * 1000), 1)
pairs = len(set(ts[0]) & set(ts[1]))
out["synced_pairs_per_s"] = round(pairs / el, 1)
out["MB_per_s"] = round(mb, 2)
print(json.dumps(out, indent=2))
os.makedirs(V.LOG_DIR, exist_ok=True)
with open(os.path.join(V.LOG_DIR, "linktest.jsonl"), "a") as f:
    f.write(json.dumps(out) + "\n")
