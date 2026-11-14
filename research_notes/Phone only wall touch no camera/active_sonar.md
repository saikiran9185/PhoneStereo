# Active acoustic sonar from one smartphone: touching the wall vs hovering near it, from 2.5–4 m away

Scope: can one phone's speaker and mic, placed near a projector 2.5–4 m from a painted wall, tell a hand that is TOUCHING the wall from one hovering 1–5 cm off it, or from a person just standing near the wall?

How to read this file: "Cited Findings" are taken from sources and linked. "Inferences" are my own physics estimates and reasoning, and they are labelled as estimates. Several primary PDFs (Strata, FM-Track, the SpeakerGesture PDF on the UMass site) returned 404 or a CAPTCHA, so some figures come from abstracts, surveys or search snippets. Those are flagged.

---

## 1. Range resolution: can the hand echo and wall echo be separated when they are 1–5 cm apart at 3–4 m?

### Takeaway
No. Phones are effectively limited to about 4 kHz of usable inaudible bandwidth (18–22 kHz at 48 kHz sampling). That gives a range resolution of about 4.3 cm, and the factory phone setup has been measured to resolve two targets only when they are about 7 cm apart. A hand 1–5 cm from the wall falls inside one range bin with the wall's very strong specular echo. Getting about 1 cm resolution needed root-level changes to the OS and audio driver, 192 kHz sampling and an 18–58 kHz chirp, and that was shown at short range.

### Cited Findings
- Phones are capped at 48 kHz recording by the manufacturer, even though the codec chips support 192 kHz. Only about 4 kHz (18–22 kHz) is usable for inaudible sensing, and 22–24 kHz is avoided because the speaker response is weak there. Amplitude generally falls as frequency rises. — [PowerPhone, MobiCom 2023 (PMC full text)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/) / [ACM](https://dl.acm.org/doi/10.1145/3570361.3613270)
- Two-target resolution, measured: the factory setting resolves two targets at 100% when they are **7 cm** apart. PowerPhone gets to **1 cm** using a 40 kHz chirp (18–58 kHz) at 192 kHz. The theoretical figure is 0.43 cm, and the authors suggest clock drift explains the gap. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- PowerPhone required editing the Device Tree and codec registers over I2C, retuning the PLL clock, and rebuilding LineageOS so that AudioFlinger stops downsampling. "Apps can request arbitrary sample rates, but … this is done by interpolation in AudioFlinger, so the true hardware rate stays at 48 kHz." AAudio still returned 48 kHz until the AudioFlinger change was made. Phones that worked at 192 kHz: Motorola Edge 2020, Galaxy S9+, Galaxy S10, **Xiaomi Redmi Note 9 Pro**. Stock flagships (S23 Ultra, Pixel 7 Pro, Mi 13 Pro) did not support 192 kHz on built-in mic/speaker. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- Strata (MobiSys 2017) estimates the channel impulse response (CIR) from a pilot sequence by least squares. It then tracks the phase of the one CIR tap that carries the finger's motion. This is how it gets sub-bin displacement tracking, without true super-resolution of two reflectors. — [Strata, ACM DL](https://dl.acm.org/doi/10.1145/3081333.3081356); [ResearchGate](https://www.researchgate.net/publication/317633460_Strata_Fine-Grained_Acoustic-based_Device-Free_Tracking)
- The Android CDD requires the UNPROCESSED source to be roughly flat only within ±10 dB over 100 Hz–7 kHz. From 7 kHz to 22 kHz it may deviate by **±30 dB**. Near-ultrasound support (18.5–20 kHz: mean response within 15 dB of the 2 kHz level, SNR ≥50 dB for a 19 kHz tone at −26 dBFS) is only "strongly recommended", and a device advertises it with `PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND`. — [Android CDD 7.8 Audio](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-platform-12.1.0_r10/7_hardware-compatibility/7_8_audio.md); [CDD 5.11 Unprocessed](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-9.0.0_r9/5_multimedia/5_11_unprocessed-audio.md)

### Inferences (my estimates, using ΔR = c/(2B) with c = 343 m/s)
| Band | B | ΔR | Notes |
|---|---|---|---|
| 18–22 kHz (stock phone, inaudible) | 4 kHz | **4.3 cm** | the realistic case |
| 17–23 kHz | 6 kHz | 2.9 cm | 17–18 kHz is audible to many young students; 22–23 kHz output is weak |
| 2–8 kHz audible chirp | 6 kHz | 2.9 cm | clearly audible in class and overlaps projector-fan and speech energy |
| 1–21 kHz audible sweep | 20 kHz | 0.86 cm | loud and annoying; phone speaker response is very uneven, so the effective bandwidth is smaller |
| 18–58 kHz (PowerPhone, rooted) | 40 kHz | 0.43 cm theoretical / 1 cm measured | not available on a stock POCO |
- Even if ΔR were small enough, the **dynamic range** problem decides it. The wall is a large flat specular reflector. Using an image-source model, its echo pressure scales as about Γ/(2R), with Γ ≈ 0.9 for painted plaster. The hand is a small scatterer whose echo pressure scales as about √(σ/4π)/R². Taking a backscatter area of σ ≈ 0.005–0.01 m² for a hand at λ ≈ 1.7 cm (rough guess) and R = 3.5 m, **the hand echo comes out roughly 30–40 dB below the wall echo**. Compressed-chirp range sidelobes are typically −13 dB without windowing and −30 to −40 dB with windowing. So a hand within one bin of the wall sits under the wall echo's mainlobe or near sidelobes. Super-resolution methods (MUSIC, deconvolution) need high SNR and models of equal-order amplitude. That 30–40 dB amplitude ratio inside a 4 cm bin is far beyond the conditions in which those methods are usually shown to work. I found no paper that separates a hand from a wall 1–5 cm away at metre range.
- **CIR differencing** (subtracting the static background) removes the wall echo itself. Whatever remains in the wall's bin is the change caused by a hand entering it, plus any changes from the body. This makes it possible to detect "something changed at the wall's range". It does not tell 1 cm from 0 cm, because both land in the same bin and the difference signal is just a phasor whose amplitude and phase depend on the exact geometry.
- A painted wall that is uneven at the mm-to-cm scale is still mostly specular at λ ≈ 1.7 cm. Unevenness makes the wall echo a little longer in time, which slightly worsens separation.

### Gaps
- I could not get the POCO M4 Pro's measured speaker and mic response above 18 kHz. The phone is not confirmed to advertise `PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND`, and that must be queried on the device.
- I found no published super-resolution results (MUSIC or ESPRIT on a CIR) for separating a small target from a large one less than one bin apart on phones.

---

## 2. Phase and micro-motion tracking: state of the art, working range, and whether it can detect "hand stopped at the wall"

### Takeaway
Phase-based phone systems (LLAP, Strata, FingerIO, CAT, EchoTrack) reach mm-to-cm accuracy, but only within about 0.25–1 m. Systems that work at 3–6 m either track whole-body or repetitive motion with 8–18 cm error (CovertBand), or need smart-speaker mic arrays (SpeakerGesture, about 4 m, with gesture classification rather than tracking). The one paper on acoustic touch-vs-hover detection (Tsuruta 2016) uses a phone placed on or right next to the surface, not at a distance. No paper found detects hand contact with a surface by sonar at a distance.

### Cited Findings
- **LLAP** (Wang et al., MobiCom 2016) is phase-based and runs on stock speaker and mics at 48 kHz. Measured accuracy is **3.5 mm** for 1-D movement and **4.6 mm (4.57 mm)** for 2-D drawing, the 2-D figure needing two microphones. Latency is **under 15 ms** on Android and iOS. A survey says it is not resilient to nearby interference. — [LLAP paper PDF](https://samsonsjarkal.github.io/KeSun/files/mobicom16_ultrasound.pdf); [ACM](https://dl.acm.org/doi/10.1145/2973750.2973764); [demo](https://dl.acm.org/doi/10.1145/2973750.2987385); [Survey arXiv 1901.03450](https://arxiv.org/pdf/1901.03450)
- **Strata** (Yun et al., MobiSys 2017) tracks the phase of the selected CIR tap. A related patent cites 0.3 mm distance-tracking error, 0.99 mm 2-D error and a 12.5 ms update interval (these patent figures are not verified against the paper). — [ACM](https://dl.acm.org/doi/10.1145/3081333.3081356)
- **FingerIO** (Nandakumar et al., CHI 2016, usually miscited as NSDI) is OFDM sonar on a Galaxy S4 with **about 8 mm** average 2-D accuracy. It works at about **25 cm** according to a review. Related-work summaries say error stays within 1 cm over a 0.5 m² surface and rises to 3 cm beyond that (secondary sources). — [FingerIO PDF](https://homes.cs.washington.edu/~gshyam/Papers/fingerio.pdf); [ACM](https://dl.acm.org/doi/pdf/10.1145/2858036.2858580)
- **CAT** (MobiCom 2016) is distributed FMCW combined with an IMU. A survey table lists 5–7 mm in 2-D and 8–9 mm in 3-D. FMCW alone gives about 2 cm in 2-D, and it is device-based (the phone itself is tracked, not a bare hand). — [CAT ACM PDF](https://dl.acm.org/doi/pdf/10.1145/2973750.2973755); [survey](https://arxiv.org/pdf/1901.03450)
- **EchoTrack** (INFOCOM 2017) uses chirp ToF from the phone's speaker array, with errors **≤3 cm in 76%** and **≤2 cm in 48%** of cases, near the phone. — [EchoTrack PDF](https://cis.temple.edu/~yu/research/EchoTrack-InfoCom17.pdf)
- **BreathJunior** is FMCW (white noise up to 24 kHz plus beamforming), with a breathing-rate error of 0.4 bpm at 40 cm and 3 bpm at 60 cm, so it degrades fast with distance (survey table). — [Survey](https://arxiv.org/pdf/1901.03450)
- **CovertBand** (Nandakumar et al., UbiComp/IMWUT 2017) hides 18–20 kHz pulses under music played from a phone into a portable speaker or TV. It tracks **repetitive body movements up to 6 m** with **8–18 cm positional error**. Through thin walls the range drops to 2–3 m. To extend range the authors raised the pulse volume, which made it audible. — [UW News](https://www.washington.edu/news/2017/08/16/computer-scientists-use-music-to-covertly-track-body-movements-activity); [UW Security Lab](https://seclab.cs.washington.edu/2017/08/16/security-lab-researchers-reveal-how-smart-devices-can-be-turned-into-surveillance-devices-with-music)
- **FM-Track** (SenSys 2020) is multi-target chirp tracking up to about **3 m** (abstract and snippet). Its related work cites a 4.5 m range for human (body) tracking. — [FM-Track (link from search; returned 404 at fetch time)](https://people.cs.umass.edu/~dli/papers/SenSys20_FM-Track.pdf)
- **SpeakerGesture / Room-scale Hand Gesture Recognition Using Smart Speakers** (Li, Liu, Lee, Xiong, SenSys 2022) extends the acoustic gesture range from about 1 m to **4–5 m**. It reaches **over 90%** gesture classification accuracy at **4 m** under strong interference, from 1,440 gestures by 16 participants in 3 environments. It uses chirp ToF plus angle-of-arrival from the smart speaker's **mic array**, not a phone. The authors state that "a very limited sensing range constrains acoustic sensing to application scenarios where users interact with devices in close proximity." — [ACM](https://dl.acm.org/doi/10.1145/3560905.3568528); [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12758040)
- **PowerPhone** on a stock phone reaches 1 m at a 5 mm error tolerance. With the rooted 192 kHz configuration it reaches 4.5 m, and 6 m with the LASense software. Human-presence detection improves from **2 m to 6 m**. A 4-class finger-gesture task improves from 61% to 90.25%. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- **Touch vs hover, acoustically**: Tsuruta et al. (HCII 2016) used a smartphone speaker and mic to detect hover and touch over arbitrary solid objects, reporting that "our system could detect hover and touch accurately". The approach relies on changes in sound diffused near the object, with the phone at or on the surface. — [Tsuruta HCII 2016 PDF](https://www.iplab.cs.tsukuba.ac.jp/paper/international/tsuruta_hcii2016.pdf) (the PDF was too large to fetch, so details come from the search abstract)
- **ActiTouch** (on-skin AR/VR touch, uses RF and acoustic sensing with a wearable): a snippet reports that 75.8% of its false positives were hover classified as touch. This shows hover-vs-touch is the hard confusion even with contact-coupled sensing. The snippet was truncated. — [ActiTouch PDF](https://yangzhang.dev/research/ActiTouch/ActiTouch.pdf)

### Inferences
- **Can relative phase show the hand stopping exactly at the wall?** Phase tracking measures *displacement* at mm scale near the phone, but it accumulates drift and has no absolute anchor unless the absolute range is resolved. Telling "stopped at 0 cm" from "stopped at 2 cm" needs about 1 cm *absolute* hand range accuracy at 3.5 m. The best published metre-range absolute accuracy for a body part from a phone is 8–18 cm (CovertBand), which is one order of magnitude too coarse. **Estimate: not achievable on a stock phone.**
- One idea is to use the hand echo "merging" into the wall tap as the cue. When the hand reaches the wall, its echo enters the same CIR tap as the wall, and the differenced CIR at that tap stops changing. At 2 cm hover the hand is *also* in that tap (ΔR ≈ 4.3 cm) and also stops changing. Both cases look the same: a static residual in the wall bin. The residual's amplitude and phase depend on the exact hand pose, so they are not a reliable signature.
- A possible weak cue is the extra phase a hovering hand adds. A hover gap of d gives a round-trip phase of 4πd/λ. At 20 kHz (λ = 1.7 cm), d = 1 cm gives about 7.4 rad, more than a full cycle. Phase therefore wraps within 0.86 cm, and with no absolute reference a hover of 0.86 cm looks identical to contact. Even mm-precise phase cannot tell whether d = 0.
- A contact-specific acoustic signature probably exists only at contact: the impact sound of a palm slapping or tapping the wall (a passive, broadband transient). That is outside active sonar and belongs to the passive-acoustic section of the report.

### Gaps
- I found no paper on remote acoustic contact detection (sonar deciding whether a hand touches a surface from metres away).
- I could not verify the RTrack, VSkin, SonarSnoop, ApneaApp or "Push the Limit of Acoustic Gesture Recognition" numbers from primary sources this session. From memory (unverified): VSkin senses gestures *on the phone's own back surface*; ApneaApp and BreathJunior work at ≤1 m; SonarSnoop works at phone-in-hand range. None operates at 3 m with hand-level precision.

---

## 3. Doppler and kinematics: is "approaches, then stops abruptly at the wall's range" a reliable cue?

### Takeaway
Doppler can detect that a hand-sized target moves toward the wall and then stops. With about 4 cm range bins and cm-to-dm absolute error at 3–4 m, it cannot confirm that the stop happened *at* the wall rather than 1–5 cm short. People hovering also stop abruptly, so the cue does not separate touch from hover. It can separate "reached toward the wall region" from "standing still nearby".

### Cited Findings
- SpeakerGesture classifies hand gestures at 4 m (>90%) using chirp ToF and AoA on a smart-speaker mic array. This shows that gross hand-motion *patterns* are detectable at room scale with better hardware than a phone. — [ACM](https://dl.acm.org/doi/10.1145/3560905.3568528)
- CovertBand detects repetitive whole-body movements (arm-pumping, walking) at up to 6 m, with 8–18 cm positional error. — [UW News](https://www.washington.edu/news/2017/08/16/computer-scientists-use-music-to-covertly-track-body-movements-activity)
- A patent on ultrasonic proximity and motion detection (Elliptic-style) describes measuring chirp echo delay for distance and recognising gestures from frequency-shift patterns. — [US 9733720](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9733720)

### Inferences (estimates)
- Doppler shift: f_d = 2vf/c. A hand moving at 0.5 m/s at 20 kHz gives f_d ≈ 58 Hz, which is easily resolved with roughly 50–100 ms frames (10–20 Hz bins). Detecting motion is feasible. Telling *where* the motion stops is limited by the range resolution above.
- Kinematic signature: a real touch decelerates over the last few cm and then stays still. A hover also decelerates and stays still. The only difference is the 1–5 cm offset, which is below ΔR. Using deceleration rate as a feature is speculative, and people's touches vary widely.
- Torso and arm clutter: someone touching the wall stands about 30–60 cm from it. Their torso echo is much larger than the hand echo and sits 1–2 range bins in front of the wall, and breathing and sway modulate it. The arm also moves. In practice the sonar sees "a person moved near the wall", and the hand is the weakest component.

### Gaps
- I found no published evaluation of "stop-at-boundary" detection accuracy for acoustic sensing.

---

## 4. Echo strength at 3–4 m, directionality, classroom clutter and noise

### Takeaway
Stock phones reach about 1–2 m for reliable sensing. That is the limit reported by PowerPhone for the factory setting and by SpeakerGesture for prior work. 3–4 m is beyond the stock limit for hand-scale targets. Multiple people, a fan, and an omnidirectional speaker make the problem worse.

### Cited Findings
- Stock smartphone sensing range is about 1 m (5 mm tolerance) for fine tracking and **2 m** for human-presence detection. A Figure 3 description says the signal becomes unusable at 5 m at 48 kHz. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- Prior acoustic gesture sensing was limited to about 1 m before SpeakerGesture's 4–5 m, which used a smart-speaker array. — [SpeakerGesture ACM](https://dl.acm.org/doi/10.1145/3560905.3568528)
- Phone speakers are not designed for ultrasound. The codec and SoC run a speaker-protection algorithm that tracks temperature and throttles power, which limits sustained loud near-ultrasound output. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- CovertBand had to raise pulse volume to audible levels to extend range. — [UW News](https://www.washington.edu/news/2017/08/16/computer-scientists-use-music-to-covertly-track-body-movements-activity)
- LLAP is described as not resilient to nearby interference (other moving people). — [Survey](https://arxiv.org/pdf/1901.03450)
- A 2023 survey notes that commodity smartphones contain a single microphone, or a limited number, which poses additional challenges versus arrays. — [Search abstract of a smartphone ultrasonic gesture survey](https://www.semanticscholar.org/paper/Hand-Gesture-Recognition-Based-on-Active-Ultrasonic-Wang-Hou/e466e019fb398bec6470ce28d776614534638652)

### Inferences (estimates)
- **SNR falloff:** a small scatterer gives a two-way spreading loss of about 1/R⁴ in power, i.e. 40 log R. Going from 0.5 m (where LLAP and Strata shine) to 3.5 m costs 40·log10(7) ≈ **34 dB**, before air absorption. Air absorption at 20 kHz is roughly 0.3–0.6 dB/m depending on humidity, about 2–4 dB over a 7 m round trip (approximate textbook values). Published systems at 0.5 m have maybe 20–30 dB of margin, so at 3.5 m the hand echo is near or below the noise floor on a stock phone unless long coherent integration (hundreds of ms) is used. That integration conflicts with fast detection.
- **Directionality:** at 20 kHz the phone speaker port (a few mm) is still small relative to λ = 1.7 cm, so the radiation is broad. Below about 10 kHz it is essentially omnidirectional. The whole classroom, including students, desks and the ceiling, gets insonified and adds clutter at all ranges. The single mic has no angle discrimination, so every reflector at 3.5 m range lands in the same bin as the wall spot: other parts of the wall, people at the same distance, the board frame. Range-only sensing defines a **sphere-like shell**, not a spot on the wall.
- **Multiple people:** anyone moving at about the same range as the wall (for example a person in the front row 3.5 m from the phone at a different angle) produces the same signature as the hand. Without angle information this is unfixable.
- **Projector fan noise** is mainly broadband with most energy below about 5 kHz (typical fan acoustics, not measured here). That argues for the 18–22 kHz band and against an audible 2–8 kHz chirp. Fans and any ultrasonic emitters (some projectors' switching supplies, occupancy sensors) are an unverified risk in the 18–22 kHz band.

### Gaps
- POCO M4 Pro speaker SPL at 18–22 kHz and mic noise floor: not found.
- I found no measured hand radar cross-section (target strength) at 18–22 kHz in the literature reviewed.

---

## 5. Setup tricks: horn, placement, clutter subtraction, calibration

### Takeaway
Tricks can buy maybe 10–20 dB of SNR and reduce clutter. None fixes the roughly 4 cm range-resolution limit, so none turns this into a touch-vs-hover detector.

### Cited Findings
- Background (static) subtraction via CIR differencing and selecting the tap tied to the moving target is standard practice (Strata). — [Strata](https://dl.acm.org/doi/10.1145/3081333.3081356)
- Beamforming is how BreathJunior extends range on smart speakers, and BeamBand uses it on a wrist array. Both need mic arrays a single phone lacks. — [Survey](https://arxiv.org/pdf/1901.03450); [BeamBand](https://mynkgoel.github.io/pdfs/beamband.pdf)
- Software-only range extension: LASense, layered on PowerPhone, took the range from 4.5 m to 6 m. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)

### Inferences (estimates)
- **Horn or cone on the speaker** (paper or 3D-printed, mouth of about 10–15 cm): at 20 kHz this could give about 10–15 dB of directivity gain and aim the beam at the touch zone. That reduces side clutter from students. A second horn on the mic gives similar receive gain. The wall echo is still in the same bin as the hand.
- **Wall-range calibration:** record the empty scene, then find the wall-echo delay by sub-sample interpolation of the matched-filter peak. That gives the wall distance to roughly mm precision, since a strong echo has high SNR, and defines a "touch shell" range gate. Then flag motion energy in the differenced CIR at that gate ±1 bin. Realistically this detects "a person or hand moved within about 4–8 cm of the wall plane in the illuminated zone", which is 'near wall', not 'touching'.
- **Placement:** put the phone on the projector, roughly perpendicular to the wall, with its speaker facing the wall and away from the fan exhaust. Avoid having students between the phone and the wall.
- **Bandwidth:** use 18–22 kHz linear chirps or ZC/GSM pilot sequences at 48 kHz, with frame lengths of about 10–40 ms. A 4 kHz band at 48 kHz can also be shifted with baseband down-conversion, which saves compute.

### Gaps
- I found no published results on phone sonar with horns.

---

## 6. API access: Chrome Android Web Audio vs native Android

### Takeaway
Chrome Android can probably capture 18–22 kHz if processing is turned off. Whether Chrome Android fully honours `echoCancellation/noiseSuppression/autoGainControl: false` depends on the device, and it should be checked with `track.getSettings()` plus a 19 kHz loopback test. Native code (AAudio/Oboe with `InputPreset::Unprocessed`, or AudioRecord `UNPROCESSED`) is the dependable path, but the hardware rate stays at 48 kHz without root. Round-trip latency (about 275 ms measured on this phone) doesn't matter for sonar ranging, because ranges are measured relative to the direct speaker-to-mic path in the same recording. It only delays the UI.

### Cited Findings
- Chrome has accepted `echoCancellation: false`. Chrome 64 documentation says that when `echoCancellation` is off, Chrome also leaves *hardware* noise suppression alone. — [Chrome blog: Disabling hardware noise suppression](https://developer.chrome.google.cn/blog/disabling-hardware-noise-suppression?hl=en)
- A 2020 report says that on a Pixel 2 (Android 10), Chrome and Firefox still applied processing with all three constraints set to false. — [Mozilla Bug 1626081](https://bugzilla.mozilla.org/show_bug.cgi?id=1626081)
- A developer write-up says Chrome may apply AGC regardless, and that setting `echoCancellation:false` (or the legacy `googAutoGainControl`) is the reliable switch. The recommended practice is to verify with `track.getSettings()`. — [addpipe: getUserMedia audio constraints](https://blog.addpipe.com/getusermedia-audio-constraints/); [GitHub issue example](https://github.com/NeuralIO444/Kinetic_Curator/issues/1052)
- A WebKit bug reports that high frequencies are still low-passed in Safari even with `echoCancellation:false`. This applies to Safari/iOS, not Chrome, but it shows the browser capture path can filter near-ultrasound. — [WebKit Bug 192932](https://bugs.webkit.org/show_bug.cgi?id=192932)
- Chrome is shipping an `echoCancellationMode` control for getUserMedia (Intent to Ship). — [blink-dev](https://groups.google.com/a/chromium.org/g/blink-dev/c/x5nfoaUAUWY)
- The Android UNPROCESSED source must be advertised via `PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED`, and its 7–22 kHz response can vary by ±30 dB. Near-ultrasound capability is advertised via `PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND` (and there is an analogous speaker property). — [CDD 7.8](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-platform-12.1.0_r10/7_hardware-compatibility/7_8_audio.md); [CDD 5.11](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-7.1.2_r1/5_multimedia/5_11_unprocessed-audio.md)
- Requesting a higher sample rate in an app is interpolated by AudioFlinger, so the true hardware rate stays at 48 kHz. — [PowerPhone](https://pmc.ncbi.nlm.nih.gov/articles/PMC12765216/)
- **Elliptic Labs on Xiaomi:** Xiaomi phones (Mi MIX 2S, Mi 9/9 SE, Redmi K50 Gaming and others) use Elliptic Labs' "virtual proximity sensor". It sends ultrasound from the earpiece and picks up the reflection with the mic, using existing hardware, and is deployed on close to 1 billion devices. — [Elliptic Labs – Mi MIX 2S](https://ellipticlabs.com/mfn_news/latest-xiaomi-mi-mix-2s-thin-bezel-created-by-elliptic-labs-ultrasound-technology); [Business Wire](https://www.businesswire.com/news/home/20200929006199/en); [GizChina device list](https://en.gizchina.it/?p=641064)

### Inferences
- If the POCO M4 Pro uses Elliptic's ultrasonic proximity (unconfirmed), its earpiece and mic path is known to pass near-ultrasound. That proximity function works at only a few cm, though, and the OEM DSP may own or filter that band during calls. That is not relevant to Chrome capture.
- Latency: the sonar estimate uses the direct-path peak in each recorded frame as the time-zero reference, so the 275 ms output-to-input latency cancels out. The real issue is **clock jitter or drift** between the playback and capture streams, which smears the CIR phase. Generating the probe in an AudioWorklet and correlating in the same worklet is the recommended approach. Expect detection-to-UI latency of about 300–400 ms in Chrome, and about 20–50 ms native with Oboe.
- Native app recipe: Oboe input preset `Unprocessed` (falling back to `VoiceRecognition`), 48 kHz, mono, `PerformanceMode::LowLatency`, with output on the bottom loudspeaker via `Usage::Media`. Check `AudioManager.getProperty(PROPERTY_SUPPORT_MIC_NEAR_ULTRASOUND)` and `PROPERTY_SUPPORT_SPEAKER_NEAR_ULTRASOUND`.

### Gaps
- POCO M4 Pro specifics were not found: whether it has an Elliptic virtual proximity sensor, its near-ultrasound property values, and its UNPROCESSED support. All need on-device checks.
- Which Android `AudioSource` Chrome selects when `echoCancellation:false` (MIC vs VOICE_RECOGNITION vs VOICE_COMMUNICATION) was not confirmed from Chromium source this session.

---

## Bottom line for ranking (my synthesis)
- **Touch vs hover at 1–5 cm, from 3–4 m, single stock phone: not feasible.** The range bin is 4.3 cm (7 cm measured two-target resolution on factory phones). The hand echo is about 30–40 dB weaker than the wall echo in the same bin. Absolute accuracy at metre range is 8–18 cm at best (CovertBand, body scale). Phase wraps every 0.86 cm with no absolute reference. There is no published precedent.
- **"Someone's hand or body moved into the 5–10 cm shell near the wall" vs "standing still nearby": weakly feasible** with an 18–22 kHz chirp, CIR differencing, wall-range gating, horns and ideally a native app. It is badly confused by other people at the same range, because one mic gives no angle information. It cannot localise *where* on the wall.
- **Best role:** a secondary or confirmation signal (Doppler shows "a hand moved toward the wall and stopped" in the wall range gate), fused with a contact-specific modality such as passive tap or impact acoustics or wall vibration. It should not be the primary touch detector.
- Ways around the limit (192 kHz rooted firmware à la PowerPhone, mic arrays à la SpeakerGesture) fall outside the "stock phone, browser" constraint. Even PowerPhone's 1 cm resolution would only just reach the 1–5 cm hover gap, and that was not shown at 3–4 m against a wall.
