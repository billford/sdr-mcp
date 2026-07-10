"""
SDR MCP Server - HackRF and RTL-SDR access for Claude Desktop.

This package provides an MCP (Model Context Protocol) server that gives
Claude Desktop real-time access to SDR hardware. It enables:

- Frequency tuning and signal measurement (HackRF)
- Band scanning with FFT-based signal detection (HackRF)
- Live ADS-B aircraft tracking and decoding (RTL-SDR, via dump1090)
- Live AIS vessel tracking (HackRF or RTL-SDR, via AIS-catcher)

Note: tune/scan and AIS run against a HackRF One. ADS-B specifically
requires a separate RTL-SDR dongle -- dump1090 has no HackRF backend in
the Homebrew-distributed build. See README.md for details.

Example usage with Claude Desktop:
    "What's the SDR status?"
    "Scan 144-148 MHz for activity"
    "Start tracking aircraft"
    "What planes are overhead Cleveland?"

Modules:
    server: MCP server entry point and tool handlers
    hardware: HackRF device abstraction with thread-safe access
    scanner: FFT-based signal analysis and band scanning
    adsb: ADS-B Mode S decoding and aircraft tracking (RTL-SDR/dump1090)
    ais: AIS vessel tracking (AIS-catcher)
    models: Data classes for signals, aircraft, and vessels
    config: Configuration file and environment handling

For more information, see:
    https://github.com/billford/sdr-mcp
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__license__ = "MIT"

from .hardware import get_device, HardwareState, HardwareError
from .models import Aircraft, SignalReading, ScanResult

__all__ = [
    "get_device",
    "HardwareState",
    "HardwareError",
    "Aircraft",
    "SignalReading",
    "ScanResult",
]
