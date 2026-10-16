# Incident 2026-10-16: runaway recursive script, OOM storm, thrash-protect crash

A shell script that kept running itself filled memory with bash processes.  The
kernel OOM killer killed 54 unrelated processes in 25 minutes without touching
the script.  Thrash-protect used most of a CPU core throughout, suspended the
user's systemd manager, and finally crashed.  Nobody was at the keyboard; the
box was rebooted by hand at 12:24.

Host: archlinux laptop, kernel 7.2.8-arch1-2, 8 cores, 30 GiB RAM, 13 GiB swap
on an LVM partition, zswap enabled.  Running a v1.2 development build.

## What ate the memory

`bash claude-state-commit --classify ...`, a script under development, called
itself.  Every instance started the next one and waited for it, so the processes
formed a single linear chain: at 11:50 atop saw 54 872 of them, each the only
child of the previous one.  Five minutes later there were 75 000.  The count
stopped growing at about 82 000, which was the `TasksMax` of the user slice.  Each
process held 4.5–6 MB.  On top of that, the page tables alone reached 3.1 GB.

The kernel's OOM reports listed only about 1000 of them, because journald
dropped most of the lines of each dump.  atop's raw log has the full picture.

## Why the kernel did not help

* `TasksMax` on `user-N.slice` defaults to 33 % of `threads-max`, here 82 839.
  `session-N.scope` and `user@N.service` have no limit.  The chain was killed
  by nothing until it hit that limit.
* The OOM killer scores single processes.  Each bash was small and had
  `oom_score_adj` 0, while the containers, browsers and Electron apps run with
  100–300.  So the kernel killed java containers, mariadb, httpd, nginx, chrome,
  firefox tabs, syncthing and finally `(sd-pam)`, but never a bash.

## What thrash-protect did

| time         | event |
|--------------|-------|
| 11:45:22     | OOM predictor fires ("exhaustion in 47 s", soon "1 s") |
| 11:45–12:12  | 116 selections and 121 distinct pids frozen: java containers, browsers, swaybg, gvfsd, upowerd, syncthing.  32 of the pids belonged to the chain |
| 12:02:03     | PageFaultingProcessSelector picks `systemd --user`.  Its cgroup is `user@N.service/init.scope`, a `.scope` under `user@`, so the whole cgroup is frozen |
| 12:11:51     | That cgroup has vanished.  The unfreeze fails with ENOENT and the item is put back, twice a second, ahead of everything else |
| 12:12:18     | `RecursionError` in `checkParents()`, which walks up through bash parents recursively; the daemon exits and systemd restarts it |

atop puts thrash-protect at 0 % CPU before 11:45 and at 55–218 % (median about
85 %) from then until the crash.  Each selector reads every `/proc/<pid>/stat`.
With 55 000–75 000 processes, every scan was a big job, and several ran per tick.

## Fixes

1. `checkParents()` walks the parents in a loop, guards against cycles, and
   stops after 32 shells.
2. Init and every process named `systemd` are never selected, and `init.scope`
   is never frozen as a cgroup.
3. A frozen cgroup that has vanished is dropped instead of being retried.
4. New `ForkBombProcessSelector`, run before the other selectors.  It groups
   processes into families: a child belongs to its parent's family when both
   have the same name, and same-named orphans share a family per subreaper.  A
   family of `--fork-bomb-threshold` (default 300) or more members that has
   grown by a tenth of that since the previous scan is treated as a fork bomb.
   A steady pool of prefork workers does not grow, and is left alone.
   - Its leaves, the newest processes with no children in the family, are
     frozen, at most 64 per tick.
   - Whatever they forked since the scan is stopped too.  This is race-free,
     because a stopped process cannot fork, and is capped at 256.
   - The whole family gets `oom_score_adj` 1000, which is not reverted on
     resume, so that if the kernel OOM killer runs, it kills the fork bomb and
     not the bystanders.
   - A cheap gate on the process count skips the scan unless the box has grown
     by half a threshold since the last scan that acted on nothing.
   - The pacing sleeps between the pids of a frozen item only apply to short
     job-control chains, so freezing and resuming 64 pids does not block the
     loop.

The largest same-name family on the author's desktop at the time was 11
chrome processes.  Prefork servers (apache with a few hundred workers, postgres
with many connections) can come close to the default threshold.

## Not addressed

* A classic wide fork bomb (`:(){ :|:& };:`) can outrun freezing 64 processes
  per tick.  `TasksMax=` on the user slice and the session scopes is the real
  defence against it.  A few thousand is plenty for a desktop user.
* Scanning `/proc` with tens of thousands of processes is still expensive.  Once
  the fork bomb is frozen it stops growing, but the other selectors still scan.
