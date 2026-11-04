"""Synthetic checks for SurfaceTouch: shadow gap, classifier, state machine.  Run: .venv/bin/python -m pytest tests"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from surfacetouch import contact, shadow, state  # noqa: E402


def scene(gap_px, exposure=1.0, content=None):
    """White wall 640x480 lit by the projector, a finger pointing up ending at (320, 200), its shadow
    shifted gap_px to the right. Returns gray, expected, white, landmarks."""
    H, W = 480, 640
    white = np.full((H, W), 200.0, np.float32)
    white *= np.linspace(0.8, 1.0, W)[None, :]                     # uneven projector brightness
    expected = np.ones((H, W), np.float32) if content is None else content
    img = white * expected * exposure
    tip, pip_ = np.array([320.0, 200.0]), np.array([320.0, 280.0])
    sh = np.zeros((H, W), np.uint8)
    # projector at an angle: shadow offset grows with distance from the wall. The tip is gap_px from its shadow;
    # the finger base is further from the wall, so its shadow is displaced more (shadow fans out sideways).
    cv2.line(sh, (int(tip[0] + gap_px), int(tip[1] + 0.3 * gap_px)), (int(pip_[0] + gap_px + 45), 470), 255, 20)
    img[sh > 0] = 25 * exposure                                     # shadow: no projector light
    fing = np.zeros((H, W), np.uint8)
    cv2.line(fing, (320, 200), (320, 470), 255, 22)
    cv2.circle(fing, (320, 200), 11, 255, -1)
    img[fing > 0] = 120 * exposure                                  # finger: lit skin, darker than the wall
    lm = np.zeros((21, 2))
    lm[0] = [320, 470]
    for i, y in zip((5, 6, 7, 8), (360, 280, 240, 200)):
        lm[i] = [320, y]
    for i, x in zip((1, 2, 3, 4, 9, 13, 17), (280, 270, 265, 262, 330, 340, 350)):
        lm[i] = [x, 400]
    for base, xs in ((9, 330), (13, 340), (17, 350)):
        for k in range(1, 4):
            lm[base + k] = [xs, 400 - 5 * k]
    img = cv2.GaussianBlur(img, (5, 5), 0) + np.random.default_rng(0).normal(0, 2, img.shape)
    return img.astype(np.float32), expected, white, lm


def test_shadow_features_follow_hover_height():
    f = {g: shadow.features(*scene(g))[0] for g in (0, 12, 36, 60)}
    assert all(v["found"] for v in f.values())
    assert f[0]["along_n"] < -0.4                      # touching: shadow tip hidden behind the finger
    assert f[0]["area_n"] < f[12]["area_n"] < f[36]["area_n"]   # more shadow visible as the finger lifts
    assert f[60]["sep_n"] > f[0]["sep_n"] + 0.2         # far hover: lit wall between finger and shadow


def test_shadow_ignores_exposure_change():
    a, _ = shadow.features(*scene(30, exposure=1.0))
    b, _ = shadow.features(*scene(30, exposure=0.6))
    assert a["found"] and b["found"]
    for k in ("gap_n", "sep_n", "area_n"):
        assert abs(a[k] - b[k]) < 0.1, k


def test_dark_projected_content_is_not_a_shadow():
    content = np.ones((480, 640), np.float32)
    content[150:250, 360:420] = 0.2               # dark box in the projected UI next to the finger
    plain, _ = shadow.features(*scene(0))
    f, _ = shadow.features(*scene(0, content=content))
    assert abs(f["area_n"] - plain["area_n"]) < 0.05, (f, plain)     # dark UI is expected, not counted as shadow


def test_classifier_learns_gap():
    rng = np.random.default_rng(1)
    rows = []
    for pos in range(4):
        for lab, g in (("TOUCH", 0.1), ("H10", 0.45), ("H20", 0.8), ("H30", 1.2), ("H50", 1.8)):
            for _ in range(40):
                rows.append({"label": lab, "pos": pos, "valid": 1, "found": 1, "gap_n": g + rng.normal(0, 0.08),
                             "sep_n": max(0, g - 0.3) + abs(rng.normal(0, 0.03)), "along_n": -0.7 + g,
                             "near_n": g * 0.7, "area_n": 0.5, "dx_n": g, "dy_n": 0.1, "parallax_v": rng.normal(),
                             "parallax_ok": 1})
    rep = contact.evaluate(rows)
    assert rep["cv_auc"] > 0.95
    assert rep["per_condition"]["TOUCH"]["called_touch_pct"] > 90
    assert rep["per_condition"]["H20"]["called_touch_pct"] < 5
    assert rep["single_feature_auc"]["gap_n"] > rep["single_feature_auc"]["parallax_v"]


def test_state_machine_debounce_and_cancel():
    sm = state.TouchStateMachine()
    ev = []
    t = 0.0
    for p in [0.1, 0.95, 0.2, 0.1]:                 # one-frame spike: no touch
        ev += sm.update(t, True, p, (0.5, 0.5))
        t += 0.033
    assert not any(e["event"] == "TOUCH_DOWN" for e in ev)
    for _ in range(6):                               # held contact -> down
        ev += sm.update(t, True, 0.95, (0.5, 0.5))
        t += 0.033
    assert any(e["event"] == "TOUCH_DOWN" for e in ev)
    for _ in range(8):                               # hand lost -> cancel, never stuck
        ev += sm.update(t, False)
        t += 0.033
    assert ev[-1]["event"] == "CANCEL" or any(e["event"] == "CANCEL" for e in ev)
    assert sm.state == state.IDLE


def test_click_vs_drag():
    sm = state.TouchStateMachine()
    t, ev = 0.0, []
    for i in range(12):
        ev += sm.update(t, True, 0.95, (0.5 + 0.01 * i, 0.5))
        t += 0.033
    for _ in range(6):
        ev += sm.update(t, True, 0.1, (0.62, 0.5))
        t += 0.033
    names = [e["event"] for e in ev]
    assert "TOUCH_UP" in names and "CLICK" not in names and sm.state == state.HOVER


def test_mac_two_view_split():
    """Finger on the wall: both views map the tip to the same wall point. Hover: they split apart."""
    import numpy as np
    from surfacetouch import maccam
    lm = np.zeros((21, 2), np.float32)
    lm[6], lm[8] = (100, 140), (100, 100)          # PIP and tip, 40 px finger
    I = np.eye(3)
    on, _ = maccam.features(lm, I, [lm.copy()], I, 1000)
    hov = lm.copy(); hov[:, 0] += 30
    off, _ = maccam.features(lm, I, [hov], I, 1000)
    assert on["mac_ok"] == 1 and on["mac_d_n"] < 0.05
    assert off["mac_d_n"] > 0.7
    from surfacetouch import contact
    assert contact.heuristic({**on, "found": 0}) > 0.9 > 0.1 > contact.heuristic({**off, "found": 0})
    none, _ = maccam.features(lm, I, None, I, 1000)
    assert none["mac_ok"] == 0


def test_teach_labels_and_tracking():
    """Button = label; frames near a button change are dropped; tracking finds which tip lands on the dot."""
    import numpy as np
    from surfacetouch import teach
    rng = np.random.default_rng(0)
    rows, t = [], 0.0
    W, H = 1000, 600
    for spot in range(4):
        tx, ty = 0.2 + 0.2 * spot, 0.5
        for cue, touching in (("HOLD", 1), ("NEAR", 0), ("TAP", None), ("WAVE", 0)):
            for k in range(60):
                t += 0.05
                b = touching if touching is not None else int((k // 10) % 2 == 0)
                d = 0.05 + 0.03 * rng.random() if b else 0.6 + 0.4 * rng.random()
                # the tracker puts the THUMB on the dot (confusion), index 30 px away
                r = {"t": t, "spot": spot, "cue": cue, "ready": int(k < 5), "tx": tx, "ty": ty, "button": b,
                     "tracked": 1, "mac_ok": 1, "mac_d_n": d, "found": 0, "parallax_ok": 0,
                     "sep_n": 3, "gap_n": 3, "parallax_v": 0, "area_n": 0}
                for cam in ("phone", "mac"):
                    r[f"{cam}_thumb_x"], r[f"{cam}_thumb_y"] = tx * W, ty * H
                    r[f"{cam}_index_x"], r[f"{cam}_index_y"] = tx * W + 30, ty * H
                    r[f"{cam}_middle_x"], r[f"{cam}_middle_y"] = tx * W + 50, ty * H
                rows.append(r)
    rep, good = teach.analyse(rows, W, H)
    assert len(good) < len(rows)                                  # ready + reaction-time frames dropped
    assert rep["single_feature_auc"]["mac_d_n"]["auc"] > 0.99
    assert rep["cv_auc"] > 0.95
    assert rep["tracking"]["phone"]["closest_to_dot_pct"]["thumb"] == 100.0
    assert rep["button_held_pct_by_cue"]["HOLD"] == 100.0 and rep["button_held_pct_by_cue"]["NEAR"] == 0.0


def test_recorder_writes_videos_and_csv(tmp_path):
    import csv
    import numpy as np
    import cv2
    from surfacetouch import record
    r = record.Recorder(str(tmp_path / "v"), log=lambda *a: None)
    r.start()
    for i in range(10):
        img = np.full((120, 160, 3), i * 20, np.uint8)
        r.add({"main": img, "mac": None if i < 3 else img}, {"t": i * 0.05, "mode": "teach"})
    r.stop()
    rows = list(csv.DictReader(open(tmp_path / "v" / "frames.csv")))
    assert len(rows) == 10 and rows[0]["mode"] == "teach"
    cap = cv2.VideoCapture(str(tmp_path / "v" / "main.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 10
    cap = cv2.VideoCapture(str(tmp_path / "v" / "mac.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 10           # late camera padded, still aligned
