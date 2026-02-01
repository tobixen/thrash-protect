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

    env_mappings = {
        "THRASH_PROTECT_DEBUG_LOGGING": ("debug_logging", _parse_bool),
        "THRASH_PROTECT_DEBUG_CHECKSTATE": ("debug_checkstate", _parse_bool),
        "THRASH_PROTECT_INTERVAL": ("interval", float),
        "THRASH_PROTECT_SWAP_PAGE_THRESHOLD": ("swap_page_threshold", int),
        "THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD": ("pgmajfault_scan_threshold", int),
        "THRASH_PROTECT_USE_PSI": ("use_psi", _parse_bool),
        "THRASH_PROTECT_PSI_THRESHOLD": ("psi_threshold", float),
        "THRASH_PROTECT_CMD_WHITELIST": ("cmd_whitelist", _parse_list),
        "THRASH_PROTECT_CMD_BLACKLIST": ("cmd_blacklist", _parse_list),
        "THRASH_PROTECT_CMD_JOBCTRLLIST": ("cmd_jobctrllist", _parse_list),
        "THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER": ("blacklist_score_multiplier", int),
        "THRASH_PROTECT_WHITELIST_SCORE_MULTIPLIER": ("whitelist_score_divider", int),
        "THRASH_PROTECT_UNFREEZE_POP_RATIO": ("unfreeze_pop_ratio", int),
        "THRASH_PROTECT_TEST_MODE": ("test_mode", int),
        "THRASH_PROTECT_LOG_USER_DATA_ON_FREEZE": ("log_user_data_on_freeze", _parse_bool),
        "THRASH_PROTECT_LOG_USER_DATA_ON_UNFREEZE": ("log_user_data_on_unfreeze", _parse_bool),
        "THRASH_PROTECT_DATE_HUMAN_READABLE": ("date_human_readable", _parse_bool),
    }

    for env_var, (config_key, converter) in env_mappings.items():
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
        "psi_threshold": 5.0,  # Trigger when full avg10 exceeds this percentage
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
    }


def normalize_file_config(file_config):
    """Normalize config keys and values from file config.

    Handles underscore/hyphen differences and type conversions.
    """
    normalized = {}

    # Key mapping (file key -> internal key)
    key_mapping = {
        "debug-logging": "debug_logging",
        "debug-checkstate": "debug_checkstate",
        "swap-page-threshold": "swap_page_threshold",
        "pgmajfault-scan-threshold": "pgmajfault_scan_threshold",
        "use-psi": "use_psi",
        "psi-threshold": "psi_threshold",
        "cmd-whitelist": "cmd_whitelist",
        "cmd-blacklist": "cmd_blacklist",
        "cmd-jobctrllist": "cmd_jobctrllist",
        "blacklist-score-multiplier": "blacklist_score_multiplier",
        "whitelist-score-divider": "whitelist_score_divider",
        "whitelist-score-multiplier": "whitelist_score_divider",  # Alias
        "unfreeze-pop-ratio": "unfreeze_pop_ratio",
        "test-mode": "test_mode",
        "log-user-data-on-freeze": "log_user_data_on_freeze",
        "log-user-data-on-unfreeze": "log_user_data_on_unfreeze",
        "date-human-readable": "date_human_readable",
    }

    # Type converters for each key
    converters = {
        "debug_logging": _parse_bool,
        "debug_checkstate": _parse_bool,
        "interval": float,
        "swap_page_threshold": int,
        "pgmajfault_scan_threshold": int,
        "use_psi": _parse_bool,
        "psi_threshold": float,
        "cmd_whitelist": _parse_list,
        "cmd_blacklist": _parse_list,
        "cmd_jobctrllist": _parse_list,
        "blacklist_score_multiplier": int,
        "whitelist_score_divider": int,
        "unfreeze_pop_ratio": int,
        "test_mode": int,
        "log_user_data_on_freeze": _parse_bool,
        "log_user_data_on_unfreeze": _parse_bool,
        "date_human_readable": _parse_bool,
    }

    for key, value in file_config.items():
        # Normalize key (replace hyphens with underscores, check mapping)
        norm_key = key_mapping.get(key, key.replace("-", "_"))

        # Apply type converter if available
        if norm_key in converters:
            try:
                normalized[norm_key] = converters[norm_key](value)
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
    """
    # 1. Defaults
    final = get_defaults()

    # 2. Config file
    config_path = getattr(args, "config", None)
    file_config = load_from_file(config_path)
    if file_config:
        normalized = normalize_file_config(file_config)
        final.update(normalized)

    # 3. Environment variables
    env_config = load_from_env()
    final.update(env_config)

    # 4. CLI arguments (non-None values only)
    cli_config = {}
    cli_mappings = {
        "debug_logging": "debug_logging",
        "debug_checkstate": "debug_checkstate",
        "interval": "interval",
        "swap_page_threshold": "swap_page_threshold",
        "pgmajfault_scan_threshold": "pgmajfault_scan_threshold",
        "cmd_whitelist": "cmd_whitelist",
        "cmd_blacklist": "cmd_blacklist",
        "cmd_jobctrllist": "cmd_jobctrllist",
        "blacklist_score_multiplier": "blacklist_score_multiplier",
        "whitelist_score_divider": "whitelist_score_divider",
        "unfreeze_pop_ratio": "unfreeze_pop_ratio",
        "test_mode": "test_mode",
        "log_user_data_on_freeze": "log_user_data_on_freeze",
        "log_user_data_on_unfreeze": "log_user_data_on_unfreeze",
        "date_human_readable": "date_human_readable",
    }

    for arg_name, config_key in cli_mappings.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            cli_config[config_key] = value

    final.update(cli_config)

    # Compute derived values
    if final.get("pgmajfault_scan_threshold") is None:
        final["pgmajfault_scan_threshold"] = final["swap_page_threshold"] * 4

    final["max_acceptable_time_delta"] = final["interval"] / 8.0

    return final


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
        help="PSI full avg10 percentage to trigger action (default: 5.0)",
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
    Uses cgroup freezing for any .scope cgroup, which are typically created
    for specific process trees (e.g., by systemd-run, tmux, screen, etc.).
    Scopes are isolated and safe to freeze without affecting unrelated processes.
    """
    cgroup_path = get_cgroup_path(pid)
    if not cgroup_path or not is_cgroup_freezable(cgroup_path):
        return None
    # Use cgroup freezing for any .scope cgroup
    # Scopes are process-specific (unlike slices which are shared)
    # Examples: tmux-spawn-<uuid>.scope, screen-<pid>.scope, run-<id>.scope
    if cgroup_path.endswith(".scope"):
        return cgroup_path
    return None


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

    cfg = load_config(args)

    # Set all config values as attributes on the config class
    for key, value in cfg.items():
        setattr(config, key, value)

    # Set up debug_check_state function based on config
    global debug_check_state
    if config.debug_checkstate:
        debug_check_state = _debug_check_state
    else:
        debug_check_state = lambda a, b: None


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
        ret = ((self.swapcount[0] - prev.swapcount[0] + 0.1) / config.swap_page_threshold) * (
            (self.swapcount[1] - prev.swapcount[1] + 0.1) / config.swap_page_threshold
        ) > 1.0
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

    def check_psi_threshold(self, prev):
        """Check if memory pressure (PSI) indicates thrashing.

        Uses the 'full' metric which indicates time when ALL tasks are
        stalled on memory - a more direct measure of thrashing than swap counts.
        """
        self.cooldown_counter = prev.cooldown_counter

        if config.test_mode and not random.getrandbits(config.test_mode):
            self.cooldown_counter = prev.cooldown_counter + 1
            return True

        if not self.psi or "full" not in self.psi:
            # PSI not available, can't check
            return None

        # Use avg10 (10-second average) for responsiveness
        psi_full = self.psi["full"].get("avg10", 0)
        ret = psi_full >= config.psi_threshold

        if ret:
            self.cooldown_counter = prev.cooldown_counter + 1
            logging.debug(f"PSI thrashing detected: full avg10={psi_full}% >= threshold {config.psi_threshold}%")
        elif prev.cooldown_counter and psi_full < config.psi_threshold / 2:
            # System is significantly below threshold, decrease cooldown
            self.cooldown_counter = prev.cooldown_counter - 1
            logging.debug(f"PSI pressure low: full avg10={psi_full}%, decreasing cooldown")

        return ret

    def check_thrashing(self, prev):
        """Check if the system is thrashing using PSI or swap page counting.

        Returns True if thrashing is detected, False otherwise.
        Uses PSI if available and enabled, falls back to swap page counting.
        """
        # Try PSI first if enabled
        if config.use_psi and is_psi_available():
            result = self.check_psi_threshold(prev)
            if result is not None:
                return result
            # PSI check failed, fall back to swap counting

        # Fall back to swap page counting
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
                if "T" in stats.state:
                    logging.debug(
                        "oom_score: %s, cmd: %s, pid: %s, state: %s - no touch"
                        % (oom_score, stats.cmd, pid, stats.state)
                    )
                    continue
            except FileNotFoundError:
                continue
            if oom_score > 0:
                logging.debug("oom_score: %s, cmd: %s, pid: %s" % (oom_score, stats.cmd, pid))
                if stats.cmd in config.cmd_whitelist:
                    logging.debug("whitelisted process %s %s %s" % (pid, stats.cmd, oom_score))
                    oom_score /= config.whitelist_score_divider
                if stats.cmd in config.cmd_blacklist:
                    oom_score *= config.blacklist_score_multiplier
                if oom_score > max:
                    ## ignore self
                    if pid in (getpid(), getppid()):
                        continue
                    max = oom_score
                    worstpid = (pid, stats.ppid)
        logging.debug("oom scan completed - selected pid: %s" % (worstpid and worstpid[0]))
        if worstpid is not None:
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
                    if line.startswith("full "):
                        # Parse: full avg10=X.XX avg60=X.XX avg300=X.XX total=XXX
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
        """Find process in cgroup with highest memory pressure."""
        if not is_psi_available():
            return None

        max_pressure = 0
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

            # Skip already frozen
            if "T" in stats.state:
                continue

            # Skip whitelisted
            if stats.cmd in config.cmd_whitelist:
                continue

            # Get cgroup path
            cgroup_path = get_cgroup_path(pid)
            if not cgroup_path:
                continue

            # Get cgroup pressure
            pressure = self.get_cgroup_pressure(cgroup_path)
            if pressure is None:
                continue

            # Apply blacklist multiplier
            if stats.cmd in config.cmd_blacklist:
                pressure *= config.blacklist_score_multiplier

            if pressure > max_pressure:
                max_pressure = pressure
                worst_pid = (pid, stats.ppid)
                worst_cgroup = cgroup_path

        if worst_pid and max_pressure > 0:
            logging.debug(
                f"cgroup pressure scan - selected pid: {worst_pid[0]}, "
                f"cgroup: {worst_cgroup}, pressure: {max_pressure}%"
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
            if stats.majflt > 0 and "T" not in stats.state:
                prev = self.pagefault_by_pid.get(pid, 0)
                self.pagefault_by_pid[pid] = stats.majflt
                diff = stats.majflt - prev
                if config.test_mode:
                    diff += random.getrandbits(3)
                if not diff:
                    continue
                if stats.cmd in config.cmd_blacklist:
                    diff *= config.blacklist_score_multiplier
                if stats.cmd in config.cmd_whitelist:
                    logging.debug("whitelisted process %s %s %s" % (pid, stats.cmd, diff))
                    diff /= config.whitelist_score_divider
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
        ## sorted from cheap to expensive.  Also, it is surely smart to be quick on refreezing a recently unfrozen process if host starts thrashing again.
        ## CgroupPressureProcessSelector is after LastFrozen because it's more targeted but slightly more expensive
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
            logging.debug("scan method: %s" % (self.scan_method_count % len(self.collection)))
            ret = self.collection[self.scan_method_count % len(self.collection)].scan()
            self.scan_method_count += 1
            if ret:
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
    except:
        logging.error("Could not fetch process user information, the process is probably gone")
        return "problem fetching process information"


def ignore_failure(method):
    def _try_except_pass(*args, **kwargs):
        try:
            method(*args, **kwargs)
        except:
            logging.critical("Exception ignored", exc_info=True)

    return _try_except_pass


## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: advanced logging


def get_all_frozen_pids():
    """Get combined list of all frozen pids (both SIGSTOP and cgroup frozen)."""
    all_frozen = []
    for item in frozen_items:
        if item[0] == "cgroup":
            all_frozen.append(item[2])  # pids
        else:  # sigstop
            all_frozen.append(item[1])  # pids
    return all_frozen


def log_frozen(pid):
    all_frozen = get_all_frozen_pids()
    with open("/var/log/thrash-protect.log", "ab") as logfile:
        if config.log_user_data_on_freeze:
            logfile.write(
                (
                    "%s - frozen   pid %5s - %s - list: %s\n"
                    % (get_date_string(), str(pid), get_process_info(pid), all_frozen)
                ).encode("utf-8")
            )
        else:
            logfile.write(
                ("%s - frozen pid %s - frozen list: %s\n" % (get_date_string(), pid, all_frozen)).encode("utf-8")
            )

    with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
        logfile.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in all_frozen]) + "\n")


@ignore_failure
def log_unfrozen(pid):
    all_frozen = get_all_frozen_pids()
    with open("/var/log/thrash-protect.log", "ab") as logfile:
        if config.log_user_data_on_unfreeze:
            logfile.write(
                (
                    "%s - unfrozen   pid %5s - %s - list: %s\n"
                    % (get_date_string(), str(pid), get_process_info(pid), all_frozen)
                ).encode("utf-8")
            )
        else:
            logfile.write(("%s - unfrozen pid %s\n" % (get_date_string(), pid)).encode("utf-8"))

    if all_frozen:
        with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
            logfile.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in all_frozen]) + "\n")
    else:
        try:
            unlink("/tmp/thrash-protect-frozen-pid-list")
        except (FileNotFoundError, OSError):
            pass


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


def freeze_something(pids_to_freeze=None):
    global frozen_items
    global global_process_selector
    pids_to_freeze = pids_to_freeze or global_process_selector.scan()
    if not pids_to_freeze:
        ## process disappeared. ignore failure
        logging.info("nothing to freeze found, or the process we were going to suspend has already exited")
        return ()
    if not hasattr(pids_to_freeze, "__iter__"):
        pids_to_freeze = (pids_to_freeze,)
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

    if cgroup_path:
        # Use cgroup freezing - freezes all processes in the cgroup atomically
        if freeze_cgroup(cgroup_path):
            frozen_items.append(("cgroup", cgroup_path, pids_to_freeze))
            for pid_to_freeze in pids_to_freeze:
                logging.debug("froze pid %s (via cgroup)" % str(pid_to_freeze))
                log_frozen(pid_to_freeze)
            return pids_to_freeze
        # Fall through to SIGSTOP if cgroup freezing failed

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
    if not any(item[0] == "sigstop" and item[1] == pids_to_freeze for item in frozen_items):
        frozen_items.append(("sigstop", pids_to_freeze))

    for pid_to_freeze in pids_to_freeze:
        ## Logging after freezing - as logging itself may be resource- and timeconsuming.
        ## Perhaps we should even fork it out.
        logging.debug("froze pid %s" % str(pid_to_freeze))
        log_frozen(pid_to_freeze)
    return pids_to_freeze


def unfreeze_something():
    global frozen_items
    global num_unfreezes

    if not frozen_items:
        return None

    ## queue or stack?  Seems like both approaches are problematic
    if num_unfreezes % config.unfreeze_pop_ratio:
        item = frozen_items.pop()
    else:
        item = frozen_items.pop(0)

    # Extract pids based on item type
    if item[0] == "cgroup":
        _, cgroup_path, pids_to_unfreeze = item
    else:  # sigstop
        _, pids_to_unfreeze = item
        cgroup_path = None

    # Normalize pids to a list
    if not hasattr(pids_to_unfreeze, "__iter__"):
        pids_to_unfreeze = [pids_to_unfreeze]
    else:
        pids_to_unfreeze = list(pids_to_unfreeze)

    if cgroup_path:
        # Unfreeze via cgroup
        logging.debug("pids to unfreeze (via cgroup): %s" % pids_to_unfreeze)
        unfreeze_cgroup(cgroup_path)
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

    num_unfreezes += 1
    return pids_to_unfreeze


def thrash_protect(args=None):
    current = SystemState()
    global frozen_items
    global global_process_selector

    ## A best-effort attempt on running mlockall()
    try:
        import ctypes

        try:
            assert not ctypes.cdll.LoadLibrary("libc.so.6").mlockall(ctypes.c_int(7))
        except:
            assert not ctypes.cdll.LoadLibrary("libc.so.6").mlockall(ctypes.c_int(3))
    except:
        logging.warning(
            "failed to do mlockall() - this makes the program vulnerable of being swapped out in an extreme thrashing event (maybe you're not running the script as root?)",
            exc_info=False,
        )

    while True:
        prev = current
        current = SystemState()
        busy = current.check_thrashing(prev)

        ## If we're thrashing, then freeze something.
        if busy:
            freeze_something()
        elif not current.cooldown_counter:
            ## If no swapping has been observed for a while then unfreeze something.
            current.unfrozen_pid = unfreeze_something()

        global_process_selector.update(prev, current)

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


def cleanup():
    ## Clean up if exiting due to an exception.
    global frozen_items

    for item in frozen_items:
        if item[0] == "cgroup":
            unfreeze_cgroup(item[1])  # cgroup_path
        else:  # sigstop
            pids_to_unfreeze = item[1]
            if not hasattr(pids_to_unfreeze, "__iter__"):
                pids_to_unfreeze = [pids_to_unfreeze]
            for pid_to_unfreeze in reversed(pids_to_unfreeze):
                try:
                    kill(pid_to_unfreeze, signal.SIGCONT)
                except ProcessLookupError:
                    pass
    try:
        unlink("/tmp/thrash-protect-frozen-pid-list")
    except FileNotFoundError:
        pass


## Globals ... we've refactored most of them away, but some still remains ...
# Unified list of frozen items - each entry is one of:
#   ('cgroup', cgroup_path, pids) - frozen via cgroup freezer
#   ('sigstop', pids)             - frozen via SIGSTOP
frozen_items = []
num_unfreezes = 0
## A singleton ...
global_process_selector = GlobalProcessSelector()


def main():
    """Main entry point for thrash-protect."""
    p = create_argument_parser()
    args = p.parse_args()

    # Initialize configuration from all sources (CLI > file > env > defaults)
    init_config(args)

    # Set up debug logging if enabled
    if config.debug_logging:
        logging.root.setLevel(logging.DEBUG)

    unfreeze_from_tmpfile()

    try:
        thrash_protect(args)
    finally:
        cleanup()


if __name__ == "__main__":
    main()
