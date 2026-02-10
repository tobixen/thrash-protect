# Code Review: thrash-protect

TODO: quite some changes done in v1.0, so this document is probably already outdated.

Date: 2025-12-16 (Updated)
Original review: 2025-12-15

## Overview

thrash-protect is a user-space daemon that protects Linux systems from thrashing by detecting swap activity and temporarily suspending processes using SIGSTOP/SIGCONT signals. The project is mature and functional. Recent modernization work has brought it up to current Python best practices.

## Architecture

The code is well-structured with clear separation of concerns:

- `config` class: Configuration via environment variables
- `SystemState` class: Tracks system state (pagefaults, swap counts, timestamps)
- `ProcessSelector` hierarchy: Strategy pattern for selecting processes to freeze
  - `LastFrozenProcessSelector`: Re-freeze recently unfrozen process (cheap)
  - `OOMScoreProcessSelector`: Select by OOM score
  - `PageFaultingProcessSelector`: Select by page fault count
  - `GlobalProcessSelector`: Combines all selectors

## Strengths

1. **Defensive design**: Uses `mlockall()` to prevent thrash-protect itself from being swapped out
2. **Multiple heuristics**: Three different process selection strategies
3. **Job control awareness**: Handles bash/sudo parent processes correctly
4. **Self-tuning**: Adjusts `max_acceptable_time_delta` based on observed behavior
5. **Cleanup on exit**: Unfreezes all frozen processes
6. **Comprehensive logging**: Log files useful for memory planning
7. **Extensive configuration**: All parameters configurable via environment variables

## Issues and Recommendations

### High Priority

#### 1. SSD Default Settings (TODO)
The default `swap_page_threshold=4` was tuned for spinning disks. SSDs are orders of magnitude faster, so this threshold causes false positives.

**Recommendation**: Increase default to 64+ for SSD systems, or auto-detect storage type.

See README.rst lines 128-141 for documented issues.

This is to be procrastinated a bit, we probably need auto-detection and some playing around to find sensible defaults.

#### 2. ~~Python 2 Compatibility Code~~ ✅ FIXED
~~Lines 13-21 contain Python 2.7 compatibility shims.~~

**Status**: Python 2 compatibility code has been removed. Now requires Python 3.9+.

#### 3. ~~No pyproject.toml~~ ✅ FIXED
~~The project uses legacy `setup.py` with hacky `shutil.copy()` to work around naming issues.~~

**Status**: Migrated to `pyproject.toml` with setuptools-scm for automatic versioning from git tags.

#### 4. ~~Test Framework (nose is deprecated)~~ ✅ FIXED
~~Tests use `nose` which is unmaintained since 2015.~~

**Status**: Migrated to `pytest`.

### Medium Priority

#### 5. Bare except clauses
Several places use bare `except:` which catches all exceptions including `KeyboardInterrupt` and `SystemExit`:

- Line 446: `except:` in `ignore_failure` decorator
- Line 565-566: `except:` in mlockall attempt

**Recommendation**: Use `except Exception:` or specific exceptions.

#### 6. Global Variables
Three global variables remain:
```python
frozen_pids = []
num_unfreezes = 0
global_process_selector = GlobalProcessSelector()
```

These work fine for a single-process daemon but complicate testing.

**Recommendation**: Consider encapsulating in a `ThrashProtect` class.

#### 7. No Type Annotations
The code has no type hints, making it harder to understand and maintain.

**Recommendation**: Add type annotations, at least for public interfaces.

#### 8. Hardcoded Log Paths
Log files are hardcoded:
- `/var/log/thrash-protect.log`
- `/tmp/thrash-protect-frozen-pid-list`

**Recommendation**: Make configurable via environment variables.

### Low Priority

#### 9. ~~Outdated CI~~ ✅ FIXED
~~`.travis.yml` exists but Travis CI is deprecated for open source. No GitHub Actions workflow.~~

**Status**: Added GitHub Actions workflows:
- `.github/workflows/ci.yml`: Linting with ruff, testing with pytest on Python 3.9-3.13
- `.github/workflows/release.yml`: Automatic PyPI release on version tags using trusted publishing

#### 10. ~~Outdated Python Version Classifiers~~ ✅ FIXED
~~setup.py lists Python 2.5-3.6. Current Python is 3.12+.~~

**Status**: Updated to Python 3.9+ only in pyproject.toml classifiers.

#### 11. ~~Version Management~~ ✅ FIXED
~~Version is defined in `thrash-protect.py` line 23 and extracted by regex in setup.py.~~

**Status**: Now using `setuptools-scm` for automatic versioning from git tags, with `importlib.metadata` for runtime version retrieval.

#### 12. ~~Code Style~~ ✅ FIXED
~~Minor style issues that ruff would flag.~~

**Status**: Applied ruff linting and formatting. Added `.pre-commit-config.yaml` for automated checks.

## File Structure Recommendations

Current:
```
thrash-protect.py          # Main script (with dash)
thrash_protect.py -> ./thrash-protect.py  # Symlink
setup.py                   # Legacy setup
```

Recommended:
```
src/
  thrash_protect/
    __init__.py
    __main__.py
    core.py
    selectors.py
    config.py
pyproject.toml
tests/
  test_thrash_protect.py
  conftest.py
```

## Security Considerations

The code runs as root and sends signals to arbitrary processes. Current safeguards:
- Excludes own PID and parent PID from freezing
- Whitelist of critical processes (sshd, bash, systemd-journal, etc.)
- Cleanup on exit

These are reasonable but the whitelist should be reviewed periodically.

## Testing

Current test coverage is limited:
- Unit tests with mocks for file operations
- Functional tests requiring root (skipped in normal runs)

**Recommendation**: Increase unit test coverage, especially for edge cases like:
- Race conditions (process exits while being frozen)
- Multiple rapid freeze/unfreeze cycles
- Configuration edge cases

## Summary

thrash-protect is a useful, battle-tested tool. Major modernization completed in 2025-12:

### ✅ Completed
- pyproject.toml with modern build system
- Python 2 compatibility code removed (Python 3.9+ required)
- Migrated from nose to pytest
- GitHub Actions CI (ruff lint + pytest on Python 3.9-3.13)
- GitHub Actions PyPI release workflow (trusted publishing)
- Automatic versioning via setuptools-scm
- ruff linting and formatting applied
- pre-commit hooks configured

### 🔲 Remaining (lower priority)
- **SSD defaults**: Tune `swap_page_threshold` for SSD systems
- **Bare except clauses**: Replace bare `except:` with `except Exception:`
- **Global variables**: Consider encapsulating in a `ThrashProtect` class
- **Type annotations**: Add type hints for better maintainability
- **Log path configuration**: Make log paths configurable via environment variables
- **Package restructure**: Consider src/ layout for larger refactors
- **Test coverage**: Increase unit test coverage for edge cases
- ~~**ChangeLog format**: The GNU-style ChangeLog format is dated; consider migrating to [Keep a Changelog](https://keepachangelog.com/) format (CHANGELOG.md)~~ **DONE** - Migrated to CHANGELOG.md
