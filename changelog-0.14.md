# Changes from release 0.13.0 to 0.14.0:

Some few bugfixes, improved documentation, more test code (but still the test coverage is rather poor)

The ChangeLog was nuked, even though it's a standard format.  It never gets updated, and it's optimized for an age prior to RCS, CVS, SVN and git.  It will be replaced with release-specific changelogs.

## Documentation

Lots of tweaks, fixups, rewritings, tweaks to logging and even pointers to competing projects.

credits: pizzonia

## Bugfixes

* Improved error handling on full disk; script will continue running even if log file or tmp file cannot be written.

* thrash-protect should never freeze itself nor it's parent

* OOMScorePorcessSelector should be used before PageFaultingProcessSelector

Github issues fixed:  https://github.com/tobixen/thrash-protect/issues/31 https://github.com/tobixen/thrash-protect/issues/30 https://github.com/tobixen/thrash-protect/issues/36

Credits: Niektory

## Changes to meta files

* Archlinux build updated to keep in sync with the Archlinux project

* Python3 is now required (though, it should still work on python2 I suppose)

* OpenRC-files (for gentoo and some few other distros)

* Support for Ubuntu 20.04

Github issues fixed: https://github.com/tobixen/thrash-protect/pull/32

Credits: Matthew Sharp, questandachievementProjects
