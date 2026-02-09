# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) - though PEP440 takes precedence for pre-releases.

For changes prior to v1.0.0, see the ChangeLog file in the v0.15.8 release.

## [Unreleased]

New local usage patterns caused problems for me:

* Claude Code tends to require quite much memory sometimes, and I tend to have multiple consoles open - so despite having fairly much memory on my laptop I've had incidents with thrashing and OOM'ing lately.
* I'm no longer using Xorg, but sway and wayland.  They were not on the whitelist, and was among the processes targeted by thrash-protect and "forgotten" in the middle of the dequeue.
* I'm using a local "spiced up" bash version (tabashco), which was not on the list of shells, causing the job-control-workaround to fail.  Even after fixing this, the job-control-workaround continued failing as tmux is aggressively running "kill -CONT" on its children.

The unreleased code solves all those problems for me, as well as bringing many other benefits and improvements.

### Added

- **Cgroup freezing for .scope cgroups**: Use `cgroup.freeze` instead of SIGSTOP for processes
  in `.scope` cgroups (e.g., tmux, screen sessions). This provides atomic freezing that can't
  be bypassed by terminal multiplexers.
- **PSI-based thrash detection**: Use Pressure Stall Information (`/proc/pressure/memory`)
  to amplify swap page counting for more accurate thrash detection. Available on Linux 4.20+.
  Configurable via `--use-psi`/`--no-psi` and `--psi-threshold`.
- **CgroupPressureProcessSelector**: New process selector that uses per-cgroup memory pressure
  (`/sys/fs/cgroup/.../memory.pressure`) to identify which cgroup is causing memory stalls.
- **Multi-format config file support**: Configuration files in INI, JSON, YAML, and TOML formats.
  Auto-detected by file extension.
- **Full CLI options**: All configuration parameters now available as `--long-options`.
- **Dynamic default whitelist**: Shells are now read from `/etc/shells` instead of hardcoded list.
- **Modernized static whitelist**: Added Wayland compositors (sway, wayfire, hyprland),
  modern terminals (alacritty, kitty, foot), login, and supervisord.
- **Example config files**: `thrash-protect.conf.example` (INI) and `thrash-protect.yaml.example` (YAML).
- **Optional dependencies**: PyYAML for YAML config, tomli for TOML config on Python < 3.11.
- **GitHub Actions**: CI for linting/testing, automatic PyPI release on tags.
- **Pre-commit hooks**: ruff linting and formatting, lychee link checker.
- **Documentation**: `docs/CODE_REVIEW.md`, `docs/TODO.md`, `docs/cgroup-enhancement-ideas.md`.

### Changed

- **Configuration priority**: CLI > environment variables > config file > defaults.
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
- **Hybrid swap+PSI thrash detection**: PSI is now used as a weight that amplifies
  the swap page counting signal, rather than as a standalone primary detector.
  This provides instant feedback (swap-based cooldown) while PSI amplifies sensitivity
  during memory pressure. Zero swap + any PSI = no trigger.

### Fixed

- **Skip kernel threads from process selection**: Kernel threads (kthreadd and its children)
  are now excluded from all process selectors. Freezing kthreadd (pid 2) would prevent the
  kernel from spawning new threads, causing a system freeze. Also added kthreadd to the
  static whitelist as defense-in-depth.
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
