"""
AIS (Automatic Identification System) vessel tracking.

Mirrors the ADS-B implementation in adsb.py. AIS-catcher handles the
RTL-SDR device directly and serves vessel data via HTTP JSON API.

AIS channels:
    Channel A: 161.975 MHz
    Channel B: 162.025 MHz

AIS-catcher invocation:
    ais-catcher -d 0 -o 4 -H 0.0.0.0 8100

Vessel data is polled from http://localhost:8100/vessels.json.
"""

import json
import logging
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional
from urllib.request import urlopen
from urllib.error import URLError

from .models import Vessel
from .hardware import HardwareState, get_device

logger = logging.getLogger(__name__)

# Poll interval for vessel data (seconds) — AIS updates every few minutes
VESSEL_POLL_INTERVAL = 5.0

# Default vessel stale timeout (seconds) — spec default is 300
STALE_TIMEOUT_SECONDS = 300.0

# AIS-catcher HTTP API
AIS_CATCHER_HOST = "http://localhost:8100"
VESSELS_URL = f"{AIS_CATCHER_HOST}/vessels.json"

AIS_CATCHER_PATHS = [
    "AIS-catcher",
    "/opt/homebrew/bin/AIS-catcher",
    "/usr/local/bin/AIS-catcher",
    "/usr/bin/AIS-catcher",
]

# AIS navigation status codes (ITU-R M.1371)
_NAV_STATUS = {
    0: "Under way using engine",
    1: "At anchor",
    2: "Not under command",
    3: "Restricted manoeuvrability",
    4: "Constrained by her draught",
    5: "Moored",
    6: "Aground",
    7: "Engaged in fishing",
    8: "Under way sailing",
    14: "AIS-SART active",
    15: "Undefined",
}

# AIS ship type codes (simplified)
_SHIP_TYPE = {
    0: "Unknown",
    20: "WIG",
    29: "WIG",
    30: "Fishing",
    31: "Towing",
    32: "Towing",
    33: "Dredging",
    34: "Diving",
    35: "Military",
    36: "Sailing",
    37: "Pleasure craft",
    40: "High speed craft",
    49: "High speed craft",
    50: "Pilot vessel",
    51: "Search and rescue",
    52: "Tug",
    53: "Port tender",
    54: "Anti-pollution",
    55: "Law enforcement",
    58: "Medical",
    60: "Passenger",
    69: "Passenger",
    70: "Cargo",
    79: "Cargo",
    80: "Tanker",
    89: "Tanker",
    90: "Other",
    99: "Other",
}


def _resolve_ship_type(code: Optional[int]) -> Optional[str]:
    if code is None:
        return None
    # Check exact match first, then range (tens digit)
    if code in _SHIP_TYPE:
        return _SHIP_TYPE[code]
    decade = (code // 10) * 10
    return _SHIP_TYPE.get(decade)


def find_ais_catcher() -> Optional[str]:
    """Find AIS-catcher binary."""
    for path in AIS_CATCHER_PATHS:
        if shutil.which(path):
            return path
    return None


class AISMonitor:
    """Background AIS decoder using AIS-catcher subprocess."""

    def __init__(self):
        self._vessels: Dict[str, Vessel] = {}
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._start_time: Optional[float] = None
        self._total_vessels_seen = 0

    def start(self) -> None:
        """Start AIS monitoring. Launches AIS-catcher and begins polling."""
        if self._running:
            return

        ais_catcher = find_ais_catcher()
        if not ais_catcher:
            raise RuntimeError(
                "AIS-catcher not found. Install with: brew install ais-catcher"
            )

        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

        self._vessels.clear()
        self._total_vessels_seen = 0
        self._start_time = time.time()
        self._running = True

        # Release Python's device handle so AIS-catcher can claim it
        try:
            dev = get_device()
            dev.set_state(HardwareState.AIS_ACTIVE)
            dev.disconnect()
        except Exception as e:
            logger.warning(f"Could not release device: {e}")

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        logger.info("AIS monitor started")

    def stop(self) -> dict:
        """Stop AIS monitoring and return stats."""
        self._running = False

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception as e:
                logger.warning(f"Error terminating AIS-catcher: {e}")
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

        duration = time.time() - self._start_time if self._start_time else 0

        try:
            dev = get_device()
            dev.set_state(HardwareState.IDLE)
        except Exception as e:
            logger.warning(f"Error resetting hardware state: {e}")

        stats = {
            "total_vessels": self._total_vessels_seen,
            "duration_seconds": duration,
        }
        logger.info(f"AIS monitor stopped: {stats}")
        return stats

    def get_vessels(self, max_age_seconds: float = STALE_TIMEOUT_SECONDS) -> List[Vessel]:
        """Get vessels seen within max_age_seconds."""
        with self._lock:
            now = time.time()
            result = [
                v for v in self._vessels.values()
                if now - v.last_seen_timestamp <= max_age_seconds
            ]
            return sorted(result, key=lambda v: v.last_seen_timestamp, reverse=True)

    def _capture_loop(self) -> None:
        """Background loop: run AIS-catcher and poll its HTTP API."""
        ais_catcher = find_ais_catcher()
        try:
            cmd = [ais_catcher, "-d", "0", "-o", "4", "-H", "0.0.0.0", "8100"]
            logger.info(f"Starting AIS-catcher: {' '.join(cmd)}")

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Give AIS-catcher time to bind its HTTP server
            time.sleep(3)

            if self._process.poll() is not None:
                stderr = self._process.stderr.read()
                logger.error(f"AIS-catcher failed to start: {stderr}")
                return

            logger.info("AIS-catcher subprocess started")

            while self._running and self._process.poll() is None:
                try:
                    self._poll_vessels()
                except Exception as e:
                    logger.debug(f"Error polling vessels: {e}")
                time.sleep(VESSEL_POLL_INTERVAL)

            if self._process and self._process.poll() is not None:
                stderr = self._process.stderr.read() if self._process.stderr else ""
                if stderr:
                    logger.warning(f"AIS-catcher exited: {stderr}")

        except Exception as e:
            logger.error(f"AIS capture error: {e}")
        finally:
            if self._process:
                try:
                    self._process.terminate()
                except Exception:
                    pass
                self._process = None

    def _poll_vessels(self) -> None:
        """Fetch vessels.json from AIS-catcher HTTP API and update state."""
        try:
            with urlopen(VESSELS_URL, timeout=4) as resp:
                data = json.loads(resp.read().decode())
        except (URLError, json.JSONDecodeError) as e:
            logger.debug(f"vessels.json fetch error: {e}")
            return

        # AIS-catcher returns {"vessels": [...]} or a top-level list
        if isinstance(data, list):
            vessel_list = data
        else:
            vessel_list = data.get("vessels", [])

        now = time.time()
        with self._lock:
            for v_data in vessel_list:
                mmsi = str(v_data.get("mmsi", "")).strip()
                if not mmsi:
                    continue

                if mmsi not in self._vessels:
                    self._vessels[mmsi] = Vessel(mmsi=mmsi)
                    self._total_vessels_seen += 1
                    logger.debug(f"New vessel: {mmsi}")

                v = self._vessels[mmsi]
                v.last_seen_timestamp = now

                name = (v_data.get("shipname") or v_data.get("name") or "").strip()
                if name:
                    v.name = name

                lat = v_data.get("lat")
                lon = v_data.get("lon") or v_data.get("lng")
                if lat is not None and lon is not None:
                    v.lat = float(lat)
                    v.lon = float(lon)

                sog = v_data.get("speed") or v_data.get("sog")
                if sog is not None:
                    v.speed_kts = float(sog)

                hdg = v_data.get("heading") or v_data.get("hdg")
                if hdg is not None:
                    try:
                        v.heading_deg = int(hdg)
                    except (ValueError, TypeError):
                        pass

                cog = v_data.get("course") or v_data.get("cog")
                if cog is not None:
                    try:
                        v.course_deg = int(float(cog))
                    except (ValueError, TypeError):
                        pass

                status_code = v_data.get("status")
                if status_code is not None:
                    v.status = _NAV_STATUS.get(int(status_code), f"Status {status_code}")

                ship_type = v_data.get("shiptype") or v_data.get("ship_type")
                if ship_type is not None:
                    v.vessel_type = _resolve_ship_type(int(ship_type))

                dest = (v_data.get("destination") or "").strip()
                if dest:
                    v.destination = dest


# Singleton
_monitor: Optional[AISMonitor] = None
_monitor_lock = threading.Lock()


def get_ais_monitor() -> AISMonitor:
    """Get the global AISMonitor singleton."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = AISMonitor()
        return _monitor
