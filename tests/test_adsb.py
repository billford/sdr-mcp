"""Tests for ADS-B monitoring and decoding."""

import time
import pytest
from unittest.mock import patch, MagicMock

from sdr_mcp.adsb import ADSBMonitor, get_adsb_monitor
from sdr_mcp.models import Aircraft


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

    def test_stop_returns_stats(self, monitor):
        """Test that stop() returns session statistics."""
        monitor._start_time = time.time() - 1.0
        monitor._running = True
        monitor._total_aircraft_seen = 5

        with patch('sdr_mcp.adsb.get_device'):
            stats = monitor.stop()

        assert stats["total_aircraft"] == 5
        assert stats["duration_seconds"] >= 1.0

    def test_start_requires_rtl_adsb(self, monitor):
        """Test that start() requires rtl_adsb binary."""
        with patch('shutil.which', return_value=None):
            with pytest.raises(RuntimeError, match="rtl_adsb not found"):
                monitor.start()

    def test_start_requires_pymodes(self, monitor):
        """Test that start() requires pyModeS."""
        with patch('sdr_mcp.adsb.PYMODES_AVAILABLE', False):
            with pytest.raises(RuntimeError, match="pyModeS not available"):
                monitor.start()


class TestADSBLineProcessing:
    """Tests for rtl_adsb output line processing."""

    @pytest.fixture
    def monitor(self):
        return ADSBMonitor()

    def test_process_line_valid_message(self, monitor):
        """Test processing valid rtl_adsb output line."""
        # Real DF17 ADS-B message (28 hex chars)
        with patch.object(monitor, '_decode_message') as mock_decode:
            monitor._process_line("*8da8e1f6ea485864ed5c0898d970;")
            mock_decode.assert_called_once_with("8da8e1f6ea485864ed5c0898d970")

    def test_process_line_ignores_invalid_prefix(self, monitor):
        """Test that lines without * prefix are ignored."""
        with patch.object(monitor, '_decode_message') as mock_decode:
            monitor._process_line("8da8e1f6ea485864ed5c0898d970;")
            mock_decode.assert_not_called()

    def test_process_line_ignores_invalid_suffix(self, monitor):
        """Test that lines without ; suffix are ignored."""
        with patch.object(monitor, '_decode_message') as mock_decode:
            monitor._process_line("*8da8e1f6ea485864ed5c0898d970")
            mock_decode.assert_not_called()

    def test_process_line_ignores_wrong_length(self, monitor):
        """Test that messages with wrong length are ignored."""
        with patch.object(monitor, '_decode_message') as mock_decode:
            # Too short
            monitor._process_line("*8da8e1f6;")
            mock_decode.assert_not_called()

            # Too long
            monitor._process_line("*8da8e1f6ea485864ed5c0898d970abcd;")
            mock_decode.assert_not_called()


class TestADSBMessageDecoding:
    """Tests for ADS-B message decoding."""

    @pytest.fixture
    def monitor(self):
        return ADSBMonitor()

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
