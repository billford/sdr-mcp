"""Tests for data models."""

import time

from sdr_mcp.models import SignalReading, ScanResult, Aircraft


class TestSignalReading:
    """Tests for SignalReading dataclass."""

    def test_creation(self):
        """Test basic creation."""
        reading = SignalReading(
            frequency_mhz=144.39,
            signal_strength_dbm=-45.2,
            noise_floor_dbm=-95.0,
            snr_db=49.8,
        )
        assert reading.frequency_mhz == 144.39
        assert reading.signal_strength_dbm == -45.2
        assert reading.noise_floor_dbm == -95.0
        assert reading.snr_db == 49.8

    def test_to_dict(self):
        """Test JSON serialization."""
        reading = SignalReading(
            frequency_mhz=146.52,
            signal_strength_dbm=-50.0,
            noise_floor_dbm=-90.0,
            snr_db=40.0,
        )
        d = reading.to_dict()
        assert d == {
            "frequency_mhz": 146.52,
            "signal_strength_dbm": -50.0,
            "noise_floor_dbm": -90.0,
            "snr_db": 40.0,
        }


class TestScanResult:
    """Tests for ScanResult dataclass."""

    def test_creation(self):
        """Test basic creation."""
        result = ScanResult(
            frequency_mhz=145.0,
            strength_dbm=-60.0,
            snr_db=15.0,
        )
        assert result.frequency_mhz == 145.0
        assert result.strength_dbm == -60.0
        assert result.snr_db == 15.0

    def test_to_dict(self):
        """Test JSON serialization."""
        result = ScanResult(
            frequency_mhz=147.0,
            strength_dbm=-55.0,
            snr_db=20.0,
        )
        d = result.to_dict()
        assert d == {
            "frequency_mhz": 147.0,
            "strength_dbm": -55.0,
            "snr_db": 20.0,
        }


class TestAircraft:
    """Tests for Aircraft dataclass."""

    def test_minimal_creation(self):
        """Test creation with only required field."""
        ac = Aircraft(icao_hex="A12345")
        assert ac.icao_hex == "A12345"
        assert ac.callsign is None
        assert ac.altitude_ft is None
        assert ac.speed_kts is None
        assert ac.heading_deg is None
        assert ac.lat is None
        assert ac.lon is None

    def test_full_creation(self):
        """Test creation with all fields."""
        ac = Aircraft(
            icao_hex="A12345",
            callsign="UAL123",
            altitude_ft=35000,
            speed_kts=450.5,
            heading_deg=270.0,
            lat=41.5,
            lon=-81.6,
        )
        assert ac.icao_hex == "A12345"
        assert ac.callsign == "UAL123"
        assert ac.altitude_ft == 35000
        assert ac.speed_kts == 450.5
        assert ac.heading_deg == 270.0
        assert ac.lat == 41.5
        assert ac.lon == -81.6

    def test_last_seen_seconds_ago(self):
        """Test last_seen_seconds_ago property."""
        ac = Aircraft(icao_hex="A12345")
        # Should be very recent
        assert ac.last_seen_seconds_ago < 1.0

        # Manually set timestamp in past
        ac.last_seen_timestamp = time.time() - 30.0
        assert 29.0 < ac.last_seen_seconds_ago < 31.0

    def test_to_dict(self):
        """Test JSON serialization."""
        ac = Aircraft(
            icao_hex="A12345",
            callsign="UAL123",
            altitude_ft=35000,
        )
        d = ac.to_dict()
        assert d["icao_hex"] == "A12345"
        assert d["callsign"] == "UAL123"
        assert d["altitude_ft"] == 35000
        assert d["speed_kts"] is None
        assert d["lat"] is None
        assert "last_seen_seconds_ago" in d
        assert isinstance(d["last_seen_seconds_ago"], float)

    def test_to_dict_null_fields(self):
        """Test that Mode C transponder data (altitude only) serializes correctly."""
        ac = Aircraft(
            icao_hex="ABCDEF",
            altitude_ft=28000,
        )
        d = ac.to_dict()
        assert d["icao_hex"] == "ABCDEF"
        assert d["callsign"] is None
        assert d["altitude_ft"] == 28000
        assert d["lat"] is None
        assert d["lon"] is None
