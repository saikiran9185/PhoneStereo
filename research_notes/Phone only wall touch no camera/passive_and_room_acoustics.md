# Passive and room-acoustic cues for wall-touch detection from a phone 2.5–4 m away (no camera, phone NOT on the wall)

Scope: whether a POCO M4 Pro sitting near the projector, 2.5–4 m from a painted classroom wall, can hear or sense a hand touching the wall, and tell it apart from hovering or standing near it. "Measured" means a cited source reports it. "Estimate" means I worked it out from physics and nobody measured it. Estimates are labelled.

Bottom line: from 3 m away, a quiet fingertip or palm touch makes no usable sound. A deliberate knock or slap can be heard, but it looks like the other impulsive noises in a classroom. Distant active sonar can see that a body or arm is moving near the wall. It cannot resolve the last few millimetres between hovering and touching. Every published tap-sensing system that works well picks up vibration through the surface, using a contact mic or a device resting on that surface.

## 1. Airborne tap/touch sound vs classroom noise

### Takeaway
A soft fingertip touch on painted plaster or concrete makes almost no impact sound. At 3–4 m it is far below occupied-classroom noise of about 48–77 dBA, so it cannot be heard in practice. A deliberate knuckle knock or palm slap is impulsive and probably reaches the phone above the background. But it has no feature that separates it from desk knocks, dropped pens, claps or footsteps, so the false-positive rate in a busy room will be high.

### Cited Findings
- ANSI S12.60 limits *unoccupied* classroom background noise to 35 dBA (1-h average, single-mode mechanical system) or 37 dBA (multi-mode), with mechanical systems and educational equipment running — [Frontiers in Built Environment 2021 (summary of ANSI S12.60)](https://www.frontiersin.org/articles/10.3389/fbuil.2021.688395/pdf)
- *Occupied* classrooms measured in Korea (57 active classes): mean 54.9 dBA (σ 6.6) for ages 8–10 and 48.6 dBA (σ 5.4) for ages 12–15. The gap between occupied and unoccupied rooms was about 15 dBA for younger pupils — [koreascience.kr study](https://koreascience.kr/article/JAKO202432043355275.pdf)
- UK primary classrooms: average 56 dBA during quiet activities, up to 77 dBA in the noisiest activities. In 27 Korean class sessions the mean ranged from 40 to 67 dBA — [Salford repository (Shield et al.)](https://salford-repository.worktribe.com/OutputFile/1496173); [Euronoise 2018 paper](https://www.euronoise2018.eu/docs/papers/294_Euronoise2018.pdf)
- Projectors and charging carts with fans are named as classroom background-noise sources in addition to HVAC. I found no measured dBA or spectrum for projector fan noise alone — [Acoustics Today, Fall 2018](https://acousticstoday.org/issues/2018AT/Fall2018/files/basic-html/page21.html)
- Hand claps for comparison. In a concert hall with typical background noise, a hand clap averaged 26.4 dB above background (SD 4.4 dB), reported by Seetharaman & Tarzia, cited second-hand. Guinness-record claps peaked at 113–117 dBA, distance not stated (aggregator source, weak) — [engineerfix (secondary)](https://engineerfix.com/how-loud-clapping-becomes-an-acoustic-challenge/); [calendar-uk Q&A (weak)](https://calendar-uk.co.uk/frequently-asked-questions/how-many-db-is-a-hand-clap)
- An uncited engineering blog gives 85–95 dBA for a forceful clap, with peaks above 120 dB at close range. Treat this as unverified — [engineerfix](https://engineerfix.com/how-loud-clapping-becomes-an-acoustic-challenge/)
- Door-knock detection patent: knock loudness depends on door material, thickness, force applied, and distance to the detector. It gives no typical value — [US 11259126](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11259126)
- Standard impact-sound testing (ASTM E492/E1007 tapping machine, ISO 10140-3 rubber ball) measures sound in the room *below* a floor. I found no bare-finger or knuckle source data — [ASTM E1007](https://softbank.iust.ac.ir/Railway/Codes/ASTM-standard/ASTM_2004_Standards/ASTM%202004%20PART%20IB/ASTM%202004%20Volume%2004.06%20Thermal%20Insulation_%20Environmental%20Acoustics/E%201007%20-%2097%20%20_RTEWMDCTUKVE.pdf)
- A hobby airborne tap-localisation project found tapping worked worse than finger snapping, because table vibration jammed the lag detection — [cansik/sonic-touch-localization](https://awesome.ecosyste.ms/projects/github.com%2Fcansik%2Fsonic-touch-localization)
- Two patents triangulate taps using microphones *off* the surface. One deliberately keeps the mic off the surface to avoid timing distortion from solid-borne vibration. No distance or accuracy figures are given — [US 10031625](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10031625)

### Distant-mic tap papers: what they actually use
- **Toffee** (Xiao, Harrison et al., MobileHCI '14): acoustic TDOA from a device *resting on* the table ("requires only a hard tabletop and gravity", which acoustically couples the device to the surface). Mean bearing error 4.3° (laptop prototype). Works over an area many times larger than the device. It does not detect taps through the air from metres away — [Robert Xiao project page](https://www.robertxiao.ca/research/toffee/); [Chris Harrison page](https://chrisharrison.net/index.php/Research/Toffee)
- **Scratch Input** (Harrison & Hudson, UIST '08): a stethoscope-coupled mic placed against the surface. Works on wood, fabric and **wall paint**. About 90% accuracy on 6 gestures after under 5 min of training. No spatial location — [project page](https://www.chrisharrison.net/index.php/Research/ScratchInput); [Wired summary](https://wirednews.pages.dev/2009/08/scratch-input)
- **Acoustic Barcodes** (UIST '12): a contact mic on the surface or object. The team later showed a phone's internal mic could also read the swipe sound, at close range — [project page](https://chrisharrison.net/index.php/Research/AcousticBarcodes); [Hackaday](https://hackaday.com/2012/10/11/acoustic-barcodes-deliver-data-with-a-fingernail-and-microphone)
- **ALTo**: piezo contact mics on the surface, TDOA localisation, 1.45 cm (x) and 2.72 cm (y) error — [arXiv 2108.06837](https://arxiv.org/pdf/2108.06837)
- **Acustico**: wrist-worn TDOA device, RMSE 7.6 mm (x) and 4.6 mm (y) across surfaces. On-body, not distant — [Toffee search summary / ALTo related work](https://ar5iv.labs.arxiv.org/html/2108.06837)
- **KAIST smart-table/mirror touch**: smartphone-based TDOA calibrated against distance, aiming for about 1 cm error. It relies on solid-surface transmission — [K-ERC summary](https://k-erc.eu/2018/12/korea-rd-research-trends-and-results/2431/)
- **MoBat** (TUM 2015): four corner mics on a 1×2 m table, about 3 cm accuracy. Phones are the sound *sources* in this setup — [Kreskowski et al.](https://www.campar.in.tum.de/pub/kreskowski2015mobat/kreskowski2015mobat.pdf)

### Inferences
- **(Estimate) Distance loss.** In free field, going from 1 m to 3.5 m costs 20·log10(3.5) ≈ 11 dB. Classrooms are reverberant. Critical distance is r_c ≈ 0.057·√(V/RT60). For V ≈ 200 m³ and RT60 ≈ 0.6–0.8 s, r_c ≈ 0.9–1.0 m. At 3–4 m the phone is well inside the reverberant field, so the direct-path onset arrives about 10–12 dB below the later reverberant energy. Overall level does not keep falling with distance, but the onset becomes less sharp and harder to time.
- **(Estimate) Fingertip touch.** A soft touch has almost no impact velocity, so it produces only faint broadband contact or friction noise, plausibly in the tens of dB SPL right at the wall. That is at least 20–40 dB below a 50–60 dBA occupied room. **Treat a soft touch as acoustically undetectable at 3 m.** No measured source found.
- **(Estimate) Deliberate knuckle knock or palm slap.** A knock on a hollow door is clearly audible across a room. A knock on a massive plastered masonry wall radiates less, because the wall barely moves. The phone will probably see an impulse that is clearly above the room's 50–60 dBA average in short time windows. But desk knocks, dropped objects, claps, footsteps and plosive speech produce similar transients from all directions. Without direction finding (section 4), the false-positive rate in an occupied room would be high. The approach works only if users are asked to *knock deliberately* (a protocol change), not to touch.
- None of the cited tap-sensing systems reports passive airborne detection of surface taps at 2.5–4 m. Their success depends on structure-borne coupling.

### Gaps
- No peer-reviewed measured SPL at 1 m for fingertip taps, knuckle knocks or palm slaps on walls was found.
- No measured spectrum or dBA for classroom projector fans was found. Manufacturer fan-noise specs, typically 25–40 dB, were not retrieved and are not cited.
- No paper measuring onset-detector false-positive rates for knocks against classroom speech, desk knocks or footsteps was found.

## 2. Structure-borne vs airborne energy; phone accelerometer on a desk

### Takeaway
Surface-tap systems work because most of a tap's useful energy stays in the solid. Very little radiates into air, especially from a heavy wall. A phone resting on a separate desk or projector table 3 m away is not coupled to the wall, and its accelerometer is capped at 200 Hz on Android 12+. In practice the accelerometer would pick up footsteps and desk bumps, not a hand on a wall.

### Cited Findings
- Toffee depends on gravity coupling the device to the surface it senses. Scratch Input and Acoustic Barcodes use mics coupled to the surface. Scratch Input works on painted walls *when the sensor touches the wall* — [Toffee](https://www.robertxiao.ca/research/toffee/); [Scratch Input](https://www.chrisharrison.net/index.php/Research/ScratchInput)
- A Cornell reimplementation of Scratch Input high-passes the signal above 3 kHz to reject ambient noise. Scratch energy is high-frequency — [Cornell ECE4760 project](https://people.ece.cornell.edu/land/courses/ece4760/FinalProjects/s2009/gh96_jbw48/gh96_jbw48/index.html)
- Android 12 (API 31+): sensor sampling is throttled to **200 Hz** unless the app declares `HIGH_SAMPLING_RATE_SENSORS`. Requesting faster rates without it throws a SecurityException in debug builds. The minimum delay is capped at 5000 µs. SensorDirectChannel is capped at RATE_NORMAL (about 50 Hz, per a third-party summary) — [Android 12 compat changes](https://developer.android.com/about/versions/12/reference/compat-framework-changes); [GeeksforGeeks summary](https://www.geeksforgeeks.org/?p=945591)

### Inferences
- **(Estimate) Accelerometer.** At 200 Hz the usable band is under 100 Hz, which misses the kHz content that tap systems use. A native app can declare `HIGH_SAMPLING_RATE_SENSORS` to get the hardware maximum, often about 400–500 Hz on mid-range IMUs (not verified for the POCO M4 Pro). Chrome DeviceMotion is commonly delivered at about 60 Hz. **(Not verified. No source retrieved for the Chrome interval.)**
- **(Estimate) Floor and building path.** A hand tap on a masonry wall sends very little energy into the floor and on to a table 3 m away, compared with footsteps (heel impacts) or someone leaning on the desk. Expect SNR well below 0 dB in an occupied room. This cue is not worth pursuing.
- **Wall contact compared (from prior work, noted briefly).** A phone taped to the wall gets the structure-borne path directly, as Scratch Input, Toffee and ALTo do. Tap-localisation errors of cm to mm become possible. From a distance, detecting even a single tap is not reliable. Expect a gap of tens of dB in effective SNR (estimate) and a different category of reliability.

### Gaps
- No measured data was found on wall-tap vibration transmitted through a classroom floor to a separate table.
- POCO M4 Pro maximum IMU rate and Chrome Android DeviceMotion rate were not confirmed from primary sources.

## 3. Active probing: does a hand on the wall change the room or wall acoustic response at a distance?

### Takeaway
Active acoustic touch sensing that tells hover from touch (e.g., Touch & Activate) needs the speaker and mic **attached to the object**. From 3–4 m, phone sonar at 18–20 kHz can track gross body motion (CovertBand: up to about 6 m, 8–18 cm error). That is about 1–2 orders of magnitude too coarse to tell a hand touching the wall from one hovering 1–5 cm away. The change in room absorption from a palm on a wall is negligible (estimate).

### Cited Findings
- **Touch & Activate** (Ono, Shizuki, Tanaka, UIST 2013): a vibration speaker and piezo mic *attached directly to the object* sweep a signal. Touch changes the object's resonant frequency response, which a classifier recognises — [Tsukuba IPLAB](https://www.iplab.cs.tsukuba.ac.jp/en/paper/6151b7ff551fd46e82abdb1b); [VibSense paper describing it](https://personal.stevens.edu/~ychen6/papers/VibSense%20Sensing%20Touches%20on%20Ubiquitous%20Surfaces%20through%20Vibration.pdf)
- A follow-up active-acoustic hover/touch study (Tsukuba, Kubo et al., MobileHCI 2019, as reported in search snippets) detected hover with 96.7% accuracy and touch with 100%. I could not extract the setup from the PDF. Based on the lab's line of work it is very likely object-attached transducers at close range, not distant sensing — [kubo_mobilehci2019.pdf](https://www.iplab.cs.tsukuba.ac.jp/paper/international/kubo_mobilehci2019.pdf)
- **CovertBand** (UW, IMWUT 2017): phone or speaker pulses at 18–20 kHz with the phone's mics as receivers. It tracks body motion up to about 6 m without barriers (3 m through walls), with 8–18 cm position error. It picks out repetitive movements such as arm pumping and walking — [UW News](https://www.washington.edu/news/2017/08/16/computer-scientists-use-music-to-covertly-track-body-movements-activity); [paper PDF](https://musicattacks.cs.washington.edu/activity-information-leakage.pdf)
- FingerIO (CHI 2016) does fine-grained finger tracking with active sonar. Its range figures were not retrieved. It is commonly described as working within tens of cm of the device (unverified here) — cited in [CovertBand paper](https://musicattacks.cs.washington.edu/activity-information-leakage.pdf)

### Inferences
- **(Estimate) Absorption change.** A palm is about 0.01–0.015 m². Even with an absorption coefficient α of about 0.5 against painted plaster's α of about 0.02, the added absorption is under about 0.007 m² sabins. A classroom's total absorption is typically tens of m² sabins, so the change is under about 0.05%. That is far below what an RT60 or room-impulse-response estimate can resolve in a room full of moving people.
- **(Estimate) What sonar would see.** A phone at 3–4 m playing 18–20 kHz (wavelength about 1.7–1.9 cm) would see the arm and body echo moving and stopping near the wall. Touch and close hover differ by a few mm to cm, below CovertBand-class resolution. Other people moving also change the echo channel all the time. At best this is a weak "someone is reaching at the wall" cue. It gives no touch/hover decision.
- **(Estimate) Wall resonance.** A thick masonry or plaster wall is heavily damped and very massive. Even with transducers attached, the change in its frequency response from a hand would be small. From 3 m through air, the probe barely excites the wall's structural modes.
- Phone speaker output and mic response above 18 kHz vary by device. POCO M4 Pro ultrasonic capability is unverified.

### Gaps
- No paper was found that does active acoustic touch sensing (touch vs hover on a surface) with the transducers *distant* from the object.
- The Kubo 2019 setup details could not be extracted (binary PDF, no text tool available).
- FingerIO range figures were not retrieved.

## 4. Microphone API details (Chrome Android / native) and 2-mic direction finding

### Takeaway
For raw audio, a native app using `AudioSource.UNPROCESSED`, or `VOICE_RECOGNITION` (noise reduction and AGC off by CDD mandate), is the dependable route. Chrome Android's handling of `echoCancellation/noiseSuppression/autoGainControl: false`, and of true stereo, depends on the device and is not well documented. Two phone mics about 15 cm apart give at most about 0.44 ms TDOA, roughly 21 samples at 48 kHz. That is enough to tell rough left/right/front direction for a sharp knock. It is too coarse, and too fragile under reverberation, to localise a point on the wall or to tell a wall knock from a nearby desk knock in the same direction.

### Cited Findings
- `UNPROCESSED` (API 24): "Microphone audio source tuned for unprocessed (raw) sound if available, behaves like DEFAULT otherwise." `VOICE_COMMUNICATION` "will for instance take advantage of echo cancellation or automatic gain control if available." `VOICE_PERFORMANCE` (API 29) minimises latency. `CAMCORDER` is tuned for video, with the same orientation as the camera — [Android MediaRecorder.AudioSource](https://developer.android.com/reference/android/media/MediaRecorder.AudioSource)
- Android CDD 5.4 (VOICE_RECOGNITION). Devices **MUST** by default disable noise reduction and AGC, and **MUST** capture at 44.1 or 48 kHz. They **SHOULD** have flat response ±3 dB from 100–4000 Hz, map 90 dB SPL at 1 kHz to RMS 2500 (16-bit) or −22.35 dBFS (float), stay linear from −18 to +12 dB re 90 dB SPL, and keep THD under 1% at 90 dB SPL. **STRONGLY RECOMMENDED**: ±20 dB over 5–100 Hz and ±30 dB over 4–22 kHz. These targets apply to every mic used. Raw capture SHOULD allow as many channels as the device has mics — [CDD 5.4 Audio Recording](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-mainline-12.0.0_r98/5_multimedia/5_4_audio-recording.md)
- Android pre-processing: an AEC should be available on VOICE_COMMUNICATION but not on by default (Android 10+) — [AOSP pre-processing](https://source.android.com/docs/core/audio/implement-pre-processing)
- Chromium's AudioRecord input path takes an echo-canceller flag from `params.effects() & ECHO_CANCELLER` to enable platform AEC (2014 patches; current behaviour not verified) — [Chromium CR 99033003](https://codereview.chromium.org/99033003/patch/120001/130014)
- getUserMedia stereo. With `echoCancellation`, `noiseSuppression` and `autoGainControl` set to false, Chrome gave true stereo on some hardware and the same mono channel duplicated on others. Requesting `channelCount: 2` together with `echoCancellation: false` is advised — [addpipe blog](https://blog.addpipe.com/recording-true-stereo-audio-using-getusermedia/); [discuss-webrtc](https://groups.google.com/g/discuss-webrtc/c/dwA_mGq0Ey8)
- Chrome can report `channelCount` 1 while delivering two channels. Other browsers have bugs where disabling AEC leaves audio only in the left channel — [W3C webrtc logs 2025](https://lists.w3.org/Archives/Public/public-webrtc-logs/2025Mar/0158.html); [WebKit bug 281978](https://bugs.webkit.org/show_bug.cgi?id=281978)
- Chrome has a mechanism to disable *hardware* noise suppression when `noiseSuppression: false` — [Chrome blog](https://developer.chrome.google.cn/blog/disabling-hardware-noise-suppression?hl=en)
- Toffee shows TDOA bearing from closely spaced sensors works when coupled to the surface (4.3° mean error) — [Toffee](https://www.robertxiao.ca/research/toffee/)

### Inferences
- **(Estimate) TDOA resolution.** Mic spacing d ≈ 0.15 m (top and bottom mics on a phone about 16 cm long; POCO M4 Pro mic positions not verified) gives a maximum delay d/c ≈ 0.44 ms, or ±21 samples at 48 kHz. With sub-sample interpolation, bearing resolution near broadside could be a few degrees *in anechoic conditions*. At 3–4 m in a reverberant classroom (direct-to-reverberant ratio about −10 dB), GCC-PHAT can still lock onto the first-arriving direct path of a sharp knock. It will fail on soft or slow sounds.
- **(Estimate) Ambiguity.** A two-mic array has a cone of confusion. It gives one angle, not a point, and cannot tell front from back or up from down. A desk knock on the same bearing as the wall, for example by a student sitting between the phone and the wall, would pass the filter. Direction gating could cut false positives from the side and behind by perhaps half or more. It cannot confirm wall contact.
- **(Estimate) Path choice.** Use a native app with UNPROCESSED where `AudioManager.getProperty(PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED)` says it is supported, otherwise VOICE_RECOGNITION, requesting stereo with a positional or index channel mask. In Chrome, request `{echoCancellation:false, noiseSuppression:false, autoGainControl:false, channelCount:2, sampleRate:48000}` and check that the two channels actually differ before relying on stereo. True stereo is not guaranteed on the POCO M4 Pro.

### Gaps
- No primary source was found for which Android AudioSource current Chromium selects when all three processing constraints are false, or whether it exposes both mics.
- POCO M4 Pro stereo capture support, mic spacing and UNPROCESSED support are unverified and need an on-device test.
- No published measurement of 2-mic phone TDOA accuracy on impulsive sounds at 3–4 m in a reverberant classroom was found.
