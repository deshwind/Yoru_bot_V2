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
