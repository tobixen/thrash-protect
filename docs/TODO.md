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

## Medium Priority

### Remove Python 2 Compatibility

Python 2 reached end-of-life in January 2020. The compatibility shims at the top of `thrash-protect.py` can be removed:

```python
from __future__ import with_statement
try:
    ProcessLookupError
except NameError:
    ProcessLookupError=OSError
# etc.
```

### Migrate Tests to pytest

Tests currently use `nose` which has been unmaintained since 2015. Should migrate to `pytest`:

- Replace `nose.tools.assert_equal` with plain `assert`
- Replace `nose.plugins.skip.SkipTest` with `pytest.skip()`
- Update test fixtures to use pytest fixtures

### Add Type Annotations

Add type hints to improve code maintainability and enable static analysis with mypy.

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
