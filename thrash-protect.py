#!/usr/bin/python

### Simple-Stupid user-space program protecting a linux host from thrashing.
### See the README for details.
### Project home: https://github.com/tobixen/thrash-protect

### This is a rapid prototype implementation.  I'm considering to implement in C.

## This was written for python3 (there exists a python24-branch, but
## it won't be maintained).  python3 is not available on a lot of
## servers, and those seems to be the only snags when running on
## python 2.5:
from __future__ import with_statement
try:
  ProcessLookupError
except NameError:
  ProcessLookupError=OSError
try:
  FileNotFoundError
except NameError:
  FileNotFoundError=IOError

__version__ = "0.6.3"
__author__ = "Tobias Brox"
__copyright__ = "Copyright 2013, Tobias Brox"
__license__ = "GPL"
__maintainer__ = "Tobias Brox"
__email__ = "tobias@redpill-linpro.com"
__status__ = "Development"
__product__ = "thrash-protect"


#########################
## Configuration section
#########################

import os

## Sleep interval, in seconds
interval = int(os.getenv('THRASH_PROTECT_INTERVAL', '1'))

## Number of acceptable page swaps during the above interval
swap_page_threshold = int(os.getenv('THRASH_PROTECT_SWAP_PAGE_THRESHOLD', '100'))

## After X number of major pagefaults, we should initiate a process scanning
pgmajfault_scan_threshold = int(os.getenv('THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD', swap_page_threshold))

## process name whitelist 
cmd_whitelist = os.getenv('THRASH_PROTECT_CMD_WHITELIST', '')
cmd_whitelist = cmd_whitelist.split(' ') if cmd_whitelist else ['sshd', 'bash', 'xinit', 'X', 'spectrwm', 'screen', 'SCREEN', 'mutt', 'ssh', 'xterm', 'rxvt', 'urxvt']
cmd_blacklist = os.getenv('THRASH_PROTECT_CMD_BLACKLIST', '').split(' ')
blacklist_score_multiplier = int(os.getenv('THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER', '5'))
whitelist_score_divider = int(os.getenv('THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER', str(blacklist_score_multiplier*2)))

## Unfreezing processes: Ratio of POP compared to GET (integer)
unfreeze_pop_ratio = int(os.getenv('THRASH_PROTECT_UNFREEZE_POP_RATIO', '5'))

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

def get_swapcount():
    ret = []
    with open('/proc/vmstat', 'r') as vmstat:
        line = True
        while line:
            line = vmstat.readline()
            if line.startswith('pswp'):
                ret.append(int(line[7:]))
    return tuple(ret)

def check_swap_threshold(curr, prev):
    global swap_page_threshold
    ## will return True if we have bidirectional traffic to swap, or if we have
    ## a big one-directional flow of data
    return (curr[0]-prev[0]+1.0/swap_page_threshold) * (curr[1]-prev[1]+1.0/swap_page_threshold) > 1.0

def scan_processes():
    ## TODO: consider using oom_score instead of major page faults?
    ## TODO: garbage collection
    global pagefault_by_pid
    global last_scan_pagefaults
    last_scan_pagefaults = get_pagefaults()
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
            diff = majflt - prev
            if cmd in cmd_blacklist:
                diff *= blacklist_score_multiplier
            if cmd in cmd_whitelist:
                diff /= whitelist_score_divider
            if diff > max:
                ## ignore self
                if pid == os.getpid():
                    continue
                max = diff
                worstpid = pid
    return worstpid

## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: support
## smtp etc.
def log_frozen(pid):
    with open("/var/log/thrash-protect.log", 'a') as logfile:
        logfile.write("%s - frozen pid %s\n" % (time.time(), pid))
    with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
        logfile.write(" ".join([str(x) for x in frozen_pids]))

def log_unfrozen(pid):
    with open("/var/log/thrash-protect.log", 'a') as logfile:
        logfile.write("%s - unfrozen pid %s\n" % (time.time(), pid))
    if frozen_pids:
        with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
            logfile.write(" ".join([str(pid) for pid in frozen_pids]) + "\n")
    else:
        try:
            os.unlink("/tmp/thrash-protect-frozen-pid-list")
        except FileNotFoundError:
            pass

def freeze_something():
    global frozen_pids
    global num_freezes
    pid_to_freeze = scan_processes()
    if not pid_to_freeze:
        ## process disappeared. ignore failure
        return
    try:
        os.kill(pid_to_freeze, signal.SIGSTOP)
    except ProcessLookupError:
        return
    frozen_pids.append(pid_to_freeze)
    ## Logging after freezing - as logging itself may be resource- and timeconsuming.
    ## Perhaps we should even fork it out.
    log_frozen(pid_to_freeze)
    num_freezes += 1

def unfreeze_something():
    global frozen_pids
    global num_unfreezes
    if frozen_pids:
        ## queue or stack?  Seems like both approaches are problematic
        if num_unfreezes % unfreeze_pop_ratio:
            pid_to_unfreeze = frozen_pids.pop()
        else:
            ## no list.get() in python?
            pid_to_unfreeze = frozen_pids[0]
            frozen_pids = frozen_pids[1:]
        try:
            os.kill(pid_to_unfreeze, signal.SIGCONT)
            ## Sometimes the parent process also gets suspended.
            ## TODO: we're doing some simple assumptions here; 
            ## 1) this
            ## problem only applies to process group id or session id
            ## (we probably need to walk through all the parents)
            ## 2) it is harmless to CONT the pgid and sid.  This may not always be so.
            ## To correct this, we may need to traverse parents
            ## (peeking into /proc/<pid>/status recursively) prior to freezing the proc.
            ## all parents that aren't already frozen should be added to the unfreeze stack
            os.kill(os.getpgid(pid_to_unfreeze), signal.SIGCONT)
            os.kill(os.getsid(pid_to_unfreeze), signal.SIGCONT)
        except ProcessLookupError:
            ## ignore failure
            pass
        log_unfrozen(pid_to_unfreeze)
        num_unfreezes += 1

def thrash_protect(args=None):
    global last_observed_swapcount
    global last_scan_pagefaults
    while True:
        current_swapcount = get_swapcount()
        current_pagefaults = get_pagefaults()
        if check_swap_threshold(current_swapcount, last_observed_swapcount):
            freeze_something()
        elif current_swapcount == last_observed_swapcount:
            unfreeze_something()
        if current_pagefaults - last_scan_pagefaults > pgmajfault_scan_threshold:
            scan_processes()
        last_observed_swapcount = current_swapcount
        time.sleep(interval)

if __name__ == '__main__':
    ## Globals
    last_observed_swapcount = get_swapcount()
    last_scan_pagefaults = 0
    pagefault_by_pid = {}
    frozen_pids = []
    num_freezes = 0
    num_unfreezes = 0

    try:
        import argparse
        p = argparse.ArgumentParser(description="protect a linux host from thrashing")
        p.add_argument('--version', action='version', version='%(prog)s ' + __version__)
        args = p.parse_args()
    except ImportError:
        ## argparse is only available from 2.7 and up
        args = None
    thrash_protect(args)


