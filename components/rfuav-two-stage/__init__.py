"""
RFUAV Two-Stage Inference Component.

Two-stage drone detection using YOLO (Stage1) and ResNet152 (Stage2).
"""

import component

RFUAVTwoStageComponent = component.RFUAVTwoStageComponent
IInferenceComponent = component.IInferenceComponent

__all__ = ["RFUAVTwoStageComponent", "IInferenceComponent"]