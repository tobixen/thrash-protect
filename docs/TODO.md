# TODO List for thrash-protect

## High Priority

### SSD Default Settings

The default `swap_page_threshold=4` was tuned for spinning magnetic disks. SSDs are orders of magnitude faster, so this threshold causes false positives - thrash-protect may suspend processes unnecessarily when the system is handling swap I/O just fine.

**Problem**: On SSD-based systems, thrash-protect can cause performance degradation by suspending processes that aren't actually causing problems.

**Current workaround**: Set `THRASH_PROTECT_SWAP_PAGE_THRESHOLD=64` (or higher) in the environment.

**Proposed solutions**:
1. Auto-detect if swap is on SSD and adjust threshold automatically
2. Change default to a higher value (e.g., 32 or 64) since SSDs are now common
3. Add a configuration option like `THRASH_PROTECT_STORAGE_TYPE=ssd|hdd|auto`

**Investigation needed**:
- How to reliably detect SSD vs HDD for the swap partition(s)
- What threshold values work well for SSDs
- Whether the page fault metrics also need adjustment for SSDs

See README.rst "Drawbacks and problems" section for more context.

### Use /proc/pressure for Thrash Detection

Modern Linux kernels (4.20+) provide Pressure Stall Information (PSI) via `/proc/pressure/`. This could provide a more accurate and efficient way to detect memory pressure than the current approach.

**Files available**:
- `/proc/pressure/memory` - memory pressure metrics
- `/proc/pressure/io` - I/O pressure metrics
- `/proc/pressure/cpu` - CPU pressure metrics

**Example output** from `/proc/pressure/memory`:
```
some avg10=0.00 avg60=0.00 avg300=0.00 total=0
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

**Benefits**:
- Kernel-provided metric specifically designed for detecting resource pressure
- "some" line: percentage of time at least one task is stalled
- "full" line: percentage of time all tasks are stalled (more severe)
- More holistic view than page fault counting
- Could complement or replace current swap page threshold detection

**Implementation ideas**:
1. Add PSI-based detection as an alternative/additional trigger
2. Use `full` memory pressure avg10 > threshold as trigger
3. Fall back to current method on older kernels without PSI support
4. Add config option: `THRASH_PROTECT_USE_PSI=auto|yes|no`

**References**:
- https://docs.kernel.org/accounting/psi.html
- https://facebookmicrosites.github.io/psi/

## Medium Priority

### Add Type Annotations

Add type hints to improve code maintainability and enable static analysis with mypy.

### Bare Except Clauses

Several places use bare `except:` which catches all exceptions including `KeyboardInterrupt` and `SystemExit`. Should use `except Exception:` or specific exceptions.

### Global Variables

Consider encapsulating the global variables (`frozen_pids`, `num_unfreezes`, `global_process_selector`) in a `ThrashProtect` class for better testability.

## Low Priority

### Package Structure

Consider restructuring as a proper Python package:

```
src/
  thrash_protect/
    __init__.py
    __main__.py
    core.py
    selectors.py
    config.py
```

This would allow:
- Cleaner imports
- Better separation of concerns
- Easier testing

### Configurable Log Paths

Currently hardcoded:
- `/var/log/thrash-protect.log`
- `/tmp/thrash-protect-frozen-pid-list`

Add environment variables:
- `THRASH_PROTECT_LOG_FILE`
- `THRASH_PROTECT_STATE_FILE`

### Review Process Whitelist

The default `cmd_whitelist` may need updating for modern systems:
```python
['sshd', 'bash', 'xinit', 'X', 'spectrwm', 'screen', 'SCREEN',
 'mutt', 'ssh', 'xterm', 'rxvt', 'urxvt', 'Xorg.bin', 'Xorg', 'systemd-journal']
```

Consider adding:
- `systemd`
- `dbus-daemon`
- `polkitd`
- Common container runtimes

## Completed

- ✅ Remove Python 2 compatibility code (done in 0.15.x)
- ✅ Migrate tests from nose to pytest (done in 0.15.x)
- ✅ Add pyproject.toml with modern build system (done in 0.15.x)
- ✅ Add GitHub Actions CI/CD (done in 0.15.x)
- ✅ Automatic versioning via setuptools-scm (done in 0.15.x)
