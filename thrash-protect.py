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

__version__ = "0.7.1"
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
interval = float(os.getenv('THRASH_PROTECT_INTERVAL', '0.5'))

## Number of acceptable page swaps during the above interval
swap_page_threshold = int(os.getenv('THRASH_PROTECT_SWAP_PAGE_THRESHOLD', '512'))

## After X number of major pagefaults, we should initiate a process scanning
pgmajfault_scan_threshold = int(os.getenv('THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD', swap_page_threshold))

## process name whitelist 
cmd_whitelist = os.getenv('THRASH_PROTECT_CMD_WHITELIST', '')
cmd_whitelist = cmd_whitelist.split(' ') if cmd_whitelist else ['sshd', 'bash', 'xinit', 'X', 'spectrwm', 'screen', 'SCREEN', 'mutt', 'ssh', 'xterm', 'rxvt', 'urxvt']
cmd_blacklist = os.getenv('THRASH_PROTECT_CMD_BLACKLIST', '').split(' ')
blacklist_score_multiplier = int(os.getenv('THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER', '16'))
whitelist_score_divider = int(os.getenv('THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER', str(blacklist_score_multiplier*4)))

## Unfreezing processes: Ratio of POP compared to GET (integer)
unfreeze_pop_ratio = int(os.getenv('THRASH_PROTECT_UNFREEZE_POP_RATIO', '5'))

## test_mode - if test_mode and not random.getrandbits(test_mode), then pretend we're thrashed
test_mode = int(os.getenv('THRASH_PROTECT_TEST_MODE', '0'))

## Poor mans logging.  Should eventually set up the logging module
#debug = print
debug = lambda foo: None

import time
import glob
import os
import signal
import random ## for the test_mode



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
    global busy_runs
    if test_mode and not random.getrandbits(test_mode):
        busy_runs += 1
        return True
    ## will return True if we have bidirectional traffic to swap, or if we have
    ## a big one-directional flow of data
    ret = (curr[0]-prev[0]+1.0/swap_page_threshold) * (curr[1]-prev[1]+1.0/swap_page_threshold) > 1.0
    ## Increase or decrese the busy-counter
    if ret:
        busy_runs += 1
    elif busy_runs:
        busy_runs -= 1
    return ret

def scan_processes():
    debug("scan_processes")
    global scan_method_count
    ## sorted from cheap to expensive.  Also, it is surely smart to be quick on refreezing a recently unfrozen process if host starts thrashing again.
    scan_methods = [ find_last_unfrozen_process, scan_processes_oom_score, scan_processes_pagefaults ]

    ## a for loop here to make sure we fall back on the next method if the first method fails to find anything.
    for i in range(0,len(scan_methods)):
        print("scan method: %s" % (scan_method_count % len(scan_methods)))
        ret = scan_methods[scan_method_count % len(scan_methods)]()
        scan_method_count += 1
        if ret:
          return ret
    debug("found nothing to stop!? :-(")

def scan_processes_oom_score():
    oom_scores = glob.glob('/proc/*/oom_score')
    max = 0
    worstpid = None
    for fn in oom_scores:
        try:
            pid = int(fn.split('/')[2])
        except ValueError:
            continue
        try:
            with open(fn, 'r') as oom_score_file:
                oom_score = int(oom_score_file.readline())
            with open("/proc/%d/stat" % pid, 'r') as stat_file:
                stats = stat_file.readline().split(' ')
                state = stats[2]
                cmd = stats[1][1:].split('/')[0].split(')')[0]
                if 'T' in state:
                    debug("oom_score: %s, cmd: %s, pid: %s, state: %s - no touch" % (oom_score, cmd, pid, state))
                    continue
        except FileNotFoundError:
            continue
        if oom_score > 0:
            debug("oom_score: %s, cmd: %s, pid: %s" % (oom_score, cmd, pid))
            if cmd in cmd_whitelist:
                oom_score /= whitelist_score_divider
            if cmd in cmd_blacklist:
                oom_score *= blacklist_score_multiplier
            if oom_score > max:
                ## ignore self
                if pid == os.getpid():
                    continue
                max = oom_score
                worstpid = pid
    debug("oom scan completed - selected pid: %s" % worstpid)
    return worstpid

def find_last_unfrozen_process():
    """
    If a process was just resumed and the system start thrashing again, it would probably be smart to freeze that process again.  This is also a very cheap operation
    """
    global last_unfrozen_pid
    debug("last unfrozen_pid is %s" % last_unfrozen_pid)
    if last_unfrozen_pid in frozen_pids:
      debug("last unfrozen_pid is already frozen")
      return None
    debug("last unfrozen process return - selected pid: %s" % last_unfrozen_pid)
    return last_unfrozen_pid

def scan_processes_pagefaults():
    ## TODO: consider using oom_score instead of major page faults?
    ## TODO: garbage collection
    global pagefault_by_pid
    global last_scan_pagefaults
    global busy_runs
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
          try: ## double try to keep it compatible with both python 2.5 and python 3.0
            with open(fn, 'r') as stat_file:
                stats = stat_file.readline().split(' ')
                majflt = int(stats[11])
                cmd = stats[1][1:].split('/')[0].split(')')[0]
          except FileNotFoundError:
              continue
        except ProcessLookupError:
            continue
        if majflt > 0:
            prev = pagefault_by_pid.get(pid, 0)
            pagefault_by_pid[pid] = majflt
            diff = majflt - prev
            if test_mode:
              diff += random.getrandbits(3)
            if not diff:
                continue
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
            debug("pagefault score: %s, cmd: %s, pid: %s" % (diff, cmd, pid))
    debug("pagefault scan completed - selected pid: %s" % worstpid)
    ## give a bit of protection against whitelisted and innocent processes being stopped
    ## (TODO: hardcoded constants)
    if max > 4.0 / (busy_runs + 1.0):
      return worstpid

## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: support
## smtp etc.
def log_frozen(pid):
    with open("/var/log/thrash-protect.log", 'a') as logfile:
        logfile.write("%s - frozen pid %s - frozen list: %s\n" % (time.time(), pid, frozen_pids))
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
    if not pid_to_freeze in frozen_pids:
        frozen_pids.append(pid_to_freeze)
    ## Logging after freezing - as logging itself may be resource- and timeconsuming.
    ## Perhaps we should even fork it out.
    debug("going to freeze %s" % pid_to_freeze)
    log_frozen(pid_to_freeze)
    num_freezes += 1

def unfreeze_something():
    global frozen_pids
    global num_unfreezes
    global last_unfrozen_pid
    if frozen_pids:
        ## queue or stack?  Seems like both approaches are problematic
        if num_unfreezes % unfreeze_pop_ratio:
            pid_to_unfreeze = frozen_pids.pop()
        else:
            ## no list.get() in python?
            pid_to_unfreeze = frozen_pids[0]
            frozen_pids = frozen_pids[1:]
        try:
            debug("going to unfreeze %s" % pid_to_unfreeze)
            os.kill(pid_to_unfreeze, signal.SIGCONT)
            ## Sometimes the parent process also gets suspended.
            ## TODO: we're doing some simple assumptions here; 
            ## 1) this
            ## problem only applies to process group id or session id
            ## (we probably need to walk through all the parents - or maybe just the ppid?)
            ## 2) it is harmless to CONT the pgid and sid.  This may not always be so.
            ## To correct this, we may need to traverse parents
            ## (peeking into /proc/<pid>/status recursively) prior to freezing the proc.
            ## all parents that aren't already frozen should be added to the unfreeze stack
            os.kill(os.getpgid(pid_to_unfreeze), signal.SIGCONT)
            os.kill(os.getsid(pid_to_unfreeze), signal.SIGCONT)
        except ProcessLookupError:
            ## ignore failure
            pass
        last_unfrozen_pid = pid_to_unfreeze
        log_unfrozen(pid_to_unfreeze)
        num_unfreezes += 1

def thrash_protect(args=None):
    global last_observed_swapcount
    global last_scan_pagefaults
    global busy_runs
    global last_time
    global frozen_pids
    global scan_method_count
    while True:
        busy = False
        current_swapcount = get_swapcount()
        current_pagefaults = get_pagefaults()
        busy = check_swap_threshold(current_swapcount, last_observed_swapcount)

        ## If we're thrashing, then freeze something.
        if busy:
            freeze_something()
        elif not busy_runs and current_swapcount == last_observed_swapcount:
            ## If no swapping has been observed for a while then unfreeze something.
            scan_method_count = 0
            unfreeze_something()
            if current_pagefaults - last_scan_pagefaults > pgmajfault_scan_threshold:
                ## If we've had a lot of major page faults, refresh our state
                ## on major page faults.
                scan_processes_pagefaults()
        last_observed_swapcount = current_swapcount

        ## If the script is significantly delayed it's most likely due to
        ## thrashing, and we should increase the busy counter and sleep less.
        delay = time.time() - last_time
        debug("delay in processing: %s" % delay)
        ## if delay is significant, bump busy_runs.  TODO: hard-coded
        ## constants ... should be moved to configuration
        if delay > interval/16.0:
            busy_runs += 1
        last_time = time.time()
        debug("interval: %s busy_runs: %s time: %s frozen pids: %s" % (interval, busy_runs, time.time(), frozen_pids))

        ## If we haven't been busy for a while, or if this run apparently was
        ## non-busy, then sleep a bit.
        if not busy_runs or not busy:
            ## TODO: bitshifting would probably be better;  sleep_interval_microseconds = interval_microseconds >> busy_runs
            sleep_interval = interval/(busy_runs + 1.0)
            debug("going to sleep %s" % sleep_interval)
            time.sleep(sleep_interval)
            delay = time.time() - last_time - sleep_interval
            last_time = time.time()
            debug("slept: %s + delay %s" % (sleep_interval, delay))
            ## if delay is significant, bump busy_runs.  TODO: hard-coded constants
            if delay > interval/16.0:
                busy_runs += 1

if __name__ == '__main__':
    ## Globals
    last_observed_swapcount = get_swapcount()
    last_scan_pagefaults = 0
    last_unfrozen_pid = None
    scan_method_count = 0
    pagefault_by_pid = {}
    frozen_pids = []
    num_freezes = 0
    num_unfreezes = 0
    busy_runs = 0
    last_time = time.time()

    try:
        import argparse
        p = argparse.ArgumentParser(description="protect a linux host from thrashing")
        p.add_argument('--version', action='version', version='%(prog)s ' + __version__)
        args = p.parse_args()
    except ImportError:
        ## argparse is only available from 2.7 and up
        args = None
    thrash_protect(args)


