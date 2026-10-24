from __future__ import annotations

import logging
import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

logger = logging.getLogger("main.utils.settings")

try:
    import config as legacy_config
except ModuleNotFoundError:
    legacy_config = None

DEFAULT_TIMEOUT = 10.0
DEFAULT_UPLOAD_BUDGET = 300.0
LOCAL_HTTP_HOSTS = ("localhost", "127.0.0.1")
KEY_PREFIXES = ("esc_sk_", "exc_eu_sk_", "exc_us_sk_")
UPLOAD_MODES = ("auto", "required", "off")

ENV_VARS = {
    "host": "CLOUDEXIT_HOST",
    "api_key": "CLOUDEXIT_API_KEY",
    "api_key_file": "CLOUDEXIT_API_KEY_FILE",
    "profile": "CLOUDEXIT_PROFILE",
    "assessment_id": "CLOUDEXIT_ASSESSMENT_ID",
    "upload": "CLOUDEXIT_UPLOAD",
    "ca_bundle": "CLOUDEXIT_CA_BUNDLE",
    "timeout": "CLOUDEXIT_TIMEOUT",
    "upload_budget": "CLOUDEXIT_UPLOAD_BUDGET",
    "spool_dir": "CLOUDEXIT_SPOOL_DIR",
    "catalogue_url": "CLOUDEXIT_CATALOGUE_URL",
}

FLAGS = {
    "host": "--host",
    "api_key_file": "--api-key-file",
    "profile": "--config-profile",
    "assessment_id": "--assessment-id",
    "ca_bundle": "--ca-bundle",
    "timeout": "--timeout",
    "upload_budget": "--upload-budget",
    "upload": "--upload",
}

FILE_KEYS = ("host", "api_key", "api_key_file", "assessment_id", "ca_bundle", "upload")
PATH_KEYS = ("api_key_file", "ca_bundle", "spool_dir")

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Setting:
    value: Any = field(default=None, repr=False)
    source: str = "unset"


@dataclass(frozen=True)
class Settings:
    host: Setting = field(default_factory=Setting)
    api_key: Setting = field(default_factory=Setting)
    api_key_file: Setting = field(default_factory=Setting)
    profile: Setting = field(default_factory=Setting)
    assessment_id: Setting = field(default_factory=Setting)
    upload: Setting = field(default_factory=lambda: Setting("auto", "default"))
    ca_bundle: Setting = field(default_factory=Setting)
    timeout: Setting = field(
        default_factory=lambda: Setting(DEFAULT_TIMEOUT, "default")
    )
    upload_budget: Setting = field(
        default_factory=lambda: Setting(DEFAULT_UPLOAD_BUDGET, "default")
    )
    spool_dir: Setting = field(default_factory=Setting)
    catalogue_url: Setting = field(default_factory=Setting)
    config_path: Path | None = None
    config_found: bool = False


def config_file_path(
    environ: Mapping[str, str] | None = None, *, platform: str = sys.platform
) -> Path:
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        xdg = environ.get("XDG_CONFIG_HOME", "")
        base = xdg if xdg and os.path.isabs(xdg) else str(Path.home() / ".config")
    return Path(base) / "cloudexit" / "config.toml"


def normalize_host(raw: str) -> str:
    host = raw.strip()
    if not host:
        raise SettingsError("host is empty")
    if "://" not in host:
        host = f"https://{host}"

    parts = urlsplit(host)
    if not parts.hostname:
        raise SettingsError(f"host {raw!r} has no host name")
    if parts.scheme == "http":
        if parts.hostname not in LOCAL_HTTP_HOSTS:
            raise SettingsError(
                f"plain http:// is not allowed for {parts.hostname}; use https:// "
                "(http is only accepted for localhost and 127.0.0.1)"
            )
    elif parts.scheme != "https":
        raise SettingsError(f"unsupported scheme {parts.scheme}:// in host {raw!r}")
    return host.rstrip("/")


def mask_key(key: str | None) -> str:
    if not key:
        return "-"
    if len(key) < 12:
        return "****"
    for prefix in KEY_PREFIXES:
        if key.startswith(prefix):
            return f"{prefix}...{key[-4:]}"
    return f"{key[:4]}...{key[-4:]}"


def flags_from_args(args: Any) -> dict[str, Any]:
    return {
        name: getattr(args, attr, None)
        for name, attr in (
            ("host", "host"),
            ("api_key_file", "api_key_file"),
            ("profile", "config_profile"),
            ("assessment_id", "assessment_id"),
            ("ca_bundle", "ca_bundle"),
            ("timeout", "timeout"),
            ("upload_budget", "upload_budget"),
            ("upload", "upload"),
        )
        if getattr(args, attr, None) is not None
    }


def _expand(raw: str, base: Path | None) -> str:
    path = Path(raw).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return str(path)


def _warn_if_readable(path: Path, what: str) -> None:
    if os.name != "posix":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        logger.warning(
            "%s %s is readable by group or others; restrict it with: chmod 600 %s",
            what,
            path,
            path,
        )


def _flag_layer(flags: Mapping[str, Any]) -> dict[str, Setting]:
    layer = {}
    for name, flag in FLAGS.items():
        value = flags.get(name)
        if isinstance(value, str):
            value = value.strip()
        if value is None or value == "":
            continue
        if name in PATH_KEYS:
            value = _expand(value, None)
        layer[name] = Setting(value, f"flag {flag}")
    return layer


def _env_layer(environ: Mapping[str, str]) -> dict[str, Setting]:
    layer = {}
    for name, var in ENV_VARS.items():
        value = environ.get(var, "").strip()
        if not value:
            continue
        if name in PATH_KEYS:
            value = _expand(value, None)
        layer[name] = Setting(value, f"env {var}")
    return layer


def _legacy_env_layer(environ: Mapping[str, str]) -> dict[str, Setting]:
    host = environ.get("HOST", "").strip()
    key = environ.get("KEY", "").strip()
    if host and key:
        return {
            "host": Setting(host, "legacy env HOST"),
            "api_key": Setting(key, "legacy env KEY"),
        }
    if host or key:
        logger.debug("Ignoring legacy HOST/KEY: they are only used when both are set.")
    return {}


def _legacy_config_layer() -> dict[str, Setting]:
    layer = {}
    for name, attr in (("host", "HOST"), ("api_key", "KEY")):
        value = getattr(legacy_config, attr, "")
        if isinstance(value, str) and value.strip():
            layer[name] = Setting(value.strip(), "legacy config.py")
    return layer


def _read_config_file(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    _warn_if_readable(path, "config file")
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError(f"cannot read config file {path}: {exc}") from exc


def _table_layer(table: Any, label: str, path: Path) -> dict[str, Setting]:
    if not isinstance(table, dict):
        raise SettingsError(f"{label} in {path} must be a table")

    layer = {}
    for key, value in table.items():
        if key not in FILE_KEYS:
            logger.warning("Unknown key %r in %s of %s is ignored.", key, label, path)
            continue
        if key == "upload":
            if not isinstance(value, (bool, str)):
                raise SettingsError(
                    f"{key} in {label} of {path} must be auto, required or off"
                )
        elif not isinstance(value, str):
            raise SettingsError(f"{key} in {label} of {path} must be a string")
        elif not value.strip():
            continue
        else:
            value = value.strip()
            if key in PATH_KEYS:
                value = _expand(value, path.parent)
        layer[key] = Setting(value, f"config file {label}")
    return layer


def _file_layers(
    data: dict[str, Any], path: Path, profile: str | None
) -> list[dict[str, Setting]]:
    for key in data:
        if key not in ("default", "profiles"):
            logger.warning("Unknown table %r in %s is ignored.", key, path)

    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise SettingsError(f"[profiles] in {path} must be a table of tables")

    layers = []
    if profile:
        if profile not in profiles:
            raise SettingsError(f"profile {profile!r} not found in {path}")
        layers.append(_table_layer(profiles[profile], f"[profiles.{profile}]", path))
    layers.append(_table_layer(data.get("default", {}), "[default]", path))
    return layers


def _read_key_file(setting: Setting) -> str:
    path = Path(setting.value)
    if not path.is_file():
        raise SettingsError(f"API key file {path} ({setting.source}) not found")
    _warn_if_readable(path, "API key file")
    try:
        key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"cannot read API key file {path}: {exc}") from exc
    if not key:
        raise SettingsError(f"API key file {path} is empty")
    return key


def _resolve_key(layers: list[dict[str, Setting]]) -> tuple[Setting, Setting]:
    for layer in layers:
        key_file = layer.get("api_key_file")
        if key_file is not None:
            return Setting(_read_key_file(key_file), key_file.source), key_file
        if "api_key" in layer:
            return layer["api_key"], Setting()
    return Setting(), Setting()


def _first(layers: list[dict[str, Setting]], name: str) -> Setting:
    for layer in layers:
        if name in layer:
            return layer[name]
    return Setting()


def _parse_timeout(setting: Setting) -> Setting:
    try:
        seconds = float(setting.value)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        raise SettingsError(
            f"timeout ({setting.source}) must be a positive number of seconds"
        )
    return Setting(seconds, setting.source)


def _parse_budget(setting: Setting) -> Setting:
    try:
        seconds = float(setting.value)
    except (TypeError, ValueError):
        seconds = -1.0
    if seconds < 0:
        raise SettingsError(
            f"upload budget ({setting.source}) must be a number of seconds, "
            "0 or more"
        )
    return Setting(seconds, setting.source)


def _parse_upload(setting: Setting) -> Setting:
    # true and false from earlier versions still mean auto and off
    value = setting.value
    if isinstance(value, bool):
        value = "auto" if value else "off"
    elif isinstance(value, str):
        word = value.strip().lower()
        if word in UPLOAD_MODES:
            value = word
        elif word in TRUE_WORDS:
            value = "auto"
        elif word in FALSE_WORDS:
            value = "off"
    if value not in UPLOAD_MODES:
        raise SettingsError(f"upload ({setting.source}) must be auto, required or off")
    return Setting(value, setting.source)


def _check_host(setting: Setting) -> Setting:
    try:
        return Setting(normalize_host(setting.value), setting.source)
    except SettingsError as exc:
        raise SettingsError(f"host ({setting.source}): {exc}") from exc


def _check_ca_bundle(setting: Setting) -> Setting:
    if not Path(setting.value).is_file():
        raise SettingsError(
            f"CA bundle {setting.value} ({setting.source}) is not a file"
        )
    return setting


def _warn_deprecated(settings: Settings) -> None:
    sources = {
        setting.source
        for setting in (settings.host, settings.api_key)
        if setting.source.startswith("legacy")
    }
    if "legacy env HOST" in sources or "legacy env KEY" in sources:
        logger.warning(
            "The HOST and KEY environment variables are deprecated; use "
            "CLOUDEXIT_HOST and CLOUDEXIT_API_KEY_FILE (or CLOUDEXIT_API_KEY)."
        )
    if "legacy config.py" in sources:
        logger.warning(
            "config.py is deprecated; move HOST and KEY to the config file "
            "(see `cloudexit config path`) or to CLOUDEXIT_* environment variables."
        )


def load_settings(
    flags: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str = sys.platform,
) -> Settings:
    environ = os.environ if environ is None else environ
    top = [_flag_layer(flags or {}), _env_layer(environ)]
    profile = _first(top, "profile")

    path = config_file_path(environ, platform=platform)
    data = _read_config_file(path)
    if data is None:
        if profile.value:
            raise SettingsError(
                f"profile {profile.value!r} ({profile.source}) requested but "
                f"there is no config file at {path}"
            )
        file_layers = []
    else:
        file_layers = _file_layers(data, path, profile.value)

    layers = [
        *top,
        *file_layers,
        _legacy_env_layer(environ),
        _legacy_config_layer(),
    ]

    api_key, api_key_file = _resolve_key(layers)
    host = _first(layers, "host")
    ca_bundle = _first(layers, "ca_bundle")
    timeout = _first(layers, "timeout")
    budget = _first(layers, "upload_budget")
    upload = _first(layers, "upload")

    settings = Settings(
        host=_check_host(host) if host.value else host,
        api_key=api_key,
        api_key_file=api_key_file,
        profile=profile,
        assessment_id=_first(layers, "assessment_id"),
        upload=(
            _parse_upload(upload) if upload.value is not None else Settings().upload
        ),
        ca_bundle=_check_ca_bundle(ca_bundle) if ca_bundle.value else ca_bundle,
        timeout=(
            _parse_timeout(timeout) if timeout.value is not None else Settings().timeout
        ),
        upload_budget=(
            _parse_budget(budget)
            if budget.value is not None
            else Settings().upload_budget
        ),
        spool_dir=_first(layers, "spool_dir"),
        catalogue_url=_first(layers, "catalogue_url"),
        config_path=path,
        config_found=data is not None,
    )
    _warn_deprecated(settings)
    return settings


def describe(settings: Settings) -> list[str]:
    found = "found" if settings.config_found else "not found"
    rows = []
    for name in (
        "host",
        "api_key",
        "api_key_file",
        "profile",
        "assessment_id",
        "upload",
        "ca_bundle",
        "timeout",
        "upload_budget",
        "spool_dir",
        "catalogue_url",
    ):
        setting = getattr(settings, name)
        value = setting.value
        if name == "api_key":
            shown = mask_key(value)
        elif value is None:
            shown = "-"
        elif isinstance(value, bool):
            shown = "true" if value else "false"
        elif isinstance(value, float):
            shown = f"{value:g}"
        else:
            shown = str(value)
        rows.append((name, shown, setting.source))

    name_width = max(len(row[0]) for row in rows)
    value_width = max(len(row[1]) for row in rows)
    lines = [f"config file: {settings.config_path} ({found})"]
    lines.extend(
        f"{name:<{name_width}}  {shown:<{value_width}}  {source}"
        for name, shown, source in rows
    )
    return lines
