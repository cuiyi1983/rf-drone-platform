"""
Stage1: YOLO detection on spectrogram.
Detects drone vs noise regions in the RF spectrogram.
"""

import os
import numpy as np
import onnxruntime as ort


# YOLO output parsing constants
CONF_THRESHOLD_DEFAULT = 0.5
YOLO_INPUT_SIZE = (640, 640)


class Stage1Infer:
    """Stage1 YOLO detector for drone detection in spectrogram."""

    def __init__(self, model_path: str, providers=None):
        """
        Initialize Stage1 YOLO inference.

        Args:
            model_path: Path to stage1.onnx model file
            providers: ONNX Runtime providers list
        """
        if providers is None:
            providers = ['CPUExecutionProvider']

        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [o.name for o in self.session.get_outputs()]

    def infer(self, spectrogram: np.ndarray, conf_threshold: float = CONF_THRESHOLD_DEFAULT) -> list:
        """
        Run YOLO detection on spectrogram.

        Args:
            spectrogram: 2D uint8 array (H, W), normalized spectrogram image
            conf_threshold: Confidence threshold for filtering detections

        Returns:
            detections: List of dicts with keys: xyxy, confidence, class_id
        """
        # Prepare input: normalize to 0-1 and add batch/channel dims
        input_data = spectrogram.astype(np.float32) / 255.0
        # YOLO expects 3 channels (RGB), replicate grayscale to all 3
        input_data = np.stack([input_data, input_data, input_data], axis=0)  # (3, H, W)
        input_data = np.expand_dims(input_data, axis=0)  # (1, 3, H, W)

        # Run inference
        outputs = self.session.run(self.output_names, {self.input_name: input_data})

        # Parse YOLO output (assuming standard YOLOv5/v8 format)
        # Output shape: (batch, num_boxes, 85) where 85 = 4(box) + 1(obj) + 80(classes)
        # For nc=1: 4 + 1 + 1 = 6
        detections = self._parse_yolo_output(outputs, conf_threshold)

        return detections

    def _parse_yolo_output(self, outputs: list, conf_threshold: float) -> list:
        """
        Parse YOLO model outputs into detection boxes.

        Args:
            outputs: List of output tensors from YOLO model
            conf_threshold: Confidence threshold

        Returns:
            List of detections with xyxy boxes
        """
        detections = []

        # Handle multiple output tensors (YOLOv5/v8 style)
        for output_tensor in outputs:
            if len(output_tensor.shape) == 3:
                # Shape: (batch, num_predictions, 85)
                # Transpose to (num_predictions, batch, 85)
                output_tensor = np.transpose(output_tensor, (1, 0, 2))

                for pred in output_tensor:
                    # pred shape: (batch, 85) -> take first
                    if len(pred.shape) == 2:
                        pred = pred[0]  # (85,)

                    # Box coordinates are first 4 values
                    x_center, y_center, width, height = pred[:4]

                    # Objectness score
                    obj_conf = pred[4]

                    # Class confidence (for nc=1, class 0)
                    if len(pred) > 5:
                        class_conf = pred[5]
                    else:
                        class_conf = 1.0

                    # Combined confidence
                    confidence = obj_conf * class_conf

                    if confidence < conf_threshold:
                        continue

                    # Convert from center to xyxy
                    img_h, img_w = YOLO_INPUT_SIZE
                    x1 = x_center - width / 2
                    y1 = y_center - height / 2
                    x2 = x_center + width / 2
                    y2 = y_center + height / 2

                    # Clip to image bounds
                    x1 = max(0, min(img_w, x1))
                    y1 = max(0, min(img_h, y1))
                    x2 = max(0, min(img_w, x2))
                    y2 = max(0, min(img_h, y2))

                    detections.append({
                        'xyxy': [float(x1), float(y1), float(x2), float(y2)],
                        'confidence': float(confidence),
                        'class_id': 0,  # drone
                        'class_name': 'drone'
                    })

        # Sort by confidence descending
        detections.sort(key=lambda d: d['confidence'], reverse=True)

        return detections

    def get_input_shape(self) -> tuple:
        """Return model input shape."""
        return self.session.get_inputs()[0].shape