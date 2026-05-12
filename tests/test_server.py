"""Tests for MCP server."""

import json
from unittest.mock import patch, MagicMock

import pytest


class TestToolHandlers:
    """Tests for individual tool handlers."""

    @pytest.mark.asyncio
    async def test_handle_sdr_status(self):
        """Test sdr_status handler."""
        from sdr_mcp.server import handle_sdr_status

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.get_status.return_value = {
                "dongle_connected": False,
                "mode": "idle",
                "gain": "auto",
                "sample_rate": 2048000,
                "active_frequency_mhz": None,
            }
            mock_get.return_value = mock_device

            result = await handle_sdr_status()

            assert len(result) == 1
            text = result[0].text
            assert "dongle_connected: False" in text
            assert "mode: idle" in text

    @pytest.mark.asyncio
    async def test_handle_tune_frequency(self):
        """Test tune_frequency handler."""
        from sdr_mcp.server import handle_tune_frequency
        from sdr_mcp.models import SignalReading

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.require_idle = MagicMock()
            mock_get.return_value = mock_device

            # Patch in scanner module where it's defined
            with patch('sdr_mcp.scanner.measure_frequency') as mock_measure:
                mock_measure.return_value = SignalReading(
                    frequency_mhz=144.39,
                    signal_strength_dbm=-50.0,
                    noise_floor_dbm=-90.0,
                    snr_db=40.0,
                )

                result = await handle_tune_frequency({"frequency_mhz": 144.39})

                assert len(result) == 1
                text = result[0].text
                assert "frequency_mhz: 144.39" in text
                assert "signal_strength_dbm: -50.0" in text

    @pytest.mark.asyncio
    async def test_handle_scan_band(self):
        """Test scan_band handler."""
        from sdr_mcp.server import handle_scan_band
        from sdr_mcp.models import ScanResult

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.require_idle = MagicMock()
            mock_get.return_value = mock_device

            # Patch in scanner module where it's defined
            with patch('sdr_mcp.scanner.scan_band') as mock_scan:
                mock_scan.return_value = [
                    ScanResult(frequency_mhz=145.0, strength_dbm=-50.0, snr_db=20.0),
                    ScanResult(frequency_mhz=146.0, strength_dbm=-55.0, snr_db=15.0),
                ]

                result = await handle_scan_band({
                    "start_mhz": 144.0,
                    "end_mhz": 148.0,
                })

                assert len(result) == 1
                data = json.loads(result[0].text)
                assert len(data) == 2
                assert data[0]["frequency_mhz"] == 145.0

    @pytest.mark.asyncio
    async def test_handle_scan_band_no_results(self):
        """Test scan_band handler with no signals found."""
        from sdr_mcp.server import handle_scan_band

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.require_idle = MagicMock()
            mock_get.return_value = mock_device

            # Patch in scanner module where it's defined
            with patch('sdr_mcp.scanner.scan_band') as mock_scan:
                mock_scan.return_value = []

                result = await handle_scan_band({
                    "start_mhz": 144.0,
                    "end_mhz": 148.0,
                })

                assert "No signals found" in result[0].text

    @pytest.mark.asyncio
    async def test_handle_get_aircraft_not_running(self):
        """Test get_aircraft when monitor not running."""
        from sdr_mcp.server import handle_get_aircraft
        from sdr_mcp.hardware import HardwareState

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.state = HardwareState.IDLE
            mock_get.return_value = mock_device

            result = await handle_get_aircraft()

            assert "not running" in result[0].text

    @pytest.mark.asyncio
    async def test_handle_get_aircraft_with_data(self):
        """Test get_aircraft with tracked aircraft."""
        from sdr_mcp.server import handle_get_aircraft
        from sdr_mcp.hardware import HardwareState
        from sdr_mcp.models import Aircraft

        with patch('sdr_mcp.server.get_device') as mock_get:
            mock_device = MagicMock()
            mock_device.state = HardwareState.ADSB_ACTIVE
            mock_get.return_value = mock_device

            # Patch in adsb module where it's defined
            with patch('sdr_mcp.adsb.get_adsb_monitor') as mock_mon:
                mock_monitor = MagicMock()
                mock_monitor.get_aircraft.return_value = [
                    Aircraft(icao_hex="A12345", callsign="UAL123", altitude_ft=35000)
                ]
                mock_mon.return_value = mock_monitor

                result = await handle_get_aircraft()

                data = json.loads(result[0].text)
                assert len(data) == 1
                assert data[0]["icao_hex"] == "A12345"
                assert data[0]["callsign"] == "UAL123"


class TestToolDispatch:
    """Tests for tool dispatch in call_tool."""

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Test handling of unknown tool name."""
        from sdr_mcp.server import call_tool

        result = await call_tool("nonexistent_tool", {})

        assert "Unknown tool" in result[0].text

    @pytest.mark.asyncio
    async def test_hardware_busy_error(self):
        """Test handling of HardwareBusyError."""
        from sdr_mcp.server import call_tool
        from sdr_mcp.hardware import HardwareBusyError

        with patch('sdr_mcp.server.handle_tune_frequency') as mock_handler:
            mock_handler.side_effect = HardwareBusyError("ADS-B monitor active")

            result = await call_tool("tune_frequency", {"frequency_mhz": 144.0})

            assert "Hardware busy" in result[0].text

    @pytest.mark.asyncio
    async def test_hardware_error(self):
        """Test handling of HardwareError."""
        from sdr_mcp.server import call_tool
        from sdr_mcp.hardware import HardwareError

        with patch('sdr_mcp.server.handle_tune_frequency') as mock_handler:
            mock_handler.side_effect = HardwareError("Dongle not connected")

            result = await call_tool("tune_frequency", {"frequency_mhz": 144.0})

            assert "Hardware error" in result[0].text


class TestListTools:
    """Tests for list_tools handler."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_all(self):
        """Test that list_tools returns all expected tools."""
        from sdr_mcp.server import list_tools

        tools = await list_tools()
        tool_names = [t.name for t in tools]

        assert "sdr_status" in tool_names
        assert "tune_frequency" in tool_names
        assert "scan_band" in tool_names
        assert "start_adsb_monitor" in tool_names
        assert "stop_adsb_monitor" in tool_names
        assert "get_aircraft" in tool_names

    @pytest.mark.asyncio
    async def test_tools_have_descriptions(self):
        """Test that all tools have descriptions."""
        from sdr_mcp.server import list_tools

        tools = await list_tools()

        for tool in tools:
            assert tool.description
            assert len(tool.description) > 10

    @pytest.mark.asyncio
    async def test_tools_have_schemas(self):
        """Test that all tools have input schemas."""
        from sdr_mcp.server import list_tools

        tools = await list_tools()

        for tool in tools:
            assert tool.inputSchema
            assert tool.inputSchema["type"] == "object"
