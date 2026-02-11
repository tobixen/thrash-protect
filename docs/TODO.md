# TODO List for thrash-protect

Update 2026-02-10: I've forgotten completely to read and update this document, so it's probably already slightly outdated.

## High Priority

(None currently - see Completed section for recently addressed items)

## Medium Priority

### OOM Protection Tuning

The v1.1 OOM protection uses a simple two-point linear projection. Future improvements:
- Exponential smoothing or weighted moving average for more stable predictions
- Adaptive horizon based on system memory size
- Per-cgroup memory tracking for targeted predictions

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

- ✅ SSD auto-detection for swap threshold (done in v1.1)
- ✅ OOM protection / memory exhaustion prediction (done in v1.1)
- ✅ Add type annotations (done in v1.1)
- ✅ Fix bare except clauses (done in v1.1)
- ✅ Encapsulate globals into ThrashProtectState class (done in v1.1)
- ✅ PSI-based thrash detection (done in v1.0)
- ✅ Remove Python 2 compatibility code (done in 0.15.x)
- ✅ Migrate tests from nose to pytest (done in 0.15.x)
- ✅ Add pyproject.toml with modern build system (done in 0.15.x)
- ✅ Add GitHub Actions CI/CD (done in 0.15.x)
- ✅ Automatic versioning via setuptools-scm (done in 0.15.x)
