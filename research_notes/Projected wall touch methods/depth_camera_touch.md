# Depth-camera touch detection on walls and arbitrary surfaces, and how to transfer it to a phone + projector + Mac kit

Scope note: the coordinator changed the scope partway through. The target kit is now only a POCO M4 Pro (Android, no ToF or LiDAR, main and ultrawide cameras), a projector and an Apple Silicon Mac. The sections below cover (1) what the classic depth-camera papers did, with their measured numbers, (2) how those ideas carry over to depth estimated from this kit (monocular ML, phone stereo, projector-camera structured light), (3) code, and (4) a short "if buying" sensor tier.

## Q1. Classic depth-camera touch papers: methods, thresholds, accuracy, geometry

### Takeaway
Every depth-camera touch system models the empty surface per pixel. It then marks as "touch" any pixels that sit in a thin band a few mm to about 1.5 cm in front of that surface. Depth noise sets the floor, and noise grows with distance. At 0.75 m Wilson's band was 3–14 mm. At 1.5 m it widened to 6–30 mm, and pure background subtraction at 1.6 m gave over 40 mm error. DIRECT reached 4.8 mm error and a 99.3% detection rate at 1.6 m because it added the IR image to find the fingertip edge. Hover and touch separate cleanly only above about 1–2 cm.

### Cited Findings
**Wilson, "Using a Depth Camera as a Touch Sensor", ITS 2010 (Microsoft Research)**
- Sensor and geometry: Kinect v1 (PrimeSense structured light), looking straight down at a table from 0.75 m and from 1.5 m. That covers a 1.05 m and a 2.1 m diagonal at 640×480, 30 Hz. The paper notes depth resolution of about 1 cm at 2 m. — [Wilson ITS 2010 PDF](https://microsoft.com/en-us/research/wp-content/uploads/2016/10/WilsonITS2010.pdf)
- Surface model: a per-pixel histogram of depth over "a few hundred frames" of the empty scene. The far threshold dmax is the first depth bin where the histogram exceeds a small count. The near threshold is dmin = dmax − Δ, where Δ is chosen to match typical finger thickness. Δ was 4 raw shift units at 0.75 m and 7 at 1.5 m. Classified pixels then get a 9×9 low-pass filter, a threshold and connected components. — [Wilson ITS 2010](https://microsoft.com/en-us/research/wp-content/uploads/2016/10/WilsonITS2010.pdf)
- Measured band, using stacked Post-it pads: at 0.75 m, dmax/dmin sat 3 mm and 14 mm above the surface. At 1.5 m they sat 6 mm and 30 mm above it. The paper notes "halving of precision when doubling camera distance", as expected from triangulation. — [Wilson ITS 2010](https://microsoft.com/en-us/research/wp-content/uploads/2016/10/WilsonITS2010.pdf)
- Position accuracy (informal): worst-case error was about 3 px (7 mm) at 0.75 m and about 6 px (15 mm) at 1.5 m. — [Wilson ITS 2010](https://microsoft.com/en-us/research/wp-content/uploads/2016/10/WilsonITS2010.pdf)
- Wilson's stated limitation: "it is impossible to say whether an observed object is in contact from the depth image alone". This is because of line of sight and hand posture, for example a finger pointing along the camera ray. The paper also showed that recalibrating works on non-flat objects such as a book. — [Wilson ITS 2010](https://microsoft.com/en-us/research/wp-content/uploads/2016/10/WilsonITS2010.pdf)

**OmniTouch, Harrison, Benko & Wilson, UIST 2011**
- Sensor: a custom short-range PrimeSense camera, 320×240 at 30 FPS, minimum range 20 cm, Z error about 5 mm. Worn on the shoulder, so the view is oblique, not top-down. Users stood about 30 cm from the wall in the wall condition. — [OmniTouch PDF](https://www.chrisharrison.net/projects/omnitouch/omnitouch.pdf)
- Method: finds fingers as "slices" 5–25 mm tall in the depth map, with no background model. Click detection uses a flood fill from the fingertip with a 13 mm depth tolerance. If the fill passes a pixel count (for example 2000 px), the finger counts as clicked. — [OmniTouch PDF](https://www.chrisharrison.net/projects/omnitouch/omnitouch.pdf)
- Hover threshold: "clicked" at ≤1 cm above the surface, reliably "hovering" above 2 cm, ambiguous between 1 and 2 cm. — [OmniTouch PDF](https://www.chrisharrison.net/projects/omnitouch/omnitouch.pdf)
- Results: across 6048 click trials, 96.5% got exactly one click. 0.8% were missed and 2.5% were double clicks. On the wall, touches landed 10.0 mm low on average, which the authors corrected with a +10 mm shift. With offsets corrected, a 95% hit rate on the wall needed a 16.2 mm button, against 15.0 mm on a real touchscreen. The hand needed 22.5 mm. — [OmniTouch PDF](https://www.chrisharrison.net/projects/omnitouch/omnitouch.pdf)
- Later reporting: a 6.3 mm (SD 3.9) mean tracing error at 40 cm on a notepad, as quoted in DIRECT. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)

**WorldKit, Xiao, Harrison & Hudson, CHI 2013**
- Geometry: a Kinect (v1) mounted on top of a Mitsubishi EX320U-ST short-throw projector, so the camera and projector frustums nearly coincide. Users see their own shadow when they occlude the camera. Depth range 50–500 cm with about 0.5% relative error. Runs at about 30 FPS on a 2 GHz i7 MacBook Pro. — [WorldKit PDF](https://www.chrisharrison.net/projects/worldkit/worldkit.pdf)
- Surface model: the mean of 50 consecutive frames at startup, with per-pixel standard deviation as a noise profile. A pixel is significant at more than 3σ, and candidate contact pixels are 3–50 mm in front of the background. Connected components follow. The model is static, with no dynamic update. — [WorldKit PDF](https://www.chrisharrison.net/projects/worldkit/worldkit.pdf)
- No formal touch-accuracy numbers appear in the paper text I read. HaloTouch also cites WorldKit only for its static background model and gives no metrics. — [HaloTouch arXiv](https://ar5iv.labs.arxiv.org/html/2503.01197)

**DIRECT, Xiao, Hudson & Harrison, ISS 2016 (depth + IR fusion)**
- Sensor and geometry: Kinect for Windows v2 (ToF), ceiling-mounted 1.60 m above a table and looking down. The projector is 2.35 m up and throws 1.0×2.0 m. Each depth pixel is 4.4 mm square at the surface. Runs at 30 FPS on one core. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Background model: a rolling 5 s per-pixel mean and standard deviation, inspired by WorldKit, updated at 15 fps in a separate thread. Pixels with high variance are "stunned" (frozen) so that hands do not get absorbed into the background. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Pipeline: Canny edges on the IR image (hysteresis 4000/8000). Arm pixels are at least 5 cm above the surface, a margin chosen because noise reaches about 1.5 cm at distance. Then a flood fill down to hand pixels at least 12 mm above the surface (12 mm is the 2.5th-percentile finger thickness). Then the fill continues into the fingertip, bounded by IR edges. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Touch versus hover: a finger is hovering if any pixel in the 5×5 neighbourhood of the tip is more than 1 cm from the background. Otherwise it is touching, with hysteresis on the state. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Latency: 15 ms average from depth input to touch output, with no temporal smoothing needed. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Results (12 users, 768 crosshair trials per table side): DIRECT had 4.8 mm mean error and a 99.3% detection rate. Slice finding (OmniTouch-style) had 11.1 mm and 83.9%. The background-subtraction methods (single-frame at 7–15 mm, max-distance/Wilson, statistical/WorldKit) had over 40 mm error and 52.1–84.8% detection. With offsets corrected these became 4.46 mm, 9.9 mm and 12.3–12.7 mm. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Single-finger count was correct in 95.8% of trials for DIRECT, against 52.8%, 66.3% and 35.1% for the background methods and 75.0% for slice finding. Lowering the max-distance threshold by just 1 mm caused "hundreds of errant touches … every second". Tracing error was 2.9 mm (SD 2.7) at 160 cm. The paper quotes Wilson as reporting about 1.5 cm error at 1.5 m. — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf)
- Summary claim: 15% better detection and 55% lower positional error than the next-best technique. — [figlab DIRECT page](https://www.figlab.com/research/2016/direct)

**Ubi Interactive (commercial, 2012–2013)**
- Kinect for Windows + projector + Windows 8 software that tells touch from hover by fingertip-to-surface distance. Supports up to 20 touch points. Cost US$149 for a 45" area and US$1,499 for the enterprise tier (100", 20 points). I found no published accuracy figures. — [Tom's Hardware](https://www.tomshardware.com/news/kinect-ubi-touch-screen-windows-8-projector,23887.html); [TechRadar](https://www.techradar.com/news/computing/pc/kinect-strays-from-xbox-nest-brings-touchscreen-to-any-surface-1173520)

**Later work (2017–2026)**
- HaloTouch (2025): Azure Kinect ToF about 0.5 m above a table, 640×576 at 30 FPS. Uses IR multi-path interference ("halo"). Results: 99.2% F1 (98.8% precision, 99.7% recall) over 3,840 touches, a 4.97 mm touch-point threshold, and 5.5 mm position error after removing a 7 mm constant offset. Hover-distance error is 1.53–2.81 mm; the paper's own text and table disagree. Latency is about 150 ms motion-to-graphics. — [HaloTouch arXiv](https://ar5iv.labs.arxiv.org/html/2503.01197)
- EclipseTouch (UIST 2025): an IR camera on a headset plus IR LEDs casting shadows, with a small network. Results: 98.0% touch accuracy, 6.9 mm hover-distance MAE below 10 cm and 2.5 mm below 1 cm, 80 FPS camera, 0.47 ms inference on an M2. Its comparison table lists OmniTouch at 96.5%, MRTouch (2018, depth + IR) at 96.5%, ShadowTouch (RGB + white LED) at 99.1%, EgoTouch (RGB) at 94.9% and TapLight at 95.3%. — [EclipseTouch arXiv](https://arxiv.org/html/2509.03430v1)
- ShadowTouch is listed with a touch-point threshold under 2 mm. TapLight has 50.4 ms latency. FarOut detects touch at up to 3 m. — [HaloTouch arXiv](https://ar5iv.labs.arxiv.org/html/2503.01197)

### Inferences
- Classic depth thresholding needs depth noise of a few mm at the working distance. A 1 cm hover/touch boundary (DIRECT, OmniTouch) means the depth source must resolve about 5 mm. If it does not, you must add a second cue, such as IR edges, shadows or a learned classifier.
- For a wall, the geometry changes: the camera looks across or at the wall at an angle instead of straight down. OmniTouch's wall result (10 mm vertical bias, 16 mm button for 95% hits) is the most relevant published wall data point.
- WorldKit's "camera on top of the projector" layout is the simplest to calibrate, but it gives the smallest triangulation baseline for structured light (see Q2).

### Gaps
- Wilson's position figures are informal "worst case" observations, not a user study.
- WorldKit reports no quantitative touch accuracy.
- I did not read the MRTouch or Ubi technical details in full. Ubi accuracy is unpublished.

## Q2. Transferring these ideas to depth estimated from a phone (POCO M4 Pro) + projector + Mac

### Takeaway
The choice of depth source decides whether 1 cm at 1–2 m is reachable. Projector-camera structured light can reach sub-millimetre to millimetre depth precision with the hardware already in hand, because the projector-to-phone baseline can be tens of cm. That makes it the only realistic path to a Wilson/WorldKit-style background model at 1–2 m. Phone main+ultrawide stereo with a ~1 cm baseline cannot get near 1 cm at 1.5 m (rough estimate: several cm to 10+ cm). Monocular ML depth gives about 5% relative error (~7–8 cm at 1.5 m), even though a fine-tuned close-range model has hit 3.8 mm at 25–45 cm. So monocular depth works as a coarse prior plus a learned contact classifier, not as a touch sensor on its own.

### Cited Findings
**Monocular depth ML**
- Depth Anything V2, fine-tuned for metric depth on NYU (indoor): AbsRel 0.056, δ1 0.984 (ViT-L). The base model predicts affine-invariant relative inverse depth, not metric depth. — [Depth Anything V2 paper](https://arxiv.org/pdf/2406.09414)
- The ViT-S variant on the Luxonis model zoo reports NYU-D AbsRel 0.053, δ1 0.973. — [Luxonis model card](https://models.luxonis.com/luxonis/depth-anything-v2/c5bf9763-d29d-4b10-8642-fbd032236383)
- Depth Pro (Apple, 2024) is a zero-shot metric model. On Booster it scores AbsRel 0.336 against 0.315 for DA-V2 in the same table. I could not confirm a Depth Pro NYU figure. — [Depth Pro paper](https://arxiv.org/pdf/2410.02073)
- Closest direct precedent: "Real-Time Multimodal Fingertip Contact Detection via Depth and Motion Fusion" (CVPR 2026). It fine-tunes DA-V2 ViT-S (max_depth 0.5 m) on 53,300 RGB-depth pairs. Ground truth came from a RealSense D405 at 25–45 cm, filmed at 30°, 45°, 60° and 90°. Depth MAE dropped from 12.3 mm to 3.8 mm. Contact uses a velocity-gated hysteresis state machine (enter at 4.5 mm, exit at 6.0 mm, 450 ms cooldown). Results: 94.2% accuracy and 94.4% F1 at 30 FPS. Code is on GitHub and the dataset is on Hugging Face. — [project page](https://muxiddin19.github.io/Multimodal-Fingertip-Contact-Detection-via-Depth-and-Motion-Fusion); [CVPR 2026 poster](https://cvpr.thecvf.com/virtual/2026/poster/38953)

**Projector-camera structured light (projector + phone camera = active stereo)**
- Dai & Chung, "Making Any Planar Surface into a Touch-Sensitive Display by a Mere Projector and Camera" (CVPRW 2012), extended in IEEE Trans. Cybernetics 44(8), 2014, as "Touchscreen Everywhere". They embed a random binary structured-light pattern imperceptibly in the normal projected content. From the camera image plus the known projection, they decide finger presence, touch and fingertip contact point, using a projector-camera-surface calibration and a homography. The numeric results were not visible in the abstract. — [CVPRW 2012 (mlanthology)](https://mlanthology.org/cvprw/2012/dai2012cvprw-making); [Trans. Cybernetics (AMiner)](https://www.aminer.org/pub/5550431145ce0a409eb45cf9/touchscreen-everywhere-on-transferring-a-normal-planar-surface-to-a-touch-sensitive)
- Patents describe inserting time-varying structured-light frames among regular projected frames so humans cannot see them. — [US 9524059](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9524059); [US 9092090](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9092090)
- Open-source procam Gray-code calibration and reconstruction:
  - [sjnarmstrong/gray-code-structured-light](https://gh.r-mj.com/sjnarmstrong/gray-code-structured-light) implements Moreno & Taubin, "Simple, Accurate, and Robust Projector-Camera Calibration".
  - [guillaume-charron/3DScanner-GrayCode](https://www.github.com/guillaume-charron/3DScanner-GrayCode) (ProjCamCalibrator).
  - [YCAMInterlab/ProCamToolkit](https://github.com/YCAMInterlab/ProCamToolkit) (openFrameworks, Gray-code procam and multi-projector calibration).
  - [BingyaoHuang/single-shot-pro-cam-calib](https://www.github.com/BingyaoHuang/single-shot-pro-cam-calib) (one shot per pose, IEEE TASE 2020).
  - [arkbriar/slstudio](https://www.github.com/arkbriar/slstudio) (real-time structured-light framework).
  - [jianjieluo/GrayCodePatternSeq](https://www.github.com/jianjieluo/GrayCodePatternSeq) (MATLAB pattern generator).

  Source: [search results summary, GitHub topic](https://GitHub.com/topics/structured-light)

**Phone stereo**
- No external source specific to the POCO. The user's own earlier tests (PhoneStereo project notes, not a web source) found that logical camera 4 streams main+ultrawide together at about 18 fps with a ~1 cm baseline, via adb only.

### Inferences
- Depth resolution of triangulation is roughly Δz ≈ z²·Δd / (f·b), where f is the focal length in px, b the baseline and Δd the disparity precision. The numbers below are my own arithmetic, not cited.
  - Phone stereo: b ≈ 0.01 m, f ≈ 500–1500 px (depends on stream resolution), Δd ≈ 0.25 px, z = 1.5 m gives Δz ≈ 4–11 cm. That is far from 1 cm. Phone stereo is fine for hand segmentation and coarse distance only.
  - Projector + phone camera: b ≈ 0.3–0.5 m (phone placed to the side of the projector), f ≈ 1500 px, Δd ≈ 0.1–0.25 px (Gray code + phase shift decodes sub-pixel), z = 1.5 m gives Δz ≈ 0.5–2 mm. That is well under 1 cm on paper. Real limits will be calibration error, defocus, wall texture/albedo, rolling shutter and camera–projector sync.
  - Monocular ML: about 5% AbsRel means roughly ±7.5 cm at 1.5 m in absolute terms. Relative depth between a fingertip and the wall right next to it may be much better than absolute error. Even so, nothing I found shows 1 cm fingertip-to-wall separation at 1–2 m. The CVPR 2026 result is at 25–45 cm, with fine-tuning on that exact geometry.
- Recommended transfer of the paper ideas to this kit:
  1. **Calibrate once** with Gray code + phase shift from projector to phone. This gives a dense per-pixel wall depth (or plane fit) and the projector↔camera mapping. It plays the role of Wilson's histogram background, or WorldKit's 50-frame mean plus σ.
  2. **At runtime**, sensing has to be imperceptible or interleaved. One option is short Gray-code bursts or high-frequency fringes inserted in blank or alternating frames (Dai & Chung style); this is limited by projector refresh and phone fps. Another is to drop dense depth at runtime and use a DIRECT-like second cue instead. For a front-projected wall, the finger's shadow plus the known projected content (the disparity between the finger and its shadow in the camera view) is the natural analogue of DIRECT's IR edges.
  3. **Use thresholds from the literature as starting points**: touch under 1 cm, hover above 2 cm (OmniTouch, DIRECT), a hysteresis band (CVPR 2026: 4.5/6.0 mm), and a 5×5 neighbourhood check around the tip (DIRECT).
  4. **Use monocular depth (DA-V2 small, which can run CoreML on the Mac)** only as a prior for hand and arm segmentation (DIRECT's ≥5 cm "arm" stage) and for robustness. Do not use it as the touch decision.
- Mounting: WorldKit's camera-on-projector layout minimises occlusion confusion but kills the structured-light baseline. A phone offset 30–50 cm to the side, looking obliquely at the wall, trades some occlusion for depth precision. It also makes shadow-based touch cues stronger.

### Gaps
- I found no published numbers for Dai & Chung's touch accuracy or height resolution; the full IEEE paper is needed.
- I found no paper measuring monocular foundation-model depth for fingertip-vs-wall at 1–2 m.
- POCO focal length in px per stream and the achievable projector–phone sync were not researched. They need measuring.
- I did not verify whether the OpenCV `structured_light` contrib module (GrayCodePattern, SinusoidalPattern) works well in Python on macOS. I recall it exists but did not fetch a source.

## Q3. Open-source code for depth-camera touch

### Takeaway
DIRECT's code is public: github.com/nneonneo/direct-handtracking (C++, Kinect v2). The CVPR 2026 monocular contact detector has public code and data. The rest is small hobby repos. I found no official WorldKit code.

### Cited Findings
- DIRECT reference implementation: https://github.com/nneonneo/direct-handtracking (stated in the paper). — [DIRECT PDF](https://robertxiao.ca/pubs/2016_ISS_DIRECT.pdf); [relatedrepos listing](https://relatedrepos.com/gh/nneonneo/direct-handtracking)
- Monocular fingertip contact (DA-V2 + motion fusion): code on GitHub (muxiddin19/Multimodal-Fingertip-Contact-Detection-via-Depth-and-Motion-Fusion), dataset and models on Hugging Face. — [project page](https://muxiddin19.github.io/Multimodal-Fingertip-Contact-Detection-via-Depth-and-Motion-Fusion)
- Desktopography: Python, RealSense D435i + MediaPipe index-finger landmark. Infers touch from the depth change at the fingertip pixel on a projected flat surface. — [Desktopography mirror](https://github-three-lyart.vercel.app/aastha235/desktopography)
- C++ Kinect touch detector: background-depth threshold mapped to TUIO cursors. — [gist](https://gist.github.com/as8190255/f72346a4e2290de7a59a3d83ef0545a4)
- Kinect projector tabletop touch app. — [akhilperincherry/KinectTableTopTouchApplication-Hyperlink](https://github.com/akhilperincherry/KinectTableTopTouchApplication-Hyperlink)
- A Google Research learning-based touch detector for camera-projector tabletops (2023). — [arXiv 2304.04687](https://arxiv.org/pdf/2304.04687)
- Procam structured-light libraries are listed in Q2.

### Inferences
- DIRECT's pipeline (rolling mean/σ background, arm→hand→tip flood fill, 5×5 1 cm hover test) can be ported to Python/NumPy. You would swap the Kinect v2 depth+IR for structured-light wall depth plus RGB edges or shadow.

### Gaps
- I found no public WorldKit code. I did not check licences or maintenance status of the hobby repos.

## Q4. If buying a depth sensor: brief tier (status, macOS, INR)

### Takeaway
If the phone-only route fails, the cheapest Mac-friendly sensors are an OAK-D Lite (about ₹34k, DepthAI runs on Apple Silicon) or an Orbbec Gemini 2 / Astra 2 (Orbbec SDK v2 supports macOS on M-series). RealSense on macOS is unofficial. Femto Bolt (the Azure Kinect successor) is flagged "unsupport macOS". An iPhone or iPad with LiDAR streaming to the Mac via Record3D is a cheap option if one can be borrowed, but its 256×192 depth is coarse.

### Cited Findings
- RealSense spun out of Intel as RealSense Inc. on 11 Jul 2025 with a $50M Series A. — [Tom's Hardware](https://www.tomshardware.com/tech-industry/realsense-completes-spin-out-from-intel-gets-usd50-million-in-funding-from-intel-capital-and-mediatek)
- macOS support: a RealSense forum reply says macOS development stopped at macOS 10.13.2 and Apple Silicon updates are unlikely. Community wrappers exist, such as realsense-applesilicon on PyPI (needs Homebrew librealsense). — [RealSense support forum](https://support.realsenseai.com/hc/en-us/community/posts/39737455977875/comments/39739467974291); [PyPI](https://pypi.org/project/realsense-applesilicon/0.1.0/)
- D435 Z-accuracy is ≤2% up to 2 m over 80% of the FOV, so ±3 cm at 1.5 m. That is too coarse for pure thresholding at wall distance. The D405 is rated sub-mm at 7–50 cm only. — [Intel support](https://intel.com/content/www/us/en/support/articles/000030916/emerging-technologies/intel-realsense-technology.html); [RealSense community](https://support.realsenseai.com/hc/en-us/community/posts/7338190580371/comments/7365673119763)
- INR prices for D435/D435i:
  - D435i ₹52,499 on [Amazon.in](https://www.amazon.in/Intel-RealSense-Depth-Camera-D435i/dp/B07MWR2YJB).
  - D435 ₹36,995–38,999 on [IndiaMART](https://www.indiamart.com/proddetail/d435-intel-realsense-depth-camera-21759485055.html) and ₹33,499 (out of stock) at [Tanna Tech](https://tannatechbiz.com/depth-camera-d435.html).
  - D435i ₹39,499 and D435 ₹37,199 (sold out) at [MGSL](https://mgsl.in/collections/intel-realsense).
- OAK-D Lite: ₹33,899 at [MGSL](https://mgsl.in/collections/intel-realsense) (as reported in a search summary). Launch price was US$149 (US$79 in the Kickstarter campaign). — [Hackster](https://www.hackster.io/news/opencv-and-luxonis-launch-smaller-lighter-cheaper-oak-d-lite-all-in-one-computer-vision-gadget-fb5da778dfbd)
- DepthAI on M1: there are community reports of native wheels and building from source. The evidence is mostly 2021–22. — [Luxonis forum](https://discuss.luxonis.com/d/69-running-depthai-on-apple-m1-based-macs?page=3); [Luxonis forum 2](https://discuss.luxonis.com/d/1738/3)
- Orbbec SDK v2 has been tested on M2 / macOS 13.2. The macOS list includes Gemini 2, Gemini 2 L, Astra 2 and Femto Mega. Femto Bolt shows "unsupport macOS" in the v2.8.7 notes. — [OrbbecSDK_v2 v2.8.7](https://github.com/orbbec/OrbbecSDK_v2/releases/tag/v2.8.7); [Orbbec forum](https://3dclub.orbbec3d.com/t/mac-os-support-orbbec-sdk-1-9-3-and-later/3986)
- Gemini 2 is ₹48,999.99 (regular ₹59,999.99, marked sold out) at [ThinkRobotics](https://thinkrobotics.com/collections/orbbec-official-page).
- Femto Bolt is marketed as the Azure Kinect DK successor with the same ToF module: 1024×1024 at 30 fps, 0.5–3.8 m. Prices run from about US$418 to US$609. — [Hackster](https://www.hackster.io/news/orbbec-partners-with-microsoft-on-new-indirect-time-of-flight-depth-sensing-camera-models-f1f5576e361d); [Switch Science](https://www.switch-science.com/products/9996)
- iPhone/iPad LiDAR to Mac: the Record3D app has USB streaming plus a pip `record3d` library that supports macOS. ARKit sceneDepth is 256×192. Per-pixel accuracy depends on ARConfidenceLevel. — [record3d GitHub](https://www.github.com/marek-simonik/record3d); [Record3DStream](https://github.com/YoshiRi/Record3DStream); [Apple dev forum](https://developer.apple.com/forums/thread/650321)

### Inferences
- At 1.5 m, 256×192 over a roughly 2 m wide wall is about 8 mm per pixel. That is coarser than DIRECT's 4.4 mm Kinect v2 pixel, so LiDAR-only touch would likely land in the background-subtraction class (≥1–4 cm error) rather than DIRECT class.
- A used Kinect v2 (DIRECT's exact sensor) would also need the libfreenect2 Mac route plus a USB 3 adapter. Its status is unverified.

### Gaps
- I found no INR prices for Kinect v2 (used, OLX), Astra 2 or Femto Bolt in India. The ThinkRobotics Femto Bolt page returned HTTP 429.
- libfreenect2 on Apple Silicon was not verified.
- I found no published mm-level accuracy for ARKit sceneDepth at 1–2 m. The ISIF iPad Pro 2021 uncertainty paper was not read.
