"""Pytest fixtures for SDR MCP tests."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

MOCK_SERIAL = "0000000000000000436c63dc38843c63"

MOCK_HACKRF_INFO_OUTPUT = f"""hackrf_info version: 2026.01.3
libhackrf version: 2026.01.3 (0.9.2)
Found HackRF
Index: 0
Serial number: {MOCK_SERIAL}
Board ID Number: 2 (HackRF One)
Firmware Version: 2024.02.1 (API:1.08)
Part ID Number: 0xa000cb3c 0x00614f5b
Hardware Revision: r10
"""


def _make_mock_iq_bytes(num_samples: int) -> bytes:
    """Build a fake HackRF capture: signed 8-bit interleaved I/Q, carrier + noise."""
    t = np.arange(num_samples) / 2.048e6
    signal = 40 * np.exp(2j * np.pi * 10000 * t)  # 10kHz offset carrier
    noise = 8 * (np.random.randn(num_samples) + 1j * np.random.randn(num_samples))
    iq = signal + noise
    i_vals = np.clip(iq.real, -128, 127).astype(np.int8)
    q_vals = np.clip(iq.imag, -128, 127).astype(np.int8)
    interleaved = np.empty(i_vals.size + q_vals.size, dtype=np.int8)
    interleaved[0::2] = i_vals
    interleaved[1::2] = q_vals
    return interleaved.tobytes()


@pytest.fixture
def mock_hackrf_cli():
    """Mock hackrf_info/hackrf_transfer subprocess calls for testing without hardware.

    Patches sdr_mcp.hardware.HACKRF_AVAILABLE to True and subprocess.run so that
    hackrf_info reports a connected device and hackrf_transfer writes a fake
    IQ capture file (carrier + noise) to whatever path is requested with -r.
    """
    def fake_run(cmd, **kwargs):  # pylint: disable=unused-argument
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if cmd[0] == "hackrf_info":
            result.stdout = MOCK_HACKRF_INFO_OUTPUT
        elif cmd[0] == "hackrf_transfer":
            out_path = cmd[cmd.index("-r") + 1]
            num_samples = int(cmd[cmd.index("-n") + 1])
            with open(out_path, "wb") as f:
                f.write(_make_mock_iq_bytes(num_samples))
            result.stdout = ""
        else:
            raise AssertionError(f"Unexpected command: {cmd}")
        return result

    with patch("sdr_mcp.hardware.HACKRF_AVAILABLE", True):
        with patch("sdr_mcp.hardware.subprocess.run", side_effect=fake_run):
            yield fake_run


@pytest.fixture
def device(mock_hackrf_cli):  # pylint: disable=unused-argument
    """Get a device instance with mocked hardware."""
    from sdr_mcp.hardware import HackRFDevice
    dev = HackRFDevice()
    dev.connect()
    return dev


@pytest.fixture
def disconnected_device():
    """Get a device instance without hardware connection.

    Patches HACKRF_AVAILABLE to False for the duration of the test.
    """
    with patch("sdr_mcp.hardware.HACKRF_AVAILABLE", False):
        from sdr_mcp.hardware import HackRFDevice
        dev = HackRFDevice()
        yield dev
