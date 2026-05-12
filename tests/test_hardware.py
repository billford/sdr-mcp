"""Tests for hardware abstraction layer."""

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


class TestRTLSDRDeviceWithoutHardware:
    """Tests for RTLSDRDevice when hardware is not available."""

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


class TestRTLSDRDeviceWithMockHardware:
    """Tests for RTLSDRDevice with mocked hardware."""

    def test_connect_succeeds(self, device):
        """Test connection succeeds with mock hardware."""
        assert device.connected is True

    def test_get_status_connected(self, device):
        """Test status shows connected."""
        status = device.get_status()
        assert status["dongle_connected"] is True
        assert status["mode"] == "idle"

    def test_tune_frequency(self, device):
        """Test tuning to a frequency."""
        device.tune(144.39)
        assert device.current_frequency_mhz == 144.39

    def test_read_samples(self, device):
        """Test reading samples."""
        samples = device.read_samples(1024)
        assert len(samples) == 1024
        # Samples should be complex
        assert samples.dtype == complex

    def test_disconnect(self, device):
        """Test disconnecting."""
        device.disconnect()
        assert device.connected is False
        assert device.state == HardwareState.IDLE
        assert device.current_frequency_mhz is None


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


class TestGetDevice:
    """Tests for get_device singleton."""

    def test_singleton(self):
        """Test get_device returns same instance."""
        from sdr_mcp.hardware import get_device, _device, _device_lock

        # Reset singleton for test
        import sdr_mcp.hardware
        with _device_lock:
            sdr_mcp.hardware._device = None

        d1 = get_device()
        d2 = get_device()
        assert d1 is d2
