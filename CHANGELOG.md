# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For changes prior to v0.16.0, see the ChangeLog file in the v0.15.8 release.

## [Unreleased]

### Added

- **Cgroup freezing for .scope cgroups**: Use `cgroup.freeze` instead of SIGSTOP for processes
  in `.scope` cgroups (e.g., tmux, screen sessions). This provides atomic freezing that can't
  be bypassed by terminal multiplexers.
- **PSI-based thrash detection**: Use Pressure Stall Information (`/proc/pressure/memory`)
  instead of swap page counting for more accurate thrash detection. Available on Linux 4.20+.
  Configurable via `--use-psi`/`--no-psi` and `--psi-threshold`.
- **CgroupPressureProcessSelector**: New process selector that uses per-cgroup memory pressure
  (`/sys/fs/cgroup/.../memory.pressure`) to identify which cgroup is causing memory stalls.
- **Multi-format config file support**: Configuration files in INI, JSON, YAML, and TOML formats.
  Auto-detected by file extension.
- **Full CLI options**: All configuration parameters now available as `--long-options`.
- **Dynamic default whitelist**: Shells are now read from `/etc/shells` instead of hardcoded list.
- **Modernized static whitelist**: Added Wayland compositors (sway, wayfire, hyprland) and
  modern terminals (alacritty, kitty, foot).
- **Example config files**: `thrash-protect.conf.example` (INI) and `thrash-protect.yaml.example` (YAML).
- **Optional dependencies**: PyYAML for YAML config, tomli for TOML config on Python < 3.11.
- **GitHub Actions**: CI for linting/testing, automatic PyPI release on tags.
- **Pre-commit hooks**: ruff linting and formatting, lychee link checker.
- **Documentation**: `docs/CODE_REVIEW.md`, `docs/TODO.md`, `docs/cgroup-enhancement-ideas.md`.

### Changed

- **Configuration priority**: CLI > environment variables > config file > defaults.
- **Unified frozen items tracking**: Internal refactor - `frozen_pids` and `frozen_cgroups`
  merged into single `frozen_items` list for fair FIFO/LIFO ordering.
- **Unified CONFIG_SCHEMA**: Single source of truth for configuration keys, types, and mappings.
- **Helper functions**: Added `normalize_pids()`, `apply_score_adjustments()`, `unpack_frozen_item()`
  to reduce code duplication.
- **Log functions refactored**: `log_frozen()` and `log_unfrozen()` now share common helpers.
- **Build system**: Migrated from setuptools-scm to Hatch with hatch-vcs.
- **Python requirements**: Python 3.9+ required (removed Python 2 compatibility code).
- **File renamed**: `thrash-protect.py` renamed to `thrash_protect.py` for setuptools compatibility.
- **Test framework**: Migrated from nose to pytest.
- **Code formatting**: Applied ruff linting and formatting throughout.
- **Millisecond precision**: Log timestamps now include milliseconds.

### Fixed

- **Job control detection for login shells**: Fixed detection of shells with `-` prefix
  (e.g., `-bash` for login shells).
- **Config priority**: Environment variables now correctly override config file settings.
- **Test mock attributes**: Fixed missing `timer_alert` attribute in PSI fallback test.

### Removed

- **Python 2 compatibility**: Removed all Python 2 compatibility code.
- **setup.py**: Replaced by pyproject.toml.
- **ChangeLog**: Replaced by this CHANGELOG.md (Keep a Changelog format).

## [0.15.8] - 2025-12-16

See the ChangeLog file in this release for the complete history of changes
from v0.6 (2013) through v0.15.8.
