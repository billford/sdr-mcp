"""
ADS-B (Automatic Dependent Surveillance-Broadcast) monitoring and decoding.

This module provides real-time aircraft tracking by decoding ADS-B Mode S
transponder signals at 1090 MHz using an RTL-SDR dongle.

ADS-B Overview:
    ADS-B is a surveillance technology where aircraft broadcast their
    position, altitude, speed, and identification. It operates on 1090 MHz
    and uses PPM (Pulse Position Modulation) with a 1 Mbps data rate.

Implementation:
    Uses dump1090-fa subprocess for professional-grade signal processing.
    dump1090 provides:
    - Optimized preamble detection and bit extraction
    - CPR position decoding (latitude/longitude from odd/even frames)
    - Error correction and validation
    - JSON output with complete aircraft state

    We read dump1090's aircraft.json output periodically to update our
    aircraft tracking state.

Example:
    >>> from sdr_mcp.adsb import get_adsb_monitor
    >>> monitor = get_adsb_monitor()
    >>> monitor.start()
    >>> # Wait for aircraft...
    >>> aircraft = monitor.get_aircraft()
    >>> for ac in aircraft:
    ...     print(f"{ac.callsign}: {ac.altitude_ft} ft at {ac.lat}, {ac.lon}")
    >>> monitor.stop()
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from .models import Aircraft
from .hardware import HardwareState, get_device

logger = logging.getLogger(__name__)

# Remove aircraft from tracking after this many seconds without updates
STALE_TIMEOUT_SECONDS = 60.0

# How often to read aircraft.json (seconds)
JSON_POLL_INTERVAL = 1.0

# Path to dump1090 binary (check common locations)
DUMP1090_PATHS = [
    "dump1090",  # In PATH
    "/opt/homebrew/bin/dump1090",  # Homebrew on Apple Silicon
    "/usr/local/bin/dump1090",  # Homebrew on Intel / Linux
    "/usr/bin/dump1090",  # System install
]


def find_dump1090() -> Optional[str]:
    """Find dump1090 binary."""
    for path in DUMP1090_PATHS:
        if shutil.which(path):
            return path
    return None


class ADSBMonitor:
    """Background ADS-B decoder using dump1090 subprocess."""

    def __init__(self):
        self._aircraft: Dict[str, Aircraft] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._json_dir: Optional[str] = None
        self._start_time: Optional[float] = None
        self._total_aircraft_seen = 0

    def start(self, device=None) -> None:  # pylint: disable=unused-argument
        """Start ADS-B monitoring.

        Args:
            device: Optional device parameter (ignored, kept for API compat).
                    dump1090 manages the RTL-SDR device directly.
        """
        if self._running:
            return

        # Find dump1090 binary
        dump1090_path = find_dump1090()
        if not dump1090_path:
            raise RuntimeError(
                "dump1090 not found. Install with: brew install dump1090-fa"
            )

        # Clean up any stale thread from a previous crash
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

        self._aircraft.clear()
        self._total_aircraft_seen = 0
        self._start_time = time.time()
        self._running = True

        # Create temp directory for JSON output
        self._json_dir = tempfile.mkdtemp(prefix="dump1090_")

        # Release Python's handle so dump1090 can claim the device
        try:
            dev = get_device()
            dev.disconnect()  # dump1090 needs exclusive access; disconnect resets state to IDLE
            dev.set_state(HardwareState.ADSB_ACTIVE)
        except Exception as e:
            logger.warning(f"Could not release device: {e}")

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info(f"ADS-B monitor started (using dump1090, json dir: {self._json_dir})")

    def stop(self) -> dict:
        """Stop ADS-B monitoring and return stats."""
        self._running = False

        # Terminate dump1090 subprocess
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception as e:
                logger.warning(f"Error terminating dump1090: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        # Clean up temp directory
        if self._json_dir and os.path.exists(self._json_dir):
            try:
                shutil.rmtree(self._json_dir)
            except Exception as e:
                logger.warning(f"Error cleaning up json dir: {e}")
            self._json_dir = None

        duration = time.time() - self._start_time if self._start_time else 0

        # Reset hardware state
        try:
            dev = get_device()
            dev.set_state(HardwareState.IDLE)
        except Exception as e:
            logger.warning(f"Error resetting hardware state: {e}")

        stats = {
            "total_aircraft": self._total_aircraft_seen,
            "duration_seconds": duration,
        }

        logger.info(f"ADS-B monitor stopped: {stats}")
        return stats

    def get_aircraft(self, max_age_seconds: float = STALE_TIMEOUT_SECONDS) -> List[Aircraft]:
        """Get aircraft seen within max_age_seconds."""
        with self._lock:
            now = time.time()
            result = []
            for aircraft in self._aircraft.values():
                age = now - aircraft.last_seen_timestamp
                if age <= max_age_seconds:
                    result.append(aircraft)
            return sorted(result, key=lambda a: a.last_seen_timestamp, reverse=True)

    def _capture_loop(self) -> None:
        """Background loop that runs dump1090 and reads JSON output."""
        dump1090_path = find_dump1090()

        try:
            cmd = [
                dump1090_path,
                "--quiet",
                "--write-json", self._json_dir,
                "--write-json-every", "1",
            ]

            # Retry up to 5 times — USB device may be transiently busy after reboot
            max_attempts = 5
            retry_delay = 15  # seconds between attempts
            for attempt in range(1, max_attempts + 1):
                logger.info(f"Starting dump1090 (attempt {attempt}/{max_attempts}): {' '.join(cmd)}")

                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )

                time.sleep(2)

                if self._process.poll() is None:
                    logger.info("dump1090 subprocess started")
                    break

                output = self._process.stdout.read()
                if attempt < max_attempts:
                    logger.warning(f"dump1090 failed to start (attempt {attempt}): {output.strip()} — retrying in {retry_delay}s")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"dump1090 failed to start after {max_attempts} attempts: {output.strip()}")
                    return

            # Poll aircraft.json periodically
            aircraft_json_path = Path(self._json_dir) / "aircraft.json"

            while self._running and self._process.poll() is None:
                try:
                    if aircraft_json_path.exists():
                        self._read_aircraft_json(aircraft_json_path)
                except Exception as e:
                    logger.debug(f"Error reading aircraft.json: {e}")

                time.sleep(JSON_POLL_INTERVAL)

            # Check if dump1090 exited with error
            if self._process and self._process.poll() is not None:
                output = self._process.stdout.read() if self._process.stdout else ""
                if output:
                    logger.warning(f"dump1090 exited: {output.strip()}")

        except Exception as e:
            logger.error(f"ADS-B capture error: {e}")
        finally:
            if self._process:
                try:
                    self._process.terminate()
                except Exception:
                    pass
                self._process = None

    def _read_aircraft_json(self, json_path: Path) -> None:
        """Read and process dump1090's aircraft.json file."""
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.debug(f"Error parsing aircraft.json: {e}")
            return

        aircraft_list = data.get('aircraft', [])
        now = time.time()

        with self._lock:
            for ac_data in aircraft_list:
                icao = ac_data.get('hex', '').upper()
                if not icao:
                    continue

                # Get or create aircraft
                if icao not in self._aircraft:
                    self._aircraft[icao] = Aircraft(icao_hex=icao)
                    self._total_aircraft_seen += 1
                    logger.debug(f"New aircraft: {icao}")

                ac = self._aircraft[icao]

                # Update last seen based on dump1090's 'seen' field
                seen_ago = ac_data.get('seen', 0)
                ac.last_seen_timestamp = now - seen_ago

                # Callsign (flight number)
                flight = ac_data.get('flight', '').strip()
                if flight:
                    ac.callsign = flight

                # Altitude (prefer barometric, fall back to geometric)
                alt = ac_data.get('alt_baro') or ac_data.get('alt_geom')
                if alt and alt != 'ground':
                    ac.altitude_ft = int(alt)

                # Ground speed in knots
                gs = ac_data.get('gs')
                if gs is not None:
                    ac.speed_kts = float(gs)

                # Track/heading in degrees
                track = ac_data.get('track')
                if track is not None:
                    ac.heading_deg = float(track)

                # Position (from CPR decoding)
                lat = ac_data.get('lat')
                lon = ac_data.get('lon')
                if lat is not None and lon is not None:
                    ac.lat = float(lat)
                    ac.lon = float(lon)

            # Prune stale aircraft
            self._prune_stale()

    def _prune_stale(self) -> None:
        """Remove aircraft not seen recently."""
        now = time.time()
        stale = [
            icao for icao, ac in self._aircraft.items()
            if now - ac.last_seen_timestamp > STALE_TIMEOUT_SECONDS
        ]
        for icao in stale:
            del self._aircraft[icao]


# Singleton
_monitor: Optional[ADSBMonitor] = None
_monitor_lock = threading.Lock()


def get_adsb_monitor() -> ADSBMonitor:
    """Get the global ADSBMonitor singleton."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = ADSBMonitor()
        return _monitor
