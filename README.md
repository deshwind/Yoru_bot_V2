# Yoru Bot V2 — CCTV-Triggered Smoking-Compliance Robot

ROS 2 Humble. A laptop acts as the **CCTV cameras + server**: it watches for
smoking and vaping with YOLO, announces *"Smoking is not allowed"* through
its speakers, and if the person carries on it dispatches the **autonomous
robot** (Raspberry Pi 4, RPLIDAR, Pi camera) to the spot marked for that
camera on the map. The robot repeats a direct warning three times, photos
are captured and emailed to the admin via Gmail, and the incident is logged.

Everything is managed from a **password-protected web dashboard** served by
the laptop — first-run password setup, keyboard mapping drive, marking
camera and base spots on the map, live CCTV/robot views, incident history.

## Quick start — simulation (one command)

```bash
./start_sim.sh
```

That starts everything: Gazebo (two-room world) + SLAM/Nav2 + RViz + CCTV
smoking detection + PA voice + escalation FSM + the admin dashboard, which
opens at http://localhost:8080.

Prefer the two-terminal split that mirrors the real deployment?

```bash
./start_sim.sh robot     # Terminal 1: robot side only
./start_server.sh sim    # Terminal 2: server side
```

**First run** (no saved map yet — the sim boots in *mapping* mode):

1. The dashboard asks you to **create the admin password** (stored as a
   salted hash in `data/admin.json`, never in a config file).
2. It lands on the **Setup** screen: drive the robot with **W A S D /
   arrow keys** (or the PS4 pad) until the two rooms are mapped.
3. Press **Save Map**.
4. Press **Add camera spot**, click where the robot should stand for
   `cctv1` (room A, east) and drag towards where it should face. Repeat
   for `cctv2` (room B, west). **Mark base spot** sets the parking pose
   used by *Return to Base*.
5. Restart both terminals — with a map saved, everything now boots in
   *localization* mode and the robot is on duty.

**On duty**: the scenario publisher injects a smoking event in room A after
~30 s. The server announces the PA warning; the smoking continues, so the
robot drives to the `cctv1` spot, speaks the final warning, and the
incident is logged (and emailed, if configured).

## Real robot — laptop + Raspberry Pi (same Wi-Fi)

One-time Pi setup:

```bash
./connect_pi.sh <pi-ip> <user>      # check the link
scp setup_pi.sh <user>@<pi-ip>: && ssh <user>@<pi-ip> ./setup_pi.sh
./deploy_to_pi.sh <pi-ip> <user>    # sync sources + build on the Pi
```

Daily operation:

```bash
# On the Pi — background, survives the SSH session closing:
ssh <user>@<pi-ip> 'cd ~/Yoru_bot_V2 && setsid nohup ./start_robot.sh \
    > /tmp/robot.log 2>&1 < /dev/null & echo starting'

# On the laptop:
./start_server.sh
```

`./start_robot.sh` boots in mapping mode until a map exists on the Pi, then
in localization mode. Watch it with `ssh <user>@<pi-ip> 'tail -f /tmp/robot.log'`,
stop it with `ssh <user>@<pi-ip> 'pkill -f "ros2 launch yoru_bringup"'`.

### Networking: DDS discovery server

University/corporate Wi-Fi blocks the multicast that ROS 2 normally uses to
find peers, so discovery runs through a **FastDDS discovery server hosted on
the robot** (`start_robot.sh` starts it automatically). Both machines connect
to it as super clients via `ros_network.env` — **if the Pi's IP changes,
update `ROS_DISCOVERY_SERVER` there on both machines**. Symptom of a stale
IP: each machine works alone, but the dashboard shows no map, no robot
camera, and the joystick does not drive the robot.

### Mapping a new area

Dashboard Setup screen → **Reset Map (new area)**: deletes the map and camera
spots on both machines and restarts the robot into mapping mode. Then drive
slowly (gentle turns give the cleanest scans), **Save Map**, copy it to the
robot, and restart:

```bash
scp ~/Yoru_bot_V2/maps/main_map.* <user>@<pi-ip>:~/Yoru_bot_V2/maps/
```

Maps are git-ignored — they move by `scp`, not by git. Re-mark the camera
and base spots afterwards: old spots hold coordinates in the *old* map's
frame.

## The escalation flow

| Stage | What happens |
|---|---|
| MONITORING | CCTV + YOLO: person + cigarette/vape confirmed for several seconds |
| PA_WARNING | Laptop speakers: “Attention. Smoking detected in camera 1…” — 3 s grace period |
| APPROACH | Still smoking → robot drives (Nav2) to the marked camera spot |
| DIRECT_WARNING | Robot speaker: final warning ×3; photos captured (CCTV frame + robot close-up) |
| LOGGING | Incident logged to `~/compliance_robot_logs/incidents.jsonl` + Gmail evidence email |

Stopping at any stage = “complied”, escalation resets. Admin joystick and
the dashboard e-stop always override. **Return to Base** sends the robot to
the marked base spot.

### Obstacle avoidance

Two independent layers:

- **Nav2 costmaps** — the lidar feeds a local costmap at 5 Hz; the planner
  steers around obstacles (`robot_radius` 0.22 m, `inflation_radius` 0.55 m).
- **FSM emergency stop** — anything closer than `obstacle_stop_distance`
  (0.35 m) for `obstacle_debounce_duration` (0.4 s) halts the approach
  outright. The debounce stops a single stray reading from aborting a run.

The lidar sees the robot's own frame at ~0.18 m, so both layers ignore
returns inside `scan_ignore_radius` (0.2 m); slam_toolbox's
`min_laser_range` (0.3 m) keeps those self-hits out of the map. The lidar
scans one horizontal plane: objects below or above it (low cables,
overhanging shelves) and glass/mirrors are not reliably detected.

## Email evidence (Gmail)

```bash
cp secrets.env.example secrets.env     # then edit it
```

Put your Gmail **app password** in `secrets.env`
(Google Account → Security → App passwords). `start_server.sh` sources it;
sender/recipient are in `src/yoru_bringup/config/yoru_real.yaml`.

## Detection (two models per frame)

On real hardware the YOLO node runs **two models on every CCTV frame** and
merges the detections:

- `model_path: yolov8n.pt` — persons (stock COCO model, auto-downloads)
- `extra_model_path: smoking_vape_yolov8.pt` — the trained 3-class
  specialist (`cigarette`, `vape_device`, `smoke_vapour`; test mAP50 0.83).
  Weights are git-ignored — keep a copy at the workspace root; training and
  dataset citations are in `src/yoru_core/training/` and `docs/DATASETS.md`.

The confirmation node then requires the device near the person's mouth
region for several consecutive frames (criteria C1–C7) before anything is
announced. A missed frame decays the persistence count instead of resetting
it, and a new SORT track ID inherits the camera's ongoing violation, so
normal hand movement does not restart the escalation.

**Sensitivity**: `device_confidence` and `confounder_override_confidence`
are both **0.3** — any vape/cigarette detection at 30 % or better escalates,
even when COCO simultaneously calls the object a phone near the face (C7
confounder). This is deliberately permissive for demos; raise both values if
false alarms appear.

---

# Technical reference

Everything below is the detail needed to describe or reproduce the system:
models, datasets, training, the decision algorithms, and the tuned
parameters. Dataset licences and the label-quality audit are in
[`docs/DATASETS.md`](docs/DATASETS.md); the chronological build history,
including every bug and its root cause, is in
[`docs/DEVLOG.md`](docs/DEVLOG.md).

## 1. Perception

### 1.1 Models

Two YOLOv8 models run on **every** CCTV frame and their detections are
merged into one array (`yolo_detector_node`):

| Role | Model | Classes | Source |
|---|---|---|---|
| People | **YOLOv8n** (stock COCO weights, auto-downloads) | `person` (+ COCO classes remapped for C7 confounders) | Ultralytics, AGPL-3.0 |
| Devices | **YOLOv8n specialist**, `smoking_vape_yolov8.pt` | `cigarette`, `vape_device`, `smoke_vapour` | Trained for this project (below) |

Frames are processed at `process_hz: 5.0` per camera at `input_size: 640`.
Two CCTV pipelines run in parallel (`cctv1` = laptop webcam, `cctv2` =
Logitech C920), each with its own detector, tracker, confirmation node and
camera spot.

### 1.2 Training datasets

Three Roboflow Universe datasets, all **CC BY 4.0**, merged into
`datasets/smoking_vape_v1` in YOLO format:

| # | Dataset (workspace) | Version | Link | Base images | Classes used |
|---|---|---|---|---|---|
| 1 | Cigarette Vape Detection (`takoyati`) | v14 | https://universe.roboflow.com/takoyati/cigarette-vape-detection/dataset/14 | 5,774 | `cigarette`, `vape` |
| 2 | vaping (`tiara-fb7pp` / `vaping-ulrul`) | v13 | https://universe.roboflow.com/tiara-fb7pp/vaping-ulrul/dataset/13 | 2,300 | `vape-pod`, `asap` |
| 3 | Vape Dataset (`vape-dataset`) | v1 | https://universe.roboflow.com/vape-dataset/vape-dataset/dataset/1 | 815 | `Vape` |

Class remapping into the project vocabulary (`asap` is Indonesian for
"smoke" — exhaled clouds):

| Source class | Project class (id) |
|---|---|
| takoyati `cigarette` | `cigarette` (0) |
| takoyati `vape`, tiara `vape-pod`, vape-dataset `Vape` | `vape_device` (1) |
| tiara `asap` | `smoke_vapour` (2) |

Merged totals as trained (including the augmentations published with each
dataset version):

| Split | Images | `cigarette` boxes | `vape_device` boxes | `smoke_vapour` boxes |
|---|---|---|---|---|
| train | 18,905 | 7,077 | 14,262 | 3,239 |
| valid | 1,779 | 651 | 1,231 | 261 |
| test | 658 | 322 | 555 | 0 |

**Label-quality audit**: 36 randomly sampled annotated images were rendered
with their boxes and inspected manually — 34/36 clearly correct, 2
tiny/ambiguous, 0 clearly mislabelled.

`datasets/` and `*.pt` weights are git-ignored; re-fetch the sources with a
free Roboflow API key using the links above.

### 1.3 Training run

| Setting | Value |
|---|---|
| Architecture | YOLOv8n (nano) |
| Epochs | **49** (early-stopped, `patience: 20`; script default is 100) |
| Image size | 640 × 640 |
| Batch size | 16 |
| Hardware | NVIDIA RTX 3050 Ti (4 GB, CUDA 13.0) |
| Inference cost | ~4.5 ms/frame on GPU |
| Script | `src/yoru_core/training/train_and_evaluate.py` |

Reproduce with:

```bash
python3 src/yoru_core/training/train_and_evaluate.py \
    --data datasets/smoking_vape_v1/data.yaml --epochs 100 --device 0
```

### 1.4 Results

| Split | mAP50 overall | `cigarette` | `vape_device` | `smoke_vapour` |
|---|---|---|---|---|
| test (held out) | **0.832** | 0.821 | 0.843 | — (no test boxes) |
| valid | 0.726 | 0.839 | 0.916 | 0.423 |

The test split contains no `smoke_vapour` instances, so that class is
evaluated on validation only. `smoke_vapour` is used **solely** as C5
supporting evidence and can never trigger an escalation by itself.

## 2. Tracking — SORT

`sort_tracker.py` implements SORT (Simple Online and Realtime Tracking): a
**constant-velocity Kalman filter** per bounding box, with greedy IoU
association between predictions and new detections.

| Parameter | Value |
|---|---|
| `iou_threshold` | 0.3 |
| `max_missed_frames` | 15 |
| `min_hits` | 1 |
| Track ID prefix | `c1_` / `c2_` (per camera) |

Track IDs give criterion C6 and let the FSM follow one individual. Because
SORT reassigns IDs when a person or device moves sharply, the confirmation
and FSM layers both tolerate ID churn (§3.2).

## 3. Decision layer

### 3.1 Event confirmation (criteria C1–C7)

`event_confirmation_node` is the decision gate. Per person track:

| Criterion | Test |
|---|---|
| C1 person | person confidence > `person_confidence` (0.7) |
| C2 device | cigarette/vape confidence > `device_confidence` (0.3) |
| C3 proximity | device overlaps the mouth region (upper 40 % of the person box) or IoU > `proximity_iou` (0.05) |
| C4 persistence | C1∧C2∧C3 held for `persistence_frames` (5) consecutive frames |
| C5 support | optional evidence: `smoke_vapour` (0.3), `hand_mouth_gesture` (0.2), `hand_face` (0.1) |
| C6 tracking | same SORT track ID throughout |
| C7 FP risk | `pen` / `mobile_phone` / `straw` near the mouth ⇒ high risk |

Scoring:

```
confidence = 0.4·device + 0.3·proximity + 0.2·persistence + 0.1·support
```

- **confirmed** — `confidence ≥ 0.6` ∧ C1 ∧ C2 ∧ C4 ∧ risk ≠ high
- **uncertain** — `0.4 ≤ confidence < 0.6` (dashboard only, no escalation)
- **rejected** — below 0.4, discarded silently

**C7 override**: a specialist detection at or above
`confounder_override_confidence` (0.3) overrides the confounder veto —
without it, COCO labelling a vape at the mouth as `cell phone` permanently
blocked confirmation (status `uncertain`, `C7=high`). This was the observed
cause of non-dispatch during live testing.

### 3.2 Robustness to track-ID churn

- A frame failing C1–C3 **decrements** the persistence count instead of
  zeroing it, so motion blur or brief occlusion does not restart C4.
- A new track ID inherits the room's ongoing violation start time, so the
  FSM's confirm window survives re-identification.
- During escalation the target stays active while *any* confirmed event
  keeps arriving from the same camera, so the robot is not called off
  mid-approach by an ID change.

### 3.3 Escalation FSM

`compliance_fsm_node` — five graduated stages plus safety overrides:

| Stage | Trigger | Tuned duration |
|---|---|---|
| S0 MONITORING | confirmed event must persist | `monitor_confirm_duration` 5.0 s |
| S1 PA_WARNING | laptop PA announcement, grace period | `pa_warning_duration` 3.0 s |
| S2 APPROACH | Nav2 drives to the camera spot | `approach_timeout` 90 s |
| S3 DIRECT_WARNING | robot speaks on arrival | 3 repeats × 8 s (`direct_warning_duration` 30 s cap) |
| S4 LOGGING | incident written + evidence email | `logging_duration` 2.0 s |
| SAFE_STOP | obstacle inside 0.35 m for 0.4 s | `safe_stop_duration` 3.0 s |

Overrides: compliance reset (`compliance_clear_duration` 10 s of no
violation ⇒ "complied"), target loss (`target_lost_timeout` 8 s), per-person
`cooldown_duration` 60 s, admin pause, dashboard e-stop. The e-stop
publishes on `cmd_vel_tracker`, which outranks Nav2 in twist_mux
(joystick 100 > tracker 20 > navigation 10).

## 4. Navigation and mapping

| Component | Choice / value |
|---|---|
| SLAM | `slam_toolbox` async, Ceres solver (SPARSE_NORMAL_CHOLESKY, SCHUR_JACOBI) |
| Map resolution | 0.05 m/cell |
| Laser range used | 0.3 – 12.0 m (A1 real range; 0.3 excludes robot self-hits) |
| Scan linking | `minimum_travel_distance` 0.2 m, `minimum_travel_heading` 0.25 rad |
| Localization | AMCL, differential motion model, 500–2000 particles, auto initial pose at map origin |
| Global planner | NavFn (`nav2_navfn_planner/NavfnPlanner`) |
| Local planner | DWB (`dwb_core::DWBLocalPlanner`), `sim_time` 1.7 s |
| Speed limits | `max_vel_x` 0.26 m/s, `max_vel_theta` 1.0 rad/s |
| Goal tolerance | 0.25 m / 0.25 rad |
| Costmap layers | local: voxel + inflation; global: static + obstacle + inflation |
| Footprint | `robot_radius` 0.22 m, `inflation_radius` 0.55 m |

Odometry is wheel-encoder based (`arduino_driver_node`), publishing
`odom → base_link` at ~18 Hz; AMCL supplies `map → odom`.

## Packages

| Package | Contents |
|---|---|
| `yoru_base` | Robot base: URDF/xacro, ros2_control (sim), SLAM + Nav2 configs, joystick, RPLIDAR |
| `yoru_core` | All nodes: YOLO detector, tracker, event confirmation, camera target, FSM, nav goal sender, audio, dashboard, emailer, logger, Arduino motor bridge, map reset |
| `yoru_bringup` | Worlds, parameter files, RViz config, the launch files |
| `firmware/` | `yoru_motor_bridge` — Arduino sketch for the Nano Every |

### Launch files

| Launch | Runs on | Started by |
|---|---|---|
| `sim_full.launch.py` | laptop | `./start_sim.sh` (one command: sim + server) |
| `sim.launch.py` | laptop | `./start_sim.sh robot` (robot side only) |
| `real_robot.launch.py` | Raspberry Pi | `./start_robot.sh` |
| `server.launch.py` | laptop | `./start_server.sh [sim]` |

Both `sim` and `real_robot` support `mode:=auto|mapping|localization`
(`auto` = mapping until `maps/main_map.yaml` exists).

## Hardware (robot)

Raspberry Pi 4 (Ubuntu 22.04 + ROS 2 Humble base), **Arduino Nano Every** →
L298N → DC motors with quadrature encoders, RPLIDAR A1 (USB), Pi HQ Camera
(IMX477, ribbon), USB speaker, PS4 pad.

The Pi does **not** drive the L298N over GPIO. It talks to the Nano Every
over USB serial (57600 baud) running `firmware/yoru_motor_bridge` — a
ROSArduinoBridge port that does PWM, quadrature counting and a 30 Hz PID
onboard, with a 2 s auto-stop dead-man. `arduino_driver_node` on the Pi
handles kinematics and odometry. (`l298n_driver_node` is the legacy
direct-GPIO driver, kept for reference.)

Flash the firmware from the Pi:

```bash
arduino-cli compile -b arduino:megaavr:nona4809 firmware/yoru_motor_bridge
arduino-cli upload -p /dev/ttyACM0 -b arduino:megaavr:nona4809 \
    firmware/yoru_motor_bridge
```

Note the board FQBN is `nona4809`, **not** `nanoevery`.

### Measured constants

Set in the `arduino_driver_node` section of `yoru_real.yaml`, and mirrored in
the URDF/sim configs:

| Constant | Value |
|---|---|
| `enc_counts_per_rev` | 1965 (measured by hand rotation) |
| `wheel_radius` | 0.0325 m (65 mm wheels) |
| `wheel_separation` | 0.32 m (measured centre-to-centre) |

If odometry drifts or maps come out smeared, re-verify `enc_counts_per_rev`
first — it scales every distance the robot believes it has travelled.

### Robot voice

The robot speaks with **Piper** neural TTS (`voices/en_GB-alba-medium.onnx`,
git-ignored). Without piper installed it falls back to espeak-ng, then to
pre-generated audio files. Install on the Pi with `pip install --user piper-tts`
plus a voice model from the Rhasspy piper-voices release.

## V2 vs V1 (yoru_robot)

- **Camera spots instead of homography calibration**: the robot drives to
  the spot you mark per camera in the dashboard (`maps/cameras.json`,
  hot-reloaded — no restarts, no calibration).
- **First-run password setup** in the GUI; no passwords or app passwords in
  config files (V1's leaked credentials are dead — revoke them!).
- **Dashboard Setup screen**: keyboard teleop mapping, Save Map, Reset Map,
  camera- and base-spot marking, setup checklist.
- **Cameras screen**: live MJPEG YOLO debug views + robot onboard camera.
- **Split audio**: laptop speaks the PA announcement, robot speaks the
  close-range warning (three times).
- **Arduino motor bridge** replaces V1's direct-GPIO L298N driver.
- Same proven base as V1: SLAM/Nav2 tuning, SORT tracking, C1–C7
  confirmation, incident logger/emailer, PS4 joystick.
