#!/usr/bin/env python3
"""Offline analysis of Sign Cue Test sessions (speedtest.py v3) -> logs/research/results.json + .md

Re-scores every session from its saved frames with several ways of combining cameras, so the setups
are compared on identical hand movements:
  mac            Mac camera alone
  phone_main     phone main lens alone
  phone2         phone main + ultra-wide, finger probabilities averaged
  all3_avg       Mac + phone views, finger probabilities averaged
  all3_live      what ran live: per finger, the view with the best trust from logs/trust.json
                 (learned in earlier Sign Check games, a different phone position)
  all3_cv        per finger, the view that was more often right in the OTHER cue sessions
                 (leave-one-session-out: never learns from the session it is scored on)
  vote3          majority of the three cameras' signs (Mac breaks ties)
Statistics: mean +- sd across sessions, and a paired bootstrap 95% CI (resampling cues) for each
setup's settled accuracy minus the Mac camera's.
  .venv/bin/python research.py [--sessions speed_2026...] (default: all v3 sessions)
"""
import argparse
import collections
import csv
import glob
import json
import os

import numpy as np

import signs as S

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
OUT = os.path.join(LOG, "research")
SETUPS = ["mac", "phone_main", "phone2", "all3_avg", "all3_live", "all3_cv", "vote3"]


def load_session(base):
    summ = json.load(open(base + "_summary.json"))
    cues = [(float(r["onset_s"]), r["sign"]) for r in csv.DictReader(open(base + "_cues.csv"))]
    rows = collections.defaultdict(list)
    for r in csv.DictReader(open(base + "_frames.csv")):
        p = None if r["hand"] == "0" else np.array([float(r[k]) for k in
                                                     ("p_thumb", "p_index", "p_middle", "p_ring", "p_pinky")])
        rows[r["camera"]].append((float(r["t_ready_s"]), p))
    return {"base": os.path.basename(base), "summary": summ, "cues": cues, "rows": dict(rows)}


def grid_probs(sess, step=0.02):
    """Each camera's latest finger probabilities (< 150 ms old) on a common grid."""
    cues = sess["cues"]
    t0, t1 = cues[0][0], cues[-1][0] + 3.0
    grid = np.arange(t0, t1, step)
    out = {}
    for cam, ss in sess["rows"].items():
        ts = np.array([s[0] for s in ss])
        j = np.searchsorted(ts, grid, side="right") - 1
        vals = []
        for g, i in zip(grid, j):
            vals.append(ss[i][1] if i >= 0 and g - ts[i] < 0.15 else None)
        out[cam] = vals
    return grid, out


def finger_trust(sessions):
    """Per view, per finger: share of settled frames where p>0.5 matched the cued sign's finger."""
    hit = {"mac": np.zeros(5), "phone": np.zeros(5)}
    n = {"mac": np.zeros(5), "phone": np.zeros(5)}
    for s in sessions:
        grid, g = s["grid"]
        lab = s["label"]
        for k, t in enumerate(grid):
            cue = lab[k]
            if cue is None:
                continue
            want = S.TEMPLATES[cue]
            ph = [g[c][k] for c in ("phone_main", "phone_wide") if c in g and g[c][k] is not None]
            views = {"mac": g.get("mac", [None] * len(grid))[k], "phone": np.mean(ph, 0) if ph else None}
            for v, p in views.items():
                if p is None:
                    continue
                for i, w in enumerate(want):
                    if w is not None:
                        n[v][i] += 1
                        hit[v][i] += (p[i] > 0.5) == bool(w)
    return {v: list(np.round(hit[v] / np.maximum(n[v], 1), 3)) for v in hit}


def setup_signs(s, trust_live, trust_cv):
    grid, g = s["grid"]
    N = len(grid)
    mac = g.get("mac", [None] * N)
    pm, pw = g.get("phone_main", [None] * N), g.get("phone_wide", [None] * N)
    sig = lambda p: None if p is None else S.sign_from(p)[0]
    out = {k: [] for k in SETUPS}
    for k in range(N):
        ph = [p for p in (pm[k], pw[k]) if p is not None]
        phone = np.mean(ph, 0) if ph else None
        views = {v: p for v, p in (("mac", mac[k]), ("phone", phone)) if p is not None}

        def pick(trust):
            if not views:
                return None
            f = np.zeros(5)
            for i in range(5):
                f[i] = views[max(views, key=lambda v: trust.get(v, [0.5] * 5)[i])][i]
            return f

        out["mac"].append(sig(mac[k]))
        out["phone_main"].append(sig(pm[k]))
        out["phone2"].append(sig(phone))
        out["all3_avg"].append(sig(np.mean(list(views.values()), 0)) if views else None)
        out["all3_live"].append(sig(pick(trust_live)))
        out["all3_cv"].append(sig(pick(trust_cv)))
        votes = [sig(p) for p in (mac[k], pm[k], pw[k]) if p is not None]
        if votes:
            c = collections.Counter(votes).most_common()
            top = [v for v, m in c if m == c[0][1]]
            out["vote3"].append(sig(mac[k]) if len(top) > 1 and sig(mac[k]) in top else top[0])
        else:
            out["vote3"].append(None)
    return out


def score(grid, signs, cues, t_end):
    """-> per-cue records: (sign, latency or None, settled_right, settled_frames)."""
    recs = []
    for ci, (on, lab) in enumerate(cues):
        end = cues[ci + 1][0] if ci + 1 < len(cues) else t_end
        idx = np.where((grid >= on) & (grid < end))[0]
        hit = None
        for a, b in zip(idx, idx[1:]):
            if signs[a] == lab and signs[b] == lab:
                hit = grid[a] - on
                break
        st = [k for k in idx if grid[k] >= on + 1.0]
        recs.append({"sign": lab, "lat": hit if hit is not None and hit <= 2.0 else None,
                     "right": sum(signs[k] == lab for k in st), "frames": len(st),
                     "said": collections.Counter(signs[k] or "no hand" for k in st)})
    return recs


def summarise(recs):
    lat = [r["lat"] for r in recs if r["lat"] is not None]
    fr = sum(r["frames"] for r in recs)
    return {"recognised_pct": round(100 * len(lat) / len(recs), 1),
            "latency_ms_median": round(1000 * float(np.median(lat))) if lat else None,
            "latency_ms_p90": round(1000 * float(np.percentile(lat, 90))) if lat else None,
            "settled_acc_pct": round(100 * sum(r["right"] for r in recs) / max(1, fr), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", nargs="*")
    ap.add_argument("--phone", default="back", choices=["back", "front"],
                    help="which phone camera the sessions used (back pair or front selfie camera)")
    a = ap.parse_args()
    bases = a.sessions or sorted(p[:-len("_summary.json")] for p in glob.glob(os.path.join(LOG, "speed_*_summary.json")))
    sessions = []
    for b in bases:
        b = b if os.path.isabs(b) else os.path.join(LOG, os.path.basename(b))
        try:
            sm = json.load(open(b + "_summary.json"))
            if sm.get("version") != 3 or (sm.get("phone_camera") or "back") != a.phone:
                continue
            s = load_session(b)
        except (OSError, ValueError, KeyError):
            continue
        s["grid"] = grid_probs(s)
        grid = s["grid"][0]
        lab = []
        for t in grid:
            cur = [(on, l) for on, l in s["cues"] if on <= t]
            lab.append(cur[-1][1] if cur and t >= cur[-1][0] + 1.0 else None)    # settled frames only
        s["label"] = lab
        sessions.append(s)
    if not sessions:
        raise SystemExit("no v3 sessions")
    trust_live = sessions[0]["summary"].get("trust_used") or {}
    all_recs = {k: [] for k in SETUPS}
    per_session = []
    cv_trusts = {}
    for i, s in enumerate(sessions):
        others = [o for j, o in enumerate(sessions) if j != i] or [s]
        tcv = finger_trust(others)
        cv_trusts[s["base"]] = tcv
        signs = setup_signs(s, trust_live, tcv)
        grid = s["grid"][0]
        t_end = s["cues"][-1][0] + 3.0
        row = {"session": s["base"], "link": s["summary"]["link"], "cameras": s["summary"]["cameras"]}
        for k in SETUPS:
            recs = score(grid, signs[k], s["cues"], t_end)
            for r in recs:
                r["session"] = s["base"]
            all_recs[k] += recs
            row[k] = summarise(recs)
        per_session.append(row)

    res = {"sessions": [s["base"] for s in sessions], "n_cues": len(all_recs["mac"]),
           "trust_live": trust_live, "trust_learned_all_sessions": finger_trust(sessions),
           "trust_cv": cv_trusts, "per_session": per_session, "pooled": {}, "across_sessions": {},
           "per_sign": {}, "confusion": {}, "vs_mac_bootstrap": {}, "added_delay_vs_fastest_ms": {}}
    rng = np.random.default_rng(0)
    nc = len(all_recs["mac"])
    for k in SETUPS:
        res["pooled"][k] = summarise(all_recs[k])
        vals = {m: [ps[k][m] for ps in per_session if ps[k][m] is not None]
                for m in ("recognised_pct", "latency_ms_median", "settled_acc_pct")}
        res["across_sessions"][k] = {m: {"mean": round(float(np.mean(v)), 1), "sd": round(float(np.std(v, ddof=1)), 1)
                                         if len(v) > 1 else None} for m, v in vals.items() if v}
        bys = collections.defaultdict(list)
        conf = collections.defaultdict(collections.Counter)
        for r in all_recs[k]:
            bys[r["sign"]].append(r)
            conf[r["sign"]].update(r["said"])
        res["per_sign"][k] = {sg: summarise(rs) for sg, rs in bys.items()}
        res["confusion"][k] = {sg: dict(c) for sg, c in conf.items()}
        # paired bootstrap over cues: settled accuracy difference vs Mac
        a_r = np.array([r["right"] for r in all_recs[k]], float)
        m_r = np.array([r["right"] for r in all_recs["mac"]], float)
        fr = np.array([r["frames"] for r in all_recs[k]], float)
        diffs = []
        for _ in range(2000):
            ix = rng.integers(0, nc, nc)
            diffs.append(100 * (a_r[ix].sum() - m_r[ix].sum()) / max(1, fr[ix].sum()))
        res["vs_mac_bootstrap"][k] = {"diff_pct": round(100 * (a_r.sum() - m_r.sum()) / fr.sum(), 1),
                                      "ci95": [round(float(np.percentile(diffs, 2.5)), 1),
                                               round(float(np.percentile(diffs, 97.5)), 1)]}
    main3 = ["mac", "phone2", "all3_cv"]
    for k in SETUPS:
        d = []
        for i in range(nc):
            ls = [all_recs[s][i]["lat"] for s in main3]
            if all(x is not None for x in ls) and all_recs[k][i]["lat"] is not None:
                d.append(all_recs[k][i]["lat"] - min(ls))
        res["added_delay_vs_fastest_ms"][k] = round(1000 * float(np.median(d))) if d else None
    os.makedirs(OUT, exist_ok=True)
    res["phone_camera"] = a.phone
    with open(os.path.join(OUT, "results.json" if a.phone == "back" else f"results_{a.phone}.json"), "w") as f:
        json.dump(res, f, indent=2, default=float)
    print(f"{len(sessions)} sessions, {nc} cues")
    print(f"{'setup':11s} {'recog%':>7} {'lat ms':>7} {'p90':>6} {'+delay':>7} {'settled%':>9} {'vs mac (95% CI)':>22}")
    for k in SETUPS:
        p, b = res["pooled"][k], res["vs_mac_bootstrap"][k]
        print(f"{k:11s} {p['recognised_pct']:7} {p['latency_ms_median']:7} {p['latency_ms_p90']:6} "
              f"{str(res['added_delay_vs_fastest_ms'][k]):>7} {p['settled_acc_pct']:9} "
              f"{b['diff_pct']:+6} [{b['ci95'][0]:+},{b['ci95'][1]:+}]")
    print("per sign settled acc:")
    for k in ("mac", "phone2", "all3_cv", "vote3"):
        print(f"  {k:9s}", {sg: v["settled_acc_pct"] for sg, v in res["per_sign"][k].items()})
    print("trust learned (all sessions):", res["trust_learned_all_sessions"])


if __name__ == "__main__":
    main()
