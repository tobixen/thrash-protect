Installation and usage
======================

Requirements
------------

This will only work on linux, it depends on reading stats from the
/proc directory, it depends on python 3 (python 2.5 or higher should probably work - and an old version of the script was backported to python 2.4).

No other dependencies.

The box or VM running thrash-protect needs to be set up with swap, or
trash-protect won't do anything useful (even if thrash-like situations
can happen without swap installed).  A reasonably large swap partition
is recommended, possibly twice as much swap as physical memory, though
YMMV, and even a very small swap partition is enough for
thrash-protect to do useful work.

My original idea was to make a rapid prototype in python, and then
port it over to C for a smaller memory- and CPU footprint; while
thrash-protect has successfully been running on single-CPU instances
with 512M RAM, it's probably best suited on systems with at least 1GB
RAM and multiple CPUs (or CPU cores) due to the overhead costs.

Compile and Install
-------------------

As it's in python, no compilation is needed.

"make install" will hopefully do the right thing and install the
script as a service.

Archlinux users may also install through AUR.  rpm and deb packages
will be made available on request.  There are some logic in the Makefile for creating such packages, but it's poorly tested.


Usage
-----

The service will need to be started and/or set up to start at boot.

If everything else fails, just run the script as root and do whatever
is necessary to ensure it will be started again after next reboot.

While it should be possible to adjust configuration through
environment variables, best practice is probably to run it without any
configuration.

The System V init file is so far quite redhat-specific and may need
tuning for usage with other distributions.

Configuration
-------------

It should be possible to configure the script through environment
variables, though this is poorly tested - the default configuration
has mostly been working out for me.  However, the defaults was made in 2013 and may possibly need a bit of tweaking for state-of-the-art equipment.

Configuration environment variables that may need tweaking:

* THRASH_PROTECT_CMD_WHITELIST - a list of processes that you rather don't want thrash-protect to touch (no guarantees - it just adds a weight).  Defaults to "sshd bash xinit X spectrwm screen SCREEN mutt ssh xterm rxvt urxvt Xorg.bin Xorg systemd-journal".  Can most likely be trimmed down, particularly on servers.  On desktop systems you may want to add more processes, depending on your desktop system.
* THRASH_PROTECT_CMD_BLACKLIST - opposite of whitelist - processes thrash-protect should prioritize to stop.  Defaults to ''.
* THRASH_PROTECT_CMD_JOBCTRLLIST - processes that may be confused if the child process gets suspended.  Defaults to "bash sudo".  You may want to do some research if you use another shell, run bash under some pseudonym, or have other job control systems or experience problems with other processes.  (See the README for details).
* THRASH_PROTECT_INTERVAL - thrash protect is set to sleep for 0.5s between each normal iteration, as long as no thrashing is detected.  This default was set in 2013, perhaps it can be tuned down on modern hardware.
* SWAP_PAGE_THRESHOLD - defaults to 4.  If there is 4 pages swapped in and 4 pages swapped out during the interval, the script will be triggered.  There is also a hard coded constant 10x for single-direction swapping during the interval, so if 40 blocks are swapped in or out, the algorithm will also trigger.  The default was set in 2013, maybe it should be adjusted upwards on swap media with high bandwidth, to prevent thrash-protect from suspending processes when it's not needed.
* THRASH_PROTECT_UNFREEZE_POP_RATIO - default 5.  TLDR: should probably be lowered on interactive desktops and increased on servers doing only batch processing.  All suspended processes are put in a double ended queue (a double ended queue behaves both as a queue and a stack - so the pid is placed at the end of the queue or at the top of the stack according to how you look at it).  If the host has stopped thrashing, the "fair" thing to do would be to always resume the process at the front of the queue (unfreeze_pop_ratio set to 1), but the most effective thing to do is probably to resume and suspend the same process over and over again (unfreeze_pop_ratio set to MAXINT). When set to five it will pop four processes from the top of the stack before it pulls out one process from the front of the queue.
* THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER - default 16.  A blacklisted job will be 16 times more likely to be picked up for suspension than a non-blacklisted job.
* THRASH_PROTECT_WHITELIST_SCORE_MULTIPLIER - default 4 times the blacklist score multiplier.  A non-whitelisted job will by default be 64 times more likely to be choosen for suspension than a whitelisted job.
* THRASH_PROTECT_LOG_USER_DATA_ON_FREEZE - we may log extra process data when freezing processes.  The current code forks up a `ps` subprocess (should be rewritten to just check up /proc/stat).  Since the system may be critically overloaded when we want to freeze a process, it's considered that we probably don't want to do this, so it's defaulted to false.  Note that this is about "hard" logging and the log location is hard coded to /var/log/thrash-protect.log (should probably be consolidated with logging done through the logging module).
* THRASH_PROTECT_LOG_USER_DATA_ON_UNFREEZE - much the same as the former.  Since the system is probably not critically overloaded when we want to unfreeze a process, it's considered that we probably do want this logging, so default is set to true.
* THRASH_PROTECT_DEBUG_LOGGING - leave it turned off, or thrash-protect will log a lot to stderr (trough the logging module).
* THRASH_PROTECT_DEBUG_CHECKSTATE - will log warnings (through the logging module) if processes are in unexpected states, i.e. because two instances of the script is running at the same time.
* THRASH_PROTECT_DATE_HUMAN_READABLE - the early versions of the script logged timestamps in unix format (long int).  Set to 0 if you prefer such timestamps.
* THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD - the script maintains a list of processes and amount of "major page faults" every process has done.  This is a bit expensive process hence it's only done when the global major page fault counter has passed some threshold.  Default set to swap_page_threshold*4.  Can probably be left where it is.
 * THRASH_PROTECT_TEST_MODE - pretend the system is thrashed every now and then, for testing purposes.  This hasn't been exercised for quite some years, should probably be removed.

Monitoring
----------

thrash-protect may relatively safely live it's own life, users will
only notice some delays and slowness, and bad situations will
autorecover (i.e. the resource-consuming process will stop by itself,
or the kernel will finally run out of swap and the OOM-killer will
kill the rogue process).

For production servers, thrash-protect should ideally only be latent,
only occationally stop something very briefly, if it becomes active a
system administrator should manually inspect the box and deal with the
situation, and eventually order more memory.

There are three useful ways to monitor:

* Monitoring the number of suspended processes.  This will possibly
  catch situations where thrash-protect itself has gone haywire,
  suspending processes but unable to reanimate them.  Unfortunately it
  may also cause false alarms on systems where processes are being
  suspended legitimately outside thrash-protect (i.e. due to some
  sysadmin pressing ^Z).

* Monitoring the /tmp/thrash-protect-frozen-pid-list file.  It should
  only exist briefly.

* Age of the /tmp/thrash-protect-frozen-pid-list file; if it exists
  and is old, most likely thrash-protect is not running anymore.

nrpe-scripts and icinga-configuration may be done available on request.

Subdirectories
--------------

The subdirectories contains various logic for deploying the script:

* archlinux - contains logic for submitting to AUR for Arch Linux
* systemv - contains a traditional init-script, though it may be rather RedHat-specific as for now
* systemd - contains a service config file for running the script under systemd
* upstart - contains the config file for starting up the script under the (Ubuntu) upstart system
* debian - contains files necessary for "debianization" and creating .deb-packages for ubuntu and debian
