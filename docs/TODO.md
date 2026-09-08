# TODO List for thrash-protect

Updated 2026-02-12 with GitHub issue cross-references.

Background reading:
[incident-2026-08-09-io-starvation.md](incident-2026-08-09-io-starvation.md) —
a real false positive where IO starvation, not memory shortage, drove the
trigger; several items below come from it.  Fixes 1–3 from that document (PSI
swap floor, anon/file refault weighting, IO-pressure veto) are implemented;
4–7 are still open, and fix 4 is the pgid rollup described under Fork Bomb
Protection below.

[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md) —
the mirror-image case: a hard freeze with swap 100 % full, where the tool
triggered continuously and did not help.  Fixes 1–4 there are implemented;
5 and 6 are the victim-decay and page-residency items below.

## Design principles

Recorded here because several TODO items only make sense against them, and
because a reader coming to the code cold tends to mistake the third one for a
bug.

* If the computer starts looking clogged, stop something.  If it still looks
  clogged, stop something else — and repeat until things look OK.  If it does
  not look clogged any more, resume a process.
* This presumes the meters being probed are accurate, have good resolution, and
  can say more or less immediately whether the last thing the script did made
  the situation better or worse.  A five-minute load average is useless for
  that; **PSI also lacks good enough resolution**.  Most of the hard problems in
  this list trace back to that gap.
* Processes occasionally getting frozen for half a second, and perhaps even the
  same process over and over again, **is acceptable**.  It is also a signal that
  it may be time to look into memory usage and add RAM or swap.  When judging
  whether a server has enough memory, the thrash-protect log is one of the first
  things to look at.  (On a laptop, a warning symbol in waybar when
  thrash-protect is doing something serves the same purpose.)
* Fairness towards the frozen processes was never a design goal.  Hitting the
  same process over and over is unacceptable *if some other process is creating
  havoc in the meantime*; otherwise it is merely unfair, which is not good but
  does not need fixing.
* The algorithm is primarily designed to make thrashing less harmful on a box
  with **sufficient** swap.  Extending it to delay OOM situations was a
  hypothesis, and may turn out to be wrong thinking — perhaps the answer there
  is to let the OOM killer do its job and/or make more swap available.  One
  thing is clear either way: if the previous stop was made because of rapid
  memory consumption, the process must not be resumed on the next tick just
  because the box is not thrashing.

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

- ~~The IO-pressure veto reads io `full`, while memory PSI deliberately reads
  `some`.  Needs a measurement from a multi-core host before changing.~~
  **Measured, no change needed.**  On the 8-core host of
  [incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md),
  during a genuine memory collapse, `iofull` read 5 → 6 → 13 → **2** → **0** %
  while `iosome` reached 61–76 %; tasks were stalled on memory, not on IO.
  Against the 1-vCPU Aug-09 host at `iofull` 84.8 %, `full` separates the two
  cases cleanly and the veto correctly stayed out of the way.  Switching to
  `some` would have vetoed a real emergency.
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

### Victim preference should decay after a calm period

Preferring the most recently resumed process is the right thing to do when the
box clogs again very soon after a resume — that is the whole point of the
repeat-offender blacklist.  But the preference does not decay, and the loop is
self-reinforcing.  The mechanism is `LastFrozenProcessSelector.last_unfrozen_pid`,
which is never cleared except when the process exits: it keeps re-selecting the
same pid, and `FreezeBlacklist.add()` — whose only call site is guarded on that
selector — refreshes the entry on every hit, so the 60 s expiry window never
elapses while the tool keeps choosing it.  (Not `BlacklistProcessSelector`, which
an earlier draft of this item blamed; freezing via that selector never touches
`last_refreshed`.)

Since the allowance now escalates, that loop also ratchets it to the cap on every
round, so the steady state is "the same victim skipped for up to
`--blacklist-max-skip-count` × `--blacklist-escalation-cap` unfreeze cycles,
every round".  Bounded — `_pick_unfreeze_item`'s deadlock guard force-releases
when every frozen item is blacklisted — but a material change to this item's
premise.

Measured consequence, from
[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md):
41 of 66 freezes during the incident went to a zimbra JVM capped at `-Xmx256m`,
which is structurally incapable of being the memory hog, while bedework's 2.4 G
JBoss — the process the kernel eventually had to kill — was frozen once.  On two
subsequent quiet days it was 150 of 163 freezes on a single electron renderer.

Per the design principles above this is not a fairness problem, and repeatedly
hitting one process is acceptable in itself.  It becomes a defect precisely
because another process *was* creating havoc in the meantime.  After a longer
calm period the right move is to choose afresh rather than return to the last
offender.

### Keeping the terminal alive needs page residency, not a whitelist

The `cmd_whitelist` only divides a process's score for being SIGSTOPped.  It
does nothing to keep that process's pages resident, so a whitelisted terminal is
still refaulting its own `.text` on every scheduling quantum once the kernel
starts evicting executable pages.  During
[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md)
`sway` and `foot` were both whitelisted, both stayed unfrozen, and the desktop
was still completely unusable.

If "the sysadmin keeps a working terminal while the box degrades" is a goal —
and it is one of the stated reasons the project exists — then the mechanism has
to be something else.  `memory.min` on a protected cgroup v2 slice is the
purpose-built tool; `MemoryMin=` in a systemd drop-in for the session scope is
the cheap version.  Neither belongs inside the daemon necessarily; documenting
the recipe may be enough.

### The primary signal is weakest when swap is full

`swap_product` multiplies the swap-in delta by the swap-out delta.  A full swap
device drives both towards zero, so detection is at its weakest exactly when the
box is least recoverable — and recovers its strength the moment the OOM killer
frees enough anonymous memory for the kernel to write to swap again.  In
[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md)
the busiest minute of the whole event was the one *after* the reaping.

The OOM predictor is supposed to cover this gap.  Options, none obviously right:
make the predictor level-sensitive as well as rate-sensitive (`oom_low_pct`
defaults to 100.0, which disables the level check entirely); bring in
`workingset_restore_file`, which counts pages that were active when evicted and
so distinguishes working-set thrashing from a streaming read; or accept per the
design principles that an out-of-swap box is the OOM killer's problem and say so
in the documentation.

### OOM predictor observation window vs. the actual tick rate

`MemoryExhaustionPredictor._find_observation_at` accepts an observation only
within +/-window/2 of the target age, where the window derives from the
*configured* interval.  Under load the loop does not run at the configured rate:
during the second occurrence in
[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md)
it managed 19 iterations in 51 seconds - 0.37/s against 2/s nominal - and every
scale reported "no past observation" for the whole descent.  A tolerance that
assumes the nominal rate goes blind exactly when it is needed.

Options: scale the tolerance by the observed inter-tick spacing, or drop the
fixed `_SCALES` in favour of fitting whatever observations actually exist, which
is the direction the class docstring already wonders about.  Note also that the
fast scale's 10 s horizon rejected an `eta=14s` projection one second before the
OOM killer ran, and that `oom_low_pct` defaults to 100.0, so 2.0 % of resources
remaining raises no alarm on its own.

### The Makefile version stamp is shadowed by a stale dist-info

`make install` substitutes `__version__ = "DEVELOPMENT"` with the real version,
and that literal is used whenever `importlib.metadata` raises — which is the
normal case for a standalone install on a box with no `*.dist-info`.  It is
shadowed rather than dead: the module prefers
`importlib.metadata.version("thrash-protect")`.  Any leftover `*.dist-info` therefore answers first:
on the incident host
`/usr/lib/python3.14/site-packages/thrash_protect-1.1.2.dist-info` survived
removal of the distro package, so a standalone install of a branch build still
reported `1.1.2`.  A build whose version cannot be trusted is a bad thing to be
debugging an incident against — and it has a second consequence: `init_config()`
auto-enables diagnostic logging when `__version__` contains `dev`, `alpha`,
`beta`, `rc` or `.dirty`, so a stale dist-info reporting a release version
silently turns the auto-diagnostics *off* on a branch build.  That is the same
bug as the item below.

### Every INFO message is unreachable unless --diagnostic is on

`logging.root.setLevel()` is called in exactly two places, both conditional:
DEBUG under `debug_logging`, INFO under `diagnostic_logging`.  The module never
calls `logging.basicConfig()`, so with both off — the shipped default — the root
logger sits at Python's default WARNING and every `logging.info()` in the program
is discarded.  That includes `OOM protection: memory exhaustion predicted in %.0f
seconds`, which is the single most useful line the daemon emits and the one a
sysadmin would go looking for after a freeze.

Found while writing
[incident-2026-09-03-swap-exhaustion.md](incident-2026-09-03-swap-exhaustion.md),
where it invalidated a conclusion drawn from the *absence* of that line.  It also
means the `--diagnostic` flag conflates two things a user would want separately:
"tell me when you act" and "log every interval's arithmetic".

Fix is a one-liner (`logging.basicConfig(level=logging.INFO)` in `main()`, or an
unconditional `setLevel(INFO)` before the conditionals), but it changes what a
default install writes to the journal, so it wants a deliberate decision about
which lines are INFO and which should drop to DEBUG.  Note the freeze itself is
currently logged at DEBUG (`logging.debug("froze pid %s")`), which is the
opposite of where it belongs.

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

- ✅ Hold OOM-driven freezes past the swap-based all-clear (`--oom-hold-ticks`)
- ✅ Escalating hold for repeat offenders (`--blacklist-escalation-cap`)
- ✅ Scheduling priority for the daemon in the packaged systemd unit
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
