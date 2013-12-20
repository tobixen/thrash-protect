thrash-protect
=============

Simple-Stupid user-space program protecting a linux host from thrashing.

The program will on fixed intervals check if there has been a lot of
swapping since previous run, and if there are a lot of swapping, the
program with the most page faults will be temporary suspended.  This
way the host will never become so thrashed up that it won't be
possible for a system administrator to ssh into the box and fix the
problems, and in many cases the problems will resolve by themselves.

Installation and usage
----------------------

"make install" will hopefully do the right thing.  Archlinux users may
also install through AUR.  There is a spec file included so it should
be possible to build rpm packages - but "make rpm" probably won't work
out as for now.  Script has to be run as root user.  While it's
possible to adjust configuration through environment variables, best
practice is probably to run it without any configuration.

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

Simple solution
---------------

This script will be checking the pswpin and pswpout variables in
/proc/vmstat on configurable intervals (default: one second).  If both
swap in and swap out is detected within the interval, the program will
STOP the process that has had most major page faults since previous
run - same if there has been significant amounts of swap in or swap
out (default: 100 pages).  When the host has stopped swapping the host
will resume one of the stopped processes.

The script creates a file on /tmp when there are frozen processes,
nrpe can eventually be set up to monitor the existance of such a file
as well as the existance of suspended processes.

Important processes (say, sshd) can be whitelisted.  Note that the
"whitelisting" is done by weighting - the intention is that if a
whitelisted process is particularly nasty, it may still be stopped.

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

