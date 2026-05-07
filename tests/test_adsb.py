"""Tests for ADS-B monitoring and decoding."""

import json
import tempfile
import time
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from sdr_mcp.adsb import ADSBMonitor, get_adsb_monitor, find_dump1090
from sdr_mcp.models import Aircraft


class TestFindDump1090:
    """Tests for dump1090 binary detection."""

    def test_find_dump1090_in_path(self):
        """Test finding dump1090 when it's in PATH."""
        with patch('shutil.which') as mock_which:
            mock_which.return_value = "/usr/bin/dump1090"
            result = find_dump1090()
            assert result == "dump1090"

    def test_find_dump1090_not_found(self):
        """Test when dump1090 is not installed."""
        with patch('shutil.which', return_value=None):
            result = find_dump1090()
            assert result is None


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

    def test_start_requires_dump1090(self, monitor):
        """Test that start() requires dump1090 binary."""
        with patch('sdr_mcp.adsb.find_dump1090', return_value=None):
            with pytest.raises(RuntimeError, match="dump1090 not found"):
                monitor.start()


class TestAircraftJsonParsing:
    """Tests for dump1090 aircraft.json parsing."""

    @pytest.fixture
    def monitor(self):
        return ADSBMonitor()

    @pytest.fixture
    def json_dir(self):
        """Create a temporary directory for JSON files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_read_aircraft_json_basic(self, monitor, json_dir):
        """Test parsing basic aircraft data."""
        aircraft_data = {
            "now": time.time(),
            "aircraft": [
                {
                    "hex": "a12345",
                    "flight": "UAL123  ",
                    "alt_baro": 35000,
                    "gs": 450.5,
                    "track": 270.0,
                    "lat": 41.5,
                    "lon": -81.6,
                    "seen": 0.5,
                }
            ]
        }

        json_path = Path(json_dir) / "aircraft.json"
        with open(json_path, 'w') as f:
            json.dump(aircraft_data, f)

        monitor._read_aircraft_json(json_path)

        assert len(monitor._aircraft) == 1
        ac = monitor._aircraft["A12345"]
        assert ac.callsign == "UAL123"
        assert ac.altitude_ft == 35000
        assert ac.speed_kts == 450.5
        assert ac.heading_deg == 270.0
        assert ac.lat == 41.5
        assert ac.lon == -81.6

    def test_read_aircraft_json_multiple(self, monitor, json_dir):
        """Test parsing multiple aircraft."""
        aircraft_data = {
            "now": time.time(),
            "aircraft": [
                {"hex": "a11111", "flight": "DAL100"},
                {"hex": "a22222", "flight": "AAL200"},
                {"hex": "a33333", "flight": "SWA300"},
            ]
        }

        json_path = Path(json_dir) / "aircraft.json"
        with open(json_path, 'w') as f:
            json.dump(aircraft_data, f)

        monitor._read_aircraft_json(json_path)

        assert len(monitor._aircraft) == 3
        assert monitor._total_aircraft_seen == 3

    def test_read_aircraft_json_updates_existing(self, monitor, json_dir):
        """Test that existing aircraft are updated."""
        # First read
        aircraft_data = {
            "now": time.time(),
            "aircraft": [{"hex": "a12345", "alt_baro": 30000, "seen": 0}]
        }
        json_path = Path(json_dir) / "aircraft.json"
        with open(json_path, 'w') as f:
            json.dump(aircraft_data, f)
        monitor._read_aircraft_json(json_path)

        # Second read with updated altitude
        aircraft_data["aircraft"][0]["alt_baro"] = 35000
        with open(json_path, 'w') as f:
            json.dump(aircraft_data, f)
        monitor._read_aircraft_json(json_path)

        # Should still be 1 aircraft, with updated altitude
        assert len(monitor._aircraft) == 1
        assert monitor._total_aircraft_seen == 1  # Not incremented
        assert monitor._aircraft["A12345"].altitude_ft == 35000

    def test_read_aircraft_json_handles_ground(self, monitor, json_dir):
        """Test handling of aircraft on ground."""
        aircraft_data = {
            "now": time.time(),
            "aircraft": [{"hex": "a12345", "alt_baro": "ground", "seen": 0}]
        }

        json_path = Path(json_dir) / "aircraft.json"
        with open(json_path, 'w') as f:
            json.dump(aircraft_data, f)

        monitor._read_aircraft_json(json_path)

        # Should not set altitude for ground aircraft
        assert monitor._aircraft["A12345"].altitude_ft is None

    def test_read_aircraft_json_invalid_file(self, monitor, json_dir):
        """Test handling of invalid JSON."""
        json_path = Path(json_dir) / "aircraft.json"
        with open(json_path, 'w') as f:
            f.write("not valid json{{{")

        # Should not raise, just log
        monitor._read_aircraft_json(json_path)
        assert len(monitor._aircraft) == 0

    def test_prune_stale(self, monitor):
        """Test stale aircraft removal."""
        old_ac = Aircraft(icao_hex="OLD123")
        old_ac.last_seen_timestamp = time.time() - 120

        new_ac = Aircraft(icao_hex="NEW456")
        new_ac.last_seen_timestamp = time.time()

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
