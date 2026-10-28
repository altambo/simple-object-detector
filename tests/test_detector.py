import numpy as np
import pytest
from PIL import Image

from conftest import MODEL_PATH, requires_model
from detector import Detection, YoloOnnxDetector, annotate, load_image, non_max_suppression, summarize


# --- non_max_suppression -------------------------------------------------

def test_nms_suppresses_overlapping_lower_score_box():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    keep = non_max_suppression(boxes, scores, iou_threshold=0.5)
    assert keep.tolist() == [0, 2]


def test_nms_keeps_boxes_below_iou_threshold():
    boxes = np.array([[0, 0, 10, 10], [5, 0, 15, 10]], dtype=np.float32)  # IoU = 1/3
    scores = np.array([0.6, 0.9], dtype=np.float32)
    keep = non_max_suppression(boxes, scores, iou_threshold=0.5)
    assert keep.tolist() == [1, 0]  # highest score first


# --- summarize -------------------------------------------------------------

def _det(label, class_id=0):
    return Detection(class_id=class_id, label=label, confidence=0.9, box=(0, 0, 1, 1))


def test_summarize_no_detections():
    assert summarize([]) == "No objects found."


def test_summarize_counts_and_orders_by_frequency():
    dets = [_det("bus", 5), _det("person"), _det("person"), _det("person")]
    assert summarize(dets) == "Found 4 objects: 3 person, 1 bus."


def test_summarize_singular():
    assert summarize([_det("dog", 16)]) == "Found 1 object: 1 dog."


# --- load_image / annotate ----------------------------------------------------

def test_load_image_converts_to_rgb_and_applies_exif_orientation(tmp_path):
    path = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90° CW to display
    Image.new("L", (40, 20)).save(path, exif=exif)
    img = load_image(path)
    assert img.mode == "RGB"
    assert img.size == (20, 40)


def test_annotate_returns_copy_with_same_size():
    img = Image.new("RGB", (100, 80), (0, 0, 0))
    out = annotate(img, [Detection(0, "person", 0.9, (10, 10, 50, 50))])
    assert out.size == img.size
    assert out is not img
    assert img.getpixel((10, 30)) == (0, 0, 0)  # original untouched
    assert out.getpixel((10, 30)) != (0, 0, 0)  # box edge drawn


# --- model -------------------------------------------------------------------

@pytest.fixture(scope="module")
def detector():
    return YoloOnnxDetector(str(MODEL_PATH))


@requires_model
def test_model_metadata(detector):
    assert (detector.input_h, detector.input_w) == (640, 640)
    assert detector.names[0] == "person"
    assert len(detector.names) == 80


@requires_model
def test_detects_bus_and_people(detector, bus_image_path):
    img = load_image(bus_image_path)
    dets = detector.detect(img)
    labels = [d.label for d in dets]
    assert labels.count("bus") == 1
    assert labels.count("person") >= 3
    assert all(d.confidence > 0.25 for d in dets)
    # Boxes are in original image coordinates and inside the image
    w, h = img.size
    for d in dets:
        x1, y1, x2, y2 = d.box
        assert 0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h
    # Results are ordered best first
    assert [d.confidence for d in dets] == sorted((d.confidence for d in dets), reverse=True)


@requires_model
def test_blank_image_has_no_detections(detector):
    assert detector.detect(Image.new("RGB", (640, 480), (114, 114, 114))) == []
