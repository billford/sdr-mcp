"""
MCP server for SDR hardware access (HackRF + RTL-SDR).

This module implements the Model Context Protocol (MCP) server that exposes
SDR functionality to Claude Desktop. It uses stdio transport for
communication and provides tools for frequency tuning, band scanning
(HackRF), ADS-B aircraft tracking (RTL-SDR, via dump1090), and AIS vessel
tracking (HackRF, via AIS-catcher).

MCP Protocol:
    The server communicates via JSON-RPC over stdin/stdout. All logging
    is directed to stderr to avoid corrupting the transport.

Available Tools:
    - sdr_status: Query hardware state
    - tune_frequency: Measure signal at specific frequency
    - scan_band: Sweep frequency range for active signals
    - start_adsb_monitor: Begin aircraft tracking
    - stop_adsb_monitor: End aircraft tracking
    - get_aircraft: List currently tracked aircraft

Usage:
    Run as a module:
        python -m sdr_mcp.server

    Or via Claude Desktop configuration:
        {
            "mcpServers": {
                "sdr": {
                    "command": "python",
                    "args": ["-m", "sdr_mcp.server"]
                }
            }
        }
"""

import atexit
import json
import logging
import signal
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .hardware import get_device, HardwareError, HardwareBusyError
from .config import load_config

# Load configuration
config = load_config()

# Configure logging to stderr (stdout is MCP transport)
handlers = [logging.StreamHandler(sys.stderr)]
if config.log_file:
    handlers.append(logging.FileHandler(config.log_file))

logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=handlers,
)
logger = logging.getLogger(__name__)


def cleanup():
    """Clean up hardware on exit."""
    try:
        from .adsb import get_adsb_monitor
        adsb = get_adsb_monitor()
        if adsb._running:
            adsb.stop()
        from .ais import get_ais_monitor
        ais = get_ais_monitor()
        if ais._running:
            ais.stop()
        device = get_device()
        device.disconnect()
        logger.info("Cleanup complete")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


def signal_handler(signum, _frame):
    """Handle termination signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    cleanup()
    sys.exit(0)


# Register cleanup handlers
atexit.register(cleanup)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Create the MCP server
server = Server("sdr-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available SDR tools."""
    return [
        Tool(
            name="sdr_status",
            description=(
                "Get HackRF hardware status. Returns connection state, "
                "current mode (idle/scanning/adsb/ais), gain, sample rate, and "
                "active frequency if tuned. Works even if dongle is not connected."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="tune_frequency",
            description=(
                "Tune to a specific frequency, collect IQ samples, and return "
                "signal metrics including signal strength, noise floor, and SNR."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "frequency_mhz": {
                        "type": "number",
                        "description": "Target frequency in MHz (e.g., 144.39 for APRS)",
                    },
                    "dwell_ms": {
                        "type": "integer",
                        "description": "Time to collect samples in milliseconds",
                        "default": 500,
                    },
                },
                "required": ["frequency_mhz"],
            },
        ),
        Tool(
            name="scan_band",
            description=(
                "FFT sweep across a frequency range. Returns signals above noise "
                "floor, sorted by strength descending. Cannot run while ADS-B "
                "monitor is active."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "start_mhz": {
                        "type": "number",
                        "description": "Start frequency in MHz",
                    },
                    "end_mhz": {
                        "type": "number",
                        "description": "End frequency in MHz",
                    },
                    "step_khz": {
                        "type": "integer",
                        "description": "Frequency step in kHz",
                        "default": 25,
                    },
                    "dwell_ms": {
                        "type": "integer",
                        "description": "Dwell time at each frequency in ms",
                        "default": 200,
                    },
                },
                "required": ["start_mhz", "end_mhz"],
            },
        ),
        Tool(
            name="start_adsb_monitor",
            description=(
                "Lock hardware to 1090 MHz and start background ADS-B decoding. "
                "Aircraft will be tracked continuously until stop_adsb_monitor is called."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="stop_adsb_monitor",
            description=(
                "Stop ADS-B monitoring and release hardware for other operations."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_aircraft",
            description=(
                "Get currently tracked aircraft. Requires ADS-B monitor to be running. "
                "Returns aircraft seen within the last 60 seconds with ICAO hex, "
                "callsign, altitude, speed, heading, and position (if available)."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="start_ais_monitor",
            description=(
                "Start AIS vessel tracking on Lake Erie VHF channels (161.975 / 162.025 MHz). "
                "Launches AIS-catcher, which takes exclusive HackRF ownership. "
                "Cannot run while tune_frequency or scan_band is active on the HackRF."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="stop_ais_monitor",
            description=(
                "Stop AIS vessel tracking and release hardware for other operations. "
                "Returns total vessels seen and session duration."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="get_vessels",
            description=(
                "Get currently tracked vessels. Requires AIS monitor to be running. "
                "Returns vessels seen within max_age_seconds (default 300). "
                "Each vessel includes MMSI, name, position, speed, heading, course, "
                "navigation status, vessel type, and destination where available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "max_age_seconds": {
                        "type": "integer",
                        "description": "Maximum age of vessel data to return (seconds)",
                        "default": 300,
                    },
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    logger.info(f"Tool called: {name} with arguments: {arguments}")

    try:
        if name == "sdr_status":
            return await handle_sdr_status()
        elif name == "tune_frequency":
            return await handle_tune_frequency(arguments)
        elif name == "scan_band":
            return await handle_scan_band(arguments)
        elif name == "start_adsb_monitor":
            return await handle_start_adsb_monitor()
        elif name == "stop_adsb_monitor":
            return await handle_stop_adsb_monitor()
        elif name == "get_aircraft":
            return await handle_get_aircraft()
        elif name == "start_ais_monitor":
            return await handle_start_ais_monitor()
        elif name == "stop_ais_monitor":
            return await handle_stop_ais_monitor()
        elif name == "get_vessels":
            return await handle_get_vessels(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except HardwareBusyError as e:
        return [TextContent(type="text", text=f"Hardware busy: {e}")]
    except HardwareError as e:
        return [TextContent(type="text", text=f"Hardware error: {e}")]
    except Exception as e:
        logger.exception(f"Unexpected error in {name}")
        return [TextContent(type="text", text=f"Error: {e}")]


async def handle_sdr_status() -> list[TextContent]:
    """Handle sdr_status tool call."""
    device = get_device()
    status = device.get_status()

    lines = [
        f"dongle_connected: {status['dongle_connected']}",
        f"mode: {status['mode']}",
        f"gain: {status['gain']}",
        f"sample_rate: {status['sample_rate']} Hz",
        f"active_frequency_mhz: {status['active_frequency_mhz']}",
    ]
    result = "\n".join(lines)

    logger.info(f"sdr_status result: {status}")
    return [TextContent(type="text", text=result)]


async def handle_tune_frequency(arguments: dict) -> list[TextContent]:
    """Handle tune_frequency tool call."""
    from .scanner import measure_frequency

    frequency_mhz = arguments["frequency_mhz"]
    dwell_ms = arguments.get("dwell_ms", 500)

    device = get_device()
    device.require_idle()

    reading = measure_frequency(device, frequency_mhz, dwell_ms)

    lines = [
        f"frequency_mhz: {reading.frequency_mhz}",
        f"signal_strength_dbm: {reading.signal_strength_dbm}",
        f"noise_floor_dbm: {reading.noise_floor_dbm}",
        f"snr_db: {reading.snr_db}",
    ]
    result = "\n".join(lines)

    logger.info(f"tune_frequency result: {reading.to_dict()}")
    return [TextContent(type="text", text=result)]


async def handle_scan_band(arguments: dict) -> list[TextContent]:
    """Handle scan_band tool call."""
    from .scanner import scan_band

    start_mhz = arguments["start_mhz"]
    end_mhz = arguments["end_mhz"]
    step_khz = arguments.get("step_khz", 25)
    dwell_ms = arguments.get("dwell_ms", 200)

    # Validate frequency range isn't too large (prevent accidental hour-long scans)
    max_range_mhz = 100  # Reasonable limit
    if end_mhz - start_mhz > max_range_mhz:
        return [TextContent(
            type="text",
            text=(
                f"Scan range too large ({end_mhz - start_mhz} MHz). "
                f"Maximum is {max_range_mhz} MHz."
            )
        )]

    device = get_device()
    device.require_idle()

    results = scan_band(device, start_mhz, end_mhz, step_khz, dwell_ms)

    if not results:
        return [TextContent(type="text", text="No signals found above noise floor.")]

    # Format as JSON array
    output = json.dumps([r.to_dict() for r in results], indent=2)

    logger.info(f"scan_band found {len(results)} signals")
    return [TextContent(type="text", text=output)]


async def handle_start_adsb_monitor() -> list[TextContent]:
    """Handle start_adsb_monitor tool call."""
    from .adsb import get_adsb_monitor

    # Check device is idle (not scanning)
    device = get_device()
    device.require_idle()

    monitor = get_adsb_monitor()
    monitor.start()  # rtl_adsb subprocess manages device directly

    return [TextContent(
        type="text",
        text="ADS-B monitor started. Listening on 1090 MHz.\nInitial aircraft count: 0"
    )]


async def handle_stop_adsb_monitor() -> list[TextContent]:
    """Handle stop_adsb_monitor tool call."""
    from .adsb import get_adsb_monitor

    monitor = get_adsb_monitor()
    stats = monitor.stop()

    lines = [
        "ADS-B monitor stopped.",
        f"Total aircraft seen: {stats['total_aircraft']}",
        f"Session duration: {stats['duration_seconds']:.1f} seconds",
    ]

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_get_aircraft() -> list[TextContent]:
    """Handle get_aircraft tool call."""
    from .adsb import get_adsb_monitor
    from .hardware import HardwareState

    device = get_device()
    if device.state != HardwareState.ADSB_ACTIVE:
        return [TextContent(
            type="text",
            text="ADS-B monitor not running. Call start_adsb_monitor first."
        )]

    monitor = get_adsb_monitor()
    aircraft = monitor.get_aircraft()

    if not aircraft:
        return [TextContent(type="text", text="No aircraft currently tracked.")]

    output = json.dumps([a.to_dict() for a in aircraft], indent=2)

    logger.info(f"get_aircraft returning {len(aircraft)} aircraft")
    return [TextContent(type="text", text=output)]


async def handle_start_ais_monitor() -> list[TextContent]:
    """Handle start_ais_monitor tool call."""
    from .ais import get_ais_monitor

    device = get_device()
    device.require_idle()

    monitor = get_ais_monitor()
    monitor.start()

    return [TextContent(
        type="text",
        text="AIS monitor started. Listening on 161.975 MHz and 162.025 MHz.\n"
             "Initial vessel count: 0"
    )]


async def handle_stop_ais_monitor() -> list[TextContent]:
    """Handle stop_ais_monitor tool call."""
    from .ais import get_ais_monitor

    monitor = get_ais_monitor()
    stats = monitor.stop()

    lines = [
        "AIS monitor stopped.",
        f"Total vessels seen: {stats['total_vessels']}",
        f"Session duration: {stats['duration_seconds']:.1f} seconds",
    ]
    return [TextContent(type="text", text="\n".join(lines))]


async def handle_get_vessels(arguments: dict) -> list[TextContent]:
    """Handle get_vessels tool call."""
    from .ais import get_ais_monitor
    from .hardware import HardwareState

    device = get_device()
    if device.state != HardwareState.AIS_ACTIVE:
        return [TextContent(
            type="text",
            text="AIS monitor not running. Call start_ais_monitor first."
        )]

    max_age = arguments.get("max_age_seconds", 300)
    monitor = get_ais_monitor()
    vessels = monitor.get_vessels(max_age_seconds=float(max_age))

    if not vessels:
        return [TextContent(type="text", text="No vessels currently tracked.")]

    output = json.dumps([v.to_dict() for v in vessels], indent=2)
    logger.info(f"get_vessels returning {len(vessels)} vessels")
    return [TextContent(type="text", text=output)]


async def main():
    """Run the MCP server."""
    logger.info("Starting SDR MCP server...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def run():
    """Entry point for running the server."""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    run()
