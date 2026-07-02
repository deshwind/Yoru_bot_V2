"""YOLO detector node (Yoru V2).

Detects objects from one of four camera sources (ROS topic / USB / RTSP /
video file) and publishes vision_msgs/Detection2DArray in pixel coordinates.

Supports TWO models per frame, merged into one detection array:
  model_path       : main model - stock yolov8n gives 'person' (plus
                     confounders like 'cell phone') via the COCO class map
  extra_model_path : optional specialist model whose class names are used
                     as-is - e.g. a single-class 'cigarette' detector.
                     Both run on the same frame so the event confirmation
                     node sees person + cigarette together.

The custom six-class model (person, cigarette, vape_device, smoke_vapour,
hand_mouth_gesture, hand_face) can replace both via 'model_path' +
use_coco_class_map: false once trained.
"""

import json

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

import cv2
from cv_bridge import CvBridge

# Map COCO names to the project class vocabulary (confounders feed C7).
COCO_CLASS_MAP = {
    'person': 'person',
    'cell phone': 'mobile_phone',
    'remote': 'mobile_phone',
    'toothbrush': 'pen',
    'fork': 'pen',
    'cup': 'straw',
    'bottle': 'straw',
}


class YoloDetectorNode(Node):

    def __init__(self):
        super().__init__('yolo_detector_node')

        self.declare_parameter('source_type', 'ros_topic')  # ros_topic|usb|rtsp|video
        self.declare_parameter('ros_topic', '/cctv/image_raw')
        self.declare_parameter('device_index', 0)
        self.declare_parameter('rtsp_url', '')
        self.declare_parameter('video_path', '')
        self.declare_parameter('model_path', 'yolov8n.pt')
        self.declare_parameter('confidence_threshold', 0.4)
        # Optional second model on the same frame (classes used as-is)
        self.declare_parameter('extra_model_path', '')
        self.declare_parameter('extra_confidence_threshold', 0.35)
        self.declare_parameter('input_size', 640)
        self.declare_parameter('process_hz', 5.0)
        self.declare_parameter('publish_debug_image', True)
        self.declare_parameter('detections_topic', '/compliance/detections')
        self.declare_parameter('debug_image_topic', '/compliance/debug_image')
        self.declare_parameter('use_coco_class_map', True)

        self.source_type = self.get_parameter('source_type').value
        self.conf_threshold = self.get_parameter('confidence_threshold').value
        self.input_size = int(self.get_parameter('input_size').value)
        self.use_coco_map = self.get_parameter('use_coco_class_map').value
        process_hz = self.get_parameter('process_hz').value

        self.bridge = CvBridge()
        self.model = None
        self.capture = None
        self.latest_frame = None

        det_topic = self.get_parameter('detections_topic').value
        self.det_pub = self.create_publisher(Detection2DArray, det_topic, 10)
        self.alert_pub = self.create_publisher(String, '/compliance/smoking_detected', 10)
        self.debug_pub = None
        if self.get_parameter('publish_debug_image').value:
            self.debug_pub = self.create_publisher(
                Image, self.get_parameter('debug_image_topic').value, 2)

        self._load_model()

        if self.source_type == 'ros_topic':
            topic = self.get_parameter('ros_topic').value
            self.create_subscription(Image, topic, self.image_callback, 2)
            self.get_logger().info(f'Camera source: ROS topic {topic}')
        else:
            self._open_capture()

        self.create_timer(1.0 / max(process_hz, 0.5), self.process_frame)
        self.get_logger().info(
            f'YOLO detector ready (model={self.get_parameter("model_path").value}, '
            f'source={self.source_type}, publishing {det_topic})')

    def _load_model(self):
        model_path = self.get_parameter('model_path').value
        try:
            from ultralytics import YOLO
            self.model = YOLO(model_path)
        except Exception as exc:  # noqa: BLE001 - report and run degraded
            self.get_logger().error(
                f'Could not load YOLO model "{model_path}": {exc}. '
                'Node stays alive but publishes no detections.')
            self.model = None

        self.extra_model = None
        extra_path = self.get_parameter('extra_model_path').value
        if extra_path:
            try:
                from ultralytics import YOLO
                self.extra_model = YOLO(extra_path)
                self.get_logger().info(
                    f'Extra model loaded: {extra_path} '
                    f'(classes: {list(self.extra_model.names.values())})')
            except Exception as exc:  # noqa: BLE001 - main model still runs
                self.get_logger().error(
                    f'Could not load extra model "{extra_path}": {exc}. '
                    'Continuing with the main model only.')

    def _open_capture(self):
        if self.source_type == 'usb':
            src = int(self.get_parameter('device_index').value)
        elif self.source_type == 'rtsp':
            src = self.get_parameter('rtsp_url').value
        elif self.source_type == 'video':
            src = self.get_parameter('video_path').value
        else:
            self.get_logger().error(f'Unknown source_type: {self.source_type}')
            return
        self.capture = cv2.VideoCapture(src)
        if not self.capture.isOpened():
            self.get_logger().error(f'Failed to open camera source: {src}')

    def image_callback(self, msg):
        self.latest_frame = (self.bridge.imgmsg_to_cv2(msg, 'bgr8'), msg.header)

    def process_frame(self):
        if self.model is None:
            return
        if self.source_type == 'ros_topic':
            if self.latest_frame is None:
                return
            frame, header = self.latest_frame
        else:
            if self.capture is None or not self.capture.isOpened():
                return
            ok, frame = self.capture.read()
            if not ok:
                if self.source_type == 'video':  # loop test videos
                    self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return
            header = None

        array = Detection2DArray()
        if header is not None:
            array.header = header
        else:
            array.header.stamp = self.get_clock().now().to_msg()
            array.header.frame_id = 'camera'

        alert_classes = []
        # Main model (COCO map filters it to the project vocabulary)
        self._detect(self.model, frame, self.conf_threshold,
                     self.use_coco_map, array, alert_classes, (0, 200, 0))
        # Specialist model, e.g. the cigarette detector (classes as-is)
        if self.extra_model is not None:
            extra_conf = self.get_parameter('extra_confidence_threshold').value
            self._detect(self.extra_model, frame, extra_conf,
                         False, array, alert_classes, (0, 0, 230))

        self.det_pub.publish(array)

        if alert_classes:
            alert = String()
            alert.data = json.dumps({
                'stamp': float(self.get_clock().now().nanoseconds) * 1e-9,
                'classes': alert_classes,
            })
            self.alert_pub.publish(alert)

        if self.debug_pub is not None:
            self.debug_pub.publish(self.bridge.cv2_to_imgmsg(frame, 'bgr8'))

    def _detect(self, model, frame, conf, use_coco_map, array, alert_classes,
                color):
        """Runs one model on the frame, appending detections to the shared
        array (and boxes to the debug frame) so both models' results arrive
        in a single message for the confirmation node."""
        results = model.predict(
            frame, imgsz=self.input_size, conf=conf, verbose=False)
        names = results[0].names
        for box in results[0].boxes:
            raw_name = names[int(box.cls[0])]
            if use_coco_map:
                class_id = COCO_CLASS_MAP.get(raw_name)
                if class_id is None:
                    continue
            else:
                class_id = raw_name

            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            det = Detection2D()
            det.header = array.header
            det.bbox.center.position.x = (x1 + x2) / 2.0
            det.bbox.center.position.y = (y1 + y2) / 2.0
            det.bbox.size_x = x2 - x1
            det.bbox.size_y = y2 - y1
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis.class_id = class_id
            hyp.hypothesis.score = float(box.conf[0])
            det.results.append(hyp)
            array.detections.append(det)
            if class_id in ('cigarette', 'vape_device'):
                alert_classes.append(class_id)

            if self.debug_pub is not None:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)),
                              color, 2)
                cv2.putText(frame, f'{class_id} {float(box.conf[0]):.2f}',
                            (int(x1), max(int(y1) - 5, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def main(args=None):
    rclpy.init(args=args)
    node = YoloDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
