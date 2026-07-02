# Yoru Bot V2 — CCTV-Triggered Smoking-Compliance Robot

ROS 2 Humble. A laptop acts as the **CCTV camera + server**: it watches for
smoking with YOLO, announces *"Smoking is not allowed"* through its speakers,
and if the person keeps smoking it dispatches the **autonomous robot**
(Raspberry Pi 4, RPLIDAR, Pi camera) to the spot marked for that camera on
the map. The robot delivers a direct warning, a photo is captured and
emailed to the admin via Gmail, and the incident is logged.

Everything is managed from a **password-protected web dashboard** served by
the laptop — first-run password setup, keyboard mapping drive, marking
camera spots on the map, live CCTV/robot views, incident history.

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
   for `cctv2` (room B, west).
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
# On the Pi (SSH):
cd ~/Yoru_bot_V2 && ./start_robot.sh    # auto: mapping if no map, else duty

# On the laptop:
./start_server.sh
```

First run is the same GUI workflow as the sim: create password → drive to
map (keyboard/joystick) → Save Map → mark the camera spot (`cctv1` = the
laptop webcam's area) → **re-run `./deploy_to_pi.sh`** (syncs the map to
the Pi) → restart `./start_robot.sh`.

## The escalation flow

| Stage | What happens |
|---|---|
| MONITORING | Laptop webcam + YOLO: person + cigarette confirmed for several seconds |
| PA_WARNING | Laptop speakers: “Attention. Smoking detected in camera 1. Smoking is not allowed here…” — grace period follows |
| APPROACH | Still smoking → robot drives (Nav2) to the marked camera spot |
| DIRECT_WARNING | Robot speaker: final warning; photos captured (CCTV frame + robot close-up) |
| LOGGING | Incident logged to `~/compliance_robot_logs/incidents.jsonl` + Gmail evidence email |

Stopping at any stage = “complied”, escalation resets. Admin joystick and
the dashboard e-stop always override.

## Email evidence (Gmail)

```bash
cp secrets.env.example secrets.env     # then edit it
```

Put your Gmail **app password** in `secrets.env`
(Google Account → Security → App passwords). `start_server.sh` sources it;
sender/recipient are in `src/yoru_bringup/config/yoru_real.yaml`.

## Packages

| Package | Contents |
|---|---|
| `yoru_base` | Robot base: URDF/xacro, ros2_control (sim), SLAM + Nav2 configs, joystick, RPLIDAR |
| `yoru_core` | All nodes: YOLO detector, tracker, event confirmation, camera target, FSM, nav goal sender, audio, dashboard, emailer, logger, L298N driver |
| `yoru_bringup` | Worlds, parameter files, RViz config, the three launch files |

### Launch files

| Launch | Runs on | Started by |
|---|---|---|
| `sim_full.launch.py` | laptop | `./start_sim.sh` (one command: sim + server) |
| `sim.launch.py` | laptop | `./start_sim.sh robot` (robot side only) |
| `real_robot.launch.py` | Raspberry Pi | `./start_robot.sh` |
| `server.launch.py` | laptop | `./start_server.sh [sim]` |

Both `sim` and `real_robot` support `mode:=auto|mapping|localization`
(`auto` = mapping until `maps/main_map.yaml` exists).

## V2 vs V1 (yoru_robot)

- **Camera spots instead of homography calibration**: the robot drives to
  the spot you mark per camera in the dashboard (`maps/cameras.json`,
  hot-reloaded — no restarts, no calibration).
- **First-run password setup** in the GUI; no passwords or app passwords in
  config files (V1's leaked credentials are dead — revoke them!).
- **Dashboard Setup screen**: keyboard teleop mapping, Save Map button,
  camera-spot marking, setup checklist.
- **Cameras screen**: live YOLO debug view + robot onboard camera.
- **Split audio**: laptop speaks the PA announcement, robot speaks the
  close-range warning.
- Same proven base as V1: L298N driver, SLAM/Nav2 tuning, SORT tracking,
  C1–C7 confirmation, incident logger/emailer, PS4 joystick.

## Hardware (robot)

Raspberry Pi 4 (Ubuntu 22.04 + ROS 2 Humble base), L298N + DC motors with
encoders, RPLIDAR, Pi Camera Module, USB speaker. Wiring/pins in the
`l298n_driver_node` section of `yoru_real.yaml`.
