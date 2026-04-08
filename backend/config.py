"""
Centralized runtime configuration for the RFID backend.

You can edit this file directly, or override values through environment
variables for production deployments.
"""

from __future__ import annotations

import os

LOADED_ENV_FILE = ""


def _load_env_file() -> None:
    global LOADED_ENV_FILE
    configured = os.getenv("CONFIG_ENV_FILE", "").strip()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, ".env")
    prod_path = os.path.join(base_dir, ".env.production")

    if configured:
        candidates = [configured]
    else:
        app_env = os.getenv("APP_ENV", "").strip().lower()
        if app_env == "production":
            candidates = [prod_path, env_path]
        else:
            candidates = [env_path, prod_path]

    for path in candidates:
        if not path or not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
        LOADED_ENV_FILE = path
        break


_load_env_file()


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = [part.strip() for part in raw.split(",")]
    return [value for value in values if value]


def _as_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _parse_int_list(values: list[str], fallback: list[int]) -> list[int]:
    parsed: list[int] = []
    for value in values:
        try:
            parsed.append(int(value))
        except ValueError:
            continue
    return parsed or fallback


def _parse_antenna_map(raw: str) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        antenna, direction = chunk.split(":", 1)
        antenna = antenna.strip()
        direction = direction.strip().upper()
        if not antenna or direction not in {"IN", "OUT"}:
            continue
        mapped[antenna] = direction
    return mapped


def _parse_antenna_float_map(raw: str) -> dict[int, float]:
    mapped: dict[int, float] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        antenna_raw, value_raw = chunk.split(":", 1)
        try:
            antenna = int(antenna_raw.strip())
            value = float(value_raw.strip())
        except ValueError:
            continue
        mapped[antenna] = value
    return mapped


APP_ENV = _env_str("APP_ENV", "development").lower()
IS_PRODUCTION = APP_ENV == "production"

# -----------------------------------------------------------------------------
# Application server settings
# -----------------------------------------------------------------------------
APP_HOST = _env_str("APP_HOST", "0.0.0.0")
APP_PORT = _env_int("APP_PORT", 8000)
APP_LOOPBACK_HOST = _env_str("APP_LOOPBACK_HOST", "127.0.0.1")
APP_LOG_LEVEL = _env_str("APP_LOG_LEVEL", "INFO").upper()
APP_FAIL_FAST = _env_bool("APP_FAIL_FAST", IS_PRODUCTION)
HIDE_INTERNAL_ERRORS = _env_bool("HIDE_INTERNAL_ERRORS", IS_PRODUCTION)

# Backward-compatible aliases used in code
HOST = APP_HOST

# -----------------------------------------------------------------------------
# CORS settings
# -----------------------------------------------------------------------------
CORS_SETTINGS = {
    "ALLOW_ORIGINS": _env_list("CORS_ALLOW_ORIGINS", ["*"]),
    "ALLOW_CREDENTIALS": _env_bool("CORS_ALLOW_CREDENTIALS", False),
}

# -----------------------------------------------------------------------------
# Database settings (MSSQL)
# -----------------------------------------------------------------------------
DB_SETTINGS = {
    "DRIVER": _env_str("DB_DRIVER", "{ODBC Driver 17 for SQL Server}"),
    "SERVER": _env_str("DB_SERVER", r"WAYINFOTECH\SQLEXPRESS"),
    "PORT": _env_str("DB_PORT", ""),
    "DATABASE": _env_str("DB_NAME", "jewellery_db"),
    "TRUSTED_CONNECTION": _env_str(
        "DB_TRUSTED_CONNECTION",
        _as_yes_no(_env_bool("DB_TRUSTED_CONNECTION_BOOL", True)),
    ),
    "USERNAME": _env_str("DB_USERNAME", ""),
    "PASSWORD": _env_str("DB_PASSWORD", ""),
    "ENCRYPT": _env_str("DB_ENCRYPT", "no"),
    "TRUST_SERVER_CERTIFICATE": _env_str("DB_TRUST_SERVER_CERTIFICATE", "yes"),
    "TIMEOUT_SECONDS": _env_int("DB_TIMEOUT_SECONDS", 5),
}

# Optional full override. Example: "sqlite:///./runtime_test.db"
SQLALCHEMY_DATABASE_URL = _env_str("SQLALCHEMY_DATABASE_URL", "")

# -----------------------------------------------------------------------------
# Reader ingest TCP servers (for handheld/fixed reader socket streams)
# -----------------------------------------------------------------------------
PORT_FIXED_READER = _env_int("PORT_FIXED_READER", 5001)

# -----------------------------------------------------------------------------
# Embedded active reader (LLRP) settings
# -----------------------------------------------------------------------------
default_antennas = [1, 2, 3, 4]
parsed_antennas = _parse_int_list(_env_list("RFID_ACTIVE_ANTENNAS", ["1", "2", "3", "4"]), default_antennas)
RFID_ACTIVE_SETTINGS = {
    "ENABLED": _env_bool("RFID_ACTIVE_ENABLED", True),
    "READER_HOST": _env_str("RFID_ACTIVE_READER_HOST", "169.254.4.161"),
    "READER_PORT": _env_int("RFID_ACTIVE_READER_PORT", 5084),
    "ANTENNAS": parsed_antennas or default_antennas,
    "PUSH_URL": _env_str("RFID_ACTIVE_PUSH_URL", f"http://{APP_LOOPBACK_HOST}:{APP_PORT}/rfid-read"),
    "RECONNECT_DELAY": _env_float("RFID_ACTIVE_RECONNECT_DELAY", 3.0),
    "REPORT_EVERY_N_TAGS": _env_int("RFID_ACTIVE_REPORT_EVERY_N_TAGS", 1),
    "REPORT_TIMEOUT_MS": _env_int("RFID_ACTIVE_REPORT_TIMEOUT_MS", 0),
    "SESSION": _env_int("RFID_ACTIVE_SESSION", 0),
    "ANTENNA_CYCLE_SECONDS": _env_float("RFID_ACTIVE_ANTENNA_CYCLE_SECONDS", 0.0),
    "STALE_GRACE_SECONDS": _env_float("RFID_ACTIVE_STALE_GRACE_SECONDS", 1.0),
    "API_TIMEOUT_SECONDS": _env_float("RFID_ACTIVE_API_TIMEOUT_SECONDS", 1.0),
    "DROP_STALE_REPORTS": _env_bool("RFID_ACTIVE_DROP_STALE_REPORTS", False),
    "TX_POWER_DBM": _parse_antenna_float_map(_env_str("RFID_ACTIVE_TX_POWER_DBM", "")),
    "DEBUG": _env_bool("RFID_ACTIVE_DEBUG", False),
    "PUSH_TOKEN": _env_str("RFID_ACTIVE_PUSH_TOKEN", ""),
    "SINGLE_INSTANCE_LOCK": _env_bool("RFID_ACTIVE_SINGLE_INSTANCE_LOCK", True),
    "LOCK_FILE_PATH": _env_str("RFID_ACTIVE_LOCK_FILE_PATH", "reader.lock"),
}

# -----------------------------------------------------------------------------
# Scan logic settings
# -----------------------------------------------------------------------------
SCAN_COOLDOWN_SECONDS = _env_int("SCAN_COOLDOWN_SECONDS", 60)
IN_REENTRY_COOLDOWN_SECONDS = _env_int("IN_REENTRY_COOLDOWN_SECONDS", SCAN_COOLDOWN_SECONDS)
OUT_BLOCK_AFTER_IN_SECONDS = _env_int("OUT_BLOCK_AFTER_IN_SECONDS", 60)
DIRECTION_SWITCH_GUARD_SECONDS = _env_int(
    "DIRECTION_SWITCH_GUARD_SECONDS",
    OUT_BLOCK_AFTER_IN_SECONDS,
)
ANTENNA_COALESCE_WINDOW_MS = _env_int("ANTENNA_COALESCE_WINDOW_MS", 0)
ALLOW_PENDING_IN_RELOG = _env_bool("ALLOW_PENDING_IN_RELOG", False)
PENDING_IN_RELOG_SECONDS = _env_int("PENDING_IN_RELOG_SECONDS", 0)

# Antenna-to-direction mapping for live IN/OUT feed
_antenna_map_from_env = _parse_antenna_map(_env_str("ANTENNA_MAP", "1:IN,2:OUT"))
ANTENNA_MAP = _antenna_map_from_env or {"1": "IN", "2": "OUT"}

# -----------------------------------------------------------------------------
# Security/dashboard behavior
# -----------------------------------------------------------------------------
COOKIE_NAME = _env_str("COOKIE_NAME", "session_id")
MAX_LOG_LIMIT = _env_int("MAX_LOG_LIMIT", 10000)

# -----------------------------------------------------------------------------
# Authentication/security settings
# -----------------------------------------------------------------------------
SECURITY_SETTINGS = {
    "SECRET_KEY": _env_str("SECRET_KEY", "change-me-before-production"),
    "COOKIE_NAME": COOKIE_NAME,
    "COOKIE_SECURE": _env_bool("COOKIE_SECURE", IS_PRODUCTION),
    "COOKIE_SAMESITE": _env_str("COOKIE_SAMESITE", "lax"),
    "COOKIE_PATH": _env_str("COOKIE_PATH", "/"),
    "SESSION_MAX_AGE_SECONDS": _env_int("SESSION_MAX_AGE_SECONDS", 60 * 60 * 8),
    "PASSWORD_HASH_ITERATIONS": _env_int("PASSWORD_HASH_ITERATIONS", 180000),
    "BOOTSTRAP_ADMIN": {
        "USERNAME": _env_str("BOOTSTRAP_ADMIN_USERNAME", ""),
        "PASSWORD": _env_str("BOOTSTRAP_ADMIN_PASSWORD", ""),
        "ROLE": _env_str("BOOTSTRAP_ADMIN_ROLE", "admin"),
    },
}

CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
