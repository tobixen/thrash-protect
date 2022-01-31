#!/usr/bin/python3

### Simple-Stupid user-space program protecting a linux host from thrashing.
### See the README for details.
### Project home: https://github.com/tobixen/thrash-protect

### This is a rapid prototype implementation.  I'm considering to implement in C.

## This was written for python3 (there exists a python24-branch, but
## it won't be maintained).  python3 is not available on a lot of
## servers, and those seems to be the only snags when running on
## python 2.7:
from __future__ import with_statement
try:
    ProcessLookupError
except NameError:
    ProcessLookupError=OSError
try:
    FileNotFoundError
except NameError:
    FileNotFoundError=IOError

__version__ = "0.14.3"
__author__ = "Tobias Brox"
__copyright__ = "Copyright 2013-2021, Tobias Brox"
__license__ = "GPL"
__maintainer__ = "Tobias Brox"
__email__ = "tobias@redpill-linpro.com"
__product__ = "thrash-protect"

## subprocess.check_output is not available in python 2.6.  this is used in a
## non-critical part of the script, already inside a try-except-scope, so the
## import has been moved there to allow the script to work on servers without 2.7 installed.
#from subprocess import check_output 
from os import getenv, kill, getpid, unlink, getpgid, getsid, getpid, getppid
from collections import namedtuple
import time
from datetime import datetime
import glob
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
    ## debug
    debug_logging = getenv('THRASH_PROTECT_DEBUG_LOGGING', False)
    ## will check the state and warn if thrash_protects attempts unfreezing a process that is running or freezing a process that is already suspended
    debug_checkstate = getenv('THRASH_PROTECT_DEBUG_CHECKSTATE', False)
    
    ## Normal sleep interval, in seconds.
    interval = float(getenv('THRASH_PROTECT_INTERVAL', '0.5'))

    ## max acceptable time delta in one iteration
    max_acceptable_time_delta = interval/8.0

    ## Number of acceptable page swaps during the above interval
    swap_page_threshold = int(getenv('THRASH_PROTECT_SWAP_PAGE_THRESHOLD', '4'))

    ## After X number of major pagefaults, we should initiate a process scanning
    pgmajfault_scan_threshold = int(getenv('THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD', swap_page_threshold*4))

    ## process name whitelist 
    cmd_whitelist = getenv('THRASH_PROTECT_CMD_WHITELIST', '')
    cmd_whitelist = cmd_whitelist.split(' ') if cmd_whitelist else ['sshd', 'bash', 'xinit', 'X', 'spectrwm', 'screen', 'SCREEN', 'mutt', 'ssh', 'xterm', 'rxvt', 'urxvt', 'Xorg.bin', 'Xorg', 'systemd-journal']
    cmd_blacklist = getenv('THRASH_PROTECT_CMD_BLACKLIST', '').split(' ')
    cmd_jobctrllist = getenv('THRASH_PROTECT_CMD_JOBCTRLLIST', 'bash sudo').split(' ')
    blacklist_score_multiplier = int(getenv('THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER', '16'))
    whitelist_score_divider = int(getenv('THRASH_PROTECT_WHITELIST_SCORE_MULTIPLIER', str(blacklist_score_multiplier*4)))

    ## Unfreezing processes: Ratio of POP compared to GET (integer)
    unfreeze_pop_ratio = int(getenv('THRASH_PROTECT_UNFREEZE_POP_RATIO', '5'))

    ## test_mode - if test_mode and not random.getrandbits(test_mode), then pretend we're thrashed
    test_mode = int(getenv('THRASH_PROTECT_TEST_MODE', '0'))

    ## ADVANCED LOGGING OPTIONS
    ## When freezing a process, enables logging of username, CPU usage, memory usage and command string
    ## (will spawn ps, so some overhead costs)
    log_user_data_on_freeze = int(getenv('THRASH_PROTECT_LOG_USER_DATA_ON_FREEZE', '0'))
    ## Log the extra process data on unfreeze (when the extra overhead cost is probably harmless)
    log_user_data_on_unfreeze = int(getenv('THRASH_PROTECT_LOG_USER_DATA_ON_UNFREEZE', '1'))
    ## Enable human-readable date format instead of UNIX timestamp
    date_human_readable = int(getenv('THRASH_PROTECT_DATE_HUMAN_READABLE', '1'))

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
        self.timer_alert = False

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
        
        ## will return True if we have bidirectional traffic to swap,
        ## or if we have a big one-directional flow of data.
        ##
        ## * if both swap counters are above the swap_page_threshold, trigger
        ##
        ## * if one of the swap counters is quite much above the
        ##   swap_page_threshold, while the other is 0, we should trigger
        ##
        ## the below algorithm seems to satisfy those two criterias, though
        ## I'm not much happy with the arbitrary constant "0.1" being thrown
        ## in.
        ret = (
            ((self.swapcount[0]-prev.swapcount[0]+0.1)/config.swap_page_threshold) *
            ((self.swapcount[1]-prev.swapcount[1]+0.1)/config.swap_page_threshold)
            > 1.0)
        ## Increase or decrease the busy-counter ... or keep it where it is
        if ret:
            ## thrashing alert, increase the counter
            self.cooldown_counter = prev.cooldown_counter+1
            if not prev.timer_alert:
                logging.debug("potential thrashing detected, but we got no timing alarm. Perhaps max_acceptable_time_delta should be tweaked down")
                config.max_acceptable_time_delta/=1.1
        elif prev.cooldown_counter and prev.swapcount==self.swapcount and self.timestamp-prev.timestamp>=self.get_sleep_interval():
            ## not busy at all, and we have slept since the previous check.  Decrease counter.
            self.cooldown_counter = prev.cooldown_counter-1
            if prev.timer_alert:
                logging.debug("we got a timer alert, even if the system is not busy.  Increasing the timer alert threshold")
                config.max_acceptable_time_delta*=1.1
        else:
            logging.debug("prev.swapcount==self.swapcount: %s,  self.timestamp-prev.timestamp>=self.get_sleep_interval(): %s, self.timestamp-prev.timestamp: %s, self.get_sleep_interval(): %s" % (prev.swapcount==self.swapcount, self.timestamp-prev.timestamp>=self.get_sleep_interval(),  self.timestamp-prev.timestamp,  self.get_sleep_interval()))
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
        if delta > config.max_acceptable_time_delta:
            logging.info("relatively big time delta observed. interval: %s cooldown_counter: %s expected delay: %s max acceptable delta: %s delta: %s time: %s frozen pids: %s.  (this message is to be expected every now and then as the max acceptable delta parameter is autotuned)" % (config.interval, self.cooldown_counter, expected_delay, config.max_acceptable_time_delta, delta, time.time(), frozen_pids))
            self.cooldown_counter += 2
            self.timer_alert = True
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

    procstat = namedtuple('procstat', ('cmd', 'state', 'majflt', 'ppid'))

    def readStat(self, sfn):
        try: 
            return self.readStat_(sfn)
        except (FileNotFoundError, ProcessLookupError):
            return None

    def readStat_(self, sfn):
        """
        helper method - reads the stats file and returns a tuple (cmd, state, 
        majflt, pids)
        """
        if isinstance(sfn, int):
            sfn = "/proc/%s/stat" % sfn
        with open(sfn, 'rb') as stat_file:
            stats=[]
            stats_tx = stat_file.read().decode('utf-8', 'ignore')
            stats_tx = stats_tx.split("(",1)
            stats.append(stats_tx[0])
            stats_tx=stats_tx[1].rsplit(")",1)   
            stats.append(stats_tx[0])
            stats.extend(stats_tx[1].split(' ')[1:])
        return self.procstat(stats[1], stats[2], int(stats[11]), int(stats[3]))

    def checkParents(self, pid, ppid=None):
        """
        helper method - find a list of pids that should be suspended, given
        a pid (and for optimalization reasons, ppid if it's already
        known).

        If a process running under an interactive bash session gets
        suspended, the bash job control kicks in and causes havoc.
        Hence, we should check if the cmd of the parent process is
        'bash'.
        """
        if ppid is None:
            stats = self.readStat(pid)
            if not stats:
                return ()
            ppid = stats.ppid
        if ppid <= 1:
            return (pid,)
        pstats = self.readStat(ppid)
        if pstats and pstats.cmd in config.cmd_jobctrllist:
            return self.checkParents(ppid, pstats.ppid) + (pid,)
        else:
            return (pid,)

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
                stats = self.readStat(pid)
                if not stats:
                    continue
                if 'T' in stats.state:
                    logging.debug("oom_score: %s, cmd: %s, pid: %s, state: %s - no touch" % (oom_score, stats.cmd, pid, stats.state))
                    continue
            except FileNotFoundError:
                continue
            if oom_score > 0:
                logging.debug("oom_score: %s, cmd: %s, pid: %s" % (oom_score, stats.cmd, pid))
                if stats.cmd in config.cmd_whitelist:
                    logging.debug("whitelisted process %s %s %s" % (pid, stats.cmd, oom_score))
                    oom_score /= config.whitelist_score_divider
                if stats.cmd in config.cmd_blacklist:
                    oom_score *= config.blacklist_score_multiplier
                if oom_score > max:
                    ## ignore self
                    if pid in (getpid(), getppid()):
                        continue
                    max = oom_score
                    worstpid = (pid, stats.ppid)
        logging.debug("oom scan completed - selected pid: %s" % (worstpid and worstpid[0]))
        if worstpid != None:
            return self.checkParents(*worstpid)
        else:
            return None

class LastFrozenProcessSelector(ProcessSelector):
    """Class containing one method for selecting a process to freeze,
    simply refreezing the last unfrozen process.  The rationale is
    that if a process was just resumed and the system start thrashing
    again, it would probably be smart to freeze that process again -
    and it's also a very cheap operation to do.

    If refreezing the last unfrozen process helps, then we're good -
    though it may potentially a problem that the same process is
    selected all the time.
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
        logging.debug("last unfrozen_pid is %s" % self.last_unfrozen_pid)
        if self.last_unfrozen_pid in frozen_pids:
          logging.debug("last unfrozen_pid is already frozen")
          return None
        logging.debug("last unfrozen process return - selected pid: %s" % self.last_unfrozen_pid)

        ## it may have exited already, in that case we should purge the record
        if self.last_unfrozen_pid and not [True for x in self.last_unfrozen_pid if self.readStat(x)]:
            self.last_unfrozen_pid = None

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
        ## TODO: garbage collection
        self.pagefault_by_pid = {}
        self.cooldown_counter = 0

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
            stats = self.readStat(fn)
            if not stats:
                continue
            if stats.majflt > 0 and not 'T' in stats.state:
                prev = self.pagefault_by_pid.get(pid, 0)
                self.pagefault_by_pid[pid] = stats.majflt
                diff = stats.majflt - prev
                if config.test_mode:
                  diff += random.getrandbits(3)
                if not diff:
                    continue
                if stats.cmd in config.cmd_blacklist:
                    diff *= config.blacklist_score_multiplier
                if stats.cmd in config.cmd_whitelist:
                    logging.debug("whitelisted process %s %s %s" % (pid, stats.cmd, diff))
                    diff /= config.whitelist_score_divider
                if diff > max:
                    ## ignore self
                    if pid == getpid():
                        continue
                    max = diff
                    worstpid = (pid, stats.ppid)
                logging.debug("pagefault score: %s, cmd: %s, pid: %s" % (diff, stats.cmd, pid))
        logging.debug("pagefault scan completed - selected pid: %s" % (worstpid and worstpid[0]))
        ## give a bit of protection against whitelisted and innocent processes being stopped
        ## (TODO: hardcoded constants)
        if max > 4.0 / (self.cooldown_counter + 1.0):
            return self.checkParents(*worstpid)

class GlobalProcessSelector(ProcessSelector):
    """
    This is a collection of the various process selectors.
    """
    def __init__(self):
        ## sorted from cheap to expensive.  Also, it is surely smart to be quick on refreezing a recently unfrozen process if host starts thrashing again.
        self.collection = [LastFrozenProcessSelector(), OOMScoreProcessSelector(), PageFaultingProcessSelector()]
        self.scan_method_count = 0

    def update(self, prev, cur):
        if cur.unfrozen_pid:
            self.scan_method_count = 0
        for c in self.collection:
            c.update(prev, cur)

    def scan(self):
        logging.debug("scan_processes")

        ## a for loop here to make sure we fall back on the next method if the first method fails to find anything.
        for i in range(0,len(self.collection)):
            logging.debug("scan method: %s" % (self.scan_method_count % len(self.collection)))
            ret = self.collection[self.scan_method_count % len(self.collection)].scan()
            self.scan_method_count += 1
            if ret:
                return ret
    logging.debug("found nothing to stop!? :-(")

def get_date_string():
    if config.date_human_readable:
        return datetime.strftime(datetime.now(), "%Y-%m-%d %H:%M:%S")
    else:
        return str(time.time())

## returns string with detailed process information
def get_process_info(pid):
    try:
        ## check_output is only available from 2.7, and compatibility
        ## with 2.6 is currently a requirement.
        ## TODO: move the import back where it belongs, eventually.
        from subprocess import check_output
        ## TODO: we should fetch this information from /proc filesystem instead of using ps
        info = check_output("ps -p %d uf" % pid, shell = True).decode('utf-8', 'ignore')
        info = info.split('\n')[1]
        info = info.split()
        if len(info) >= 4:
            return "u:%10s  CPU:%5s%%  MEM:%5s%%  CMD: %s" % (info[0], info[2], info[3], ' '.join(info[10:]))
        else:
            return "No information available, the process was probably killed or 'ps' returns unexpected output."
    except:
        logging.error("Could not fetch process user information, the process is probably gone")
        return "problem fetching process information"

def ignore_failure(method):
    def _try_except_pass(*args, **kwargs):
        try:
            method(*args, **kwargs)
        except:
            logging.critical("Exception ignored", exc_info=True)

    return _try_except_pass

## hard coded logic as for now.  One state file and one log file.
## state file can be monitored, i.e. through nagios.  todo: advanced logging
@ignore_failure
def log_frozen(pid):
    with open("/var/log/thrash-protect.log", 'ab') as logfile:
        if config.log_user_data_on_freeze:
            logfile.write(("%s - frozen   pid %5s - %s - list: %s\n" % (get_date_string(), str(pid), get_process_info(pid), frozen_pids)).encode("utf-8"))
        else:
            logfile.write(("%s - frozen pid %s - frozen list: %s\n" % (get_date_string(), pid, frozen_pids)).encode('utf-8'))

    with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
            logfile.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in frozen_pids]) + "\n")

@ignore_failure
def log_unfrozen(pid):
    with open("/var/log/thrash-protect.log", 'ab') as logfile:
        if config.log_user_data_on_unfreeze:
            logfile.write(("%s - unfrozen   pid %5s - %s - list: %s\n" % (get_date_string(), str(pid), get_process_info(pid), frozen_pids)).encode('utf-8'))
        else:
            logfile.write(("%s - unfrozen pid %s\n" % (get_date_string(), pid)).encode('utf-8'))

    if frozen_pids:
        with open("/tmp/thrash-protect-frozen-pid-list", "w") as logfile:
            logfile.write(" ".join([" ".join([str(pid) for pid in pid_group]) for pid_group in frozen_pids]) + "\n")
    else:
        try:
            unlink("/tmp/thrash-protect-frozen-pid-list")
        except (FileNotFoundError, OSError):
            pass

def _debug_check_state(pid, should_be_suspended=False):
    procstate = ProcessSelector().readStat(pid)
    if not procstate and not should_be_suspended:
        return
    if not procstate:
        logging.warn("Pid %s should be suspended, but is gone" % pid)
        return
    is_suspended = 'T' in procstate.state
    if is_suspended != should_be_suspended:
        logging.warn("Pid %s - state: %s, should_be_suspended: %s - mismatch" % (pid, procstate, should_be_suspended))

if config.debug_checkstate:
    debug_check_state = _debug_check_state
else:
    debug_check_state = lambda a,b: None

def freeze_something(pids_to_freeze=None):
    global frozen_pids
    global global_process_selector
    pids_to_freeze = pids_to_freeze or global_process_selector.scan()
    if not pids_to_freeze:
        ## process disappeared. ignore failure
        logging.info("nothing to freeze found, or the process we were going to suspend has already exited")
        return ()
    if not hasattr(pids_to_freeze, '__iter__'):
        pids_to_freeze = (pids_to_freeze,)
    if getpid() in pids_to_freeze:
        logging.error("Oups.  Own pid is next on the list of processes to freeze.  This is very bad.  Skipping.")
        return ()
    for pid_to_freeze in pids_to_freeze:
        try:
            debug_check_state(pid_to_freeze, 0)
            kill(pid_to_freeze, signal.SIGSTOP)
            if len(pids_to_freeze)>1:
                time.sleep(config.max_acceptable_time_delta/3)
        except ProcessLookupError:
            continue
    if not pids_to_freeze in frozen_pids:
        frozen_pids.append(pids_to_freeze)

    for pid_to_freeze in pids_to_freeze:
        ## Logging after freezing - as logging itself may be resource- and timeconsuming.
        ## Perhaps we should even fork it out.
        logging.debug("froze pid %s" % str(pid_to_freeze))
        log_frozen(pid_to_freeze)
    return pids_to_freeze

def unfreeze_something():
    global frozen_pids
    global num_unfreezes
    if frozen_pids:
        ## queue or stack?  Seems like both approaches are problematic
        if num_unfreezes % config.unfreeze_pop_ratio:
            pids_to_unfreeze = frozen_pids.pop()
        else:
            pids_to_unfreeze = frozen_pids.pop(0)
        ## pids_to_unfreeze can be both numeric and tuple
        if not hasattr(pids_to_unfreeze, '__iter__'):
            pids_to_unfreeze = [pids_to_unfreeze]
        else:
            pids_to_unfreeze = list(pids_to_unfreeze)
        logging.debug("pids to unfreeze: %s" % pids_to_unfreeze)
        for pid_to_unfreeze in reversed(pids_to_unfreeze):
            try:
                logging.debug("going to unfreeze %s" % str(pid_to_unfreeze))
                debug_check_state(pid_to_unfreeze, 1)
                kill(pid_to_unfreeze, signal.SIGCONT)
                if len(pids_to_unfreeze)>1:
                    time.sleep(config.max_acceptable_time_delta)
            except ProcessLookupError:
                ## ignore failure
                pass
            log_unfrozen(pid_to_unfreeze)
        num_unfreezes += 1
        return pids_to_unfreeze

def thrash_protect(args=None):
    current = SystemState()
    global frozen_pids
    global global_process_selector

    ## A best-effort attempt on running mlockall()
    try:
        import ctypes
        try:
            assert(not ctypes.cdll.LoadLibrary('libc.so.6').mlockall(ctypes.c_int(7)))
        except:
            assert(not ctypes.cdll.LoadLibrary('libc.so.6').mlockall(ctypes.c_int(3)))
    except:
        logging.warning("failed to do mlockall() - this makes the program vulnerable of being swapped out in an extreme thrashing event (maybe you're not running the script as root?)", exc_info=False)

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
            logging.debug("going to sleep %s" % sleep_interval)
            time.sleep(sleep_interval)
            current.check_delay(sleep_interval)

def unfreeze_from_tmpfile():
    """
    Cleanup - unfreezing pids from last run, if applicable

    this may arguably be harmful, if box has been rebooted, or long
    time has passed, and the pidfile actually contains processes that
    should be frozen.  At the other hand, if thrash-protect dies for
    any reason, and is instantly restarted by systemd, it's probably a
    good thing to start fresh from scratch.  (or maybe the system will
    go insta-thrashed, that would be quite bad indeed).
    """
    try:
        with open("/tmp/thrash-protect-frozen-pid-list", "r") as pidfile:
            logging.info("cleaning up - unfreezing pids from last run")
            pids_to_open = pidfile.read()
            for pid in pids_to_open.split():
                kill(int(pid), signal.SIGCONT)
    except FileNotFoundError:
        pass

def cleanup():
    ## Clean up if exiting due to an exception.
    global frozen_pids
    for pids_to_unfreeze in frozen_pids:
        for pid_to_unfreeze in reversed(pids_to_unfreeze):
            try:
                kill(pid_to_unfreeze, signal.SIGCONT)
            except ProcessLookupError:
                pass
    try:
        unlink("/tmp/thrash-protect-frozen-pid-list")
    except FileNotFoundError:
        pass
    

## Globals ... we've refactored most of them away, but some still remains ...
frozen_pids = []
num_unfreezes = 0
## A singleton ...
global_process_selector = GlobalProcessSelector()

def main():
    ## Parsing arguments (TODO: none provided as for now.  The
    ## configuration passed through environment should also be
    ## possible to pass through parameters)
    try:
        import argparse
        p = argparse.ArgumentParser(description="protect a linux host from thrashing")
        p.add_argument('--version', action='version', version='%(prog)s ' + __version__)
        args = p.parse_args()
    except ImportError:
        ## argparse is only available from 2.7 and up
        args = None

    unfreeze_from_tmpfile()

    try:
        thrash_protect(args)
    finally:
        cleanup()

if __name__ == '__main__':
    if config.debug_logging:
        logging.root.setLevel(10)
    main()

