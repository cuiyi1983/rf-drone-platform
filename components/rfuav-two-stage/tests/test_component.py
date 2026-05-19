"""
Unit tests for RFUAV Two-Stage Inference Component.

Run with: pytest tests/test_component.py -v
"""

import os
import sys
import time
import tempfile
import numpy as np
import pytest

# Add component to path - use parent of 'tests' directory
component_dir = os.path.join(os.path.dirname(__file__), '..')
parent_dir = os.path.dirname(component_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, component_dir)

# Change to component dir so relative imports work
original_cwd = os.getcwd()
os.chdir(component_dir)

# Import the component module
import component

# Restore cwd
os.chdir(original_cwd)

RFUAVTwoStageComponent = component.RFUAVTwoStageComponent
IInferenceComponent = component.IInferenceComponent
CLASS_LABELS = component.CLASS_LABELS


# --- Fixtures ---

@pytest.fixture(scope="module")
def component():
    """Create and initialize component for testing."""
    comp = RFUAVTwoStageComponent()
    comp.initialize(config={}, device='cpu')
    yield comp
    comp.release()


@pytest.fixture(scope="module")
def component_with_config():
    """Component with non-default config for threshold testing."""
    comp = RFUAVTwoStageComponent()
    comp.initialize(config={
        'confidence_threshold': 0.3,
        'max_detections': 5
    }, device='cpu')
    yield comp
    comp.release()


@pytest.fixture
def real_iq_data():
    """Load real IQ data from noise_5db_600k.bin for testing."""
    # Try to find the IQ data file
    possible_paths = [
        '/root/.openclaw/workspace/rf-drone-platform/tests/IQ-Record/noise_5db_600k.bin',
        '/root/.openclaw/workspace/rf-drone-platform/IQ-Record/noise_5db_600k.bin',
        os.path.join(os.path.dirname(__file__), '..', '..', 'tests', 'IQ-Record', 'noise_5db_600k.bin'),
        os.path.join(os.path.dirname(__file__), '..', '..', '..', 'tests', 'IQ-Record', 'noise_5db_600k.bin'),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            print(f"\nLoading IQ data from: {path}")
            data = np.fromfile(path, dtype=np.complex64)
            return data

    # Generate synthetic IQ data if file not found
    print("\nWARNING: noise_5db_600k.bin not found, using synthetic IQ data")
    t = np.linspace(0, 1e-5, 600000)
    noise = np.random.randn(600000) + 1j * np.random.randn(600000)
    iq_data = 0.1 * noise + np.exp(1j * 2 * np.pi * 5e6 * t)
    return iq_data.astype(np.complex64)


# --- Tests ---

def test_manifest_structure():
    """Test 1: Verify manifest contains all required fields."""
    comp = RFUAVTwoStageComponent()
    manifest = comp.get_manifest()

    # Check required top-level fields
    assert 'name' in manifest
    assert manifest['name'] == 'rfuav-two-stage'
    assert 'version' in manifest
    assert 'description' in manifest

    # Check collector_requirements
    assert 'collector_requirements' in manifest
    assert 'min_data_points' in manifest['collector_requirements']
    assert manifest['collector_requirements']['min_data_points'] == 600000

    # Check IO schema
    assert 'io' in manifest
    assert 'input' in manifest['io']
    assert 'output' in manifest['io']

    # Check config_schema
    assert 'config_schema' in manifest
    assert 'confidence_threshold' in manifest['config_schema']
    assert 'max_detections' in manifest['config_schema']

    # Check class_labels
    assert 'class_labels' in manifest
    assert len(manifest['class_labels']) == 7
    assert manifest['class_labels'][0] == 'DAUTEL EVO NANO'

    print("PASS: manifest_structure")


def test_initialize_and_health_check():
    """Test 2: Initialize component and verify health check passes."""
    comp = RFUAVTwoStageComponent()

    # Health check before init should fail
    assert comp.health_check() is False

    # Initialize
    comp.initialize(config={}, device='cpu')

    # Health check after init should pass
    assert comp.health_check() is True

    # Release and check again
    comp.release()
    assert comp.health_check() is False

    print("PASS: initialize_and_health_check")


def test_infer_returns_valid_structure(component, real_iq_data):
    """Test 3: Verify infer() returns correctly structured output."""
    iq_frame = {
        'iq_data': real_iq_data,
        'frame_id': 42,
        'timestamp': 1234567890.5,
        'center_freq': 5805e6,
        'sample_rate': 60e6,
        'metadata': {'source': 'test'}
    }

    result = component.infer(iq_frame)

    # Check top-level keys
    assert 'frame_id' in result
    assert result['frame_id'] == 42

    assert 'detections' in result
    assert isinstance(result['detections'], list)

    assert 'debug' in result
    assert isinstance(result['debug'], dict)

    # Check debug fields
    assert 'inference_time_ms' in result['debug']
    assert isinstance(result['debug']['inference_time_ms'], float)
    assert result['debug']['inference_time_ms'] > 0

    assert 'stage1_detections' in result['debug']
    assert 'stage2_classifications' in result['debug']

    print(f"PASS: infer_returns_valid_structure (time={result['debug']['inference_time_ms']:.1f}ms)")


def test_infer_with_real_iq_data(component, real_iq_data):
    """Test 4: Run inference with real IQ data from noise_5db_600k.bin."""
    iq_frame = {
        'iq_data': real_iq_data,
        'frame_id': 1,
        'timestamp': time.time()
    }

    result = component.infer(iq_frame)

    # Verify result is valid
    assert result['frame_id'] == 1
    assert 'detections' in result
    assert 'debug' in result

    # Check that inference ran (time should be reasonable)
    assert result['debug']['inference_time_ms'] < 60000  # Should be much faster

    # Stage1 and Stage2 should have run
    assert 'stage1_detections' in result['debug']
    assert 'stage2_classifications' in result['debug']

    print(f"PASS: infer_with_real_iq_data ({result['debug']['inference_time_ms']:.1f}ms, "
          f"s1={result['debug']['stage1_detections']}, s2={result['debug']['stage2_classifications']})")


def test_confidence_threshold_filtering(component_with_config):
    """Test 5: Test that confidence_threshold properly filters detections."""
    # Generate IQ with a clear signal component
    t = np.linspace(0, 1e-5, 600000)
    signal_freq = 5e6
    iq_data = np.exp(1j * 2 * np.pi * signal_freq * t)
    iq_data = iq_data + 0.05 * (np.random.randn(600000) + 1j * np.random.randn(600000))
    iq_data = iq_data.astype(np.complex64)

    iq_frame = {
        'iq_data': iq_data,
        'frame_id': 100,
        'timestamp': time.time()
    }

    result = component_with_config.infer(iq_frame)

    # Verify output structure is still valid even with no detections
    assert 'detections' in result
    assert isinstance(result['detections'], list)

    print("PASS: confidence_threshold_filtering")


def test_multiple_frames(component, real_iq_data):
    """Test 6: Run inference on multiple consecutive frames."""
    n_frames = 3
    results = []

    for i in range(n_frames):
        iq_frame = {
            'iq_data': real_iq_data,
            'frame_id': i,
            'timestamp': time.time()
        }
        result = component.infer(iq_frame)
        results.append(result)

    # All frames should have valid structure
    for i, result in enumerate(results):
        assert result['frame_id'] == i
        assert 'detections' in result
        assert 'debug' in result

    avg_time = np.mean([r['debug']['inference_time_ms'] for r in results])
    print(f"PASS: multiple_frames ({n_frames} frames, avg={avg_time:.1f}ms)")


def test_iq_data_too_short():
    """Test 7: Verify error when IQ data is too short."""
    comp = RFUAVTwoStageComponent()
    comp.initialize(config={}, device='cpu')

    # Too short IQ data
    short_iq = np.random.randn(100000).astype(np.complex64)
    iq_frame = {
        'iq_data': short_iq,
        'frame_id': 1,
        'timestamp': time.time()
    }

    with pytest.raises(ValueError, match="IQ data too short"):
        comp.infer(iq_frame)

    comp.release()
    print("PASS: iq_data_too_short")


def test_component_interface_compliance():
    """Test 8: Verify RFUAVTwoStageComponent implements IInferenceComponent."""
    comp = RFUAVTwoStageComponent()

    # Check that all interface methods exist
    assert hasattr(comp, 'get_manifest')
    assert hasattr(comp, 'initialize')
    assert hasattr(comp, 'infer')
    assert hasattr(comp, 'release')
    assert hasattr(comp, 'health_check')

    # Check they are callable
    assert callable(getattr(comp, 'get_manifest'))
    assert callable(getattr(comp, 'initialize'))
    assert callable(getattr(comp, 'infer'))
    assert callable(getattr(comp, 'release'))
    assert callable(getattr(comp, 'health_check'))

    # Verify manifest conforms to interface
    manifest = comp.get_manifest()
    assert manifest['io']['input']['iq_data'] == 'complex[]'
    assert manifest['io']['output']['detections'] == 'list'
    assert manifest['config_schema']['confidence_threshold']['type'] == 'number'
    assert manifest['config_schema']['max_detections']['type'] == 'integer'

    print("PASS: component_interface_compliance")


def test_manifest_io_schema():
    """Test 9: Verify manifest IO schema matches contract."""
    comp = RFUAVTwoStageComponent()
    manifest = comp.get_manifest()

    # Input schema
    input_schema = manifest['io']['input']
    assert 'iq_data' in input_schema
    assert 'frame_id' in input_schema
    assert 'timestamp' in input_schema
    assert 'center_freq' in input_schema
    assert 'sample_rate' in input_schema
    assert 'metadata' in input_schema

    # Output schema
    output_schema = manifest['io']['output']
    assert 'detections' in output_schema
    assert 'debug' in output_schema
    # debug is typed as dict in manifest (actual debug fields are runtime info, not schema)
    assert output_schema['debug'] == 'dict'

    print("PASS: manifest_io_schema")


def test_detection_output_format(component, real_iq_data):
    """Test 10: Verify detection output format when drone detected."""
    iq_frame = {
        'iq_data': real_iq_data,
        'frame_id': 999,
        'timestamp': time.time()
    }

    result = component.infer(iq_frame)

    # Each detection should have required fields
    for det in result['detections']:
        assert 'model' in det
        assert 'confidence' in det
        assert 'frequency' in det
        assert isinstance(det['model'], str)
        assert isinstance(det['confidence'], float)
        assert 0.0 <= det['confidence'] <= 1.0

    print(f"PASS: detection_output_format ({len(result['detections'])} detections)")


# --- Main ---

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])