"""Logging setup for VAULT."""

import logging
import sys
from pathlib import Path
from vault.config import get_config, get_base_dir


def setup_logger(name="vault"):
    """Create and configure a logger instance."""
    cfg = get_config()["logging"]

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, cfg["level"].upper()))

    formatter = logging.Formatter(cfg["format"])

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler
    log_path = get_base_dir() / cfg["file"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
