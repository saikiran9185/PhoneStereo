# AI depth touch test (4 Nov 2026)

This test asked whether monocular AI depth can tell a fingertip touching the projected wall from one hovering off it.
It used the phone camera at about 1.5 m.

## Results

Scored on 867 recorded frames from Experiment H at 0, 5, 10, 20 and 40 cm. Full numbers are in `results.json`.

| Model | Rank corr. | Touch vs 5 cm (AUC) | Touch vs any hover | Accuracy on unseen spots | M3 fps |
|---|---|---|---|---|---|
| Depth Anything V2 Small, relative | 0.37 | 0.55 | 0.62 | 32% | 5.7 |
| **Depth Anything V2 Small, metric indoor** | **0.68** | **0.80** | **0.88** | **72%** | 3.9 |
| CVPR 2026 fingertip-contact model (fine-tuned at 25–45 cm) | 0.12 | 0.65 | 0.67 | 48% | 4.7 |

The metric model is now used by Touch Stage (`touchstage/`). Touch Stage runs only on hand crops, with both cameras in one batch, at about 43 ms per pass.

## Files

- `depth_touch_test.py`: the offline test over all three models.
- `live_depth.py` and `live_hand_depth.py`: the first live viewers. They were superseded by Touch Stage's dashboard.

## Setup

These files are not in this repo. They live in `~/Projects/DepthTouchTest`:

- `da2/`: https://github.com/DepthAnything/Depth-Anything-V2
- `paper/`: https://github.com/muxiddin19/Multimodal-Fingertip-Contact-Detection-via-Depth-and-Motion-Fusion
- `ckpt/`: the model weights.
  - `depth_anything_v2_vits.pth` and `depth_anything_v2_metric_hypersim_vits.pth` come from the Depth-Anything Hugging Face pages.
  - `depth_anything_v2_vits_d405_finetuned.pth` comes from `muxiddin19/vrkeyb-cvpr2026`.
- `frames/` and `tips.csv`: frames and landmarks extracted from the session video. They are private camera images, so they are not committed.
