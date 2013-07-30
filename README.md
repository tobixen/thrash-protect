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
unresponsive - to the point that it's often needed to reboot the box.

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

The script should also trigger alarms (i.e. by sending email, SMS or
trigging an alarm in an external monitoring system, like
nagios/icinga).

Important processes (say, sshd) can be whitelisted.

With this approach, hopefully the most-thrashing processes will be
breaked sufficiently that it will always be possible to ssh into a
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
from the pid/pagefault dict, improved log handling, etc) but it would
probably be better to make a C-implementation.

Drawbacks
---------

* Some parent processes may behave unexpectedly when the children gets
  suspended.
