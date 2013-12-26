Installation and usage
======================

"make install" will hopefully do the right thing and install the
script as a service.  The service will then need to be started and/or
set up to start at boot.

Archlinux users may also install through AUR.  There is a spec file
included so it should be possible to build rpm packages - but "make
rpm" probably won't work out as for now.  I'm working on
debianization.

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
* debian - will contain files necessary for "debianization" and creating .deb-packages for ubuntu and debian
