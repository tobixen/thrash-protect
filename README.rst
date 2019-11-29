thrash-protect
==============

Simple-Stupid user-space program protecting a linux host from
thrashing.  It's supposed both to be used as an "insurance" on systems
that aren't expected to thrash and as a stop-gap measure on hosts
where thrashing has been observed.

The script attempts to detect thrashing situations and stop rogue
processes for short intervals.  It works a bit like the ABS break on
the car - hopefully allowing a sysadmin to get control over the
situation despite the thrashing - or eventually letting the box become
slightly degraded instead of completely thrashed (until the rogue
process ends or gets killed by the oom killer).

The commit rate has been fairly low during the last few years - for
the very simple reason that it seems to work well enough.

Problem
-------

It's common to add relatively much swap space to linux installations.
Swapping things out is good as long as the the swapped-out data is
really inactive. Unfortunately, if actively used memory ends up being
swapped out (actively running applications using more memory than what's
available), linux has a tendency to become completely unresponsive - to
the point that it's often needed to reboot the box through hardware
button or remote management.

It can be frustrating enough when it happens on a laptop or a work
station; on a production server it's just unacceptable.

If asking around on how to solve problems with thrashing, the typical
answer would be one out of four:

-  Install enough memory! In the real world, that's not always trivial;
   there may be physical, logistical and economical constraints delaying
   or stopping a memory upgrade. It may also be non-trivial to
   determinate how much memory one would need to install to have
   "enough" of it. Also, no matter how much memory is installed, one
   won't be safe against all the memory getting hogged by some software
   bug.

-  Disable swap. Even together with the advice "install enough memory"
   this is not a fail-safe way to prevent thrashing; without sufficient
   buffers/cache space Linux will start thrashing (ref
   https://github.com/tobixen/thrash-protect/issues/2). It doesn't give
   good protection against all memory getting hogged by some software
   bug, the OOM-killer may kill the wrong process. Also, in many
   situations swap can be a very good thing - i.e. if having processes
   with memory leakages, aggressive usage of tmpfs, some applications
   simply expects swap (keeping large datasets in memory), etc. Enabling
   swap can be a lifesaver when a much-needed memory upgrade is delayed.

-  Tune the swap amount to prevent thrashing. This doesn't actually work,
   even a modest amount of swap can be sufficient to cause severe
   thrash situations.

-  Restrict your processes with ulimit, cgroups or kernel
   parameters. In general it makes sense, but doesn't really help
   against the thrashing problem; if one wants to use swap one will
   risk thrashing.

Simple solution
---------------

In a severe thrash situation, the linux kernel may spend a second
doing context switching just to allow the process to do useful work
for some few milliseconds.  Wouldn't it be better if the process was
allowed to run uninterrupted for some few seconds before the next
context switch?  Thrash-protect attempts to suspend processes for
seconds allowing the non-suspended processes to actually do useful
work.

This script will be checking the pswpin and pswpout variables
/proc/vmstat on configurable intervals to detect thrashing (in the
future, /proc/pressure/memory will probably be used instead).  The
formula is set up so that a lot of unidirectional swap movement or a
little bit of bidirectional swapping within a time interval will
trigger (something like
`(swapin+epsilon)*(swapout+epsilon)>threshold`).  The program will
then STOP the most nasty process. When the host has stopped swapping
the host will resume one of the stopped processes. If the host starts
swapping again, the last resumed PID will be refrozen.

Finding the most "nasty" process seems to be a bit non-trivial, as
there is no per-process counters on swapin/swapout. Currently three
algorithms have been implemented and the script uses them in this
order:

-  Last unfrozen pid, if it's still running. Of course this can't work
   as a stand-alone solution, but it's a very cheap operation and just
   the right thing to do if the host started swapping heavily just after
   unfreezing some pid - hence it's always the first algorithm to run
   after unfreezing some pid.

-  oom\_score; intended to catch processes gobbling up "too much"
   memory. It has some drawbacks - it doesn't target the program
   behaviour "right now", and it will give priority to parent pids -
   when suspending a process, it may not help to simply suspend the
   parent process.

-  Number of page faults. This was the first algorithm I made, but it
   does not catch rogue processes gobbling up memory and swap through
   write-only operations, as that won't cause page faults.  The
   algorithm also came up with false positives, a "page fault" is not
   the same as swapin - it also happens when a program wants to
   access data that the kernel has postponed loading from disk
   (typically program code - hence one typically gets lots of page
   fault when starting some relatively big application). The worst
   problem with this approach is that it requires state about every
   process to be stored in memory, this memory may be swapped out, and
   if the box is really thrashed it may take forever to get through
   this algorithm.

The script creates a file on /tmp when there are frozen processes, nrpe
can eventually be set up to monitor the existence of such a file as well
as the existence of suspended processes.

Important processes (say, sshd) can be whitelisted, and processes
known to be nasty or unimportant can be blacklisted (there are some
default settings on this). Note that the "black/whitelisting" is done
by weighting - randomly stopping blacklisted processes may not be
sufficient to stop thrashing, and a whitelisted process may still be
particularly nasty and stopped.

With this approach, hopefully the most-thrashing processes will be
slowed down sufficiently that it will always be possible to ssh into a
thrashing box and see what's going on.

Experiences
-----------

Even the quite-so-buggy first implementation saved the day.  A heavy
computing job started by our customer had three times caused the need
for a power-cycle.  After implementing thrash-protect it was easy to
identify the "rogue" process and the user that had started it.  I let
the process run - even installed some more swap as it needed it - and
eventually the process completed successfully!

As of 2019 I have several years of experience having thrash-protect
actively suspending processes on dozens of VMs and real computers.
I'm running it everywhere, both on production servers, personal work
stations and laptops.  I can tell that ...

* ... I haven't observed any significant drawbacks with running this
  script

* ... the script definitively has saved us from several power-cyclings

* ... I'm using the log files to identify when it's needed to add more
  memory - I've found this to be a more useful and reliable indicator
  than anything else!

* ... most problems that otherwise would cause severe thrashing
  (i.e. a backup job kicking in at night time, fighting with the
  production application for the available memory) will resolve by
  themselves with thrash-protect running (backup job completing but
  taking a bit longer time and causing some performance degradation in
  the production app, rogue process gobbling up all the memory killed
  off by the OOM-killer, etc).

All this said, the script hasn't been through any thorough
peer-review, and it hasn't been deployed on any massive scale - don't
blame me if you start up this script and anything goes kaboom.

Drawbacks and problems
----------------------
- On hosts actually using swap, every now and then some process will
   be suspended for a short period of time, so it's probably not a
   good idea to use thrash-protect on "real time"-systems (then again,
   you would probably not be using swap or overcommitting memory on a
   "real time"-system).  Many of my colleagues frown upon the idea of
   a busy database server being arbitrarily suspended - but then
   again, on almost any system a database request that normally takes
   milliseconds will every now and then take a couple of seconds, no
   matter if thrash-protect is in use or not.  My experience is that
   such suspendings typically happens once per day or more rarely on
   hosts having "sufficient" amounts of memory, and lasts for a
   fraction of a second.  In most use-cases this is negligible. In
   some cases many processes are suspended for more than a second or
   many times pr hour - but in those circumstances the alternative
   would most likely be an even worse performance degradation or even
   total downtime due to thrashing.

- Some parent processes may behave unexpectedly when the children gets
  suspended, particularly interactive processes under bash - mutt,
  less, even running a minecraft server interactively under bash
  (early work-around: start them directly from screen). We've observed
  one problem with the condor job control system, but we haven't
  checked if the problem was related to thrash-protect. Implementation
  fix: if the parent process name is within a configurable list (with
  sane defaults), then the parent process will be suspended before the
  child process and resumed after the child process has been
  resumed. Please tell if more process names ought to be added to that
  list (perhaps *all* processes should be treated this way).

- Thrash-protect is not optimized to be "fair". Say there are two
   significant processes A and B; letting both of them run causes
   thrashing, suspending one of them stops the thrashing. Probably
   thrash-protect should be flapping between suspending A and
   suspending B. What *may* happen is that process B is flapping
   between suspended and running, while A is allowed to run 100%.

-  I've observed situations where parent processes automatically have
   gone into suspend-mode as the children got suspended and been stuck
   there even as the child process got resumed. I've done a quick
   work-around on this by always running SIGCONT on the session process
   id and group process id. This may be harmful if you're actively using
   SIGSTOP on processes having children.

-  This was supposed to be a rapid prototype, so it doesn't recognize
   any options. Configuration settings can be given through OS
   environment, but there exists no documentation. I've always been
   running it without any special configuration.

-  Usage of mlockall should be made optional. On a system with small
   amounts of RAM (i.e. half a gig) thrash-protect itself can consume
   significant amounts of memory.

-  It seems very unlikely to be related, but it has been reported that
   "swapoff" failed to complete on a server where thrash-protect was
   running.

Other thoughts
--------------

This should eventually be a kernel-feature - ultra slow context
switching between swapping processes would probably "solve" a majority
of thrashing-issues. In a majority of thrashing scenarioes the problem
is too fast context switching between processes, causing insignificant
amount of CPU cycles to be actually be spent on the processes.

Implementation
--------------

A prototype has been made in python - my initial thought was to
reimplement in C for smallest possible footstep, memory consumption and
fastest possible action - though I'm not sure if it's worth the effort.

I very soon realized that both a queue approach and a stack approach on
the frozen pid list has its problems (the stack may permanently freeze
relatively innocent processes, the queue is inefficient and causes quite
much paging) so I made some logic "get from the head of the list
sometimes, pop from the tail most of the times".

I found that I couldn't allow to do a full sleep(sleep\_interval)
between each frozen process if the box was thrashing. I've also
attempted to detect if there are delays in the processing, and let the
script be more aggressive. Unfortunately this change introduced quite
some added complexity.

Some research should eventually be done to learn if the program would
benefit significantly from being rewritten into C - but it seems like
I won't bother, it seems to work well enough in python.

Roadmap
-------

Focus up until 1.0 is deployment, testing, production-hardening,
testing, testing, bugfixing and eventually some tweaking but only if
it's *really* needed.

Some things that SHOULD be fixed before 1.0 is released:

-  Support configuration through command line switches as well as through
   a config file.  Fix official usage documentation to be availabe at --help.

-  Graceful handling of SIGTERM (any suspended processes should be
   reanimated)

-  Recovery on restart (read status file and resume any suspended
   processes)

-  Clean up logging and error handling properly - logging should be done
   through the logging module. Separate error log?

-  More testing, make sure all the code has been tested.  I.e. is the 
   check_delay function useful?

Some things that MAY be considered before 1.0:

-  Add more automated unit tests and functional test code.  
   All parts of the code needs to be exercised, including 
   parsing configuration variables, etc.

-  More "lab testing", and research on possible situations were
   thrash-bot wins over thrash-protect. Verify that the mlockall()
   actually works.

-  Tune for lower memory consumption

-  look into init scripts, startup script and systemd script to ensure
   program is run with "nice -n -20"

-  Look into init scripts, startup script and systemd script to allow
   for site-specific configuration

-  Fix puppet manifest to accept config params

-  look into the systemd service config, can the cgroup swappiness
   configuration be tweaked?

-  Do more testing on parent suspension problems (particularly
   stress-testing with the condor system, testing with other interactive
   shells besides bash, etc)

-  More work is needed on getting "make rpm" and "make debian" to work

-  Package should include munin plugins

Things that eventually may go into 2.0:

-  Replace floats with ints

-  Rewrite to C for better control of the memory footprint

-  Use regexps instead of split (?)

-  Garbage collection of old processes from the pid/pagefault dict

