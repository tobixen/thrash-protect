## TODO: I had to add a symlink from thrash-protect to thrash_protect to get this to work ...

import sys
from unittest.mock import patch, MagicMock
from io import StringIO, BytesIO
import signal
import logging
import time
import os

import pytest

import thrash_protect


class FileMockup:
    def __init__(self, files_override={}, write_failure=False):
        self.file_mocks = {}
        self.write_failure = write_failure
        self.files = {
            '/proc/10/stat': b'10 (cat) R 9 11054 16079 34823 11054 4202496 122 0 321 0 0 0 0 0 20 0 1 0 26355394 21721088 272 18446744073709551615 4194304 4239532 140734890833696 140734890833192 139859681691520 0 0 0 0 0 0 0 17 1 0 0 0 0 0 6340112 6341396 9216000 140734890837648 140734890837668 140734890837668 140734890840043 0\n',
            '/proc/9/stat': b'9 (bash) S 16077 16079 16079 34823 19840 4202496 5108 15681 0 1 22 3 64 17 20 0 1 0 20562390 31199232 1440 18446744073709551615 4194304 4959876 140736853723312 140736853721992 139725430523274 0 65536 3670020 1266777851 18446744071579314463 0 0 17 0 0 0 7 0 0 7060960 7076956 39927808 140736853724444 140736853724449 140736853724449 140736853725166 0\n',
            ## this stat file actually contains broken utf-8, ref https://github.com/tobixen/thrash-protect/issues/25#issuecomment-473707329
            '/proc/16077/stat': b'16077 (\xd0\x99\xd0\xa6\xd0\xa3\xd0\x9a\xd0\x95\xd0\x9d\xd0\x93\xd0) S 3451 16077 16077 0 -1 4202496 1915 87 0 0 49 21 0 0 20 0 1 0 20562367 89366528 2592 18446744073709551615 4194304 4674684 140733398864064 140733398863096 139952016082147 0 0 2097153 86022 18446744071580791049 0 0 17 0 0 0 0 0 0 6774128 6808520 20299776 140733398867312 140733398867318 140733398867318 140733398867945 0',
            '/tmp/thrash-protect-frozen-pid-list': b'1234 2345 3456'
        }
        self.files.update(files_override)

    def open(self, fn, mode):
        if mode.startswith('r') and fn in self.files:
            if mode == 'rb':
                return BytesIO(self.files[fn])
            else:
                return StringIO(self.files[fn].decode('utf-8', 'ignore'))
        elif mode.startswith('r'):
            raise FileNotFoundError
        elif mode.startswith('w') or mode.startswith('a'):
            if self.write_failure:
                raise IOError
            else:
                if fn not in self.file_mocks:
                    self.file_mocks[fn] = MagicMock()
                return self.file_mocks[fn]
        else:
            raise NotImplementedError


class TestUnitTest:
    """
    pure unit tests.  Should not have any side effects.
    """
    @patch('thrash_protect.getpgid')
    @patch('thrash_protect.getsid')
    @patch('thrash_protect.kill')
    @patch('thrash_protect.open')
    @patch('thrash_protect.unlink')
    def test_simple_freeze_unfreeze(self, unlink, open, kill, getsid, getpgid):
        """This test should assert that os.kill is called appropriately when
        freezing and unfreezing pids.  We'll keep it there as for now.
        Pure unit test, no side effects or system calls should be done
        here.
        """
        ## Freezing something 6 times (to make sure we pass the default
        ## unfreeze_pop_ratio)
        for i in range(1, 7):
            thrash_protect.freeze_something(i * 10)

        assert kill.call_args_list == [((i * 10, signal.SIGSTOP),) for i in range(1, 7)]

        ## Unfreeze
        for i in range(0, 6):
            thrash_protect.unfreeze_something()

        ## we do no asserts of in which order the pids were reanimated.
        call_list = kill.call_args_list
        assert len(call_list) == 12
        for i in range(1, 7):
            assert ((i * 10, signal.SIGSTOP),) in call_list

    @patch('logging.critical')
    @patch('thrash_protect.open', new=FileMockup(write_failure=True).open)
    @patch('thrash_protect.getpgid')
    @patch('thrash_protect.getsid')
    @patch('thrash_protect.kill')
    @patch('thrash_protect.unlink')
    def test_tuple_freeze_unfreeze_log_failure(self, unlink, kill, getsid, getpgid, critical):
        """A logging failure should not cause the application to exit, but
        should log criticals
        """
        self._test_tuple_freeze_unfreeze(kill)
        assert critical.call_count == 6

    @patch('logging.critical')
    @patch('thrash_protect.getpgid')
    @patch('thrash_protect.getsid')
    @patch('thrash_protect.kill')
    @patch('thrash_protect.unlink')
    def test_tuple_freeze_unfreeze(self, unlink, kill, getsid, getpgid, critical):
        with patch('thrash_protect.open', new=FileMockup().open) as open:
            self._test_tuple_freeze_unfreeze(kill)
            assert ("10 20 30\n",) == open("/tmp/thrash-protect-frozen-pid-list", "w").__enter__().write.call_args.args
            assert critical.call_count == 0

    def _test_tuple_freeze_unfreeze(self, kill):
        """In some cases the parent pid does not like the child to be
        suspended (processes implementing job control, like an
        interactive bash session).  To cater for this, we'll need to
        make sure the parent is suspended before the child, and that
        the child is resumed before the parent.  Such pairs (or even
        longer chains) should be handled as tuples.
        """
        thrash_protect.freeze_something((10, 20, 30))
        thrash_protect.unfreeze_something()

        assert kill.call_args_list == [
            ((10, signal.SIGSTOP),), ((20, signal.SIGSTOP),), ((30, signal.SIGSTOP),),
            ((30, signal.SIGCONT),), ((20, signal.SIGCONT),), ((10, signal.SIGCONT),)]

    @patch('thrash_protect.open', new=FileMockup().open)
    @patch('thrash_protect.kill')
    def test_unfreeze_from_tmpfile(self, kill):
        thrash_protect.unfreeze_from_tmpfile()
        assert kill.call_args_list == [
            ((1234, signal.SIGCONT),), ((2345, signal.SIGCONT),), ((3456, signal.SIGCONT),)
        ]

    @patch('thrash_protect.open', new=FileMockup().open)
    def test_read_stat(self):
        stat_ret = thrash_protect.ProcessSelector().readStat('/proc/10/stat')
        assert stat_ret.cmd == 'cat'
        assert stat_ret.state == 'R'
        assert stat_ret.majflt == 321
        assert stat_ret.ppid == 9
        stat_ret2 = thrash_protect.ProcessSelector().readStat(10)
        assert stat_ret == stat_ret2

    @patch('thrash_protect.open', new=FileMockup().open)
    def test_check_parents(self):
        assert thrash_protect.ProcessSelector().checkParents(9) == (9,)
        assert thrash_protect.ProcessSelector().checkParents(9, 16077) == (9,)
        assert thrash_protect.ProcessSelector().checkParents(10) == (9, 10)
        assert thrash_protect.ProcessSelector().checkParents(10, 9) == (9, 10)

    @patch('thrash_protect.kill')
    @patch('thrash_protect.unlink')
    def test_cleanup(self, unlink, kill):
        thrash_protect.frozen_pids = [(10,), (20, 30), (40,)]
        thrash_protect.cleanup()
        assert len(kill.call_args_list) == 4
        assert len(unlink.call_args_list) == 1


class TestUncleanUnitTest:
    """
    Those unit tests will do some probing into the system, but should
    not require root and should not have any side effects.  Because
    mockups are tedious.
    """
    def _find_unused_pid(self):
        """Some tests requires a pid that is not in use.  Let's do an attempt
        on finding a pid that is unlikely to be in use.
        """
        pid = os.getpid()
        if pid < 1024:
            pid += 30000
        while True:
            pid -= 1
            ## Either we're running on a very weird very overloaded system,
            ## or something is wrong in this very algorithm
            assert pid > 1
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                self.unused_pid = pid
                return pid
            except PermissionError:
                pass

    def setup_method(self):
        self._find_unused_pid()

    def test_read_stat_self(self):
        read_stat = thrash_protect.ProcessSelector().readStat
        my_stats = read_stat(os.getpid())
        logging.info("my cmd is " + my_stats.cmd)
        parent_stats = read_stat(my_stats.ppid)
        logging.info("my parent cmd is " + parent_stats.cmd)
        ## No asserts done here.  Probably cmd is "pytest" and parent cmd
        ## is "bash", but we cannot assert that.
        ## read_stats can accept both a file name and an integer
        my_stats2 = read_stat("/proc/%i/stat" % os.getpid())
        assert my_stats2.cmd == my_stats.cmd
        assert my_stats2.ppid == my_stats.ppid

    def test_read_stat_non_existent(self):
        read_stat = thrash_protect.ProcessSelector().readStat
        should_be_none = read_stat(self.unused_pid)
        assert should_be_none is None

    def test_oom_score_process_selector_process_not_found(self):
        ## this could earlier crash if a process exited while the algorithm was running
        with patch('glob.glob', return_value=["/proc/%i/oom_score" % self.unused_pid]):
            thrash_protect.OOMScoreProcessSelector().scan()


@pytest.mark.skipif(os.geteuid() != 0, reason="Functional tests require root")
class TestRootFuncTest:
    """Making good unit tests that doesn't have side effects is sometimes
    not really trivial.  We'll allow the methods in this class to have
    side effects.  Also, those tests have to be run as root.  That's
    not recommended practice.  Don't run this as root in a production
    environment, and don't blame me if anything goes kaboom when
    running this as root.
    """

    @patch('thrash_protect.log_frozen')
    @patch('thrash_protect.log_unfrozen')
    def test_simple_freeze_unfreeze(self, log_unfrozen, log_frozen):
        """This test should assert that suspension and resuming works.
        """
        ## Freezing something 6 times (to make sure we pass the default
        ## unfreeze_pop_ratio)
        my_frozen_pids = []
        prev = thrash_protect.SystemState()
        time.sleep(1)
        current = thrash_protect.SystemState()
        for i in range(0, 6):
            thrash_protect.global_process_selector.update(prev, current)
            my_frozen_pids.append(thrash_protect.freeze_something())

        frozen_calls = log_frozen.call_args_list
        assert len(frozen_calls) >= 6

        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert my_frozen_pids

        for pids in my_frozen_pids:
            for pid in pids:
                assert thrash_protect.ProcessSelector().readStat(pid).state == 'T'

        ## Unfreeze
        for i in range(0, 6):
            thrash_protect.unfreeze_something()

        assert log_frozen.call_args_list == frozen_calls
        assert len(log_unfrozen.call_args_list) == len(log_frozen.call_args_list)

        thrash_protect.global_process_selector.update(current, thrash_protect.SystemState())

        ## once again, to make sure the "unfreeze_last_frozen" also gets excersised
        for i in range(0, 6):
            my_frozen_pids.append(thrash_protect.freeze_something())
        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert my_frozen_pids
        ## Unfreeze
        for i in range(0, 6):
            thrash_protect.unfreeze_something()
