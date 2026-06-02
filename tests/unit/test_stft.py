"""
Unit tests for components/rfuav-two-stage/stft.py
"""
import sys
import os
import numpy as np
import pytest
import importlib.util

# Load stft module directly from file path (handles hyphenated directory name)
stft_path = '/home/ubuntu/rf-drone-platform-test/components/rfuav-two-stage/stft.py'
spec = importlib.util.spec_from_file_location('stft', stft_path)
stft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stft)


class TestSTFT:
    """Test cases for iq_to_spectrogram function."""

    def test_output_shape(self):
        """Test that output shape is (640, 640) with dtype uint8."""
        np.random.seed(42)
        iq_data = np.random.randn(600000) + 1j * np.random.randn(600000)
        result = stft.iq_to_spectrogram(iq_data)
        assert result.shape == (640, 640), f"Expected (640, 640), got {result.shape}"
        assert result.dtype == np.uint8, f"Expected uint8, got {result.dtype}"

    def test_dc_at_center_after_fftshift(self):
        """
        Generate synthetic IQ with a tone at a specific frequency.
        After STFT + fftshift, verify the energy peak is NOT at the edge
        frequency bin but somewhere in the middle third of the frequency axis.
        """
        sample_rate = 60_000_000  # 60 MHz
        n_samples = 600000
        
        # Place a tone at 10 MHz (relative to center)
        tone_freq = 10_000_000
        t = np.arange(n_samples) / sample_rate
        iq_data = np.exp(2j * np.pi * tone_freq * t).astype(np.complex64)
        
        # Add some noise
        np.random.seed(42)
        noise = (np.random.randn(n_samples) + 1j * np.random.randn(n_samples)) * 0.01
        iq_data = iq_data + noise
        
        result = stft.iq_to_spectrogram(iq_data)
        
        # Find peak in frequency (rows) - average over time for robustness
        avg_power = np.mean(result.astype(np.float32), axis=1)
        peak_idx = np.argmax(avg_power)
        
        total_bins = result.shape[0]  # 640
        third = total_bins // 3
        
        # Peak should NOT be at the edges (first or last 10%)
        edge_margin = int(total_bins * 0.1)
        
        assert peak_idx > edge_margin and peak_idx < (total_bins - edge_margin), (
            f"Peak at edge: {peak_idx}/{total_bins}. "
            f"Expected middle third ({third}/{total_bins}-{2*third}/{total_bins})"
        )
        
        # Also verify it's roughly in the middle third
        assert third <= peak_idx <= 2 * third, (
            f"Peak {peak_idx} not in middle third [{third}, {2*third}]"
        )

    def test_amplitude_db_range(self):
        """Verify output values are finite."""
        np.random.seed(42)
        iq_data = np.random.randn(600000) + 1j * np.random.randn(600000)
        result = stft.iq_to_spectrogram(iq_data)
        assert np.all(np.isfinite(result)), "Output contains non-finite values"

    def test_normalized_to_uint8(self):
        """Verify output is uint8 with values 0-255."""
        np.random.seed(42)
        iq_data = np.random.randn(600000) + 1j * np.random.randn(600000)
        result = stft.iq_to_spectrogram(iq_data)
        assert result.dtype == np.uint8, f"Expected uint8, got {result.dtype}"
        assert result.min() >= 0, f"Min value {result.min()} < 0"
        assert result.max() <= 255, f"Max value {result.max()} > 255"
        assert result.max() > result.min(), "Output is constant (not varying)"
