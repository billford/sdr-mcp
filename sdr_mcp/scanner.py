"""
FFT-based signal analysis and band scanning for RTL-SDR.

This module provides signal measurement and band scanning capabilities
using Fast Fourier Transform (FFT) based power spectral density analysis.

Signal Detection Approach:
    1. Collect IQ (In-phase/Quadrature) samples from the SDR
    2. Compute power spectral density using Welch's method
    3. Estimate noise floor using median (robust to narrowband signals)
    4. Calculate SNR as peak power minus noise floor
    5. Report signals exceeding the SNR threshold

Welch's Method:
    We use scipy.signal.welch() for PSD estimation because it:
    - Averages multiple periodograms, reducing variance
    - Applies windowing to reduce spectral leakage
    - Provides smoother, more reliable power estimates than raw FFT

Typical Use Cases:
    - Finding active frequencies in a band (repeaters, beacons)
    - Measuring signal strength at a known frequency
    - Spectrum surveys before setting up ADS-B monitoring

Example:
    >>> from sdr_mcp.hardware import get_device
    >>> from sdr_mcp.scanner import scan_band
    >>> device = get_device()
    >>> signals = scan_band(device, 144.0, 148.0, step_khz=25)
    >>> for s in signals:
    ...     print(f"{s.frequency_mhz} MHz: {s.strength_dbm} dBm")
"""

import logging
from typing import List, TYPE_CHECKING

import numpy as np
from scipy import signal as scipy_signal

from .models import SignalReading, ScanResult

if TYPE_CHECKING:
    from .hardware import RTLSDRDevice

logger = logging.getLogger(__name__)


def calculate_power_dbm(iq_samples: np.ndarray, sample_rate: float) -> tuple[float, float, float]:
    """Calculate signal power metrics from IQ samples.

    Args:
        iq_samples: Complex IQ samples from SDR.
        sample_rate: Sample rate in Hz.

    Returns:
        Tuple of (signal_strength_dbm, noise_floor_dbm, snr_db).
    """
    if len(iq_samples) == 0:
        return -100.0, -100.0, 0.0

    # Compute power spectrum using Welch's method for better noise estimation
    _, psd = scipy_signal.welch(
        iq_samples,
        fs=sample_rate,
        nperseg=min(1024, len(iq_samples)),
        return_onesided=False,
    )

    # Convert to dB scale (power spectral density)
    psd_db = 10 * np.log10(psd + 1e-12)  # Add small value to avoid log(0)

    # Signal strength: peak power
    signal_strength_dbm = float(np.max(psd_db))

    # Noise floor: use median (robust to signals)
    noise_floor_dbm = float(np.median(psd_db))

    # SNR
    snr_db = signal_strength_dbm - noise_floor_dbm

    return signal_strength_dbm, noise_floor_dbm, snr_db


def measure_frequency(
    device,
    frequency_mhz: float,
    dwell_ms: int = 500,
) -> SignalReading:
    """Tune to frequency and measure signal strength.

    Args:
        device: RTLSDRDevice instance.
        frequency_mhz: Target frequency in MHz.
        dwell_ms: Time to collect samples in milliseconds.

    Returns:
        SignalReading with power measurements.
    """
    # Calculate samples needed for dwell time
    sample_rate = device.sample_rate
    num_samples = int(sample_rate * dwell_ms / 1000)

    # Tune and collect samples
    device.tune(frequency_mhz)
    iq_samples = device.read_samples(num_samples)

    # Calculate power metrics
    strength, noise, snr = calculate_power_dbm(iq_samples, sample_rate)

    return SignalReading(
        frequency_mhz=frequency_mhz,
        signal_strength_dbm=round(strength, 1),
        noise_floor_dbm=round(noise, 1),
        snr_db=round(snr, 1),
    )


def scan_band(
    device,
    start_mhz: float,
    end_mhz: float,
    step_khz: int = 25,
    dwell_ms: int = 200,
    threshold_db: float = 6.0,
) -> List[ScanResult]:
    """Scan a frequency band and return signals above noise floor.

    Uses step-and-dwell approach: tune to each frequency, collect samples,
    measure power, move to next frequency.

    Args:
        device: RTLSDRDevice instance.
        start_mhz: Start frequency in MHz.
        end_mhz: End frequency in MHz.
        step_khz: Frequency step in kHz.
        dwell_ms: Dwell time at each frequency in ms.
        threshold_db: Minimum SNR to report a signal.

    Returns:
        List of ScanResult for signals above threshold, sorted by strength descending.

    Raises:
        ValueError: If start_mhz > end_mhz or step_khz <= 0.
    """
    from .hardware import HardwareState

    # Input validation
    if start_mhz > end_mhz:
        raise ValueError(f"start_mhz ({start_mhz}) must be <= end_mhz ({end_mhz})")
    if step_khz <= 0:
        raise ValueError(f"step_khz must be positive, got {step_khz}")
    if dwell_ms <= 0:
        raise ValueError(f"dwell_ms must be positive, got {dwell_ms}")

    results = []
    step_mhz = step_khz / 1000.0
    current_mhz = start_mhz
    errors = 0

    logger.info(f"Scanning {start_mhz}-{end_mhz} MHz, step={step_khz} kHz, dwell={dwell_ms} ms")

    # Set state to scanning
    device.set_state(HardwareState.SCANNING)

    try:
        while current_mhz <= end_mhz:
            try:
                reading = measure_frequency(device, current_mhz, dwell_ms)

                # Only include signals above threshold
                if reading.snr_db >= threshold_db:
                    results.append(ScanResult(
                        frequency_mhz=reading.frequency_mhz,
                        strength_dbm=reading.signal_strength_dbm,
                        snr_db=reading.snr_db,
                    ))
                    logger.debug(
                        f"Signal at {current_mhz:.3f} MHz: "
                        f"{reading.signal_strength_dbm:.1f} dBm, SNR {reading.snr_db:.1f} dB"
                    )
            except Exception as e:
                # Log but continue scanning other frequencies
                logger.warning(f"Error measuring {current_mhz:.3f} MHz: {e}")
                errors += 1
                # If too many consecutive errors, abort
                if errors > 10:
                    logger.error("Too many measurement errors, aborting scan")
                    break

            current_mhz += step_mhz

    finally:
        # Always return to idle state
        device.set_state(HardwareState.IDLE)

    # Sort by signal strength descending
    results.sort(key=lambda x: x.strength_dbm, reverse=True)

    logger.info(f"Scan complete: {len(results)} signals found above {threshold_db} dB threshold")

    return results
