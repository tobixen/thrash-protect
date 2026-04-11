# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) - though PEP440 takes precedence for pre-releases.

For changes prior to v1.0.0, see the ChangeLog file in the v0.15.8 release.

## [1.1.0] - Unreleased

SSDs are different things than HDDs.  Some observations:

* It may take a long time to fill up a swap partition on an old
  spinning disk, but SSDs gets filled up really fast.
* Thrash-protect is checking the IO-load to figure how badly thrashed
  a computer is.  This is not tuned for SSD - the box typically don't
  appear to be thrashed at all until it's suddenly out of memory (and
  at that time, thrash-protect goes crazy stopping basically
  everything)

This release is partially working around or solving some of those problems.

### Added

- **OOM protection**: Proactive memory exhaustion prediction using multi-scale
  linear projection on weighted MemAvailable + SwapFree. Maintains a sliding
  window of observations and checks at three time scales (main 60s window, a
  1/12x short window for rapid decline detection, and a 1/60x rapid scale for
  active freeze/unfreeze cycles), each with a proportional horizon. Swap is
  weighted higher than memory so the predictor naturally triggers when swap
  starts depleting. Configurable via `--oom-protection`/`--no-oom-protection`,
  `--oom-observation-window` (default 60s), `--oom-horizon` (default 600s),
  `--oom-swap-weight`, `--oom-low-pct` (default 100 = always predict; lower to
  predict only when available memory is below that percentage).
- **Repeat-offender blacklist**: Processes that are unfrozen and immediately
  cause re-thrashing (detected by `LastFrozenProcessSelector`) are blacklisted
  and kept frozen longer by skipping a configurable number of unfreeze cycles.
  Includes a deadlock guard for when all frozen items are blacklisted.
  Configurable via `--blacklist-expiry-time` (default 60s) and
  `--blacklist-max-skip-count` (default 3).
- **SSD auto-detection**: Automatically detects if swap is on SSD via
  `/proc/swaps` + `/sys/block/*/queue/rotational`. When SSD is detected,
  `swap_page_threshold` is raised from 4 to 64 to avoid false positives.
  Configurable via `--storage-type auto|ssd|hdd`.
- **Type annotations**: Full type hints throughout with `from __future__ import annotations`.
- **zswap-aware thrash detection**: The swap product formula now includes zswap
  counters (`zswpin`/`zswpout` from `/proc/vmstat`, Linux 6.3+). Disk swap pages
  are weighted by `pswp_weight` (auto-detected: HDD=128, SSD=8) relative to zswap
  pages, reflecting the large speed difference between disk I/O and in-RAM compression.
  The effective zswap trigger level (`swap_page_threshold × pswp_weight`) is
  storage-type-independent (both HDD and SSD yield 512 pages). Configurable via
  `--pswp-weight`. Falls back gracefully to disk-only detection on kernels without
  `zswpin`/`zswpout` (pre-6.3).
- **OOM predictor diagnostic logging**: `MemoryExhaustionPredictor.update_and_predict()`
  now emits detailed `diagnostic_log` output for each observation scale: current
  available/total memory, decline rate, projected ETA, and whether the scale
  triggered.

### Changed

- **ThrashProtectState class**: Encapsulated global state (`frozen_items`,
  `frozen_cgroup_paths`, `num_unfreezes`, process selector, memory predictor)
  into a `ThrashProtectState` class with a module-level singleton. Module-level
  backward-compatible functions still available.
- `load_config()` now returns `(config_dict, explicitly_set_keys)` to support
  SSD auto-detection without overriding explicit user settings.
- **Diagnostic logging auto-enabled for dev builds**: When the version string
  contains pre-release markers (dev, alpha, beta, rc, .dirty), diagnostic
  logging is automatically enabled unless the user has explicitly disabled it.

### Fixed

- **OOM predictor never fired with default config**: `--oom-low-pct` defaulted
  to 0.0, which meant the condition `avail_pct >= low_pct` was always true and
  prediction was always skipped. Default corrected to 100.0 (threshold
  effectively inactive; lower to e.g. 10 to predict only when < 10% free).
- **OOM predictor false positives**: Replaced naive two-point projection with
  multi-scale sliding window predictor. The old algorithm treated normal memory
  fluctuations as impending doom. The new algorithm uses three time scales with
  proportional horizons, and requires that reference observations fall within a
  tolerance window of the target time (preventing a 0.5s-old sample being used
  as a 60s-ago reference).
- Bare `except:` clauses replaced with `except Exception:` (4 occurrences).
  E722 now enforced via ruff.

## [1.0.5] - 2026-04-09

Some bugfixes and whitelistings added, as I've been having some troubles with thrash-protect lately.

### Fixed

- **Cgroup freeze bugs**: prevent self-freezing deadlock (thrash-protect could freeze its own
  cgroup, causing a deadlock), re-insert items on failed unfreeze instead of silently losing
  track of them (which caused an ever-growing frozen list under certain conditions), persist
  frozen cgroup paths to `/tmp/thrash-protect-frozen-cgroup-list` for crash recovery.
- Fix version embedding for standalone installs: switch to `importlib.metadata` with a
  `DEVELOPMENT` sentinel replaced by `sed` during install (avoids broken `_version.py` imports).
- **Extended whitelist for modern Wayland desktops**: `waybar`, `wireplumber`, `pipewire`,
  `swaync`, `swayidle`, `dbus-broker` are now protected from being frozen. Previously, freezing
  `waybar` could stall the sway compositor event loop via a full IPC socket buffer.

### Added

- **Sway/waybar integration extras**: visual indicator scripts and a systemd user service
  showing when thrash-protect is actively throttling processes (`extras/`).

### Removed

- Debian sysvinit init script (superseded by systemd service).

## [1.0.4] - 2026-02-12

My priority now is to produce rpm and deb packages.  This is done via the Makefile and a "make release" is needed for every attempt - hence I may need to change version numbers frequently until it works.

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
