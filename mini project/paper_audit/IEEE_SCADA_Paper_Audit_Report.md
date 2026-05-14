# IEEE SCADA Paper Audit Report

Audited document: `C:\Users\nandi\Desktop\V_2_BOILER_MNGT_SYS_MINI_PROJECT\paper_audit\IEEE_SCADA_Paper_Final.docx`

Original user file: `C:\Users\nandi\Downloads\IEEE_SCADA_Paper_Final.docx`

Audit date: 2026-05-12

## Verdict

The paper is technically promising and the main system architecture mostly matches the project implementation, but it is not yet submission-ready. The biggest blockers are citation accuracy, over-strong claims around "ML" and validation results, an emergency-countdown inconsistency, and a DOCX image-packaging issue.

## Local Extraction Summary

- Extracted 124 non-empty paragraphs.
- Extracted 1 comparison table.
- Found 7 drawing references and 7 real embedded PNG figures.
- The embedded images are valid PNG data, but the DOCX stores them as `*.undefined` files inside `word/media/`.
- LibreOffice/`soffice` is not installed, so page-by-page render QA could not be completed.

## Architecture Check Against Code

The core architecture described in the paper is mostly correct:

- Arduino Nano field layer matches the firmware:
  - HC-SR04 pins: trigger `D9`, echo `D10`.
  - Pressure input: `A0`.
  - `MAX_DISTANCE = 150`.
  - CRC polynomial `0xA001`.
  - COBS frame delimiter `0x00`.
  - Serial baud `115200`.
  - Watchdog `WDTO_2S`.
  - Loop delay `300 ms`.
- Raspberry Pi edge layer matches `Sensors.py` and `OutputController.py`:
  - `boiler/sensors` MQTT topic.
  - `boiler/command` command topic.
  - Packed struct format `<H I f f H`.
  - DHT11 on `board.D4` and `board.D17`.
  - Heater GPIO `27`, motor GPIO `22`.
  - MQTT publish loop sleeps `0.2 s`, so nominal publication is about `5 Hz`.
  - Three Pi-side processes are `arduino`, `dht`, and `mqtt`.
- Supervisory station matches `scada_core.py`:
  - Services: `TELEMETRY`, `CAMERA`, `GUI`, `VOICE_ALARM`, `ML_ENGINE`, `WEB_DASHBOARD`.
  - Shared bus writes `shared_state.json` and `shared_log.json`.
  - Web dashboard reads state and posts commands through `web_command.json`.
- ML engine matches five heuristic detectors:
  - Range check, rate-of-change, flatline, drift, and correlation checks.
  - `HISTORY_SIZE = 60`.
  - Current countdown constants are `SINGLE = 20s`, `COMPOUND = 10s`.

## Blocking Issues

1. Emergency countdown is inconsistent.

The abstract and Section IV state `20 s` single-condition and `10 s` compound-condition countdowns. The current code also has `20/10`. However, Section V and Fig. 4 cite a `30 s` HIGH LEVEL validation cycle from an older log. This must be reconciled before submission.

Recommended fix: say the current implementation uses `20/10`, and either remove the old `30 s` validation event or explicitly label it as an earlier configuration run.

2. The "ML" claim is overstated.

The implementation is a rule-based/heuristic anomaly detector, not a trained machine-learning model. It is okay to describe it as an anomaly-detection engine, confidence-scoring engine, or ML-inspired health model, but "ML engine implements five concurrent anomaly detection algorithms" may be challenged by reviewers unless trained models are actually used.

Recommended fix: replace "ML-based" in the strongest places with "rule-based anomaly detection with confidence scoring" or add a real trained model experiment.

3. Several references are inaccurate or misused.

High-risk citation problems:

- `[2]` and `[4]` duplicate the Zare & Iqbal paper. The verified DOI is `10.1109/IEMTRONICS51293.2020.9216412`, not the paper's listed ELECTRO DOI.
- `[3]` and `[6]` duplicate the Oton & Iqbal paper.
- `[7]` should be INTERCON 2021 with DOI `10.1109/INTERCON52678.2021.9532668`, not INDICON. The verified summary says Extra Trees was best, not SVM.
- `[9]` appears to be a 2024 ICEPES paper with DOI `10.1109/ICEPES60647.2024.10653574`, not a 2021 generic IEEE paper.
- `[14]` appears to have wrong conference/DOI. Verified public citations list TCSET 2020 and DOI `10.1109/TCSET49122.2020.235462`.
- `[21]` author/title mismatch: the DOI `10.1109/ICAICST53116.2021.9497829` appears tied to "Security Challenges in Industry 4.0 SCADA Systems - A Digital Forensic Prospective" by Malik et al., not Hassanzadeh et al.
- `[26]` is a connected-vehicle survey, not a source for industrial sensor confidence aggregation.
- `[27]` is a traffic-flow prediction citation, not rotating machinery anomaly detection.
- `[28]` is an AI/XAI Industry 4.0 survey with DOI `10.1109/TII.2022.3146552`, not process-vessel predictive maintenance with 94% accuracy.
- `[29]` is a digital twin conceptual model, not an MQTT/CoAP/AMQP industrial gateway benchmark.
- `[31]` is not a good source for industrial automation edge latency as cited; the visible search result points to smart precision agriculture.

Recommended fix: rebuild the reference list, remove duplicates, and only use each source for claims it actually supports.

4. Some validation claims are too strong for the evidence present.

- "100% packet error detection" is acceptable only for the four injected corrupt packets tested; it should not imply universal CRC detection.
- CRC-16 does not provide "99.998% single-burst-error detection capability" in that exact way. Better: it detects all burst errors up to 16 bits and has about `1/65536` undetected probability for random error patterns.
- "MQTT QoS 1 delivery confirmed zero message loss" needs stronger evidence than sequence-jump checks and observed subscriber rate.
- "Flatline flags correctly identify near-constant idle values" should be softened because near-constant idle values can be normal operation, not necessarily sensor failure.
- "Sub-400 ms latency" should include measurement method, sample count, and timestamp source, otherwise say "observed in bench tests" rather than "measured consistently."

5. DOCX image packaging should be repaired.

All actual figures are valid PNGs, but the DOCX relationship targets are named `*.undefined`, and `[Content_Types].xml` does not define `undefined` as an image type. Microsoft Word may tolerate this, but strict OOXML renderers may fail or drop images.

Recommended fix: rename embedded media parts to `.png`, update `word/_rels/document.xml.rels`, and keep the existing PNG content type.

## Figure Review

The figures appear original, relevant, and mostly consistent with the project. They are readable at extracted resolution.

Specific notes:

- Fig. 1 three-tier architecture is consistent with code.
- Fig. 2 ML flow is visually clean but should say heuristic/rule-based unless trained ML is added.
- Fig. 3 IPC model is consistent with `shared_state.json`, `ml_predictions.json`, and `web_command.json`.
- Fig. 4 has the countdown mismatch: it shows a `30 s` run, while current implementation is `20/10`.
- Fig. 5 detailed hardware/software architecture is consistent with `scada_core.py` and Pi code.
- Fig. 6 flowchart matches current ML constants: SINGLE `20s`, COMPOUND `10s`.
- Fig. 7 telemetry packet is consistent with the Arduino and Pi packet struct.

## Plagiarism-Risk Sweep

I ran exact-phrase web searches on the title, unique abstract sentences, "Audio NVIC", and the COBS/CRC wording. I did not find a copied source for the unique project-specific text.

However, some generic literature-review phrasing is common online, especially phrases like "SCADA systems have long been the backbone..." This is not proof of plagiarism, but it is worth rewriting those generic sentences in a more original, project-specific voice.

Important limitation: this is not a substitute for Turnitin/iThenticate. It is a manual web-search risk check.

## High-Quality Content Recommendations

- Add a short "Limitations" paragraph: DHT11 accuracy, no pressure-certified boiler test, local LAN security only, heuristic anomaly detection, and no formal SIL/IEC safety certification.
- Add a reproducibility table: hardware, firmware version, Pi OS version, Python version, broker version, test duration, number of packets, number of injected corrupt packets.
- Replace "industrial boiler" language with "bench-scale boiler-management test bed" unless it was tested on a real certified boiler.
- Add equations or pseudocode for the confidence score and emergency arming logic.
- Include exact validation metrics: number of alarm trials, false positives, false negatives, mean latency, max latency, packet count, packet loss count.
- Replace weak/incorrect sources with stronger primary sources and standards where possible.

## Sources Used For External Verification

- OASIS MQTT v3.1.1 specification: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html
- Mosquitto JOSS paper: https://joss.theoj.org/papers/10.21105/joss.00265
- COBS paper metadata: https://www.researchgate.net/publication/3334608_Consistent_overhead_byte_stuffing
- Jahin et al. boiler SCADA DOI record: https://colab.ws/articles/10.1109%2Facit62333.2024.10712619
- Cook et al. anomaly detection survey DOI record: https://colab.ws/articles/10.1109%2Fjiot.2019.2958185
- Zare & Iqbal low-cost SCADA record: https://www.researchgate.net/publication/347154219_Low-Cost_ESP32_Raspberry_Pi_Node-Red_and_MQTT_Protocol_Based_SCADA_System
- Aghenta & Iqbal PV SCADA record: https://www.researchgate.net/publication/336439913_Development_of_an_IoT_Based_Open_Source_SCADA_System_for_PV_System_Monitoring
- Babilonia Risco et al. IEEE record: https://ieeexplore.ieee.org/document/9532668/
- Hoque et al. scalable MQTT/time-series paper record: https://colab.ws/articles/10.1109%2Ficepes60647.2024.10653574
- Al-Ali et al. digital twin paper: https://www.mdpi.com/840252
- Ahmed et al. AI/XAI Industry 4.0 record: https://scholars.aku.edu/en/publications/from-artificial-intelligence-to-explainable-artificial-intelligen/
- Dizdarevic et al. IoT protocol survey record: https://www.researchgate.net/publication/324245813_A_Survey_of_Communication_Protocols_for_Internet_of_Things_and_Related_Challenges_of_Fog_and_Cloud_Computing_Integration

