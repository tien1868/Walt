"""Configuration loader for VAULT."""

import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

_config = None


def get_config():
    """Load and return configuration from config.yaml with env overrides."""
    global _config
    if _config is not None:
        return _config

    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path) as f:
        _config = yaml.safe_load(f)

    # Inject secrets from environment
    _config["gemini_api_key"] = os.getenv("GEMINI_API_KEY", "")
    _config["flask_secret_key"] = os.getenv("FLASK_SECRET_KEY", "dev-key-change-me")

    # Allow env overrides for common settings
    if os.getenv("VAULT_DB_PATH"):
        _config["storage"]["database_path"] = os.getenv("VAULT_DB_PATH")
    if os.getenv("VAULT_LOG_LEVEL"):
        _config["logging"]["level"] = os.getenv("VAULT_LOG_LEVEL")

    return _config


def get_base_dir():
    """Return the base directory of the vault project."""
    return Path(__file__).parent.parent


def get_db_path():
    """Return absolute path to the SQLite database."""
    cfg = get_config()
    return get_base_dir() / cfg["storage"]["database_path"]


def get_images_dir():
    """Return absolute path to the images directory."""
    cfg = get_config()
    return get_base_dir() / cfg["storage"]["images_dir"]


def get_reports_dir():
    """Return absolute path to the reports directory."""
    cfg = get_config()
    return get_base_dir() / cfg["storage"]["reports_dir"]
