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

Decoding Pipeline:
    1. Capture IQ samples at 2 MSPS on 1090 MHz
    2. AM demodulate (magnitude of complex samples)
    3. Detect preamble pattern (8µs specific pulse sequence)
    4. Extract 112-bit message following preamble
    5. Verify CRC-24 checksum
    6. Decode using pyModeS library
    7. Update aircraft state dictionary

Limitations:
    - Position decoding requires both odd and even CPR frames
      (this simplified implementation may not always have both)
    - Mode C transponders only provide altitude, not position
    - Range depends on antenna quality and local RF environment

Example:
    >>> from sdr_mcp.hardware import get_device
    >>> from sdr_mcp.adsb import get_adsb_monitor
    >>> device = get_device()
    >>> monitor = get_adsb_monitor()
    >>> monitor.start(device)
    >>> # Wait for aircraft...
    >>> aircraft = monitor.get_aircraft()
    >>> for ac in aircraft:
    ...     print(f"{ac.callsign}: {ac.altitude_ft} ft")
    >>> monitor.stop()
"""

import logging
import threading
import time
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np

try:
    import pyModeS as pms
    PYMODES_AVAILABLE = True
except ImportError:
    PYMODES_AVAILABLE = False

from .models import Aircraft
from .hardware import HardwareState

if TYPE_CHECKING:
    from .hardware import RTLSDRDevice

logger = logging.getLogger(__name__)

# ADS-B operates on 1090 MHz
ADSB_FREQUENCY_MHZ = 1090.0

# 2 MSPS provides 2 samples per bit (1µs bit period)
# This is the standard sample rate for RTL-SDR ADS-B reception
ADSB_SAMPLE_RATE = 2.0e6

# Remove aircraft from tracking after this many seconds without updates
STALE_TIMEOUT_SECONDS = 60.0


class ADSBMonitor:
    """Background ADS-B decoder using pyModeS."""

    def __init__(self):
        self._aircraft: Dict[str, Aircraft] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._device = None
        self._start_time: Optional[float] = None
        self._total_aircraft_seen = 0

    def start(self, device) -> None:
        """Start ADS-B monitoring."""
        if self._running:
            return

        if not PYMODES_AVAILABLE:
            raise RuntimeError("pyModeS not available")

        # Clean up any stale thread from a previous crash
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

        self._device = device
        self._aircraft.clear()
        self._total_aircraft_seen = 0
        self._start_time = time.time()
        self._running = True

        # Configure device for ADS-B
        device.set_state(HardwareState.ADSB_ACTIVE)
        device.tune(ADSB_FREQUENCY_MHZ)

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info("ADS-B monitor started")

    def stop(self) -> dict:
        """Stop ADS-B monitoring and return stats."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        duration = time.time() - self._start_time if self._start_time else 0

        if self._device:
            try:
                self._device.set_state(HardwareState.IDLE)
            except Exception as e:
                logger.warning(f"Error resetting hardware state: {e}")
            self._device = None

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
        """Background capture and decode loop."""
        # Buffer size for ~0.5 second of samples
        buffer_size = int(ADSB_SAMPLE_RATE * 0.5)

        while self._running:
            try:
                # Read raw IQ samples
                samples = self._device.read_samples(buffer_size)
                self._process_samples(samples)
                self._prune_stale()
            except Exception as e:
                logger.error(f"ADS-B capture error: {e}")
                # Do NOT retry on hardware error — the USB state is likely
                # corrupt after an overflow or pipe error. Retrying causes
                # a native crash (segfault in librtlsdr) that kills the
                # entire server process.
                # Instead, shut down cleanly and let the server survive.
                self._running = False
                try:
                    if self._device is not None:
                        self._device.disconnect()
                except Exception as cleanup_err:
                    logger.warning(f"Error during hardware cleanup: {cleanup_err}")
                finally:
                    self._device = None
                break

    def _process_samples(self, samples: np.ndarray) -> None:
        """Extract and decode ADS-B messages from IQ samples."""
        # Convert complex IQ to magnitude (AM demodulation)
        magnitude = np.abs(samples)

        # Simple threshold-based preamble detection
        # ADS-B preamble: 8µs, consisting of specific pulse pattern
        # At 2 MSPS, 8µs = 16 samples
        threshold = np.mean(magnitude) + 2 * np.std(magnitude)

        # Find potential message starts (simplified detection)
        # Real implementation would use proper preamble correlation
        messages = self._detect_messages(magnitude, threshold)

        for msg_bits in messages:
            self._decode_message(msg_bits)

    def _detect_messages(self, magnitude: np.ndarray, threshold: float) -> List[str]:
        """Detect ADS-B messages in magnitude data.

        This is a simplified detector. Production code would use
        proper preamble correlation and bit timing recovery.
        """
        messages = []
        # Samples per bit at 2 MSPS with 1µs bit period = 2 samples/bit
        samples_per_bit = 2
        msg_length_bits = 112  # Long ADS-B message
        msg_length_samples = msg_length_bits * samples_per_bit

        i = 0
        while i < len(magnitude) - msg_length_samples - 16:
            # Check for preamble pattern (simplified)
            if magnitude[i] > threshold:
                # Extract message samples
                msg_start = i + 16  # Skip preamble
                msg_samples = magnitude[msg_start:msg_start + msg_length_samples]

                # Decode bits using Manchester-like decoding
                bits = []
                for j in range(0, len(msg_samples), samples_per_bit):
                    if j + 1 < len(msg_samples):
                        # Compare first and second half of bit period
                        if msg_samples[j] > msg_samples[j + 1]:
                            bits.append('1')
                        else:
                            bits.append('0')

                if len(bits) >= 112:
                    msg_hex = self._bits_to_hex(bits[:112])
                    if msg_hex and self._check_crc(msg_hex):
                        messages.append(msg_hex)

                i += msg_length_samples
            else:
                i += 1

        return messages

    def _bits_to_hex(self, bits: List[str]) -> Optional[str]:
        """Convert bit string to hex."""
        try:
            bit_string = ''.join(bits)
            # Convert to hex, 4 bits at a time
            hex_chars = []
            for i in range(0, len(bit_string), 4):
                nibble = bit_string[i:i+4]
                hex_chars.append(format(int(nibble, 2), 'x'))
            return ''.join(hex_chars)
        except Exception:
            return None

    def _check_crc(self, msg_hex: str) -> bool:
        """Check ADS-B message CRC."""
        try:
            return pms.crc(msg_hex) == 0
        except Exception:
            return False

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
