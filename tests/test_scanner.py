"""Tests for FFT-based scanning."""

import numpy as np

from sdr_mcp.scanner import calculate_power_dbm, measure_frequency, scan_band
from sdr_mcp.models import SignalReading, ScanResult
from sdr_mcp.hardware import HardwareState


class TestCalculatePowerDbm:
    """Tests for power calculation function."""

    def test_empty_samples(self):
        """Test handling of empty sample array."""
        strength, noise, snr = calculate_power_dbm(np.array([]), 2.048e6)
        assert strength == -100.0
        assert noise == -100.0
        assert snr == 0.0

    def test_pure_noise(self):
        """Test with pure noise (low SNR expected)."""
        np.random.seed(42)
        noise = 0.01 * (np.random.randn(10000) + 1j * np.random.randn(10000))
        strength, floor, snr = calculate_power_dbm(noise, 2.048e6)

        # Noise should have low SNR
        assert snr < 10.0
        # Floor and strength should be close for pure noise
        assert abs(strength - floor) < 15.0

    def test_signal_plus_noise(self):
        """Test with signal + noise (higher SNR expected)."""
        np.random.seed(42)
        t = np.arange(10000) / 2.048e6
        # Strong signal at 50kHz offset
        signal = 1.0 * np.exp(2j * np.pi * 50000 * t)
        noise = 0.01 * (np.random.randn(10000) + 1j * np.random.randn(10000))
        samples = signal + noise

        strength, floor, snr = calculate_power_dbm(samples, 2.048e6)

        # Should have significant SNR
        assert snr > 20.0
        # Signal should be stronger than noise floor
        assert strength > floor

    def test_returns_floats(self):
        """Test that function returns Python floats, not numpy types."""
        samples = np.random.randn(1000) + 1j * np.random.randn(1000)
        strength, noise, snr = calculate_power_dbm(samples, 2.048e6)

        assert isinstance(strength, float)
        assert isinstance(noise, float)
        assert isinstance(snr, float)


class TestMeasureFrequency:
    """Tests for frequency measurement."""

    def test_returns_signal_reading(self, device):
        """Test that measure_frequency returns SignalReading."""
        reading = measure_frequency(device, 144.39, dwell_ms=100)

        assert isinstance(reading, SignalReading)
        assert reading.frequency_mhz == 144.39
        assert isinstance(reading.signal_strength_dbm, float)
        assert isinstance(reading.noise_floor_dbm, float)
        assert isinstance(reading.snr_db, float)

    def test_tunes_device(self, device):
        """Test that measure_frequency tunes the device."""
        measure_frequency(device, 146.52, dwell_ms=100)
        assert device.current_frequency_mhz == 146.52

    def test_values_rounded(self, device):
        """Test that values are rounded to 1 decimal."""
        reading = measure_frequency(device, 145.0, dwell_ms=100)

        # Check values are rounded (no more than 1 decimal place)
        assert reading.signal_strength_dbm == round(reading.signal_strength_dbm, 1)
        assert reading.noise_floor_dbm == round(reading.noise_floor_dbm, 1)
        assert reading.snr_db == round(reading.snr_db, 1)


class TestScanBand:
    """Tests for band scanning."""

    def test_returns_list(self, device):
        """Test that scan_band returns a list."""
        results = scan_band(device, 144.0, 144.1, step_khz=50, dwell_ms=50)
        assert isinstance(results, list)

    def test_results_are_scan_results(self, device):
        """Test that results are ScanResult instances."""
        results = scan_band(device, 144.0, 144.1, step_khz=50, dwell_ms=50)
        for r in results:
            assert isinstance(r, ScanResult)

    def test_sorted_by_strength(self, device):
        """Test that results are sorted by strength descending."""
        results = scan_band(device, 144.0, 145.0, step_khz=100, dwell_ms=50)
        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i].strength_dbm >= results[i + 1].strength_dbm

    def test_frequency_range(self, device):
        """Test that results are within scanned range."""
        results = scan_band(
            device, 144.0, 145.0, step_khz=100, dwell_ms=50, threshold_db=0
        )
        for r in results:
            assert 144.0 <= r.frequency_mhz <= 145.0

    def test_returns_to_idle(self, device):
        """Test that device returns to IDLE state after scan."""
        device.set_state(HardwareState.IDLE)
        scan_band(device, 144.0, 144.1, step_khz=50, dwell_ms=50)
        assert device.state == HardwareState.IDLE

    def test_returns_to_idle_on_error(self, device):
        """Test that device returns to IDLE even when measurements fail."""
        device.set_state(HardwareState.IDLE)

        # Force errors by making read_samples raise
        original = device._sdr.read_samples
        device._sdr.read_samples = lambda n: (_ for _ in ()).throw(RuntimeError("Test"))

        try:
            # Scan should complete (with errors logged) rather than raise
            # because individual frequency errors are now caught and skipped
            results = scan_band(device, 144.0, 144.1, step_khz=50, dwell_ms=50)
            # Should return empty results since all measurements failed
            assert results == []
        finally:
            device._sdr.read_samples = original

        assert device.state == HardwareState.IDLE

    def test_threshold_filtering(self, device):
        """Test that threshold filters weak signals."""
        # With very high threshold, should get fewer/no results
        results_high = scan_band(
            device, 144.0, 144.1, step_khz=50, dwell_ms=50, threshold_db=100
        )
        results_low = scan_band(
            device, 144.0, 144.1, step_khz=50, dwell_ms=50, threshold_db=0
        )

        assert len(results_high) <= len(results_low)
