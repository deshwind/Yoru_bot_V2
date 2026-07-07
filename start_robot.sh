#!/usr/bin/env bash
# REAL ROBOT - run this ON the Raspberry Pi (deployed with ./deploy_to_pi.sh).
# Runs: motors, RPLIDAR, Pi camera, robot speaker, SLAM/AMCL + Nav2 onboard.
# The laptop runs ./start_server.sh (both machines on the same Wi-Fi).
#
# Usage (on the Pi):
#   ./start_robot.sh                    # auto: mapping if no saved map yet,
#                                       #       localization once a map exists
#   ./start_robot.sh mode:=mapping      # force re-mapping
#   ./start_robot.sh camera:=usb        # USB webcam instead of the Pi camera
set -e
cd "$(dirname "$0")"

source /opt/ros/humble/setup.bash
source ./ros_network.env

# Campus Wi-Fi blocks multicast: discovery runs through a FastDDS discovery
# server hosted here on the robot (see ros_network.env). Start it if it
# isn't already running; it stays up across launch restarts.
if ! pgrep -f "fastdds discovery" > /dev/null; then
    nohup fastdds discovery -i 0 -p 11811 > /tmp/fastdds_discovery.log 2>&1 &
    sleep 1
    echo "[yoru] started FastDDS discovery server on :11811"
fi

if [ ! -f install/setup.bash ]; then
    echo "First run: building workspace (this takes a while on the Pi)..."
    colcon build --symlink-install
fi
source install/setup.bash

exec ros2 launch yoru_bringup real_robot.launch.py "$@"
