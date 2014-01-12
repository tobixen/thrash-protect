thrash-protect
=============

Simple-Stupid user-space program protecting a linux host from thrashing.

The program will on fixed intervals check if there has been a lot of
swapping since previous run, and if there are a lot of swapping, the
program with the most page faults will be temporary suspended.  This
way the host will never become so thrashed up that it won't be
possible for a system administrator to ssh into the box and fix the
problems, and in many cases the problems will resolve by themselves.

Problem
-------

It's common to add relatively much swap space to linux installations.
Swapping things out is good as long as the the swapped-out data is
really inactive.  Unfortunately, if actively used memory ends up being
swapped out (actively running applications using more memory than
what's available), linux has a tendency to become completely
unresponsive - to the point that it's often needed to reboot the box
through hardware button or remote management.

It can be frustrating enough when it happens on a laptop or a work
station; on a production server it's just unacceptable.

If asking around on how to solve problems with thrashing, the typical
answer would be one out of four:

* Install enough memory!  In the real world, that's not always
  trivial; there may be physical, logistical and economical
  constraints delaying or stopping a memory upgrade.  It may also be
  non-trivial to determinate how much memory one would need to install
  to have "enough" of it.  

* Disable swap.  Together with the advice "install enough memory" this
  is really a safe way (and the only safe way) to prevent thrashing.
  However, in many situations swap can be a very good thing - i.e. if
  having processes with memory leakages, aggressive usage of tmpfs,
  some applications simply expects swap (keeping large datasets in
  memory), etc.  Enabling swap can be a lifesaver when a much-needed
  memory upgrade is delayed.

* Tune the swap amount to prevent thrashing.  This doesn't actually
  work - even a modest amount of swap can be sufficient to cause
  severe thrash situations.

* Restrict your processes with ulimit.  In general it makes sense, but
  doesn't really help against the thrashing problem; if one wants to
  use swap one will risk thrashing.

Simple solution
---------------

This script will be checking the pswpin and pswpout variables in
/proc/vmstat on configurable intervals (default: one second).  If both
swap in and swap out is detected within the interval, the program will
STOP the most nasty process.  The same will happen if there has been
significant amounts of swap in or swap out (default: 100 pages).  When
the host has stopped swapping the host will resume one of the stopped
processes.

Finding the most "nasty" process seems to be a bit non-trivial, as
there is no per-process counters on swapin/swapout.  Perhaps it's
possible to check the delta of the total swap pages used and sum it
together with the number of page faults.  Currently three algorithms
have been implemented and the script uses them in order:

* Number of page faults.  This is non-ideal because a rogue process
  gobbling up memory and swap through write-only operations won't
  cause page faults.  Also, a "page fault" is not the same as swapin -
  it may also happen when a program wants to access data that the
  kernel has postponed loading from disk (typically program code -
  hence one typically gets lots of page fault when starting some
  relatively big application)

* oom_score; intended to catch processes gobbling up memory without
  making significant amounts of page faults.  It has some drawbacks -
  it doesn't target the program behaviour "right now", and it will
  give priority to parent pids - when suspending a process, it may not
  help to simply suspend the parent process.

* Last unfrozen pid.  Of course this can't work as a stand-alone
  solution, but it's a very cheap operation and just the right thing
  to do if the host started swapping heavily just after unfreezing
  some pid.

The script creates a file on /tmp when there are frozen processes,
nrpe can eventually be set up to monitor the existance of such a file
as well as the existance of suspended processes.

Important processes (say, sshd) can be whitelisted, and processes
known to be nasty or unimportant can be blacklisted.  Note that the
"black/whitelisting" is done by weighting - randomly stopping
blacklisted processes may not be sufficient to stop thrashing, and a
whitelisted process may still be particularly nasty and stopped.

With this approach, hopefully the most-thrashing processes will be
slowed down sufficiently that it will always be possible to ssh into a
thrashing box and see what's going on.

Implementation
--------------

A prototype has been made in python - my initial thought was to
reimplement in C for smallest possible footstep, memory consumption
and fastest possible action - though I'm not sure if it's worth the
effort.

Tweaking
--------

I realized that both a queue approach and a stack approach has its
problems (the stack may permanently freeze relatively innocent
processes, the queue is inefficient and causes quite much paging) so I
made some logic "get from the head of the list sometimes, pop from the
tail most of the times".  I guess there are a lot of other ways to
tweak and tune this script, though I'm worried that the simplicity
will go down the drain if doing too much tweaking.

Selecting the process only by maj_page_faults may not be very
accurate.  Should probably use other measurements instead or in
addition, such as /proc/*/oom_score

It's important to do some research to learn if the program would
benefit significantly from being rewritten into C.  If not, the python
script should be tweaked, refactored and optimized a bit (i.e. using
re instead of split, garbage collection of old processes from the
pid/pagefault dict, read more options, improved log handling, etc).

Experiences
-----------

This script is not really production-ready, but still I would
recommend to give it a shot as a temporary stop-gap if you have a
server that have had thrashing problem earlier, and where the problem
cannot be solved (in a timely manner) by adding more memory.

This script has been run both on my workstation and on production
servers and has saved me from several logins into the remote
management interface and the servers from being rebooted.  Best of
all, I didn't need to do anything except adding a bit more swap and
monitoring the situation - problem resolved itself thanks to this
script.

I've also been provocing thrashing situations by running some script
that gobbles up memory.

Other thoughts
--------------

This should eventually be a kernel-feature - ultra slow context 
switching between swapping processes would probably "solve" a majority 
of thrashing-issues.

Drawbacks and problems
----------------------

* Some parent processes may behave unexpectedly when the children gets
  suspended - particularly, the suspension of interactive programs
  under the login shell (say, "less") may be annoying.

* I've observed situations where parent processes also have gone into
  suspend-mode and been stuck there even as the child process got
  resumed.  I've done a quick work-around on this by always running
  SIGCONT on the session process id and group process id.  This may be
  harmful if you're actively using SIGSTOP on processes having
  children.

* This was supposed to be a rapid prototype, so it doesn't recognize
  any options.  Configuration settings can be given through OS
  environment, but there exists no documentation.  I've always been
  running it without any special configuration.

Roadmap
-------

Focus up until 1.0 is deployment, testing, production-hardening,
testing, testing, bugfixing and eventually some tweaking but only if
it's _really_ needed.  Some things that should be considered:

* Better handling of the parent suspension problemacy

* Graceful handling of SIGTERM (any suspended processes should be reanimated)

* Recovery on restart (read status file and resume any suspended processes)

* Improved logging and error handling

* More work is needed on getting "make rpm" and "make debian" to work.

* Package should include munin plugins

