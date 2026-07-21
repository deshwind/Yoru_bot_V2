"""Return-to-base node.

Integrates the docking-prototype behaviour (archive/dock_scripts): when the
battery is low or a manual request arrives, navigate to the nearest base
(charging dock) pose. Waits for any active escalation to finish first and
asks the patrol node to pause via /compliance/base_request.

Triggers:
  /compliance/battery_level  (std_msgs/Float32, percent) below threshold
  /compliance/return_to_base (std_msgs/Bool) manual request

Base poses come from <maps_dir>/bases.json (hot-reloaded, same pattern as
camera spots): [{"id": "...", "name": "...", "x":, "y":, "yaw":}, ...],
written by the dashboard Map screen "Mark base spot" button. The static
'bases' parameter is a fallback for deployments with no bases.json yet.
"""

import json
import math
import os

import rclpy
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, String
from tf2_ros import Buffer, TransformListener


class ReturnToBaseNode(Node):

    def __init__(self):
        super().__init__('return_to_base_node')

        self.declare_parameter('bases', [0.0, 0.0, 0.0])
        self.declare_parameter('bases_file', '')
        self.declare_parameter('battery_threshold', 20.0)

        self.nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.fsm_state = 'MONITORING'
        self.requested = False
        self.navigating = False
        self.at_base = False
        self.goal_handle = None
        self.bases_from_file = None  # None until bases.json loads at least once
        self.file_mtime = 0.0

        self.base_request_pub = self.create_publisher(
            Bool, '/compliance/base_request', 10)
        self.status_pub = self.create_publisher(
            String, '/compliance/return_to_base_status', 10)

        self.create_subscription(Float32, '/compliance/battery_level',
                                 self.battery_callback, 10)
        self.create_subscription(Bool, '/compliance/return_to_base',
                                 self.request_callback, 10)
        self.create_subscription(String, '/compliance/fsm_status',
                                 self.fsm_callback, 10)
        self.create_timer(1.0, self.tick)

        self.reload_bases()
        n = len(self.bases()) // 3
        self.get_logger().info(f'Return-to-base ready ({n} base pose(s))')

    def reload_bases(self):
        """Re-reads bases.json when it changed on disk (mirrors cameras.json)."""
        path = self.get_parameter('bases_file').value
        if not path:
            return
        path = os.path.expanduser(path)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if mtime == self.file_mtime:
            return
        try:
            with open(path, encoding='utf-8') as f:
                entries = json.load(f)
            flat = []
            for b in entries:
                flat += [float(b['x']), float(b['y']), float(b.get('yaw', 0.0))]
            self.bases_from_file = flat
            self.file_mtime = mtime
            self.get_logger().info(
                f'Loaded {len(entries)} base spot(s) from {path}')
        except (ValueError, OSError, KeyError) as exc:
            self.get_logger().warn(f'Could not read {path}: {exc}')

    def bases(self):
        """bases.json spots once any exist, else the static param fallback."""
        if self.bases_from_file:
            return self.bases_from_file
        return self.get_parameter('bases').value

    def battery_callback(self, msg):
        if msg.data < self.get_parameter('battery_threshold').value and not self.requested:
            self.get_logger().warn(f'Battery {msg.data:.0f}% below threshold: '
                                   'returning to base')
            self.requested = True

    def request_callback(self, msg):
        self.requested = msg.data
        if not msg.data:
            self.at_base = False
            if self.navigating and self.goal_handle is not None:
                self.get_logger().info('Return-to-base cancelled by admin')
                self.goal_handle.cancel_goal_async()
                self.navigating = False
                self.publish_status('cancelled')

    def fsm_callback(self, msg):
        try:
            self.fsm_state = json.loads(msg.data).get('state', self.fsm_state)
        except ValueError:
            pass

    def robot_xy(self):
        try:
            tf = self.tf_buffer.lookup_transform('map', 'base_link', rclpy.time.Time())
            return tf.transform.translation.x, tf.transform.translation.y
        except Exception:  # noqa: BLE001
            return None

    def publish_status(self, state):
        msg = String()
        msg.data = json.dumps({'state': state})
        self.status_pub.publish(msg)

    def tick(self):
        active = self.requested and not self.at_base
        self.base_request_pub.publish(Bool(data=active))
        if not active or self.navigating:
            return
        if self.fsm_state != 'MONITORING':
            return  # never interrupt an active escalation

        robot = self.robot_xy()
        if robot is None:
            return

        self.reload_bases()
        bases = self.bases()
        nearest, best_d = None, float('inf')
        for i in range(len(bases) // 3):
            x, y, yaw = bases[3 * i], bases[3 * i + 1], bases[3 * i + 2]
            d = math.hypot(x - robot[0], y - robot[1])
            if d < best_d:
                nearest, best_d = (x, y, yaw), d
        if nearest is None:
            return

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(nearest[0])
        goal.pose.pose.position.y = float(nearest[1])
        goal.pose.pose.orientation.z = math.sin(nearest[2] / 2.0)
        goal.pose.pose.orientation.w = math.cos(nearest[2] / 2.0)

        if not self.nav_client.wait_for_server(timeout_sec=1.0):
            return

        self.get_logger().info(
            f'Returning to nearest base ({nearest[0]:.1f}, {nearest[1]:.1f}), '
            f'{best_d:.1f} m away')
        self.navigating = True
        self.publish_status('navigating')
        future = self.nav_client.send_goal_async(goal)
        future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        handle = future.result()
        if not handle or not handle.accepted:
            self.navigating = False
            self.publish_status('rejected')
            return
        self.goal_handle = handle
        handle.get_result_async().add_done_callback(self.result_callback)

    def result_callback(self, future):
        self.navigating = False
        if future.result().status == 4:  # SUCCEEDED
            self.at_base = True
            self.publish_status('at_base')
            self.get_logger().info('Arrived at base')
        else:
            self.publish_status('failed')


def main(args=None):
    rclpy.init(args=args)
    node = ReturnToBaseNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
