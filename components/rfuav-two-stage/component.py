"""
RFUAV Two-Stage Inference Component.

Stage1: YOLO detection on STFT spectrogram (drone vs noise)
Stage2: ResNet152 classification of detected regions (7 drone models)

Implements IInferenceComponent interface.
"""

import os
import sys
import time
import numpy as np
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional

# Add current directory to path for direct import
_component_dir = os.path.dirname(os.path.abspath(__file__))
if _component_dir not in sys.path:
    sys.path.insert(0, _component_dir)

import stft as stft_module
import stage1_infer as s1_module
import stage2_infer as s2_module

iq_to_spectrogram = stft_module.iq_to_spectrogram
SAMPLE_RATE = stft_module.SAMPLE_RATE
NPERSEG = stft_module.NPERSEG
HOP = stft_module.HOP
Stage1Infer = s1_module.Stage1Infer
YOLO_INPUT_SIZE = s1_module.YOLO_INPUT_SIZE
Stage2Infer = s2_module.Stage2Infer
CLASS_LABELS = s2_module.CLASS_LABELS


class IInferenceComponent(ABC):
    """Interface for all inference components in the platform."""

    @abstractmethod
    def get_manifest(self) -> dict:
        """Return component manifest/self-description."""
        pass

    @abstractmethod
    def initialize(self, config: dict, device: str) -> None:
        """Load models and initialize resources."""
        pass

    @abstractmethod
    def infer(self, iq_frame: dict) -> dict:
        """Run inference on a single IQ frame."""
        pass

    @abstractmethod
    def release(self) -> None:
        """Release resources."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if component is healthy."""
        pass


class RFUAVTwoStageComponent(IInferenceComponent):
    """
    Two-stage RF drone detection component.

    Stage1: YOLO v2 detector on STFT spectrogram (detects drone presence)
    Stage2: ResNet152 classifier on cropped regions (identifies drone model)
    """

    def __init__(self):
        self._initialized = False
        self._config = {}
        self._device = None
        self._stage1: Optional[Stage1Infer] = None
        self._stage2: Optional[Stage2Infer] = None
        self._models_dir = None

    # --- IInferenceComponent implementation ---

    def get_manifest(self) -> dict:
        """Return component manifest."""
        manifest = {
            "name": "rfuav-two-stage",
            "version": "1.0.0",
            "description": "Two-stage RF drone detection (YOLO + ResNet152)",
            "collector_requirements": {
                "min_data_points": 600000,
            },
            "io": {
                "input": {
                    "iq_data": "complex[]",
                    "frame_id": "int",
                    "timestamp": "float",
                    "center_freq": "float",
                    "sample_rate": "float",
                    "metadata": "dict"
                },
                "output": {
                    "detections": "list",
                    "debug": "dict"
                }
            },
            "config_schema": {
                "confidence_threshold": {"type": "number", "default": 0.5},
                "max_detections": {"type": "integer", "default": 10}
            },
            "class_labels": CLASS_LABELS
        }
        return manifest

    def initialize(self, config: dict, device: str) -> None:
        """
        Initialize the component by loading models.

        Args:
            config: Configuration dict. Supported keys:
                - confidence_threshold: float (default 0.5)
                - max_detections: int (default 10)
                - models_dir: str (optional, default: component directory)
            device: Device string ('cpu', 'cuda', etc.)
        """
        self._config = config
        self._device = device

        # Find models directory
        if 'models_dir' in config:
            self._models_dir = config['models_dir']
        else:
            # Default: models subdirectory of this component
            self._models_dir = os.path.join(os.path.dirname(__file__), 'models')

        # Determine ONNX Runtime providers
        if device == 'cuda':
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        else:
            providers = ['CPUExecutionProvider']

        # Load Stage1 YOLO model
        stage1_path = os.path.join(self._models_dir, 'stage1.onnx')
        if not os.path.exists(stage1_path):
            raise FileNotFoundError(f"Stage1 model not found: {stage1_path}")
        self._stage1 = Stage1Infer(stage1_path, providers=providers)

        # Load Stage2 ResNet152 model
        stage2_path = os.path.join(self._models_dir, 'stage2.onnx')
        if not os.path.exists(stage2_path):
            raise FileNotFoundError(f"Stage2 model not found: {stage2_path}")
        self._stage2 = Stage2Infer(stage2_path, providers=providers)

        self._initialized = True

    def infer(self, iq_frame: dict) -> dict:
        """
        Run two-stage inference on a single IQ frame.

        Args:
            iq_frame: Dict with keys:
                - iq_data: complex[] array, length >= 600000
                - frame_id: int
                - timestamp: float (optional)
                - center_freq: float (optional)
                - sample_rate: float (optional)
                - metadata: dict (optional)

        Returns:
            result: Dict with keys:
                - frame_id: int
                - detections: list of detection dicts
                - debug: dict with timing and stage info
        """
        if not self._initialized:
            raise RuntimeError("Component not initialized. Call initialize() first.")

        t_start = time.perf_counter()

        # Extract IQ data
        iq_data = iq_frame['iq_data']
        frame_id = iq_frame.get('frame_id', 0)

        # Validate input length
        min_points = self.get_manifest()['collector_requirements']['min_data_points']
        if len(iq_data) < min_points:
            raise ValueError(
                f"IQ data too short: {len(iq_data)} < {min_points} (min required)"
            )

        # Get config
        conf_threshold = self._config.get('confidence_threshold', 0.5)
        max_detections = self._config.get('max_detections', 10)

        # Step 1: Convert IQ to spectrogram
        t_stft = time.perf_counter()
        spectrogram = iq_to_spectrogram(iq_data, target_height=640, target_width=640)
        t_stft_end = time.perf_counter()

        # Step 2: Stage1 YOLO detection
        t_stage1 = time.perf_counter()
        stage1_detections = self._stage1.infer(spectrogram, conf_threshold=conf_threshold)
        t_stage1_end = time.perf_counter()

        # Step 3: Stage2 classification for each detection
        t_stage2 = time.perf_counter()
        final_detections = []
        for det in stage1_detections[:max_detections]:
            # Crop region from spectrogram
            x1, y1, x2, y2 = [int(v) for v in det['xyxy']]
            cropped = spectrogram[y1:y2, x1:x2]

            # Skip if crop is too small
            if cropped.size < 10:
                continue

            # Classify with Stage2
            classification = self._stage2.infer(cropped)

            final_detections.append({
                'model': classification['class_name'],
                'confidence': float(np.clip(det['confidence'] * classification['confidence'], 0, 1)),
                'frequency': det['confidence'],  # Stage1 raw confidence
                'stage1_conf': float(det['confidence']),
                'stage2_class': classification['class_name'],
                'stage2_conf': float(classification['confidence']),
                'bbox': det['xyxy']
            })
        t_stage2_end = time.perf_counter()

        t_total = time.perf_counter() - t_start

        # Build result
        result = {
            'frame_id': frame_id,
            'detections': final_detections,
            'debug': {
                'inference_time_ms': t_total * 1000,
                'stft_time_ms': (t_stft_end - t_stft) * 1000,
                'stage1_time_ms': (t_stage1_end - t_stage1) * 1000,
                'stage2_time_ms': (t_stage2_end - t_stage2) * 1000,
                'stage1_detections': len(stage1_detections),
                'stage2_classifications': len(final_detections),
                'spectrogram_shape': spectrogram.shape
            }
        }

        return result

    def release(self) -> None:
        """Release model resources."""
        self._stage1 = None
        self._stage2 = None
        self._initialized = False

    def health_check(self) -> bool:
        """Check if component is properly initialized and healthy."""
        if not self._initialized:
            return False
        try:
            # Check if sessions are still valid
            if self._stage1 is None or self._stage2 is None:
                return False
            return True
        except Exception:
            return False