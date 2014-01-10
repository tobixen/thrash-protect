Installation and usage
======================

Requirements
------------

This will only work on linux, it depends on reading stats from the
/proc directory.

python 2.5 or newer is required.  There exists a branch for python
2.4, but it's not maintained.

compile and install
-------------------

As it's in python, no compilation is needed.

"make install" will hopefully do the right thing and install the
script as a service.  

Archlinux users may also install through AUR.  rpm and deb packages
will be made available on request.

configuration
-------------

It should theoretically be possible to configure the script through
environment variables.  This is not tested as for now, and so far
neither supported in the init-scripts.

usage
-----

The service will need to be started and/or set up to start at boot.

If everything else fails, just run the script as root and do whatever
is necessary to ensure it will be started again after next reboot.

While it should be possible to adjust configuration through
environment variables, best practice is probably to run it without any
configuration.

The System V init file is so far quite redhat-specific and may need
tuning for debian or other distributions.

Subdirectories
--------------

The subdirectories contains various logic for deploying the script:

* archlinux - contains logic for submitting to AUR
* systemv - contains a traditional init-script, though it may be rather RedHat-specific as for now
* systemd - contains a service config file for running the script under systemd
* upstart - contains the config file for starting up the script under the (Ubuntu) upstart system
* debian - contains files necessary for "debianization" and creating .deb-packages for ubuntu and debian
