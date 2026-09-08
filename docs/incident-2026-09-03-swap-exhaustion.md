# Incident 2026-09-03: total freeze with swap exhausted

The box froze hard — no cursor, no keyboard, no VT switch — until the OOM killer
reaped a container.  Thrash-protect was running and active throughout, and did
not help.  This document records the measurements, what the tool actually did,
and which of its behaviours turned out to be defects as opposed to working as
designed.

Host: archlinux laptop, kernel 7.1.9-arch1-2, 8 cores, 30.7 GiB RAM, 8 GiB swap
on nvme (LVM), zswap enabled.  Running packaged **1.1.2** with default settings
apart from `/etc/thrash-protect.json`.  `systemd-oomd` and `earlyoom` inactive.

## Timeline

`atop` 30 s samples.  Kernel OOM at 23:25:23.  Last column is freeze events from
`/var/log/thrash-protect.log` in that minute.

```
time      free   avail  SWPfree   pgin    swout zswout memsome memfull iofull  avg1  freezes
23:15:01  285.7M  1.4G    0.0M   126196     0      0      0%      0%     ~1%   1.91    1
23:19:01  247.1M  1.3G    0.0M   102431    12      0      0%      0%     ~1%   3.97    1
23:22:01  304.7M  1.4G    0.0M   133046    12     66      0%      0%      1%   2.62    1
23:23:01  247.7M  1.3G    0.0M   261285     0      0      0%      0%      5%   3.87    8
23:24:01  224.0M 973.8M   0.2M   701086    70      0      3%      3%      6%   4.10   12
23:24:31  255.3M  1.1G    0.1M  2839509    82     68     35%     31%     13%  11.19
23:25:01  234.3M 757.9M   0.2M  1148925    57      0     86%     79%      2%  76.00   10
23:25:31    1.0G  2.7G    3.9M   373576   990  12373     71%     70%      0% 261.37        <- 2 oomkills
23:26:01  302.2M  2.3G    0.0M    90547   128   3633      0%      0%      0% 163.40   26
23:27:01  385.2M  1.7G   23.0M   337518  2968  14181      1%      1%      4%  64.69    7
```

Swap had been **100 % full for at least ten minutes** before the collapse.  At
23:24:31 the box pulled in **2 839 509 pages in 30 s — 11.6 GiB, ~390 MB/s** while
writing 82 pages to swap.  Effectively all of that traffic is file-backed refault:
executable text and mmap'd libraries evicted and immediately faulted back.  That
is what a frozen cursor looks like from the kernel's side.

The OOM killer took electron pid 602936 (65 MB anon — a useless kill) and then
java pid 603458, **bedework's JBoss** at 2.4 G RSS, in a podman container.
Recovery was immediate: `pgin` fell from 1.1 M to 90 k and memory PSI to 0 %.

## What thrash-protect did

66 freeze/unfreeze cycles between 23:22 and 23:28.

```
holds:     n=66   min 0.25s   median 1.01s   p90 7.21s   max 13.53s
victims:   41x 536227 (zimbra java, -Xmx256m)      7x 2736924 (electron renderer)
            5x 884    4x 1048805    3x 2738229    3x 1816368 (chrome, 1.8G)
            1x 616095   1x 603384    1x 602936
```

So the tool was neither idle nor blind.  It triggered, it escalated with the
event, and it froze things.  Three observations, of which only two are defects.

### Short, repeated freezes are working as designed

Median hold of one second, and the same process picked over and over, look
alarming but are the intended behaviour.  The algorithm is deliberately
incremental: if the box looks clogged, stop something; if it still looks clogged,
stop something else; if it looks fine, resume one.  Occasional half-second
freezes — even of the same process repeatedly — are acceptable, and are
themselves the signal that the box is short of memory.  (On this laptop they
raise a warning symbol in waybar; on a server the thrash-protect log is the first
place to look when judging whether a box has enough RAM.)

What the algorithm presumes is that the meters have enough resolution to say
whether the last action helped.  A five-minute load average is useless for that,
and PSI is not much better.  That limitation is the root of most of what follows.

### Defect 1: an OOM-driven freeze is released on the next tick

The unfreeze criterion in the main loop was `cooldown_counter`, which is written
by `check_swap_threshold` from swap traffic and bumped by `check_delay()` on a
timer alert.  Neither of those measures memory headroom:

```python
elif not current.cooldown_counter:          # 1.1.2; now guarded by may_unfreeze()
    current.unfrozen_pid = self.unfreeze_something()
```

A freeze ordered by the OOM predictor therefore had nothing holding it down.  When swap is
full there is no swap traffic to count, the counter is already zero, and the
process is resumed on the very next tick *because the box is not thrashing* —
which is exactly the situation it was frozen for.  `memory_predictor.reset()`
then guarantees the predictor needs two fresh observations before it can ask
again.

Stated as a rule: **if the previous stop was done because of rapid memory
consumption, it must not be resumed on the next tick just because the box is not
thrashing.**  Fixed — see "Fixes" below.

### Defect 2: a repeat offender does not stay down longer

The design rule is that a process which causes the box to clog again right after
being resumed should be stopped again and held *longer than last time*.  The
repeat-offender blacklist implemented the first half but not the second:
`FreezeBlacklist.add()` reset `skip_remaining` to `max_skip_count` on every
re-offence, so the hold never grew.  Fixed — see "Fixes" below.

### Not a defect, but worth knowing: the detector is blind while swap is full

The busiest minute of the whole event is 23:26 — 26 freezes, *after* the OOM
killer had already run.  The `swout`/`zswout` columns show why: swap traffic was
~0 for the entire collapse and only resumed once the reaper had freed enough
anonymous memory for the kernel to write to swap again.

`swap_product` is a product of swap-in and swap-out deltas.  A full swap device
drives both towards zero, so the primary signal is at its weakest exactly when
the machine is least recoverable, and it recovers its strength at the moment the
emergency ends.  The OOM predictor is meant to cover that gap, and whether it
did on 1.1.2 cannot be established from these logs (see below).  Whether the
right answer is a better signal, or accepting that
an out-of-swap box is the OOM killer's problem and not thrash-protect's, is an
open question — recorded in [TODO.md](TODO.md).

## What the OOM predictor did on 1.1.2 is unknown — retracted

An earlier draft of this document concluded that the predictor never fired on
1.1.2, on the grounds that no `OOM protection: memory exhaustion predicted` line
appears anywhere in the eleven days of journal spanning three OOM events, and
asserted that the line "is logged at INFO and is not gated behind diagnostic
logging, so its absence is real".

**That was wrong.**  `logging.root.setLevel(logging.INFO)` runs only under
`diagnostic_logging`, the module never calls `logging.basicConfig()`, and
`diagnostic_logging` defaulted to false — so the root logger sat at Python's
default WARNING and *every* `logging.info` in the program was discarded.  The
`ERROR` lines that did reach the journal are consistent with exactly that.  The
absence of the line is no evidence about the predictor at all, and the "123
predictions in 2.9 quiet days" comparison below is against the build where
diagnostics were on — so the log level alone accounts for the whole observation.
`/var/log/thrash-protect.log` does not record *why* a freeze happened, so the 66
freezes cannot be attributed either way.

What this does establish is a real defect, and a worse one than the retracted
claim: **a root daemon whose most important INFO message is unreachable unless
`--diagnostic` is on.**  Recorded in [TODO.md](TODO.md).

The reasoning below stands on its own — it is about the predictor's arithmetic
against the measured memory curve, not about log lines:

`available = MemAvailable + SwapFree * swap_weight`; with `SwapFree` at zero it
reduces to `MemAvailable`, which sat flat at 1.3–1.5 G for ten minutes and then
oscillated (973.8M → 1.1G → 757.9M).  Every observation scale exits on
`if available >= past_available: continue`.  A box pinned against the floor and
thrashing looks perfectly stable to a rate-based predictor, because the kernel
*keeps* `MemAvailable` flat by evicting the page cache — which is the damage.
There is no absolute-level trigger to catch it: `oom_low_pct` defaults to 100.0,
which disables the level check entirely.

On the gated build installed afterwards — with diagnostics on — the predictor
produced 123 predictions in 2.9 quiet days and accounted for roughly three
quarters of all freezes.  Since the log level changed at the same time as the
code, that number says what the predictor does *now*, and nothing about 1.1.2.

## Follow-up measurements, 2026-09-04 to 09-06

`diagnostic_logging` was enabled and the daemon replaced with a build of the
gated trigger (the fix from
[incident-2026-08-09-io-starvation.md](incident-2026-08-09-io-starvation.md)).
2.86 M diagnostic lines over 476 875 sampled intervals, no incident in the
window.  Loop rate held at the nominal 2/s.

The gates from that fix, measured live rather than argued about:

```
swap-detector triggers                            38   (0.008% of intervals)
  swap_product > 1.0 unaided                      31   (81.6%)
  needed PSI amplification to cross 1.0            7   (18.4%)
  raw swap below psi_swap_floor=2 either way       0   (0.0%)
  psi_weight > 1.0 after anon-fraction damping    22   (57.9%)
```

The swap floor blocked nothing and PSI amplification survived gating on 22 of 38
triggers, so the gates are not strangling the detector.  The caveat is that these
are quiet days: anon-fraction damping only bites when refaults go file-dominated,
which is the storm condition that has not recurred.

This window also supplies the multi-core io-`full` measurement that TODO.md was
waiting for.  During the collapse `iofull` read 5 %, 6 %, 13 %, then **2 %** and
**0 %** at the two worst samples, while `iosome` reached 61–76 % — on an 8-core
box, with tasks stalled on memory rather than IO.  Against the 1-vCPU Aug-09 host
where `iofull` was 84.8 %, that is a clean separation, and it means the veto's
choice of `full` is defensible: it did not fire here, and it should not have.

## Fixes

**1. Hold an OOM-driven freeze.** ✅ *implemented*
`ThrashProtectState.note_oom_freeze()` arms a counter when the OOM predictor
orders a freeze; `may_unfreeze()` requires both that counter and the swap-based
cooldown to be clear.  `--oom-hold-ticks`, default 4 (2 s at the default
interval).  Deliberately modest: it is not an attempt to decide how long the
process should stay down, only to stop the swap-based all-clear from answering a
question it was never measuring.  A repeat offender earns a longer hold through
fix 2 instead.

**2. Escalate the hold for repeat offenders.** ✅ *implemented*
`FreezeBlacklist.add()` now adds `max_skip_count` to the offender's allowance on
each re-offence instead of resetting it, capped at
`max_skip_count * --blacklist-escalation-cap` (default 8).  Escalation survives
partial consumption of the current allowance; `expire()` is what clears it, so
behaving for a full expiry window is what earns a fresh start.

**3. Report `anon_fraction` in the diagnostic line.** ✅ *implemented*
It is the gate that decides whether memory PSI may amplify at all, and it was
reported only via `logging.debug` — invisible with `--diagnostic` on and
`--debug` off, which is the configuration recommended for capturing an incident.
`io_vetoed` was already there.

**4. Give the daemon scheduling priority.** ✅ *implemented*
`Nice=-15` and `OOMScoreAdjust=-900` in the packaged systemd unit, plus restart
limits so a capability failure degrades to "restarting" rather than "gave up".
`mlockall()` already keeps its pages resident, but nothing protected its CPU
share, and the load average reached 261 on 8 cores.  Only the CPU half: an
earlier draft also set `IOSchedulingClass=realtime`, which does nothing here —
the daemon's IO is procfs reads plus a buffered log append that kernel writeback
flushes on its own priority, and `/sys/block/nvme0n1/queue/scheduler` is
`[none]`, so there is no scheduler to honour ioprio in the first place.

**5. Victim selection after a calm period.** ❌ *open* — see [TODO.md](TODO.md).
Preferring the most recently resumed process is right when the box clogs again
immediately after a resume, but the preference does not decay, so after a calm
period the tool keeps returning to the same process instead of choosing afresh.
Fairness is not the concern; the concern is that on 2026-09-03 the 256 MB-capped
zimbra JVM took 62 % of the freezes while bedework's 2.4 G JBoss — the process
the kernel eventually had to kill — was frozen once.

**6. Keeping the terminal alive.** ❌ *open* — see [TODO.md](TODO.md).
`sway` and `foot` are both whitelisted and both froze anyway.  The whitelist only
lowers a process's score for being SIGSTOPped; it does nothing to keep its pages
resident.

## Standing condition

`vmcom` 112.9 G against `vmlim` 23.3 G, and swap 100 % full continuously — still
7.4/8.0 G an hour after the event.  ~30 `claude` processes at ~300 MB each, five
`java` across zimbra and bedework containers, chrome, firefox.

Worth stating plainly, because no amount of detector tuning addresses it:
**thrash-protect's strategy presumes somewhere to put the pages.**  Freezing helps
because the victim's anonymous memory can then be swapped out.  At 100 % swap
there is nowhere for it to go, so even a correct, long, well-targeted freeze
reclaims only the victim's file pages.  On this box, more swap is the fix that
would have prevented the freeze.


---

# Second occurrence: 2026-09-07, captured with diagnostics

Same failure mode, 82 seconds instead of three minutes, and this time with
`diagnostic_logging` on and the gated build running.  Swap was again 100 % full
throughout.  `MemAvailable` fell 2.2 G -> 1.9 G -> 1.8 G -> **583 M**, the OOM
killer took electron pid 2736924 (110 MB - another useless kill) and chrome pid
5956 (1.14 G) at 18:28:40, and `vmcom` dropped from 123.7 G to 57.6 G on the
instant.

**sway was not stopped by thrash-protect.**  Neither `sway`, `swaybg`, `swaybar`,
`swayidle` nor `swaync` appears anywhere in `/var/log/thrash-protect.log` for
this event, or at all since 2026-07-05.  The desktop went unresponsive for the
same reason as on 09-03: page eviction and refault, not `SIGSTOP`.  The whitelist
did its job, and the job it does is not the one that matters here - see the
page-residency item in [TODO.md](TODO.md).

## What the diagnostics show

6 swap-detector triggers and 13 OOM predictions across the window.  Two distinct
failures, one already fixed and one new.

### The mass release at 18:27:35, which fix 1 addresses directly

```
18:27:33  OOM-PREDICT eta=3s      -> froze 2736924
18:27:34  SWAP-TRIGGER  14.1612   -> froze 5956, 1411975+2542644, 2736924
18:27:35  (nothing)               -> unfroze 1411975, 2542644
18:27:36  (nothing)               -> unfroze 5956, 2736924
18:27:39  OOM-PREDICT eta=3s      -> froze 2736924 again ...
```

The predictor said memory exhaustion in **three seconds**.  Two seconds later,
with no trigger on that tick, the swap-based all-clear released all four
processes - including a login shell and a `claude` process frozen as a group -
and the accumulation had to start again from nothing.  This is precisely the
defect fix 1 was written for, observed live.

### The 51-second silence, which is new and worse

From 18:27:48 to 18:28:39 there was no trigger and no prediction at all, while
`MemAvailable` fell to 583 M.  The reason is not a badly chosen threshold:

```
loop iterations in those 51 seconds:              19   (0.37/s, against 2/s nominal)
OOM predictor scale=0.0833 "no past observation": 19   of 19
OOM predictor scale=1.0000 "no past observation": 18   of 19
OOM predictor scale=0.0167 "no past observation": 17   of 19
```

**The daemon was running 5x slower than configured, and that alone blinded the
predictor.**  `_find_observation_at` accepts a sample only within +/-window/2 of
the target age, and at 0.37 iterations per second the samples are ~2.7 s apart.
Note which way round the table runs: the 5 s scale failed on *every* one of the
19 iterations, while the 1 s scale — the one whose +/-0.5 s tolerance looks
arithmetically impossible at that spacing — is the only one that ever produced a
projection, twice.  So the mechanism is not simply "spacing exceeds tolerance";
`memory_predictor.reset()` empties the deque after every OOM-driven freeze, and
which scale can find a pair afterwards depends on how the surviving samples fall.
The measured fact is that all three scales were starved for the whole descent.
The two failures feed each other: a starved loop produces no usable observation
pair, so nothing is predicted, so nothing is frozen, so the loop stays starved.

This is the measurement fix 4 was guessing at.  It is now installed.

Two smaller things fall out of the same window:

* When observations finally did line up at 18:28:39, the fast scale computed
  `decline=50284kB/s eta=14s` and did **not** trigger, because its horizon is
  10 s.  Four seconds too strict, one second before the OOM killer ran.
* The predictor logged `available=998080 total=48955644 (2.0%)`.  Two per cent
  of resources remaining raises no alarm of its own, because `oom_low_pct`
  defaults to 100.0 and disables the level check entirely.

### Freezing does not reduce RSS

Both processes the OOM killer chose were **already frozen by thrash-protect when
it killed them** - chrome pid 5956 had been suspended for 58 seconds, electron
pid 2736924 for 41.  `SIGSTOP` stops a process running; it does not release its
anonymous pages, and a stopped process generates no reclaim pressure of its own
to make itself a preferential victim.

With swap at 100 % there is nowhere for those pages to go, so the suspension
bought no memory at all - only the time the process spent not allocating more.
That is the "strategy presumes somewhere to put the pages" point from the first
incident, now with a receipt: 1.14 G held frozen for 58 s and killed anyway.

## Answering "could it have handled this better"

In order of how much each would have mattered here:

1. **Scheduling priority** - the 5x loop slowdown is the proximate cause of the
   51-second silence.  Installed: `Nice=-15`, `OOMScoreAdjust=-900` and restart
   limits.  `Nice` is the whole of it; see fix 4 above for why the IO half of an
   earlier draft was dropped rather than kept.
2. **Predictor observation tolerance** - a tolerance defined as a fraction of the
   *nominal* window breaks when the actual tick rate collapses under load, which
   is exactly when the predictor is needed.  Open, see [TODO.md](TODO.md).
3. **Not releasing OOM-driven freezes on a swap all-clear** - cost this event one
   full restart of the accumulation.  Installed: `--oom-hold-ticks`.
4. **Escalating repeat-offender holds** - pid 2736924 was frozen 642 times over
   the day and 8 times in this event alone.  Installed:
   `--blacklist-escalation-cap`.
5. **A level-sensitive predictor** - 2.0 % available should not be silent.  Open.

None of these change the standing condition: 8 GiB of swap, permanently full, on
a box committing over 120 G.
