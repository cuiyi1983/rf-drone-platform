"""
STFT utilities for RFUAV inference.
Converts IQ data to spectrogram for YOLO detection.
"""

import numpy as np
from scipy.signal import windows
from scipy.ndimage import zoom as scipy_zoom


# STFT parameters (from RF-Training config)
SAMPLE_RATE = 60_000_000      # 60 MHz
NPERSEG = 1024                # FFT window size
HOP = 512                     # Hop size
WINDOW_NAME = "hamming"       # Window function
# Input: 600000 points → ~1169 frames STFT
# STFT output shape: (n_frames, n_freq_bins) = (~1169, 513)


def get_window():
    """Get the STFT window function."""
    if WINDOW_NAME == "hamming":
        return windows.hamming(NPERSEG)
    elif WINDOW_NAME == "hann":
        return windows.hann(NPERSEG)
    elif WINDOW_NAME == "blackman":
        return windows.blackman(NPERSEG)
    else:
        return windows.hamming(NPERSEG)


def iq_to_spectrogram(iq_data: np.ndarray, target_height: int = 640, target_width: int = 640) -> np.ndarray:
    """
    Convert IQ data to spectrogram image (normalized to 0-255 for YOLO).

    Args:
        iq_data: Complex IQ array, shape (N,) where N >= 600000
        target_height: Target image height (default 640 for YOLO)
        target_width: Target image width (default 640 for YOLO)

    Returns:
        spectrogram: 2D numpy array (target_height, target_width), dtype uint8
                     Values 0-255 representing spectrogram power in dB
    """
    iq_data = np.asarray(iq_data, dtype=np.complex64)

    # Use first 600000 points if more available
    if len(iq_data) >= 600000:
        iq_data = iq_data[:600000]
    else:
        raise ValueError(f"IQ data too short: {len(iq_data)} < 600000 (min required)")

    # Compute STFT
    window = get_window()
    n_frames = (len(iq_data) - NPERSEG) // HOP + 1

    # Compute STFT manually to get (n_freq_bins, n_frames)
    # Use full FFT since IQ data is complex; we'll take magnitude later
    stft_matrix = np.zeros((NPERSEG, n_frames), dtype=np.complex64)

    for i in range(n_frames):
        start = i * HOP
        segment = iq_data[start:start + NPERSEG]
        if len(segment) < NPERSEG:
            break
        # Ensure segment is complex64
        segment = segment.astype(np.complex64)
        window_f = window.astype(np.float32)
        stft_matrix[:, i] = np.fft.fft(segment * window_f)

    # Apply fftshift to move zero frequency to center (on full 1024 bins)
    stft_matrix = np.fft.fftshift(stft_matrix, axes=0)

    # Take magnitude (only first half after fftshift = negative freq to +fs/2)
    stft_matrix = np.abs(stft_matrix[:NPERSEG // 2 + 1, :])

    # Convert to dB scale (amplitude dB, matching training amp_type: dB)
    # 10*log10(|Z|) not 10*log10(|Z|^2) = 20*log10(|Z|)
    spectrogram_db = 10 * np.log10(stft_matrix + 1e-10)

    # Normalize to 0-255 (minmax, matching training config)
    spec_min = spectrogram_db.min()
    spec_max = spectrogram_db.max()
    spectrogram_norm = (spectrogram_db - spec_min) / (spec_max - spec_min + 1e-10)
    spectrogram = (spectrogram_norm * 255).astype(np.uint8)

    # Resize to target size using bilinear interpolation
    h_scale = target_height / spectrogram.shape[0]
    w_scale = target_width / spectrogram.shape[1]
    spectrogram_resized = scipy_zoom(spectrogram, (h_scale, w_scale), order=1)

    # Ensure exact target size
    spectrogram_resized = spectrogram_resized[:target_height, :target_width]

    return spectrogram_resized.astype(np.uint8)


def stft_shape_from_data_length(n_samples: int = 600000) -> tuple:
    """
    Calculate STFT output shape given input sample count.

    Returns:
        (n_frames, n_freq_bins)
    """
    n_frames = (n_samples - NPERSEG) // HOP + 1
    n_freq_bins = NPERSEG // 2 + 1
    return (n_frames, n_freq_bins)