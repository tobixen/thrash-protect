# Code Review: thrash-protect

Date: 2025-12-15

## Overview

thrash-protect is a user-space daemon that protects Linux systems from thrashing by detecting swap activity and temporarily suspending processes using SIGSTOP/SIGCONT signals. The project is mature and functional but needs modernization to align with current Python best practices.

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

#### 2. Python 2 Compatibility Code (Remove)
Lines 13-21 contain Python 2.7 compatibility shims:
```python
from __future__ import with_statement
try:
    ProcessLookupError
except NameError:
    ProcessLookupError=OSError
```

Python 2 reached EOL in 2020. This code should be removed.

#### 3. No pyproject.toml
The project uses legacy `setup.py` with hacky `shutil.copy()` to work around naming issues (`thrash-protect.py` vs `thrash_protect`).

**Recommendation**: Migrate to `pyproject.toml` with proper package structure.

#### 4. Test Framework (nose is deprecated)
Tests use `nose` which is unmaintained since 2015.

**Recommendation**: Migrate to `pytest`.

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

#### 9. Outdated CI
`.travis.yml` exists but Travis CI is deprecated for open source. No GitHub Actions workflow.

**Recommendation**: Add `.github/workflows/` with ruff linting and pytest.

#### 10. Outdated Python Version Classifiers
setup.py lists Python 2.5-3.6. Current Python is 3.12+.

**Recommendation**: Update to Python 3.9+ only.

#### 11. Version Management
Version is defined in `thrash-protect.py` line 23 and extracted by regex in setup.py.

**Recommendation**: Use `setuptools-scm` or similar for automatic versioning from git tags.

#### 12. Code Style
Minor style issues that ruff would flag:
- Some long lines
- f-strings could replace `%` formatting
- Unused imports possible

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

thrash-protect is a useful, battle-tested tool that needs modernization:

1. **Must do**: pyproject.toml, remove Python 2 code, fix SSD defaults
2. **Should do**: Migrate to pytest, add type hints, GitHub Actions
3. **Nice to have**: Restructure as proper package, increase test coverage
