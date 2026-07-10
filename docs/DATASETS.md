# Training Datasets — Smoking/Vape Specialist Detector

The specialist detector (`cigarette`, `vape_device`, `smoke_vapour`) is
trained on three publicly available object-detection datasets from
**Roboflow Universe**, all published under the **CC BY 4.0** license.
They were merged into `datasets/smoking_vape_v1` (YOLO format) with the
class remapping documented below; no images were altered beyond the
augmentations already included in the published dataset versions.

## Sources

| # | Dataset | Version | Link | License | Contents used |
|---|---------|---------|------|---------|---------------|
| 1 | Cigarette Vape Detection (workspace `takoyati`) | v14 | https://universe.roboflow.com/takoyati/cigarette-vape-detection/dataset/14 | CC BY 4.0 | 5,774 base images; classes `cigarette`, `vape` |
| 2 | vaping (workspace `tiara-fb7pp`, project `vaping-ulrul`) | v13 | https://universe.roboflow.com/tiara-fb7pp/vaping-ulrul/dataset/13 | CC BY 4.0 | 2,300 base images; classes `vape-pod`, `asap` (Indonesian: "smoke" — exhaled smoke/vapour clouds) |
| 3 | Vape Dataset (workspace `vape-dataset`) | v1 | https://universe.roboflow.com/vape-dataset/vape-dataset/dataset/1 | CC BY 4.0 | 815 base images; class `Vape` |

## Class remapping (source → project vocabulary)

| Source class | Project class (id) |
|---|---|
| takoyati `cigarette` | `cigarette` (0) |
| takoyati `vape` | `vape_device` (1) |
| tiara `vape-pod` | `vape_device` (1) |
| tiara `asap` | `smoke_vapour` (2) |
| vape-dataset `Vape` | `vape_device` (1) |

## Merged dataset statistics (as trained, incl. published augmentations)

| Split | Images | `cigarette` boxes | `vape_device` boxes | `smoke_vapour` boxes |
|---|---|---|---|---|
| train | 18,905 | 7,077 | 14,262 | 3,239 |
| valid | 1,779 | 651 | 1,231 | 261 |
| test | 658 | 322 | 555 | 0 |

Persons are NOT part of this specialist model: person detection comes from
stock YOLOv8n (COCO, AGPL-3.0 weights by Ultralytics), and the two models
run on the same CCTV frame with their detections merged
(`yolo_detector_node`, `extra_model_path`).

Reproduction: the merge script logic and exact class mapping are recorded
in docs/DEVLOG.md (session 5); the raw downloads are not committed
(`datasets/` is git-ignored) but can be re-fetched with a free Roboflow
API key using the links above.

## Label-quality audit (manual)

36 randomly sampled annotated images (12 per audit sheet: cigarette, vape,
tiara vape/smoke) were rendered with their bounding boxes and inspected
manually on 2026-07-09: 34/36 boxes clearly correct, 2 tiny/ambiguous,
0 clearly mislabeled. Content is deployment-relevant: smoking/vaping at
the mouth, devices in hand, multiple vape form factors (pen, pod, box mod,
disposable, heat-not-burn), exhaled clouds, and some camera-distance shots.

## Trained model results (YOLOv8n, 49 epochs, RTX 3050 Ti)

| Split | mAP50 overall | cigarette | vape_device | smoke_vapour |
|---|---|---|---|---|
| test (held out) | 0.832 | 0.821 | 0.843 | — (no test boxes) |
| valid | 0.726 | 0.839 | 0.916 | 0.423 |

Notes: the test split contains no `smoke_vapour` instances, so that class
is evaluated on the validation split only. `smoke_vapour` is used solely
as C5 supporting evidence (weight 0.3) in the event-confirmation gate and
can never trigger an escalation by itself. Inference: ~4.5 ms/frame (GPU).
