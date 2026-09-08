#!/usr/bin/python3
from __future__ import annotations

### Simple-Stupid user-space program protecting a linux host from thrashing.
### See the README for details.
### Project home: https://github.com/tobixen/thrash-protect

### This was a rapid prototype implementation.  I was considering to implement in C.
### While I have been considering this, Moore's Law has made it pretty moot.

try:
    from importlib.metadata import version as _metadata_version

    __version__ = _metadata_version("thrash-protect")
except Exception:
    __version__ = "DEVELOPMENT"  # Replaced by Makefile during standalone install

__author__ = "Tobias Brox"
__copyright__ = "Copyright 2013-2026, Tobias Brox"
__license__ = "GPL"
__maintainer__ = "Tobias Brox"
__email__ = "tobias@redpill-linpro.com"
__product__ = "thrash-protect"

import argparse
import configparser
import glob
import json
import logging
import os
import random  ## for the test_mode
import signal
import time
from collections import deque, namedtuple
from datetime import datetime
from os import getenv, getpid, getppid, kill, unlink
from subprocess import check_output
from typing import Any, Callable

# Optional imports with graceful fallback
try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import tomllib  # Python 3.11+

    HAS_TOML = True
except ImportError:
    try:
        import tomli as tomllib  # Fallback for older Python

        HAS_TOML = True
    except ImportError:
        HAS_TOML = False


#########################
## Configuration section
#########################

# Default config file search paths (in order of preference)
CONFIG_SEARCH_PATHS = [
    "/etc/thrash-protect.yaml",
    "/etc/thrash-protect.yml",
    "/etc/thrash-protect.toml",
    "/etc/thrash-protect.json",
    "/etc/thrash-protect.conf",
]

# Static whitelist - processes that should always be protected
STATIC_WHITELIST = [
    # SSH/terminals
    "sshd",
    "ssh",
    "xterm",
    "rxvt",
    "urxvt",
    "alacritty",
    "kitty",
    "foot",
    # Multiplexers
    "screen",
    "SCREEN",
    "tmux",
    # X11
    "xinit",
    "X",
    "Xorg",
    "Xorg.bin",
    # Wayland compositors
    "sway",
    "wayfire",
    "hyprland",
    # Window managers
    "spectrwm",
    "i3",
    "dwm",
    "openbox",
    "awesome",
    "bspwm",
    # Desktop environments
    "gnome-shell",
    "kwin_x11",
    "kwin_wayland",
    "plasmashell",
    "xfce4-session",
    "waybar",
    "wireplumber",
    "pipewire",
    "swaync",
    "swayidle",
    "dbus-broker",
    # System
    "systemd-journal",
    "dbus-daemon",
    "kthreadd",
    "login",
    "supervisord",
]


def get_shells_from_etc() -> list[str]:
    """Read shell basenames from /etc/shells."""
    try:
        shells = set()
        with open("/etc/shells") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                basename = line.rsplit("/", 1)[-1]
                if basename:
                    shells.add(basename)
        return list(shells) if shells else ["bash", "sh", "zsh", "fish"]
    except (FileNotFoundError, PermissionError, OSError):
        return ["bash", "sh", "zsh", "fish"]


def get_default_whitelist() -> list[str]:
    """Static whitelist + all shells from /etc/shells."""
    shells = get_shells_from_etc()
    return list(set(STATIC_WHITELIST + shells))


def get_default_jobctrllist() -> list[str]:
    """Shells from /etc/shells plus sudo."""
    shells = get_shells_from_etc()
    if "sudo" not in shells:
        shells.append("sudo")
    return shells


def _parse_bool(value: bool | int | str) -> bool:
    """Parse boolean from string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return str(value).lower() in ("true", "yes", "1", "on")


def _parse_list(value: list[str] | str | None) -> list[str]:
    """Parse space-separated list."""
    if isinstance(value, list):
        return value
    if not value or not str(value).strip():
        return []
    return str(value).split()


# Unified configuration schema
# Each entry: config_key -> (type_converter, env_var_name, file_key_aliases)
CONFIG_SCHEMA = {
    "debug_logging": (_parse_bool, "THRASH_PROTECT_DEBUG_LOGGING", ["debug-logging"]),
    "debug_checkstate": (_parse_bool, "THRASH_PROTECT_DEBUG_CHECKSTATE", ["debug-checkstate"]),
    "interval": (float, "THRASH_PROTECT_INTERVAL", []),
    "swap_page_threshold": (int, "THRASH_PROTECT_SWAP_PAGE_THRESHOLD", ["swap-page-threshold"]),
    "pgmajfault_scan_threshold": (int, "THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD", ["pgmajfault-scan-threshold"]),
    "use_psi": (_parse_bool, "THRASH_PROTECT_USE_PSI", ["use-psi"]),
    "psi_threshold": (float, "THRASH_PROTECT_PSI_THRESHOLD", ["psi-threshold"]),
    "psi_swap_floor": (int, "THRASH_PROTECT_PSI_SWAP_FLOOR", ["psi-swap-floor"]),
    "io_pressure_veto": (_parse_bool, "THRASH_PROTECT_IO_PRESSURE_VETO", ["io-pressure-veto"]),
    "io_pressure_threshold": (float, "THRASH_PROTECT_IO_PRESSURE_THRESHOLD", ["io-pressure-threshold"]),
    "cmd_whitelist": (_parse_list, "THRASH_PROTECT_CMD_WHITELIST", ["cmd-whitelist"]),
    "cmd_blacklist": (_parse_list, "THRASH_PROTECT_CMD_BLACKLIST", ["cmd-blacklist"]),
    "cmd_jobctrllist": (_parse_list, "THRASH_PROTECT_CMD_JOBCTRLLIST", ["cmd-jobctrllist"]),
    "blacklist_score_multiplier": (int, "THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER", ["blacklist-score-multiplier"]),
    "whitelist_score_divider": (
        int,
        "THRASH_PROTECT_WHITELIST_SCORE_MULTIPLIER",
        ["whitelist-score-divider", "whitelist-score-multiplier"],
    ),
    "unfreeze_pop_ratio": (int, "THRASH_PROTECT_UNFREEZE_POP_RATIO", ["unfreeze-pop-ratio"]),
    "test_mode": (int, "THRASH_PROTECT_TEST_MODE", ["test-mode"]),
    "log_user_data_on_freeze": (_parse_bool, "THRASH_PROTECT_LOG_USER_DATA_ON_FREEZE", ["log-user-data-on-freeze"]),
    "log_user_data_on_unfreeze": (
        _parse_bool,
        "THRASH_PROTECT_LOG_USER_DATA_ON_UNFREEZE",
        ["log-user-data-on-unfreeze"],
    ),
    "date_human_readable": (_parse_bool, "THRASH_PROTECT_DATE_HUMAN_READABLE", ["date-human-readable"]),
    "diagnostic_logging": (_parse_bool, "THRASH_PROTECT_DIAGNOSTIC_LOGGING", ["diagnostic-logging"]),
    "storage_type": (str, "THRASH_PROTECT_STORAGE_TYPE", ["storage-type"]),
    "oom_protection": (_parse_bool, "THRASH_PROTECT_OOM_PROTECTION", ["oom-protection"]),
    "oom_observation_window": (int, "THRASH_PROTECT_OOM_OBSERVATION_WINDOW", ["oom-observation-window"]),
    "oom_horizon": (int, "THRASH_PROTECT_OOM_HORIZON", ["oom-horizon"]),
    "oom_swap_weight": (float, "THRASH_PROTECT_OOM_SWAP_WEIGHT", ["oom-swap-weight"]),
    "oom_low_pct": (float, "THRASH_PROTECT_OOM_LOW_PCT", ["oom-low-pct"]),
    "pswp_weight": (float, "THRASH_PROTECT_PSWP_WEIGHT", ["pswp-weight"]),
    "blacklist_expiry_time": (float, "THRASH_PROTECT_BLACKLIST_EXPIRY_TIME", ["blacklist-expiry-time"]),
    "blacklist_max_skip_count": (int, "THRASH_PROTECT_BLACKLIST_MAX_SKIP_COUNT", ["blacklist-max-skip-count"]),
    "blacklist_escalation_cap": (int, "THRASH_PROTECT_BLACKLIST_ESCALATION_CAP", ["blacklist-escalation-cap"]),
    "oom_hold_ticks": (int, "THRASH_PROTECT_OOM_HOLD_TICKS", ["oom-hold-ticks"]),
}


def load_from_file(path: str | None = None) -> dict[str, Any]:
    """Load configuration from file (auto-detect format by extension)."""
    if path:
        paths = [path]
    else:
        paths = CONFIG_SEARCH_PATHS

    for filepath in paths:
        if not os.path.exists(filepath):
            continue
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext in (".yaml", ".yml"):
                return _load_yaml(filepath)
            elif ext == ".toml":
                return _load_toml(filepath)
            elif ext == ".json":
                return _load_json(filepath)
            else:  # .conf, .ini, or unknown
                return _load_ini(filepath)
        except ImportError as e:
            logging.warning(f"Config format not supported for {filepath}: {e}")
            continue
        except Exception as e:
            logging.warning(f"Failed to load config from {filepath}: {e}")
            continue
    return {}


def _load_yaml(path: str) -> dict[str, Any]:
    """Load YAML config file."""
    if not HAS_YAML:
        raise ImportError("PyYAML not installed - install with: pip install PyYAML")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("thrash-protect", data)


def _load_toml(path: str) -> dict[str, Any]:
    """Load TOML config file."""
    if not HAS_TOML:
        raise ImportError("TOML support not available - install tomli (Python <3.11) or use Python 3.11+")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("thrash-protect", data)


def _load_json(path: str) -> dict[str, Any]:
    """Load JSON config file."""
    with open(path) as f:
        data = json.load(f)
    return data.get("thrash-protect", data)


def _load_ini(path: str) -> dict[str, Any]:
    """Load INI config file."""
    parser = configparser.ConfigParser()
    parser.read(path)
    if "thrash-protect" not in parser:
        return {}
    return dict(parser["thrash-protect"])


def load_from_env() -> dict[str, Any]:
    """Load configuration from environment variables."""
    env_config = {}

    for config_key, (converter, env_var, _) in CONFIG_SCHEMA.items():
        value = getenv(env_var)
        if value is not None:
            try:
                env_config[config_key] = converter(value)
            except (ValueError, TypeError) as e:
                logging.warning(f"Invalid value for {env_var}: {value} - {e}")

    return env_config


def get_defaults() -> dict[str, Any]:
    """Get default configuration values."""
    return {
        "debug_logging": False,
        "debug_checkstate": False,
        "interval": 0.5,
        "swap_page_threshold": 4,
        "pgmajfault_scan_threshold": None,  # Computed from swap_page_threshold if not set
        "use_psi": True,  # Use PSI for thrash detection if available
        "psi_threshold": 5.0,  # Trigger when some avg10 exceeds this percentage
        # Minimum swap pages in EACH direction before PSI is allowed to amplify.
        # Without it, memory PSI can multiply a one-page trickle past the trigger.
        "psi_swap_floor": 2,
        # Treat a saturated disk with no swap traffic as an IO problem, not thrashing
        "io_pressure_veto": True,
        "io_pressure_threshold": 50.0,  # io "full" avg10 above this counts as IO starvation
        "cmd_whitelist": get_default_whitelist(),
        "cmd_jobctrllist": get_default_jobctrllist(),
        "cmd_blacklist": [],
        "blacklist_score_multiplier": 16,
        "whitelist_score_divider": 64,  # 16 * 4
        "unfreeze_pop_ratio": 5,
        "test_mode": 0,
        "log_user_data_on_freeze": False,
        "log_user_data_on_unfreeze": True,
        "date_human_readable": True,
        "diagnostic_logging": False,
        "storage_type": "auto",
        "oom_protection": True,
        "oom_observation_window": 60,
        "oom_horizon": 600,
        "oom_swap_weight": None,  # Auto-set based on storage type
        "oom_low_pct": 100.0,  # 100% = always predict; lower (e.g. 10) to predict only when <10% free
        "pswp_weight": None,  # Auto-set based on storage type (HDD=128, SSD=8)
        "blacklist_expiry_time": 60.0,  # Seconds before a blacklist entry expires
        "blacklist_max_skip_count": 3,  # Unfreeze cycles a blacklisted item gets skipped
        "blacklist_escalation_cap": 8,  # Max allowance is this many times max_skip_count
        "oom_hold_ticks": 4,  # Ticks an OOM-driven freeze is held despite quiet swap
    }


def normalize_file_config(file_config: dict[str, Any]) -> dict[str, Any]:
    """Normalize config keys and values from file config.

    Handles underscore/hyphen differences and type conversions.
    """
    normalized = {}

    # Build reverse mapping from file key aliases to config keys
    file_key_to_config = {}
    for config_key, (_, _, aliases) in CONFIG_SCHEMA.items():
        for alias in aliases:
            file_key_to_config[alias] = config_key

    for key, value in file_config.items():
        # Normalize key (check alias mapping, then replace hyphens with underscores)
        norm_key = file_key_to_config.get(key, key.replace("-", "_"))

        # Apply type converter if available
        if norm_key in CONFIG_SCHEMA:
            converter = CONFIG_SCHEMA[norm_key][0]
            try:
                normalized[norm_key] = converter(value)
            except (ValueError, TypeError) as e:
                logging.warning(f"Invalid value for config key {key}: {value} - {e}")
        else:
            normalized[norm_key] = value

    return normalized


def load_config(args: argparse.Namespace) -> tuple[dict[str, Any], set[str]]:
    """Merge config from defaults <- file <- env <- CLI.

    Priority order (highest to lowest):
    1. CLI arguments
    2. Environment variables
    3. Config file
    4. Defaults

    Returns (config_dict, explicitly_set_keys) where explicitly_set_keys
    tracks which keys were set by file, env, or CLI (not just defaults).
    """
    # 1. Defaults
    final = get_defaults()
    explicitly_set = set()

    # 2. Config file
    config_path = getattr(args, "config", None)
    file_config = load_from_file(config_path)
    if file_config:
        normalized = normalize_file_config(file_config)
        explicitly_set.update(normalized.keys())
        final.update(normalized)

    # 3. Environment variables
    env_config = load_from_env()
    explicitly_set.update(env_config.keys())
    final.update(env_config)

    # 4. CLI arguments (non-None values only)
    for config_key in CONFIG_SCHEMA:
        value = getattr(args, config_key, None)
        if value is not None:
            explicitly_set.add(config_key)
            final[config_key] = value

    # Compute derived values
    if final.get("pgmajfault_scan_threshold") is None:
        final["pgmajfault_scan_threshold"] = final["swap_page_threshold"] * 4

    final["max_acceptable_time_delta"] = final["interval"] / 8.0

    return final, explicitly_set


def create_argument_parser() -> argparse.ArgumentParser:
    """Create argument parser with all configuration options."""
    p = argparse.ArgumentParser(
        description="Protect a Linux host from thrashing by temporarily suspending processes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration priority (highest to lowest):
  1. Command-line arguments
  2. Environment variables (THRASH_PROTECT_*)
  3. Config file (--config or auto-detected)
  4. Built-in defaults

Config file search order (first found is used):
  /etc/thrash-protect.yaml
  /etc/thrash-protect.yml
  /etc/thrash-protect.toml
  /etc/thrash-protect.json
  /etc/thrash-protect.conf

Example usage:
  thrash-protect
  thrash-protect --interval=1.0 --debug
  thrash-protect --config=/path/to/config.yaml
""",
    )

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # Config file
    p.add_argument(
        "--config",
        "-c",
        metavar="PATH",
        help="Configuration file path (auto-detects format by extension)",
    )

    # Debug options
    p.add_argument(
        "--debug",
        "--debug-logging",
        dest="debug_logging",
        action="store_true",
        default=None,
        help="Enable debug logging to stderr",
    )
    p.add_argument(
        "--debug-checkstate",
        dest="debug_checkstate",
        action="store_true",
        default=None,
        help="Log warnings when processes are in unexpected states",
    )

    # Timing options
    p.add_argument(
        "--interval",
        type=float,
        metavar="SECONDS",
        help="Sleep interval between checks (default: 0.5)",
    )
    p.add_argument(
        "--swap-page-threshold",
        dest="swap_page_threshold",
        type=int,
        metavar="N",
        help="Number of swap pages to trigger action (default: 4)",
    )
    p.add_argument(
        "--pgmajfault-scan-threshold",
        dest="pgmajfault_scan_threshold",
        type=int,
        metavar="N",
        help="Major page faults before process scan (default: swap_page_threshold * 4)",
    )

    # PSI (Pressure Stall Information) options
    p.add_argument(
        "--use-psi",
        dest="use_psi",
        action="store_true",
        default=None,
        help="Use PSI for thrash detection (default: true if available)",
    )
    p.add_argument(
        "--no-psi",
        dest="use_psi",
        action="store_false",
        help="Disable PSI, use swap page counting instead",
    )
    p.add_argument(
        "--psi-threshold",
        dest="psi_threshold",
        type=float,
        metavar="PCT",
        help="PSI some avg10 percentage to trigger action (default: 5.0)",
    )
    p.add_argument(
        "--psi-swap-floor",
        dest="psi_swap_floor",
        type=int,
        metavar="PAGES",
        help="Minimum swap pages in each direction before PSI may amplify (default: 2)",
    )
    p.add_argument(
        "--io-pressure-veto",
        dest="io_pressure_veto",
        action="store_true",
        default=None,
        help="Suppress triggering when a saturated disk explains the pressure (default: true)",
    )
    p.add_argument(
        "--no-io-pressure-veto",
        dest="io_pressure_veto",
        action="store_false",
        help="Do not suppress triggering when a saturated disk explains the pressure",
    )
    p.add_argument(
        "--io-pressure-threshold",
        dest="io_pressure_threshold",
        type=float,
        metavar="PCT",
        help="io full avg10 percentage counting as IO starvation (default: 50.0)",
    )

    # Process lists
    p.add_argument(
        "--cmd-whitelist",
        dest="cmd_whitelist",
        nargs="+",
        metavar="CMD",
        help="Processes to protect from suspension (space-separated)",
    )
    p.add_argument(
        "--cmd-blacklist",
        dest="cmd_blacklist",
        nargs="+",
        metavar="CMD",
        help="Processes to prioritize for suspension (space-separated)",
    )
    p.add_argument(
        "--cmd-jobctrllist",
        dest="cmd_jobctrllist",
        nargs="+",
        metavar="CMD",
        help="Processes with job control (suspend parent too)",
    )

    # Scoring options
    p.add_argument(
        "--blacklist-score-multiplier",
        dest="blacklist_score_multiplier",
        type=int,
        metavar="N",
        help="Score multiplier for blacklisted processes (default: 16)",
    )
    p.add_argument(
        "--whitelist-score-divider",
        dest="whitelist_score_divider",
        type=int,
        metavar="N",
        help="Score divider for whitelisted processes (default: 64)",
    )
    p.add_argument(
        "--unfreeze-pop-ratio",
        dest="unfreeze_pop_ratio",
        type=int,
        metavar="N",
        help="Ratio of stack pops vs queue pops when unfreezing (default: 5)",
    )

    # Testing
    p.add_argument(
        "--test-mode",
        dest="test_mode",
        type=int,
        metavar="N",
        help="Pretend thrashing every 2^N iterations (for testing)",
    )

    # Logging options
    p.add_argument(
        "--log-user-data-on-freeze",
        dest="log_user_data_on_freeze",
        action="store_true",
        default=None,
        help="Log detailed process info when freezing",
    )
    p.add_argument(
        "--log-user-data-on-unfreeze",
        dest="log_user_data_on_unfreeze",
        action="store_true",
        default=None,
        help="Log detailed process info when unfreezing (default: true)",
    )
    p.add_argument(
        "--no-log-user-data-on-unfreeze",
        dest="log_user_data_on_unfreeze",
        action="store_false",
        help="Disable logging detailed process info when unfreezing",
    )
    p.add_argument(
        "--date-human-readable",
        dest="date_human_readable",
        action="store_true",
        default=None,
        help="Use human-readable date format in logs (default: true)",
    )
    p.add_argument(
        "--date-unix",
        dest="date_human_readable",
        action="store_false",
        help="Use Unix timestamp in logs",
    )

    # Diagnostic options
    p.add_argument(
        "--diagnostic",
        dest="diagnostic_logging",
        action="store_true",
        default=None,
        help="Enable diagnostic logging (logs selector decisions, scores, and PSI weights)",
    )

    # Storage type
    p.add_argument(
        "--storage-type",
        dest="storage_type",
        choices=["auto", "ssd", "hdd"],
        default=None,
        help="Swap storage type for threshold tuning (default: auto-detect)",
    )

    # OOM protection
    p.add_argument(
        "--oom-protection",
        dest="oom_protection",
        action="store_true",
        default=None,
        help="Enable proactive OOM protection via memory exhaustion prediction (default: true)",
    )
    p.add_argument(
        "--no-oom-protection",
        dest="oom_protection",
        action="store_false",
        help="Disable proactive OOM protection",
    )
    p.add_argument(
        "--oom-observation-window",
        dest="oom_observation_window",
        type=int,
        metavar="SECONDS",
        help="Main observation window for OOM prediction in seconds (default: 60)",
    )
    p.add_argument(
        "--oom-horizon",
        dest="oom_horizon",
        type=int,
        metavar="SECONDS",
        help="Prediction horizon for the main observation window in seconds (default: 600)",
    )
    p.add_argument(
        "--oom-swap-weight",
        dest="oom_swap_weight",
        type=float,
        metavar="WEIGHT",
        help="Weight for swap in OOM prediction (default: auto based on storage type, SSD=2.0 HDD=4.0)",
    )
    p.add_argument(
        "--oom-low-pct",
        dest="oom_low_pct",
        type=float,
        metavar="PERCENT",
        help="Only predict OOM when available resources are below this percentage of total (default: 100.0)",
    )
    p.add_argument(
        "--pswp-weight",
        dest="pswp_weight",
        type=float,
        metavar="WEIGHT",
        help="Weight of disk swap pages relative to zswap pages (default: auto based on storage type, SSD=8 HDD=128)",
    )

    # Repeat-offender blacklist
    p.add_argument(
        "--blacklist-expiry-time",
        dest="blacklist_expiry_time",
        type=float,
        metavar="SECONDS",
        help="Seconds before a repeat-offender blacklist entry expires (default: 60.0)",
    )
    p.add_argument(
        "--blacklist-max-skip-count",
        dest="blacklist_max_skip_count",
        type=int,
        metavar="N",
        help="Number of unfreeze cycles a blacklisted process gets skipped (default: 3)",
    )
    p.add_argument(
        "--blacklist-escalation-cap",
        dest="blacklist_escalation_cap",
        type=int,
        metavar="N",
        help="Cap the escalated hold at this many times --blacklist-max-skip-count (default: 8)",
    )
    p.add_argument(
        "--oom-hold-ticks",
        dest="oom_hold_ticks",
        type=int,
        metavar="N",
        help="Ticks an OOM-driven freeze is held even when swap is quiet (default: 4)",
    )

    return p


#########################
## Cgroup Freezing Support
#########################


def get_cgroup_path(pid: int) -> str | None:
    """Get the cgroup v2 path for a process, returns None if not available."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                # Format: hierarchy-ID:controller-list:cgroup-path
                # For cgroup v2: 0::/<path>
                parts = line.strip().split(":", 2)
                if len(parts) == 3 and parts[0] == "0":
                    cgroup_rel_path = parts[2]
                    if cgroup_rel_path.startswith("/"):
                        cgroup_rel_path = cgroup_rel_path[1:]
                    return f"/sys/fs/cgroup/{cgroup_rel_path}"
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return None


def is_cgroup_freezable(cgroup_path: str | None) -> bool:
    """Check if cgroup supports freezing."""
    if not cgroup_path:
        return False
    freeze_file = os.path.join(cgroup_path, "cgroup.freeze")
    return os.path.exists(freeze_file)


def freeze_cgroup(cgroup_path: str) -> bool:
    """Freeze all processes in a cgroup. Returns True on success."""
    try:
        freeze_file = os.path.join(cgroup_path, "cgroup.freeze")
        with open(freeze_file, "w") as f:
            f.write("1")
        logging.debug(f"Froze cgroup {cgroup_path}")
        return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logging.warning(f"Failed to freeze cgroup {cgroup_path}: {e}")
        return False


def unfreeze_cgroup(cgroup_path: str) -> bool:
    """Unfreeze all processes in a cgroup. Returns True on success."""
    try:
        freeze_file = os.path.join(cgroup_path, "cgroup.freeze")
        with open(freeze_file, "w") as f:
            f.write("0")
        logging.debug(f"Unfroze cgroup {cgroup_path}")
        return True
    except (FileNotFoundError, PermissionError, OSError) as e:
        logging.warning(f"Failed to unfreeze cgroup {cgroup_path}: {e}")
        return False


def get_own_cgroup_path() -> str | None:
    """Get the cgroup path for thrash-protect's own process, lazily cached."""
    if _tp._own_cgroup_path == "_unset":
        _tp._own_cgroup_path = get_cgroup_path(getpid())
    return _tp._own_cgroup_path


def should_use_cgroup_freeze(pid: int) -> str | None:
    """Check if we should use cgroup freezing for this process.

    Returns cgroup_path if cgroup freezing should be used, None otherwise.
    Only uses cgroup freezing for .scope cgroups under user@NNN.service/
    (e.g., tmux-spawn, screen sessions). Rejects session-N.scope which lives
    directly under user-N.slice/ and contains the entire graphical session
    (sway, waybar, Xwayland, all chromium renderers, etc.).
    """
    cgroup_path = get_cgroup_path(pid)
    if not cgroup_path or not is_cgroup_freezable(cgroup_path):
        return None
    # Must be a .scope cgroup (not a .slice or .service)
    if not cgroup_path.endswith(".scope"):
        return None
    # Must be under user@NNN.service/ (tmux-spawn, screen, systemd-run scopes)
    # Reject session-N.scope which is under user-N.slice/ and contains
    # the entire login session (124+ processes)
    if "/user@" not in cgroup_path:
        return None
    return cgroup_path


#########################
## Helper Functions
#########################


def normalize_pids(pids: tuple[int, ...] | list[int] | int | None) -> tuple[int, ...]:
    """Normalize pids to a tuple.

    Handles single pids, tuples, lists, and other iterables consistently.
    """
    if pids is None:
        return ()
    if not hasattr(pids, "__iter__") or isinstance(pids, str):
        return (pids,)
    return tuple(pids)


def apply_score_adjustments(score: float, cmd: str) -> float:
    """Apply whitelist/blacklist score adjustments.

    Divides score for whitelisted commands, multiplies for blacklisted.
    Returns the adjusted score.
    """
    if cmd in config.cmd_whitelist:
        score /= config.whitelist_score_divider
    if cmd in config.cmd_blacklist:
        score *= config.blacklist_score_multiplier
    return score


def unpack_frozen_item(item: tuple[str, ...]) -> tuple[str, str | None, tuple[int, ...]]:
    """Unpack a frozen_items entry into (item_type, cgroup_path, pids).

    For cgroup items: returns ('cgroup', cgroup_path, pids)
    For sigstop items: returns ('sigstop', None, pids)
    """
    if item[0] == "cgroup":
        return item[0], item[1], item[2]
    else:  # sigstop
        return item[0], None, item[1]


#########################
## PSI (Pressure Stall Information) Support
#########################

# Cache for PSI availability check
_psi_available = None


def is_psi_available() -> bool:
    """Check if PSI is available on this system (Linux 4.20+)."""
    global _psi_available
    if _psi_available is None:
        _psi_available = os.path.exists("/proc/pressure/memory")
    return _psi_available


def _read_psi(path: str) -> dict[str, dict[str, float | int]] | None:
    """Read a /proc/pressure/* file.

    Returns a dict with 'some' and 'full' pressure metrics, each containing
    avg10, avg60, avg300 (percentages) and total (microseconds).
    Returns None if PSI is not available or the file cannot be read.
    """
    if not is_psi_available():
        return None
    try:
        pressure = {}
        with open(path) as f:
            for line in f:
                parts = line.strip().split()
                if not parts:
                    continue
                ptype = parts[0]  # 'some' or 'full'
                metrics = {}
                for part in parts[1:]:
                    if "=" in part:
                        key, value = part.split("=", 1)
                        if key == "total":
                            metrics[key] = int(value)
                        else:
                            metrics[key] = float(value)
                pressure[ptype] = metrics
        return pressure
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None


def get_memory_pressure() -> dict[str, dict[str, float | int]] | None:
    """Read memory pressure from /proc/pressure/memory.

    Example output:
        {'some': {'avg10': 0.0, 'avg60': 0.0, 'avg300': 0.0, 'total': 0},
         'full': {'avg10': 5.23, 'avg60': 2.10, 'avg300': 0.50, 'total': 123456}}
    """
    return _read_psi("/proc/pressure/memory")


def get_io_pressure() -> dict[str, dict[str, float | int]] | None:
    """Read IO pressure from /proc/pressure/io.

    Memory pressure and IO pressure overlap: a page-cache refault from a
    saturated disk raises both, even though nothing is short of memory.
    Reading them side by side is what lets us tell the two apart.
    """
    return _read_psi("/proc/pressure/io")


def get_workingset_refaults() -> tuple[int, int] | None:
    """Read (anon, file) workingset refault counters from /proc/vmstat.

    A refault is a page we evicted and then had to read back.  The anon
    counter is the honest definition of memory thrashing; the file counter
    rises whenever anything walks more file data than fits in the page cache,
    which is a completely different problem with a completely different cure.

    Both counters exist from Linux 5.9.  Older kernels report (0, 0), which
    callers must treat as "no information" rather than "no refaults".

    A failed read returns None instead, which is a different thing again: the
    zeros of an old kernel are stable across samples, whereas a one-off
    failure would otherwise make the *next* interval subtract zero and so
    compute its ratio against the lifetime counters.
    """
    counters = {"workingset_refault_anon": 0, "workingset_refault_file": 0}
    try:
        with open("/proc/vmstat") as vmstat:
            for line in vmstat:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in counters:
                    counters[parts[0]] = int(parts[1])
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None
    return (counters["workingset_refault_anon"], counters["workingset_refault_file"])


#########################
## Swap Storage Detection
#########################


def detect_swap_storage_type() -> str | None:
    """Detect whether swap storage is SSD or HDD.

    Reads /proc/swaps for active swap devices, resolves each to a block device,
    and checks /sys/block/<dev>/queue/rotational (0=SSD, 1=HDD).

    Returns "ssd", "hdd", or None if detection fails.
    If any swap is on HDD, returns "hdd" (conservative).
    """
    try:
        with open("/proc/swaps") as f:
            lines = f.readlines()
    except (FileNotFoundError, PermissionError, OSError):
        return None

    found_ssd = False
    for line in lines[1:]:  # skip header
        parts = line.split()
        if not parts:
            continue
        device = parts[0]

        rotational = _get_device_rotational(device)
        if rotational is None:
            continue
        if rotational == 1:
            return "hdd"
        if rotational == 0:
            found_ssd = True

    return "ssd" if found_ssd else None


def _get_device_rotational(device: str) -> int | None:
    """Check if a device is rotational (1=HDD) or not (0=SSD).

    Resolves the device path to a block device and checks
    /sys/block/<dev>/queue/rotational.
    Returns 0, 1, or None if detection fails.
    """
    try:
        # Resolve symlinks (e.g. /dev/dm-0 -> real device)
        real_path = os.path.realpath(device)
        st = os.stat(real_path)
    except (OSError, ValueError):
        return None

    # Get major:minor from the device
    import stat as stat_mod

    if not stat_mod.S_ISBLK(st.st_mode):
        # Not a block device (e.g. swap file on tmpfs)
        return None

    major = os.major(st.st_rdev)
    minor = os.minor(st.st_rdev)

    # Try /sys/dev/block/major:minor -> resolve to find the parent disk
    sys_path = f"/sys/dev/block/{major}:{minor}"
    try:
        real_sys_path = os.path.realpath(sys_path)
    except OSError:
        return None

    # Walk up to find the disk (parent of partition)
    # e.g. /sys/devices/.../sda/sda1 -> we want /sys/devices/.../sda
    path = real_sys_path
    while path and path != "/":
        rotational_file = os.path.join(path, "queue", "rotational")
        try:
            with open(rotational_file) as f:
                return int(f.read().strip())
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            pass
        path = os.path.dirname(path)

    return None


#########################
## OOM Protection
#########################


def read_meminfo() -> tuple[int, int, int, int] | None:
    """Read memory stats from /proc/meminfo (in kB).

    Returns (mem_available, swap_free, mem_total, swap_total) or None if unavailable.
    """
    mem_available = None
    swap_free = None
    mem_total = None
    swap_total = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
                elif line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                if all(v is not None for v in (mem_available, swap_free, mem_total, swap_total)):
                    return (mem_available, swap_free, mem_total, swap_total)
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        pass
    return None


class MemoryExhaustionPredictor:
    """Predicts memory exhaustion using multi-scale linear projection.

    Tracks a weighted "available resources" metric:
        available = mem_available + swap_free * swap_weight

    Where swap_weight reflects that swap depletion is more dangerous
    (SSD: 2.0, HDD: 4.0).  This means the predictor naturally becomes
    alarmed when swap starts being consumed, even if MemAvailable is stable.

    Maintains a sliding window of observations and checks projections at
    multiple time scales, each with a proportional horizon:

    - Main window (default 60s) with main horizon (default 600s)
    - Short window (1/12 of main, ~5s) with proportional horizon (~50s)

    The constant ratio (horizon / observation_window) ensures that longer
    observation windows tolerate slower declines while shorter windows
    only trigger on rapid consumption.  This prevents false positives from
    normal memory fluctuations while still catching runaway allocations.
    """

    # Observation scales relative to the main window.
    # Each scale checks a different time range with proportional horizon.
    # The 1/60 scale (~1s window, ~10s horizon) gives fast feedback after
    # a reset — essential when freezing/unfreezing several processes per second.
    _SCALES = (1.0, 1 / 12, 1 / 60)
    ## I'm not sure if I like the idea with hard-coded "scales".
    ## I think I would favor a way to algorithmically find an
    ## appropriate "horizon".

    def __init__(
        self,
        swap_weight: float = 2.0,
        observation_window: int = 60,
        horizon: int = 600,
        low_pct: float = 100.0,
    ) -> None:
        self.swap_weight = swap_weight
        self.observation_window = observation_window
        self.horizon = horizon
        self.low_pct = low_pct
        self._horizon_ratio = horizon / observation_window if observation_window > 0 else 10.0
        self._observations: deque[tuple[float, float]] = deque()
        self._max_age = observation_window * 1.5

    def _find_observation_at(self, target_time: float, tolerance: float) -> tuple[float, float] | None:
        """Find the observation closest to target_time within tolerance.

        Returns None if no observation falls within [target_time - tolerance,
        target_time + tolerance].  This prevents using a 0.5s-old observation
        when we need one from 60s ago.

        In practice the caller passes target_time = now - window and
        tolerance = window / 2, so the accepted age range is an asymmetric
        window: [window/2, window*1.5] seconds ago.
        """
        best: tuple[float, float] | None = None
        best_diff = float("inf")
        for obs_time, obs_available in self._observations:
            diff = abs(obs_time - target_time)
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best = (obs_time, obs_available)
        return best

    def update_and_predict(self) -> float | None:
        """Record observation, project time to exhaustion at multiple scales.

        Returns the minimum ETA (seconds) across all scales that triggered
        (i.e., ETA < that scale's horizon), or None if no scale triggered.
        """
        meminfo = read_meminfo()
        if meminfo is None:
            if diagnostic_log:
                diagnostic_log("OOM predictor: read_meminfo() returned None, skipping")
            return None

        mem_available, swap_free, mem_total, swap_total = meminfo
        available = mem_available + swap_free * self.swap_weight
        total = mem_total + swap_total * self.swap_weight
        now = time.time()

        self._observations.append((now, available))

        # Prune old observations
        cutoff = now - self._max_age
        while self._observations and self._observations[0][0] < cutoff:
            self._observations.popleft()

        if len(self._observations) < 2:
            if diagnostic_log:
                diagnostic_log(f"OOM predictor: only {len(self._observations)} observation(s), need ≥2")
            return None

        # Optional low_pct threshold (effectively disabled at 100%)
        avail_pct = available / total * 100 if total > 0 else 100.0
        if diagnostic_log:
            diagnostic_log(
                f"OOM predictor: mem_avail={mem_available}kB swap_free={swap_free}kB "
                f"available={available:.0f} total={total:.0f} ({avail_pct:.1f}%) "
                f"observations={len(self._observations)}"
            )
        if total > 0 and avail_pct >= self.low_pct:
            if diagnostic_log:
                diagnostic_log(f"OOM predictor: {avail_pct:.1f}% >= low_pct {self.low_pct:.1f}%, skipping")
            return None

        min_eta: float | None = None
        for scale in self._SCALES:
            target_window = self.observation_window * scale
            target_horizon = target_window * self._horizon_ratio

            target_time = now - target_window
            # Tolerance: accept observations within half the target window.
            # This prevents using a 0.5s-old sample for a 60s window.
            tolerance = target_window * 0.5
            past = self._find_observation_at(target_time, tolerance)
            if past is None:
                if diagnostic_log:
                    diagnostic_log(
                        f"OOM predictor scale={scale:.4f} (window={target_window:.1f}s): "
                        f"no past observation within ±{tolerance:.1f}s of {target_window:.1f}s ago"
                    )
                continue

            past_time, past_available = past
            # Don't compare an observation with itself
            if past_time == now:
                continue

            # Not declining at this scale
            if available >= past_available:
                if diagnostic_log:
                    diagnostic_log(
                        f"OOM predictor scale={scale:.4f} (window={target_window:.1f}s): "
                        f"stable or rising (past={past_available:.0f} now={available:.0f})"
                    )
                continue

            dt = now - past_time
            if dt <= 0:
                continue

            decline_rate = (past_available - available) / dt
            if decline_rate <= 0:
                continue

            eta = available / decline_rate

            if diagnostic_log:
                diagnostic_log(
                    f"OOM predictor scale={scale:.4f} (window={target_window:.1f}s "
                    f"horizon={target_horizon:.1f}s): "
                    f"decline={decline_rate:.1f}kB/s eta={eta:.0f}s "
                    f"{'TRIGGERED' if eta < target_horizon else 'within horizon, no trigger'}"
                )

            if eta < target_horizon and (min_eta is None or eta < min_eta):
                min_eta = eta

        if diagnostic_log:
            diagnostic_log(
                f"OOM predictor result: {'FREEZE predicted, min_eta=' + f'{min_eta:.0f}s' if min_eta is not None else 'no trigger'}"
            )
        return min_eta

    def reset(self) -> None:
        """Clear observation history.

        Must be called after a freeze triggered by OOM prediction.
        The old observations reflect pre-freeze memory trends that are
        no longer representative.  After reset, the short scale (~5s)
        will be the first to accumulate enough data to make predictions,
        giving fast feedback on whether the freeze helped.
        """
        self._observations.clear()

    def should_freeze(self) -> bool:
        """Check if proactive freezing should be triggered.

        Returns True if any observation scale predicts exhaustion within
        its proportional horizon.
        """
        eta = self.update_and_predict()
        if eta is not None:
            logging.info("OOM protection: memory exhaustion predicted in %.0f seconds", eta)
            return True
        return False


class config:
    """
    Configuration namespace - populated at startup by init_config().

    Access configuration values as config.interval, config.cmd_whitelist, etc.
    """

    pass


def init_config(args: argparse.Namespace | None = None) -> None:
    """Initialize the config namespace from all configuration sources.

    This should be called once at startup, after argument parsing.
    """
    if args is None:
        # Create a minimal args namespace if none provided
        args = argparse.Namespace()

    cfg, explicitly_set = load_config(args)

    # SSD auto-detection: adjust swap_page_threshold if user didn't set it explicitly
    resolved_storage = cfg["storage_type"]
    if resolved_storage == "auto":
        resolved_storage = detect_swap_storage_type()
    if resolved_storage == "ssd" and "swap_page_threshold" not in explicitly_set:
        cfg["swap_page_threshold"] = 64
        # Recompute derived pgmajfault threshold if also not explicit
        if "pgmajfault_scan_threshold" not in explicitly_set:
            cfg["pgmajfault_scan_threshold"] = cfg["swap_page_threshold"] * 4
        logging.debug("SSD detected: swap_page_threshold adjusted to 64")
    cfg["_resolved_storage_type"] = resolved_storage

    # Resolve OOM swap weight based on storage type if not explicitly set
    if cfg["oom_swap_weight"] is None:
        if resolved_storage == "hdd":
            cfg["oom_swap_weight"] = 4.0
        else:
            cfg["oom_swap_weight"] = 2.0  # SSD or unknown

    # Resolve pswp_weight based on storage type if not explicitly set.
    # This weights disk swap pages relative to zswap pages in the thrash
    # detection formula. HDD disk I/O is ~1000x slower than zswap; SSD ~10-50x.
    # The chosen defaults (HDD=128, SSD=8) are deliberately conservative but
    # ensure that even a few disk swap pages are treated as a strong signal.
    # Note: the effective zswap trigger level (swap_page_threshold * pswp_weight)
    # is the same for both storage types (4*128 = 64*8 = 512 pages), so zswap
    # sensitivity does not depend on storage type.
    if cfg["pswp_weight"] is None:
        if resolved_storage == "hdd":
            cfg["pswp_weight"] = 128.0
        else:
            cfg["pswp_weight"] = 8.0  # SSD or unknown

    # Set all config values as attributes on the config class
    for key, value in cfg.items():
        setattr(config, key, value)

    # Set up freeze blacklist on the singleton
    _tp.freeze_blacklist = FreezeBlacklist(
        expiry_time=config.blacklist_expiry_time,
        max_skip_count=config.blacklist_max_skip_count,
        escalation_cap=config.blacklist_escalation_cap,
    )
    _tp.process_selector = GlobalProcessSelector(blacklist=_tp.freeze_blacklist)

    # Set up memory exhaustion predictor on the singleton
    if config.oom_protection:
        _tp.memory_predictor = MemoryExhaustionPredictor(
            swap_weight=config.oom_swap_weight,
            observation_window=config.oom_observation_window,
            horizon=config.oom_horizon,
            low_pct=config.oom_low_pct,
        )
    else:
        _tp.memory_predictor = None

    # Set up debug_check_state function based on config
    global debug_check_state
    if config.debug_checkstate:
        debug_check_state = _debug_check_state
    else:
        debug_check_state = lambda a, b: None

    # Auto-enable diagnostic logging for dev/pre-release versions unless explicitly disabled
    if (
        "diagnostic_logging" not in explicitly_set
        and not config.diagnostic_logging
        and any(tag in __version__.lower() for tag in ("dev", "alpha", "beta", "rc", ".dirty"))
    ):
        config.diagnostic_logging = True
        logging.info("diagnostic logging auto-enabled for dev version %s", __version__)

    # Set up diagnostic_log function based on config
    # When disabled, set to None so `if diagnostic_log:` guards skip string formatting
    global diagnostic_log
    if config.diagnostic_logging:
        diagnostic_log = _diagnostic_log
    else:
        diagnostic_log = None


# Initialize with defaults immediately so the module can be imported
# (will be re-initialized in main() with proper args)
def _init_default_config() -> None:
    """Initialize config with defaults for module import compatibility."""
    defaults = get_defaults()
    for key, value in defaults.items():
        setattr(config, key, value)
    # Compute derived values
    if config.pgmajfault_scan_threshold is None:
        config.pgmajfault_scan_threshold = config.swap_page_threshold * 4
    config.max_acceptable_time_delta = config.interval / 8.0


_init_default_config()


class SystemState:
    """A "system state" is a collection of observed and calculated
    variables at a specific point of time.  We'll probably never have
    more than two instantiated objects - "last" and "current".  (This
    class replaces a bunch of global variables from version 0.8 -
    let's hope that the overhead in instantiation and garbage
    collection will be insignificant)
    """

    ## Class-level fallbacks.  SystemState objects are also built through
    ## __new__ (notably in the test suite), and every consumer of these must
    ## cope with them being absent anyway - (0, 0) refaults and no io_psi both
    ## mean "no information", which is exactly the pre-5.9/pre-4.20 situation.
    refaults: tuple[int, int] = (0, 0)
    io_psi: dict[str, dict[str, float | int]] | None = None

    def __init__(self) -> None:
        self.timestamp: float = time.time()
        self.pagefaults: int | None = self.get_pagefaults()
        self.swapcount: tuple[int, ...] = self.get_swapcount()
        self.psi: dict[str, dict[str, float | int]] | None = get_memory_pressure()  # None if PSI not available
        self.io_psi = get_io_pressure()  # None if PSI not available
        self.refaults = get_workingset_refaults()  # (0, 0) on kernels below 5.9
        self.cooldown_counter: int = 0
        self.unfrozen_pid: tuple[int, ...] | list[int] | None = None
        self.timer_alert: bool = False

    def get_pagefaults(self) -> int | None:
        with open("/proc/vmstat") as vmstat:
            line = ""
            while line is not None:
                line = vmstat.readline()
                if line.startswith("pgmajfault "):
                    return int(line[12:])

    def get_swapcount(self) -> tuple[int, ...]:
        """Read swap counters from /proc/vmstat.

        Returns a 4-tuple: (pswpin, pswpout, zswpin, zswpout)
          - pswpin/pswpout: pages swapped to/from real swap device (disk I/O)
          - zswpin/zswpout: pages decompressed from / compressed into zswap pool
        zswp* counters are present on Linux 6.3+ and default to 0 on older kernels.
        """
        counters = {"pswpin": 0, "pswpout": 0, "zswpin": 0, "zswpout": 0}
        with open("/proc/vmstat") as vmstat:
            for line in vmstat:
                parts = line.split()
                if len(parts) >= 2 and parts[0] in counters:
                    counters[parts[0]] = int(parts[1])
        return (counters["pswpin"], counters["pswpout"], counters["zswpin"], counters["zswpout"])

    def check_swap_threshold(self, prev: SystemState) -> bool:
        self.cooldown_counter = prev.cooldown_counter
        if config.test_mode and not random.getrandbits(config.test_mode):
            self.cooldown_counter = prev.cooldown_counter + 1
            return True

        ## will return True if we have bidirectional traffic to swap,
        ## or if we have a big one-directional flow of data.
        ##
        ## Disk swap pages (pswpin/pswpout) are weighted by pswp_weight relative
        ## to zswap pages (zswpin/zswpout), since disk I/O is much slower (HDD:
        ## ~1000x, SSD: ~10-50x) than zswap compression/decompression.
        ## The effective threshold is swap_page_threshold * pswp_weight, which
        ## keeps backward-compat for disk-only systems while making the zswap
        ## trigger level storage-type-independent.
        ##
        ## The 0.1 epsilon prevents zero-product collapse when one direction is
        ## idle: a large one-directional flow still contributes to the product.
        pswp_weight = config.pswp_weight
        effective_threshold = config.swap_page_threshold * pswp_weight

        delta_disk_in = self.swapcount[0] - prev.swapcount[0]
        delta_disk_out = self.swapcount[1] - prev.swapcount[1]
        delta_zswap_in = self.swapcount[2] - prev.swapcount[2]
        delta_zswap_out = self.swapcount[3] - prev.swapcount[3]

        # pswp_weight scales disk pages up (not zswap pages down) so that
        # effective_threshold = swap_page_threshold * pswp_weight cancels out,
        # making the trigger level storage-type-independent.
        combined_in = delta_disk_in * pswp_weight + delta_zswap_in + 0.1
        combined_out = delta_disk_out * pswp_weight + delta_zswap_out + 0.1

        swap_product = (combined_in / effective_threshold) * (combined_out / effective_threshold)

        ## Total swap traffic actually observed, unweighted.  Used to decide
        ## whether there is enough of a swap signal to be worth amplifying.
        raw_swap_in = delta_disk_in + delta_zswap_in
        raw_swap_out = delta_disk_out + delta_zswap_out

        ## IO-pressure veto: a saturated disk raises memory PSI through
        ## page-cache refault alone.  When the disk is stalling everything and
        ## swap is meanwhile idle, this is an IO problem and suspending
        ## processes cannot help - it only moves a fixed IO budget from the
        ## victims to whatever is hogging the device.
        io_vetoed = False
        if config.io_pressure_veto and self.io_psi and "full" in self.io_psi:
            io_full = self.io_psi["full"].get("avg10", 0)
            ## Gate on the unamplified swap signal, not on the PSI floor: the
            ## veto only bites when PSI amplification is the sole reason we
            ## would trigger.  Real thrashing clears swap_product > 1.0 on its
            ## own and is therefore never vetoed, however busy the disk is.
            if io_full > config.io_pressure_threshold and swap_product <= 1.0:
                logging.debug(
                    f"IO pressure veto: io full avg10={io_full}%, "
                    f"swap in/out={raw_swap_in}/{raw_swap_out} - "
                    f"IO starvation, not thrashing"
                )
                io_vetoed = True

        ## PSI weight: amplify swap signal when memory pressure is detected
        ## Uses "some" (at least one task stalled) rather than "full" (all CPUs stalled),
        ## because "full" can be near-zero even during heavy thrashing on multi-core systems.
        psi_weight = 1.0
        ## Hoisted out of the PSI block so the diagnostic line can report it.
        ## None means "never computed" - the sample was vetoed, PSI was off or
        ## the swap floor blocked amplification.  Reporting the 1.0 default
        ## instead would read as "refaults were 100% anonymous", which is the
        ## exact inverse of the truth in the swap-full case.
        anon_fraction: float | None = None
        if not io_vetoed and config.use_psi and self.psi and "some" in self.psi:
            psi_some = self.psi["some"].get("avg10", 0)

            ## Only amplify a swap signal that actually exists.  Memory PSI is
            ## a percentage and reaches 50%+ on page-cache churn, so without a
            ## floor it can multiply a one-page trickle straight past 1.0.
            if min(raw_swap_in, raw_swap_out) < config.psi_swap_floor:
                logging.debug(
                    f"PSI not applied: swap in/out={raw_swap_in}/{raw_swap_out} "
                    f"below psi_swap_floor={config.psi_swap_floor}"
                )
            else:
                ## Trust memory PSI only insofar as it is anon-driven.  A
                ## refault of an anonymous page is thrashing; a refault of a
                ## file page is usually just something reading more data than
                ## fits in cache.  On kernels without the split counters the
                ## delta is zero and the fraction stays 1.0 (no damping), and
                ## so it does when either sample could not be read at all.
                anon_fraction = 1.0
                if self.refaults is not None and prev.refaults is not None:
                    delta_anon = self.refaults[0] - prev.refaults[0]
                    delta_file = self.refaults[1] - prev.refaults[1]
                    total_refaults = delta_anon + delta_file
                    ## A negative delta means the counters went backwards - a
                    ## reset, a per-cpu fold - and that is missing information,
                    ## not evidence of file-backed refaults.  Damping on it
                    ## would turn the amplifier into a suppressor on garbage.
                    if delta_anon >= 0 and delta_file >= 0 and total_refaults > 0:
                        anon_fraction = delta_anon / total_refaults
                        logging.debug(
                            f"refault split: anon={delta_anon} file={delta_file} anon_fraction={anon_fraction:.3f}"
                        )

                psi_weight = 1.0 + (psi_some / config.psi_threshold) * anon_fraction
                if psi_weight > 1.0:
                    logging.debug(f"PSI weight applied: some avg10={psi_some}%, weight={psi_weight:.2f}")

        ## A vetoed sample is a quiet sample, not an early return: it still has
        ## to fall through to the counter bookkeeping below, or whatever was
        ## suspended when the disk got busy would never be resumed.  (The veto
        ## only fires when swap_product <= 1.0 and it leaves psi_weight at 1.0,
        ## so the comparison below is already False - this is belt and braces.)
        ret = not io_vetoed and swap_product * psi_weight > 1.0
        if diagnostic_log:
            diagnostic_log(
                f"check_swap_threshold: "
                f"disk_in={delta_disk_in} disk_out={delta_disk_out} "
                f"zswap_in={delta_zswap_in} zswap_out={delta_zswap_out} "
                f"pswp_weight={pswp_weight} eff_threshold={effective_threshold:.0f} "
                f"combined_in={combined_in:.1f} combined_out={combined_out:.1f} "
                f"swap_product={swap_product:.4f} psi_weight={psi_weight:.2f} "
                f"io_vetoed={io_vetoed} "
                f"anon_fraction={'n/a' if anon_fraction is None else format(anon_fraction, '.3f')} "
                f"final={swap_product * psi_weight:.4f} trigger={ret}"
            )
        ## Increase or decrease the busy-counter ... or keep it where it is
        if ret:
            ## thrashing alert, increase the counter
            self.cooldown_counter = prev.cooldown_counter + 1
            if not prev.timer_alert:
                logging.debug(
                    "potential thrashing detected, but we got no timing alarm. Perhaps max_acceptable_time_delta should be tweaked down"
                )
                config.max_acceptable_time_delta /= 1.1
        elif (
            prev.cooldown_counter
            and prev.swapcount == self.swapcount
            and self.timestamp - prev.timestamp >= self.get_sleep_interval()
        ):
            ## not busy at all, and we have slept since the previous check.  Decrease counter.
            self.cooldown_counter = prev.cooldown_counter - 1
            if prev.timer_alert:
                logging.debug(
                    "we got a timer alert, even if the system is not busy.  Increasing the timer alert threshold"
                )
                config.max_acceptable_time_delta *= 1.1
        else:
            logging.debug(
                "prev.swapcount==self.swapcount: %s,  self.timestamp-prev.timestamp>=self.get_sleep_interval(): %s, self.timestamp-prev.timestamp: %s, self.get_sleep_interval(): %s"
                % (
                    prev.swapcount == self.swapcount,
                    self.timestamp - prev.timestamp >= self.get_sleep_interval(),
                    self.timestamp - prev.timestamp,
                    self.get_sleep_interval(),
                )
            )
            ## some swapin or swapout has been observed, or we haven't slept since previous run.  Keep the cooldown counter steady.
            ## (Hm - we risk that process A gets frozen but never unfrozen due to process B generating swap activity?)
        return ret

    def check_thrashing(self, prev: SystemState) -> bool:
        """Check if the system is thrashing using swap page counting with PSI amplification.

        Returns True if thrashing is detected, False otherwise.
        Uses swap page counting as the primary trigger. When PSI is available
        and enabled, it amplifies the swap signal (heavy PSI + small swap = trigger,
        but zero swap + any PSI = no trigger).
        """
        return self.check_swap_threshold(prev)

    def get_sleep_interval(self) -> float:
        return config.interval / (self.cooldown_counter + 1.0)

    def check_delay(self, expected_delay: float = 0) -> bool:
        """
        If the code execution takes a too long time it may be that we're thrashing and this process has been swapped out.
        (TODO: detect possible problem: wrong tuning of max_acceptable_time_delta causes this to always trigger)
        """
        delta = time.time() - self.timestamp - expected_delay
        if delta > config.max_acceptable_time_delta:
            logging.info(
                "relatively big time delta observed. interval: %s cooldown_counter: %s expected delay: %s max acceptable delta: %s delta: %s time: %s frozen pids: %s.  (this message is to be expected every now and then as the max acceptable delta parameter is autotuned)"
                % (
                    config.interval,
                    self.cooldown_counter,
                    expected_delay,
                    config.max_acceptable_time_delta,
                    delta,
                    time.time(),
                    get_all_frozen_pids(),
                )
            )
            self.cooldown_counter += 2
            self.timer_alert = True
            return False
        return True


class ProcessSelector:
    """Base class for process selector classes.

    Those classes have two methods, scan() which will search for a
    suitable process to suspend, and update() to update state in the
    object, if needed.  scan is required
    """

    def scan(self) -> tuple[int, ...] | None:
        raise NotImplementedError()

    def update(self, prev: SystemState, curr: SystemState) -> None:
        pass

    @staticmethod
    def _is_kernel_thread(pid: int, stats: ProcessSelector.procstat) -> bool:
        """Kernel threads have kthreadd (pid 2) as parent, or are kthreadd itself."""
        return pid == 2 or stats.ppid == 2

    @staticmethod
    def _is_frozen(pid: int, stats: ProcessSelector.procstat) -> bool:
        """Check if a process is already frozen (SIGSTOP or cgroup freeze).

        Cgroup-frozen processes don't show state "T" in /proc/pid/stat,
        so we also check if the process's cgroup is in frozen_cgroup_paths.
        """
        if "T" in stats.state:
            return True
        if _tp.frozen_cgroup_paths:
            cgroup_path = get_cgroup_path(pid)
            if cgroup_path in _tp.frozen_cgroup_paths:
                return True
        return False

    procstat = namedtuple("procstat", ("cmd", "state", "majflt", "ppid"))

    def readStat(self, sfn: int | str) -> ProcessSelector.procstat | None:
        try:
            return self.readStat_(sfn)
        except (FileNotFoundError, ProcessLookupError):
            return None

    def readStat_(self, sfn: int | str) -> ProcessSelector.procstat:
        """
        helper method - reads the stats file and returns a tuple (cmd, state,
        majflt, pids)
        """
        if isinstance(sfn, int):
            sfn = "/proc/%s/stat" % sfn
        with open(sfn, "rb") as stat_file:
            stats = []
            stats_tx = stat_file.read().decode("utf-8", "ignore")
            stats_tx = stats_tx.split("(", 1)
            stats.append(stats_tx[0])
            stats_tx = stats_tx[1].rsplit(")", 1)
            stats.append(stats_tx[0])
            stats.extend(stats_tx[1].split(" ")[1:])
        return self.procstat(stats[1], stats[2], int(stats[11]), int(stats[3]))

    def checkParents(self, pid: int, ppid: int | None = None) -> tuple[int, ...]:
        """
        helper method - find a list of pids that should be suspended, given
        a pid (and for optimalization reasons, ppid if it's already
        known).

        If a process running under an interactive bash session gets
        suspended, the bash job control kicks in and causes havoc.
        Hence, we should check if the cmd of the parent process is
        'bash'.
        """
        if ppid is None:
            stats = self.readStat(pid)
            if not stats:
                return ()
            ppid = stats.ppid
        if ppid <= 1:
            return (pid,)
        pstats = self.readStat(ppid)
        # Strip leading '-' from login shells (e.g., '-bash' -> 'bash')
        cmd_to_check = pstats.cmd.lstrip("-") if pstats else None
        if pstats and cmd_to_check in config.cmd_jobctrllist:
            return self.checkParents(ppid, pstats.ppid) + (pid,)
        else:
            return (pid,)


class OOMScoreProcessSelector(ProcessSelector):
    """
    Class containing one method for selecting a process to freeze,
    based on oom_score.  No stored state required.
    """

    def scan(self) -> tuple[int, ...] | None:
        oom_scores = glob.glob("/proc/*/oom_score")
        max = 0
        worstpid = None
        for fn in oom_scores:
            try:
                pid = int(fn.split("/")[2])
            except ValueError:
                continue
            try:
                with open(fn) as oom_score_file:
                    oom_score = int(oom_score_file.readline())
                stats = self.readStat(pid)
                if not stats:
                    continue
                if self._is_kernel_thread(pid, stats):
                    continue
                if self._is_frozen(pid, stats):
                    logging.debug(
                        "oom_score: %s, cmd: %s, pid: %s, state: %s - no touch"
                        % (oom_score, stats.cmd, pid, stats.state)
                    )
                    continue
            except FileNotFoundError:
                continue
            if oom_score > 0:
                logging.debug("oom_score: %s, cmd: %s, pid: %s" % (oom_score, stats.cmd, pid))
                oom_score = apply_score_adjustments(oom_score, stats.cmd)
                if oom_score > max:
                    ## ignore self
                    if pid in (getpid(), getppid()):
                        continue
                    max = oom_score
                    worstpid = (pid, stats.ppid)
        logging.debug("oom scan completed - selected pid: %s" % (worstpid and worstpid[0]))
        if worstpid is not None:
            if diagnostic_log:
                diagnostic_log(f"OOMScoreProcessSelector: pid={worstpid[0]}, oom_score={max}")
            return self.checkParents(*worstpid)
        else:
            return None


class LastFrozenProcessSelector(ProcessSelector):
    """Class containing one method for selecting a process to freeze,
    simply refreezing the last unfrozen process.  The rationale is
    that if a process was just resumed and the system start thrashing
    again, it would probably be smart to freeze that process again -
    and it's also a very cheap operation to do.

    If refreezing the last unfrozen process helps, then we're good -
    though it may potentially a problem that the same process is
    selected all the time.
    """

    def __init__(self) -> None:
        self.last_unfrozen_pid: tuple[int, ...] | list[int] | None = None

    def update(self, prev: SystemState, cur: SystemState) -> None:
        if cur.unfrozen_pid:
            self.last_unfrozen_pid = cur.unfrozen_pid

    def scan(self) -> tuple[int, ...] | list[int] | None:
        """
        If a process was just resumed and the system start thrashing again, it would probably be smart to freeze that process again.  This is also a very cheap operation
        """
        logging.debug("last unfrozen_pid is %s" % self.last_unfrozen_pid)
        if self.last_unfrozen_pid in get_all_frozen_pids():
            logging.debug("last unfrozen_pid is already frozen")
            return None
        logging.debug("last unfrozen process return - selected pid: %s" % self.last_unfrozen_pid)

        ## it may have exited already, in that case we should purge the record
        if self.last_unfrozen_pid and not [True for x in self.last_unfrozen_pid if self.readStat(x)]:
            self.last_unfrozen_pid = None

        return self.last_unfrozen_pid


class BlacklistProcessSelector(ProcessSelector):
    """Selects a blacklisted process that is alive and not currently frozen.

    When a process has been identified as a repeat offender (unfrozen then
    immediately causes re-thrashing), this selector prefers it as a freeze
    target so it gets frozen quickly without waiting for expensive scoring.
    """

    def __init__(self, blacklist: FreezeBlacklist) -> None:
        self.blacklist: FreezeBlacklist = blacklist

    def scan(self) -> tuple[int, ...] | None:
        for pids in list(self.blacklist.entries.keys()):
            # Check if any PID in the tuple is alive
            alive = False
            for pid in pids:
                stats = self.readStat(pid)
                if stats is not None:
                    alive = True
                    # Skip if already frozen
                    if self._is_frozen(pid, stats):
                        alive = False
                        break
            if alive:
                logging.debug("blacklist selector found alive unfrozen pids %s" % (pids,))
                return pids
        return None

    def update(self, prev: SystemState, cur: SystemState) -> None:
        pass


class CgroupPressureProcessSelector(ProcessSelector):
    """
    Selects a process from the cgroup with highest memory pressure.

    Uses per-cgroup memory.pressure PSI metrics to find which cgroup
    is causing the most memory stalls, then selects a process from
    that cgroup to freeze.

    This is more targeted than OOM scores because it identifies the
    actual source of memory pressure rather than just memory usage.
    """

    def __init__(self) -> None:
        self.cgroup_pressure_cache: dict[str, tuple[float, float]] = {}  # cgroup_path -> (timestamp, pressure)
        self.cache_ttl: float = 1.0  # Cache PSI readings for 1 second

    def get_cgroup_pressure(self, cgroup_path: str) -> float | None:
        """Get memory pressure for a cgroup, with caching."""
        now = time.time()

        # Check cache
        if cgroup_path in self.cgroup_pressure_cache:
            cached_time, cached_pressure = self.cgroup_pressure_cache[cgroup_path]
            if now - cached_time < self.cache_ttl:
                return cached_pressure

        # Read pressure from cgroup
        pressure_file = os.path.join(cgroup_path, "memory.pressure")
        try:
            with open(pressure_file) as f:
                for line in f:
                    if line.startswith("some "):
                        # Parse: some avg10=X.XX avg60=X.XX avg300=X.XX total=XXX
                        parts = line.strip().split()
                        for part in parts[1:]:
                            if part.startswith("avg10="):
                                pressure = float(part[6:])
                                self.cgroup_pressure_cache[cgroup_path] = (now, pressure)
                                return pressure
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            pass

        return None

    def scan(self) -> tuple[int, ...] | None:
        """Find process in cgroup with highest memory pressure, weighted by OOM score.

        Combines cgroup pressure with per-process oom_score to avoid bias toward
        large aggregate cgroups (e.g., session-1.scope with 124 processes) over
        smaller cgroups with individual high-memory processes.
        """
        if not is_psi_available():
            return None

        max_score = 0
        worst_pid = None
        worst_cgroup = None

        # Scan all processes
        for stat_file in glob.glob("/proc/*/stat"):
            try:
                pid = int(stat_file.split("/")[2])
            except ValueError:
                continue

            # Skip self
            if pid in (getpid(), getppid()):
                continue

            # Get process stats
            stats = self.readStat(pid)
            if not stats:
                continue

            # Skip kernel threads
            if self._is_kernel_thread(pid, stats):
                continue

            # Skip already frozen
            if self._is_frozen(pid, stats):
                continue

            # Get cgroup path
            cgroup_path = get_cgroup_path(pid)
            if not cgroup_path:
                continue

            # Get cgroup pressure
            pressure = self.get_cgroup_pressure(cgroup_path)
            if pressure is None:
                continue

            # Read per-process OOM score to weight the cgroup pressure.
            # This prevents large session cgroups (many processes, high aggregate
            # pressure) from always winning over smaller cgroups with individual
            # high-memory processes.
            try:
                with open(f"/proc/{pid}/oom_score") as f:
                    oom_score = int(f.readline())
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                continue

            # Combined score: pressure * oom_score
            score = pressure * max(oom_score, 1)

            # Apply whitelist/blacklist score adjustments
            score = apply_score_adjustments(score, stats.cmd)

            if score > max_score:
                max_score = score
                worst_pid = (pid, stats.ppid)
                worst_cgroup = cgroup_path

        if worst_pid and max_score > 0:
            logging.debug(
                f"cgroup pressure scan - selected pid: {worst_pid[0]}, "
                f"cgroup: {worst_cgroup}, combined score: {max_score}"
            )
            if diagnostic_log:
                diagnostic_log(
                    f"CgroupPressureProcessSelector: pid={worst_pid[0]}, "
                    f"cgroup={worst_cgroup}, combined_score={max_score}"
                )
            return self.checkParents(*worst_pid)

        return None


class PageFaultingProcessSelector(ProcessSelector):
    """
    Selects the process that have had most page faults since previous
    run.  This method have two problems; it is relatively expensive in
    terms of memory usage since it needs to keep counts of the page
    faults for every process, secondly, "page fault" is not equivalent
    with "swap".  (When a process is started, loading the program code
    into memory is usually postponed - when pages that aren't loaded
    yet are needed, it's also a "page fault")
    """

    def __init__(self) -> None:
        ## TODO: garbage collection
        self.pagefault_by_pid: dict[int, int] = {}
        self.cooldown_counter: int = 0

    def update(self, prev: SystemState, cur: SystemState) -> None:
        self.cooldown_counter = cur.cooldown_counter
        if cur.pagefaults - prev.pagefaults > config.pgmajfault_scan_threshold:
            ## If we've had a lot of major page faults, refresh our state
            ## on major page faults.
            self.scan()

    def scan(self) -> tuple[int, ...] | None:
        ## TODO: garbage collection
        stat_files = glob.glob("/proc/*/stat")
        max = 0
        worstpid = None
        for fn in stat_files:
            try:
                pid = int(fn.split("/")[2])
            except ValueError:
                continue
            stats = self.readStat(fn)
            if not stats:
                continue
            if self._is_kernel_thread(pid, stats):
                continue
            if stats.majflt > 0 and not self._is_frozen(pid, stats):
                prev = self.pagefault_by_pid.get(pid, 0)
                self.pagefault_by_pid[pid] = stats.majflt
                diff = stats.majflt - prev
                if config.test_mode:
                    diff += random.getrandbits(3)
                if not diff:
                    continue
                diff = apply_score_adjustments(diff, stats.cmd)
                if diff > max:
                    ## ignore self
                    if pid == getpid():
                        continue
                    max = diff
                    worstpid = (pid, stats.ppid)
                logging.debug("pagefault score: %s, cmd: %s, pid: %s" % (diff, stats.cmd, pid))
        logging.debug("pagefault scan completed - selected pid: %s" % (worstpid and worstpid[0]))
        ## give a bit of protection against whitelisted and innocent processes being stopped
        ## (TODO: hardcoded constants)
        if max > 4.0 / (self.cooldown_counter + 1.0):
            return self.checkParents(*worstpid)


class GlobalProcessSelector(ProcessSelector):
    """
    This is a collection of the various process selectors.
    """

    def __init__(self, blacklist: FreezeBlacklist | None = None) -> None:
        ## Sets up a prioritized list of selectors:
        ## * LastFrozenProcessSelector is the cheapest and it's surely
        ##   smart to be quick on refreezing a recently unfrozen process
        ##   if unfreezing it causes immediate swap problems.  So it's
        ##   the first method.
        ## * BlacklistProcessSelector targets known repeat offenders that
        ##   were previously identified by LastFrozenProcessSelector.
        ## * CgroupPressureProcessSelector is the best at targeting but
        ##   slightly more expensive
        ## * OOMScoreProcessSelector is cheaper than
        ##   PageFaultingProcessSelector()
        ## * PageFaultingProcessSelector() is expensive and gives many false
        ##   positives, so it's last.
        ## I'm not sure if we need all of those.  Perhaps it would make sense
        ## to shed some of the older obsoleted selecting methods (TODO).
        self.blacklist: FreezeBlacklist | None = blacklist
        selectors: list[ProcessSelector] = [
            LastFrozenProcessSelector(),
        ]
        if blacklist is not None:
            selectors.append(BlacklistProcessSelector(blacklist))
        selectors.extend(
            [
                CgroupPressureProcessSelector(),
                OOMScoreProcessSelector(),
                PageFaultingProcessSelector(),
            ]
        )
        self.collection: list[ProcessSelector] = selectors
        self.scan_method_count: int = 0

    def update(self, prev: SystemState, cur: SystemState) -> None:
        if cur.unfrozen_pid:
            self.scan_method_count = 0
        for c in self.collection:
            c.update(prev, cur)

    def scan(self) -> tuple[int, ...] | None:
        logging.debug("scan_processes")

        ## a for loop here to make sure we fall back on the next method if the first method fails to find anything.
        for i in range(0, len(self.collection)):
            selector = self.collection[self.scan_method_count % len(self.collection)]
            logging.debug("scan method: %s" % (self.scan_method_count % len(self.collection)))
            ret = selector.scan()
            self.scan_method_count += 1
            if ret:
                # When LastFrozenProcessSelector fires, it means the last
                # unfrozen process is being re-selected because thrashing
                # resumed immediately.  Record this as a repeat offender.
                if isinstance(selector, LastFrozenProcessSelector) and self.blacklist is not None:
                    self.blacklist.add(ret)
                if diagnostic_log:
                    diagnostic_log(
                        f"selected pids {ret} via {type(selector).__name__} (method #{self.scan_method_count - 1})"
                    )
                return ret

    logging.debug("found nothing to stop!? :-(")


def get_date_string() -> str:
    if config.date_human_readable:
        now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S") + f".{now.microsecond // 1000:03d}"
    else:
        return str(time.time())


## returns string with detailed process information
def get_process_info(pid: int) -> str:
    try:
        ## TODO: we should fetch this information from /proc filesystem instead of using ps
        info = check_output(["ps", "-p", str(pid), "uf"]).decode("utf-8", "ignore")
        info = info.split("\n")[1]
        info = info.split()
        if len(info) >= 4:
            return "u:%10s  CPU:%5s%%  MEM:%5s%%  CMD: %s" % (info[0], info[2], info[3], " ".join(info[10:]))
        else:
            return "No information available, the process was probably killed or 'ps' returns unexpected output."
    except Exception:
        logging.error("Could not fetch process user information, the process is probably gone")
        return "problem fetching process information"


def ignore_failure(method: Callable[..., Any]) -> Callable[..., None]:
    def _try_except_pass(*args: Any, **kwargs: Any) -> None:
        try:
            method(*args, **kwargs)
        except Exception:
            logging.critical("Exception ignored", exc_info=True)

    return _try_except_pass


## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: advanced logging


FROZEN_PID_FILE = "/tmp/thrash-protect-frozen-pid-list"
FROZEN_CGROUP_FILE = "/tmp/thrash-protect-frozen-cgroup-list"
LOG_FILE = "/var/log/thrash-protect.log"


def _write_log_entry(action: str, pid: int, log_user_data: bool, all_frozen: list[tuple[int, ...]]) -> None:
    """Write a log entry to the thrash-protect log file."""
    with open(LOG_FILE, "ab") as logfile:
        if log_user_data:
            logfile.write(
                (
                    "%s - %s   pid %5s - %s - list: %s\n"
                    % (get_date_string(), action, str(pid), get_process_info(pid), all_frozen)
                ).encode("utf-8")
            )
        else:
            if all_frozen:
                logfile.write(
                    ("%s - %s pid %s - frozen list: %s\n" % (get_date_string(), action, pid, all_frozen)).encode(
                        "utf-8"
                    )
                )
            else:
                logfile.write(("%s - %s pid %s\n" % (get_date_string(), action, pid)).encode("utf-8"))


def _update_frozen_pid_file(all_frozen: list[tuple[int, ...]]) -> None:
    """Update or remove the frozen PID list file and cgroup list file."""
    if all_frozen:
        with open(FROZEN_PID_FILE, "w") as f:
            f.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in all_frozen]) + "\n")
    else:
        try:
            unlink(FROZEN_PID_FILE)
        except (FileNotFoundError, OSError):
            pass
    # Also persist frozen cgroup paths for crash recovery
    if _tp.frozen_cgroup_paths:
        with open(FROZEN_CGROUP_FILE, "w") as f:
            for path in _tp.frozen_cgroup_paths:
                f.write(path + "\n")
    else:
        try:
            unlink(FROZEN_CGROUP_FILE)
        except (FileNotFoundError, OSError):
            pass


# Intentionally has no @ignore_failure — every frozen PID must be logged
# and persisted for crash recovery.  Operations are lightweight local file
# writes, so failure indicates a serious problem.
def log_frozen(pid: int) -> None:
    all_frozen = get_all_frozen_pids()
    _write_log_entry("frozen", pid, config.log_user_data_on_freeze, all_frozen)
    _update_frozen_pid_file(all_frozen)


# Uses @ignore_failure because it gathers extra process info (get_process_info)
# that may fail for exited processes.  Failing to log an unfreeze is tolerable
# since the process is already unfrozen.
@ignore_failure
def log_unfrozen(pid: int) -> None:
    all_frozen = get_all_frozen_pids()
    _write_log_entry("unfrozen", pid, config.log_user_data_on_unfreeze, all_frozen)
    _update_frozen_pid_file(all_frozen)


def _debug_check_state(pid: int, should_be_suspended: bool = False) -> None:
    procstate = ProcessSelector().readStat(pid)
    if not procstate and not should_be_suspended:
        return
    if not procstate:
        logging.warn("Pid %s should be suspended, but is gone" % pid)
        return
    is_suspended = "T" in procstate.state
    if is_suspended != should_be_suspended:
        logging.warn("Pid %s - state: %s, should_be_suspended: %s - mismatch" % (pid, procstate, should_be_suspended))


# debug_check_state is set up by init_config() based on debug_checkstate setting
debug_check_state = lambda a, b: None


def _diagnostic_log(msg: str) -> None:
    """Log diagnostic information at INFO level (only called when --diagnostic is enabled)."""
    logging.info("DIAGNOSTIC: %s" % msg)


# diagnostic_log is set up by init_config() based on diagnostic_logging setting.
# When disabled, set to None so `if diagnostic_log:` guards skip string formatting.
diagnostic_log = None


class _BlacklistEntry:
    """A single entry in the freeze blacklist."""

    __slots__ = ("last_refreshed", "skip_remaining", "allowance")

    def __init__(self, now: float, max_skip_count: int) -> None:
        self.last_refreshed: float = now
        self.skip_remaining: int = max_skip_count
        ## The allowance this offender has earned.  Grows on re-offence; the
        ## entry is discarded wholesale by expire(), so behaving for a full
        ## expiry window is what resets it.
        self.allowance: int = max_skip_count


class FreezeBlacklist:
    """Tracks processes that cause immediate re-thrashing after unfreeze.

    When a process is unfrozen and immediately causes thrashing again,
    it gets blacklisted. Blacklisted processes:
    1. Are preferred targets for freezing (via BlacklistProcessSelector)
    2. Stay frozen longer (skipped during unfreeze cycles)
    """

    def __init__(self, expiry_time: float = 60.0, max_skip_count: int = 3, escalation_cap: int = 8) -> None:
        self.entries: dict[tuple[int, ...], _BlacklistEntry] = {}
        self.expiry_time: float = expiry_time
        self.max_skip_count: int = max_skip_count
        ## Below 1 the cap would sit under the base allowance, so a re-offender
        ## would come out with a *shorter* hold than a first offender.
        self.escalation_cap: int = max(1, escalation_cap)

    def add(self, pids: tuple[int, ...] | list[int]) -> None:
        """Add a blacklist entry, or escalate an existing one.

        A first offence gets max_skip_count.  Each re-offence adds another
        max_skip_count to the allowance, capped at max_skip_count *
        escalation_cap, so a process that keeps re-thrashing on resume stays
        suspended longer each time round rather than getting the same short
        hold forever.  Escalation survives partial consumption of the current
        allowance; only expire() takes it away.
        """
        pids = tuple(pids)
        now = time.time()
        entry = self.entries.get(pids)
        if entry is None:
            self.entries[pids] = _BlacklistEntry(now, self.max_skip_count)
            logging.debug("blacklisted pids %s (skip_remaining=%d)" % (pids, self.max_skip_count))
            return
        cap = self.max_skip_count * self.escalation_cap
        entry.allowance = min(entry.allowance + self.max_skip_count, cap)
        entry.skip_remaining = entry.allowance
        entry.last_refreshed = now
        logging.debug("blacklist escalated for pids %s (skip_remaining=%d)" % (pids, entry.skip_remaining))

    def is_blacklisted(self, pids: tuple[int, ...] | list[int]) -> bool:
        """Check if a PID tuple is currently blacklisted."""
        return tuple(pids) in self.entries

    def should_skip_unfreeze(self, pids: tuple[int, ...] | list[int]) -> bool:
        """Check if this item should be skipped during unfreeze.

        If blacklisted and skip_remaining > 0, decrements and returns True.
        Otherwise returns False.
        """
        entry = self.entries.get(tuple(pids))
        if entry is None:
            return False
        if entry.skip_remaining > 0:
            entry.skip_remaining -= 1
            logging.debug("skipping unfreeze of blacklisted pids %s (skip_remaining=%d)" % (pids, entry.skip_remaining))
            return True
        return False

    def expire(self) -> None:
        """Remove entries older than expiry_time."""
        now = time.time()
        expired = [pids for pids, entry in self.entries.items() if now - entry.last_refreshed > self.expiry_time]
        for pids in expired:
            del self.entries[pids]
            logging.debug("blacklist entry expired for pids %s" % (pids,))

    def clear(self) -> None:
        """Remove all entries."""
        self.entries.clear()


class ThrashProtectState:
    """Encapsulates the runtime state of thrash-protect.

    Holds frozen process tracking, process selectors, and the OOM predictor.
    Provides freeze/unfreeze/cleanup methods that operate on this state.
    """

    def __init__(self) -> None:
        self.frozen_items: list[tuple[str, ...]] = []
        ## Ticks remaining before an OOM-driven freeze may be released.  See
        ## note_oom_freeze() for why the swap-based cooldown cannot serve here.
        self.oom_hold_counter: int = 0
        self.frozen_cgroup_paths: set[str] = set()
        self.num_unfreezes: int = 0
        self.freeze_blacklist: FreezeBlacklist = FreezeBlacklist()
        self.process_selector: GlobalProcessSelector = GlobalProcessSelector(blacklist=self.freeze_blacklist)
        self.memory_predictor: MemoryExhaustionPredictor | None = None
        # Lazily cached cgroup path for our own process.
        # Sentinel "_unset" distinguishes "not computed" from None (no cgroup).
        self._own_cgroup_path: str | None = "_unset"

    def reset(self) -> None:
        """Reset all state (useful for testing)."""
        self.frozen_items = []
        self.oom_hold_counter = 0
        self.frozen_cgroup_paths.clear()
        self.num_unfreezes = 0
        self.freeze_blacklist = FreezeBlacklist()
        self.process_selector = GlobalProcessSelector(blacklist=self.freeze_blacklist)
        self.memory_predictor = None
        self._own_cgroup_path = "_unset"

    def get_all_frozen_pids(self) -> list[tuple[int, ...]]:
        """Get combined list of all frozen pids (both SIGSTOP and cgroup frozen)."""
        return [unpack_frozen_item(item)[2] for item in self.frozen_items]

    def note_oom_freeze(self) -> None:
        """Arm the hold that stops an OOM-driven freeze being released at once.

        The unfreeze criterion in the main loop is `cooldown_counter`, which is
        written by check_swap_threshold from swap traffic and bumped by
        check_delay() on a timer alert.  Neither measures memory headroom, so a
        freeze ordered by the OOM predictor has nothing holding it: when swap is
        full there is no swap traffic to count, the counter is already zero, and
        the next tick resumes the process "because the box is not thrashing" -
        which is precisely the situation it was frozen for.

        The hold is global, not per-process: while it is armed *nothing* is
        resumed, including items suspended earlier by the swap detector.  A
        prediction says the whole box is in trouble, so that is the intended
        reading, but it is deliberately modest - it is not an attempt to decide
        how long any one process should stay down.  A repeat offender earns a
        longer hold through FreezeBlacklist escalation instead.

        A negative `oom_hold_ticks` would make `may_unfreeze()` false for ever
        and send the counter runaway-negative, so it is clamped here rather than
        trusted; 0 disables the hold.
        """
        self.oom_hold_counter = max(0, config.oom_hold_ticks)

    def tick_oom_hold(self) -> None:
        """Age the OOM hold by one tick.  Call once per loop iteration."""
        if self.oom_hold_counter > 0:
            self.oom_hold_counter -= 1

    def may_unfreeze(self, cooldown_counter: int) -> bool:
        """Whether anything may be resumed this tick.

        Both criteria have to agree: no thrashing (the swap-based cooldown) and
        no OOM hold outstanding.
        """
        return not cooldown_counter and not self.oom_hold_counter

    def freeze_something(self, pids_to_freeze: tuple[int, ...] | list[int] | int | None = None) -> tuple[int, ...]:
        pids_to_freeze = normalize_pids(pids_to_freeze or self.process_selector.scan())
        if not pids_to_freeze:
            ## process disappeared. ignore failure
            logging.info("nothing to freeze found, or the process we were going to suspend has already exited")
            return ()
        if getpid() in pids_to_freeze:
            logging.error("Oups.  Own pid is next on the list of processes to freeze.  This is very bad.  Skipping.")
            return ()

        # Check if any process in the chain should use cgroup freezing
        # (for tmux/screen sessions where SIGSTOP doesn't work properly)
        cgroup_path = None
        for pid in pids_to_freeze:
            cgroup_path = should_use_cgroup_freeze(pid)
            if cgroup_path:
                break

        # Prevent self-freezing deadlock: if the target cgroup contains
        # thrash-protect itself, fall back to SIGSTOP for the individual process.
        if cgroup_path and cgroup_path == get_own_cgroup_path():
            logging.warning(
                "target cgroup %s contains thrash-protect's own process, falling back to SIGSTOP" % cgroup_path
            )
            cgroup_path = None

        if cgroup_path and freeze_cgroup(cgroup_path):
            # Cgroup freezing succeeded - freezes all processes atomically
            # Check if already frozen (avoid duplicates) - keyed on cgroup_path
            if not any(item[0] == "cgroup" and item[1] == cgroup_path for item in self.frozen_items):
                self.frozen_items.append(("cgroup", cgroup_path, pids_to_freeze))
            self.frozen_cgroup_paths.add(cgroup_path)
            for pid_to_freeze in pids_to_freeze:
                logging.debug("froze pid %s (via cgroup)" % str(pid_to_freeze))
                log_frozen(pid_to_freeze)
            return pids_to_freeze

        # Use SIGSTOP (original behavior)
        for pid_to_freeze in pids_to_freeze:
            try:
                debug_check_state(pid_to_freeze, 0)
                kill(pid_to_freeze, signal.SIGSTOP)
                if len(pids_to_freeze) > 1:
                    time.sleep(config.max_acceptable_time_delta / 3)
            except ProcessLookupError:
                continue
        # Check if already frozen (avoid duplicates)
        if not any(item[0] == "sigstop" and item[1] == pids_to_freeze for item in self.frozen_items):
            self.frozen_items.append(("sigstop", pids_to_freeze))

        for pid_to_freeze in pids_to_freeze:
            ## Logging after freezing - as logging itself may be resource- and timeconsuming.
            ## Perhaps we should even fork it out.
            logging.debug("froze pid %s" % str(pid_to_freeze))
            log_frozen(pid_to_freeze)
        return pids_to_freeze

    def _pick_unfreeze_item(self) -> tuple[tuple[str, ...], int] | None:
        """Pick the next item to unfreeze, skipping blacklisted items.

        Returns (item, pop_index) or None if nothing to unfreeze.
        Blacklisted items with remaining skip budget are put back at the
        opposite end of the list.  If all items are blacklisted, the first
        candidate is unfrozen anyway to avoid deadlock.
        """
        if not self.frozen_items:
            return None

        if self.num_unfreezes % config.unfreeze_pop_ratio:
            pop_index = -1
        else:
            pop_index = 0

        first_skipped = None
        tried = 0

        while tried < len(self.frozen_items):
            item = self.frozen_items.pop(pop_index)
            tried += 1
            _, _, pids = unpack_frozen_item(item)
            pids = tuple(normalize_pids(pids))

            if self.freeze_blacklist.should_skip_unfreeze(pids):
                if first_skipped is None:
                    first_skipped = item
                # Put back at opposite end
                if pop_index == -1:
                    self.frozen_items.insert(0, item)
                else:
                    self.frozen_items.append(item)
                continue

            return item, pop_index

        # All items were skipped — unfreeze the first one we skipped (deadlock guard)
        if first_skipped is not None:
            # It's already been re-inserted; remove and return it
            self.frozen_items.remove(first_skipped)
            logging.debug("all frozen items blacklisted, force-unfreezing one to avoid deadlock")
            return first_skipped, pop_index

        return None

    def unfreeze_something(self) -> list[int] | None:
        pick = self._pick_unfreeze_item()
        if pick is None:
            return None

        item, pop_index = pick
        item_type, cgroup_path, pids_to_unfreeze = unpack_frozen_item(item)
        pids_to_unfreeze = list(normalize_pids(pids_to_unfreeze))

        if cgroup_path:
            # Unfreeze via cgroup
            logging.debug("pids to unfreeze (via cgroup): %s" % pids_to_unfreeze)
            if not unfreeze_cgroup(cgroup_path):
                logging.warning("failed to unfreeze cgroup %s, re-inserting" % cgroup_path)
                self.frozen_items.insert(pop_index if pop_index == 0 else len(self.frozen_items), item)
                return None
            self.frozen_cgroup_paths.discard(cgroup_path)
        else:
            # Unfreeze via SIGCONT
            logging.debug("pids to unfreeze: %s" % pids_to_unfreeze)
            for pid_to_unfreeze in reversed(pids_to_unfreeze):
                try:
                    logging.debug("going to unfreeze %s" % str(pid_to_unfreeze))
                    debug_check_state(pid_to_unfreeze, 1)
                    kill(pid_to_unfreeze, signal.SIGCONT)
                    if len(pids_to_unfreeze) > 1:
                        time.sleep(config.max_acceptable_time_delta)
                except ProcessLookupError:
                    ## ignore failure
                    pass

        for pid_to_unfreeze in pids_to_unfreeze:
            log_unfrozen(pid_to_unfreeze)

        self.num_unfreezes += 1
        return pids_to_unfreeze

    def cleanup(self) -> None:
        """Clean up if exiting due to an exception."""
        self.oom_hold_counter = 0
        self.freeze_blacklist.clear()
        self.frozen_cgroup_paths.clear()
        for item in self.frozen_items:
            item_type, cgroup_path, pids = unpack_frozen_item(item)
            if item_type == "cgroup":
                unfreeze_cgroup(cgroup_path)
            else:  # sigstop
                for pid_to_unfreeze in reversed(normalize_pids(pids)):
                    try:
                        kill(pid_to_unfreeze, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
        try:
            unlink(FROZEN_PID_FILE)
        except FileNotFoundError:
            pass
        try:
            unlink(FROZEN_CGROUP_FILE)
        except FileNotFoundError:
            pass

    def run(self, args: argparse.Namespace | None = None) -> None:
        """Main thrash-protect loop."""
        current = SystemState()

        ## A best-effort attempt on running mlockall()
        try:
            import ctypes

            try:
                assert not ctypes.cdll.LoadLibrary("libc.so.6").mlockall(ctypes.c_int(7))
            except Exception:
                assert not ctypes.cdll.LoadLibrary("libc.so.6").mlockall(ctypes.c_int(3))
        except Exception:
            logging.warning(
                "failed to do mlockall() - this makes the program vulnerable of being swapped out in an extreme thrashing event (maybe you're not running the script as root?)",
                exc_info=False,
            )

        while True:
            prev = current
            current = SystemState()
            busy = current.check_thrashing(prev)

            ## Check OOM prediction (proactive memory exhaustion protection)
            oom_predicted = False
            if self.memory_predictor and not busy:
                oom_predicted = self.memory_predictor.should_freeze()

            ## If we're thrashing or OOM is predicted, then freeze something.
            if busy or oom_predicted:
                froze = self.freeze_something()
                if oom_predicted:
                    ## Hold it down for a few ticks - the swap-based cooldown
                    ## counter says nothing about memory exhaustion and would
                    ## release it on the very next tick.  Only if we actually
                    ## suspended something: arming on a prediction that found
                    ## nothing to freeze would block unrelated releases for
                    ## nothing.
                    if froze:
                        self.note_oom_freeze()
                    # Old observations reflect pre-freeze memory trends and
                    # would immediately re-trigger.  Reset so the short scale
                    # can quickly assess whether the freeze helped.
                    self.memory_predictor.reset()
            elif self.may_unfreeze(current.cooldown_counter):
                ## If no swapping has been observed for a while and no OOM
                ## freeze is still being held, then unfreeze something.
                current.unfrozen_pid = self.unfreeze_something()

            ## Age the hold only on a tick where releasing was actually on the
            ## table.  `oom_predicted` is only computed when `busy` is false, so
            ## aging on every non-predicting tick would let thrashing ticks -
            ## on which nothing could be released anyway - eat the whole hold.
            if not busy and not oom_predicted:
                self.tick_oom_hold()

            ## Blacklist bookkeeping, once per iteration.  It used to live in
            ## _pick_unfreeze_item(), which the unfreeze gate above can now keep
            ## us out of for a whole storm - and then no entry would ever expire.
            self.freeze_blacklist.expire()

            self.process_selector.update(prev, current)

            if current.check_delay() and not busy:
                sleep_interval = current.get_sleep_interval()
                logging.debug("going to sleep %s" % sleep_interval)
                time.sleep(sleep_interval)
                current.check_delay(sleep_interval)


def unfreeze_from_tmpfile() -> None:
    """
    Cleanup - unfreezing pids from last run, if applicable

    this may arguably be harmful, if box has been rebooted, or long
    time has passed, and the pidfile actually contains processes that
    should be frozen.  At the other hand, if thrash-protect dies for
    any reason, and is instantly restarted by systemd, it's probably a
    good thing to start fresh from scratch.  (or maybe the system will
    go insta-thrashed, that would be quite bad indeed).
    """
    try:
        with open(FROZEN_PID_FILE) as pidfile:
            logging.info("cleaning up - unfreezing pids from last run")
            pids_to_open = pidfile.read()
            for pid in pids_to_open.split():
                try:
                    kill(int(pid), signal.SIGCONT)
                except (ProcessLookupError, ValueError):
                    pass
    except FileNotFoundError:
        pass
    # Also unfreeze any cgroups from a previous run
    try:
        with open(FROZEN_CGROUP_FILE) as cgfile:
            logging.info("cleaning up - unfreezing cgroups from last run")
            for line in cgfile:
                path = line.strip()
                if path:
                    unfreeze_cgroup(path)
    except FileNotFoundError:
        pass


## Module-level singleton instance
_tp = ThrashProtectState()

# Backward-compatible module-level aliases for globals.
# These are references to the mutable containers in _tp, so mutations
# (append, pop, add, discard) work through the alias. However,
# reassignment (e.g. `frozen_items = []`) replaces the module attribute
# only - use _tp.reset() or _tp.frozen_items = [] for that.
frozen_items = _tp.frozen_items
frozen_cgroup_paths = _tp.frozen_cgroup_paths
num_unfreezes = _tp.num_unfreezes
global_process_selector = _tp.process_selector
memory_predictor = _tp.memory_predictor


# Backward-compatible module-level functions that delegate to singleton
def get_all_frozen_pids() -> list[tuple[int, ...]]:
    return _tp.get_all_frozen_pids()


def freeze_something(pids_to_freeze: tuple[int, ...] | list[int] | int | None = None) -> tuple[int, ...]:
    return _tp.freeze_something(pids_to_freeze)


def unfreeze_something() -> list[int] | None:
    return _tp.unfreeze_something()


def cleanup() -> None:
    return _tp.cleanup()


def thrash_protect(args: argparse.Namespace | None = None) -> None:
    return _tp.run(args)


def main() -> None:
    """Main entry point for thrash-protect."""
    p = create_argument_parser()
    args = p.parse_args()

    # Initialize configuration from all sources (CLI > file > env > defaults)
    init_config(args)

    # Set up logging level
    if config.debug_logging:
        logging.root.setLevel(logging.DEBUG)
    elif config.diagnostic_logging:
        logging.root.setLevel(logging.INFO)

    unfreeze_from_tmpfile()

    try:
        thrash_protect(args)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
