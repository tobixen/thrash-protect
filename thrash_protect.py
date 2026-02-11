#!/usr/bin/python3

### Simple-Stupid user-space program protecting a linux host from thrashing.
### See the README for details.
### Project home: https://github.com/tobixen/thrash-protect

### This was a rapid prototype implementation.  I was considering to implement in C.
### While I have been considering this, Moore's Law has made it pretty moot.

try:
    from _version import __version__
except ImportError:
    __version__ = "0.0.0.dev"  # Fallback if _version.py not generated yet

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
from collections import namedtuple
from datetime import datetime
from os import getenv, getpid, getppid, kill, unlink
from subprocess import check_output

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
    # System
    "systemd-journal",
    "dbus-daemon",
    "kthreadd",
    "login",
    "supervisord",
]


def get_shells_from_etc():
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


def get_default_whitelist():
    """Static whitelist + all shells from /etc/shells."""
    shells = get_shells_from_etc()
    return list(set(STATIC_WHITELIST + shells))


def get_default_jobctrllist():
    """Shells from /etc/shells plus sudo."""
    shells = get_shells_from_etc()
    if "sudo" not in shells:
        shells.append("sudo")
    return shells


def _parse_bool(value):
    """Parse boolean from string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return str(value).lower() in ("true", "yes", "1", "on")


def _parse_list(value):
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
    "oom_horizon": (int, "THRASH_PROTECT_OOM_HORIZON", ["oom-horizon"]),
    "oom_swap_weight": (float, "THRASH_PROTECT_OOM_SWAP_WEIGHT", ["oom-swap-weight"]),
}


def load_from_file(path=None):
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


def _load_yaml(path):
    """Load YAML config file."""
    if not HAS_YAML:
        raise ImportError("PyYAML not installed - install with: pip install PyYAML")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data.get("thrash-protect", data)


def _load_toml(path):
    """Load TOML config file."""
    if not HAS_TOML:
        raise ImportError("TOML support not available - install tomli (Python <3.11) or use Python 3.11+")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data.get("thrash-protect", data)


def _load_json(path):
    """Load JSON config file."""
    with open(path) as f:
        data = json.load(f)
    return data.get("thrash-protect", data)


def _load_ini(path):
    """Load INI config file."""
    parser = configparser.ConfigParser()
    parser.read(path)
    if "thrash-protect" not in parser:
        return {}
    return dict(parser["thrash-protect"])


def load_from_env():
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


def get_defaults():
    """Get default configuration values."""
    return {
        "debug_logging": False,
        "debug_checkstate": False,
        "interval": 0.5,
        "swap_page_threshold": 4,
        "pgmajfault_scan_threshold": None,  # Computed from swap_page_threshold if not set
        "use_psi": True,  # Use PSI for thrash detection if available
        "psi_threshold": 5.0,  # Trigger when some avg10 exceeds this percentage
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
        "oom_horizon": 3600,
        "oom_swap_weight": None,  # Auto-set based on storage type
    }


def normalize_file_config(file_config):
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


def load_config(args):
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


def create_argument_parser():
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
        "--oom-horizon",
        dest="oom_horizon",
        type=int,
        metavar="SECONDS",
        help="Time horizon for OOM prediction in seconds (default: 3600)",
    )
    p.add_argument(
        "--oom-swap-weight",
        dest="oom_swap_weight",
        type=float,
        metavar="WEIGHT",
        help="Weight for swap in OOM prediction (default: auto based on storage type, SSD=2.0 HDD=4.0)",
    )

    return p


#########################
## Cgroup Freezing Support
#########################


def get_cgroup_path(pid):
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


def is_cgroup_freezable(cgroup_path):
    """Check if cgroup supports freezing."""
    if not cgroup_path:
        return False
    freeze_file = os.path.join(cgroup_path, "cgroup.freeze")
    return os.path.exists(freeze_file)


def freeze_cgroup(cgroup_path):
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


def unfreeze_cgroup(cgroup_path):
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


def should_use_cgroup_freeze(pid):
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


def normalize_pids(pids):
    """Normalize pids to a tuple.

    Handles single pids, tuples, lists, and other iterables consistently.
    """
    if pids is None:
        return ()
    if not hasattr(pids, "__iter__") or isinstance(pids, str):
        return (pids,)
    return tuple(pids)


def apply_score_adjustments(score, cmd):
    """Apply whitelist/blacklist score adjustments.

    Divides score for whitelisted commands, multiplies for blacklisted.
    Returns the adjusted score.
    """
    if cmd in config.cmd_whitelist:
        score /= config.whitelist_score_divider
    if cmd in config.cmd_blacklist:
        score *= config.blacklist_score_multiplier
    return score


def unpack_frozen_item(item):
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


def is_psi_available():
    """Check if PSI is available on this system (Linux 4.20+)."""
    global _psi_available
    if _psi_available is None:
        _psi_available = os.path.exists("/proc/pressure/memory")
    return _psi_available


def get_memory_pressure():
    """Read memory pressure from /proc/pressure/memory.

    Returns a dict with 'some' and 'full' pressure metrics, each containing
    avg10, avg60, avg300 (percentages) and total (microseconds).
    Returns None if PSI is not available.

    Example output:
        {'some': {'avg10': 0.0, 'avg60': 0.0, 'avg300': 0.0, 'total': 0},
         'full': {'avg10': 5.23, 'avg60': 2.10, 'avg300': 0.50, 'total': 123456}}
    """
    if not is_psi_available():
        return None
    try:
        pressure = {}
        with open("/proc/pressure/memory") as f:
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


#########################
## Swap Storage Detection
#########################


def detect_swap_storage_type():
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


def _get_device_rotational(device):
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


def read_meminfo():
    """Read MemAvailable and SwapFree from /proc/meminfo (in kB).

    Returns (mem_available_kb, swap_free_kb) or None if unavailable.
    """
    mem_available = None
    swap_free = None
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
                if mem_available is not None and swap_free is not None:
                    return (mem_available, swap_free)
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        pass
    return None


class MemoryExhaustionPredictor:
    """Predicts memory exhaustion using two-point linear projection.

    Tracks a weighted "available resources" metric:
        available = mem_available + swap_free * swap_weight

    Where swap_weight reflects that swap depletion is more dangerous
    (SSD: 2.0, HDD: 4.0).

    Uses two consecutive observations to project when resources will hit zero.
    If the projected time-to-exhaustion is less than oom_horizon seconds,
    triggers proactive freezing to prevent OOM kills.
    """

    def __init__(self, swap_weight=2.0, horizon=3600):
        self.swap_weight = swap_weight
        self.horizon = horizon
        self._prev_time = None
        self._prev_available = None

    def update_and_predict(self):
        """Read current memory state, project time to exhaustion.

        Returns estimated seconds until exhaustion, or None if:
        - Resources are not declining
        - Not enough observations yet
        - /proc/meminfo is unavailable
        """
        meminfo = read_meminfo()
        if meminfo is None:
            return None

        mem_available, swap_free = meminfo
        available = mem_available + swap_free * self.swap_weight
        now = time.time()

        if self._prev_time is None:
            # First observation - store and return
            self._prev_time = now
            self._prev_available = available
            return None

        prev_time = self._prev_time
        prev_available = self._prev_available

        # Update stored state for next call
        self._prev_time = now
        self._prev_available = available

        # Not declining -> no exhaustion predicted
        if available >= prev_available:
            return None

        # Two-point projection: when will available hit zero?
        dt = now - prev_time
        if dt <= 0:
            return None

        decline_rate = (prev_available - available) / dt  # kB/sec declining
        if decline_rate <= 0:
            return None

        eta = available / decline_rate
        return eta

    def should_freeze(self):
        """Check if proactive freezing should be triggered.

        Returns True if projected time to exhaustion is within horizon.
        """
        eta = self.update_and_predict()
        if eta is not None and eta < self.horizon:
            logging.info(
                "OOM protection: memory exhaustion predicted in %.0f seconds "
                "(horizon: %d seconds)" % (eta, self.horizon)
            )
            return True
        return False


class config:
    """
    Configuration namespace - populated at startup by init_config().

    Access configuration values as config.interval, config.cmd_whitelist, etc.
    """

    pass


def init_config(args=None):
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

    # Set all config values as attributes on the config class
    for key, value in cfg.items():
        setattr(config, key, value)

    # Set up memory exhaustion predictor on the singleton
    if config.oom_protection:
        _tp.memory_predictor = MemoryExhaustionPredictor(
            swap_weight=config.oom_swap_weight,
            horizon=config.oom_horizon,
        )
    else:
        _tp.memory_predictor = None

    # Set up debug_check_state function based on config
    global debug_check_state
    if config.debug_checkstate:
        debug_check_state = _debug_check_state
    else:
        debug_check_state = lambda a, b: None

    # Set up diagnostic_log function based on config
    # When disabled, set to None so `if diagnostic_log:` guards skip string formatting
    global diagnostic_log
    if config.diagnostic_logging:
        diagnostic_log = _diagnostic_log
    else:
        diagnostic_log = None


# Initialize with defaults immediately so the module can be imported
# (will be re-initialized in main() with proper args)
def _init_default_config():
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

    def __init__(self):
        self.timestamp = time.time()
        self.pagefaults = self.get_pagefaults()
        self.swapcount = self.get_swapcount()
        self.psi = get_memory_pressure()  # None if PSI not available
        self.cooldown_counter = 0
        self.unfrozen_pid = None
        self.timer_alert = False

    def get_pagefaults(self):
        with open("/proc/vmstat") as vmstat:
            line = ""
            while line is not None:
                line = vmstat.readline()
                if line.startswith("pgmajfault "):
                    return int(line[12:])

    def get_swapcount(self):
        ret = []
        with open("/proc/vmstat") as vmstat:
            line = True
            while line:
                line = vmstat.readline()
                if line.startswith("pswp"):
                    ret.append(int(line[7:]))
        return tuple(ret)

    def check_swap_threshold(self, prev):
        self.cooldown_counter = prev.cooldown_counter
        if config.test_mode and not random.getrandbits(config.test_mode):
            self.cooldown_counter = prev.cooldown_counter + 1
            return True

        ## will return True if we have bidirectional traffic to swap,
        ## or if we have a big one-directional flow of data.
        ##
        ## * if both swap counters are above the swap_page_threshold, trigger
        ##
        ## * if one of the swap counters is quite much above the
        ##   swap_page_threshold, while the other is 0, we should trigger
        ##
        ## the below algorithm seems to satisfy those two criterias, though
        ## I'm not much happy with the arbitrary constant "0.1" being thrown
        ## in.
        swap_product = ((self.swapcount[0] - prev.swapcount[0] + 0.1) / config.swap_page_threshold) * (
            (self.swapcount[1] - prev.swapcount[1] + 0.1) / config.swap_page_threshold
        )

        ## PSI weight: amplify swap signal when memory pressure is detected
        ## Uses "some" (at least one task stalled) rather than "full" (all CPUs stalled),
        ## because "full" can be near-zero even during heavy thrashing on multi-core systems.
        psi_weight = 1.0
        if config.use_psi and self.psi and "some" in self.psi:
            psi_some = self.psi["some"].get("avg10", 0)
            psi_weight = 1.0 + psi_some / config.psi_threshold
            if psi_weight > 1.0:
                logging.debug(f"PSI weight applied: some avg10={psi_some}%, weight={psi_weight:.2f}")

        ret = swap_product * psi_weight > 1.0
        if diagnostic_log:
            diagnostic_log(
                f"check_swap_threshold: swap_product={swap_product:.4f}, "
                f"psi_weight={psi_weight:.2f}, "
                f"final={swap_product * psi_weight:.4f}, trigger={ret}"
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

    def check_thrashing(self, prev):
        """Check if the system is thrashing using swap page counting with PSI amplification.

        Returns True if thrashing is detected, False otherwise.
        Uses swap page counting as the primary trigger. When PSI is available
        and enabled, it amplifies the swap signal (heavy PSI + small swap = trigger,
        but zero swap + any PSI = no trigger).
        """
        return self.check_swap_threshold(prev)

    def get_sleep_interval(self):
        return config.interval / (self.cooldown_counter + 1.0)

    def check_delay(self, expected_delay=0):
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

    def scan(self):
        raise NotImplementedError()

    def update(self, prev, curr):
        pass

    @staticmethod
    def _is_kernel_thread(pid, stats):
        """Kernel threads have kthreadd (pid 2) as parent, or are kthreadd itself."""
        return pid == 2 or stats.ppid == 2

    @staticmethod
    def _is_frozen(pid, stats):
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

    def readStat(self, sfn):
        try:
            return self.readStat_(sfn)
        except (FileNotFoundError, ProcessLookupError):
            return None

    def readStat_(self, sfn):
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

    def checkParents(self, pid, ppid=None):
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

    def scan(self):
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

    def __init__(self):
        self.last_unfrozen_pid = None

    def update(self, prev, cur):
        if cur.unfrozen_pid:
            self.last_unfrozen_pid = cur.unfrozen_pid

    def scan(self):
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


class CgroupPressureProcessSelector(ProcessSelector):
    """
    Selects a process from the cgroup with highest memory pressure.

    Uses per-cgroup memory.pressure PSI metrics to find which cgroup
    is causing the most memory stalls, then selects a process from
    that cgroup to freeze.

    This is more targeted than OOM scores because it identifies the
    actual source of memory pressure rather than just memory usage.
    """

    def __init__(self):
        self.cgroup_pressure_cache = {}  # cgroup_path -> (timestamp, pressure)
        self.cache_ttl = 1.0  # Cache PSI readings for 1 second

    def get_cgroup_pressure(self, cgroup_path):
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

    def scan(self):
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

    def __init__(self):
        ## TODO: garbage collection
        self.pagefault_by_pid = {}
        self.cooldown_counter = 0

    def update(self, prev, cur):
        self.cooldown_counter = cur.cooldown_counter
        if cur.pagefaults - prev.pagefaults > config.pgmajfault_scan_threshold:
            ## If we've had a lot of major page faults, refresh our state
            ## on major page faults.
            self.scan()

    def scan(self):
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

    def __init__(self):
        ## Sets up a prioritized list of selectors:
        ## * LastFrozenProcessSelector is the cheapest and it's surely
        ##   smart to be quick on refreezing a recently unfrozen process
        ##   if unfreezing it causes immediate swap problems.  So it's
        ##   the first method.
        ## * CgroupPressureProcessSelector is the best at targeting but
        ##   slightly more expensive
        ## * OOMScoreProcessSelector is cheaper than
        ##   PageFaultingProcessSelector()
        ## * PageFaultingProcessSelector() is expensive and gives many false
        ##   positives, so it's last.
        ## I'm not sure if we need all of those.  Perhaps it would make sense
        ## to shed some of the older obsoleted selecting methods (TODO).
        self.collection = [
            LastFrozenProcessSelector(),
            CgroupPressureProcessSelector(),
            OOMScoreProcessSelector(),
            PageFaultingProcessSelector(),
        ]
        self.scan_method_count = 0

    def update(self, prev, cur):
        if cur.unfrozen_pid:
            self.scan_method_count = 0
        for c in self.collection:
            c.update(prev, cur)

    def scan(self):
        logging.debug("scan_processes")

        ## a for loop here to make sure we fall back on the next method if the first method fails to find anything.
        for i in range(0, len(self.collection)):
            selector = self.collection[self.scan_method_count % len(self.collection)]
            logging.debug("scan method: %s" % (self.scan_method_count % len(self.collection)))
            ret = selector.scan()
            self.scan_method_count += 1
            if ret:
                if diagnostic_log:
                    diagnostic_log(
                        f"selected pids {ret} via {type(selector).__name__} (method #{self.scan_method_count - 1})"
                    )
                return ret

    logging.debug("found nothing to stop!? :-(")


def get_date_string():
    if config.date_human_readable:
        now = datetime.now()
        return now.strftime("%Y-%m-%d %H:%M:%S") + f".{now.microsecond // 1000:03d}"
    else:
        return str(time.time())


## returns string with detailed process information
def get_process_info(pid):
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


def ignore_failure(method):
    def _try_except_pass(*args, **kwargs):
        try:
            method(*args, **kwargs)
        except Exception:
            logging.critical("Exception ignored", exc_info=True)

    return _try_except_pass


## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: advanced logging


FROZEN_PID_FILE = "/tmp/thrash-protect-frozen-pid-list"
LOG_FILE = "/var/log/thrash-protect.log"


def _write_log_entry(action, pid, log_user_data, all_frozen):
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


def _update_frozen_pid_file(all_frozen):
    """Update or remove the frozen PID list file."""
    if all_frozen:
        with open(FROZEN_PID_FILE, "w") as f:
            f.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in all_frozen]) + "\n")
    else:
        try:
            unlink(FROZEN_PID_FILE)
        except (FileNotFoundError, OSError):
            pass


def log_frozen(pid):
    all_frozen = get_all_frozen_pids()
    _write_log_entry("frozen", pid, config.log_user_data_on_freeze, all_frozen)
    _update_frozen_pid_file(all_frozen)


@ignore_failure
def log_unfrozen(pid):
    all_frozen = get_all_frozen_pids()
    _write_log_entry("unfrozen", pid, config.log_user_data_on_unfreeze, all_frozen)
    _update_frozen_pid_file(all_frozen)


def _debug_check_state(pid, should_be_suspended=False):
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


def _diagnostic_log(msg):
    """Log diagnostic information at INFO level (only called when --diagnostic is enabled)."""
    logging.info("DIAGNOSTIC: %s" % msg)


# diagnostic_log is set up by init_config() based on diagnostic_logging setting.
# When disabled, set to None so `if diagnostic_log:` guards skip string formatting.
diagnostic_log = None


class ThrashProtectState:
    """Encapsulates the runtime state of thrash-protect.

    Holds frozen process tracking, process selectors, and the OOM predictor.
    Provides freeze/unfreeze/cleanup methods that operate on this state.
    """

    def __init__(self):
        self.frozen_items = []
        self.frozen_cgroup_paths = set()
        self.num_unfreezes = 0
        self.process_selector = GlobalProcessSelector()
        self.memory_predictor = None

    def reset(self):
        """Reset all state (useful for testing)."""
        self.frozen_items = []
        self.frozen_cgroup_paths.clear()
        self.num_unfreezes = 0
        self.process_selector = GlobalProcessSelector()
        self.memory_predictor = None

    def get_all_frozen_pids(self):
        """Get combined list of all frozen pids (both SIGSTOP and cgroup frozen)."""
        return [unpack_frozen_item(item)[2] for item in self.frozen_items]

    def freeze_something(self, pids_to_freeze=None):
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

    def unfreeze_something(self):
        if not self.frozen_items:
            return None

        ## queue or stack?  Seems like both approaches are problematic
        if self.num_unfreezes % config.unfreeze_pop_ratio:
            item = self.frozen_items.pop()
        else:
            item = self.frozen_items.pop(0)

        item_type, cgroup_path, pids_to_unfreeze = unpack_frozen_item(item)
        pids_to_unfreeze = list(normalize_pids(pids_to_unfreeze))

        if cgroup_path:
            # Unfreeze via cgroup
            logging.debug("pids to unfreeze (via cgroup): %s" % pids_to_unfreeze)
            unfreeze_cgroup(cgroup_path)
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

    def cleanup(self):
        """Clean up if exiting due to an exception."""
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
            unlink("/tmp/thrash-protect-frozen-pid-list")
        except FileNotFoundError:
            pass

    def run(self, args=None):
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
                self.freeze_something()
            elif not current.cooldown_counter:
                ## If no swapping has been observed for a while and no OOM predicted,
                ## then unfreeze something.
                current.unfrozen_pid = self.unfreeze_something()

            self.process_selector.update(prev, current)

            if current.check_delay() and not busy:
                sleep_interval = current.get_sleep_interval()
                logging.debug("going to sleep %s" % sleep_interval)
                time.sleep(sleep_interval)
                current.check_delay(sleep_interval)


def unfreeze_from_tmpfile():
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
        with open("/tmp/thrash-protect-frozen-pid-list") as pidfile:
            logging.info("cleaning up - unfreezing pids from last run")
            pids_to_open = pidfile.read()
            for pid in pids_to_open.split():
                kill(int(pid), signal.SIGCONT)
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
def get_all_frozen_pids():
    return _tp.get_all_frozen_pids()


def freeze_something(pids_to_freeze=None):
    return _tp.freeze_something(pids_to_freeze)


def unfreeze_something():
    return _tp.unfreeze_something()


def cleanup():
    return _tp.cleanup()


def thrash_protect(args=None):
    return _tp.run(args)


def main():
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
