"""
ADS-B (Automatic Dependent Surveillance-Broadcast) monitoring and decoding.

This module provides real-time aircraft tracking by decoding ADS-B Mode S
transponder signals at 1090 MHz using an RTL-SDR dongle.

ADS-B Overview:
    ADS-B is a surveillance technology where aircraft broadcast their
    position, altitude, speed, and identification. It operates on 1090 MHz
    and uses PPM (Pulse Position Modulation) with a 1 Mbps data rate.

Message Types Decoded:
    - DF17 (ADS-B Extended Squitter): Position, velocity, identification
    - DF11 (All-Call Reply): ICAO address only

    Type Codes (TC) within DF17:
    - TC 1-4: Aircraft identification (callsign)
    - TC 9-18: Airborne position (altitude, lat/lon)
    - TC 19: Airborne velocity (speed, heading, vertical rate)

Implementation:
    Uses rtl_adsb subprocess for reliable signal detection and demodulation.
    The C-based rtl_adsb provides optimized preamble detection and bit
    extraction. We parse its output and decode messages using pyModeS.

Example:
    >>> from sdr_mcp.adsb import get_adsb_monitor
    >>> monitor = get_adsb_monitor()
    >>> monitor.start()
    >>> # Wait for aircraft...
    >>> aircraft = monitor.get_aircraft()
    >>> for ac in aircraft:
    ...     print(f"{ac.callsign}: {ac.altitude_ft} ft")
    >>> monitor.stop()
"""

import logging
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

try:
    import pyModeS as pms
    PYMODES_AVAILABLE = True
except ImportError:
    PYMODES_AVAILABLE = False

from .models import Aircraft
from .hardware import HardwareState, get_device

logger = logging.getLogger(__name__)

# Remove aircraft from tracking after this many seconds without updates
STALE_TIMEOUT_SECONDS = 60.0


class ADSBMonitor:
    """Background ADS-B decoder using rtl_adsb subprocess."""

    def __init__(self):
        self._aircraft: Dict[str, Aircraft] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._start_time: Optional[float] = None
        self._total_aircraft_seen = 0

    def start(self, device=None) -> None:
        """Start ADS-B monitoring.

        Args:
            device: Optional device parameter (ignored, kept for API compat).
                    We use rtl_adsb which manages the device directly.
        """
        if self._running:
            return

        if not PYMODES_AVAILABLE:
            raise RuntimeError("pyModeS not available")

        # Check rtl_adsb is available
        if not shutil.which("rtl_adsb"):
            raise RuntimeError("rtl_adsb not found. Install rtl-sdr tools.")

        # Clean up any stale thread from a previous crash
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

        self._aircraft.clear()
        self._total_aircraft_seen = 0
        self._start_time = time.time()
        self._running = True

        # Mark hardware as ADS-B active (for status reporting)
        try:
            dev = get_device()
            dev.set_state(HardwareState.ADSB_ACTIVE)
        except Exception as e:
            logger.warning(f"Could not set hardware state: {e}")

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info("ADS-B monitor started (using rtl_adsb)")

    def stop(self) -> dict:
        """Stop ADS-B monitoring and return stats."""
        self._running = False

        # Terminate rtl_adsb subprocess
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception as e:
                logger.warning(f"Error terminating rtl_adsb: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

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
        """Background loop that runs rtl_adsb and parses output."""
        try:
            # Start rtl_adsb subprocess
            self._process = subprocess.Popen(
                ["rtl_adsb"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # Line buffered
            )
            logger.info("rtl_adsb subprocess started")

            # Read output lines
            while self._running and self._process.poll() is None:
                line = self._process.stdout.readline()
                if line:
                    self._process_line(line.strip())
                    self._prune_stale()

        except Exception as e:
            logger.error(f"ADS-B capture error: {e}")
        finally:
            if self._process:
                try:
                    self._process.terminate()
                except Exception:
                    pass
                self._process = None

    def _process_line(self, line: str) -> None:
        """Process a line of rtl_adsb output.

        rtl_adsb outputs lines like: *8da8e1f6ea485864ed5c0898d970;
        """
        if not line.startswith("*") or not line.endswith(";"):
            return

        # Extract hex message (remove * prefix and ; suffix)
        msg_hex = line[1:-1]

        # Validate length (28 hex chars = 112 bits)
        if len(msg_hex) != 28:
            return

        self._decode_message(msg_hex)

    def _decode_message(self, msg_hex: str) -> None:
        """Decode ADS-B message and update aircraft state."""
        try:
            df = pms.df(msg_hex)

            # Only process DF17 (ADS-B) and DF11 (All-Call Reply)
            if df not in [17, 11]:
                return

            icao = pms.icao(msg_hex)
            if not icao:
                return

            with self._lock:
                if icao not in self._aircraft:
                    self._aircraft[icao] = Aircraft(icao_hex=icao)
                    self._total_aircraft_seen += 1
                    logger.debug(f"New aircraft: {icao}")

                ac = self._aircraft[icao]
                ac.last_seen_timestamp = time.time()

                if df == 17:
                    tc = pms.adsb.typecode(msg_hex)

                    # Aircraft identification (TC 1-4)
                    if 1 <= tc <= 4:
                        callsign = pms.adsb.callsign(msg_hex)
                        if callsign:
                            ac.callsign = callsign.strip()

                    # Airborne position (TC 9-18)
                    elif 9 <= tc <= 18:
                        alt = pms.adsb.altitude(msg_hex)
                        if alt:
                            ac.altitude_ft = alt

                        # Position requires odd/even frame pair - simplified here
                        # Full implementation would track frame pairs

                    # Airborne velocity (TC 19)
                    elif tc == 19:
                        velocity = pms.adsb.velocity(msg_hex)
                        if velocity:
                            speed, heading, vrate, _ = velocity
                            if speed:
                                ac.speed_kts = speed
                            if heading:
                                ac.heading_deg = heading

        except Exception as e:
            logger.debug(f"Decode error: {e}")

    def _prune_stale(self) -> None:
        """Remove aircraft not seen recently."""
        with self._lock:
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
