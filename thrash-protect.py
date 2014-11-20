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

__version__ = "0.9.0"
__author__ = "Tobias Brox"
__copyright__ = "Copyright 2013-2014, Tobias Brox"
__license__ = "GPL"
__maintainer__ = "Tobias Brox"
__email__ = "tobias@redpill-linpro.com"
__status__ = "Development"
__product__ = "thrash-protect"


import os
import time
import glob
import os
import signal
import logging
import random ## for the test_mode

#########################
## Configuration section
#########################

class config:
    """
    Collection of configuration variables.  (Those are still really
    global variables, but looks a bit neater to access
    config.bits_per_byte than bits_per_byte.  Perhaps we'll parse some
    a config file and initiate some object with the name config in
    some future version)
    """
    ## Normal sleep interval, in seconds.
    interval = float(os.getenv('THRASH_PROTECT_INTERVAL', '0.5'))

    ## max acceptable time delta in one iteration
    max_acceptable_time_delta = interval/16.0

    ## Number of acceptable page swaps during the above interval
    swap_page_threshold = int(os.getenv('THRASH_PROTECT_SWAP_PAGE_THRESHOLD', '512'))

    ## After X number of major pagefaults, we should initiate a process scanning
    pgmajfault_scan_threshold = int(os.getenv('THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD', swap_page_threshold*4))

    ## process name whitelist 
    cmd_whitelist = os.getenv('THRASH_PROTECT_CMD_WHITELIST', '')
    cmd_whitelist = cmd_whitelist.split(' ') if cmd_whitelist else ['sshd', 'bash', 'xinit', 'X', 'spectrwm', 'screen', 'SCREEN', 'mutt', 'ssh', 'xterm', 'rxvt', 'urxvt', 'Xorg.bin', 'systemd-journal']
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

class SystemState:
    """A "system state" is a collection of observed and calculated
    variables at a specific point of time.  We'll probably never have
    more than two instantiated objects - "last" and "current".  (This
    class replaces a bunch of global variables from version 0.8 -
    let's hope that the overhead in instantiation and garbage
    collection will be insignificant)
    """
    def __init__(self):
        self.timestamp = time.time()
        self.pagefaults = self.get_pagefaults()
        self.swapcount = self.get_swapcount()
        self.cooldown_counter = 0
        self.unfrozen_pid = None

    def get_pagefaults(self):
        with open('/proc/vmstat', 'r') as vmstat:
            line = ''
            while line is not None:
                line = vmstat.readline()
                if line.startswith('pgmajfault '):
                    return int(line[12:])

    def get_swapcount(self):
        ret = []
        with open('/proc/vmstat', 'r') as vmstat:
            line = True
            while line:
                line = vmstat.readline()
                if line.startswith('pswp'):
                    ret.append(int(line[7:]))
        return tuple(ret)

    def check_swap_threshold(self, prev):
        self.cooldown_counter = prev.cooldown_counter
        if config.test_mode and not random.getrandbits(config.test_mode):
            self.cooldown_counter = prev.cooldown_counter+1
            return True
        
        ## will return True if we have bidirectional traffic to swap, or if we have
        ## a big one-directional flow of data
        ret = (self.swapcount[0]-prev.swapcount[0]+1.0/config.swap_page_threshold) * (self.swapcount[1]-prev.swapcount[1]+1.0/config.swap_page_threshold) > 1.0
        ## Increase or decrease the busy-counter ... or keep it where it is
        if ret:
            ## thrashing alert, increase the counter
            self.cooldown_counter = prev.cooldown_counter+1
        elif prev.cooldown_counter and prev.swapcount==self.swapcount and self.timestamp-prev.timestamp>=self.get_sleep_interval():
            ## not busy at all, and we have slept since the previous check.  Decrease counter.
            self.cooldown_counter = prev.cooldown_counter-1
        else:
            debug("prev.swapcount==self.swapcount: %s,  self.timestamp-prev.timestamp>=self.get_sleep_interval(): %s, self.timestamp-prev.timestamp: %s, self.get_sleep_interval(): %s" % (prev.swapcount==self.swapcount, self.timestamp-prev.timestamp>=self.get_sleep_interval(),  self.timestamp-prev.timestamp,  self.get_sleep_interval()))
            ## some swapin or swapout has been observed, or we haven't slept since previous run.  Keep the cooldown counter steady.
            ## (Hm - we risk that process A gets frozen but never unfrozen due to process B generating swap activity?)
        return ret

    def get_sleep_interval(self):
        return config.interval/(self.cooldown_counter + 1.0)

    def check_delay(self, expected_delay=0):
        """
        If the code execution takes a too long time it may be that we're thrashing and this process has been swapped out.
        (TODO: detect possible problem: wrong tuning of max_acceptable_time_delta causes this to always trigger)
        """
        global frozen_pids
        delta = time.time() - self.timestamp - expected_delay
        debug("interval: %s cooldown_counter: %s expected delay: %s delta: %s time: %s frozen pids: %s" % (config.interval, self.cooldown_counter, expected_delay, delta, time.time(), frozen_pids))
        if delta > config.max_acceptable_time_delta:
            debug("red alert!  unacceptable time delta observed!")
            self.cooldown_counter += 1
            return False
        return True

class ProcessSelector:
    """Base class for process selector classes.

    Those classes have two methods, scan() which will search for a
    suitable process to suspend, and update() to update state in the
    object, if needed.  scan is required
    """
    def scan(self):
        raise NotImplementedError()
    
    def update(self, prev, curr):
        pass

class OOMScoreProcessSelector(ProcessSelector):
    """
    Class containing one method for selecting a process to freeze,
    based on oom_score.  No stored state required.
    """
    def scan(self):
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
                if cmd in config.cmd_whitelist:
                    debug("whitelisted process %s %s %s" % (pid, cmd, oom_score))
                    oom_score /= config.whitelist_score_divider
                if cmd in config.cmd_blacklist:
                    oom_score *= config.blacklist_score_multiplier
                if oom_score > max:
                    ## ignore self
                    if pid == os.getpid():
                        continue
                    max = oom_score
                    worstpid = pid
        debug("oom scan completed - selected pid: %s" % worstpid)
        return worstpid

class LastFrozenProcessSelector(ProcessSelector):
    """Class containing one method for selecting a process to freeze,
    simply refreezing the last unfrozen process.  The rationale is
    that if a process was just resumed and the system start thrashing
    again, it would probably be smart to freeze that process again -
    and it's also a very cheap operation to do.

    If refreezing the last unfrozen process helps, then we're good -
    though it may potentially a problem that the same process is
    selected all the time.

    We need to know what pid was unfrozen last time - this is
    currently passed through a global variable, but we should consider
    something smarter.
    """
    def __init__(self):
        self.last_unfrozen_pid = None
    
    def update(self, prev, cur):
        if cur.unfrozen_pid:
            self.last_unfrozen_pid = cur.unfrozen_pid
    
    def scan(self):
        """
        If a process was just resumed and the system start thrashing again, it would probably be smart to freeze that process again.  This is also a very cheap operation
        """
        debug("last unfrozen_pid is %s" % self.last_unfrozen_pid)
        if self.last_unfrozen_pid in frozen_pids:
          debug("last unfrozen_pid is already frozen")
          return None
        debug("last unfrozen process return - selected pid: %s" % self.last_unfrozen_pid)
        return self.last_unfrozen_pid

class PageFaultingProcessSelector(ProcessSelector):
    """
    Selects the process that have had most page faults since previous
    run.  This method have two problems; it is relatively expensive in
    terms of memory usage since it needs to keep counts of the page
    faults for every process, secondly, "page fault" is not equivalent
    with "swap".  (When a process is started, loading the program code
    into memory is usually postponed - when pages that aren't loaded
    yet are needed, it's also a "page fault")
    """
    def __init__(self):
        self.pagefault_by_pid = {}

    def update(self, prev, cur):
        self.cooldown_counter = cur.cooldown_counter
        if cur.pagefaults - prev.pagefaults > config.pgmajfault_scan_threshold:
                ## If we've had a lot of major page faults, refresh our state
                ## on major page faults.
                self.scan()
        
    def scan(self):
        ## TODO: garbage collection
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
                prev = self.pagefault_by_pid.get(pid, 0)
                self.pagefault_by_pid[pid] = majflt
                diff = majflt - prev
                if config.test_mode:
                  diff += random.getrandbits(3)
                if not diff:
                    continue
                if cmd in config.cmd_blacklist:
                    diff *= config.blacklist_score_multiplier
                if cmd in config.cmd_whitelist:
                    debug("whitelisted process %s %s %s" % (pid, cmd, diff))
                    diff /= config.whitelist_score_divider
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
        if max > 4.0 / (self.cooldown_counter + 1.0):
            return worstpid

class GlobalProcessSelector(ProcessSelector):
    """
    This is a collection of the various process selectors.
    """
    def __init__(self):
        ## sorted from cheap to expensive.  Also, it is surely smart to be quick on refreezing a recently unfrozen process if host starts thrashing again.
        self.collection = [LastFrozenProcessSelector(), PageFaultingProcessSelector(), OOMScoreProcessSelector()]
        self.scan_method_count = 0

    def update(self, prev, cur):
        if cur.unfrozen_pid:
            self.scan_method_count = 0
        for c in self.collection:
            c.update(prev, cur)

    def scan(self):
        debug("scan_processes")

        ## a for loop here to make sure we fall back on the next method if the first method fails to find anything.
        for i in range(0,len(self.collection)):
            debug("scan method: %s" % (self.scan_method_count % len(self.collection)))
            ret = self.collection[self.scan_method_count % len(self.collection)].scan()
            self.scan_method_count += 1
        if ret:
          return ret
    debug("found nothing to stop!? :-(")

## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: advanced logging
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
    global global_process_selector
    pid_to_freeze = global_process_selector.scan()
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
    if frozen_pids:
        ## queue or stack?  Seems like both approaches are problematic
        if num_unfreezes % config.unfreeze_pop_ratio:
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
        log_unfrozen(pid_to_unfreeze)
        num_unfreezes += 1
        return pid_to_unfreeze

def thrash_protect(args=None):
    current = SystemState()
    global frozen_pids
    global global_process_selector

    ## A best-effort attempt on running mlockall()
    try:
        import ctypes
        assert(not ctypes.cdll.LoadLibrary('libc.so.6').mlockall(ctypes.c_int(3)))
    except:
        logging.warning("failed to do mlockall() - this makes the program vulnerable of being swapped out in an extreme thrashing event", exc_info=True)

    while True:
        prev = current
        current = SystemState()
        busy = current.check_swap_threshold(prev)

        ## If we're thrashing, then freeze something.
        if busy:
            freeze_something()
        elif not current.cooldown_counter:
            ## If no swapping has been observed for a while then unfreeze something.
            current.unfrozen_pid = unfreeze_something()

        global_process_selector.update(prev, current)
        
        if current.check_delay() and not busy:
            sleep_interval = current.get_sleep_interval()
            debug("going to sleep %s" % sleep_interval)
            time.sleep(sleep_interval)
            current.check_delay(sleep_interval)

if __name__ == '__main__':
    ## Globals ... we've refactored most of them away, but some still remains ...
    frozen_pids = []
    num_freezes = 0
    num_unfreezes = 0
    ## A singleton ...
    global_process_selector = GlobalProcessSelector()

    try:
        import argparse
        p = argparse.ArgumentParser(description="protect a linux host from thrashing")
        p.add_argument('--version', action='version', version='%(prog)s ' + __version__)
        args = p.parse_args()
    except ImportError:
        ## argparse is only available from 2.7 and up
        args = None
    thrash_protect(args)


