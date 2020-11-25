Installation and usage
======================

Requirements
------------

This will only work on linux, it depends on reading stats from the
/proc directory.

The script is made for python 3, but will probably work on 2.5 and
newer.  There exists a branch for python 2.4, but it's not maintained.
No other dependencies.

The box or VM running thrash-protect needs to be set up with swap, or
trash-protect won't do anything useful (even if thrash-like situations
can happen without swap installed).  A reasonably large swap partition
is recommended, possibly twice as much swap as physical memory, though
YMMV, and even a very small swap partition is enough for
thrash-protect to do useful work..

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
will be made available on request.

Configuration
-------------

It should theoretically be possible to configure the script through
environment variables.  This is neither tested nor supported in the
init-scripts by now.  Probably the default configuration will work out
for you.

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
