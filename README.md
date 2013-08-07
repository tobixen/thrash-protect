trash-protect
=============

Simple-Stupid user-space program attempting to protect a linux host from thrashing.

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

The script creates a file on /tmp when there are frozen processes, nrpe 
can eventually be set up to monitor the existance of such a file

Important processes (say, sshd) can be whitelisted.

With this approach, hopefully the most-thrashing processes will be
slowed down sufficiently that it will always be possible to ssh into a
thrashing box and see what's going on.

Implementation
--------------

A prototype has been made in python, but eventually it should be
implemented in C for smallest possible footstep, memory consumption
and fastest possible action.

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
sluggish than usual due to the trash-protect stopping it all the time.

The python script could be tweaked, refactored and optimized a bit
(i.e. using re instead of split, garbage collection of old processes
from the pid/pagefault dict, improved log handling, tweaking the 
monitoring of /proc-files so that the script only kicks in when the 
box is really trashing (that is, actively moving stuff both into and out 
from swap during an interval) etc) but it would
probably be better to make a C-implementation.

Experiences
-----------

This script has been run in production and has saved me from several
logins into the remote management interface and the servers from
being rebooted.  Best of all, I didn't need to do anything except
adding a bit more swap and monitoring the situation - problem resolved 
itself thanks to this script.

Drawbacks
---------

* Some parent processes may behave unexpectedly when the children gets
  suspended - particularly, the suspension of interactive programs
  under the login shell (say, "less") may be annoying.
