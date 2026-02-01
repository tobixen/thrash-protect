# Cgroup Enhancement Ideas for thrash-protect

This document outlines potential enhancements using Linux cgroup v2 features.

## Implemented

### Cgroup Freezer (v0.x.x)
Use `cgroup.freeze` instead of SIGSTOP for processes in `.scope` cgroups.
- Atomic freezing of all processes in the cgroup
- Can't be bypassed by terminal multiplexers or parent processes
- Fallback to SIGSTOP for systems without cgroup v2

### PSI-based Thrash Detection (v0.x.x)
Use Pressure Stall Information instead of swap page counting.
- More direct measure of memory pressure
- Available since Linux 4.20 (2018)
- Uses `full avg10` metric (% time ALL tasks stalled on memory)
- Configurable threshold (default: 5%)
- Falls back to swap counting if PSI unavailable
- See: `/proc/pressure/memory`

Configuration:
```bash
# Enable/disable PSI (default: enabled if available)
thrash-protect --use-psi
thrash-protect --no-psi

# Set PSI threshold (default: 5.0%)
thrash-protect --psi-threshold 10.0

# Environment variables
THRASH_PROTECT_USE_PSI=true
THRASH_PROTECT_PSI_THRESHOLD=5.0
```

## Future Ideas

### 1. Per-cgroup Memory Pressure
Instead of system-wide PSI, monitor per-cgroup pressure:
```bash
cat /sys/fs/cgroup/.../memory.pressure
```
Could select which cgroup to freeze based on which one is causing the most pressure.

### 2. Memory Throttling (Soft Limits)
Instead of hard-freezing, use memory.high to throttle:
```bash
echo 500M > /sys/fs/cgroup/.../memory.high
```
The kernel will throttle the cgroup's memory allocations, slowing it down
without completely stopping it. Less disruptive than freezing.

### 3. IO Throttling
Reduce swap storm by throttling IO for memory-hungry cgroups:
```bash
echo "8:0 wbps=1048576" > /sys/fs/cgroup/.../io.max
```
Could limit write bandwidth to reduce swap write pressure.

### 4. Hierarchical Cgroup Management
Freeze parent cgroups to atomically freeze entire process subtrees.
Useful for complex applications with many child processes.

### 5. Memory Events Monitoring
Watch `memory.events` for OOM kills, high water marks, etc:
```bash
cat /sys/fs/cgroup/.../memory.events
# low 0
# high 12
# max 0
# oom 0
# oom_kill 0
```
Could react to memory events proactively.

### 6. Cgroup-based Process Selection
Instead of OOM scores per-process, look at cgroups and their aggregate
memory usage to decide what to freeze. More holistic approach.

## References

- [Kernel cgroup v2 documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [PSI documentation](https://www.kernel.org/doc/html/latest/accounting/psi.html)
- [Facebook's PSI blog post](https://facebookmicrosites.github.io/psi/)
