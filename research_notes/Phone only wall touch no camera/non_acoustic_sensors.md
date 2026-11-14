# Non-acoustic phone sensors for wall-touch detection from 2.5–4 m (no camera)

Setup assumed throughout: POCO M4 Pro (the brief says 4G / Helio G96) lying near a projector, 2.5–4 m from an uneven painted classroom wall, India 230 V / 50 Hz mains. Goal: detect a hand touching the wall, and ideally tell touch apart from a hand hovering 1–5 cm off the wall.

**Bottom line.** No non-acoustic sensor on the phone, used alone, can tell touch from a 1–5 cm hover at 3–4 m. The only approach with *measured* centimetre-level hover/touch results is body-as-antenna mains-hum sensing (RMS error 4.1 cm on hand-to-wall distance; 98% touch vs no-touch). It needs an electrode **in contact with the person's skin**, and a high-impedance (around 10 MΩ) input sampled at 250–400 kS/s. A phone near the projector, with or without a long wire in its mic jack, does not meet those conditions. Wi-Fi/BLE/RTT are metre-scale or need root firmware the MediaTek chip doesn't have. The ambient-light sensor can at best see "something is in the beam". Geometry stops it from telling touch from hover when it sits beside the projector.

---

## 1. Electric-field / mains-hum sensing (Humantenna family, Wall++, EPIC)

### Takeaway
This is the one non-acoustic method with measured hover-vs-touch resolution of a few cm. But every result comes from a sensor **galvanically connected to the user's body**: a skin pad on the neck or forearm, a 10 MΩ bias, and a DAQ sampling at hundreds of kS/s. A phone 3 m away cannot reproduce that, even with a wire antenna in the 3.5 mm jack. Wall++ gets touch by painting the wall itself as the electrode, which is a helper approach, not phone-only.

### Cited Findings
**"Your Noise is My Command" (Cohn, Morris, Patel, Tan, CHI 2011). Measured. Taken from the paper's full text.** ([PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2011/05/chi2011_rfgestures_cohn.pdf); [MSR page](https://www.microsoft.com/en-us/research/publication/noise-command-sensing-gestures-using-body-antenna/))
- Sensing chain: a conductive pad (standard anti-static grounding strap) on the back of the participant's neck, wired to an NI USB-6216 DAQ that sampled at **400 kS/s**. The body was biased to the DAQ's isolated local ground through a **10 MΩ** resistor. The DAQ ground was isolated from the laptop ground.
- The received spectrum "is dominated by 60 Hz and its harmonics". Harmonics become negligible above about 25 kHz. Features covered DC–2 kHz at 12 Hz resolution, plus DC–200 kHz. The work was done in the US (60 Hz). The authors note that other regions use 50 Hz.
- The authors cite research saying the body's broadband antenna response captures roughly **40 Hz to 400 MHz**.
- Results (10 participants, 10 homes): room/location near **100%**; **touching the wall vs not touching: 98%**; location plus position on the wall: **80%**. Positions were the switch plate and about 20 cm above, below, left and right of a light switch.
- **Hand-to-wall proximity regression:** the hand was held 5–30 cm from a light switch. **RMS error was 4.1 cm.** The paper's words: "resolution on the order of several centimeters". Position *along* the wall, 10–60 cm from the switch, had RMS error 8.1 cm.
- Signal strength depends on closeness to the radiating wiring. The authors expected switches to beat blank walls.
- Location could even be classified "without even touching the wall" (99.5%, standing at arm's length). This is location of the person, not touch detection.
- Moving the pad from the neck to the forearm still worked ("where it might be connected to a watch"). Body contact was kept in every condition.

**Humantenna (Cohn, Morris, Patel, Tan, CHI 2012). Measured.** ([PDF](https://ubicomplab.cs.washington.edu/pdfs/humantenna.pdf); [UW page](https://ubicomplab.cs.washington.edu/publications/humantenna))
- Neck contact again (grounding strap), with a wire to an NI WLS-9206 wireless DAQ (9.5 × 18.2 × 3.7 cm) in a backpack: **250 kS/s, 16-bit**, 10 MΩ bias.
- The authors warn that the earlier laptop's size "could have created significant ground coupling to the body, which might have produced exaggerated performance over what would be expected on a small mobile device".
- Gestures could be classified using only content **below 500 Hz** (mains and harmonics), and with data down-sampled to **976.56 S/s**.
- Results: 12 whole-body gestures at **92.7% (σ 3.0%)**; 5-location classification at **99.6%**.
- Movement detection used a 40 mV inter-quartile threshold on the low-passed body-voltage waveform. This gives the scale of the signal at the body with a 10 MΩ input.

**Wall++ (Zhang, Yang, Hudson, Harrison, Sample, CHI 2018).** ([CMU news](https://www.cmu.edu/news/stories/archives/2018/april/paint-transforms-walls-into-sensors.html); [ScienceDaily](https://www.sciencedaily.com/releases/2018/04/180423135046.htm))
- The wall is painted with water-based nickel conductive paint in a diamond electrode grid, under a latex top coat. Cost is about **$20/m²**.
- Capacitive mode tracks touch and gestures, and estimates body pose when people are close.
- Passive "airborne EM" mode detects which appliances are active and where. It also locates people wearing EM-emitting devices.
- A well-grounded object near the wall (such as a TV) reduces sensitivity.
- The sensing electrode is the wall itself, wired to custom electronics. It is not remote sensing.

**Electrick (Zhang, Laput, Harrison, CHI 2017).** ([FIG Lab](https://www.figlab.com/research/2017/electrick); [Engineering.com](https://mobile.engineering.com/amp/14884.html))
- Electric-field tomography on a conductive coating (paint, film), with electrodes around the edges. Touch accuracy is about **1 cm**. Demonstrated on drywall among other surfaces. It also needs the surface painted and wired.

**Passive electric-potential sensors (EPIC, Univ. of Sussex / Plessey).**
- Heartbeat was detected at up to **40 cm** ([phys.org](https://phys.org/news197054417.html)).
- Changes in the ambient electric field caused by people were measured "at standoff distances of several metres", with no line of sight needed ([SPIE 2009](https://neurophotonics.spiedigitallibrary.org/conference-proceedings-of-spie/7313/73130P/Detecting-electric-field-disturbances-for-passive-through-wall-movement-and/10.1117/12.817919.full)).
- Plessey's commercial chip claims a range of "several metres for body movements" ([Electronic Specifier](https://www.electronicspecifier.com/products/sensors/epic-sensor-plessey-releases-new-epic-sensor-for-movement-sensing-applications)).
- These are vendor and press claims, not peer-reviewed numbers. They describe gross body *movement or presence*, not cm-scale limb position.

**Phone audio input low-frequency response.**
- The Android CDD §5.11 (Capture for Unprocessed, which applies only if the device declares UNPROCESSED support) says response SHOULD be flat **±10 dB from 100 Hz to 7 kHz**, and within **±20 dB from 5 Hz to 100 Hz** relative to mid-band ([CDD 9.0 §5.11](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-9.0.0_r9/5_multimedia/5_11_unprocessed-audio.md); [CDD 7.1.2](https://android.googlesource.com/platform/compatibility/cdd/+/refs/tags/android-7.1.2_r1/5_multimedia/5_11_unprocessed-audio.md)). So 50 Hz is *allowed* to be attenuated by up to 20 dB, but is not required to be blocked.
- Codec and mic inputs use AC coupling, with a high-pass corner set by 1/(2π·C·Z_in), typically designed for about 20 Hz. TI's example uses Z_in = 2.5 kΩ and about 3 µF ([TI SBAA583](https://www.ti.com/document-viewer/lit/html/SBAA583/GUID-F394CA0F-475E-416B-9306-43A364011BF3); [USPTO 8139790](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8139790)). Digital DC-block filters trade DC rejection against loss of low-frequency content ([Knowles](https://www.knowles.com/docs/default-source/default-document-library/dc-blocking-filter.pdf?sfvrsn=6)).
- I found **no POCO M4 Pro or Helio G96 codec datasheet** that states the actual mic-path high-pass corner.

### Inferences
- **The 50 Hz band itself probably gets through** a phone mic input, attenuated by roughly 0–20 dB, so the high-pass is *not* the main blocker. The real blockers are input impedance and geometry:
  - **Impedance mismatch (estimate).** A floating wire or body couples to mains fields through tens of pF. At 50 Hz, 100 pF has a reactance of about 32 MΩ. Humantenna used a 10 MΩ bias for this reason. A headset mic input has an impedance of about 2–3 kΩ and carries a bias resistor on the order of 2 kΩ, so it would divide the coupled signal by roughly 10⁴. That leaves millivolts at the body as microvolts at the codec, next to the codec's own noise and the bias supply. Also, Android's headset detection may not even switch to the external mic if it sees an open wire instead of a mic load.
  - **Geometry (physics argument, not measured).** In Humantenna the *person* is the antenna, and the measured quantity is the body's potential relative to a local ground. Touching or nearing a wall with wiring changes the body's capacitive coupling to the live and earthed conductors, and the pad reads that directly. A phone or wire antenna 3 m away only sees the *perturbation* that the body causes in the room's quasi-static 50 Hz field. That perturbation falls off steeply with distance (dipole-like, roughly 1/r³ in the near field). It would be swamped by people walking, fans and tube lights, and by projector and laptop SMPS noise. Whether the hand is touching or 3 cm away changes the body's own potential by an amount that was measurable *at the skin*. At 3 m it would be a second-order change on an already tiny perturbation.
  - **Wiring location.** The CHI 2011 wall results relied on wiring behind the touched area: switches beat blank walls. A classroom projection wall may have no conduit behind most of the image area.
- **Verdict.** Phone-only remote mains-hum sensing gives at most presence or gross motion near the wall. It **cannot distinguish touch from a 1–5 cm hover**, and robustness is poor (many people, many emitters).
- **Cheap-helper variant that *is* plausible.** A wrist strap or ring electrode on the presenter, wired to a high-impedance front end (op-amp buffer with a 10 MΩ bias, or an EPIC-type sensor) plus an ESP32 ADC sampling at 1–10 kS/s, sending over BLE or Wi-Fi to the phone. This reproduces the measured ~4 cm hover regression and 98% touch detection. Only the presenter is sensed, and per-room training is needed.
- **The other cheap-helper option** is Wall++/Electrick-style conductive paint or film on the wall, with an electrode driver. It gives about 1 cm touch accuracy, but it is a wall modification.
- **Latency (estimate).** Humantenna-style features use windows of tens to hundreds of ms (98 ms windows in Humantenna), so roughly 100–200 ms is achievable with a body-worn helper.

### Gaps
- No published measurement of remote (non-contact, metres away) detection of *touch vs hover* via mains-field perturbation.
- No datasheet for the POCO M4 Pro (MediaTek MT6358-class PMIC/codec) mic high-pass corner, or for whether the 3.5 mm input accepts an unloaded wire. Measuring a 50 Hz tone injected through a 1 MΩ resistor into the mic pin would settle the corner empirically.
- Humantenna was done at 60 Hz in US homes, not 50 Hz / 230 V Indian classrooms with metal conduit. No data on how conduit shielding changes the body pickup.
- EM-Sense (Laput et al., UIST 2015) did not come up in searches, so it is not summarized. My understanding is that it too uses a body-worn electrode (a smartwatch) to identify touched electrical objects. This is unverified.

---

## 2. Wi-Fi CSI / RSSI / RTT and BLE RSSI

### Takeaway
None of these reach cm-scale hand-to-wall resolution from a phone. CSI, the only Wi-Fi signal rich enough for fine sensing, is not exposed on Android. The phone-side extraction tool (Nexmon) only works on certain Broadcom chips; the POCO M4 Pro uses MediaTek. RSSI is coarse and throttled. RTT is metre-scale. Chrome exposes none of it.

### Cited Findings
- **Nexmon CSI** works on Broadcom/Cypress chips, including rooted Nexus phones, Raspberry Pi and some APs ([seemoo-lab/nexmon_csi](https://github.com/seemoo-lab/nexmon_csi)).
- **MediaTek CSI tools exist only for routers.** The MtkCSIdump project targets OpenWrt routers with MediaTek chips ([OpenWrt forum](https://forum.openwrt.org/t/csi-extraction-for-mediatek-based-wi-fi-chipsets/244703)). There is also a router toolkit for the GL-MT6000 ([arXiv 2506.16957](https://arxiv.org/html/2506.16957v1)).
- Reverse-engineering of MediaTek Wi-Fi firmware notes that the firmware files sit in `/system/etc/firmware` on many MediaTek Android phones, but there is no CSI patch for phones ([cyrozap/mediatek-wifi-re](https://github.com/cyrozap/mediatek-wifi-re)).
- **No phone-side MediaTek CSI tool was found.**
- **RSSI gesture sensing (measured).**
  - About 91% accuracy on 4 arm gestures, but only within about **1 ft (30 cm)** of the receiver, using a laptop-class receiver ([arXiv 1411.5394](https://arxiv.org/pdf/1411.5394)).
  - On unmodified smartphones, RSSI is "burstier" and weaker than on laptops. Wisture reached up to 94% on 3 hand gestures, averaging 78% ([arXiv 1707.08569](https://arxiv.org/pdf/1707.08569)).
  - Reviews call RSSI coarse and sensitive to environment ([MDPI Sensors 2021](https://mdpi.com/1424-8220/21/1/222)).
- **Android scan throttling.** Since Android 9, each foreground app may call `startScan()` **4 times per 2 minutes**. The limit can be turned off only in Developer Options ([Android dev docs](https://developer.android.com/develop/connectivity/wifi/wifi-scan)). The RSSI of the *connected* link can be polled faster, but it is a single scalar.
- **Wi-Fi RTT (802.11mc) calibration targets (AOSP).** 90th-percentile range error of **2 m at 80 MHz, 4 m at 40 MHz, 8 m at 20 MHz**. For 802.11az (Android 15+), it is 0.5 m at 160 MHz and 1 m at 80 MHz ([AOSP Wi-Fi RTT](https://source.android.com/docs/core/connect/wifi-rtt)). RTT measures the phone-to-AP distance, not the distance to a hand.
- **Chipset note.** Xiaomi lists the 4G POCO M4 Pro with a MediaTek Helio G96, and the 5G model with a Dimensity 810 ([Xiaomi SG 4G specs](https://www.mi.com/sg/product/poco-m4-pro/specs); [Xiaomi MY 5G specs](https://www.mi.com/my/product/poco-m4-pro-5g/specs/)). Both use MediaTek connectivity, so Nexmon does not apply to either.

### Inferences
- **Physics limit (estimate).** Wavelengths are about 12.5 cm at 2.4 GHz and about 6 cm at 5 GHz. A hand moving 1–5 cm next to a wall 3 m from the phone changes the multipath only slightly, and only if the hand sits inside the Fresnel zone of some TX–RX link. The phone near the projector has no partner transmitter on the far side, so there is no link crossing the hand. Even full CSI research systems recognize gestures; they do not measure absolute hand-to-wall gaps at 3 m. Touch vs 1–5 cm hover: **not distinguishable.**
- **BLE RSSI** has the same physics and is coarser: 1 MHz channels, around 1–10 Hz advertisement rate, and multipath fading of several dB. It is useless for this.
- **Browser access.** No Wi-Fi RSSI, CSI or RTT from Chrome at all. Web Bluetooth gives no general RSSI scanning in stable Chrome (from my knowledge; the scanning API has stayed behind a flag). Native app only.
- **Classroom robustness.** Many students with phones means constant RF churn. This is poor.

### Gaps
- I did not confirm whether the POCO M4 Pro declares `FEATURE_WIFI_RTT`. Checking `adb shell pm list features` would settle it.
- No published phone-to-wall hand-gap Wi-Fi results exist to cite. The "not distinguishable" verdict is an inference.

---

## 3. Optical, non-camera: ambient light sensor (ALS), IR blaster, flashlight

### Takeaway
The ALS can probably detect a large body or hand shadow, or projector-content changes, as a lux dip. But it is a single wide-angle pixel, and projector content changes dominate its readings. With the phone beside the projector, the hand and its own shadow line up almost exactly from the sensor's viewpoint, so touch vs hover is **geometrically indistinguishable**. The IR blaster can only transmit, Android has no IR receive API, and its reflection from 3 m would be far below the ALS/proximity floor. Chrome's AmbientLightSensor is behind a flag.

### Cited Findings
- **ConsumerIrManager is transmit-only.** It offers `hasIrEmitter()`, `getCarrierFrequencies()` and `transmit(carrierFrequency, pattern)`, and only patterns shorter than 2 s are sent. There is no receive method ([Android reference](https://developer.android.com/reference/android/hardware/ConsumerIrManager)).
- **Android light and proximity sensors are "On-change" reporting sensors.** Some proximity sensors are binary near/far only ([AOSP sensor types](https://source.android.com/docs/core/interaction/sensors/sensor-types)). A requested rate is only a hint. Rates above 200 Hz need `HIGH_SAMPLING_RATE_SENSORS` ([SensorManager](https://developer.android.com/reference/android/hardware/SensorManager)).
- **Chrome.** AmbientLightSensor sits behind `chrome://flags/#enable-generic-sensor-extra-classes`. Motion sensors have been on by default since Chrome 67 ([W3C DAS log 2021](https://lists.w3.org/Archives/Public/public-device-apis-log/2021Sep/0033.html); [chromestatus 5298357018820608](https://www.chromestatus.com/feature/5298357018820608)). I could not confirm the status as of 2026.
- **Prior art on ALS gestures.** Patents describe using the phone's passive ALS to detect hand motion as "dips" in the light signal, close to the sensor ([US 9256290](https://patents.google.com/patent/US9256290); [US 8766162](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8766162)). GestureLite used a 3×3 photodiode array to read shadow patterns ([Dartmouth thesis](https://core.ac.uk/download/337600943.pdf)). All are near-field (cm), not 3 m.
- **POCO M4 Pro (4G) sensors:** proximity, ambient light, gyroscope, accelerometer, electronic compass, IR blaster ([Xiaomi SG specs](https://www.mi.com/sg/product/poco-m4-pro/specs)). GSMArena lists "virtual proximity" ([GSMArena](https://m.gsmarena.com/xiaomi_poco_m4_pro-11388.php)). The sources conflict on whether the proximity sensor is a hardware IR part or the software/ultrasonic one.

### Inferences
- **The ALS as a projector-shadow detector is plausible for "something entered the beam". The numbers below are my estimates.**
  - The ALS sits on the phone's front face, with roughly a 90–120° cosine field of view. Pointed at the screen, it integrates light from the whole wall plus room light.
  - A hand blocks about 0.01–0.02 m² of a roughly 2–3 m² image, which is a **0.5–1% drop** in reflected light. A body is several times more.
  - ALS lux resolution is usually fine enough to see 1% changes. But projector content changes (slides, video) swing the reflected light by tens of %. Detection is only reliable on a static or known frame, or by dividing out the expected brightness, which needs the frame being shown. The phone could know the frame if it drives the projector.
  - On-change reporting plus sensor integration times (typically about 50–100 ms) give roughly **100–200 ms latency**. This is an estimate; no POCO-specific rate was found.
- **Touch vs hover is impossible with this geometry.** The hand's shadow on the wall is offset from the hand by about baseline × gap / distance, as seen from a point displaced by "baseline" from the projector lens. With the phone 0.2–0.3 m from the lens, a 3 cm gap and a 3 m throw, the offset is about **2–3 mm**. A single-pixel sensor cannot resolve that at all; even a camera would struggle. Shadow-based touch detection in research uses a camera with a large lateral offset from the light source, for example Wilson's PlayAnywhere (UIST 2005). That is from my own knowledge, not fetched this session.
  - **Cheap helper:** a second light source far to the side (a lamp or second projector), plus several photodiodes at known positions, could time-gate shadow merging. This is camera-like in practice.
- **Flashlight plus ALS** (phone as emitter and receiver): a phone LED gives about 50–100 lm, spread wide. At 3 m the illuminance on a hand is about 1–10 lux, less than projector and room light. The hand's reflection back to the phone falls off again with distance squared. Undetectable against a 3000 lm projector.
- **IR blaster plus ALS/proximity:** the blaster is a low-power 940 nm LED that sends roughly 38 kHz carrier bursts. Android exposes only fused lux from the ALS (any IR channel is used internally for compensation), and on-change reporting at about 10 Hz cannot demodulate 38 kHz. **Not viable.**
- **Classroom robustness:** sunlight, tube-light flicker (100 Hz) and people walking through the beam all produce false dips.

### Gaps
- No measured ALS response for the POCO M4 Pro: min delay, resolution, field of view. `adb shell dumpsys sensorservice` would list the vendor part and min delay.
- No study of a single ALS detecting a hand shadow at 3 m in projector light.

---

## 4. Magnetometer, barometer, proximity, accelerometer/gyroscope

### Takeaway
None of these sensors sense a person 3 m away in any touch-relevant way. The POCO M4 Pro has **no barometer**. The proximity sensor works within a few cm, and some models use Elliptic Labs' ultrasound-based "virtual" proximity. Accelerometer pickup of a wall tap through the building structure is a long shot, and it cannot tell a gentle palm touch from a hover.

### Cited Findings
- **Barometer:** Xiaomi spec pages for the 4G and 5G POCO M4 Pro list no barometer ([Xiaomi SG](https://www.mi.com/sg/product/poco-m4-pro/specs); [Xiaomi MY 5G](https://www.mi.com/my/product/poco-m4-pro-5g/specs/)).
- **Proximity:**
  - Elliptic Labs' "AI Virtual Proximity Sensor" is software-only, using ultrasound and sensor fusion with no dedicated hardware. It shipped on the Redmi Note 11 / 11S / 11 Pro family, the POCO M4 Pro 4G's sibling platform, and is used to blank the screen at the user's ear ([Business Wire, Oct 2021](https://www.businesswire.com/news/home/20211028006335/en); [Elliptic Labs](https://ellipticlabs.com/mfn_news/elliptic-labs-ai-virtual-proximity-sensor-launches-on-four-xiaomi-redmi-smartphone-models-for-global-markets/)).
  - GSMArena lists "virtual proximity" for the POCO M4 Pro ([GSMArena](https://m.gsmarena.com/xiaomi_poco_m4_pro-11388.php)).
  - Android proximity sensors are on-change, and some are binary near/far ([AOSP](https://source.android.com/docs/core/interaction/sensors/sensor-types)).
- **Magnetometer and accelerometer:** listed as "electronic compass", gyroscope and accelerometer ([Xiaomi SG](https://www.mi.com/sg/product/poco-m4-pro/specs)). Above 200 Hz requires the high-sampling-rate permission ([SensorManager](https://developer.android.com/reference/android/hardware/SensorManager)). Motion sensors are available in Chrome by default ([W3C log](https://lists.w3.org/Archives/Public/public-device-apis-log/2021Sep/0033.html)).

### Inferences
- **Magnetometer.** The human body is effectively non-magnetic. A watch or keys 3 m away cause sub-nT changes, below the noise of typical phone magnetometers (roughly 0.1–1 µT class, from general knowledge). Mains 50 Hz magnetic fields come from current loops, not from a person touching a wall. **Not viable.**
- **Proximity (virtual or IR).** Range is a few cm in front of the phone, so it is irrelevant at 3 m. The ultrasonic version is acoustic and outside this note's scope anyway.
- **Accelerometer/gyroscope.** A firm wall *tap* sends a vibration into the wall and floor. If the phone sits on a table connected to the same floor, a large tap might rise above the accelerometer noise floor; this is not measured. A soft touch or press almost certainly won't. Hover makes no vibration, so this method could only detect forceful taps, never position or hover. It is also arguably structure-borne vibration, close to the acoustic family. Footsteps and desks cause false triggers.
- **Barometer:** absent. Even if present, a hand touching a wall causes no pressure change at 3 m.

### Gaps
- No measured data on phone accelerometer detection of wall taps at 3 m through a floor and table. This would need a quick on-device test.
- The proximity hardware on this specific unit (IR versus virtual) is unconfirmed. Check `dumpsys sensorservice`.

---

## Ranking summary for the report writer

The "(measured)" and "(estimate)" tags mark whether the claim rests on published results or on my reasoning.

| Method | Phone-only at 3–4 m? | Touch vs 1–5 cm hover? | Latency | Classroom robustness | Access |
|---|---|---|---|---|---|
| Body-as-antenna mains hum, skin electrode | No: needs a body-worn electrode and a high-Z front end (measured setups) | **Yes**: 4.1 cm RMS hover regression, 98% touch/no-touch (measured, CHI 2011) | ~100–200 ms (estimate) | Medium (per-room training; only the presenter) | Helper (ESP32 + strap) to phone over BLE or Wi-Fi |
| Remote mains-field perturbation (wire in mic jack) | Technically connectable; signal likely ~10⁴× attenuated by the low-Z mic input (estimate) | No (estimate) | — | Poor | Native (AudioRecord UNPROCESSED); Chrome getUserMedia possible but processed |
| Conductive-paint wall (Wall++/Electrick) | No (wall modification) | Touch ~1 cm (measured, Electrick) | Low | Good | Helper hardware |
| Wi-Fi CSI | No (no MediaTek phone CSI) | No | — | Poor | None |
| Wi-Fi RSSI / BLE RSSI | Yes, but throttled and coarse | No (works within ~30 cm of receiver in literature) | Seconds (scan limits) | Poor | Native only |
| Wi-Fi RTT | Possibly unsupported | No (2 m at 90th percentile) | — | — | Native only |
| ALS shadow detection | Yes | No: geometric offset ~2–3 mm (estimate) | ~100–200 ms (estimate) | Poor (content and room-light changes) | Native; Chrome only behind a flag |
| Flashlight / IR blaster + ALS | No (signal far too weak; IR is transmit-only) | No | — | — | — |
| Accelerometer (wall tap via floor) | Maybe, for hard taps only (unverified) | No | ~10–50 ms | Poor | Native; Chrome DeviceMotion |
| Magnetometer / barometer / proximity | No (barometer absent; proximity range is cm) | No | — | — | — |
