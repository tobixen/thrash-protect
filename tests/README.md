Testing thrash-protect
======================

Unit testing
------------

So far no unit tests have been written up - this is deliberately
postponed due to uncertainity on weather to implement version 1.0 in
python or C.  If I settle down on python, I will populate this
directory with some unit tests.

Unit tests can protect against simple syntax errors and to some extent
it can verify that the functions behave as intended, but it certainly
cannot verify that the script will actually prevent thrashing or not.

Functional testing
------------------

The thought is to populate this directory with some stand-alone
scripts doing functional testing, but I'm not sure how to proceed.
Maybe some simple virtualization technology can be utilized,
i.e. vagrant.

Manual testing of thrash protection
-----------------------------------

It's best to have a dedicated lab box that can be rebooted easily, or
to use dedicated VMs for this purpose.  The box should of course be
set up with swap, preferably several gigabytes.

First we should make sure that we can reproduce the thrashing problem:

* ensure thrash_protect is _not_ running
* open a terminal, run sth like "watch -n 0.3 free -m" to see the memory usage
* you may want to watch uptime as well
* run the thrash_bot.py script from this directory
* observe available memory disappearing
* observe the script starting to eat swap
* observe that the watch process gets frozen (there should be a clock up to the right)
* wait for some minutes (or hours if you want), and verify that reboot is the only option to get control over the box.

Next, check that thrash_protect will work out for you:

* Reboot and start up thrash_protect.
* open a terminal, run sth like "watch -n 0.3 free -m" to see the memory usage again
* you may want to watch uptime as well
* open up another terminal, run "tail -F /var/log/thrash-protect.log"
* start up thrash_bot again.
* observe available memory disappearing
* observe the script starting to eat swap
* observe that thrash protect will log that it's suspending and resuming PIDs
* observe that you still have some degrees of control of the box.
* it should be possible to ssh into the box at all times - and if ssh is suspended, it will hopefully get up within some seconds

Side effects:

* thrash_protect will stop other processes than only thrash_bot
* the terminal windows running watch will become ugly if the watch process gets suspended
* even whitelisted processes may become suspended
* if you're running this on your work station or laptop, your WM and desktop environment may become suspended.

You may want to run several instances of thrash_bot, as well as tune
some of the hard coded constants to see if you can bring the box down.

Manual testing of side effects
------------------------------

The script includes a test mode option (though, I haven't added
argsparse yet - parameters can only be passed through environment
variables) which will cause the script to stop and resume random
processes.  Keep it running for a while in a staging environment to
find out if stopping random processes may cause nasty side effects
other than terminal windows getting messed up.
