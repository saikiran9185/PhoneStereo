"""Contact probability from finger features, learned from labelled touch / hover samples.

Features: shadow gap (tip to shadow tip), nearest shadow pixel, shadow found, shadow area, gap direction,
stereo parallax score (supporting, often weak with a 1 cm lens baseline). A small logistic regression on
standardised features keeps it inspectable: the weights say which signal the model relies on.
Until a model is trained, a shadow-gap rule is used and a missing shadow returns 0.5 (uncertain), never a touch.
"""
import json

import numpy as np

FEATURES = ["sep_n", "gap_n", "along_n", "near_n", "found", "area_n", "dx_n", "dy_n", "parallax_v", "parallax_y", "parallax_ok",
            "mac_d_n", "mac_dx_n", "mac_dy_n", "mac_ok"]


def vec(f):
    return np.array([float(f.get(k, 0.0) or 0.0) for k in FEATURES])


def heuristic(f):
    if f.get("mac_ok"):                     # two wide viewpoints agree on the wall point = contact
        return float(1 / (1 + np.exp(-(0.45 - f["mac_d_n"]) / 0.1)))
    if not f.get("found"):
        return 0.5
    return float(1 / (1 + np.exp(-(0.12 - f["sep_n"]) / 0.04)))      # shadow attached to the finger = contact


class ContactModel:
    def __init__(self, w=None, b=0.0, mu=None, sd=None, info=None, thr=0.5):
        self.w, self.b, self.mu, self.sd, self.info = w, b, mu, sd, info or {}
        self.thr = thr              # touch-down threshold learned from the data (state machine p_down)

    @property
    def trained(self):
        return self.w is not None

    def fit(self, X, y, l2=0.05, steps=3000, lr=0.3):
        X, y = np.asarray(X, float), np.asarray(y, float)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - self.mu) / self.sd
        w, b = np.zeros(Z.shape[1]), 0.0
        pos = max(1, y.sum())
        cw = np.where(y > 0, len(y) / (2 * pos), len(y) / (2 * max(1, len(y) - pos)))   # balance classes
        for _ in range(steps):
            p = 1 / (1 + np.exp(-(Z @ w + b)))
            g = (p - y) * cw
            w -= lr * (Z.T @ g / len(y) + l2 * w)
            b -= lr * g.mean()
        self.w, self.b = w, b
        # threshold: highest recall while keeping hover false alarms <= 2% on the training data
        p = np.array([self.prob_z(z) for z in Z])
        best = (0.5, -1)
        for t in np.linspace(0.3, 0.95, 66):
            fa = float(np.mean(p[y == 0] >= t)) if (y == 0).any() else 0
            rec = float(np.mean(p[y == 1] >= t)) if (y == 1).any() else 0
            if fa <= 0.02 and rec > best[1]:
                best = (float(t), rec)
        self.thr = best[0] if best[1] >= 0 else 0.9
        return self

    def prob_z(self, z):
        return float(1 / (1 + np.exp(-(z @ self.w + self.b))))

    def prob(self, f):
        if not self.trained:
            return heuristic(f)
        return self.prob_z((vec(f) - self.mu) / self.sd)

    def to_json(self):
        return {"features": FEATURES, "w": None if self.w is None else self.w.tolist(), "b": self.b,
                "mu": None if self.mu is None else self.mu.tolist(), "sd": None if self.sd is None else self.sd.tolist(),
                "info": self.info, "thr": self.thr}

    @classmethod
    def from_json(cls, d):
        if not d or d.get("w") is None or d.get("features") != FEATURES:
            return cls()                    # untrained, or trained on another feature set
        return cls(np.array(d["w"]), d["b"], np.array(d["mu"]), np.array(d["sd"]), d.get("info"), d.get("thr", 0.5))


def auc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return None
    return float(np.mean(pos[:, None] > neg[None, :]) + 0.5 * np.mean(pos[:, None] == neg[None, :]))


def evaluate(rows):
    """rows: dicts with features + 'label' (TOUCH / H10 / H20 / H30 / H50) + 'pos' (wall position id).
    Leave-one-position-out: train on the other positions, test on this one. -> report dict."""
    rows = [r for r in rows if r.get("valid")]
    positions = sorted({r["pos"] for r in rows})
    pred = []
    for p in positions:
        tr = [r for r in rows if r["pos"] != p]
        te = [r for r in rows if r["pos"] == p]
        if len({r["label"] == "TOUCH" for r in tr}) < 2 or not te:
            continue
        m = ContactModel().fit([vec(r) for r in tr], [r["label"] == "TOUCH" for r in tr])
        pred += [(r["label"], m.prob(r) - m.thr + 0.5) for r in te]   # centred: > 0.5 means touch
    rep = {"positions": len(positions), "samples": len(rows), "per_condition": {}}
    for lab in ("TOUCH", "H10", "H20", "H30", "H50"):
        ps = [p for l, p in pred if l == lab]
        if ps:
            rep["per_condition"][lab] = {"n": len(ps), "called_touch_pct": round(100 * float(np.mean(np.array(ps) > 0.5)), 1),
                                         "p_median": round(float(np.median(ps)), 3)}
    t = [p for l, p in pred if l == "TOUCH"]
    hv = [p for l, p in pred if l != "TOUCH"]
    rep["cv_auc"] = None if auc(t, hv) is None else round(auc(t, hv), 3)
    if t and hv:
        tp = sum(p > 0.5 for p in t)
        fp = sum(p > 0.5 for p in hv)
        rep["precision_pct"] = round(100 * tp / max(1, tp + fp), 1)
        rep["recall_pct"] = round(100 * tp / len(t), 1)
    # which single signal separates touch from hover on its own (higher = better; 0.5 = useless)
    rep["single_feature_auc"] = {}
    for k in ("sep_n", "gap_n", "near_n", "area_n", "parallax_v"):
        tv = [-float(r.get(k, 0) or 0) for r in rows if r["label"] == "TOUCH" and (k != "parallax_v" or r.get("parallax_ok"))]
        hvv = [-float(r.get(k, 0) or 0) for r in rows if r["label"] != "TOUCH" and (k != "parallax_v" or r.get("parallax_ok"))]
        a = auc(tv, hvv)
        if a is not None:
            rep["single_feature_auc"][k] = round(max(a, 1 - a), 3)
    shadow_found = {lab: round(100 * float(np.mean([r["found"] for r in rows if r["label"] == lab])), 1)
                    for lab in ("TOUCH", "H10", "H20", "H30", "H50") if any(r["label"] == lab for r in rows)}
    rep["shadow_found_pct"] = shadow_found
    return rep


def save(path, d):
    with open(path, "w") as f:
        json.dump(d, f, indent=1)
