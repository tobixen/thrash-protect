# TODO List for thrash-protect

Updated 2026-02-12 with GitHub issue cross-references.

Background reading:
[incident-2026-08-09-io-starvation.md](incident-2026-08-09-io-starvation.md) —
a real false positive where IO starvation, not memory shortage, drove the
trigger; several items below come from it.  Fixes 1–3 from that document (PSI
swap floor, anon/file refault weighting, IO-pressure veto) are implemented;
4–7 are still open, and fix 4 is the pgid rollup described under Fork Bomb
Protection below.

## v1.1.0

SSD-backed swap may go full rather quickly.  I want to extend the scope of thrash-protect to not only protect against heavy thrashing, but also to protect against OOM-situations.

General idea: in addition to stopping processes when there are two-way swapping, thrash-protect should also stop processes when a linear projection of memory usage gives indications that all memory will be spent within (configurable value:) an hour.

The devil is in the details here.  Memory usage may go pretty fast up and down.  A "linear prediction" needs two observations, and under ordinary circumstances (no thrashing, plenty of memory, no stopped processes) it's needed with some distance between those two observation points.  We should have shorter distance between the observation points when there is less memory available.  We should have very small observation intervals when we're actively stopping and resuming processes.

It should be considered if those ideas are sane, and the details should be fleshed out before starting implementation.

## High Priority

### ~~SSD Auto-detection (GitHub #27)~~ ✅ Partially done

SSD auto-detection was implemented in v1.1.  ZRAM swap may still need attention — compression/decompression is CPU-bound rather than I/O-bound, so the swap page threshold tuning may not be appropriate.  Whether thrash-protect is useful at all on ZRAM systems is an open question.

### ~~Use /proc/pressure for Thrash Detection (GitHub #28)~~ ✅ Done

Implemented as PSI amplification on swap-based detection. Configurable via `--use-psi`/`--no-psi` and `--psi-threshold`. Falls back to swap counting on older kernels.

The amplifier is now gated, after it was found to convict on page-cache
refault pressure alone — see
[incident-2026-08-09-io-starvation.md](incident-2026-08-09-io-starvation.md).
It requires a minimum swap signal in both directions (`--psi-swap-floor`), is
scaled by the anonymous share of workingset refaults, and is vetoed outright
when IO pressure explains the stall (`--io-pressure-threshold`).

Two follow-ups from the review of that work:

- The IO-pressure veto reads io `full`, while memory PSI deliberately reads
  `some` because `full` under-reports on multi-core machines.  On the 1-vCPU
  incident host `full` was 84.8 and the veto fires; on an 8-core box with one
  thread stalled on IO, `full` can sit near zero while `some` is above 90, so
  the veto may rarely fire where it is meant to.  Needs a measurement from a
  multi-core host before changing.
- `/proc/vmstat` is now parsed three times per interval (`get_pagefaults`,
  `get_swapcount`, `get_workingset_refaults`), in a daemon that polls faster
  the busier it gets.  One read into a dict of the handful of keys of interest
  would do — related to fix 5 in the incident document, which criticises
  thrash-protect for adding to the IO load it is reacting to.

### Fork Bomb Protection (GitHub #39)

Thrash-protect does not protect sufficiently against fork bombs.  It should be more aggressive in suspending parent processes when a fork bomb is detected (rapid process creation from the same parent).

Related to #12 (parent process freezing).

The same rollup is needed for a non-malicious case: a fork-heavy batch job
(`find / | xargs -n100 grep`) respawns its worker so fast that victim selection
can never signal it, and falls through to innocent long-lived services instead.
Aggregating `/proc/<pid>/io:read_bytes` and fault counters by pgid/session/cgroup
would serve #39, #12 and that case at once.  See
[incident-2026-08-09-io-starvation.md](incident-2026-08-09-io-starvation.md),
fix 4.

### Parent Process Freezing / Job Control (GitHub #12)

Suspending a child process causes side-effects for the parent sometimes (notably, bash job control and sudo).  Current workarounds exist (resuming session/group process IDs, freezing parent before child for bash/sudo), but a more general solution is needed.

**Possible approach**: Always freeze the parent process before suspending a child (possibly recursively, but never freezing PID 1).  Needs more research and testing.

## Medium Priority

### Swap product threshold refactoring

Currently `check_swap_threshold` normalizes inside the product and compares to 1.0:

```python
swap_product = (combined_in / eff_threshold) * (combined_out / eff_threshold)
ret = swap_product * psi_weight > 1.0
```

The actual trigger condition is therefore `combined_in * combined_out * psi_weight > eff_threshold²`,
which is non-obvious. A cleaner formulation would remove `eff_threshold` from the product and compare
directly to a threshold value:

```python
swap_product = combined_in * combined_out
ret = swap_product * psi_weight > threshold
```

This makes the trigger level explicit in the comparison rather than hiding it in two denominator divisions.
The default `threshold` would change from 4 to something like `(4 * 8)²  = 1024` (eff_threshold² for unknown storage),
which is a breaking change in config semantics and needs a migration note.

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
[
    "sshd",
    "bash",
    "xinit",
    "X",
    "spectrwm",
    "screen",
    "SCREEN",
    "mutt",
    "ssh",
    "xterm",
    "rxvt",
    "urxvt",
    "Xorg.bin",
    "Xorg",
    "systemd-journal",
]
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
