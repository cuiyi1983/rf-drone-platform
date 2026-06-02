"""
STFT utilities for RFUAV inference.
Converts IQ data to spectrogram for YOLO detection.
MATCHES training code (stage1_generate_spectrograms.py) exactly.
"""

import numpy as np
from scipy.signal import stft
from scipy.signal.windows import hamming
from scipy.ndimage import zoom as scipy_zoom


# STFT parameters (MATCH training config)
SAMPLE_RATE = 60_000_000      # 60 MHz
NPERSEG = 1024                # FFT window size (=NFFT in training)
NOVLAP = 512                  # Hop size (=HOP in training)
WINDOW = hamming(NPERSEG)     # Hamming window (MATCH training)
TARGET_HEIGHT = 640
TARGET_WIDTH = 640


def iq_to_spectrogram(iq_data: np.ndarray, target_height: int = TARGET_HEIGHT, target_width: int = TARGET_WIDTH) -> np.ndarray:
    """
    Convert IQ data to spectrogram image (normalized to 0-255 for YOLO).
    MATCHES stage1_generate_spectrograms.py exactly:
      - scipy.signal.stft with same params
      - np.fft.fftshift on frequency axis
      - 10 * log10(|Z|) amplitude dB
      - min-max normalization
    """
    iq_data = np.asarray(iq_data, dtype=np.complex64)

    # Use first 600000 points if more available (MATCH training)
    if len(iq_data) >= 600000:
        iq_data = iq_data[:600000]
    else:
        raise ValueError(f"IQ data too short: {len(iq_data)} < 600000 (min required)")

    # Compute STFT with same params as training code
    f, t, Zxx = stft(
        iq_data,
        fs=SAMPLE_RATE,
        window=WINDOW,
        nperseg=NPERSEG,
        noverlap=NOVLAP,
        nfft=NPERSEG,
        boundary=None,
        padded=False
    )

    # fftshift on frequency axis (MATCH training: np.fft.fftshift(Zxx, axes=0))
    Zxx = np.fft.fftshift(Zxx, axes=0)

    # Amplitude dB: 10 * log10(|Z|) (MATCH training code exactly)
    mag_dB = 10 * np.log10(np.abs(Zxx) + 1e-10)

    # Min-max normalization (MATCH training: (dB - min) / (max - min))
    v_min = mag_dB.min()
    v_max = mag_dB.max()
    if v_max > v_min:
        spec = (mag_dB - v_min) / (v_max - v_min)
    else:
        spec = np.zeros_like(mag_dB)

    spectrogram = (spec * 255).astype(np.uint8)

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
        (n_freq_bins, n_frames) after fftshift
    """
    n_frames = (n_samples - NPERSEG) // NOVLAP + 1
    n_freq_bins = NPERSEG  # Full FFT size (fftshift brings DC to center)
    return (n_freq_bins, n_frames)
