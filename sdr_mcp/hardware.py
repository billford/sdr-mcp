"""
RTL-SDR hardware abstraction with thread-safe access control.

This module provides a thread-safe wrapper around the pyrtlsdr library,
managing exclusive access to the RTL-SDR dongle through a state machine
and mutex.

Hardware Constraint:
    The RTL-SDR is a single physical device that can only perform one
    operation at a time. This module enforces mutual exclusion via a
    threading lock.

State Machine:
    IDLE → tune_frequency() → IDLE
    IDLE → scan_band() → SCANNING → IDLE
    IDLE → start_adsb_monitor() → ADSB_ACTIVE
    ADSB_ACTIVE → stop_adsb_monitor() → IDLE

    Operations that conflict with the current state raise HardwareBusyError.

Graceful Degradation:
    If pyrtlsdr fails to load (missing librtlsdr, symbol errors, etc.),
    the module remains importable and reports dongle_connected=False.
    This allows the MCP server to start and report status even without
    working hardware.

Example:
    >>> from sdr_mcp.hardware import get_device
    >>> device = get_device()
    >>> status = device.get_status()
    >>> print(status['dongle_connected'])
    True
"""

import logging
import threading
from enum import Enum
from typing import Optional

# Attempt to import pyrtlsdr, gracefully handling failures
# Common failure modes:
# - ImportError: pyrtlsdr not installed
# - OSError: librtlsdr.so/dylib not found
# - AttributeError: librtlsdr version mismatch (missing symbols like rtlsdr_set_dithering)
RtlSdr = None
RTLSDR_AVAILABLE = False
_IMPORT_ERROR = ""

try:
    from rtlsdr import RtlSdr
    RTLSDR_AVAILABLE = True
except (ImportError, OSError, AttributeError) as e:
    RTLSDR_AVAILABLE = False
    _IMPORT_ERROR = str(e)
except Exception as e:
    RTLSDR_AVAILABLE = False
    _IMPORT_ERROR = f"Unexpected error loading pyrtlsdr: {e}"

logger = logging.getLogger(__name__)


class HardwareState(Enum):
    """Current state of the RTL-SDR hardware."""
    IDLE = "idle"
    SCANNING = "scanning"
    ADSB_ACTIVE = "adsb"
    AIS_ACTIVE = "ais"


class HardwareError(Exception):
    """Raised when hardware operations fail."""
    pass


class HardwareBusyError(HardwareError):
    """Raised when hardware is busy with another operation."""
    pass


class RTLSDRDevice:
    """Thread-safe wrapper around pyrtlsdr with state management.

    The RTL-SDR is a single physical device. All operations are mutually
    exclusive, enforced via threading mutex.
    """

    DEFAULT_SAMPLE_RATE = 2.048e6  # 2.048 MSPS standard
    DEFAULT_GAIN = "auto"

    def __init__(self):
        self._lock = threading.RLock()
        self._state = HardwareState.IDLE
        self._sdr = None  # RtlSdr instance when connected
        self._connected = False
        self._current_frequency_mhz: Optional[float] = None
        self._gain = self.DEFAULT_GAIN
        self._sample_rate = self.DEFAULT_SAMPLE_RATE

    @property
    def state(self) -> HardwareState:
        """Current hardware state."""
        with self._lock:
            return self._state

    @property
    def connected(self) -> bool:
        """Whether dongle is connected and accessible."""
        with self._lock:
            return self._connected

    @property
    def current_frequency_mhz(self) -> Optional[float]:
        """Currently tuned frequency in MHz, or None if not tuned."""
        with self._lock:
            return self._current_frequency_mhz

    @property
    def gain(self):
        """Current gain setting."""
        with self._lock:
            return self._gain

    @property
    def sample_rate(self) -> float:
        """Current sample rate in Hz."""
        with self._lock:
            return self._sample_rate

    def connect(self) -> bool:
        """Attempt to connect to the RTL-SDR dongle.

        Returns:
            True if connection successful, False otherwise.
        """
        with self._lock:
            if self._connected and self._sdr is not None:
                return True

            if not RTLSDR_AVAILABLE:
                logger.warning(f"pyrtlsdr not available: {_IMPORT_ERROR}")
                self._connected = False
                return False

            try:
                self._sdr = RtlSdr()
                self._sdr.sample_rate = self._sample_rate
                if self._gain == "auto":
                    self._sdr.gain = "auto"
                else:
                    self._sdr.gain = self._gain
                self._connected = True
                logger.info("RTL-SDR connected successfully")
                return True
            except Exception as e:
                logger.error(f"Failed to connect to RTL-SDR: {e}")
                self._sdr = None
                self._connected = False
                return False

    def disconnect(self):
        """Disconnect from the RTL-SDR dongle."""
        with self._lock:
            if self._sdr is not None:
                try:
                    self._sdr.close()
                except Exception as e:
                    logger.warning(f"Error closing RTL-SDR: {e}")
                finally:
                    self._sdr = None
                    self._connected = False
                    self._state = HardwareState.IDLE
                    self._current_frequency_mhz = None
                    logger.info("RTL-SDR disconnected")

    def check_connection(self) -> bool:
        """Check if dongle is connected without modifying state.

        This method attempts a lightweight connection check suitable
        for status queries.
        """
        with self._lock:
            if self._connected and self._sdr is not None:
                return True

            if not RTLSDR_AVAILABLE:
                return False

            # Try to open and immediately close to check availability
            try:
                test_sdr = RtlSdr()
                test_sdr.close()
                return True
            except Exception:
                return False

    def get_status(self) -> dict:
        """Get current hardware status.

        Returns:
            Dict with dongle_connected, mode, gain, sample_rate,
            active_frequency_mhz fields.
        """
        with self._lock:
            # Check connection status
            if self._connected and self._sdr is not None:
                dongle_connected = True
            else:
                dongle_connected = self.check_connection()

            return {
                "dongle_connected": dongle_connected,
                "mode": self._state.value,
                "gain": self._gain if self._gain != "auto" else "auto",
                "sample_rate": int(self._sample_rate),
                "active_frequency_mhz": self._current_frequency_mhz,
            }

    def set_state(self, new_state: HardwareState):
        """Set hardware state (internal use)."""
        with self._lock:
            self._state = new_state

    def require_idle(self):
        """Raise error if hardware is not idle."""
        with self._lock:
            if self._state == HardwareState.ADSB_ACTIVE:
                raise HardwareBusyError(
                    "ADS-B monitor active. Call stop_adsb_monitor first."
                )
            if self._state == HardwareState.AIS_ACTIVE:
                raise HardwareBusyError(
                    "AIS monitor active. Call stop_ais_monitor first."
                )
            if self._state == HardwareState.SCANNING:
                raise HardwareBusyError("Scan in progress.")

    def require_connected(self):
        """Ensure hardware is connected, attempt connection if not."""
        if not self.connect():
            raise HardwareError(
                "RTL-SDR dongle not connected or not accessible. "
                "Check USB connection and ensure no other application "
                "is using the device."
            )

    def tune(self, frequency_mhz: float):
        """Tune to specified frequency.

        Args:
            frequency_mhz: Target frequency in MHz.
        """
        with self._lock:
            self.require_connected()
            frequency_hz = frequency_mhz * 1e6
            self._sdr.center_freq = frequency_hz
            self._current_frequency_mhz = frequency_mhz
            logger.debug(f"Tuned to {frequency_mhz} MHz")

    def read_samples(self, num_samples: int) -> 'np.ndarray':
        """Read IQ samples from the SDR.

        Args:
            num_samples: Number of complex samples to read.

        Returns:
            numpy array of complex IQ samples.
        """
        # Grab the sdr reference under lock, but release before blocking read
        with self._lock:
            self.require_connected()
            sdr = self._sdr

        # Blocking read happens outside the lock so other threads
        # (e.g. sdr_status) are not starved for ~0.5 seconds
        return sdr.read_samples(num_samples)

    def read_bytes(self, num_bytes: int) -> bytes:
        """Read raw bytes from the SDR.

        Args:
            num_bytes: Number of bytes to read.

        Returns:
            Raw byte data.
        """
        with self._lock:
            self.require_connected()
            return self._sdr.read_bytes(num_bytes)


# Global singleton instance
_device: Optional[RTLSDRDevice] = None
_device_lock = threading.Lock()


def get_device() -> RTLSDRDevice:
    """Get the global RTLSDRDevice singleton."""
    global _device
    with _device_lock:
        if _device is None:
            _device = RTLSDRDevice()
        return _device
