#!/usr/bin/env bash
# SERVER (this laptop) - CCTV perception, PA announcements, escalation FSM,
# admin dashboard (auto-opens in the browser), incident log + Gmail email,
# admin joystick. The SAME command works for simulation and the real robot:
#
#   ./start_server.sh sim       # pair with ./start_sim.sh (other terminal)
#   ./start_server.sh           # pair with the real robot (./start_robot.sh on the Pi)
#   ./start_server.sh use_cctv2:=true   # extra args pass through to ros2 launch
set -e
cd "$(dirname "$0")"

source /opt/ros/humble/setup.bash
source ./ros_network.env
# Gmail app password etc. (git-ignored) - see secrets.env.example
[ -f secrets.env ] && source ./secrets.env

if [ ! -f install/setup.bash ]; then
    echo "First run: building workspace..."
    colcon build --symlink-install
fi
source install/setup.bash

# "./start_server.sh sim" is a friendly shortcut for sim:=true
ARGS=()
for a in "$@"; do
    if [ "$a" = "sim" ]; then ARGS+=("sim:=true"); else ARGS+=("$a"); fi
done

exec ros2 launch yoru_bringup server.launch.py "${ARGS[@]}"
