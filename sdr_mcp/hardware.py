"""
HackRF hardware abstraction with thread-safe access control.

This module provides a thread-safe wrapper around the HackRF One, managing
exclusive access to the device through a state machine and mutex. Unlike
the original RTL-SDR backend (which used the pyrtlsdr ctypes bindings),
this backend drives the device through the official ``hackrf_transfer`` /
``hackrf_info`` command-line tools that ship with ``brew install hackrf``.

Why a CLI wrapper instead of a Python binding:
    The most popular pure-Python HackRF binding (pyhackrf2) hardcodes
    ``ctypes.CDLL("libhackrf.so.0")`` -- a Linux shared-object name that
    will never resolve on macOS, where Homebrew installs
    ``libhackrf.dylib``. Rather than vendor a patch for an unmaintained,
    single-file ctypes wrapper that we cannot test against real hardware
    here, we shell out to the same officially-supported binaries the user
    already confirmed working (``hackrf_info``). This mirrors the existing
    pattern in adsb.py/ais.py, which already shell out to dump1090 and
    AIS-catcher rather than talking to hardware directly.

Hardware Constraint:
    The HackRF is a single physical device that can only perform one
    operation at a time. This module enforces mutual exclusion via a
    threading lock.

State Machine:
    IDLE -> tune_frequency() -> IDLE
    IDLE -> scan_band() -> SCANNING -> IDLE
    IDLE -> start_adsb_monitor() -> ADSB_ACTIVE
    IDLE -> start_ais_monitor() -> AIS_ACTIVE
    ADSB_ACTIVE -> stop_adsb_monitor() -> IDLE
    AIS_ACTIVE -> stop_ais_monitor() -> IDLE

    Operations that conflict with the current state raise HardwareBusyError.

    Note: ADS-B monitoring (adsb.py) drives a *separate* RTL-SDR dongle via
    dump1090, which is a physically independent device from the HackRF.
    The ADSB_ACTIVE lock on this object is therefore a software convention
    (kept for behavioral parity with the original single-dongle design),
    not a hardware necessity -- scan_band/tune_frequency on the HackRF
    could safely run concurrently with ADS-B on the RTL-SDR if that
    restriction is ever relaxed.

Gain Model:
    The HackRF has no autonomous AGC like RTL-SDR's "auto" gain. It has two
    independently configurable gain stages (LNA 0-40dB in 8dB steps, VGA
    0-62dB in 2dB steps) plus a 14dB RF amplifier toggle. "auto" in this
    module maps to fixed, reasonable defaults (LNA 16dB, VGA 20dB, amp
    off) -- it is not a real hardware auto-gain algorithm.

Graceful Degradation:
    If the hackrf_info/hackrf_transfer binaries are not on PATH, or no
    device is attached, the module remains importable and reports
    dongle_connected=False. This allows the MCP server to start and report
    status even without working hardware.

Example:
    >>> from sdr_mcp.hardware import get_device
    >>> device = get_device()
    >>> status = device.get_status()
    >>> print(status['dongle_connected'])
    True
"""

import logging
import re
import shutil
import subprocess
import tempfile
import threading
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# HackRF CLI tools installed by `brew install hackrf`
HACKRF_INFO_BIN = "hackrf_info"
HACKRF_TRANSFER_BIN = "hackrf_transfer"

HACKRF_AVAILABLE = (
    shutil.which(HACKRF_TRANSFER_BIN) is not None and shutil.which(HACKRF_INFO_BIN) is not None
)
_MISSING_BINARY_MSG = (
    f"{HACKRF_INFO_BIN}/{HACKRF_TRANSFER_BIN} not found on PATH. "
    "Install with: brew install hackrf"
)

_SERIAL_RE = re.compile(r"Serial number:\s*([0-9A-Fa-f]+)")

# Gain limits from hackrf_transfer(1)
LNA_GAIN_MAX_DB = 40
LNA_GAIN_STEP_DB = 8
VGA_GAIN_MAX_DB = 62
VGA_GAIN_STEP_DB = 2

# Default gain settings used for gain="auto" (HackRF has no true auto-gain)
DEFAULT_LNA_GAIN_DB = 16
DEFAULT_VGA_GAIN_DB = 20

# Subprocess timeouts (seconds)
INFO_TIMEOUT_S = 5.0
# Generous ceiling above requested dwell time to allow for process startup
TRANSFER_TIMEOUT_MARGIN_S = 5.0


class HardwareState(Enum):
    """Current state of the HackRF hardware."""
    IDLE = "idle"
    SCANNING = "scanning"
    ADSB_ACTIVE = "adsb"
    AIS_ACTIVE = "ais"


class HardwareError(Exception):
    """Raised when hardware operations fail."""


class HardwareBusyError(HardwareError):
    """Raised when hardware is busy with another operation."""


def _round_down_to_step(value: float, step: int, max_value: int) -> int:
    """Clamp value to [0, max_value] and round down to the nearest step."""
    value = max(0, min(value, max_value))
    return int(value) - (int(value) % step)


class HackRFDevice:
    """Thread-safe wrapper around the hackrf_transfer/hackrf_info CLI tools.

    The HackRF is a single physical device. All operations are mutually
    exclusive, enforced via threading mutex.
    """

    DEFAULT_SAMPLE_RATE = 2.048e6  # matches the original RTL-SDR default
    DEFAULT_GAIN = "auto"

    def __init__(self):
        self._lock = threading.RLock()
        self._state = HardwareState.IDLE
        self._connected = False
        self._serial: Optional[str] = None
        self._current_frequency_mhz: Optional[float] = None
        self._gain = self.DEFAULT_GAIN
        self._lna_gain_db = DEFAULT_LNA_GAIN_DB
        self._vga_gain_db = DEFAULT_VGA_GAIN_DB
        self._amp_enable = False
        self._sample_rate = self.DEFAULT_SAMPLE_RATE

    @property
    def state(self) -> HardwareState:
        """Current hardware state."""
        with self._lock:
            return self._state

    @property
    def connected(self) -> bool:
        """Whether the HackRF is connected and accessible."""
        with self._lock:
            return self._connected

    @property
    def current_frequency_mhz(self) -> Optional[float]:
        """Currently tuned frequency in MHz, or None if not tuned."""
        with self._lock:
            return self._current_frequency_mhz

    @property
    def gain(self):
        """Current gain setting ("auto" or a numeric dB value applied to VGA)."""
        with self._lock:
            return self._gain

    @property
    def sample_rate(self) -> float:
        """Current sample rate in Hz."""
        with self._lock:
            return self._sample_rate

    def set_gain(self, gain) -> None:
        """Set gain. "auto" uses fixed defaults; a number is applied to VGA gain.

        HackRF has no autonomous AGC, so "auto" is not a real hardware
        auto-gain mode -- it selects DEFAULT_LNA_GAIN_DB / DEFAULT_VGA_GAIN_DB.
        """
        with self._lock:
            self._gain = gain
            if gain == "auto":
                self._lna_gain_db = DEFAULT_LNA_GAIN_DB
                self._vga_gain_db = DEFAULT_VGA_GAIN_DB
            else:
                self._vga_gain_db = _round_down_to_step(
                    float(gain), VGA_GAIN_STEP_DB, VGA_GAIN_MAX_DB
                )

    def get_serial(self) -> Optional[str]:
        """Return the serial number of the first attached HackRF, or None."""
        if not HACKRF_AVAILABLE:
            return None
        if self._serial is not None:
            return self._serial
        self._refresh_info()
        return self._serial

    def _refresh_info(self) -> bool:
        """Run hackrf_info and update cached connection/serial state.

        Returns:
            True if a HackRF was found, False otherwise.
        """
        if not HACKRF_AVAILABLE:
            logger.warning(_MISSING_BINARY_MSG)
            return False

        try:
            result = subprocess.run(
                [HACKRF_INFO_BIN],
                capture_output=True,
                text=True,
                timeout=INFO_TIMEOUT_S,
                check=False,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.error(f"Failed to run {HACKRF_INFO_BIN}: {e}")
            return False

        output = result.stdout or ""
        if "Found HackRF" not in output:
            return False

        match = _SERIAL_RE.search(output)
        if match:
            self._serial = match.group(1)
        return True

    def connect(self) -> bool:
        """Verify the HackRF is present and accessible.

        Unlike pyrtlsdr, hackrf_transfer opens/closes the device per
        capture, so there is no persistent handle to hold open here --
        this just confirms the device responds to hackrf_info. Short-circuits
        if already connected, so read_samples()'s require_connected() call
        doesn't re-invoke hackrf_info on every single capture.

        Returns:
            True if the device was found, False otherwise.
        """
        with self._lock:
            if self._connected:
                return True
            self._connected = self._refresh_info()
            if self._connected:
                logger.info("HackRF connected successfully")
            return self._connected

    def disconnect(self) -> None:
        """Reset connection state.

        No persistent OS handle is held between captures, so this is a
        state reset rather than a device close.
        """
        with self._lock:
            self._connected = False
            self._state = HardwareState.IDLE
            self._current_frequency_mhz = None
            logger.info("HackRF disconnected")

    def check_connection(self) -> bool:
        """Check if the HackRF is connected without modifying cached state."""
        if self._connected:
            return True
        return self._refresh_info()

    def get_status(self) -> dict:
        """Get current hardware status.

        Returns:
            Dict with dongle_connected, mode, gain, sample_rate,
            active_frequency_mhz fields.
        """
        with self._lock:
            dongle_connected = self._connected or self.check_connection()

            return {
                "dongle_connected": dongle_connected,
                "mode": self._state.value,
                "gain": self._gain if self._gain != "auto" else "auto",
                "sample_rate": int(self._sample_rate),
                "active_frequency_mhz": self._current_frequency_mhz,
            }

    def set_state(self, new_state: HardwareState) -> None:
        """Set hardware state (internal use)."""
        with self._lock:
            self._state = new_state

    def require_idle(self) -> None:
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

    def require_connected(self) -> None:
        """Ensure hardware is connected, attempt connection if not."""
        if not self.connect():
            raise HardwareError(
                "HackRF not connected or not accessible. "
                "Check USB connection and ensure no other application "
                "is using the device."
            )

    def tune(self, frequency_mhz: float) -> None:
        """Record the frequency to use for the next read_samples() call.

        Note: unlike the original pyrtlsdr backend, this does not push a
        live retune to the hardware immediately -- hackrf_transfer only
        accepts frequency as a per-capture argument, so the actual retune
        happens lazily when read_samples() next runs. Callers that always
        pair tune() with an immediate read_samples() (as scanner.py does)
        see identical net behavior.
        """
        with self._lock:
            self.require_connected()
            self._current_frequency_mhz = frequency_mhz
            logger.debug(f"Tuned to {frequency_mhz} MHz")

    def read_samples(self, num_samples: int) -> np.ndarray:
        """Capture IQ samples from the HackRF at the current frequency.

        Args:
            num_samples: Number of complex samples to capture.

        Returns:
            numpy array of complex128 IQ samples, normalized to [-1, 1].

        Raises:
            HardwareError: if not connected/tuned, or the capture fails.
        """
        with self._lock:
            self.require_connected()
            if self._current_frequency_mhz is None:
                raise HardwareError("Must call tune() before read_samples().")
            frequency_hz = int(self._current_frequency_mhz * 1e6)
            sample_rate_hz = int(self._sample_rate)
            lna_gain = self._lna_gain_db
            vga_gain = self._vga_gain_db
            amp_enable = self._amp_enable

        return self._capture(
            frequency_hz, sample_rate_hz, num_samples, lna_gain, vga_gain, amp_enable
        )

    def _capture(
        self,
        frequency_hz: int,
        sample_rate_hz: int,
        num_samples: int,
        lna_gain: int,
        vga_gain: int,
        amp_enable: bool,
    ) -> np.ndarray:
        """Run hackrf_transfer to capture num_samples IQ samples to a temp file."""
        if num_samples <= 0:
            return np.array([], dtype=np.complex128)

        with tempfile.NamedTemporaryFile(
            prefix="hackrf_capture_", suffix=".iq", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        cmd = [
            HACKRF_TRANSFER_BIN,
            "-r", str(tmp_path),
            "-f", str(frequency_hz),
            "-s", str(sample_rate_hz),
            "-n", str(num_samples),
            "-l", str(lna_gain),
            "-g", str(vga_gain),
            "-a", "1" if amp_enable else "0",
        ]

        # Generous timeout: expected capture duration plus startup margin
        expected_s = num_samples / sample_rate_hz if sample_rate_hz else 0
        timeout_s = expected_s + TRANSFER_TIMEOUT_MARGIN_S

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            if result.returncode != 0:
                raise HardwareError(
                    f"hackrf_transfer failed (code {result.returncode}): "
                    f"{result.stderr.strip()}"
                )
            return self._read_iq_file(tmp_path, num_samples)
        except subprocess.TimeoutExpired as e:
            raise HardwareError(f"hackrf_transfer timed out: {e}") from e
        except (subprocess.SubprocessError, OSError) as e:
            raise HardwareError(f"Failed to run hackrf_transfer: {e}") from e
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def _read_iq_file(path: Path, num_samples: int) -> np.ndarray:
        """Read a HackRF raw capture file into a normalized complex array.

        HackRF samples are interleaved signed 8-bit I/Q pairs (native
        int8, no DC offset bias -- unlike RTL-SDR's unsigned 8-bit format).
        """
        raw = np.fromfile(path, dtype=np.int8)
        if raw.size == 0:
            return np.array([], dtype=np.complex128)

        # Drop a trailing unpaired byte, if any
        if raw.size % 2:
            raw = raw[:-1]

        i_samples = raw[0::2].astype(np.float64) / 128.0
        q_samples = raw[1::2].astype(np.float64) / 128.0
        iq = i_samples + 1j * q_samples
        return iq[:num_samples]

    def read_bytes(self, num_bytes: int) -> bytes:
        """Read raw bytes from the SDR.

        Kept for interface parity with the original backend; not used by
        scanner.py (which uses read_samples). Approximates by capturing
        num_bytes // 2 complex samples and re-packing to int8 pairs.
        """
        num_samples = max(1, num_bytes // 2)
        samples = self.read_samples(num_samples)
        i_bytes = np.clip(samples.real * 128, -128, 127).astype(np.int8)
        q_bytes = np.clip(samples.imag * 128, -128, 127).astype(np.int8)
        interleaved = np.empty(i_bytes.size + q_bytes.size, dtype=np.int8)
        interleaved[0::2] = i_bytes
        interleaved[1::2] = q_bytes
        return interleaved.tobytes()[:num_bytes]


# Global singleton instance
_device: Optional[HackRFDevice] = None
_device_lock = threading.Lock()


def get_device() -> HackRFDevice:
    """Get the global HackRFDevice singleton."""
    global _device
    with _device_lock:
        if _device is None:
            _device = HackRFDevice()
        return _device
