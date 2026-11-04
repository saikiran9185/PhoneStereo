# Laser light plane ("light curtain") touch and IR pen / Wiimote whiteboards for projected walls

Scope: how a laser sheet just above a wall plus a camera turns a projected wall into a touch surface; commercial units (Epson FTU, BenQ PointWrite, Optoma TouchBeam, Mimio/Boxlight, ViewSonic, Indian kits); DIY (NUI Group LLP, CCV/tbeta/Touchlib/TUIO); and IR pen + Wiimote whiteboards as the cheap alternative. Researched 2026-11-04. Several primary PDFs were extracted in full (NUI Group handbook, Epson FTU addendum); many retail and spec figures come from search-result snippets and are marked as such.

## 1. How it works, and the geometry: plane height, fan angle, number of lasers, power, eye safety

### Takeaway
An IR line laser (or several) fans a sheet of light about 1 mm thick, sitting a few mm off the surface. A fingertip that breaks the sheet scatters light, and an IR-filtered camera sees that as a bright blob. Commercial units use one 180° fan from a module mounted about 25 mm above the image. The curtain sits about 2–8 mm off the board, and the lasers are Class 1 at 830–940 nm. DIY builds used 2–4 corner lasers (5–25 mW, 120° line lenses) to cut down occlusion. Those DIY powers are well above Class 1, so the main safety risk of a homemade build is the user's eyes.

### Cited Findings
**Principle**
- "Infrared light from a laser(s) is shined just above the surface. The laser plane of light is about 1mm thick and is positioned right above the surface, when the finger just touches it, it will hit the tip of the finger which will register as a IR blob." The handbook says LLP gives "amazing (very high contrast) blobs with very little effort." — [NUI Group, Multi-Touch Technologies handbook, 1st ed. 2009, §1.5 and Appendix B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- Epson's FTU ("Light Curtain Unit") "employs infrared-emitting laser technology to overlay an 'Infrared laser light curtain' over the surface of the whiteboard. Any contact on the board results in this beam being interrupted… detected by the infrared camera on the projector", and the projector maps it to display coordinates. — [Epson FTU installation manual addendum rev C](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)

**Plane height and mounting gap (commercial)**
- Epson: once calibrated, "the laser curtain should be approx. 8mm from the surface of the board at the top, and approx. 2mm from the surface at the bottom." So the sheet is deliberately tilted slightly toward the board. — [Epson FTU addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)
- Epson recommends mounting the FTU "approximately 25mm above the display area… can be mounted at a distance of up to 100mm above the display area. However, 25mm would provide optimal coverage." The FTU must sit "on the same virtual plane as the surface of the whiteboard", and a bezel deeper than 3 mm means "operation of the FTU cannot be guaranteed." — [Epson FTU addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)
- Epson EB-725Wi: if the board frame stands more than 3 mm (0.1 in) proud of the surface, the touch unit must be mounted on the board itself. On the EB-1470Ui/1460Ui the external bracket is used when the wall-to-surface gap is 20–50 mm. — search snippets of [Epson EB-725Wi install guide](https://www.manualowl.com/m/Epson/BrightLink-EB-725Wi/Manual/623416?page=84), [EB-1470Ui install guide](https://manualshive.com/en/manual/epson/eb-1470ui/110182?page=72)
- Optoma TouchBeam (ZW300USTI): Class 1 laser; distance spec 20–100 mm for 75–115" screens and 40–100 mm for 120–140" screens. The snippet does not make clear whether this is the curtain-unit offset or the gap to the screen. — search snippet of [Optoma ZW300USTI manual p.39](https://www.manualowl.com/m/Optoma/ZW300USTI/Manual/519979?page=39)
- Dell S560T touch unit: 20–100 mm from the bottom of the unit to the screen. — search snippet of [Dell S560T User's Guide](https://dl.dell.com/manuals/all-products/esuprt_display_projector/esuprt_projector/dell-s560t-projector_User's%20Guide2_en-us.pdf)

**Fan angle and number of lasers**
- BenQ PT02 (PointWrite finger-touch curtain): 850 nm, "180° fan from one laser beam", maximum line thickness 3 mm, maximum angle error 4°, Class 1, USB or 5 V powered, maximum 180 mA (sales sheet) vs 250 mA at 4.7–5.2 V (user manual). The two sources disagree on current. — search snippets of [BenQ PointWrite sales sheet Jan 2015](https://www.home-cinema.bg/uploads/user/Benq/SalesSheet_PointWriteFlyer_Jan2015v4.pdf) and [BenQ PT02 manual](https://manualmachine.com/benq/pt02/17718064-user-manual). The PDF itself returned 403, so these figures are unverified beyond the snippet.
- ViewSonic PJ-vTouch-10S: 10-point module with "180 degree" laser curtain. — [ViewSonic PJ-vTouch-10S](https://www.viewsonic.com/sg/products/projectors/PJ-vTouch-10S); [digitec listing](https://digitec.ch/en/s1/product/viewsonic-pj-vtouch-10s-10-point-interactive-module-180-degree-wide-angle-response-various-projector-10345663)
- DIY LLP: "Most setups go with 2-4 lasers, positioned on the corners… The 120 degree line lens is most commonly used, so as to reduce the number of lasers." The handbook recommends "between two and four depending on the size… The ideal setup has a light plane generated from a laser in each of the four corners. … it is always important to have more rather than higher quality ones." — [NUI Group handbook](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- One hobbyist LLP build used 780 nm diodes with 89° line lenses. — [duino4projects](https://duino4projects.com/?p=6651)
- Line generators are Maddox-rod/cylindrical "line lenses" in DIY, or Powell lenses. Powell lenses give a more uniform line than cylinder lenses; the beam should enter centred and perpendicular, and the fan angle is chosen to suit the line length. — [NUI handbook](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf); [Syron Optics, How to use a Powell lens](https://syronoptics.com/blogs/technical-article/how-to-use-a-powell-lens)

**Power and wavelength**
- DIY: "Common powers are 5mW, 10mW, and 25mW. Anything above 25 … is way overkill… 5mW and 10mW lasers work great as a starting point… For any screen over 20 inches (~51 cm) diagonal, 10mW should be preferred." Wavelength "between 780 and 900 should work fine"; 780 nm was the cheapest; "no real reason" to prefer 850. — [NUI handbook App. B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- Power supply: "Assume (very roughly) 500mA for each laser"; four lasers need about 2 A. Lasers are 3.3 V or 5 V, wired in parallel; "running a 3.3V laser at 5V is almost guaranteed to fry it." — [NUI handbook App. B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- Commercial wavelengths: Epson FTU 940 nm Class 1 ([Epson addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)); BenQ PT02 850 nm Class 1 (snippet, [BenQ sheet](https://www.home-cinema.bg/uploads/user/Benq/SalesSheet_PointWriteFlyer_Jan2015v4.pdf)); Mimio/Boxlight 825 nm (snippet, [Mimio 320LT sheet](https://manualmachine.com/mimio/320lt/5962807-product-sheet)); a ten-touch UST projector's curtain at 830 nm (snippet, [Sargent Welch listing](https://www.sargentwelch.com/store/product/11999572/this-ten-touch-interactive-ultra-short-throw-projector-features-ir-camera-and-laser-curtain-modules-laser-curtain-ir-laser-830-nm-20-000-hours-lifet)).
- Off-the-shelf 850 nm line modules are often Class 3R, even at 5 mW (e.g. Picotronic LC850-5-3-F, 5 mW Class 3R; 850 nm/100°/80 mW Class 3R). — [Picotronic](https://picotronic.de/picopage/en/product/detail/id/356820) (snippet); [Picotronic 70155488](https://shop.picotronic.de/en/p/70155488)

**Eye safety**
- "Infrared lasers… are imperceptible to the human eye and therefore activate no blink response, allowing for greater damage." The handbook urges goggles matched to the wavelength during setup, a non-reflective "fence" a few mm high (wide masking tape) around the touch area to stop stray IR, and aligning first with low-power visible red lasers, which "optically behave similarly", plus IR detection cards. It also warns that curved glass, such as a wine-glass base, sprays laser light around. — [NUI handbook §1.5.1 and App. B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- Epson's FTU is "a 'Class 1' laser emitting device… 940nm." — [Epson addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)

### Inferences
- For a 1.5–2 m wide painted wall, the commercial layout is one 180° fan centred above the image (Epson about 25 mm above the top edge). A DIY version is two 120° modules in the top corners, or one 120° module mounted centrally 0.4–0.6 m above the image so the fan covers the width. Two corner lasers also give the second viewing direction that fixes most two-finger occlusion.
- An ordinary painted wall is not flat to the ±2–3 mm that commercial units want (Mimio quotes "non-planarity under 3 mm"). With a 1 mm sheet, the sheet height has to be set larger, about 3–8 mm, which brings hover false-triggers. Expect to shim the laser mount and keep the sheet tilted slightly toward the wall, as Epson does (8 mm at the top, 2 mm at the bottom).
- Because a visible-red 650 nm 5 mW line module is about ₹32 and sits at the Class 2/3R boundary with a blink reflex, it is the safest prototyping path. It is only viable if the camera can tell the red scatter apart from projected red content (see section 2).

### Gaps
- I found no official laser class or mW for DIY 850/940 nm line modules sold in India. Most Chinese 850 nm 25–100 mW line modules have no stated class.
- No measured figure for how flat a wall must be beyond Epson's "same plane", 3 mm bezel limit and Mimio's 3 mm non-planarity (snippet).

## 2. Camera requirements: IR-pass filter, IR-cut removal, frame rate, phone/MacBook cameras, visible red laser, background subtraction

### Takeaway
You need a camera that can see the laser wavelength and reject the projector's visible light. The usual combination is an IR-cut-removed (NoIR) sensor plus an IR band-pass filter matched to the laser. 850 nm is easy for ordinary CMOS sensors. 940 nm is roughly several times dimmer to them, but it is invisible to people and better against tungsten lamps. Phones and laptop webcams keep their IR-cut filters, so they can see bright 850/940 nm point sources (remote LEDs) but probably not the faint scatter off a finger. A modified PS3 Eye (up to 187 fps at 320×240) or a NoIR module was the DIY standard.

### Cited Findings
- DIY materials list: "Camera with IR Bandpass Filter to match lasers"; goggles and band-pass "must match your lasers, or they will be useless." — [NUI handbook App. B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- PS3 Eye: 187 fps only at 320×240. People remove the IR-blocking glass by carving around the lens; difficulty varies by revision. Some later flat-lens units have weak IR filters and track IR LEDs unmodified with a 720/850 nm pass filter. Exposed floppy-disk film works as a crude visible-block/IR-pass filter, but a real 850 nm band-pass is preferred (about $15; one buyer's "850 nm" filter arrived labelled 725 nm). Removing the filter can upset focus. — search summaries of [philipzucker.com](https://www.philipzucker.com/cartpole-camera-system-opencv-ps-eye-ir/), [IL-2 forum](https://forum.il2sturmovik.com/topic/43300-any-tips-on-removing-ir-filter-on-ps3-camera), [Falcon BMS forum](https://forum.falcon-bms.com/post/254984) (forum-grade, anecdotal)
- "850nm is near IR so many webcams are able to see that wavelength"; the usual approach is removing the IR-blocking filter, and purpose-made IR cameras "tend to be very expensive." — [Hackaday.io discussion](https://hackaday.io/page/6837)
- 940 nm vs 850 nm on phones: a Galaxy Note8 "can see 940nm. It just won't be nearly as visibly bright to the camera as 850nm." Forum users say sensors detect 940 nm "just don't do that as well" and need "much higher levels". — [Syron Optics, 850 vs 940](https://syronoptics.com/blogs/technical-article/what-is-better-850nm-or-940nm); [ipcamtalk](https://www.ipcamtalk.com/threads/ir-wavelength-question.66325) (vendor blog plus forum, not controlled tests)
- Phone IR visibility varies by lens and by camera app (stock vs Open Camera) because of the processing pipeline; IR remotes are pulsed, so exposure timing matters. — [geekchamp guide](https://geekchamp.com/?p=378465) (low-authority guide)
- Ambient IR: Epson reports a case where "the laser curtain is completely inactive on the part of the board exposed to direct sunlight… closing the blinds rectified this issue." — [Epson addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)
- BenQ quotes touch areas of 65–100" "without external lighting interference" (PT20 manual, not PT02). — search snippet of [BenQ PT20 manual](https://manualshive.com/en/manual/benq/pointwrite-pt20/2734941?page=22)
- CCV includes filter stages (background removal, smoothing, highpass, amplify, threshold), so "it'll work with all optical setups (FTIR, DI, LLP, DSI)". — search summary of [CCV on Softpedia](https://mac.softpedia.com/get/Video/CCV.shtml); [ccv15 mirror](https://github.com/Nebopolis/ccv15)

### Inferences
- **POCO M4 Pro.** The rear cameras certainly have IR-cut filters, so 850 nm scatter from a 5–25 mW sheet hitting a finger will be faint. Test before buying anything: point a TV remote at the phone in a manual-exposure app. If the remote LED shows as a strong blob at short exposure, an 850 nm sheet might be visible at 1–2 m. Phone-to-Mac streaming adds latency on top of that.
- **MacBook built-in webcam.** It has an IR-cut filter and cannot take an external filter or be opened. Treat it as unsuitable for IR LLP. A visible 650 nm red sheet might be seen, but projected red content and skin under the projector light will also be bright. Workarounds:
  - Pulse the laser on alternate frames and subtract (needs a microcontroller synced to the camera, or a high-fps camera).
  - Put a narrow 650 nm band-pass filter on an external camera.
  - Keep the touch region's projected image dark or non-red.
- **Background subtraction.** A static background frame taken with the laser on and no hand catches fixed reflections such as a skirting board or pen tray. Epson's "secondary curtain" ghosting from pen trays shows why fixed reflections need masking. Threshold, then connected components or blob detection, then a homography from camera to projector space using a 4- or 9-point calibration.
- **Frame rate.** Commercial units run at 60 fps (BenQ PT20 snippet in section 3). 30 fps webcams work but add about 33 ms per frame of latency. A PS3 Eye at 60–75 fps at 640×480 is the classic cheap upgrade; it is discontinued but sold second-hand.

### Gaps
- No controlled measurement of POCO M4 Pro or MacBook webcam response at 850/940 nm. This needs a hands-on test.
- No source found for the 650 nm visible-laser-plane variant being used successfully against a live projected image. Treat it as unproven.

## 3. Accuracy (mm), latency (ms), multi-touch

### Takeaway
Commercial laser-curtain units claim 10-point multi-touch at about 60 fps with about 16 ms response (BenQ PT20). DIY LLP gives clean blobs but suffers occlusion with one laser, so you need 2–4 lasers for reliable two-finger use. Wiimote IR-pen boards track up to 4 IR points and are badly affected by sunlight. No source gave rigorous mm accuracy for any of these. Expect the camera-pixel footprint plus the hover offset (a few mm) to set accuracy.

### Cited Findings
- BenQ PT20: up to 10 touch points (pen/finger), 0.016 s response time, 60 frames/sec. — search summary of [BenQ PT20 quick start / manual](https://res.cloudinary.com/iwh/image/upload/q_auto,g_center/assets/1/26/BenQ_PT20_Quick_Start_Guide.pdf) (snippet-level)
- Mimio/Boxlight laser curtain: 825 nm, 10 touch points, auto/manual calibration, whiteboard non-planarity under 3 mm. — search summary of [Mimio 320LT product sheet](https://manualmachine.com/mimio/320lt/5962807-product-sheet)
- ViewSonic PJ-vTouch-10S: 10-point touch. Vivitek's finger-touch module supports 10-finger recognition with multiple people writing at once. — [ViewSonic](https://www.viewsonic.com/sg/products/projectors/PJ-vTouch-10S); [Vivitek DT02](https://www.vivitekusa.com/productdetail/DT02)
- Optoma's PCT-101 (30 points) is a capacitive overlay, not a laser curtain. I found no Optoma laser-curtain latency spec. — [Optoma PCT101 datasheet](https://www.fullcompass.com/common/files/40717-PCT101Datasheet.pdf)
- Occlusion in LLP with a single laser: "the touch closest to the laser would cause total occlusion… Finger one would show up as a brilliant blob, however Finger two would not show up at all." "Most people use four lasers… protection from all but the weirdest and rarest occlusions." With two or more lasers occlusion is rarely a problem, "unless your setup is very large with more than one person using it." — [NUI handbook App. B](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- LLP disadvantages: cannot track objects or fiducials; "not truly pressure sensitive"; occlusion with 1–2 lasers. — [NUI handbook comparison table](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- Ghost touches: a pen tray or any obstacle "will cause the laser to reflect and create a 'secondary' curtain… two contacts on the board when there should be one". Two adjacent FTUs with overlapping curtains caused duplicated annotations. The fix is IR deflector strips or barriers. — [Epson addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)
- False positives during calibration: the curtain can bounce off the board before hitting the marker, so a misaligned curtain that touches the board registers no touches. — [Epson addendum](https://www.epson.co.nz/d/pub/epson/techtips/installation%20manual%20addendum%20-%20rev_c.pdf)
- Wiimote: tracks up to 4 IR sources. A related two-Wiimote motion-capture study (WiiMoCap, not a whiteboard) measured mean acquisition latency of 49.6 ms (SD 13.6 ms) and accuracy around 1–5 mm. — search summary citing [SciTePress 2009 paper](https://www.scitepress.org/Papers/2009/18066/18066.pdf)

### Inferences
- **DIY accuracy estimate.** A 640×480 camera viewing a 2 m wide wall gives about 3 mm per pixel. Blob centroids reach sub-pixel precision, but the finger contact point sits a few mm off the blob (the sheet is 2–8 mm off the wall, and the camera sees the lit fingertip side). Plan for about 5–10 mm effective accuracy after homography calibration. This is not a sourced number.
- **DIY latency estimate.** Camera exposure plus transfer (16–33 ms) plus processing (1–5 ms with OpenCV) plus projector input lag (often 30–60 ms) gives about 50–100 ms end-to-end. Again, an estimate.

### Gaps
- No published mm-accuracy spec for the Epson FTU, BenQ PT02 or PW02. The PW02 spec page lists only OS compatibility (Windows/Mac) and compatible projectors. — [BenQ PW02 specs](https://www.benq.eu/en-eu/business/projector/pointwrite-pw02/specifications.html)
- No rigorous HCI paper with measured LLP accuracy or latency was retrieved. One student-grade paper repeats the "about 1 mm thick" figure. — [IMEKO TC4 2008](https://imeko.org/publications/tc4-2008/IMEKO-TC4-2008-163.pdf); [arXiv 1301.1551 processing pipeline for optical multi-touch](https://arxiv.org/pdf/1301.1551) (not read in full)

## 4. Parts and prices in India (INR)

### Takeaway
Visible 650 nm line modules are nearly free on Robu (about ₹32). 850/940 nm line modules with 90–120° fans are hard to find from Indian sellers; most stock is eBay/AliExpress (about €20–33 for 25–60 mW, 120°) or industrial Picotronic/Quarton at ₹5,000 or more. NoIR cameras are easy (Arducam/RPi NoIR CSI about ₹1,600–1,700), but no-IR-filter USB cameras need importing. Commercial finger-touch kits in India cost ₹23,000–27,000 (generic boards/kits). Interactive projectors with built-in touch run from about ₹53k (BenQ, with a PointWrite kit as an add-on) to about ₹2 lakh (Epson EB-725Wi).

### Cited Findings
| Item | Price | Source |
|---|---|---|
| 650 nm 5 V red straight-line laser module (3–5 V, line about 135 mm) | ₹32 incl. GST (staging page; check live) | [Robu stg listing](https://stg.robu.in/?p=44163); [Robu laser category](https://robu.in/product-category/laser-module/) |
| 650 nm 5 mW 6 mm red module (Robodo) | ₹32 | [robodo.in](https://robodo.in/products/laser-sensor-module-650nm-6mm-5v-5mw-red-laser); [Flipkart](https://www.flipkart.com/robodo-laser-module-650nm-6mm-5v-5mw-red-diode-pcb/p/itmeps9b89bgqxyq) |
| 850 nm 25 mW 120° IR line module, 12×40 mm (eBay.de) | about US$23.4 / €20 (about ₹2,000) plus shipping/customs | [eBay](https://www.ebay.de/itm/252682826093) |
| 850 nm 60 mW 120° IR line module, 12×45 mm (eBay.de) | about US$37.7 / €32.6 | [eBay](https://www.ebay.de/itm/326728909929) |
| Line-generator lens set, 30/45/60/90/120°, 200–1100 nm (5 pcs, eBay.de) | price not captured | [eBay](https://www.ebay.de/itm/363141644263) |
| Quarton VLM-850-01-LPA 850 nm laser (Digikey India) | ₹5,278 (out of stock) | [Digikey.in](https://www.digikey.in/en/products/detail/quarton-inc/VLM-850-01-LPA/8636991) |
| 850 nm 1–5 mW dot laser (import) | ₹5,900 | [cart2india](https://cart2india.com/diode-lasers/d6mm-850nm-1mw-5mw-ir-dot-laser-module-mini-size-infrared-laser-diode-industrial-grade-apc-driver-laser-head/00000000004392758859) |
| Arducam 8MP IMX219 NoIR V2 (CSI, Pi) | ₹1,609 incl. GST | [Robu](https://st.robu.in/product/arducam-8mp-imx219-noir-v2-night-vision-camera-module-for-raspberry-pi) |
| Raspberry Pi NoIR Camera V2 | ₹1,699 (MRP ₹3,499) | [Robocraze](https://robocraze.com/products/raspberry-pi-noir-camera-module-v2-8mp-1080p30) |
| Arducam OV5647 NoIR M12 / CS-mount (B0035/B0036) | price not shown | [The Engineer Store](https://www.theengineerstore.in/products/arducam-b0036-noir-camera-for-raspberry-pi-interchangeable-cs-mount-lens-ls-2717cs-ov5647-5mp-1080p) |
| 3 W IR illuminator board for Pi cam | ₹168 (out of stock) | [Robu](https://stgaws.robu.in/?p=52043) |
| MidOpt BP850 C-mount band-pass (machine-vision grade, far over budget) | ₹37,500 | [cart2india](https://cart2india.com/infrared-filters/midopt-nearir-bandpass-filter-for-machine-vision-bp-series-broad-bandwidth-range-820910nm-bp850254-cmount-fits-cmount-cameras/00000000004399368515) |
| Generic "Interactive Finger Touch Board" (IndiaMART) | ₹23,000/piece | [IndiaMART Ambica Techno](https://m.indiamart.com/ambicatechnosolution/interactive-whiteboard.html) |
| Willswell finger-touch interactive board kit (47" area) | ₹27,000/piece | [IndiaMART Willswell](https://m.indiamart.com/willswelltechnologies/interactive-board-kit.html) |
| BenQ MX808STH short-throw (PointWrite-kit compatible) | ₹53,000–55,000 incl. GST | [AVShack](https://avshack.in/products/benq-mx808sth-xga-interactive-projector-with-short-throw) |
| ViewSonic PS750W with finger-touch module | ₹1,59,000–1,65,000 | [AVShack](https://avshack.in/products/viewsonic-ps750w-3-300-lumens-wxga-education-projector) |
| Epson EB-725Wi (laser UST, finger touch + pens) | ₹1,97,999 | [AVShack](https://avshack.in/products/epson-eb-725wi-4000-lumens-full-hd-1080p-3lcd-wifi-enabled-interactive-ultra-short-throw-laser-projector) |

- The Epson ELPFT01 Finger Touch Unit (V12H007A23/A24) is sold abroad as an accessory for EB-1480Fi/1485Fi/735Fi/725Wi. It only works with those Epson projectors; no Indian price was found. — [Epson UAE ELPFT01](https://www.epson.ae/products/elpft01-finger-touch-unit-v12h007a24); [alloys.com.au](https://alloys.com.au/audio-visual/av-brackets/projector-mounts/Interactive-Touch-Module-for-EB-1480Fi-EB-1485Fi)
- BenQ PT02 is "a touch transmitter that works only with BenQ PointWrite-series projectors, and the PointWrite camera module must be installed first." — search summary of [BenQ PT02 manual](https://manualmachine.com/benq/pt02/17718064-user-manual)

### Inferences
- Commercial curtain units (Epson FTU, BenQ PT02) are locked to their own projector's camera and firmware, so they are useless with a generic projector. The only "kit" route for an arbitrary wall is a generic IR-camera kit plus pen, or a laser-curtain bundle from Chinese or IndiaMART vendors. Their software is usually Windows-only.
- Cheapest DIY build, from the listings above:
  - two 650 nm line modules at ₹32 each (prototype only), or two imported 850 nm 25 mW 120° modules at about ₹2,000 each including import;
  - a NoIR camera at about ₹1,600 (Pi-based), or a second-hand PS3 Eye;
  - a cheap 850 nm filter, a 5 V 2 A supply and mounting parts.

  That comes to roughly ₹2,000–6,000. These totals are my own assembly of the listed prices.

### Gaps
- I found no Amazon.in or Robu listing for an 850/940 nm *line* laser, a Powell lens, a cheap 850 nm IR-pass filter (e.g. a 25–30 mm glass filter or a 720 nm gel), or a USB no-IR-filter camera (ELP / Arducam USB). Searches returned eBay/AliExpress/Picotronic only. Check live on Amazon.in, Robu, Probots and Robocraze with terms such as "850nm line laser", "IR pass filter 850", "ELP no IR filter USB".
- No Indian price for a PS3 Eye (discontinued; second-hand only) or the Epson ELPFT01.

## 5. Open-source software and how hard it is to build into Python/OpenCV + a Swift Mac app

### Takeaway
The NUI-era stack is CCV (formerly tbeta, successor of Touchlib), reacTIVision, and TUIO over OSC/UDP. It still documents the pipeline well, but it is 2009–2012 C++/openFrameworks code that builds badly today. Re-implementing the LLP pipeline in Python/OpenCV is small (about 100–200 lines): background subtraction, threshold, blobs, tracking, homography, TUIO/OSC or a native macOS mouse/touch event. It fits easily alongside an existing Swift app.

### Cited Findings
- CCV "takes a video input stream and outputs tracking data (e.g. coordinates and blob size) and events (e.g. finger down, moved and released)". It can send OSC TUIO messages directly. Its filters let it "work with all optical setups (FTIR, DI, LLP, DSI)". — search summary of [CCV on Softpedia (Mac build)](https://mac.softpedia.com/get/Video/CCV.shtml)
- The CCV 1.5 source mirror ships build notes for Visual Studio 2008 and Ubuntu/Code::Blocks; no releases are published. — [github.com/Nebopolis/ccv15](https://github.com/Nebopolis/ccv15); related NUI Group repo [nuigroup/ccv-hand](https://github.com/nuigroup/ccv-hand)
- A user reported tracking problems near the edges of the camera view in CCV 1.2/1.3 where the original tbeta worked. — [vvvv forum](https://discourse.vvvv.org/t/7250)
- The NUI handbook's software chapter covers TUIO, Touchlib, tbeta/CCV and supports FTIR, DI, DSI and LLP lighting. — [NUI handbook](https://multigesture.net//wp-content/uploads/2009/07/First_Edition_Community_Release.pdf)
- A patent covers vision multi-touch using IR line-generating lasers that detects breaks in the plane (relevant for any commercial productisation, not for personal DIY). — [US8952894B2](https://patents.google.com/patent/US8952894B2/en)

### Inferences
- **Suggested pipeline (Python/OpenCV):**
  1. capture with `cv2.VideoCapture` at the highest fps;
  2. take a median background image with no hand present;
  3. `absdiff`, then Gaussian blur, then threshold;
  4. `connectedComponentsWithStats` and filter blobs by area;
  5. track with nearest-neighbour IDs;
  6. calibrate camera to projector with `findHomography` from 4–9 projected targets;
  7. output via python-osc as TUIO 1.1 `/tuio/2Dcur`, or post `CGEvent` mouse events through pyobjc or a Swift helper.

  Swift could do the same natively with AVFoundation plus Accelerate/vImage or Core Image. Roughly a weekend of work for single touch, more for robust multi-touch.
- This pipeline is the same as the shadow/IR-blob pipeline the user already has in ~/Projects/PhoneStereo (SurfaceTouch), so most code could be reused. Only the light source changes, from projector shadow to laser sheet.

### Gaps
- No current (2024–2026) maintained CCV fork confirmed; status of nuigroup GitHub org not checked in depth.
- reacTIVision is fiducial-oriented and not specific to LLP; its current release status was not verified in this pass.

## 6. IR pen + camera whiteboards (Johnny Lee Wiimote, Smoothboard) as the cheap alternative

### Takeaway
Johnny Lee's 2007 Wiimote whiteboard (and Smoothboard after it) points the Wiimote's IR camera at the projected image and tracks an IR-LED pen after 4-point calibration. It tracks up to 4 points, needs no laser or flatness, and is the cheapest robust option. But you must hold a pen (no bare finger), the pen body can block the camera's view, and sunlight floods it. In 2026 the same idea works with any NoIR or filter-modded camera plus a ₹10–50 IR LED pen.

### Cited Findings
- Lee's whiteboard uses the Wiimote's IR camera, IR-LED pens and a PC to make a low-cost interactive or multi-point display; the Wiimote tracks up to 4 IR sources. — [Hackaday 2007](https://hackaday.com/2007/12/09/wiimote-projector-whiteboard); [Engadget 2007](https://www.engadget.com/2007/12/10/wiimote-repurposed-for-multi-point-interactive-whiteboard/); [Slashdot](https://hardware.slashdot.org/story/07/12/19/140236/wiimote-as-multi-touch-display-controller)
- A Wiimote board "isn't touch-sensitive", so users must use the IR pen. Calibration locates the pen. — search summary of [UBC MET wiki](https://wiki.ubc.ca/MET:Wiimote_Whiteboard); [Instructables](https://instructables.com/Interactive-Smartboard-using-Wiimote)
- Smoothboard (Boon Jin Goh) is a more teacher-friendly, more advanced successor. Its camera-sensitivity setting can be lowered to cut sunlight interference, especially for permanently mounted Wiimotes. — [techshoestring blog](https://techshoestring.blogspot.com/2009/02/forty-dollar-smart-board-using-wii.html); [CR4 blog](https://cr4.globalspec.com/blogentry/6714/Wii-Shall-Overcome-Wii-Whiteboard-Part-7); [Smoothboard user guide (Scribd)](https://www.scribd.com/document/465041452/Smoothboard-User-Guide-pdf)
- Sunlight, candles and heat sources interfere. One user found opening the drapes on a sunny day "made the wiimote useless". — [3pinteract blog](https://3pinteract.blogspot.com/2009/03/trouble-shooting-crazy-mouse.html); [Slashdot comments](https://hardware.slashdot.org/story/07/12/19/140236/wiimote-as-multi-touch-display-controller)
- An "IR saber" variant (IR LED on a stick) exists for reaching large walls. — [Make: Wiimote whiteboard IR saber](https://makezine.com/projects/wiimote-whiteboard-ir-saber)
- On Mac, OSCulator was used to bridge the Wiimote. — [OSCulator tutorial](https://osculator.net/doc/tutorial:5)

### Inferences
- **Laser plane vs IR pen.** The laser plane gives bare-finger, multi-person touch but needs flatness, alignment and eye-safety care. The IR pen needs none of those, and tracks robustly because the source is bright and point-like. A phone or webcam with a visible-blocking filter (or even unmodded for a bright 850 nm LED) might see it.
- On modern Macs, Wiimote Bluetooth pairing is unreliable. A NoIR USB or Pi camera plus a self-made pen (850 nm LED, tactile switch at the tip, AAA cell) makes more sense in 2026.

### Gaps
- The commonly quoted Wiimote IR camera spec (1024×768 interpolated, about 100 Hz, 4 points) was not confirmed by a primary source in this search.
- No measured mm accuracy or latency for Lee's whiteboard or Smoothboard was found. The only number is the 49.6 ms Wiimote acquisition latency from a motion-capture study, which is a different application ([SciTePress 2009](https://www.scitepress.org/Papers/2009/18066/18066.pdf)).
- No Indian listing for a ready-made IR whiteboard pen kit was retrieved.

## 7. Scope update: with only a phone (POCO M4 Pro), the projector and a Mac, can the light plane be improvised, and can the phone see a visible red line laser?

### Takeaway
Treat the IR laser curtain as the reference design and paid kits (Epson/BenQ/IndiaMART, ₹23k–2 lakh, section 4) as the "if buying" tier. With no purchase at all, the projector can't make a true grazing light plane: its light comes from the front, so it hits the wall face-on. What the projector *can* do is the shadow trick (published as PlayAnywhere and "Everywhere Switch"), which is what SurfaceTouch already does. A phone torch through a slit gives a wedge, not a 1–2 mm sheet; that is an inference, not tested. The best cheap add-on is a 650 nm red line laser (₹32–300) grazing the wall, watched by the phone's rear camera with manual exposure. The phone's IR-cut filter doesn't matter at 650 nm. The catch is telling the laser scatter apart from projected red/white content. That is solved by low exposure plus colour thresholding, keeping the touch area dark or non-red, or toggling the laser and differencing frames.

### Cited Findings
**Projector-only touch tricks (no added light source)**
- PlayAnywhere (Wilson, UIST 2005): front projector and camera sit off to one side of the surface. Touch is inferred from how the finger's shadow changes as it nears the surface, so touch and hover are told apart by shadow shape. The original prototype used an IR-pass camera with an IR LED illuminant. — [Microsoft Research, PlayAnywhere](https://www.microsoft.com/en-us/research/?p=304277); [video](https://www.microsoft.com/en-us/research/video/playanywhere/) (DOI 10.1145/1095034.1095047)
- "Everywhere Switch" (SciTePress 2015): an ordinary optical camera mounted about 50 cm beside the projector so shadows are visible. The authors say a depth camera wasn't accurate enough for them. The shadow is large when hovering and nearly vanishes at contact; the foreground is split into finger and shadow regions and their area ratio is used. The method has to tolerate varying living-room brightness. — [SciTePress 2015 paper](https://www.scitepress.org/Papers/2015/53076/53076.pdf)
- Earlier, Hartmann et al. 2012 found the fingertip by foreground shape analysis after separating out its shadow (cited in the paper above). — [SciTePress 2015](https://www.scitepress.org/Papers/2015/53076/53076.pdf)
- Two illumination sources at different positions plus one camera give each finger two shadows, which allows multi-touch contact detection. A related patent family detects touch from finger and shadow without touch sensors. — [US10152177B2](https://patents.google.com/patent/US10152177); [US9367176B2](https://patents.google.com/patent/US9367176)
- NAIST "slope disparity gating": a projector synchronised with a rolling-shutter camera so the camera only images a thin slab just above the surface, registering fingers only on contact. This is effectively a virtual light plane made by the projector itself, but it needs a sync-capable laser-scanning projector and camera. — [Electronics For You](https://www.electronicsforu.com/tech-zone/display-tech/touch-sensing-for-a-projected-screen)
- Structured light on the fingertip: project a coded local pattern; the disparity of the code on the fingertip shows whether a touch really happens. — [IEEE paper via infona](https://www.infona.pl/resource/bwmeta1.element.ieee-art-000006920563)

**Visible red line laser + ordinary camera**
- Easy-Touch V1.0 (student project, GitHub): an ordinary camera finds a red laser dot (YOLOv8), OpenCV contours find the projected screen corners, and a perspective transform maps camera to screen. The authors advise making the red laser bright relative to the projection for reliable detection. Dependencies are opencv-python, numpy and keyboard. This is a laser *pointer* rather than a laser plane, but the calibration and mapping pipeline is the same. — [Easy_Touch_V1.0 mirror](https://gh.r-mj.com/ZBoyn/Easy_Touch_V1.0) (status and licence not verified)
- IEEE Access 2018, "Turn Any Display Into a Touch Screen Using Infrared Optical Technique": one webcam plus an IR laser line projector and image processing. It is a peer-reviewed LLP-style reference, but IR, not red. — [DOAJ record](https://doaj.org/article/a8be869c509a40cdbbc2b3e592227df8)
- Laser projection keyboards (Celluon etc.) are a commercial miniature of the same principle. A red diode with a diffractive element projects the keys; an IR plane "just above and parallel to the surface", a few mm high, is broken by the fingertip; and an IR-filtered camera sees the reflection while ignoring the visible projection. — [Wikipedia, Projection keyboard](https://en.wikipedia.org/wiki/Projection_keyboard). An OpenCV re-implementation exists: [hxy513696765/OpenCV-laser-projection-virtual-keyboard](https://github.com/hxy513696765/OpenCV-laser-projection-virtual-keyboard/blob/master/README.md)
- Interactive-wall kit vendors describe a line laser just above the projection surface plus a camera. — [KL Multimedia, setup interactive wall projection](https://www.klmultimedia.com/setup-interactive-wall-projection.html)
- Robu sells 650 nm 5 V red straight-line modules at about ₹32 (section 4). — [Robu](https://stg.robu.in/?p=44163)

**Code: CCV, TUIO, blob trackers**
- CCV 1.5 source: [Nebopolis/ccv15](https://github.com/Nebopolis/ccv15). NUI Group org: [nuigroup/ccv-hand](https://github.com/nuigroup/ccv-hand). reacTIVision (tracks untagged blobs and sends them via the TUIO blob profile): [mkalten/reacTIVision on ecosyste.ms](https://awesome.ecosyste.ms/projects/github.com%2Fmkalten%2Freactivision). TUIO 1.1 clients: [TUIO11_Processing](https://github.com/mkalten/TUIO11_Processing), [TUIO11_NET](https://github.com/mkalten/TUIO11_NET), [TUIO11_MaxMSP](https://github.com/mkalten/TUIO11_MaxMSP). Python TUIO receiver (old, Python 2-era): [pytuio](https://pypi.org/project/pytuio/). A proposal-stage blob-to-TUIO tool: [k-alligator-eye](https://github.com/kevinaloys/k-alligator-eye).
- Papers on LLP/optical touch pipelines: [arXiv 1301.1551, A novel processing pipeline for optical multi-touch surfaces](https://arxiv.org/pdf/1301.1551); the NUI handbook (section 1); IR laser-plane patent [US8952894B2](https://patents.google.com/patent/US8952894B2/en); "Learning to Detect Touches on Cluttered Tables" (2023, learned touch detection for projector-camera tables) [arXiv 2304.04687](https://ar5iv.labs.arxiv.org/html/2304.04687).

### Inferences
- **Projector as the light plane.** Not physically possible on a flat wall. The projector's rays hit the wall at a steep angle, so any "thin line" it draws lies *on* the wall, not grazing it. What it does give is a shadow cue (PlayAnywhere-style), which needs the camera offset from the projector. An offset phone camera (the 50 cm baseline in the Everywhere Switch paper) is the zero-cost route the user already has.
- **Phone torch through a slit.** A white LED is an extended, uncollimated source. A slit 1 mm wide and tens of cm from the LED still spreads into a wedge many mm thick at 1–2 m. You would need a cylindrical lens or a long baffle tube, and even then the sheet would be dim compared with the projector. Likely to cause hover false-positives. Untested inference; I found no source describing it.
- **Phone seeing a 650 nm laser.** Phone camera filters pass 650 nm (it is visible red), so a red sheet scattering off a fingertip shows up as a small saturated red spot. To make it robust:
  1. Lock exposure and ISO low with Camera2 manual controls, so the projected image is dim and the laser scatter still clips.
  2. Threshold in HSV/red-minus-green space.
  3. Subtract a background taken with no hand.
  4. Avoid projecting saturated red in touch zones, or periodically toggle the laser (Arduino/ESP32, or a USB-powered module switched by a phone-controlled relay) and difference laser-on/off frames.
  5. Grazing geometry on a painted wall: mount the module at the top or side edge, roughly 3–8 mm off the wall, tilted slightly into it (Epson-style 8 mm → 2 mm).
  6. Two modules from adjacent corners for two-finger occlusion.

  A red sheet is also visible to people and lights up skin, which looks fine for a demo but is distracting. Class 2 (under 1 mW) or a 5 mW module spread by a 90–120° lens gives low irradiance per area, but never look into the source.
- **IR on this hardware.** 850 nm needs a NoIR camera, and the POCO's cameras aren't NoIR, so an IR curtain only becomes an option if they buy a NoIR Pi/USB camera (about ₹1,600) or an IR-sensitive USB webcam. That makes IR the "if buying ~₹4–6k DIY" tier, below the ₹23k+ commercial-kit tier.
- **Pipeline on this hardware.** Phone (Camera2 manual exposure; stream frames, or detect blobs on-device to cut latency) → Wi-Fi/USB → Mac (OpenCV homography → TUIO/OSC or CGEvent). The same calibration and homography code serves the shadow method, the red laser plane and an IR pen, so they can be swapped as tiers.

### Gaps
- No source found showing a visible-red laser *plane* (as opposed to a pointer) used with a phone camera against a live projection. Feasibility is inferred and needs a bench test with a ₹32 module.
- No source on torch-plus-slit light planes.
- No latency figures for phone-to-Mac frame streaming on a POCO M4 Pro.
