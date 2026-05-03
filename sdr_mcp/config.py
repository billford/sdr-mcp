"""Configuration management for SDR MCP server."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


@dataclass
class SDRConfig:
    """SDR configuration settings."""
    gain: str = "auto"
    sample_rate: int = 2048000
    stale_timeout_seconds: float = 60.0
    noise_floor_threshold_db: float = 6.0
    log_level: str = "INFO"
    log_file: str = ""


def load_config(config_path: Path = None) -> SDRConfig:
    """Load configuration from file or environment."""
    config = SDRConfig()

    # Try config file
    if config_path is None:
        config_path = Path.cwd() / "config.toml"

    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
            sdr = data.get("sdr", {})
            if "gain" in sdr:
                config.gain = sdr["gain"]
            if "sample_rate" in sdr:
                config.sample_rate = sdr["sample_rate"]
            if "stale_timeout_seconds" in sdr:
                config.stale_timeout_seconds = sdr["stale_timeout_seconds"]
            if "noise_floor_threshold_db" in sdr:
                config.noise_floor_threshold_db = sdr["noise_floor_threshold_db"]

            logging_cfg = data.get("logging", {})
            if "level" in logging_cfg:
                config.log_level = logging_cfg["level"]
            if "file" in logging_cfg:
                config.log_file = logging_cfg["file"]

    # Environment overrides
    if os.environ.get("SDR_GAIN"):
        config.gain = os.environ["SDR_GAIN"]
    if os.environ.get("SDR_SAMPLE_RATE"):
        config.sample_rate = int(os.environ["SDR_SAMPLE_RATE"])
    if os.environ.get("SDR_LOG_LEVEL"):
        config.log_level = os.environ["SDR_LOG_LEVEL"]

    return config
