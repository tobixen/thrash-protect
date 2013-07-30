#!/usr/bin/python3

### This is a rapid prototype implementation.  It's consuming surprisingly much cpu and memory.  I hope the C version will be smoother.
### This is stub - work in process.

## Configuration section
## (TODO: better names)

## Sleep interval, in seconds
sleep = 1

## Number of acceptable pgmajfaults during the above interval
fault_threshold = 5

## number of pagefaults between each process scanning
process_scanning_threshold = fault_threshold * 3

## process name whitelist
cmd_whitelist = ['sshd', 'bash', 'xinit', 'X', 'spectrwm']

import time
import glob
import os
import signal

def get_pagefaults():
    with open('/proc/vmstat', 'r') as vmstat:
        line = ''
        while line is not None:
            line = vmstat.readline()
            if line.startswith('pgmajfault '):
                return int(line[12:])

def scan_processes():
    ## TODO: garbage collection
    global pagefault_by_pid
    global last_scan_pagefaults
    last_scan_pagefaults = last_observed_pagefaults
    stat_files = glob.glob('/proc/*/stat')
    max = 0
    worstpid = None
    for fn in stat_files:
        try:
            pid = int(fn.split('/')[2])
        except ValueError:
            continue
        try:
            with open(fn, 'r') as stat_file:
                stats = stat_file.readline().split(' ')
                majflt = int(stats[11])
                cmd = stats[1][1:].split('/')[0].split(')')[0]
        except FileNotFoundError:
            pass
        if majflt > 0:
            prev = pagefault_by_pid.get(pid, 0)
            pagefault_by_pid[pid] = majflt
            if majflt - prev > max:
                ## ignore whitelisted
                if cmd in cmd_whitelist:
                    continue
                ## ignore self
                if pid == os.getpid():
                    continue
                max = majflt - prev
                worstpid = pid
    return worstpid

def log_frozen(pid):
    with open("/var/log/trash-protect.log", 'a') as logfile:
        logfile.write("%s - frozen pid %s\n" % (time.time(), pid))
    with open("/tmp/trash-protect-frozen-pid-list", "w") as logfile:
        logfile.write(" ".join([str(x) for x in frozen_pids]))

## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: support
## smtp etc.
def log_unfrozen(pid):
    with open("/var/log/trash-protect.log", 'a') as logfile:
        logfile.write("%s - unfrozen pid %s\n" % (time.time(), pid))
    if frozen_pids:
        with open("/tmp/trash-protect-frozen-pid-list", "w") as logfile:
            logfile.write(" ".join([str(pid) for pid in frozen_pids]) + "\n")
    else:
        os.unlink("/tmp/trash-protect-frozen-pid-list")

def freeze_something():
    pid_to_freeze = scan_processes()
    if not pid_to_freeze:
        return
    os.kill(pid_to_freeze, signal.SIGSTOP)
    frozen_pids.insert(0, pid_to_freeze)
    ## Logging after freezing - as logging itself may be resource- and timeconsuming.
    ## Perhaps we should even fork it out.
    log_frozen(pid_to_freeze)

def unfreeze_something():
    if frozen_pids:
        pid_to_unfreeze = frozen_pids.pop()
        os.kill(pid_to_unfreeze, signal.SIGCONT)
        log_unfrozen(pid_to_unfreeze)

## Globals
last_observed_pagefaults = get_pagefaults()
last_scan_pagefaults = 0
pagefault_by_pid = {}
frozen_pids = []

while True:
    current_pagefaults = get_pagefaults()
    if current_pagefaults - last_observed_pagefaults > fault_threshold:
        freeze_something()
    elif current_pagefaults - last_observed_pagefaults == 0:
        unfreeze_something()
    if current_pagefaults - last_scan_pagefaults > process_scanning_threshold:
        scan_processes()
    last_observed_pagefaults = current_pagefaults
    time.sleep(sleep)
