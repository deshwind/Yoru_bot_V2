#!/usr/bin/env bash
# SIMULATED ROBOT - Terminal 1 of the simulation test.
#   Gazebo (two-room world with CCTV cameras) + SLAM/AMCL + Nav2 + RViz.
# Then in Terminal 2 run the server (perception + FSM + dashboard):
#   ./start_server.sh sim
#
# Usage:
#   ./start_sim.sh                    # auto: mapping if no saved map yet
#   ./start_sim.sh mode:=mapping      # force re-mapping
#   ./start_sim.sh gui:=false rviz:=false
set -e
cd "$(dirname "$0")"

source /opt/ros/humble/setup.bash
source ./ros_network.env

# Build once if the workspace has not been built yet
if [ ! -f install/setup.bash ]; then
    echo "First run: building workspace..."
    colcon build --symlink-install
fi
source install/setup.bash

echo ""
echo "============================================================"
echo "  Yoru V2 simulation (robot side)"
echo "  Now run the server in ANOTHER terminal:  ./start_server.sh sim"
echo "============================================================"
echo ""

exec ros2 launch yoru_bringup sim.launch.py "$@"
