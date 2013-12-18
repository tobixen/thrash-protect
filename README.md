thrash-protect
=============

Simple-Stupid user-space program protecting a linux host from thrashing.

Usage
-----

No init-script has been written so far.  Script has to be started up
manually after each boot, or from crontab, with root permissions.
Properly installed, the script should be available as
/usr/sbin/thrash-protect

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

This script will be checking the pgmajfault variable in /proc/vmstat
on configurable intervals (i.e. twice a second).  If the number of
page faults changes more than a configurable threshold value (say,
10), the script will actively do "kill -STOP" on the currently running
process doing most of the major page faults.  If the number of page
faults is 0, the script will do "kill -CONT" on the stopped process(es).

The script creates a file on /tmp when there are frozen processes,
nrpe can eventually be set up to monitor the existance of such a file
as well as the existance of suspended processes.

Important processes (say, sshd) can be whitelisted.

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

I realized that both a queue approach and a stack approach has it's
problems (the stack may permanently freeze relatively innocent
processes, the queue is inefficient and causes quite much paging) so I
made some logic "get from the head of the list sometimes, pop from the
tail most of the times".  I guess there are a lot of other ways to
tweak and tune this script, though I'm worried that the simplicity
will go down the drain if doing too much tweaking.

Current implementation is only counting page faults - this should be
modified a bit.  Neither swap in nor swap out is a big problem by
itself, the real problem is when swapin and swapout happens at the
same time.  With the current implementation, when some application
(say, an interactive big thing like emacs or firefox) has been
inactive for a while and has significant amounts of memory have been
moved to swap, resuming the application will become a lot more
sluggish than usual due to the thrash-protect stopping it all the time.

The python script could be tweaked, refactored and optimized a bit
(i.e. using re instead of split, garbage collection of old processes
from the pid/pagefault dict, improved log handling, tweaking the 
monitoring of /proc-files so that the script only kicks in when the 
box is really thrashing (that is, actively moving stuff both into and out 
from swap during an interval) etc) but it would
probably be better to make a C-implementation.

Experiences
-----------

This script is not really production-ready, but still I would
recommend to give it a shot as a temporary stop-gap if you have a
server that have had thrashing problem earlier, and where neither
installing more memory or tweaking processes to eat less memory cannot
be done in a flash.

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

In the 0.5.x version series the focus will be on packaging and
wrapping.

* Should be installable through yum, pacman and apt-get.

* Should include System V startup scripts for debian and rhel, plus
  configuration files for ubuntu upstart and systemd.

* Puppet module

* nrpe scripts

* munin scripts

Focus up until 1.0 is testing, production-hardening, testing, testing,
bugfixing and eventually some tweaking but only if it's _really_
needed.  Some things that should be considered:

* Better handling of the parent suspension problemacy

* Graceful handling of SIGTERM (any suspended processes should be reanimated)

* Recovery on restart (read status file and resume any suspended processes)

* Improved logging and error handling

* Makefile (for "make install", "make rpm", "make PKGBUILD", etc)

