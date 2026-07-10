"""Tests for hardware abstraction layer."""

import subprocess
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from sdr_mcp.hardware import (
    HardwareState,
    HardwareError,
    HardwareBusyError,
)


class TestHardwareState:
    """Tests for HardwareState enum."""

    def test_values(self):
        """Test enum values match expected strings."""
        assert HardwareState.IDLE.value == "idle"
        assert HardwareState.SCANNING.value == "scanning"
        assert HardwareState.ADSB_ACTIVE.value == "adsb"
        assert HardwareState.AIS_ACTIVE.value == "ais"


class TestHackRFDeviceWithoutHardware:
    """Tests for HackRFDevice when hardware is not available."""

    def test_initial_state(self, disconnected_device):
        """Test device starts in correct initial state."""
        assert disconnected_device.state == HardwareState.IDLE
        assert disconnected_device.connected is False
        assert disconnected_device.current_frequency_mhz is None
        assert disconnected_device.gain == "auto"
        assert disconnected_device.sample_rate == 2.048e6

    def test_connect_fails_without_hardware(self, disconnected_device):
        """Test connection fails gracefully without hardware."""
        result = disconnected_device.connect()
        assert result is False
        assert disconnected_device.connected is False

    def test_check_connection_without_hardware(self, disconnected_device):
        """Test connection check returns False without hardware."""
        assert disconnected_device.check_connection() is False

    def test_get_status_without_hardware(self, disconnected_device):
        """Test status returns correct values without hardware."""
        status = disconnected_device.get_status()
        assert status["dongle_connected"] is False
        assert status["mode"] == "idle"
        assert status["gain"] == "auto"
        assert status["sample_rate"] == 2048000
        assert status["active_frequency_mhz"] is None

    def test_require_connected_raises(self, disconnected_device):
        """Test require_connected raises HardwareError."""
        with pytest.raises(HardwareError, match="not connected"):
            disconnected_device.require_connected()

    def test_get_serial_returns_none(self, disconnected_device):
        """Test get_serial returns None without hardware."""
        assert disconnected_device.get_serial() is None


class TestHackRFDeviceWithMockHardware:
    """Tests for HackRFDevice with mocked hackrf_info/hackrf_transfer."""

    def test_connect_succeeds(self, device):
        """Test connection succeeds with mock hardware."""
        assert device.connected is True

    def test_get_status_connected(self, device):
        """Test status shows connected."""
        status = device.get_status()
        assert status["dongle_connected"] is True
        assert status["mode"] == "idle"

    def test_get_serial(self, device):
        """Test serial number is parsed from hackrf_info output."""
        assert device.get_serial() == "0000000000000000436c63dc38843c63"

    def test_tune_frequency(self, device):
        """Test tuning to a frequency."""
        device.tune(144.39)
        assert device.current_frequency_mhz == 144.39

    def test_read_samples_requires_tune_first(self, device):
        """Test read_samples raises if tune() was never called."""
        with pytest.raises(HardwareError, match="Must call tune"):
            device.read_samples(1024)

    def test_read_samples(self, device):
        """Test reading samples via mocked hackrf_transfer."""
        device.tune(100.0)
        samples = device.read_samples(1024)
        assert len(samples) == 1024
        assert samples.dtype == complex

    def test_read_samples_zero(self, device):
        """Test reading zero samples returns an empty array."""
        device.tune(100.0)
        samples = device.read_samples(0)
        assert len(samples) == 0

    def test_disconnect(self, device):
        """Test disconnecting."""
        device.disconnect()
        assert device.connected is False
        assert device.state == HardwareState.IDLE
        assert device.current_frequency_mhz is None

    def test_check_connection_when_already_connected(self, device):
        """Test check_connection short-circuits without a subprocess call when connected."""
        assert device.connected is True
        with patch("sdr_mcp.hardware.subprocess.run") as mock_run:
            assert device.check_connection() is True
            mock_run.assert_not_called()

    def test_get_serial_uses_cache(self, device):
        """Test get_serial returns the value cached by connect() without another subprocess call."""
        assert device.get_serial() == "0000000000000000436c63dc38843c63"
        with patch("sdr_mcp.hardware.subprocess.run") as mock_run:
            assert device.get_serial() == "0000000000000000436c63dc38843c63"
            mock_run.assert_not_called()

    def test_read_bytes(self, device):
        """Test read_bytes returns the requested number of bytes."""
        device.tune(100.0)
        data = device.read_bytes(2048)
        assert isinstance(data, bytes)
        assert len(data) == 2048


class TestHackRFDeviceErrorPaths:
    """Tests for error handling in hackrf_info/hackrf_transfer subprocess calls."""

    def test_get_serial_without_prior_connect(self, mock_hackrf_cli):  # pylint: disable=unused-argument
        """Test get_serial() itself triggers hackrf_info when not yet cached."""
        from sdr_mcp.hardware import HackRFDevice

        dev = HackRFDevice()
        serial = dev.get_serial()
        assert serial == "0000000000000000436c63dc38843c63"

    def test_hackrf_info_not_found_in_output(self):
        """Test connect() fails when hackrf_info runs but reports no device."""
        from sdr_mcp.hardware import HackRFDevice

        fake_result = MagicMock(returncode=1, stdout="No HackRF boards found.\n", stderr="")
        with patch("sdr_mcp.hardware.HACKRF_AVAILABLE", True):
            with patch("sdr_mcp.hardware.subprocess.run", return_value=fake_result):
                dev = HackRFDevice()
                assert dev.connect() is False

    def test_hackrf_info_subprocess_error(self):
        """Test connect() fails gracefully if hackrf_info raises."""
        from sdr_mcp.hardware import HackRFDevice

        with patch("sdr_mcp.hardware.HACKRF_AVAILABLE", True):
            with patch(
                "sdr_mcp.hardware.subprocess.run", side_effect=OSError("no such file")
            ):
                dev = HackRFDevice()
                assert dev.connect() is False

    def test_capture_nonzero_returncode_raises(self, device):
        """Test read_samples raises HardwareError when hackrf_transfer exits non-zero."""
        device.tune(100.0)
        fake_result = MagicMock(returncode=1, stdout="", stderr="hackrf_open() failed")
        with patch("sdr_mcp.hardware.subprocess.run", return_value=fake_result):
            with pytest.raises(HardwareError, match="hackrf_transfer failed"):
                device.read_samples(1024)

    def test_capture_timeout_raises(self, device):
        """Test read_samples raises HardwareError when hackrf_transfer times out."""
        device.tune(100.0)
        with patch(
            "sdr_mcp.hardware.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="hackrf_transfer", timeout=1.0),
        ):
            with pytest.raises(HardwareError, match="timed out"):
                device.read_samples(1024)

    def test_capture_oserror_raises(self, device):
        """Test read_samples raises HardwareError when hackrf_transfer can't be launched."""
        device.tune(100.0)
        with patch("sdr_mcp.hardware.subprocess.run", side_effect=OSError("not found")):
            with pytest.raises(HardwareError, match="Failed to run hackrf_transfer"):
                device.read_samples(1024)

    def test_read_iq_file_empty(self, tmp_path):
        """Test _read_iq_file returns an empty array for an empty capture file."""
        from sdr_mcp.hardware import HackRFDevice

        empty_file = tmp_path / "empty.iq"
        empty_file.write_bytes(b"")
        result = HackRFDevice._read_iq_file(empty_file, 1024)  # pylint: disable=protected-access
        assert len(result) == 0
        assert result.dtype == np.complex128

    def test_set_gain_auto(self, device):
        """Test setting gain to auto uses fixed defaults."""
        device.set_gain("auto")
        assert device.gain == "auto"

    def test_set_gain_numeric(self, device):
        """Test setting a numeric gain applies (and rounds) VGA gain."""
        device.set_gain(35)
        assert device.gain == 35
        # 35 rounds down to nearest 2dB step -> 34
        assert device._vga_gain_db == 34  # pylint: disable=protected-access


class TestHardwareStateMachine:
    """Tests for hardware state machine transitions."""

    def test_set_state(self, device):
        """Test setting state."""
        device.set_state(HardwareState.SCANNING)
        assert device.state == HardwareState.SCANNING

        device.set_state(HardwareState.IDLE)
        assert device.state == HardwareState.IDLE

    def test_require_idle_when_idle(self, device):
        """Test require_idle passes when idle."""
        device.set_state(HardwareState.IDLE)
        device.require_idle()  # Should not raise

    def test_require_idle_when_scanning(self, device):
        """Test require_idle raises when scanning."""
        device.set_state(HardwareState.SCANNING)
        with pytest.raises(HardwareBusyError, match="Scan in progress"):
            device.require_idle()

    def test_require_idle_when_adsb_active(self, device):
        """Test require_idle raises when ADS-B active."""
        device.set_state(HardwareState.ADSB_ACTIVE)
        with pytest.raises(HardwareBusyError, match="ADS-B monitor active"):
            device.require_idle()

    def test_require_idle_when_ais_active(self, device):
        """Test require_idle raises when AIS active."""
        device.set_state(HardwareState.AIS_ACTIVE)
        with pytest.raises(HardwareBusyError, match="AIS monitor active"):
            device.require_idle()


class TestGetDevice:
    """Tests for get_device singleton."""

    def test_singleton(self):
        """Test get_device returns same instance."""
        from sdr_mcp.hardware import get_device, _device_lock
        import sdr_mcp.hardware

        # Reset singleton for test
        with _device_lock:
            sdr_mcp.hardware._device = None  # pylint: disable=protected-access

        d1 = get_device()
        d2 = get_device()
        assert d1 is d2
