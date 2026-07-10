# Yoru V2 — Development Log

## Session 5 — 2026-07-09 (vape-aware specialist model trained + deployed)

- **Dataset** (user required Roboflow, rejected Kaggle): three CC BY 4.0
  Roboflow Universe sets merged into `datasets/smoking_vape_v1` — 18,905
  train images, classes cigarette/vape_device/smoke_vapour (tiara's "asap"
  class = Indonesian for smoke → smoke_vapour). Full citations, remapping,
  manual label audit (34/36 correct) and metrics in docs/DATASETS.md.
- **Training**: YOLOv8n, 49 epochs on the RTX 3050 Ti (paused/resumed once
  at user request). Test mAP50 0.832 (cigarette 0.821, vape_device 0.843);
  valid vape_device 0.916. ~4.5 ms/frame GPU inference.
- **Deployed**: best.pt → `smoking_vape_yolov8.pt` as `extra_model_path`
  for both CCTV pipelines, replacing the single-class cigarette model.
  Vape at mouth is now a real escalating detection (device class
  `vape_device` in C2); interim "possible vape" hint retained as fallback.
- Weights are git-ignored (`*.pt`); regenerate by re-running training or
  copy from `runs/smoking_vape_v1/weights/best.pt`.

## Session 4 — 2026-07-09 (vape problem, GPU repair, interim soft alert)

### The vape problem

A vape at the mouth is detected by COCO as `cell phone` → `mobile_phone`,
which is a C7 confounder — so vaping not only went undetected, it actively
suppressed escalation. No config fix exists (remapping phone→vape would
false-alarm on real phone calls). Proper fix: train the planned model with
a real `vape_device` class.

### Done

- **Interim soft alert** (commit 0c80d14): phone-like object held at the
  mouth for the persistence window → amber "possible vape (unverified)"
  chip on the dashboard; never announces/dispatches/emails. Plus
  `confounder_override_confidence` (0.75): a high-confidence specialist
  cigarette detection is no longer blocked by a phone/pen near the face.
- **NVIDIA driver repaired**: root cause was Ubuntu HWE kernel 6.8 needing
  gcc-12 while system gcc was 11 → nvidia-dkms-580 module build failed →
  userspace 580.159 vs old loaded module 580.95 mismatch. Fix: gcc-12
  installed and made default (update-alternatives), DKMS rebuilt +
  installed, reboot. Verified: nvidia-smi OK, torch.cuda available
  (RTX 3050 Ti, 4GB, CUDA 13.0). Detection restored to 5 fps per camera
  (was lowered to 3 for CPU).

### Next (in progress)

Train the 3-class specialist (`cigarette`, `vape_device`, `smoke_vapour`)
on the RTX 3050 Ti using public datasets, replacing cigarette_yolov8.pt
as `extra_model_path`. Then vape at mouth = real escalation.

## Session 3 — 2026-07-09 (PA announcements, second CCTV, map colors)

### Fixed / built

- **Silent PA root cause**: discovery routed through the robot's FastDDS
  server, so with the Pi off the laptop's nodes never discovered each
  other. `start_server.sh` now falls back to local discovery when the
  robot is unreachable ("STANDALONE mode"); sim mode is always local.
- **Voice**: espeak-ng installed → announcements are dynamic (speak the
  camera name); pa/direct mp3 regenerated with the V2 message (gTTS) as
  fallback. **Test Announcement button** on the dashboard Control screen
  exercises the real PA path and reports listening audio nodes.
- **Second CCTV**: Logitech C920 (/dev/video2) as full cctv2 pipeline —
  dual-model YOLO, confirmation (room `camera_2`), FSM/emailer/camera-spot
  wiring, live view on the Cameras screen (3 feeds). Both cameras
  confirmed working live. process_hz 5→3 per camera (CPU ~8→~5 cores).
- **Map colors**: dashboard map renders walls/edges white, ground grey.

### Notes

- Old leaked PAT finally revoked; new fine-grained token in gh
  (`~/.config/gh/hosts.yml`). Ubuntu's gh 2.4.0 credential helper doesn't
  feed git, so pushes use the token explicitly.
- Pre-existing broken `nvidia-driver-580` dpkg state on the laptop (also
  why YOLO runs on CPU) — untouched, fix someday.
- **Pi is stale**: re-run `./deploy_to_pi.sh` before the next robot
  session (needs sessions 3 changes).

### Next steps

1. Map the real room (robot + laptop), mark `cctv1` and `cctv2` spots.
2. Full hardware escalation test with both cameras.
3. Polish: email/incident status in dashboard, unique admin password,
   6-class model training.

## Session 2 — 2026-07-07 (real hardware bring-up on the Pi)

### Hardware as actually wired (differs from V1 plan)

Pi 4 ── ribbon ── HQ Camera (IMX477); USB ── RPLIDAR A1 (CP2102, ttyUSB0);
USB ── **Arduino Nano Every** (ttyACM0) ── L298N ── motors + quadrature
encoders. Wiring follows the ROSArduinoBridge standard map (same as
github.com/sushanthsujeerkumar/Astra_Real_robot). Wheels Ø65mm × 25mm,
track 32cm (measured).

### What was built

- **firmware/yoru_motor_bridge/**: ROSArduinoBridge port for the Nano
  Every (ATmega4809) — the stock ATmega328 PCINT encoder ISRs replaced
  with attachInterrupt(); same 57600-baud e/m/o/r/u protocol, onboard
  PID @30Hz, 2s auto-stop. Flash from the Pi:
  `arduino-cli compile|upload -b arduino:megaavr:nona4809` (arduino-cli
  in ~/.local/bin).
- **arduino_driver_node** (yoru_core): serial bridge replacing the GPIO
  l298n_driver_node in real_robot.launch.py; same topic contract
  (twist_mux output in, /odom + TF out), kinematics + odometry on the Pi.
- Encoder polarity fixed in firmware (forward was counting negative on
  both sides — bench-verified with single-wheel pulses).
- **enc_counts_per_rev = 1965**, re-measured by hand-rotating the wheels
  one revolution (left 1959 / right 1977; an earlier session measured
  ~3166 — first hand-turns were over-rotated; ≈11PPR × 4 × ~45:1 gearbox).
- Configs updated to measured chassis: wheel_radius 0.0325, separation
  0.32 (yoru_real.yaml, xacro, sim controller + gazebo diff_drive).

### Verified working (2026-07-07, on the robot)

/scan 6.8Hz, /camera/image_raw 17.3Hz (IMX477 via camera_ros), /odom
17.6Hz, slam_toolbox mapping, Nav2 up. Drive test via cmd_vel_joy:
0.1m/s for 2s → odom +0.233m, lateral drift 0.25mm.

### Next steps

1. Map the real room: laptop `./start_server.sh`, dashboard Setup screen,
   drive around, Save Map, mark the camera spot.
2. Re-check camera calibration warning (no imx477 yaml — harmless, but
   calibrate if the detector needs undistorted frames).
3. Full hardware escalation test (CCTV smoking → PA → robot dispatch).

## Session 1 — 2026-07-02 (project built from zero to working sim + real detection)

### What was decided

- **Yoru V2 = rebuild of V1** (`~/dock_ws` / github.com/deshwind/yoru_robot, the
  MSc dissertation robot). Reuse V1's proven core, put the new effort into a
  GUI-driven workflow. **~/dock_ws stays untouched.**
- **Web dashboard** (not a desktop app) is the admin GUI.
- Robot navigates to the **marked camera spot** (clicked on the map in the
  dashboard), replacing V1's pixel→map homography calibration.
- Hardware unchanged from V1: Pi 4, L298N + encoders, RPLIDAR, Pi Camera,
  speaker, PS4 pad.

### What was built

- Packages renamed/copied: `dockbot→yoru_base`, `compliance_core→yoru_core`,
  `compliance_bringup→yoru_bringup`.
- **camera_target_node** (new): confirmed smoking events resolve to the
  camera's marked pose from `maps/cameras.json` (hot-reloaded on change).
- **Dashboard V2**: first-run admin password setup (PBKDF2 hash in
  `data/admin.json`, nothing in configs), Setup screen (WASD keyboard teleop,
  Save Map button, click-to-mark camera spots), Cameras screen (live YOLO
  debug view + robot camera), plus V1's Control/Map/History.
- **Audio split**: laptop (`pa_audio_node`) speaks the PA announcement;
  robot (`robot_audio_node`) speaks the close-range final warning.
- **Launches**: `sim_full.launch.py` (one command), `sim.launch.py`
  (robot-side sim), `real_robot.launch.py` (Pi), `server.launch.py`
  (laptop, `sim:=true` to pair with sim). `mode:=auto` boots mapping until
  `maps/main_map.yaml` exists, then localization.
- **Scripts**: `start_sim.sh` (one command; `robot` arg = robot-side only),
  `start_server.sh [sim]`, `start_robot.sh` (Pi), `deploy_to_pi.sh`,
  `setup_pi.sh`, `connect_pi.sh`, `secrets.env(.example)`.
- **Dual-model detection**: YOLO node runs stock `yolov8n.pt` (persons) +
  `cigarette_yolov8.pt` (from `~/src3/models`, single class 'cigarette') on
  the same frame, merged into one detection array. Wired into
  `yoru_real.yaml` (laptop webcam = CCTV 1).

### Bugs found and fixed along the way

| Bug | Root cause | Fix |
|---|---|---|
| Robot invisible in Gazebo (only wheels/lidar) | `GAZEBO_MODEL_PATH` was hardcoded to dock_ws in `.bashrc`; V2 mesh unreachable | `gazebo_ros` export in `yoru_base/package.xml` |
| Login/setup screens ping-pong | Background polls fired before auth; every 401 forced the sign-in screen | Polls run only in-app; 401 only bounces when in-app |
| Robot drives through walls | Chassis collision was the decorative STL trimesh (ODE trimesh contacts unreliable) | Box collision; verified by driving into the east wall (pinned at x=5.81 vs wall face 5.92) |
| Whole launch aborts on URDF | launch parsed robot_description as YAML; any ": " kills it | `ParameterValue(value_type=str)` in rsp.launch.py |
| No incident email | No Gmail app password configured (V1's leaked one deliberately dropped) | New app password in git-ignored `secrets.env`; SMTP verified + test email delivered |

### Security actions

- V1's Gmail app password was hardcoded in a **public** repo → replaced by
  `COMPLIANCE_EMAIL_PASSWORD` env var via `secrets.env` (git-ignored).
- Admin password is a salted PBKDF2 hash created on first run
  (currently set to the V1 default — change before demos).
- GitHub fine-grained PAT was pasted in chat and used for the initial push —
  **must be revoked** (GitHub → Settings → Developer settings).

### Verified working (2026-07-02)

Simulation end-to-end: mapping → Save Map → mark camera spots → relaunch →
smoking scenario → PA announcement → robot drives to the cctv1 spot →
direct warning → incident logged + evidence email. Dual-model detection
verified on a test video. Repo pushed to github.com/deshwind/Yoru_bot_V2.

### Next steps (priority order)

1. **Live webcam test** of real cigarette detection: `./start_server.sh`,
   Cameras screen, cigarette near mouth → PA fires. Tune
   `extra_confidence_threshold` / `persistence_frames` if needed.
2. **Real robot bring-up**: `./connect_pi.sh` → `setup_pi.sh` →
   `./deploy_to_pi.sh` → map a real room → mark the real camera spot →
   full hardware escalation. Check L298N pins, RPLIDAR port, Pi cam overlay.
3. Polish: dashboard email/incident status, change admin password, battery
   publisher on the Pi (or drop the field), optional visual mesh rescale,
   second camera via RTSP, train the full 6-class model
   (`src/yoru_core/training/`).
