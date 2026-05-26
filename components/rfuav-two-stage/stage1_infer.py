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
        self._input_height, self._input_width = YOLO_INPUT_SIZE

    def infer(self, spectrogram: np.ndarray, conf_threshold: float = CONF_THRESHOLD_DEFAULT) -> list:
        """
        Run YOLO detection on spectrogram.

        Args:
            spectrogram: 2D uint8 array (H, W), normalized spectrogram image
            conf_threshold: Confidence threshold for filtering detections

        Returns:
            detections: List of dicts with keys: xyxy, confidence, class_id
        """
        # Record actual input dimensions
        if len(spectrogram.shape) == 2:
            self._input_height, self._input_width = spectrogram.shape

        # Prepare input: normalize to 0-1 and add batch/channel dims
        input_data = spectrogram.astype(np.float32) / 255.0
        # YOLO expects 3 channels (RGB), replicate grayscale to all 3
        input_data = np.stack([input_data, input_data, input_data], axis=0)  # (3, H, W)
        input_data = np.expand_dims(input_data, axis=0)  # (1, 3, H, W)

        # Run inference
        outputs = self.session.run(self.output_names, {self.input_name: input_data})

        # Parse YOLO output
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

        for output_tensor in outputs:
            # shape: (batch, 5, num_predictions) e.g. (1, 5, 8400)
            # 5 = [xcenter, ycenter, width, height, objectness]
            if len(output_tensor.shape) == 3:
                # 转置为 (num_predictions, 5)
                output_tensor = output_tensor.transpose((2, 1, 0)).squeeze()  # → (8400, 5)

                for pred in output_tensor:
                    # pred[0]=xcenter, pred[1]=ycenter, pred[2]=width, pred[3]=height, pred[4]=objectness
                    xcenter, ycenter, width, height, obj_conf = pred

                    # objectness 已过 sigmoid（在 0-1 范围内）
                    confidence = float(obj_conf)

                    if confidence < conf_threshold:
                        continue

                    # Convert from center to xyxy
                    img_h, img_w = self._input_height, self._input_width
                    x1 = float(xcenter - width / 2)
                    y1 = float(ycenter - height / 2)
                    x2 = float(xcenter + width / 2)
                    y2 = float(ycenter + height / 2)

                    # Clip to image bounds
                    x1 = max(0, min(img_w, x1))
                    y1 = max(0, min(img_h, y1))
                    x2 = max(0, min(img_w, x2))
                    y2 = max(0, min(img_h, y2))

                    if x2 <= x1 or y2 <= y1:
                        continue

                    detections.append({
                        'xyxy': [x1, y1, x2, y2],
                        'confidence': confidence,
                        'class_id': 0,  # drone
                        'class_name': 'drone'
                    })

        detections.sort(key=lambda d: d['confidence'], reverse=True)
        return detections

    def get_input_shape(self) -> tuple:
        """Return model input shape."""
        return self.session.get_inputs()[0].shape
