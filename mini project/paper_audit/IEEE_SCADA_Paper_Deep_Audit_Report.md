# Deep Audit Report - IEEE SCADA Paper

Audit date: 2026-05-12  
Paper reviewed: `C:\Users\nandi\Downloads\IEEE_SCADA_Paper_Final.docx`  
Project root reviewed: `C:\Users\nandi\Desktop\V_2_BOILER_MNGT_SYS_MINI_PROJECT`

## 1. Bottom-line verdict

The paper is built around a real project, and the core architecture is mostly correct: Arduino Nano field telemetry -> Raspberry Pi edge gateway -> Windows SCADA station with GUI, web dashboard, voice alarm, camera, telemetry service, and anomaly engine.

However, the paper is not yet publication-safe in its current form. The main problems are:

1. Several references are duplicated, incomplete, wrong, or used for claims they do not support.
2. The anomaly system is rule-based/heuristic, not trained machine learning. Calling it "ML-based" throughout the paper is too strong unless the paper clearly defines it as a heuristic anomaly detector.
3. Emergency countdown evidence is inconsistent: current code uses 20 seconds for single critical faults and 10 seconds for compound faults, but Fig. 4/results still describe an older 30-second countdown.
4. Performance claims such as "sub-400 ms measured consistently", "100% packet error detection", "zero MQTT message loss over 4 hours", and "4-hour evaluation" need raw measurement tables or must be softened.
5. The DOCX images are valid PNGs, but the original DOCX stored them as `.undefined` OOXML media targets with no alt text. I created a fixed copy with proper image extensions.
6. I found no obvious exact-text plagiarism from web phrase checks, but this is not a replacement for Turnitin/iThenticate.

## 2. Artifacts generated

| Artifact | Purpose | Status |
|---|---|---|
| `paper_audit/extracted/paragraphs.txt` | Extracted paper text with paragraph indexes | Created |
| `paper_audit/extracted/images/contact_sheet.png` | Visual contact sheet of all embedded figures | Created |
| `paper_audit/IEEE_SCADA_Paper_Audit_Report.md` | Initial audit notes | Created |
| `paper_audit/IEEE_SCADA_Paper_ImageFixed.docx` | DOCX copy with `.undefined` image parts renamed to `.png` | Created |
| `paper_audit/IEEE_SCADA_Paper_Deep_Audit_Report.md` | This deeper audit | Created |

Important limitation: full rendered page QA could not be completed because LibreOffice/`soffice`, Word CLI, Pandoc, and docx2pdf were not available in the environment. The DOCX package and embedded images were inspected directly.

## 3. Claim backtracking against project files

| Paper claim | Project evidence | Verdict | Notes |
|---|---|---|---|
| Three-tier architecture: Arduino field layer, Raspberry Pi edge gateway, Windows supervisory station | `AURD_NANO_CODE/...ino`, `RASPBERRY PI FILES/Sensors.py`, `scada_core.py`, `application_tools/GUI.py` | Correct | Fig. 1 and Fig. 5 match the real architecture. |
| HC-SR04 on D9/D10, pressure transducer on A0 | Arduino firmware defines `trigPin=9`, `echoPin=10`, `pressurePin=A0` | Correct | Strong match. |
| NewPing max 150 cm, EMA alpha 0.3, pressure ADC `[100,900] -> [0,10] bar` | Arduino firmware implements all three | Correct | Strong match. |
| CRC-16 polynomial `0xA001`, COBS framing, `0x00` delimiter, watchdog `WDTO_2S`, 300 ms loop | Arduino firmware implements these | Correct | The implementation is real. |
| Telemetry packet is 14 bytes: sequence, timestamp, distance, pressure, crc16 | Arduino `TelemetryPacket` and Python `PACKET_STRUCT="<H I f f H"` | Correct | COBS size of 15-16 bytes plus delimiter is plausible. |
| CRC-16 provides "99.998% single-burst-error detection capability" | CRC implementation exists, but source [30] is COBS, not CRC theory | Needs correction | Say "approximately 1/65536 residual undetected probability for random corruptions" if you cite a CRC source. Do not cite COBS for CRC guarantees. |
| Raspberry Pi gateway has Arduino, DHT, MQTT processes | `RASPBERRY PI FILES/Sensors.py` process list | Correct | Three-process claim is accurate. |
| DHT11 sensors on GPIO D4/D17, 3 s sample interval | `Sensors.py` uses `board.D4`, `board.D17`, sleep 3 | Correct | Good. |
| MQTT publishes `boiler/sensors` at 5 Hz QoS 1 and subscribes to `boiler/command` | `Sensors.py` publishes QoS 1 every 0.2 s; subscribes command topic | Mostly correct | Arduino sampling is about 3.3 Hz, so effective new Arduino values may not be 5 Hz. |
| Heater relay GPIO 27, motor relay GPIO 22, persisted in `outputs_state.json` | `RASPBERRY PI FILES/OutputController.py` | Correct | Good evidence. |
| MJPEG camera at 640x480@30 fps, port 8080 | `RASPBERRY PI FILES/mjpeg_server.py` | Correct | Good evidence. |
| Supervisory station runs six managed services | `scada_core.py` service list | Correct | Telemetry, camera, GUI, voice, ML, web dashboard are defined. |
| Shared data bus atomic JSON writes at 2.5 Hz | `shared_data_bus.py` writer loop sleeps 0.4 s | Correct | Good evidence. |
| Alarm thresholds: low level `distance > 120`, high level `<20`, high pressure `>9`, low pressure `<1` | `scada_application_layer.py` | Correct | Good evidence. |
| Web dashboard on port 5000, `/api/data`, `/api/command`, password | `application_tools/web_dashboard/web_server.py` | Correct with security caveat | Password is hardcoded default `"1234"` and plain-text form auth. Present as prototype security, not robust industrial security. |
| Voice alarm: priority queue, stale alarm drop, debounce, winsound/pyttsx3 assets | `voice_alarm_system.py`, `voice_assets/alarm_asset_manager.py` | Correct | "Audio NVIC" is a project metaphor; define it as your implementation term, not a known standard. |
| SSH discovery uses cached IP, mDNS, subnet scan, manual fallback, 60 threads | `ssh_manager.py` | Correct | The code uses `ThreadPoolExecutor(max_workers=60)`. |
| Anomaly engine uses five detectors and 60-sample history | `application_tools/ML.py` | Partly correct | The code is heuristic/rule-based. It is not a trained ML model. |
| Emergency response: single 20 s, compound 10 s | Current `application_tools/ML.py` | Correct for current code | This conflicts with Fig. 4/results that still describe 30 s from an older log. |
| Latest state: water level 53.54, pressure 3.42, temps 28.3/28.0, sequence 1035, confidence 0.85 | `shared_state.json`, `ml_predictions.json` | Correct | The numbers match the project files. |
| 2026-05-08 high-level emergency cycle with 30 s countdown | `logs/system_events.jsonl` | Historically correct, but stale | This is from an older configuration. Label it as earlier configuration or remove it. |
| 2026-05-09 high-pressure cycle with 20 s countdown | `shared_log.json` | Correct | This matches current implementation. |
| 6000 frames, four artificial corruptions, zero message loss, sub-400 ms latency | Not fully backed by raw measurement artifact found | Needs evidence | Add tables/scripts/log extracts or soften these claims. |
| COBS+CRC protects against replay of malformed frames | COBS+CRC detects malformed/corrupt frames | Incorrect wording | CRC does not prevent replay. Replay protection needs sequence/timestamp checks plus authentication. |

## 4. Figure and image audit

The DOCX contains 7 real embedded figures. All extracted image files are valid PNGs, visually readable, and appear to be project-specific diagrams rather than copied screenshots.

| Figure | Visual content | Verdict | Required fix |
|---|---|---|---|
| Fig. 1 | Three-tier architecture | Correct | Add alt text. |
| Fig. 2 | "ML" anomaly engine flow | Needs wording fix | Rename to "Rule-based anomaly engine" or explain "ML" as heuristic anomaly scoring. |
| Fig. 3 | GUI/web/IPC data flow | Correct | Add alt text. |
| Fig. 4 | Health score + emergency timeline | Needs correction | Timeline shows 30 s; current design is 20/10 s. Replace or relabel as older test. |
| Fig. 5 | Detailed hardware/software architecture | Correct | Good match to code. |
| Fig. 6 | Emergency flowchart 20/10 s | Correct | Good match to current code. |
| Fig. 7 | Telemetry packet and COBS framing | Correct | Good match to packet struct. |

Packaging issue fixed in copy: original relationships pointed to `word/media/*.undefined`. The fixed copy uses proper `.png` targets and opens through `python-docx` with 7 inline shapes.

## 5. Citation audit and backtracking

| Ref | Audit result | Action |
|---|---|---|
| [1] Jahin/Muhammad et al. boiler SCADA, DOI `10.1109/ACIT62333.2024.10712619` | Bibliographic metadata checks out. | Keep, but cite only for PLC/SCADA/Haiwell boiler monitoring, not for this paper's automated ML design. |
| [2] Zare & Iqbal | Duplicate of [4], and DOI/conference are wrong/incomplete. Correct DOI is `10.1109/IEMTRONICS51293.2020.9216412`. | Merge with [4]. |
| [3] Oton & Iqbal | Duplicate of [6]. DOI appears valid: `10.1109/CCECE53047.2021.9569100`. | Keep one copy only. |
| [4] Zare & Iqbal | Same work as [2]. | Keep one corrected IEEE reference. |
| [5] Aghenta & Iqbal | Incomplete venue/DOI metadata. | Verify or replace with a fully traceable source. |
| [6] Oton & Iqbal | Same work as [3]. | Remove duplicate. |
| [7] Smart-grid IoT-SCADA ML paper | Paper lists INDICON and incomplete/wrong DOI. Verified DOI is `10.1109/INTERCON52678.2021.9532668`. | Correct conference to INTERCON and use accurate DOI. |
| [8] Wang & Su boiler remote monitoring | DOI incomplete. | Find complete IEEE metadata before submission. |
| [9] Hoque et al. MQTT/time-series database | Paper says 2021/incomplete; verified as ICEPES 2024, DOI `10.1109/ICEPES60647.2024.10653574`. | Correct year, venue, DOI. |
| [10]-[13], [15]-[20] | Many entries are incomplete IEEE-style citations without DOI, pages, conference name, or publisher. | Complete or remove. Incomplete references weaken the paper badly. |
| [14] Mnushka & Savchenko | Paper gives AICT DOI, but verified citation is TCSET 2020, DOI `10.1109/TCSET49122.2020.235462`. | Correct DOI/venue. |
| [21] Hassanzadeh et al. | IEEE Xplore result supports title and DOI `10.1109/ICAICST53116.2021.9497829`. | Keep for SCADA security threats. |
| [22] Forbes IoT forecast | Weak/unstable citation and URL is generic. | Replace with a stable report or remove the statistic. |
| [23] MQTT OASIS spec | Valid. | Keep for MQTT protocol/QoS facts. |
| [24] Mosquitto JOSS | Valid. | Keep for Mosquitto broker implementation. |
| [25] Cook et al. IoT anomaly survey | Valid. | Keep for general anomaly detection. Do not claim it proves domain thresholds outperform generic ML unless quoting exact support. |
| [26] Siegel et al. connected-vehicle survey | Bibliographically valid but unrelated to industrial confidence aggregation. | Remove or replace. |
| [27] Xu et al. traffic-flow LSTM | The cited title is traffic prediction, not rotating machinery anomaly detection. | Replace with an actual industrial anomaly/predictive maintenance source. |
| [28] Ahmed/Jeon/Piccialli XAI survey | Title is valid but DOI should be `10.1109/TII.2022.3146552`; it is a survey, not a process-vessel 94% accuracy study. | Correct DOI and remove unsupported 94% claim. |
| [29] Al-Ali et al. digital twin conceptual model | Bibliographically valid, but not MQTT/CoAP/AMQP gateway benchmarking. | Use only for digital twin/IoT context or replace for protocol benchmarking. |
| [30] Cheshire & Baker COBS | Valid. | Keep for COBS overhead/framing, not CRC theory. |
| [31] Hamdan et al. precision agriculture | Valid but not industrial automation edge latency. | Use only if discussing IoT sensing in rural/agriculture, or replace. |
| [32] Dizdarevic et al. IoT protocol survey | Valid. | Keep, but soften "MQTT superiority" to "MQTT is a mature, widely used option for constrained IoT layers." |

## 6. Plagiarism-risk check

I checked distinctive phrases from the abstract, architecture, Audio NVIC wording, COBS telemetry wording, and the five-detector anomaly engine wording. I did not find an obvious exact-copy source for the unique paper text.

This is a low-risk signal, not a formal plagiarism clearance. A formal check still requires Turnitin, iThenticate, or the institution's plagiarism system.

Potential academic-integrity risk is mainly citation misattribution, not copied text: some claims cite papers that do not support them. Fixing those citations is necessary before submission.

## 7. Required paper edits before submission

1. Change the title/abstract from "ML-based" to "rule-based anomaly detection" or "heuristic anomaly scoring" unless a trained model is actually added.
2. Remove the duplicated references [2]/[4] and [3]/[6].
3. Correct wrong references [7], [9], [14], [28], and complete incomplete IEEE entries.
4. Replace unsupported citations [26], [27], [29], [31] where they are being used for industrial ML, MQTT benchmarking, edge latency, or confidence aggregation.
5. Fix the countdown story: current architecture is 20 s single and 10 s compound. Do not mix this with the older 30 s validation unless it is explicitly marked as older configuration.
6. Add a reproducibility table: test date, duration, frame count, expected frame rate, observed frame rate, packet loss criteria, corruption method, alarm trigger condition, measured latency method.
7. Soften unproven absolute claims: "100% packet error detection", "zero message loss", "measured consistently", and "industrial process safety".
8. Add a limitations section: bench-scale prototype, no certified safety PLC, no SIL/IEC 61508 validation, no TLS, hardcoded web password, no authenticated command channel.
9. Replace "AZ-5" with "emergency shutdown command" unless the paper intentionally explains the analogy. "AZ-5" is not standard boiler terminology and may distract reviewers.
10. Add alt text to every figure and keep captions consistent with the final design.

## 8. Recommended corrected reference entries

Use these as replacements where applicable:

- A. Zare and M. T. Iqbal, "Low-Cost ESP32, Raspberry Pi, Node-Red, and MQTT Protocol Based SCADA System," 2020 IEEE International IOT, Electronics and Mechatronics Conference (IEMTRONICS), 2020, doi: `10.1109/IEMTRONICS51293.2020.9216412`.
- C. N. Oton and M. T. Iqbal, "Low-Cost Open Source IoT-Based SCADA System for a BTS Site Using ESP32 and Arduino IoT Cloud," 2021 IEEE Canadian Conference on Electrical and Computer Engineering (CCECE), 2021, doi: `10.1109/CCECE53047.2021.9569100`.
- A. Babilonia Risco, R. I. Gonzalez Salinas, A. Orbegoso Guerrero, and D. L. Barrera Esparta, "IoT-based SCADA System for Smart Grid Stability Monitoring using Machine Learning Algorithms," 2021 IEEE XXVIII International Conference on Electronics, Electrical Engineering and Computing (INTERCON), 2021, doi: `10.1109/INTERCON52678.2021.9532668`.
- S. Hoque et al., "Development of a Scalable Industrial Process Monitoring Sensor System for Large-Scale IoT Networks using MQTT and Time Series Database," 2024 IEEE 3rd International Conference on Electrical Power and Energy Systems (ICEPES), 2024, doi: `10.1109/ICEPES60647.2024.10653574`.
- O. Mnushka and V. Savchenko, "Security Model of IoT-based Systems," 2020 IEEE 15th International Conference on Advanced Trends in Radioelectronics, Telecommunications and Computer Engineering (TCSET), 2020, pp. 398-401, doi: `10.1109/TCSET49122.2020.235462`.
- A. Banks and R. Gupta, "MQTT Version 3.1.1," OASIS Standard, 2014.
- R. A. Light, "Mosquitto: server and client implementation of the MQTT protocol," Journal of Open Source Software, vol. 2, no. 13, p. 265, 2017, doi: `10.21105/joss.00265`.
- A. A. Cook, G. Misirli, and Z. Fan, "Anomaly Detection for IoT Time-Series Data: A Survey," IEEE Internet of Things Journal, vol. 7, no. 7, pp. 6481-6494, 2020, doi: `10.1109/JIOT.2019.2958185`.
- S. Cheshire and M. Baker, "Consistent Overhead Byte Stuffing," IEEE/ACM Transactions on Networking, vol. 7, no. 2, pp. 159-172, 1999, doi: `10.1109/90.769765`.
- J. Dizdarevic, F. Carpio, A. Jukan, and X. Masip-Bruin, "A Survey of Communication Protocols for Internet of Things and Related Challenges of Fog and Cloud Computing Integration," ACM Computing Surveys, vol. 51, no. 6, 2019, doi: `10.1145/3292674`.
- I. Ahmed, G. Jeon, and F. Piccialli, "From Artificial Intelligence to Explainable Artificial Intelligence in Industry 4.0: A Survey on What, How, and Where," IEEE Transactions on Industrial Informatics, vol. 18, no. 8, pp. 5031-5042, 2022, doi: `10.1109/TII.2022.3146552`.

## 9. Sources checked online

- Boiler SCADA paper metadata: https://colab.ws/articles/10.1109%2Facit62333.2024.10712619
- Low-cost ESP32/RPi/Node-RED/MQTT SCADA: https://www.researchgate.net/publication/347154219_Low-Cost_ESP32_Raspberry_Pi_Node-Red_and_MQTT_Protocol_Based_SCADA_System
- Oton & Iqbal CCECE DOI: https://doi.org/10.1109/CCECE53047.2021.9569100
- Smart-grid IoT-SCADA ML DOI: https://doi.org/10.1109/INTERCON52678.2021.9532668
- Hoque MQTT/time-series system: https://hossain-aimlab.info/publications/29/
- MQTT OASIS spec: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html
- Mosquitto JOSS: https://joss.theoj.org/papers/10.21105/joss.00265
- Cook IoT anomaly survey metadata: https://colab.ws/articles/10.1109%2Fjiot.2019.2958185
- COBS paper metadata: https://www.researchgate.net/publication/3334608_Consistent_overhead_byte_stuffing
- Connected vehicle survey metadata: https://trid.trb.org/View/1531607
- Digital twin security architecture metadata: https://portal.research.lu.se/en/publications/a-digital-twin-based-industrial-automation-and-control-system-sec
- Ahmed XAI Industry 4.0 metadata: https://colab.ws/articles/10.1109%2Ftii.2022.3146552
- Dizdarevic IoT protocol survey: https://upcommons.upc.edu/entities/publication/7e7efe3c-11f6-4313-8364-0e931b1ca751

