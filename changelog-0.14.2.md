# Changes from release 0.14.0 to 0.14.2:

Three bugs were reported in https://github.com/tobixen/thrash-protect/issues/37 and one was discovered by the author

* The OOMScoreProcessSelector would cause the process to die with a traceback if one of the processes exited in the middle of processing the OOMScores.

* The "cleanup at end"-logic that is supposed to resume all suspended processes and run if the program exits with an exception was not working.

* The newly implemented "cleanup at start" - reading the temp file and resuming all processes from it - was reported to not work.  I found that it has unit tests, I tested it manually, and could not reproduce this bug.

* Every now and then, "foregrounded" jobs in bash would still get backgrounded.

Based on this, I did some bugfixing, wrote some new tests, and found two other bugs (indentation bugs - one really bad one basically disabling two of the three selector algorithms, one only affecting the logging) that was fixed.

Credits: Ömer An - bounlu@github - bug reporter.
