"""
SDR MCP Server - RTL-SDR access for Claude Desktop.

This package provides an MCP (Model Context Protocol) server that gives
Claude Desktop real-time access to RTL-SDR hardware. It enables:

- Frequency tuning and signal measurement
- Band scanning with FFT-based signal detection
- Live ADS-B aircraft tracking and decoding

Example usage with Claude Desktop:
    "What's the SDR status?"
    "Scan 144-148 MHz for activity"
    "Start tracking aircraft"
    "What planes are overhead Cleveland?"

Modules:
    server: MCP server entry point and tool handlers
    hardware: RTL-SDR device abstraction with thread-safe access
    scanner: FFT-based signal analysis and band scanning
    adsb: ADS-B Mode S decoding and aircraft tracking
    models: Data classes for signals and aircraft
    config: Configuration file and environment handling

For more information, see:
    https://github.com/YOUR_USERNAME/sdr-mcp
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
