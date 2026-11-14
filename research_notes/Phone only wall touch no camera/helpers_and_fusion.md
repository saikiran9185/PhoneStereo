# Cheap helper hardware and sensor fusion for "is the person touching the projected wall?" (phone near projector, no camera)

Scope note: the main device is a POCO M4 Pro (Android, Chrome or a native app) sitting near the projector 2.5–4 m from the wall. Its sonar tracks roughly where the person or hand is. The question is what the cheapest extra hardware is that reliably supplies the moment of contact. A prior report already covered (a) a 650 nm laser plane + camera (~₹32) and (b) a wall-taped phone using audio onset. They are referenced here only for comparison.

Notation: **[measured]** means a number reported from an experiment or datasheet. **[estimate]** means my own calculation or reasoning, not a published measurement.

---

## 1. A second old Android phone taped to the wall (contact mic / accelerometer)

### Takeaway
A wall-coupled phone picks up taps (impulsive contact) well, but by physics it cannot detect a silent, still palm. It works as a "tap event" sensor, not a "touching now" sensor. Browser-only access gives a 60 Hz accelerometer cap in Chromium, which is too coarse for tap transients. The microphone (getUserMedia at 44.1/48 kHz) is the usable channel. Prior work (ScratchInput, Toffee, Acustico) shows surface-acoustic sensing detects taps very reliably. Localization from one device needs multiple sensors and per-surface training.

### Cited Findings
- ScratchInput (Harrison & Hudson, UIST '08) used a modified stethoscope + microphone into a computer's audio jack to pick up fingernail sounds on textured surfaces, explicitly including painted walls. Six gestures were recognised at ~90% accuracy after <5 min of training **[measured]**. The system cannot tell *where* on the surface the input happened, and it works poorly on glass and smooth materials — [Chris Harrison project page](https://www.chrisharrison.net/index.php/Research/ScratchInput); [Wikipedia: Scratch input](https://en.wikipedia.org/wiki/Scratch_input)
- A Cornell ECE 4760 replication used a stethoscope mic plus a microcontroller and a 3 kHz cutoff to reject ambient noise — [Cornell project](https://people.ece.cornell.edu/land/courses/ece4760/FinalProjects/s2009/gh96_jbw48/gh96_jbw48/index.html)
- Toffee (Xiao et al., MobileHCI '14) used acoustic TDOA from sensors in a device resting on a hard table. It resolved the bearing of tap events with 4.3° mean error on a laptop prototype **[measured]**. It needs "a hard tabletop and gravity" for coupling — [Robert Xiao: Toffee](https://www.robertxiao.ca/research/toffee/)
- Acustico (Gong, Gupta, Benko, UIST 2020): a wristband with 2 accelerometers + 2 microphones. Tap detection F1 = 0.9987 across environmental noise. 2D localization RMSE 7.6 mm (X) / 4.6 mm (Y) **[measured, from abstract]**. Hackster notes it needs per-surface training and did not test vibration picked up while the hand is off the surface — [Meta Research: Acustico](https://research.fb.com/publications/acustico-surface-tap-detection-and-localization-using-wrist-based-acoustic-tdoa-sensing/); [Hackster](https://www.hackster.io/news/tapping-into-vibrations-9a684dfb2a65). Note: Hackster calls these "mean errors" while the abstract says RMSE.
- Chromium caps Generic Sensor API sampling (Accelerometer etc.) at 60 Hz for most sensor types — [Chromium generic_sensor README](https://chromium.googlesource.com/chromium/src.git/+show/HEAD/services/device/generic_sensor/README.md). `DeviceMotionEvent.interval` only reports the hardware interval in ms and cannot be set — [MDN](https://developer.mozilla.org/en-US/docs/Web/API/DeviceMotionEvent.interval). An older (2014) report says devicemotion fired about every 50 ms in mobile browsers — [SO archive](https://kiwix.lema.org/stackoverflow.com_en_all_2023-11/questions/27124016/get-html5-devicemotion-events-fire-at-a-higher-rate)

### Inferences
- **Fingertip tap vs palm vs still hand [estimate]:** a fingertip tap or nail click makes a sharp broadband impulse, which is easy to detect. A palm slap is lower-frequency but loud, so it is also easy. A slow, soft palm placement and a hand already resting on the wall produce almost no vibration. This is the "silent contact problem", and no vibration sensor solves it. The UI design therefore has to ask users to *tap* (as on a phone screen), not press-and-hold. A hold could be inferred as "tap onset, then sonar says the hand is still at the wall, until the sonar sees it retreat".
- **Browser-only feasibility [estimate]:** use the mic via getUserMedia + an AudioWorklet onset detector. A 60 Hz accelerometer (~16 ms per sample) will alias or smear a ~1–5 ms tap transient, so it is only useful as a weak confirmation. A native Android app can request SENSOR_DELAY_FASTEST (typically 200–500 Hz on mid-range phones; not verified for the M4 Pro or the donor phone, so this is a gap).
- **Coupling [estimate]:** the phone must be pressed firmly against the wall (double-sided foam tape is poor because it damps; hard tape or a putty bead with the phone back on the plaster is better). A phone case damps the signal.
- **Range across the wall [estimate]:** painted brick or plaster carries tap energy over a few metres. The signal falls with distance, so taps far from the phone produce smaller onsets. Per-wall calibration of the threshold (or 2 phones) is recommended.
- **Wi-Fi latency [estimate]:** WebSocket over a local LAN or phone hotspot is typically a few ms to tens of ms with occasional 100+ ms spikes from Wi-Fi power-save. Timestamping each onset on the wall phone and sending it (after a clock sync, for example an NTP-style ping exchange) lets the fusion step align events even when delivery is jittery. WebRTC DataChannel (unreliable/unordered mode) avoids TCP head-of-line blocking but is more setup.

### Gaps
- No published measurement of a phone taped to a classroom wall as a contact sensor (detection rate vs distance) was found.
- No measured LAN WebSocket latency distribution for Android-to-Android was found in this search.
- Native accelerometer max rate on POCO M4 Pro or a typical donor phone was not verified.

---

## 2. Piezo disc(s) on the wall into the phone's 3.5 mm or USB-C audio input (no microcontroller)

### Takeaway
A piezo disc glued to the wall is a very sensitive tap sensor and costs only a few rupees. Plugging it straight into a phone mic input is possible but poorly matched. The phone's low-kΩ mic input loads the high-impedance piezo, cutting low frequencies, and mic bias DC sits on the line. A series resistor or capacitor, or better a one-transistor/JFET or op-amp buffer powered by mic bias, fixes this. Like any vibration sensor, it detects taps, not silent contact. Two piezos into a *stereo* USB audio interface allow 1D TDOA along the wall. Note that most cheap USB-C dongles have a mono mic input.

### Cited Findings
- Piezo discs measure roughly 4.7–37 nF, typically 10–24 nF (e.g. 20 mm/0.22 mm = 20 nF; 27 mm/0.52 mm = 20 nF; 35 mm/0.3 mm = 30 nF) **[measured]** — [Logos Foundation: piezo disks](https://logosfoundation.org/g_texts/Orfeus/preamps/piezo_disks.html)
- The Logos design uses a charge/current amplifier: a JFET-input op amp (TL071, TLC071, AD820), a 22 MΩ feedback resistor (10–100 MΩ also usable), and Cf ≈ 1/10 of the disc capacitance (default 1.5 nF) — [Logos Foundation](https://logosfoundation.org/g_texts/Orfeus/preamps/piezo_disks.html)
- Piezo source impedance is high (~1 MΩ typical) and should be buffered before an ordinary audio input. Connecting a piezo directly to a mic input is described as "not a good idea" — [Logos piezo PDF](https://logosfoundation.org/g_texts/Orfeus/preamps/piezo_disks.pdf); [LOM contact mic knowledge base](https://knowledge.lom.audio/_export/xhtml/research/contact_mics)
- A forum user wiring a piezo into an iPhone TRRS jack found mic detection didn't trigger with 47 Ω on the headphone pins and 1.5 kΩ on the mic pin. Many mic inputs are around 1.5 kΩ. On TRRS (CTIA) the mic is on the sleeve/ring-2 — [cycling74 forum](https://cycling74.com/forums/connecting-a-piezo-mic-directly-to-the-iphone-trrs-headphone-beginers-question) (forum source; not authoritative)
- Unshielded piezo discs pick up EMI like an antenna. Insulating or shielding the top ceramic helps — [LOM](https://knowledge.lom.audio/_export/xhtml/research/contact_mics); [Zach Poff contact mic guide](https://zachpoff.com/resources/building-contact-mics/)

### Inferences
- **Loading [estimate]:** a 20 nF disc into a ~2 kΩ mic input forms a high-pass at f = 1/(2π·2kΩ·20nF) ≈ 4 kHz. Taps still have energy above 4 kHz, so a raw piezo will still "click" on the phone, but soft touches lose most of their (low-frequency) energy. A 1 MΩ series resistor doesn't help (it attenuates more). What helps is a buffer: a single JFET (e.g. J201/2N5457) source follower, or the classic "electret-style" trick, where the JFET drain goes to the mic line and the mic bias (~2–2.7 V through ~2.2 kΩ on Android) powers it. That trick is exactly how electret capsules are powered, so the phone mic bias can power a JFET buffer. A full op-amp charge amp needs more voltage/current than mic bias gives, so it would need a 9 V or 2×AA battery.
- **Phone detection [estimate]:** an Android headset jack only switches to the external mic if it sees a mic impedance on the mic pin (roughly 1–10 kΩ DC). A JFET buffer or a ~2.2 kΩ resistor across mic-to-ground makes the phone think a headset is present. Otherwise use a USB-C to 3.5 mm adapter *with* mic support (TRRS), or a USB audio card.
- **Signal levels [estimate]:** a direct finger tap on a disc gives volts. A tap 1 m away through plaster gives tens to hundreds of mV unloaded, which is ample for a mic input (the mic-level full scale is ~tens of mV). Clipping and ADC overload (not insensitivity) is the more likely issue for near taps. The phone's AGC/noise suppression should be disabled (getUserMedia `{echoCancellation:false, noiseSuppression:false, autoGainControl:false}` or the Android `UNPROCESSED` audio source).
- **Two-piezo TDOA [estimate]:** most USB-C dongles and the phone jack give **one** mic channel. Stereo input needs a USB audio interface with stereo line-in (some cheap "USB sound card" sticks are mono-in; check before buying). Bending/flexural waves in plaster are dispersive (speed depends on frequency, roughly a few hundred m/s to ~1–2 km/s), so pure TDOA on a wall is rough. Toffee and Acustico achieved bearing and mm-level localization only with careful sensing plus (for Acustico) per-surface training. For this project, sonar or other phone-based tracking supplying position, with the piezo supplying timing, is more robust than piezo TDOA localization.
- **Compared to prior "wall-taped phone audio onset" [estimate]:** a piezo is cheaper (~₹10–30 per disc) and better coupled (glued, rigid) than a taped phone, but needs a cable to the phone. Same physics, same silent-contact limit.

### Gaps
- No measured POCO M4 Pro (or generic Android) mic-bias voltage or input impedance was found. The 2–2.7 V / 2.2 kΩ figures above are typical values, not verified.
- No published signal-level-vs-distance data for piezo on painted plaster was found.
- Indian prices for piezo discs and USB stereo audio interfaces were not retrieved in this search.

---

## 3. Capacitive / electric-field touch on the wall (no camera)

### Takeaway
This is the only family that senses *contact or near-contact itself*, including a still, silent palm, so it solves the problem vibration sensors can't. Wall++ shows room-scale painted electrodes work, but needs custom electronics. For a student, the realistic version is a few foil, copper-tape or conductive-paint "zones" (behind a poster or under paint) wired to an MPR121 (~₹73–115) or ESP32 touch pins. Touch vs 1–5 cm hover can be separated by signal magnitude on a properly sized electrode, but only coarsely, with drift and per-person variation. Treat it as "hand at or very near the surface in zone N". Sonar or other tracking gives finer position.

### Cited Findings
- **Wall++** (Zhang, Yang, Hudson, Harrison, Sample; CHI 2018 Best Paper): conductive nickel paint applied with a roller and painter's tape, covered with an ordinary latex top coat. About $20/m² in retail materials **[measured cost]**. Supports mutual-capacitive touch/hover sensing plus airborne EM sensing of devices and people — [FIGLAB Wall++](https://www.figlab.com/research/2018/wall); [Nanowerk](https://nanowerk.com/nanotechnology-news/newsid=50015.php)
- Wall++ details: diamond electrode pattern performed best. The final wall was 12×8 ft (3.7×2.4 m), with electrodes in 22 columns × 15 rows (as reported: "70 cm electrodes on a 48 cm pitch"; this pairing looks inconsistent, so check the paper). Custom electronics with 37 coax connections and two multiplexed front ends. A hand hovering at 10 cm standoff was tested in capacitive mode. Six body poses were recognised. Silver paint was rejected for cost and carbon for high resistance. Roller application gave the highest conductivity and lowest variance — [Electronic Design](https://www.electronicdesign.com/technologies/analog/article/21806738/these-smart-walls-cant-talk-but-they-definitely-can-see). The touch-tracking error in mm was not available from the sources I could read (the PDF text could not be extracted) — [paper PDF](https://modernmobile.cs.washington.edu/docs/wallplusplus.pdf)
- MIT 6.810 lab: using a 10 MΩ series resistor in a simple RC capacitive sensor allowed sensing a finger up to ~10 cm. 10 kΩ would not **[measured, course lab]** — [MIT 6.810 lab 2](https://hcie.csail.mit.edu/classes/2021-fall-6810/lab2-sensing-touch-button.html)
- ESP32 touch pins: ~10×10 mm is a fingertip starting point. Larger electrodes raise baseline capacitance, lower relative sensitivity, and can false-trigger from nearby hands — [Zbotic](https://zbotic.in/esp32-touch-sensor-capacitive-input-for-smart-switches/). Dedicated proximity mode (threshold-tuned range, smaller threshold = longer range + more false triggers) is an ESP32-S3 feature, not classic ESP32 — [Espressif Touch Proximity Sensor docs](https://docs.espressif.com/projects/esp-iot-solution/en/latest/touch/touch_proximity_sensor.html). Espressif's touch design guide covers electrode/trace design — [esp-iot-solution touch_sensor_design](https://github.com/espressif/esp-iot-solution/blob/release/v1.0/documents/touch_pad_solution/touch_sensor_design_en.md)
- Bare Conductive: large sensors work better in proximity mode, and big solid-filled painted areas respond slowly, so hatch the fill — [Bare Conductive design rules (Hackster)](https://www.hackster.io/bareconductiveteam/electric-paint-sensor-design-rules-of-thumb-e78693); [Bare Conductive proximity guide](https://bareconductive.com/make/how-to-use-proximity-sensors)
- Capacitive touch vs hover discrimination is typically done by a higher threshold for touch than for hover, optionally with dynamic thresholds and baseline updates — [US10481723 Hover rejection through dynamic thresholding](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10481723); [US11216135](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11216135). Coarse electrodes suit presence and large gestures, fine ones suit fingers — [US12204713](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12204713)
- **Indian prices (2025–2026 listings):** MPR121 12-channel module ₹73 sale (₹124 list) at Robocraze — [Robocraze](https://robocraze.com/products/mpr121-12-channel-capacitive-touch-sensor-controller-module-i2c-interface); ₹115 incl. GST at Probots — [Probots](https://probots.co.in/mpr121-capacitive-touch-sensor-controller-breakout-board-12-key.html). Official Espressif ESP32-WROVER-IE DevKitC at Robu, lowest tracked ₹2,038 (Nov 2025) — [CheapestInIndia tracker](https://www.cheapestinindia.com/price-history/esp32-wrover-ie-esp32-devkitc-core-board-for-arduino--66843b4d81e145da10bd6e5f); generic ESP32 38-pin listing at Robu — [Robu](https://aws.robu.in/product/esp32-38pin-development-board-wifibluetooth-ultra-low-power-consumption-dual-core) (price not captured). Bare Conductive 10 ml Electric Paint pen ₹1,705 incl. duties at Tanotis — [Tanotis](https://www.tanotis.com/products/sparkfun-bare-conductive-electric-paint-pen-10ml); also listed at Robu — [Robu](https://aws.robu.in/?p=975709).
- **Wireless link to the phone:** Android accepts BLE connection intervals down to 7.5 ms, but there is generally no benefit below 15 ms, and apps (and therefore Web Bluetooth) cannot request specific connection parameters — [Punch Through](https://punchthrough.com/ble-connection-interval-throughput/); [W3C web-bluetooth thread](https://lists.w3.org/Archives/Public/public-web-bluetooth-log/2020Oct/0007.html). An ESP32 forum user measured ~180 ms per update Android→ESP32 over BLE (Android "high" priority ≈ 11.25–15 ms interval) — [RemoteXY forum](https://forum.remotexy.com/viewtopic.php?id=1179). Nordic threads report ~1 s notification delays on some Android setups (older) — [Nordic DevZone](https://devzone.nordicsemi.com/f/nordic-q-a/22422/android-delayed-notification/88169)

### Inferences
- **Build this week [estimate]:** ESP32 (generic DevKit, likely ~₹350–500 from Robu/Robocraze/Amazon.in; the price was not confirmed in this search) + aluminium kitchen foil or copper tape (~₹100–300 a roll) taped to the wall *behind a paper or chart-paper poster* (projected onto). Use 4–10 zones of roughly 20×20 to 40×40 cm each, wired with short or shielded leads to the ESP32 touch pins (classic ESP32 has 10 touch pins), or to an MPR121 over I²C (12 channels). Firmware tracks a slow baseline and sets two thresholds: "near" (hover) and "touch". It sends events over WebSocket (ESP32 runs the server, the phone joins its SoftAP or both join a hotspot) or over BLE notifications to Web Bluetooth in Chrome Android.
- **Touch vs 1–5 cm hover [estimate]:** with foil behind thin paper, actual contact (palm flat on the poster) gives a much larger capacitance jump than a 1–5 cm hover, because coupling rises steeply as the gap closes and contact area grows. A fingertip touch on a 30 cm pad, however, can produce a delta similar to a whole palm hovering 2–3 cm away. So touch-vs-hover is reliable for palm-vs-hover, unreliable for fingertip-vs-close-palm. Smaller electrodes (≤10 cm) improve the fingertip case but need more channels. A capacitive zone is still the only cheap sensor here that detects a *silent still palm*.
- **Through paint [estimate]:** Wall++ proves electrodes under a latex top coat work. Painting conductive paint directly on a classroom wall probably isn't allowed, so foil behind a removable poster is the practical route. Bare Conductive at ~₹1,705 per 10 ml covers only small areas, so it is not cost-effective for zones; foil is.
- **Humidity and drift [estimate]:** Indian classrooms are humid, which causes baseline drift. A slow baseline tracker with recalibration when the sonar says "no one at the wall" is a natural fusion trick.
- **Rejecting other people [estimate]:** a zone fires for any body. Use the phone sonar's "which person/where" to gate it: accept a zone event only if the tracked hand position is within or near that zone.
- **WebSocket vs BLE [estimate]:** for a phone already on Wi-Fi, an ESP32 WebSocket is simpler and tends to have lower, steadier latency than Web Bluetooth notifications on Android, given the uncontrollable connection interval. Web Bluetooth requires HTTPS or localhost and a user gesture to pair.

### Gaps
- Wall++ quantitative touch-tracking error and touch/hover separation numbers could not be extracted (the PDF text was unreadable in this session). The report writer should cite only the figures above.
- No published measurement of touch vs 1–5 cm hover separation for foil-behind-poster electrodes of a given size on ESP32/MPR121 was found.
- Current generic ESP32 DevKit, copper tape and foil prices on Robu/Robocraze/Amazon.in were not captured.

---

## 4. IR light curtain / break-beam / ToF (VL53L0X/VL53L1X) along the wall surface

### Takeaway
A single ToF sensor looking *along* the wall can't tell "finger within a few mm of the wall" from "finger 10–20 cm out". Its beam is a 25–27° cone that becomes tens of cm wide over 1–2 m, and the wall itself returns in the cone. A grazing IR light curtain (many emitter/receiver pairs a few mm off the wall) works in principle, which is how commercial IR touch frames work, but on an uneven painted wall the beam must sit 5–15 mm out, so it detects "finger in the plane", not true contact. It is closer to the laser-plane approach than to a contact sensor.

### Cited Findings
- VL53L1X: typical full FoV 27°, programmable receiver FoV 15–27° (ROI). Range up to 4 m (long mode). Short mode ~1.3 m with better ambient immunity — [ST VL53L1X datasheet (DS12385)](https://www.makerguides.com/wp-content/uploads/2024/10/VL53L1X-Datasheet.pdf); [ST product page](https://www.st.com/content/st_com/en/products/imaging-and-photonics-solutions/time-of-flight-sensors/vl53l1x.html)
- Accuracy: ~±5 mm (SparkFun breakout claim). ST presentation: 1.5% white / 2.5% grey target, ±25 mm offset. Readings under ~4 cm are not accurate (carrier board note); one listing gives 30 mm min — [ST FlightSense presentation](https://www.st.com/content/dam/AME/2019/technology-tour-2019/anaheim/presentations/T2S8_Anaheim_Speed_of_Light_J.Kvam.pdf); [datasheet mirror](https://cdn.components.ru/media/PDF/1660996.pdf)
- Indian prices: VL53L0X ₹289 incl. GST (Probots) — [Probots](https://probots.co.in/laser-range-finder-distance-sensor-time-of-flight-vl53l0x.html); Waveshare VL53L0X ₹748 + GST (Hubtronics) — [Hubtronics](https://hubtronics.in/sensors/vl53l0x-distance-sensor); VL53L1X module listed at Probots — [Probots VL53L1X](https://probots.co.in/vl53l1x-time-of-flight-tof-laser-ranging-sensor-module-25mm-x-13-3mm.html) (price not captured)

### Inferences
- **Geometry [estimate]:** a 27° cone has diameter ≈ 2·d·tan(13.5°) ≈ 0.48·d, so it is ~24 cm wide at 0.5 m, ~48 cm at 1 m, and ~96 cm at 2 m. Even at the minimum 15° ROI it is ~26 cm wide at 1 m. With the sensor flush on the wall edge, half the cone hits the wall, which returns a constant (and, on uneven paint, noisy) reading. A finger then shows up as a *closer* return wherever it is inside the cone, whether at 2 mm or 15 cm from the wall. So ToF gives "something is in this strip of air, at distance X along the wall" (a 1D position), not contact. It could be a cheap position sensor (a few ToF units along the bottom edge giving X) for a fusion design, but not a touch sensor.
- **IR break-beam curtain [estimate]:** cheap IR break-beam pairs (3 mm/5 mm LED + receiver) on 2 opposite wall edges, a few mm off the surface, detect "finger crossed the plane". On an uneven painted wall the plane must stand off far enough to clear bumps (likely 5–15 mm), so hover inside that gap counts as a touch. A sparse curtain gives coarse X/Y. A full IR frame needs tens of pairs per metre, which is too much work for "this week". Sunlight is not an issue in a dark classroom, but the projector's light contains little 940 nm IR, so ambient noise is mostly fine.
- **Comparison to the prior ₹32 laser plane + camera [estimate]:** the IR curtain/ToF is the same "plane near the wall" concept with worse resolution and more parts. The laser plane + camera is cheaper and gives full 2D, but requires a camera, which this brief excludes.

### Gaps
- No published test of VL53L0X/VL53L1X used grazing along a wall to detect fingertip near-contact was found.
- Indian prices for IR break-beam pairs were not retrieved.

---

## 5. Fusion: distant sensor for position + contact sensor for the moment of touch

### Takeaway
The standard pattern, patented by Intel (US9971457) and implied by Acustico/Toffee, is: a far sensor establishes that a hand is close to the surface and moving toward it, which *arms* a contact or vibration sensor, and touch = arm AND contact event within a short window. For this project: phone sonar = "who/where, approaching the wall", wall helper (piezo, wall phone, or capacitive zone) = "contact now". Gating by sonar position rejects other people's touches and random knocks. Capacitive zones also give "still touching".

### Cited Findings
- Intel US 9,971,457 B2, "Audio augmentation of touch detection for surfaces" (filed 26 Jun 2015, granted 15 May 2018; inventor Carl C. Hansen): a depth camera detects a fingertip within a threshold distance of a projected surface and moving toward it. The microphone/audio sensor is enabled only then (disabled outside the threshold, so sounds aren't monitored when no touch is imminent). Touch is declared when the vibration is detected while the fingertip is approaching. The vibration is compared to a "touch signature". Aim: improve the accuracy of the *time of touch*. No numeric thresholds or ms values are given — [Google Patents US9971457](https://patents.google.com/patent/US9971457); [Justia](https://patents.justia.com/patent/9971457)
- Acustico fuses surface-wave (accelerometer) and airborne (mic) channels for tap detection and localization. F1 0.9987, RMSE 7.6/4.6 mm — [Meta Research](https://research.fb.com/publications/acustico-surface-tap-detection-and-localization-using-wrist-based-acoustic-tdoa-sensing/)
- Toffee: TDOA bearing from a device coupled to the surface, mean 4.3° error — [Toffee](https://www.robertxiao.ca/research/toffee/)
- Wall++ combines capacitive (touch/hover, near-field) and EM (wearable/device tracking at a distance) sensing in one painted wall, which is itself a near/far fusion — [Electronic Design](https://www.electronicdesign.com/technologies/analog/article/21806738/these-smart-walls-cant-talk-but-they-definitely-can-see)

### Inferences (proposed fusion design for the report)
- **State machine [estimate]:** `AWAY → NEAR (sonar: hand within ~20–30 cm of the wall plane and range decreasing) → TOUCH (helper contact event within ±50–100 ms of NEAR, and sonar position consistent) → HOLD (capacitive still active, or sonar range stable at the wall) → RELEASE (capacitive drops, or sonar range increases)`. Touch position = sonar estimate, optionally snapped to the capacitive zone or piezo TDOA side.
- **Timing alignment [estimate]:** timestamp at the source (helper device clock) and sync clocks with a ping exchange at startup and every ~10 s (take the minimum-RTT sample). The fusion window must cover sonar frame period (e.g. 20–50 ms chirps) + network jitter, so ~100–150 ms is a sensible initial window, tuned from logs. Sonar alone can mark a "candidate touch" when range velocity goes to ~0 at the wall-plane distance. The helper then confirms or rejects it.
- **Rejecting others [estimate]:** (1) accept helper events only while the sonar is in NEAR for the tracked user; (2) for capacitive, accept only the zone that matches the sonar's X; (3) for piezo/wall-phone, use amplitude or, with 2 sensors, left/right TDOA side to check consistency with the sonar's X. Knocks (door slams, desks) produce vibration with no NEAR state and are dropped. This is exactly Intel's arming logic.
- **Failure modes [estimate]:** silent palm placement makes vibration helpers miss (capacitive catches it). Sonar multipath near the wall gives position error (capacitive zones snap it). Wi-Fi spikes (source timestamps + window) can be handled.

### Gaps
- No published system was found that pairs *acoustic sonar from a phone at 3 m* with a wall contact sensor. The fusion design above is extrapolated from the Intel patent and the near/far sensing literature.
- No published fusion window sizes in ms were found (Intel gives none).

---

## 6. Ranking (cost, effort, reliability, build-this-week with Indian parts)

### Takeaway
Best fallback tier for a student this week: **(A) ESP32 or MPR121 + foil zones behind a poster** (detects real contact including a still palm; ~₹500–800; 1–2 days). Next: **(B) piezo disc + JFET buffer into the phone mic** (~₹50–150; detects taps only; very low latency because it's wired to the main or wall phone). Then **(C) old phone taped to the wall** (₹0 if a phone is available; taps only; Wi-Fi latency). **ToF/IR curtain** ranks last for touch (hover ambiguity), but ToF is usable as an extra X-position sensor. Combining (A) or (B) with sonar gating is the recommended fusion.

### Cited Findings
- Component prices: MPR121 ₹73–115 ([Robocraze](https://robocraze.com/products/mpr121-12-channel-capacitive-touch-sensor-controller-module-i2c-interface), [Probots](https://probots.co.in/mpr121-capacitive-touch-sensor-controller-breakout-board-12-key.html)); VL53L0X ₹289–883 ([Probots](https://probots.co.in/laser-range-finder-distance-sensor-time-of-flight-vl53l0x.html), [Hubtronics](https://hubtronics.in/sensors/vl53l0x-distance-sensor)); Bare Conductive pen ₹1,705 ([Tanotis](https://www.tanotis.com/products/sparkfun-bare-conductive-electric-paint-pen-10ml)); official ESP32 DevKitC-WROVER-IE ~₹2,038 ([tracker](https://www.cheapestinindia.com/price-history/esp32-wrover-ie-esp32-devkitc-core-board-for-arduino--66843b4d81e145da10bd6e5f)); Wall++ ~$20/m² ([FIGLAB](https://www.figlab.com/research/2018/wall)).

### Inferences — ranking table [estimate; rupee totals partly unverified]

| Option | Approx cost | Effort | Touch vs hover | Silent palm? | Latency to phone | Build this week? |
|---|---|---|---|---|---|---|
| A. Foil/copper zones behind poster + ESP32 touch (or MPR121 on ESP32) | ₹400–800 (generic ESP32 price unverified) + foil | Medium (firmware + WebSocket) | Good for palm; fingertip vs close palm weaker | **Yes** | Wi-Fi WebSocket ~ms–tens of ms, spikes possible | Yes |
| B. Piezo disc(s) + JFET buffer → phone mic (TRRS or USB-C TRRS adapter) | ₹50–200 | Low–medium (solder 1 JFET) | Excellent for taps (hover is silent) | No | Wired, lowest | Yes |
| C. Old Android phone taped to wall (mic onset; native accel) | ₹0 if available | Low (web page) | Good for taps | No | Wi-Fi | Yes (prior report covered) |
| D. Bare Conductive paint electrodes (Wall++-style) | ₹1,705 per 10 ml → impractical area | High | Good (Wall++ shows touch + 10 cm hover) | Yes | — | Small zones only |
| E. VL53L1X/L0X along wall edge | ₹290–900 each + MCU | Medium | **Poor** (27° cone; can't resolve mm from wall) | n/a | Wi-Fi | Yes, but only as an X-position aid |
| F. IR break-beam curtain | Unpriced; many pairs | High | Plane 5–15 mm off uneven wall = hover counts | Yes (in plane) | Wi-Fi | Sparse version only |
| Ref: laser plane + camera (prior report) | ~₹32 | Low | Plane-based | Yes | — | Needs camera (excluded) |

- **Recommended fusion tier [estimate]:** sonar (position + approach) AND (capacitive zone active OR piezo onset) → touch. Capacitive gives hold/release, piezo gives sharp tap timing. If only one helper is affordable: capacitive zones, because they're the only option that handles the silent-contact case.

### Gaps
- Generic ESP32 DevKit (38-pin WROOM) price on Robu/Robocraze/Amazon.in, piezo disc price, JFET (J201/2N5457) availability, and USB stereo audio adapter prices were not captured in this search. The report writer should flag the rupee totals for A/B as estimates.
- No end-to-end measured reliability numbers exist for any of these options in a dark classroom. All reliability rankings are reasoned estimates.
