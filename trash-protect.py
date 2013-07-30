#!/usr/bin/python3

### This is a rapid prototype implementation.
### This is stub - work in process.

## Configuration section
## (TODO: better names)

## Sleep interval, in seconds
sleep = 0.5

## Number of acceptable pgmajfaults during the above interval
fault_threshold = 5

## number of pagefaults between each process scanning
process_scanning_threshold = fault_threshold * 3

## Unfreezing processes: Ratio of POP compared to GET:
unfreeze_pop_ratio = 5

import time
import glob

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
    stat_files = glob.glob('/proc/*/stat')
    max = 0
    maxpid = None
    for fn in stat_files:
        try:
            pid = int(fn.split('/')[2])
        except ValueError:
            continue
        with open(fn, 'r') as stat_file:
            stats = stat_file.readline()
            majflt = int(stats.split(' ')[8])
        if majflt > 0:
            prev = pagefault_by_pid.get(pid, 0)
            pagefault_by_pid[pid] = majflt
            if majflt - prev > max:
                max = majflt - prev
                maxpid = pid
    return maxpid

def freeze_something():
    raise NotImplementedError("caught up with dinner time :-(")

def unfreeze_something():
    pass
    #raise NotImplementedError("caught up with dinner time :-(")

## Globals
last_observed_pagefaults = get_pagefaults()
last_scan_pagefaults = 0
pagefault_by_pid = {}

while True:
    current_pagefaults = get_pagefaults()
    if current_pagefaults - last_observed_pagefaults > fault_threshold:
        freeze_something()
    elif current_pagefaults - last_observed_pagefaults == 0:
        unfreeze_something()
    if current_pagefaults - last_scan_pagefaults > process_scanning_threshold:
        scan_processes()
    time.sleep(sleep)
