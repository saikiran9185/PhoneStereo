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


def test_zoom_unrotate_matches_numpy_rotation():
    import numpy as np
    from surfacetouch import zoom
    raw = np.zeros((30, 40), np.uint8)
    raw[7, 25] = 255                                         # (x=25, y=7)
    for turn in range(4):
        rot = np.rot90(raw, -turn)
        y, x = np.argwhere(rot == 255)[0]
        back = zoom.unrotate([[x, y]], turn, 40, 30)[0]
        assert np.allclose(back, [25, 7]), (turn, back)


def test_zoom_crop_covers_wall_with_margin_and_limits():
    import numpy as np
    from surfacetouch import zoom
    wall = np.array([[400, 300], [560, 300], [560, 420], [400, 420]], float)     # in a 960x720 view, turn 0
    c = zoom.crop_for(wall, 0, 960, 720)
    x0, y0, x1, y1 = c
    assert x0 < 400 / 960 and x1 > 560 / 960 and y0 < 300 / 720 and y1 > 420 / 720
    assert abs((x1 - x0) - (y1 - y0)) < 1e-3                 # 4:3 in pixels = equal in normalised units
    assert zoom.zoom_factor(c) <= zoom.MAX_ZOOM + 1e-6
    assert zoom.crop_for(np.array([[5, 5], [955, 5], [955, 715], [5, 715]], float), 0, 960, 720) is None
    # a second zoom step inside an existing crop maps back to full-sensor coordinates
    c2 = zoom.crop_for(wall, 0, 960, 720, current=(0.25, 0.25, 0.75, 0.75))
    assert 0.25 <= c2[0] and c2[2] <= 0.75 + 1e-6


def test_finger_lock_ignores_brief_thumb_flicker():
    import numpy as np
    from surfacetouch.replay import FingerLock
    def hand(thumb_out):
        lm = np.zeros((21, 2), np.float32)
        lm[0], lm[9] = (0, 100), (0, 50)                     # palm 50 px
        lm[5], lm[8] = (0, 50), (0, 0)                       # index out (50 px)
        lm[2], lm[4] = (-20, 60), ((-20 - (90 if thumb_out else 10)), 60)
        lm[9], lm[12] = (0, 50), (0, 40)
        return lm
    fl = FingerLock()
    assert fl.tip(hand(False), 0.0) == 8
    assert fl.tip(hand(True), 0.1) == 8                      # thumb looks longer for one frame: keep index
    assert fl.tip(hand(False), 0.2) == 8
    for t in (1.0, 1.2, 1.4):                                # thumb really pointing for 0.4 s: switch
        k = fl.tip(hand(True), t)
    assert k == 4


def test_replay_runs_on_a_recorded_session(tmp_path):
    import json
    import numpy as np
    from surfacetouch import record, replay
    base = str(tmp_path / "surface_x")
    rec = record.Recorder(base + "_video", log=lambda *a: None)
    rec.start()
    for i in range(30):
        img = np.full((240, 320, 3), 200, np.uint8)
        rec.add({"main": img, "wide": img, "mac": img},
                {"t": i * 0.05, "mode": "teach", "cue": "HOLD", "spot": 0, "ready": 0, "tx": 0.5, "ty": 0.5,
                 "button": int(i > 10), "calib": 1})
    rec.stop()
    json.dump({"id": 1, "Hs": np.eye(3).tolist(), "Hp": None, "Hm": None, "turn": 0, "W": 320, "H": 240,
               "roi": [None, None], "mac_roi": None}, open(base + "_video/calib_1.json", "w"))
    rep = replay.run(base, log=lambda *a: None)
    assert rep["frames"] == 30 and rep["phone_tracked_pct"] == 0.0     # no hand in a blank wall: nothing invented


def test_depth_analysis_finds_a_signal_that_grows_with_distance():
    import numpy as np
    from surfacetouch import depthtest
    rng = np.random.default_rng(1)
    rows, t = [], 0.0
    W, H = 1000, 600
    for spot in range(4):
        for d in depthtest.DIST:
            for k in range(40):
                t += 0.05
                r = {"t": t, "spot": spot, "cue": f"D{d}", "ready": int(k < 5), "tracked": 1, "x": 0.5, "y": 0.5,
                     "phone_index_x": 500.0, "phone_index_y": 300.0,
                     "mac_index_x": 500.0 + 4 * d + rng.normal(0, 2), "mac_index_y": 300.0,   # split grows with d
                     "parallax_ok": 1, "parallax_v": rng.normal(0, 1),                         # pure noise
                     "palm_phone": 40 + 0.3 * d + rng.normal(0, 1)}
                rows.append(r)
    rep, good = depthtest.analyse(rows, W, H)
    assert rep["best_signal"] == "split_pct"
    assert rep["signals"]["split_pct"]["rank_corr"] > 0.9
    assert abs(rep["signals"]["parallax_v"]["rank_corr"]) < 0.3
    assert rep["signals"]["split_pct"]["cv_error_cm"]["median"] < 3
    assert rep["tracked_pct"][20]["phone"] == 100.0


def test_pinch_click_lands_where_you_pointed_and_drag_has_no_jump():
    import numpy as np
    from surfacetouch import pinch
    c = pinch.Cursor()
    tip = np.array([500.0, 300.0])
    # pointing, fingers open
    for i in range(5):
        out = c.update(i * 0.05, 0.9, tip, tip + [40, 40])
    assert np.allclose(out, tip)
    # fingers close: the index tip slides 30 px toward the thumb, cursor must stay put
    t = 0.3
    for k, r in enumerate((0.55, 0.45, 0.35, 0.25, 0.2)):
        moved = tip + [10 * k, 10 * k]
        out = c.update(t, r, moved, moved + [15, 15])
        t += 0.05
    assert c.closed and np.allclose(out, tip, atol=1)
    # drag: past the dead zone the cursor starts moving, continuing from where it was (no jump)
    out = c.update(t, 0.2, moved + [40, 0], moved + [55, 15])
    assert np.allclose(out, tip, atol=1)
    t += 0.05
    out = c.update(t, 0.2, moved + [140, 0], moved + [155, 15])
    assert np.allclose(out, tip + [100, 0], atol=1)
    # release: cursor holds still briefly (no jump on opening)
    out2 = c.update(t + 0.05, 0.6, moved + [130, -20], moved + [160, 0])
    assert not c.closed and np.allclose(out2, out)
    assert pinch.prob(0.1) > 0.95 and pinch.prob(0.6) < 0.05 and abs(pinch.prob(0.34) - 0.5) < 0.01


def test_pinch_state_machine_tap_is_click_hold_is_drag():
    from surfacetouch.state import TouchStateMachine
    from surfacetouch import pinch
    sm = TouchStateMachine(p_down=0.6, p_up=0.15, confirm_down=0.06, confirm_up=0.08)
    ev, t = [], 0.0
    seq = [0.9] * 5 + [0.2] * 4 + [0.9] * 5            # quick pinch (~0.2 s)
    for r in seq:
        ev += [e["event"] for e in sm.update(t, True, pinch.prob(r), (0.5, 0.5))]
        t += 0.05
    assert "CLICK" in ev
    sm = TouchStateMachine(p_down=0.6, p_up=0.15, confirm_down=0.06, confirm_up=0.08)
    ev, t = [], 0.0
    for i in range(40):                                  # pinch held 1.5 s while moving
        r = 0.2 if 3 <= i < 33 else 0.9
        ev += [e["event"] for e in sm.update(t, True, pinch.prob(r), (0.3 + 0.01 * i, 0.5))]
        t += 0.05
    assert "TOUCH_MOVE" in ev and "TOUCH_UP" in ev and "CLICK" not in ev


def test_side_mode_learns_the_wall_from_touches_and_sees_hover():
    """Synthetic side camera: wall points project through a known homography; a hovering fingertip is displaced."""
    import numpy as np
    import cv2
    from surfacetouch import side
    Htrue = np.array([[0.2, 0.0, 300.0], [0.05, 0.6, 100.0], [0.0001, 0.0, 1.0]])      # wall pt -> phone px
    proj = lambda w: cv2.perspectiveTransform(np.array([[w]], np.float32), Htrue)[0, 0]
    W, H = 1512, 982
    walls = [np.array([x * W, y * H]) for x, y in side.DOTS]
    Hs, err = side.fit([proj(w) for w in walls], walls)
    assert err < 0.5
    Hm = np.eye(3)                                                       # Mac image = wall coords for the test
    def phone_lm(tip):
        lm = np.zeros((21, 2), np.float32)
        lm[8], lm[6] = tip, tip + [0, 25]                                # 25 px finger
        return lm
    w = np.array([700.0, 500.0])
    mac = np.zeros((21, 2), np.float32); mac[8] = w
    touch, _, pos = side.features(phone_lm(proj(w)), [mac], Hm, Hs)
    hover, _, _ = side.features(phone_lm(proj(w) + [0, -15]), [mac], Hm, Hs)   # 15 px off the wall line
    assert touch["side_ok"] == 1 and touch["side_gap_n"] < 0.05 and np.allclose(pos, w)
    assert hover["side_gap_n"] > 0.5
    assert side.heuristic(touch) > 0.95 > 0.2 > side.heuristic(hover)
