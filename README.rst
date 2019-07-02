thrash-protect
==============

Simple-Stupid user-space program protecting a linux host from thrashing.

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

-  Tune the swap amount to prevent thrashing. This doesn't actually work
   - even a modest amount of swap can be sufficient to cause severe
   thrash situations.

-  Restrict your processes with ulimit or cgroups. In general it makes
   sense, but doesn't really help against the thrashing problem; if one 
   wants to use swap one will risk thrashing.

There is also a fifth avenue that I haven't done any research on: maybe
it's possible to tune the kernel parameters to ensure that malloc(3)
will fail if allocating too much memory. Anyway, that will cause
inefficient resource utilization.

Simple solution
---------------

The reason why the box becomes completely thrashed is that Linux
spends all available resources on context switches; it's not much
smart to spend seconds on context switching and then let the
application run for milliseconds until the next context switch.  By
stopping rogue processes for seconds rather than switching it out for
milliseconds, things will still work despite the thrashing.

This script will be checking the pswpin and pswpout variables in
/proc/vmstat on configurable intervals to detect thrashing.  The
formula is set up so that a lot of unidirectional swap movement or a
little bit of bidirectional swapping within a time interval will trigger (something like
`(swapin+epsilon)*(swapout+epsilon)>threshold`).  The program will then STOP
the most nasty process. When the host has stopped swapping the host
will resume one of the stopped processes. If the host starts swapping
again, the last resumed PID will be refrozen.

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

Experiences
-----------

As of 2018, I feel pretty confident that there are no significant
drawbacks with running this script, and that it almost eliminates the
risk of having to reboot a system due to thrashing.  I'm running it
everywhere, both on personal desktops and production servers at work,
and I've done that for years.

The script has definitively saved us from several power-cyclings. Best
of all, in most of the cases I haven't had to do anything.  Once I
actually added more swap, to let the "rogue" process finish up (it was
an important calculation job started by a customer).  Before
thrash-protect I couldn't even identify the "rogue" process because
the whole server went down too quickly, after thrash-protect it was
easy to spot the "rogue" process, it ate a lot of memory, but it
completed!

Another thing, if thrash protect is creating log lines, it's probably
about time to upgrade the memory on a box.  I've found this to be a
more useful and reliable indicator than anything else!

All this said, the script hasn't been through any thorough
peer-review, and it hasn't been deployed to many systems yet - don't
blame me if you start up this script and anything goes kaboom.

Drawbacks and problems
----------------------

-  Some parent processes may behave unexpectedly when the children gets
   suspended, particularly interactive processes under bash - mutt,
   less, even running a minecraft server interactively under bash
   (work-around: start them directly from screen). We've observed one
   problem with the condor job control system, but we haven't checked if
   the problem was related to thrash-protect. Implementation fix: if the
   parent process name is within a configurable list (with sane defaults),
   then the parent process will be suspended before the child process
   and resumed after the child process has been resumed. Please tell if
   more process names ought to be added to that list.

-  Thrash-protect may be "unfair". Say there are two significant
   processes A and B; letting both of them run causes thrashing,
   suspending one of them stops the thrashing. Probably thrash-protect
   should be flapping between suspending A and suspending B. What may
   happen is that process B is flapping between suspended and running,
   while A is allowed to run 100%.

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
   amounts of RAM (i.e. half a gig) thrash\_protect itself can consume
   significant amounts of memory.

-  It seems very unlikely to be related, but it has been reported that
   "swapoff" failed to complete on a server where thrash-protect was
   running.

Other thoughts
--------------

This should eventually be a kernel-feature - ultra slow context
switching between swapping processes would probably "solve" a majority
of thrashing-issues. In a majority of thrashing scenarioes the problem
is too fast context switching between processes; this causes a very
insignificant amount of CPU cycles to be actually be spent on the
process, while the very most time is spent swapping between processes.

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

