import numpy as np
from PIL import Image

from modules.utils.device import get_providers
from modules.utils.download import ModelDownloader, ModelID
from modules.utils.onnx import make_session
from modules.utils.textblock import TextBlock
from modules.detection.utils.geometry import calculate_iou
from .base import DetectionEngine
from .rtdetr_v2_onnx import RTDetrV2ONNXDetection


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Standard greedy non-maximum suppression on xyxy boxes."""
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[order[1:]] - intersection
        iou = np.where(union > 0, intersection / union, 0.0)
        order = order[1:][iou <= iou_threshold]
    return keep


class SpeechBubbleSegONNX:
    """YOLOv8m-seg speech bubble segmentation model.

    Weights: kitsumed/yolov8m_seg-speech-bubble (ONNX export); the same
    fine-tune is also published as mayocream/speech-bubble-segmentation.
    Single class ("speech bubble") with 32 mask coefficients over a
    (32, 160, 160) prototype tensor.
    """

    INPUT_SIZE = 640
    MASK_COEFFS = 32

    def __init__(self):
        self.session = None
        self.conf_threshold = 0.25
        self.nms_threshold = 0.45
        self.mask_threshold = 0.5

    def initialize(
        self,
        device: str = 'cpu',
        conf_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        mask_threshold: float = 0.5,
    ) -> None:
        self.conf_threshold = conf_threshold
        self.nms_threshold = nms_threshold
        self.mask_threshold = mask_threshold

        file_path = ModelDownloader.get_file_path(
            ModelID.SPEECH_BUBBLE_SEG_ONNX, 'speech-bubble-seg-yolov8m.onnx'
        )
        providers = get_providers(device)
        self.session = make_session(file_path, providers=providers)

    def detect_bubbles(self, image: np.ndarray) -> np.ndarray:
        """Detect speech bubbles and return their boxes as an (N, 4) xyxy array.

        Boxes are tightened to the predicted segmentation masks, which is
        what makes this model preferable to a plain box detector.
        """
        input_tensor, scale, pad_x, pad_y = self._letterbox(image)

        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: input_tensor})
        preds, protos = outputs[0][0], outputs[1][0]  # (4+nc+32, N), (32, 160, 160)

        num_classes = preds.shape[0] - 4 - self.MASK_COEFFS
        boxes_cxcywh = preds[:4].T                        # (N, 4)
        scores = preds[4:4 + num_classes].max(axis=0)     # (N,)
        coeffs = preds[4 + num_classes:].T                # (N, 32)

        keep = scores >= self.conf_threshold
        if not keep.any():
            return np.array([])
        boxes_cxcywh, scores, coeffs = boxes_cxcywh[keep], scores[keep], coeffs[keep]

        # cxcywh -> xyxy in letterboxed input space
        boxes = np.empty_like(boxes_cxcywh)
        boxes[:, 0] = boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2
        boxes[:, 1] = boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2
        boxes[:, 2] = boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2
        boxes[:, 3] = boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2
        boxes = boxes.clip(0, self.INPUT_SIZE)

        keep_indices = _nms(boxes, scores, self.nms_threshold)

        height, width = image.shape[:2]
        results = []
        for i in keep_indices:
            box = self._refine_box_with_mask(boxes[i], coeffs[i], protos)
            # Undo letterboxing back to original image coordinates
            x1 = (box[0] - pad_x) / scale
            y1 = (box[1] - pad_y) / scale
            x2 = (box[2] - pad_x) / scale
            y2 = (box[3] - pad_y) / scale
            x1 = int(np.clip(x1, 0, width))
            y1 = int(np.clip(y1, 0, height))
            x2 = int(np.clip(x2, 0, width))
            y2 = int(np.clip(y2, 0, height))
            if x2 > x1 and y2 > y1:
                results.append([x1, y1, x2, y2])

        return np.array(results) if results else np.array([])

    def _letterbox(self, image: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        """Resize with preserved aspect ratio onto a padded square input."""
        height, width = image.shape[:2]
        size = self.INPUT_SIZE
        scale = min(size / height, size / width)
        new_w, new_h = max(1, int(round(width * scale))), max(1, int(round(height * scale)))

        resized = np.asarray(Image.fromarray(image).resize((new_w, new_h)))
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

        tensor = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[np.newaxis, ...]
        return tensor, scale, float(pad_x), float(pad_y)

    def _refine_box_with_mask(self, box: np.ndarray, coeff: np.ndarray, protos: np.ndarray) -> np.ndarray:
        """Tighten a box to the bounds of its instance mask (in input space)."""
        proto_c, proto_h, proto_w = protos.shape
        ratio = proto_w / self.INPUT_SIZE

        x1 = int(np.clip(np.floor(box[0] * ratio), 0, proto_w - 1))
        y1 = int(np.clip(np.floor(box[1] * ratio), 0, proto_h - 1))
        x2 = int(np.clip(np.ceil(box[2] * ratio), x1 + 1, proto_w))
        y2 = int(np.clip(np.ceil(box[3] * ratio), y1 + 1, proto_h))

        crop = protos[:, y1:y2, x1:x2].reshape(proto_c, -1)
        mask = _sigmoid(coeff @ crop).reshape(y2 - y1, x2 - x1) > self.mask_threshold
        if not mask.any():
            return box

        ys, xs = np.nonzero(mask)
        return np.array([
            (x1 + xs.min()) / ratio,
            (y1 + ys.min()) / ratio,
            (x1 + xs.max() + 1) / ratio,
            (y1 + ys.max() + 1) / ratio,
        ])


class RTDetrV2BubbleSegDetection(DetectionEngine):
    """Hybrid detection engine.

    Text boxes come from RT-DETR-v2 while speech bubbles come from the
    dedicated YOLOv8m segmentation model. RT-DETR bubbles that the
    segmentation model missed are kept as a fallback so unusual bubble
    styles still get matched.
    """

    BUBBLE_MERGE_IOU = 0.5

    def __init__(self, settings=None):
        super().__init__(settings)
        self.text_detector = RTDetrV2ONNXDetection(settings)
        self.bubble_segmenter = SpeechBubbleSegONNX()

    def initialize(
        self,
        device: str = 'cpu',
        confidence_threshold: float = 0.3,
        bubble_confidence_threshold: float = 0.25,
    ) -> None:
        self.text_detector.initialize(device, confidence_threshold)
        self.bubble_segmenter.initialize(device=device, conf_threshold=bubble_confidence_threshold)

    def detect(self, image: np.ndarray) -> list[TextBlock]:
        slicer = self.text_detector.image_slicer
        detector_bubbles, text_boxes = slicer.process_slices_for_detection(
            image, self.text_detector._detect_single_image
        )
        seg_bubbles, _ = slicer.process_slices_for_detection(
            image, lambda img: (self.bubble_segmenter.detect_bubbles(img), np.array([]))
        )
        bubble_boxes = self._merge_bubble_boxes(seg_bubbles, detector_bubbles)
        return self.create_text_blocks(image, text_boxes, bubble_boxes)

    @classmethod
    def _merge_bubble_boxes(cls, seg_boxes: np.ndarray, detector_boxes: np.ndarray) -> np.ndarray:
        """Prefer segmentation bubbles; add detector bubbles they missed."""
        if len(seg_boxes) == 0:
            return detector_boxes
        if len(detector_boxes) == 0:
            return seg_boxes
        extras = [
            det_box for det_box in detector_boxes
            if max(calculate_iou(det_box, seg_box) for seg_box in seg_boxes) < cls.BUBBLE_MERGE_IOU
        ]
        if not extras:
            return seg_boxes
        return np.vstack([seg_boxes, np.array(extras)])
