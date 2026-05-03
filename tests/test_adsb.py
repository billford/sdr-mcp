"""Tests for ADS-B monitoring and decoding."""

import time
import pytest
from unittest.mock import patch, MagicMock

from sdr_mcp.adsb import ADSBMonitor, get_adsb_monitor, ADSB_FREQUENCY_MHZ
from sdr_mcp.models import Aircraft
from sdr_mcp.hardware import HardwareState


class TestADSBMonitor:
    """Tests for ADSBMonitor class."""

    @pytest.fixture
    def monitor(self):
        """Create a fresh monitor instance."""
        return ADSBMonitor()

    def test_initial_state(self, monitor):
        """Test monitor starts in correct state."""
        assert monitor._running is False
        assert monitor._aircraft == {}
        assert monitor._total_aircraft_seen == 0

    def test_start_sets_adsb_state(self, monitor, device):
        """Test that start() sets device to ADSB_ACTIVE."""
        # Prevent actual capture loop from running
        with patch.object(monitor, '_capture_loop'):
            monitor.start(device)
            assert device.state == HardwareState.ADSB_ACTIVE
            assert monitor._running is True
            monitor._running = False  # Clean up

    def test_start_tunes_to_1090(self, monitor, device):
        """Test that start() tunes to 1090 MHz."""
        with patch.object(monitor, '_capture_loop'):
            monitor.start(device)
            assert device.current_frequency_mhz == ADSB_FREQUENCY_MHZ
            monitor._running = False

    def test_stop_returns_stats(self, monitor, device):
        """Test that stop() returns session statistics."""
        with patch.object(monitor, '_capture_loop'):
            monitor.start(device)
            time.sleep(0.1)  # Brief delay for duration
            stats = monitor.stop()

            assert "total_aircraft" in stats
            assert "duration_seconds" in stats
            assert stats["duration_seconds"] >= 0.1

    def test_stop_sets_idle_state(self, monitor, device):
        """Test that stop() returns device to IDLE."""
        with patch.object(monitor, '_capture_loop'):
            monitor.start(device)
            monitor.stop()
            assert device.state == HardwareState.IDLE

    def test_get_aircraft_empty(self, monitor):
        """Test get_aircraft with no aircraft."""
        result = monitor.get_aircraft()
        assert result == []

    def test_get_aircraft_filters_stale(self, monitor):
        """Test that get_aircraft filters stale entries."""
        # Add aircraft with old timestamp
        old_ac = Aircraft(icao_hex="OLD123")
        old_ac.last_seen_timestamp = time.time() - 120  # 2 minutes ago

        new_ac = Aircraft(icao_hex="NEW456")
        new_ac.last_seen_timestamp = time.time()

        monitor._aircraft = {
            "OLD123": old_ac,
            "NEW456": new_ac,
        }

        result = monitor.get_aircraft(max_age_seconds=60)

        assert len(result) == 1
        assert result[0].icao_hex == "NEW456"

    def test_get_aircraft_sorted_by_recency(self, monitor):
        """Test that get_aircraft returns most recent first."""
        ac1 = Aircraft(icao_hex="FIRST")
        ac1.last_seen_timestamp = time.time() - 30

        ac2 = Aircraft(icao_hex="SECOND")
        ac2.last_seen_timestamp = time.time() - 10

        ac3 = Aircraft(icao_hex="THIRD")
        ac3.last_seen_timestamp = time.time() - 20

        monitor._aircraft = {
            "FIRST": ac1,
            "SECOND": ac2,
            "THIRD": ac3,
        }

        result = monitor.get_aircraft()

        assert result[0].icao_hex == "SECOND"
        assert result[1].icao_hex == "THIRD"
        assert result[2].icao_hex == "FIRST"


class TestADSBMessageDecoding:
    """Tests for ADS-B message decoding helpers."""

    @pytest.fixture
    def monitor(self):
        return ADSBMonitor()

    def test_bits_to_hex(self, monitor):
        """Test bit string to hex conversion."""
        # 8 bits = 2 hex chars
        bits = ['1', '1', '1', '1', '0', '0', '0', '0']  # 0xF0
        result = monitor._bits_to_hex(bits)
        assert result == "f0"

    def test_bits_to_hex_full_message(self, monitor):
        """Test conversion of full message length."""
        # 112 bits = 28 hex chars
        bits = ['0'] * 112
        result = monitor._bits_to_hex(bits)
        assert len(result) == 28
        assert result == "0" * 28

    def test_prune_stale(self, monitor):
        """Test stale aircraft removal."""
        old_ac = Aircraft(icao_hex="OLD123")
        old_ac.last_seen_timestamp = time.time() - 120

        new_ac = Aircraft(icao_hex="NEW456")

        monitor._aircraft = {
            "OLD123": old_ac,
            "NEW456": new_ac,
        }

        monitor._prune_stale()

        assert "OLD123" not in monitor._aircraft
        assert "NEW456" in monitor._aircraft


class TestGetADSBMonitorSingleton:
    """Tests for get_adsb_monitor singleton."""

    def test_returns_same_instance(self):
        """Test that get_adsb_monitor returns same instance."""
        import sdr_mcp.adsb
        from sdr_mcp.adsb import _monitor_lock

        # Reset singleton
        with _monitor_lock:
            sdr_mcp.adsb._monitor = None

        m1 = get_adsb_monitor()
        m2 = get_adsb_monitor()
        assert m1 is m2
