import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from rich.console import Console

from cloudexit import codes

logger = logging.getLogger(__name__)
console = Console()


def load_config(file_path: str) -> dict[str, Any] | None:
    try:
        # logger.info(f"Attempting to load config file from {file_path}")
        with open(file_path, "r", encoding="utf-8-sig") as f:
            config = json.load(f)
        # logger.info("Config file loaded successfully.")
        return config
    except Exception as e:
        logger.error(f"Error loading config file: {e}", exc_info=True)
        console.print(
            f"Error loading config file: {e}", style="red", markup=False, emoji=False
        )
        return None


def resolve_name(args) -> str:
    if getattr(args, "name", None) and args.name.strip():
        return args.name.strip()
    return f"Exit Assessment {datetime.now().strftime('%Y%m%d_%H%M%S')}"


def build_config(
    cloud_provider: int,
    exit_strategy: int,
    assessment_type: int,
    provider_details: dict,
    args,
) -> dict:
    return {
        "name": resolve_name(args),
        "cloudServiceProvider": cloud_provider,
        "exitStrategy": exit_strategy,
        "assessmentType": assessment_type,
        "providerDetails": provider_details,
    }


def require_env(var: str, description: str) -> str:
    """Return the value of an env var, or exit with CONFIG if it is unset/empty."""
    value = os.environ.get(var, "").strip()
    if not value:
        console.print(
            f"--non-interactive requires {description}. "
            f"Set the {var} environment variable.",
            style="red",
            markup=False,
            emoji=False,
        )
        sys.exit(codes.CONFIG)
    return value


def require_env_int(var: str, description: str, valid: set) -> int:
    """Return an env var parsed as int, validated against an allowed set."""
    raw = require_env(var, description)
    try:
        value = int(raw)
    except ValueError:
        console.print(
            f"{var} must be an integer, got: {raw!r}",
            style="red",
            markup=False,
            emoji=False,
        )
        sys.exit(codes.CONFIG)
    if value not in valid:
        console.print(
            f"{var} must be one of {sorted(valid)}, got: {value}",
            style="red",
            markup=False,
            emoji=False,
        )
        sys.exit(codes.CONFIG)
    return value
