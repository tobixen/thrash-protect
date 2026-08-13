# Incident 2026-08-09: false positive under IO starvation

A whole-disk `grep` on a small VM caused thrash-protect to suspend 6–11 innocent
processes continuously for over two hours, while never catching the process
responsible.  There was no thrashing: swap-in was **zero** and the major fault
rate was ~1.4/s.  This document records the measurements, the trigger analysis,
and the fixes.  Fixes 1–3 are implemented; 4–7 are still proposals.

Host: broxbox05, Ubuntu jammy, kernel 5.15.0-76-generic, 1 vCPU, 1963 MB RAM,
3906 MB swap (1297 MB in use), 80 GB `QEMU HARDDISK` on `mq-deadline`.
Running the packaged **thrash-protect 1.0.1**.  Defaults throughout — there was
no `/etc/thrash-protect.yaml` on the host.

## What triggered it

A user-run script, `/tmp/scan2022.sh`, searching every file on the box for a
tracker-blob pattern:

```
find / ... -type f -print0 | xargs -0 -r -P4 -n100 grep -lasE 'BR0022[01][0-9][0-3][0-9][AV]'
```

Timeline from the script's own status file:

```
START       2026-08-09T11:42:33
PASS1-DONE  2026-08-09T13:36:37  hits=0
PASS2-DONE  2026-08-09T13:54:37  hits=0     (decompress-and-grep pass)
ALL-DONE    2026-08-09T13:54:37
```

Two hours twelve minutes.  It completed on its own; nothing was killed.

## Measurements

Taken live during the event, except where noted as reconstructed from `atop`
history (`/var/log/atop/atop_20260809`).

### There was no thrashing

Over a 5-second window at peak:

| `/proc/vmstat` counter | delta / 5 s |
|---|---|
| `pswpin`  | **0** |
| `pswpout` | **6 pages** |
| `pgmajfault` | **7** (1.4/s) |

1.4 major faults per second is idle-box noise.  `grep` uses `read()`, not
`mmap`, so it generates page-cache traffic (`pgpgin` ≈ 13.8 MB/s) and
essentially no major faults of its own.

### The disk was saturated the entire time

```
util %      : 100.3      read IOPS : 160.6      r_await : 13.81 ms
in-flight   : 2
```

From `atop`, `sda` busy% by sample (reconstructed):

```
time       readMB/s   busy%
11:41:01       0.03     1.0     <- before the scan
11:44:01      17.35    99.8     <- scan starts, pinned immediately
12:11:01       2.56    99.9
12:20:01       1.30    100.0
12:29:01      20.62    99.8
13:02:01      22.90    99.9
```

`busy%` sat at 99.7–100.0% from the first sample after the scan started until it
finished.  Throughput swung 26× (1.03 → 27.14 MB/s) at *constant* 100% busy —
the variation tracks request size and locality (the troughs are directories of
tiny files: `wireshark/radius/dictionary.*`, `dist-info/RECORD`, `__pycache__`),
not process concurrency.

### PSI

```
io:     some avg10=98.66   full avg10=84.82
memory: some avg10=55.53   full avg10=46.56
cpu:    some avg10=3.76    full avg10=0.00
```

Memory PSI at 55% is **not** swap pressure here.  `/proc/pressure/memory` counts
stalls in reclaim and page-cache refault.  A whole-disk scan is a reclaim
generator by construction: it streams ~71 GB through ~1.3 GB of cache, so every
other process's file-backed pages are evicted and refaulted from an already
saturated disk.  High memory PSI, no thrashing.

Lifetime `workingset` counters on the host show the same imbalance:

```
workingset_refault_file  251074206
workingset_refault_anon   26729497     (9.4:1 file-dominated)
```

### What thrash-protect did

Sampling `ps` once a second for 25 seconds, counting `T`-state processes:

```
 1: count=6   munin-node postgres bash puppet beam.smp nrpe
 3: count=8   munin-node postgres bash puppet beam.smp beam.smp nrpe postgres
10: count=10  munin-node postgres postgres bash puppet beam.smp beam.smp nrpe postgres postgres
19: count=11  munin-node postgres postgres bash puppet spamd beam.smp beam.smp postgres postgres postgres
25: count=7   munin-node postgres postgres bash puppet beam.smp postgres
```

Continuously 6–11 processes frozen, membership churning every 1–2 seconds:
postgres backends, spamd, beam.smp, munin-node, nrpe, puppet.  The mail server,
the database and the monitoring — none of which were causing the problem.

Meanwhile the log filled with hundreds of:

```
ERROR:root:Could not fetch process user information, the process is probably gone
```

That is victim selection **repeatedly picking the greps and losing the race**.
`xargs -n100` respawns a fresh `grep` every 100 files, so the top IO consumer is
a moving target that exits before it can be signalled.  thrash-protect fell
through to what it could catch.  The batch job it was trying to stop ran
unimpeded for the full 2h12m.

### CPU was never the constraint

```
 r  b   si   so     bi   bo   in   cs  us sy id wa st
 0  5    7    7    102   92    0    2   6  2 91  1  0
 1  4    0    0  15550   66  348  696   4  5  0 91  0
 0  6   24  118  14800  134  493  964   3  5  0 92  0
```

Runqueue 0–1, `wa` 82–93%, `st` 0.  Context switch rate from `atop` (per second,
reconstructed): ~250–450 before the scan, 600–1300 during, peaking at 1314.
Both figures are negligible on any modern CPU, and the box had ~90% of its CPU
sitting idle in iowait throughout.

## Why it fired

`check_swap_threshold` multiplies the swap signal by a PSI weight with no floor
on the swap signal itself:

```python
psi_weight = 1.0 + psi_some / config.psi_threshold
ret = swap_product * psi_weight > 1.0
```

With `psi_threshold` = 5.0 (default) and the observed `memory.some avg10` of
55.53, `psi_weight` = **12.1**.

**As it ran (1.0.1)**, with `swap_page_threshold` = 4:

```
swap_product = ((in + 0.1) / 4) * ((out + 0.1) / 4)
```

Trigger needs `(in+0.1)(out+0.1) > 1.32`, i.e. **`in=1, out=2` → 2.31 → freeze**.

**As currently shipped (1.1.2)** the arithmetic changed but the outcome did not.
`pswp_weight` is auto-set from storage type (HDD=128, SSD=8) and
`effective_threshold = swap_page_threshold * pswp_weight`.  broxbox05's disk
reports `ROTA=1` (`QEMU HARDDISK`), so it gets HDD tuning: weight 128,
threshold 4, `effective_threshold` = 512.  For `in=1, out=2` (disk swap, no
zswap):

```
combined_in  = 1*128 + 0.1 = 128.1
combined_out = 2*128 + 0.1 = 256.1
swap_product = (128.1/512) * (256.1/512) = 0.125
final        = 0.125 * 12.1 = 1.52  > 1.0  -> freeze
```

*(computed against 1.1.2, not observed — the host ran 1.0.1.)*

So **1 page in and 2 pages out per sample — about 8 KB/s of swap traffic — is
enough to convict**, provided memory PSI is high.  On a host with 1.3 GB already
resident in swap that is baseline noise.

Two aggravating factors:

**`ROTA` is meaningless under virtualisation.**  Had the disk been detected as
SSD (weight 8, threshold 64), `effective_threshold` would be identical (512) but
each disk page would count 16× less, making the product 256× smaller — no
trigger.  The HDD/SSD split is doing real work here and it is keyed off a bit
the hypervisor makes up.

**`check_delay` is a positive feedback loop under IO starvation.**
`max_acceptable_time_delta` is `interval/8` = 62.5 ms.  On a 100%-busy disk
thrash-protect's own loop overruns that constantly, each overrun does
`cooldown_counter += 2`, and `get_sleep_interval()` is
`interval / (cooldown_counter + 1)` — so it polls *faster*, and each poll forks
`ps`.  It responds to disk saturation by issuing more disk IO.  The
`max_acceptable_time_delta *= 1.1` autotune only runs in the not-busy branch, so
it cannot unwind while the pressure lasts.

## Would suspending processes have helped anyway?

There is a real argument that it might: Denning's working-set/load-control
result.  If the sum of active working sets exceeds available cache, processes
evict each other's pages, refault rates explode, the disk saturates and
throughput collapses *even with an idle CPU*.  The cure is to reduce the
multiprogramming level until the resident working sets fit.  That is exactly
what suspension-based load control is for, and it is why this project exists.

It did not apply here, for three reasons:

1. **The evictor was not among the suspended.**  In classic thrashing the
   victims collectively *are* the cause, so suspending some genuinely reduces
   demand.  Here one process generated unbounded eviction pressure and was never
   a viable candidate.  Freezing postgres removed none of the grep's demand.

2. **The quantum was far too short.**  The frozen set churned every 1–2 seconds.
   Refilling a working set on a ~300 IOPS device takes tens of seconds.  Freezing
   something briefly and releasing it pays the full latency cost and collects
   none of the locality benefit.

3. **Streaming reads defeat recency-based eviction by construction.**  The
   grep's reuse distance is infinite — it never re-reads anything.  No policy
   that ranks pages by recency can distinguish "recently used and will be used
   again" from "recently used and never again".  Linux's active/inactive split
   with refault detection is meant to handle exactly this, and it lost: `find /`
   over hundreds of thousands of files also thrashes dentry/inode slab
   (`SReclaimable` was only 131 MB), and on a 2 GB host the margin between the
   real working set and total cache is thin.

Under a hard IO ceiling the throughput argument also fails: the device was at
100% busy from the first sample, so suspension could not increase utilisation.
It could only redistribute a fixed budget — and it redistributed it *from* the
latency-sensitive services *to* the batch job.

## Fixes

Items 1–3 are **implemented**; see the `[Unreleased]` CHANGELOG section.
Items 4–7 remain proposals.  Items 4 and 5 overlap with existing TODO entries;
see cross-references.

**1. Trust memory PSI only insofar as it is anon-driven.** ✅ *implemented*
`workingset_refault_anon` / `workingset_refault_file` (Linux 5.9+) are read each
interval, and `psi_weight` is scaled by the anon share of refaults:

```python
psi_weight = 1.0 + (psi_some / psi_threshold) * anon_fraction
```

During the incident file refaults dominated by better than 9:1, collapsing the
weight from 12.1 to roughly 2.1.  On kernels without the split counters the
delta is zero, the fraction stays 1.0, and behaviour is unchanged.

*This differs from the original proposal, which was to make
`workingset_refault_anon` the primary thrash signal outright.  That turned out
to be the wrong framing: the existing signal is `(pswpin, pswpout, zswpin,
zswpout)`, which is **already** anon-only, so replacing it buys nothing.  The
contamination entered through the amplifier — memory PSI, which counts file
refaults — so that is where the anon/file split belongs.*

**2. Gate the PSI amplifier on a real swap floor.** ✅ *implemented*
`--psi-swap-floor` (default 2 pages) is the minimum swap traffic required in
**each** direction before `psi_weight` may be applied at all.  The incident
sample was 1 page in, 2 out.

Note that the `0.1` epsilon is *not* the problem in 1.1.2 — with `pswp_weight`
at 128 it is negligible — so this is about the amplifier, not the epsilon.

Worth recording, because it bounds how large the floor may usefully be:
`pswp_weight` cancels out of `swap_product` for disk-only swap, since
`combined = Δ·weight` and `effective_threshold = threshold·weight`.  The
unamplified trigger therefore reduces to `Δin · Δout > swap_page_threshold²`,
i.e. ~4 pages per direction at the default.  A floor at or above 4 would make
the amplifier dead code, which is why it defaults to 2 and why fix 1 has to do
the substantive work.

**3. Do not treat IO starvation as thrashing.** ✅ *implemented*
`/proc/pressure/io` is now read alongside `/proc/pressure/memory`.  When
`io.full avg10` exceeds `--io-pressure-threshold` (default 50%) **and** the
unamplified `swap_product` would not have triggered on its own, the check
returns False.  `--no-io-pressure-veto` disables it.

*Two deviations from the original proposal.  First, the signal is io PSI rather
than `/proc/diskstats` busy%: PSI is already a percentage and needs no
cross-interval delta sampling, so it costs one file read and no new state.
Second, the proposal was to subtract IO pressure from memory pressure — that
was rejected as unsafe, because genuine thrashing saturates the disk too (the
swap IO **is** the disk load), so subtraction would suppress exactly the case
the tool exists for.  Gating on "the raw swap signal would not have triggered
anyway" is strictly safer: it cannot suppress real thrashing at any disk
utilisation.*

**4. Roll victim accounting up to the process group.**
Aggregate `/proc/<pid>/io:read_bytes` and fault counters by pgid/session/cgroup
rather than pid, and signal with `kill(-pgid, SIGSTOP)` or the cgroup freezer.
This is what would have actually caught `scan2022.sh`, and it removes the
"process is probably gone" log spam as a side effect.
*Closely related to TODO "Fork Bomb Protection" (GitHub #39) and "Parent Process
Freezing / Job Control" (GitHub #12) — the same rollup serves all three.*

**5. Floor the poll interval; give freezes a minimum quantum.**
Put a floor under `get_sleep_interval()` so `cooldown_counter` cannot drive the
poll rate up under IO starvation, and replace the `ps` subprocess with direct
`/proc` reads.  When suspension *is* correct, hold it long enough to buy
locality — seconds, not sub-second.

**6. Prefer a reversible action for a detected streamer.**
SIGSTOPping postgres is brutal.  For a process group identified as a streaming
reader, `ionice -c3 -p` or writing its cgroup `io.max` is proportionate.
*See also `docs/cgroup-enhancement-ideas.md`.*

**7. Do not key HDD/SSD tuning on `ROTA` alone.**
Under QEMU/KVM the guest sees `ROTA=1` regardless of the backing store.  Fall
back to measured `r_await`, or make the storage type explicitly configurable and
set it from configuration management on virtualised hosts.

### Where load control still belongs

The rule should not be "never act on file refaults".  When the victims
themselves collectively oversubscribe cache — ten postgres backends each running
a large sequential scan, say — Denning applies in full and reducing the
multiprogramming level is exactly right.  Encode this as a **concentration
test** over the interval:

- compute each process group's share of total `read_bytes`;
- if one group exceeds ~50%, demand is concentrated → suspend *that group*, with
  a long quantum;
- if no group dominates, demand is distributed → reduce the multiprogramming
  level among the top readers, round-robin, with quanta measured in tens of
  seconds.

That preserves the load-control behaviour and only stops it firing when there is
a single culprit that should be targeted instead.  It would have made the right
call here: the `xargs` group owned essentially 100% of reads.

## Tests

`tests/test_thrash_protect.py::TestIOStarvationFalsePositive` covers fixes 1–3.
Each blocking test is paired with a control that must still trigger, so the
suite fails if a fix degenerates into simply switching the amplifier off:

| test | asserts |
|---|---|
| `test_psi_not_amplified_below_swap_floor` | the incident sample (1 in, 2 out, PSI 55%) does **not** trigger |
| `test_psi_still_amplifies_above_swap_floor` | 3 pages each way + PSI still does |
| `test_psi_damped_when_refaults_are_file_dominated` | file-dominated refaults do **not** trigger |
| `test_psi_amplified_when_refaults_are_anon_dominated` | anon-dominated refaults do |
| `test_absent_refault_counters_preserve_old_behaviour` | pre-5.9 kernels are unchanged |
| `test_io_pressure_vetoes_trigger_when_swap_is_idle` | saturated disk + idle swap does **not** trigger |
| `test_without_io_pressure_the_same_sample_still_triggers` | control: the veto is what changed the outcome |
| `test_io_pressure_does_not_veto_real_thrashing` | heavy swap on a saturated disk still triggers |

Still to write, for the proposals not yet implemented:

- pgid rollup: a parent respawning short-lived high-IO children → the *parent
  group* must be selected, not a transient child.

## Note for operators

thrash-protect is not an IO throttle, and this class of workload is better
handled by the kernel directly.  Confining a bulk scan to its own cgroup stops
it evicting the host's working set in the first place:

```bash
systemd-run --scope --unit=bulkscan \
  -p MemoryHigh=64M -p MemoryMax=256M \
  -p IOReadBandwidthMax="/dev/sda 8M" \
  ./whole-disk-scan.sh
```

`MemoryHigh` charges the scan's page cache to its own cgroup and reclaims it
there.  Use `IOReadBandwidthMax` rather than `IOWeight` unless BFQ or io.cost is
active.  The `nocache` package (an `LD_PRELOAD` issuing
`posix_fadvise(POSIX_FADV_DONTNEED)`) is a narrower alternative that only covers
reads made by the wrapped binary.
