#!/usr/bin/env python3
"""Scripted end-to-end simulation demo (for recording a walkthrough).

Drives the whole Yoru workflow hands-free so it can be screen-recorded:

  1. sign in to the dashboard, take admin control (autonomy paused)
  2. drive both rooms with closed-loop waypoints -> SLAM builds the map
  3. Save Map through the dashboard API
  4. publish the CCTV camera spot the robot should answer alerts at
  5. resume autonomy -> the scenario publisher's smoking event escalates:
     confirmation -> PA warning -> Nav2 dispatch -> 3 direct warnings ->
     incident logged + evidence email
  6. print a timestamped timeline of every phase and FSM transition

Run it while ./start_sim.sh is up:
    python3 src/yoru_core/tools/sim_demo.py --password desh
"""

import argparse
import json
import math
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener

# Two-room world: doorway at x=0 (y -0.8..0.8), room A east, room B west.
# 'sweep' rotates in place so the lidar sees the whole room.
ROUTE = [
    ('sweep', None),
    ('goto', (2.5, 0.0)),
    ('goto', (4.0, 1.4)),
    ('goto', (4.0, -1.4)),
    ('sweep', None),
    ('goto', (2.0, 0.0)),
    ('goto', (0.0, 0.0)),
    ('goto', (-2.5, 0.0)),
    ('goto', (-4.0, 1.4)),
    ('goto', (-4.0, -1.0)),
    ('sweep', None),
    ('goto', (-2.0, 0.0)),
    ('goto', (0.0, 0.0)),
]
CAMERA_SPOT = {'id': 'cctv1', 'name': 'Room A camera',
               'x': 1.8, 'y': 0.0, 'yaw': 0.0}
# Dashboard sidebar buttons, in screen coordinates, so the recording can be
# walked through the screens with xdotool (order matches the sidebar).
NAV_ORDER = ['setup', 'control', 'cameras', 'map', 'history']


class SimDemo(Node):

    def __init__(self, base_url, password, nav=None):
        super().__init__('sim_demo')
        self.base_url = base_url.rstrip('/')
        # (x, y_of_first_button, spacing) or None to skip view switching
        self.nav = nav
        self.xdotool = shutil.which('xdotool')
        self.cmd_pub = self.create_publisher(Twist, '/cmd_vel_joy', 10)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.fsm_state = None
        self.create_subscription(String, '/compliance/fsm_status',
                                 self.fsm_cb, 10)
        self.create_subscription(String, '/compliance/incident_log',
                                 self.incident_cb, 10)
        self.incidents = []
        self.timeline = []
        self.t0 = time.monotonic()
        self.token = self.login(password)

    # ----------------------------------------------------------- utilities

    def stamp(self):
        el = time.monotonic() - self.t0
        return f'{int(el // 60):02d}:{int(el % 60):02d}'

    def mark(self, text):
        line = f'[{self.stamp()}] {text}'
        self.timeline.append(line)
        print(line, flush=True)

    def view(self, name):
        """Clicks a dashboard sidebar button so the recording walks through
        the screens (no effect when --nav is not given)."""
        if not self.nav or not self.xdotool or name not in NAV_ORDER:
            return
        x, y0, dy = self.nav
        y = y0 + NAV_ORDER.index(name) * dy
        subprocess.run([self.xdotool, 'mousemove', str(int(x)), str(int(y)),
                        'click', '1'], check=False)
        self.mark(f'  dashboard -> {name.upper()} screen')
        self.spin(1.5)

    def api(self, path, body=None):
        url = f'{self.base_url}{path}'
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data,
                                     headers={'X-Auth': self.token}
                                     if hasattr(self, 'token') else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            return {'error': f'HTTP {exc.code}'}
        except Exception as exc:  # noqa: BLE001 - report and continue
            return {'error': str(exc)}

    def login(self, password):
        req = urllib.request.Request(
            f'{self.base_url}/api/login',
            data=json.dumps({'password': password}).encode())
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)['token']

    def fsm_cb(self, msg):
        try:
            state = json.loads(msg.data).get('state')
        except ValueError:
            return
        if state != self.fsm_state:
            self.fsm_state = state
            self.mark(f'FSM -> {state}')

    def incident_cb(self, msg):
        try:
            self.incidents.append(json.loads(msg.data))
        except ValueError:
            return
        self.mark(f'INCIDENT LOGGED: {self.incidents[-1].get("outcome")}')

    def spin(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.05)

    def pose(self):
        try:
            tf = self.tf_buffer.lookup_transform('map', 'base_link',
                                                 rclpy.time.Time())
        except Exception:  # noqa: BLE001 - TF not ready yet
            return None
        q = tf.transform.rotation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        return tf.transform.translation.x, tf.transform.translation.y, yaw

    def drive(self, lin, ang):
        t = Twist()
        t.linear.x = lin
        t.angular.z = ang
        self.cmd_pub.publish(t)

    def stop(self):
        for _ in range(5):
            self.drive(0.0, 0.0)
            self.spin(0.05)

    # -------------------------------------------------------------- motion

    def goto(self, gx, gy, tol=0.28, timeout=45.0):
        """Proportional drive to a map waypoint (no Nav2 - this is the
        manual admin drive used for mapping)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            p = self.pose()
            if p is None:
                self.spin(0.2)
                continue
            x, y, yaw = p
            dx, dy = gx - x, gy - y
            dist = math.hypot(dx, dy)
            if dist < tol:
                break
            err = math.atan2(math.sin(math.atan2(dy, dx) - yaw),
                             math.cos(math.atan2(dy, dx) - yaw))
            if abs(err) > 0.5:            # turn on the spot first
                self.drive(0.0, max(-0.7, min(0.7, 1.5 * err)))
            else:
                self.drive(min(0.22, 0.5 * dist),
                           max(-0.5, min(0.5, 1.2 * err)))
            self.spin(0.05)
        self.stop()

    def sweep(self, duration=13.0, ang=0.55):
        """Rotate in place so the lidar sweeps the whole room."""
        end = time.monotonic() + duration
        while time.monotonic() < end:
            self.drive(0.0, ang)
            self.spin(0.05)
        self.stop()

    # ---------------------------------------------------------------- demo

    def run(self):
        self.mark('DEMO START - dashboard reachable, signing in')
        boot = self.api('/api/boot')
        self.mark(f'dashboard state: mapping_active={boot.get("mapping_active")}')

        # 1. admin control so the FSM cannot dispatch while we are mapping
        self.view('setup')
        self.api('/api/mode', {'paused': True})
        self.mark('PHASE 1 - admin control (autonomy paused for mapping)')
        self.spin(5.0)

        # 2. mapping drive
        self.mark('PHASE 2 - mapping drive: covering room A and room B')
        self.view('map')
        for kind, arg in ROUTE:
            if kind == 'sweep':
                self.mark('  lidar sweep (rotate in place)')
                self.sweep()
            else:
                self.mark(f'  driving to waypoint {arg}')
                self.goto(*arg)
            info = self.api('/api/map_info')
            self.mark(f'  map now {info.get("width")}x{info.get("height")} cells')
        self.stop()

        # 3. save the map
        self.mark('PHASE 3 - Save Map')
        self.view('setup')
        res = self.api('/api/save_map', {})
        self.mark(f'  save_map: {res.get("path") or res.get("error")}')
        self.spin(4.0)

        # 4. camera spot
        self.mark('PHASE 4 - marking the CCTV camera spot for room A')
        res = self.api('/api/cameras', {'cameras': [CAMERA_SPOT]})
        self.mark(f'  spots saved: {len(res.get("cameras", []))} '
                  f'(cctv1 at x={CAMERA_SPOT["x"]}, y={CAMERA_SPOT["y"]})')
        self.view('map')
        self.spin(5.0)

        # 5. back on duty - the smoking scenario escalates
        self.mark('PHASE 5 - resuming autonomy: robot is on duty')
        self.view('cameras')
        self.api('/api/mode', {'paused': False})

        deadline = time.monotonic() + 200.0
        showed_map = False
        while time.monotonic() < deadline and not self.incidents:
            # once the robot is dispatched, watch it drive on the map
            if self.fsm_state == 'APPROACH' and not showed_map:
                showed_map = True
                self.view('map')
            self.spin(1.0)
        if not self.incidents:
            self.mark('no incident within 200s (check the scenario publisher)')

        self.spin(3.0)
        stats = self.api('/api/incidents')
        self.mark(f'PHASE 6 - history: {stats.get("stats", {}).get("total")} '
                  'incident(s) in the log')
        self.view('history')
        self.spin(8.0)
        self.mark('DEMO COMPLETE')
        return self.timeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://localhost:8080')
    ap.add_argument('--password', default='desh')
    ap.add_argument('--timeline', default='')
    ap.add_argument('--nav', default='',
                    help='x,y_first,spacing of the dashboard sidebar buttons '
                         'in screen pixels - switches screens while recording')
    args = ap.parse_args()

    nav = None
    if args.nav:
        nav = tuple(float(v) for v in args.nav.split(','))

    rclpy.init()
    demo = SimDemo(args.url, args.password, nav=nav)
    try:
        timeline = demo.run()
    finally:
        demo.stop()
        demo.destroy_node()
        rclpy.try_shutdown()
    if args.timeline:
        with open(args.timeline, 'w', encoding='utf-8') as f:
            f.write('\n'.join(timeline) + '\n')
        print(f'timeline written to {args.timeline}')


if __name__ == '__main__':
    main()
