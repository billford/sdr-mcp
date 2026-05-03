"""Pytest fixtures for SDR MCP tests."""

import pytest
from unittest.mock import MagicMock, patch
import numpy as np


@pytest.fixture
def mock_rtlsdr():
    """Mock RtlSdr device for testing without hardware."""
    with patch('sdr_mcp.hardware.RTLSDR_AVAILABLE', True):
        mock_sdr = MagicMock()
        mock_sdr.sample_rate = 2.048e6
        mock_sdr.center_freq = 100e6
        mock_sdr.gain = "auto"

        # Generate mock IQ samples (noise + signal)
        def mock_read_samples(n):
            t = np.arange(n) / 2.048e6
            # Simulated signal: carrier + noise
            signal = 0.5 * np.exp(2j * np.pi * 10000 * t)  # 10kHz offset
            noise = 0.1 * (np.random.randn(n) + 1j * np.random.randn(n))
            return signal + noise

        mock_sdr.read_samples = mock_read_samples

        with patch('sdr_mcp.hardware.RtlSdr', return_value=mock_sdr):
            yield mock_sdr


@pytest.fixture
def device(mock_rtlsdr):
    """Get a device instance with mocked hardware."""
    from sdr_mcp.hardware import RTLSDRDevice
    dev = RTLSDRDevice()
    dev.connect()
    return dev


@pytest.fixture
def disconnected_device():
    """Get a device instance without hardware connection."""
    with patch('sdr_mcp.hardware.RTLSDR_AVAILABLE', False):
        with patch('sdr_mcp.hardware._IMPORT_ERROR', 'Test: no hardware'):
            from sdr_mcp.hardware import RTLSDRDevice
            return RTLSDRDevice()
