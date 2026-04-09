# TODO List for thrash-protect

Updated 2026-02-12 with GitHub issue cross-references.

## v1.1.0

SSD-backed swap may go full rather quickly.  I want to extend the scope of thrash-protect to not only protect against heavy thrashing, but also to protect against OOM-situations.

General idea: in addition to stopping processes when there are two-way swapping, thrash-protect should also stop processes when a linear projection of memory usage gives indications that all memory will be spent within (configurable value:) an hour.

The devil is in the details here.  Memory usage may go pretty fast up and down.  A "linear prediction" needs two observations, and under ordinary circumstances (no thrashing, plenty of memory, no stopped processes) it's needed with some distance between those two observation points.  We should have shorter distance between the observation points when there is less memory available.  We should have very small observation intervals when we're actively stopping and resuming processes.

It should be considered if those ideas are sane, and the details should be fleshed out before starting implementation.

## High Priority

### SSD/ZRAM Default Settings (GitHub #27)

**Update**: I'm not sure if this is really a problem.  More research should probably be done.  Perhaps spin up a VM in OpenStack with local SSD storage, minimum memory and play with memory-hogging processes there.

The default `swap_page_threshold=4` was tuned for spinning magnetic disks. SSDs are orders of magnitude faster, so this threshold causes false positives - thrash-protect may suspend processes unnecessarily when the system is handling swap I/O just fine.  ZRAM swap has similar issues since compression/decompression is CPU-bound rather than I/O-bound.

**Problem**: On SSD-based and ZRAM-based systems, thrash-protect can cause performance degradation by suspending processes that aren't actually causing problems.

**Current workaround**: Set `THRASH_PROTECT_SWAP_PAGE_THRESHOLD=64` (or higher) in the environment.

**Proposed solutions**:
1. Auto-detect if swap is on SSD and adjust threshold automatically
2. Change default to a higher value (e.g., 32 or 64) since SSDs are now common
3. Add a configuration option like `THRASH_PROTECT_STORAGE_TYPE=ssd|hdd|auto`

**Investigation needed**:
- How to reliably detect SSD vs HDD for the swap partition(s)
- What threshold values work well for SSDs and ZRAM
- Whether the page fault metrics also need adjustment for SSDs
- Whether thrash-protect is useful at all on SSD/ZRAM systems, or should the README recommend against it

See README.rst "Drawbacks and problems" section for more context.

### ~~Use /proc/pressure for Thrash Detection (GitHub #28)~~ ✅ Done

Implemented as PSI amplification on swap-based detection. Configurable via `--use-psi`/`--no-psi` and `--psi-threshold`. Falls back to swap counting on older kernels.

### Fork Bomb Protection (GitHub #39)

Thrash-protect does not protect sufficiently against fork bombs.  It should be more aggressive in suspending parent processes when a fork bomb is detected (rapid process creation from the same parent).

Related to #12 (parent process freezing).

### Parent Process Freezing / Job Control (GitHub #12)

Suspending a child process causes side-effects for the parent sometimes (notably, bash job control and sudo).  Current workarounds exist (resuming session/group process IDs, freezing parent before child for bash/sudo), but a more general solution is needed.

**Possible approach**: Always freeze the parent process before suspending a child (possibly recursively, but never freezing PID 1).  Needs more research and testing.

## Medium Priority

### Add Type Annotations

Add type hints to improve code maintainability and enable static analysis with mypy.

### Bare Except Clauses

Several places use bare `except:` which catches all exceptions including `KeyboardInterrupt` and `SystemExit`. Should use `except Exception:` or specific exceptions.

### Global Variables

Consider encapsulating the global variables (`frozen_pids`, `num_unfreezes`, `global_process_selector`) in a `ThrashProtect` class for better testability.

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
