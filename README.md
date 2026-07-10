# SDR MCP Server

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

An MCP (Model Context Protocol) server that gives Claude Desktop real-time access to SDR hardware. Tune frequencies, scan bands for signal activity, decode live ADS-B aircraft traffic, and track AIS vessels on inland waterways — all through natural conversation.

**This project uses two physical SDRs, not one:**

- **HackRF One** — frequency tuning, band scanning, and AIS vessel tracking
- **RTL-SDR dongle** — ADS-B aircraft tracking specifically

This split isn't arbitrary: the Homebrew-distributed `dump1090-fa` binary is linked against `librtlsdr`/`libbladerf`, not `libhackrf`, so it cannot use a HackRF at all. AIS-catcher, on the other hand, has native HackRF support, so AIS runs on the HackRF instead, leaving the RTL-SDR free for ADS-B duty. If you only have a HackRF, everything works except `start_adsb_monitor`/`get_aircraft`.

## Features

- **Frequency Tuning** (HackRF) — Tune to any frequency and measure signal strength, noise floor, and SNR
- **Band Scanning** (HackRF) — FFT-based spectrum sweep to find active signals in a frequency range
- **ADS-B Tracking** (RTL-SDR) — Real-time aircraft monitoring with callsign, altitude, speed, heading, and position (lat/lon)
- **AIS Vessel Tracking** (HackRF) — Maritime vessel monitoring with MMSI, name, position, speed, heading, vessel type, and destination
- **Thread-Safe** — Mutex-protected hardware access with proper state machine
- **Graceful Degradation** — Works without hardware connected (reports status accurately)

## Requirements

- Python 3.11+
- HackRF One, with `hackrf` installed (`brew install hackrf`) — provides `hackrf_info`/`hackrf_transfer`, which this project drives directly (see [HackRF Setup](#hackrf-setup) below for why there's no pip package)
- RTL-SDR dongle (tested with RTL-SDR Blog V4) — only needed for `start_adsb_monitor`/`get_aircraft`
- dump1090-fa (for ADS-B tracking — RTL-SDR only, see note above)
- AIS-catcher (for AIS vessel tracking — built from source with HackRF support)
- macOS, Linux, or Windows

## HackRF Setup

```bash
brew install hackrf
hackrf_info   # should print "Found HackRF" and a serial number
```

There is deliberately no `pip install pyhackrf2` step here. That package hardcodes `ctypes.CDLL("libhackrf.so.0")`, a Linux shared-object name that never resolves on macOS (Homebrew installs `libhackrf.dylib`). `sdr_mcp/hardware.py` instead shells out to `hackrf_transfer`/`hackrf_info`, the same binaries you just verified above, using temp-file captures instead of a persistent ctypes handle.

### Installing dump1090 (for ADS-B — RTL-SDR only)

```bash
# macOS
brew install dump1090-fa

# Debian/Ubuntu
sudo apt install dump1090-fa
```

### Installing AIS-catcher (for AIS — runs on the HackRF)

AIS-catcher is not in Homebrew — build from source. Since `libhackrf` is already installed above, AIS-catcher's CMake build will auto-detect it and compile in HackRF support:

```bash
brew install git make gcc cmake pkg-config sqlite3
git clone https://github.com/jvde-github/AIS-catcher.git --depth 1
cd AIS-catcher
mkdir build && cd build
cmake ..
make
sudo make install
```

## Quick Start

```bash
# Clone the repository
git clone https://github.com/billford/sdr-mcp.git
cd sdr-mcp

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# Install
pip install -e .

# Test (should show dongle_connected: False if no hardware)
python -c "from sdr_mcp.hardware import get_device; print(get_device().get_status())"
```

## Claude Desktop Configuration

### Local (MacBook with dongle attached)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "sdr": {
      "command": "/path/to/sdr-mcp/.venv/bin/python",
      "args": ["-m", "sdr_mcp.server"],
      "cwd": "/path/to/sdr-mcp"
    }
  }
}
```

### Remote (Raspberry Pi over SSH)

Run the server on a Pi with the dongle attached and connect via SSH stdio — no open ports needed:

```json
{
  "mcpServers": {
    "sdr": {
      "command": "ssh",
      "args": [
        "-i", "/Users/yourname/.ssh/id_ed25519_sdr",
        "-o", "StrictHostKeyChecking=no",
        "yourname@sdr-pi.local",
        "/home/yourname/sdr-mcp/.venv/bin/python -m sdr_mcp.server"
      ]
    }
  }
}
```

See [RASPBERRY_PI.md](RASPBERRY_PI.md) for the full Pi setup walkthrough, including Tailscale for remote access.

## Available Tools

| Tool | Description |
|------|-------------|
| `sdr_status` | Get hardware status: connection state, mode, gain, sample rate |
| `tune_frequency` | Tune to frequency, return signal strength and SNR |
| `scan_band` | FFT sweep across frequency range, find active signals |
| `start_adsb_monitor` | Begin tracking aircraft on 1090 MHz |
| `stop_adsb_monitor` | Stop tracking and return session statistics |
| `get_aircraft` | List currently tracked aircraft with details |
| `start_ais_monitor` | Begin tracking vessels on AIS VHF channels (161.975 / 162.025 MHz) |
| `stop_ais_monitor` | Stop vessel tracking and return session statistics |
| `get_vessels` | List currently tracked vessels with position, speed, type, and destination |

## Example Conversations

Once configured, you can ask Claude:

> "What's the SDR status?"

> "Scan the 2 meter ham band from 144 to 148 MHz"

> "Start tracking aircraft"

> "What planes are overhead right now?"

> "Stop tracking and tell me how many aircraft we saw"

> "Start AIS vessel tracking"

> "What ships are on Lake Erie right now?"

> "Stop AIS and show me the summary"

## RTL-SDR Blog V4 Setup (macOS, ADS-B dongle only)

This section is about the RTL-SDR dongle used for ADS-B specifically — it's unrelated to the HackRF setup above. The standard Homebrew librtlsdr may lack symbols needed by the V4 dongle. Install the RTL-SDR Blog fork:

```bash
# Remove standard librtlsdr
brew uninstall librtlsdr

# Clone and build RTL-SDR Blog fork
git clone https://github.com/rtlsdrblog/rtl-sdr-blog.git
cd rtl-sdr-blog
mkdir build && cd build
cmake ..
make
sudo make install

# Add to ~/.zshrc
export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH
```

## Configuration

Create `config.toml` in the project root (see `config.toml.example`):

```toml
[sdr]
gain = "auto"                    # or numeric value like 40
sample_rate = 2048000            # 2.048 MSPS (standard)
stale_timeout_seconds = 60.0     # Remove aircraft after no updates
noise_floor_threshold_db = 6.0   # Minimum SNR to report signals

[logging]
level = "INFO"                   # DEBUG for verbose output
# file = "/tmp/sdr-mcp.log"      # Optional file logging
```

## Architecture

```
sdr_mcp/
├── server.py      # MCP entry point, tool handlers
├── hardware.py    # HackRFDevice with mutex + state machine (hackrf_transfer/hackrf_info CLI)
├── scanner.py     # FFT power analysis, band sweeping (HackRF)
├── adsb.py        # Background ADS-B decoder using dump1090 (separate RTL-SDR)
├── ais.py         # Background AIS decoder using AIS-catcher (HackRF)
├── models.py      # SignalReading, ScanResult, Aircraft, Vessel
└── config.py      # TOML configuration loader
```

### Hardware State Machine

The state machine is shared across `hardware.py`'s HackRF-backed operations (tune/scan/AIS) and the RTL-SDR-backed ADS-B monitor, even though they're two physically independent USB devices:

```
IDLE → tune_frequency() → IDLE            [HackRF]
IDLE → scan_band() → SCANNING → IDLE      [HackRF]
IDLE → start_adsb_monitor() → ADSB_ACTIVE [RTL-SDR, via dump1090]
ADSB_ACTIVE → get_aircraft() → ADSB_ACTIVE
ADSB_ACTIVE → stop_adsb_monitor() → IDLE
IDLE → start_ais_monitor() → AIS_ACTIVE   [HackRF, via AIS-catcher]
AIS_ACTIVE → get_vessels() → AIS_ACTIVE
AIS_ACTIVE → stop_ais_monitor() → IDLE
```

Only the AIS lock reflects a real hardware constraint (AIS-catcher takes exclusive ownership of the *same* HackRF that tune/scan use). The ADS-B lock is a software convention carried over from the original single-dongle design — since dump1090 uses a separate RTL-SDR, tune_frequency/scan_band could in principle run concurrently with ADS-B. That's left as a possible future relaxation rather than something this port assumes is safe without testing.

Conflicting operations return an error rather than blocking.

## Dependencies

**Python packages:**
- [mcp](https://pypi.org/project/mcp/) — Model Context Protocol SDK
- [numpy](https://pypi.org/project/numpy/) — Numerical computing
- [scipy](https://pypi.org/project/scipy/) — FFT and signal processing

No HackRF Python binding is used — see [HackRF Setup](#hackrf-setup) for why.

**External tools:**
- `hackrf_info` / `hackrf_transfer` (from `brew install hackrf`) — drives tune/scan capture
- [dump1090-fa](https://github.com/flightaware/dump1090) — ADS-B decoder (RTL-SDR only)
- [AIS-catcher](https://github.com/jvde-github/AIS-catcher) — AIS decoder (HackRF-capable, built from source)

## AIS Reception Notes

AIS signals on Lake Erie are receivable from Chagrin Falls (~20 miles inland) at 23–24 dB SNR on both channels with an indoor antenna. A few things to know:

- **Best times** — Commercial traffic is busiest in the morning and evening. Afternoons, especially in summer, tend to be slower.
- **Range** — Open-water vessels on the lake should be receivable. Harbor traffic in Cleveland or Toledo may be marginal at this distance.
- **Update rate** — Vessels update position every few minutes, not continuously like aircraft.
- **Coverage** — An outdoor antenna pointed north would significantly improve range and vessel count.

## License

MIT License — see [LICENSE](LICENSE) file.

## Contributing

Contributions welcome! Please open an issue first to discuss changes.

## Acknowledgments

- [Great Scott Gadgets](https://greatscottgadgets.com/hackrf/) for the HackRF One and its host tools
- [RTL-SDR Blog](https://www.rtl-sdr.com/) for hardware and driver support
- [FlightAware](https://github.com/flightaware/dump1090) for dump1090-fa ADS-B decoder
- [jvde-github](https://github.com/jvde-github/AIS-catcher) for AIS-catcher vessel decoder
- [Anthropic](https://anthropic.com) for MCP and Claude
