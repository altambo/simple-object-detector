"""
YOLOv8 object detection with onnxruntime, numpy and Pillow only.

Replaces the ultralytics + torch runtime, keeping its default behaviour:
letterbox resize to the model input size, confidence filtering, per-class
non-maximum suppression, and boxes scaled back to the original image.
"""
import ast
from dataclasses import dataclass

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Distinct colours for drawing boxes, cycled by class id
PALETTE = [
    (230, 25, 75), (60, 180, 75), (255, 165, 0), (0, 130, 200), (145, 30, 180),
    (70, 200, 200), (240, 50, 230), (160, 200, 40), (250, 120, 160), (0, 128, 128),
    (170, 110, 40), (128, 0, 0), (100, 160, 110), (128, 128, 0), (0, 0, 128),
    (255, 90, 50), (90, 90, 230), (200, 160, 0), (40, 100, 60), (180, 60, 120),
]
PAD_COLOR = (114, 114, 114)
# Class offset for per-class NMS; larger than any box coordinate (same value ultralytics uses)
MAX_BOX_SIZE = 7680.0


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    box: tuple  # (x1, y1, x2, y2) in original image pixels


def load_image(path):
    """Open an image as RGB, applying EXIF orientation (as phone photos need)."""
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


def non_max_suppression(boxes, scores, iou_threshold):
    """Greedy NMS on xyxy boxes. Returns indices of kept boxes, highest score first."""
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        rest = order[1:]
        inter_w = np.clip(np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]), 0, None)
        inter_h = np.clip(np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]), 0, None)
        inter = inter_w * inter_h
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return np.array(keep, dtype=int)


class YoloOnnxDetector:
    def __init__(self, model_path, conf_threshold=0.25, iou_threshold=0.7, max_detections=300):
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        _, _, self.input_h, self.input_w = self.session.get_inputs()[0].shape
        # ultralytics exports store class names as a dict literal, e.g. "{0: 'person', ...}"
        metadata = self.session.get_modelmeta().custom_metadata_map
        self.names = ast.literal_eval(metadata["names"]) if "names" in metadata else {}
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.max_detections = max_detections

    def _letterbox(self, image):
        """Resize keeping aspect ratio and pad to the model input size, centred."""
        w, h = image.size
        gain = min(self.input_h / h, self.input_w / w)
        new_w, new_h = round(w * gain), round(h * gain)
        pad_x = round((self.input_w - new_w) / 2 - 0.1)
        pad_y = round((self.input_h - new_h) / 2 - 0.1)
        canvas = Image.new("RGB", (self.input_w, self.input_h), PAD_COLOR)
        canvas.paste(image.resize((new_w, new_h), Image.Resampling.BILINEAR), (pad_x, pad_y))
        return canvas, gain, pad_x, pad_y

    def detect(self, image):
        """Run detection on a PIL RGB image and return a list of Detection, best first."""
        canvas, gain, pad_x, pad_y = self._letterbox(image)
        # HWC uint8 -> NCHW float32 in [0, 1]
        tensor = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]

        # output is (1, 4 + num_classes, num_candidates): cx, cy, w, h, then class scores
        preds = output[0].T
        class_scores = preds[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        confidences = class_scores[np.arange(len(class_ids)), class_ids]
        mask = confidences > self.conf_threshold
        if not mask.any():
            return []
        cx, cy, bw, bh = preds[mask, :4].T
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
        confidences, class_ids = confidences[mask], class_ids[mask]

        # Per-class NMS: offset boxes by class so different classes never overlap
        offsets = class_ids[:, None] * MAX_BOX_SIZE
        keep = non_max_suppression(boxes + offsets, confidences, self.iou_threshold)
        keep = keep[: self.max_detections]

        # Undo the letterbox and clip to the original image
        w, h = image.size
        boxes = (boxes[keep] - [pad_x, pad_y, pad_x, pad_y]) / gain
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)

        return [
            Detection(
                class_id=int(c),
                label=self.names.get(int(c), str(int(c))),
                confidence=float(s),
                box=tuple(float(v) for v in b),
            )
            for b, s, c in zip(boxes, confidences[keep], class_ids[keep])
        ]


def annotate(image, detections):
    """Return a copy of the image with labelled boxes drawn on it."""
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    w, h = annotated.size
    line_width = max(round((w + h) / 2 * 0.003), 2)
    font = ImageFont.load_default(size=max(round((w + h) / 2 * 0.02), 12))

    for det in detections:
        color = PALETTE[det.class_id % len(PALETTE)]
        x1, y1, x2, y2 = det.box
        draw.rectangle((x1, y1, x2, y2), outline=color, width=line_width)

        text = f"{det.label} {det.confidence:.2f}"
        tx1, ty1, tx2, ty2 = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = tx2 - tx1, ty2 - ty1
        pad = line_width
        # Put the label above the box, or inside it when the box touches the top edge
        label_y = y1 - text_h - 2 * pad if y1 - text_h - 2 * pad >= 0 else y1
        draw.rectangle((x1, label_y, x1 + text_w + 2 * pad, label_y + text_h + 2 * pad), fill=color)
        draw.text((x1 + pad - tx1, label_y + pad - ty1), text, fill=(255, 255, 255), font=font)

    return annotated


def summarize(detections):
    """Human-readable summary, e.g. 'Found 4 objects: 3 person, 1 bus.'"""
    if not detections:
        return "No objects found."
    counts = {}
    for det in detections:
        counts[det.label] = counts.get(det.label, 0) + 1
    parts = ", ".join(f"{n} {label}" for label, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    noun = "object" if len(detections) == 1 else "objects"
    return f"Found {len(detections)} {noun}: {parts}."
