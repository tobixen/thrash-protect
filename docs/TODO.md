# TODO List for thrash-protect

Updated 2026-02-12 with GitHub issue cross-references.

## v1.1.0

SSD-backed swap may go full rather quickly.  I want to extend the scope of thrash-protect to not only protect against heavy thrashing, but also to protect against OOM-situations.

General idea: in addition to stopping processes when there are two-way swapping, thrash-protect should also stop processes when a linear projection of memory usage gives indications that all memory will be spent within (configurable value:) an hour.

The devil is in the details here.  Memory usage may go pretty fast up and down.  A "linear prediction" needs two observations, and under ordinary circumstances (no thrashing, plenty of memory, no stopped processes) it's needed with some distance between those two observation points.  We should have shorter distance between the observation points when there is less memory available.  We should have very small observation intervals when we're actively stopping and resuming processes.

It should be considered if those ideas are sane, and the details should be fleshed out before starting implementation.

## High Priority

(None currently - see Completed section for recently addressed items)

## Medium Priority

### OOM Protection Tuning

The v1.1 OOM protection uses a simple two-point linear projection. Future improvements:
- Exponential smoothing or weighted moving average for more stable predictions
- Adaptive horizon based on system memory size
- Per-cgroup memory tracking for targeted predictions

### Visual Feedback When Throttling (GitHub #38)

Feature request: provide visual feedback (e.g. mouse cursor change) when thrash-protect is actively throttling processes.  Currently possible via monitoring the state file `/tmp/thrash-protect-frozen-pid-list` or the log file.  A separate desktop integration tool/script could provide this, but it's likely out of scope for thrash-protect itself.

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

### Configurable Log Paths (GitHub #26)

Currently hardcoded:
- `/var/log/thrash-protect.log`
- `/tmp/thrash-protect-frozen-pid-list`

Add environment variables:
- `THRASH_PROTECT_LOG_FILE`
- `THRASH_PROTECT_STATE_FILE`

Note: The original proposal to use `/dev/shm` instead of `/tmp` (#26) is largely moot since most modern distros mount `/tmp` as tmpfs.

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
- ✅ PSI-based thrash detection (GitHub #28, done in 1.0.x)

## GitHub Issues Summary

| Issue | Title | Status | TODO Section |
|-------|-------|--------|-------------|
| #12 | Parent process getting frozen | Open | High: Parent Process Freezing |
| #26 | Store temp files on /dev/shm | **Closed** | Low: Configurable Log Paths |
| #27 | Configuration for ZRAM/SSD swap | Open | High: SSD/ZRAM Default Settings |
| #28 | Detect thrashing using PSI | **Closed** | ✅ Done |
| #29 | Audio stuttering | **Closed** | N/A |
| #38 | Mouse cursor visual feedback | Open | Medium: Visual Feedback |
| #39 | Fork bomb protection | Open | High: Fork Bomb Protection |
