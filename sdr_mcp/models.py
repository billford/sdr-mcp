"""
Data models for SDR MCP server.

This module defines the core data structures used throughout the SDR MCP
server for representing signal measurements and aircraft tracking data.

All models are immutable dataclasses with JSON serialization support via
the to_dict() method.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


@dataclass
class SignalReading:
    """
    A single frequency measurement with power metrics.

    Returned by the tune_frequency tool when measuring a specific frequency.

    Attributes:
        frequency_mhz: The measured frequency in MHz.
        signal_strength_dbm: Peak signal power in dBm (decibels relative
            to one milliwatt). Higher values indicate stronger signals.
        noise_floor_dbm: Estimated noise floor in dBm. Calculated as the
            median power across the measurement bandwidth.
        snr_db: Signal-to-noise ratio in dB. Calculated as
            signal_strength_dbm - noise_floor_dbm. Values above 6 dB
            typically indicate a usable signal.
    """
    frequency_mhz: float
    signal_strength_dbm: float
    noise_floor_dbm: float
    snr_db: float

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ScanResult:
    """
    A detected signal from a band scan.

    Returned by scan_band for each frequency where a signal was detected
    above the noise floor threshold.

    Attributes:
        frequency_mhz: Frequency where signal was detected.
        strength_dbm: Signal power in dBm.
        snr_db: Signal-to-noise ratio in dB.
    """
    frequency_mhz: float
    strength_dbm: float
    snr_db: float

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class Aircraft:
    """
    A tracked aircraft from ADS-B reception.

    Represents the current known state of an aircraft. Fields are populated
    incrementally as different message types are received. Mode C transponders
    only provide altitude, so lat/lon may remain None.

    Attributes:
        icao_hex: 24-bit ICAO aircraft address in hexadecimal (e.g., "A12345").
            This is a globally unique identifier assigned to each aircraft.
        callsign: Flight callsign/flight number (e.g., "UAL123"). May be None
            if no identification message has been received.
        altitude_ft: Barometric altitude in feet. May be None.
        speed_kts: Ground speed in knots. May be None.
        heading_deg: Track angle in degrees (0-360, clockwise from north).
            May be None.
        lat: Latitude in decimal degrees. Requires CPR position decoding.
            May be None if position not yet determined.
        lon: Longitude in decimal degrees. May be None.
        last_seen_timestamp: Unix timestamp of last message received from
            this aircraft.
    """
    icao_hex: str
    callsign: Optional[str] = None
    altitude_ft: Optional[int] = None
    speed_kts: Optional[float] = None
    heading_deg: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    last_seen_timestamp: float = field(default_factory=time.time)

    @property
    def last_seen_seconds_ago(self) -> float:
        """Seconds since last message was received from this aircraft."""
        return time.time() - self.last_seen_timestamp

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "icao_hex": self.icao_hex,
            "callsign": self.callsign,
            "altitude_ft": self.altitude_ft,
            "speed_kts": self.speed_kts,
            "heading_deg": self.heading_deg,
            "lat": self.lat,
            "lon": self.lon,
            "last_seen_seconds_ago": round(self.last_seen_seconds_ago, 1),
        }
