# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) - though PEP440 takes precedence for pre-releases.

For changes prior to v1.0.0, see the ChangeLog file in the v0.15.8 release.

## [1.1.0] - Unreleased

### Added

- **OOM protection**: Proactive memory exhaustion prediction using multi-scale
  linear projection on weighted MemAvailable + SwapFree. Maintains a sliding
  window of observations and checks at multiple time scales (main window and
  1/12th short window), each with a proportional horizon. Swap is weighted
  higher than memory so the predictor naturally triggers when swap starts
  depleting. Configurable via `--oom-protection`/`--no-oom-protection`,
  `--oom-observation-window` (default 60s), `--oom-horizon` (default 600s),
  `--oom-swap-weight`, `--oom-low-pct`.
- **SSD auto-detection**: Automatically detects if swap is on SSD via
  `/proc/swaps` + `/sys/block/*/queue/rotational`. When SSD is detected,
  `swap_page_threshold` is raised from 4 to 64 to avoid false positives.
  Configurable via `--storage-type auto|ssd|hdd`.
- **Type annotations**: Full type hints throughout with `from __future__ import annotations`.

### Changed

- **ThrashProtectState class**: Encapsulated global state (`frozen_items`,
  `frozen_cgroup_paths`, `num_unfreezes`, process selector, memory predictor)
  into a `ThrashProtectState` class with a module-level singleton. Module-level
  backward-compatible functions still available.
- `load_config()` now returns `(config_dict, explicitly_set_keys)` to support
  SSD auto-detection without overriding explicit user settings.

### Fixed

- **OOM predictor false positives**: Replaced naive two-point projection (0.5s
  observation window, 3600s horizon) with multi-scale sliding window predictor.
  The old algorithm treated normal memory fluctuations as impending doom.
  The new algorithm uses a 60s main window with 600s horizon, plus a 5s short
  window with 50s horizon, preventing false positives while still catching
  rapid memory consumption.
- Bare `except:` clauses replaced with `except Exception:` (4 occurrences).
  E722 now enforced via ruff.

## [1.0.2] - 2026-02-11

My priority now is to produce rpm and deb packages.  I may need to change version numbers frequently until it works.

### Changed

- Auto-detect version from `.tag.*` files so package targets work without `version=X.Y.Z`

## [1.0.1] - 2026-02-10

v.1.0.1 is a "meta-release", no changes to the business logic, only Makefile, linting, etc.

### Fixed

- Fix ruff SIM102 lint error: combine nested `if` in `freeze_something()`
- Apply ruff format to pre-existing formatting issues.  (The pypi release workflow
  did not go through due to this).

### Changed

- Update Makefile references from `thrash-protect.py` to `thrash_protect.py`
- Fix `dist` tarball self-inclusion bug
- Pass version to rpm sub-make; make version substitution always run
- Replace `dpkg-buildpackage` with `dpkg-deb` for cross-distro .deb builds
- Remove `dch` dependency (not available on all platforms)
- Update RPM specs: `python` → `python3 >= 3.9`, remove deprecated `Group:` tag
- Update debian packaging: compat 12, Standards-Version 4.6.0, `python3 (>= 3.9)`
- Update `debian/copyright` with correct source URL and year range
- Add `debian/changelog` entry for v1.0.0
- Add `debian` to `.PHONY` in Makefile (directory name conflict)
- Add `gh release create` to `make release` target

## [1.0.0] - 2026-02-10

LOTS of changes done in v1.0.0.  This has been tested on my personal laptop, but nowhere else so far, so if you're dependent on thrash-protect you may want to wait for a while before upgrading.  Most of the changes was done by AI.

Claude Code tends to require quite much memory sometimes, and I tend to have multiple consoles open - so despite having fairly much memory on my laptop I've had incidents with thrashing and OOM'ing lately - and discovered that thrash-protect did make the situation worse rather than better due to changes in my software stack:
* I'm using a local "spiced up" bash version (tabashco), which was not on the list of shells, causing the job-control-workaround to fail.  But even when fixing that issue I still had problems ...
* tmux broke my job-control-workaround.  My earlier logic (stop bash and application at the same time to prevent the job control to kick in) failed because tmux would resume bash.
* I'm no longer using Xorg, but sway and wayland.  They were not on the whitelist, and was among the processes targeted by thrash-protect and "forgotten" in the middle of the dequeue.

The v1.0.0 release solves all those problems for me, as well as bringing many other benefits and improvements.

### Added

- **Cgroup freezing for .scope cgroups**: Use `cgroup.freeze` instead of SIGSTOP for processes
  in `.scope` cgroups (e.g., tmux, screen sessions). This provides atomic freezing that can't
  be bypassed by terminal multiplexers.
- An attempt on **PSI-based thrash detection**: Use Pressure Stall
  Information (`/proc/pressure/memory`) to amplify swap page counting
  for more accurate thrash detection. Available on Linux 4.20+.
  Configurable via `--use-psi`/`--no-psi` and `--psi-threshold`.
  However, the "10s average" is not much useful, so I've reverted to the old behaviour and only adjusting the thresholds according to the PSI stats.
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
- **Documentation**: `docs/CODE_REVIEW.md`, `docs/TODO.md`, `docs/cgroup-enhancement-ideas.md`.  (oh, I didn't read through the CHANGELOG before releasing - this documentation is probably obsoleted already)
- **Diagnostic logging**: `--diagnostic` flag enables detailed logging of process selection
  decisions, swap/PSI values, and scoring. Zero-cost when disabled (no string formatting).

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

### Fixed

- **Skip kernel threads from process selection**: Kernel threads (kthreadd and its children)
  are now excluded from all process selectors. Freezing kthreadd (pid 2) would prevent the
  kernel from spawning new threads, causing a system freeze. Also added kthreadd to the
  static whitelist as defense-in-depth.
- **Job control detection for login shells**: Fixed detection of shells with `-` prefix
  (e.g., `-bash` for login shells).

### Removed

- **Python 2 compatibility**: Removed all Python 2 compatibility code.
- **setup.py**: Replaced by pyproject.toml.
- **ChangeLog**: Replaced by this CHANGELOG.md (Keep a Changelog format).

## [0.15.8] - 2025-12-16

See the ChangeLog file in this release for the complete history of changes
from v0.6 (2013) through v0.15.8.
