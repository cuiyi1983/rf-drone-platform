"""
Stage2: ResNet152 classification of detected drone regions.
Classifies cropped spectrogram regions into 7 drone model classes.
"""

import numpy as np
import onnxruntime as ort
from scipy.ndimage import zoom as scipy_zoom


# Class labels for 7 drone models
CLASS_LABELS = [
    "DAUTEL EVO NANO",      # 0
    "DEVENTION DEVO",        # 1
    "DJI AVATA2",            # 2
    "DJI FPV COMBO",         # 3
    "DJI MAVIC3 PRO",        # 4
    "DJI MINI3.1",          # 5
    "DJI MINI4 PRO"         # 6
]

RESNET_INPUT_SIZE = (224, 224)


class Stage2Infer:
    """Stage2 ResNet152 classifier for drone model identification."""

    def __init__(self, model_path: str, providers=None):
        """
        Initialize Stage2 ResNet152 inference.

        Args:
            model_path: Path to stage2.onnx model file
            providers: ONNX Runtime providers list
        """
        if providers is None:
            providers = ['CPUExecutionProvider']

        self.session = ort.InferenceSession(model_path, providers=providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def infer(self, cropped_spectrogram: np.ndarray) -> dict:
        """
        Classify cropped spectrogram region into drone model.

        Args:
            cropped_spectrogram: 2D uint8 array of cropped spectrogram region
                                 Can be any size, will be resized to 224x224

        Returns:
            result: dict with keys: class_id, class_name, confidence, probabilities
        """
        # Resize to 224x224 using scipy (PIL not available in all environments)
        # cropped_spectrogram is 2D uint8 (H, W)
        h, w = cropped_spectrogram.shape
        target_h, target_w = RESNET_INPUT_SIZE
        zoom_h = target_h / h
        zoom_w = target_w / w
        resized = scipy_zoom(cropped_spectrogram.astype(np.float32), (zoom_h, zoom_w), order=1)
        input_data = resized.astype(np.uint8)

        # Handle grayscale (2D) or color (3D) images
        if input_data.ndim == 2:
            # Grayscale: expand to 3 channels (H, W) -> (H, W, 1) -> repeat to (H, W, 3)
            input_data = np.repeat(input_data[:, :, np.newaxis], 3, axis=2)  # (H, W, 3)
        # else: assume already (H, W, 3)

        # Normalize: ImageNet-style normalization
        # Convert to (C, H, W) and normalize
        input_data = input_data.transpose(2, 0, 1)  # (H, W, C) -> (C, H, W)

        # Normalize with ImageNet mean and std
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
        input_data = (input_data / 255.0 - mean) / std

        # Ensure float32 for ONNX Runtime
        input_data = input_data.astype(np.float32)

        # Add batch dimension
        input_data = np.expand_dims(input_data, axis=0)

        # Run inference
        outputs = self.session.run([self.output_name], {self.input_name: input_data})
        logits = outputs[0][0]  # Shape: (7,)

        # Apply softmax to get probabilities
        exp_logits = np.exp(logits - np.max(logits))  # Numerical stability
        probabilities = exp_logits / np.sum(exp_logits)

        # Get top prediction
        class_id = int(np.argmax(probabilities))
        confidence = float(probabilities[class_id])

        return {
            'class_id': class_id,
            'class_name': CLASS_LABELS[class_id],
            'confidence': confidence,
            'probabilities': probabilities.tolist()
        }

    def infer_batch(self, cropped_spectrograms: list) -> list:
        """
        Classify multiple cropped spectrogram regions.

        Args:
            cropped_spectrograms: List of 2D uint8 arrays

        Returns:
            List of classification results
        """
        results = []
        for spec in cropped_spectrograms:
            results.append(self.infer(spec))
        return results

    def get_input_shape(self) -> tuple:
        """Return model input shape."""
        return self.session.get_inputs()[0].shape