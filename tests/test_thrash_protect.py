## TODO: I had to add a symlink from thrash-protect to thrash_protect to get this to work ...

import sys

from unittest.mock import patch
from io import StringIO,BytesIO
#import importlib
import signal
import time
import os
import nose.plugins

#thrash_protect = importlib.import_module('thrash-protect')
import thrash_protect
from nose.tools import assert_equal

class FileMockup():
    def __init__(self, files_override={}):
        self.files = {
            '/proc/10/stat': '10 (cat) R 9 11054 16079 34823 11054 4202496 122 0 321 0 0 0 0 0 20 0 1 0 26355394 21721088 272 18446744073709551615 4194304 4239532 140734890833696 140734890833192 139859681691520 0 0 0 0 0 0 0 17 1 0 0 0 0 0 6340112 6341396 9216000 140734890837648 140734890837668 140734890837668 140734890840043 0\n',
            '/proc/9/stat': '9 (bash) S 16077 16079 16079 34823 19840 4202496 5108 15681 0 1 22 3 64 17 20 0 1 0 20562390 31199232 1440 18446744073709551615 4194304 4959876 140736853723312 140736853721992 139725430523274 0 65536 3670020 1266777851 18446744071579314463 0 0 17 0 0 0 7 0 0 7060960 7076956 39927808 140736853724444 140736853724449 140736853724449 140736853725166 0\n',
            '/proc/16077/stat': '16077 (xterm) S 3451 16077 16077 0 -1 4202496 1915 87 0 0 49 21 0 0 20 0 1 0 20562367 89366528 2592 18446744073709551615 4194304 4674684 140733398864064 140733398863096 139952016082147 0 0 2097153 86022 18446744071580791049 0 0 17 0 0 0 0 0 0 6774128 6808520 20299776 140733398867312 140733398867318 140733398867318 140733398867945 0'
        }
        self.files.update(files_override)

    def open(self, fn, mode):
        if mode.startswith('r') and fn in self.files:
            if mode == 'rb':
                return BytesIO(self.files[fn].encode('utf-8'))
            else:
                return StringIO(self.files[fn])
        elif mode.startswith('r'):
            try:
                raise FileNotFoundError
            except AttributeError:
                raise thrash_protect.FileNotFoundError
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
    def testSimpleFreezeUnfreeze(self, open, kill, getsid, getpgid):
        """This test should assert that os.kill is called appropriately when
        freezing and unfreezing pids.  We'll keep it there as for now.
        Pure unit test, no side effects or system calls should be done
        here.
        """
        ## Freezing something 6 times (to make sure we pass the default
        ## unfreeze_pop_ratio)
        for i in range(1,7):
            thrash_protect.freeze_something(i*10)

        assert_equal(kill.call_args_list, [((i*10, signal.SIGSTOP),) for i in range(1,7)])

        ## Unfreeze
        for i in range(0,6):
            thrash_protect.unfreeze_something()

        ## we do no asserts of in which order the pids were reanimated.
        call_list = kill.call_args_list
        assert_equal(len(call_list), 12)
        for i in range(1,7):
            assert ((i*10, signal.SIGSTOP),) in call_list

    @patch('thrash_protect.getpgid')
    @patch('thrash_protect.getsid')
    @patch('thrash_protect.kill')
    @patch('thrash_protect.open')
    def testTupleFreezeUnfreeze(self, open, kill, getsid, getpgid):
        """
        In some cases the parent pid does not like the child to be suspended (processes implementing job control, like an interactive bash session).  To cater for this, we'll need to make sure the parent is suspended before the child, and that the child is resumed before the parent.  Such pairs (or even longer chains) should be handled as tuples.
        """
        thrash_protect.freeze_something((10, 20, 30))
        thrash_protect.unfreeze_something()
        
        assert_equal(kill.call_args_list, [
            ((10, signal.SIGSTOP),), ((20, signal.SIGSTOP),), ((30, signal.SIGSTOP),),
            ((30, signal.SIGCONT),), ((20, signal.SIGCONT),), ((10, signal.SIGCONT),)])

    @patch('thrash_protect.open', new=FileMockup().open)
    def testReadStat(self):
        stat_ret = thrash_protect.ProcessSelector().readStat('/proc/10/stat')
        assert_equal(stat_ret.cmd, 'cat')
        assert_equal(stat_ret.state, 'R')
        assert_equal(stat_ret.majflt, 321)
        assert_equal(stat_ret.ppid, 9)
        stat_ret2 = thrash_protect.ProcessSelector().readStat(10)
        assert_equal(stat_ret, stat_ret2)

    @patch('thrash_protect.open', new=FileMockup().open)
    def testCheckParents(self):
        assert_equal(thrash_protect.ProcessSelector().checkParents(9),        (9,))
        assert_equal(thrash_protect.ProcessSelector().checkParents(9, 16077), (9,))  
        assert_equal(thrash_protect.ProcessSelector().checkParents(10),       (9, 10))
        assert_equal(thrash_protect.ProcessSelector().checkParents(10, 9),    (9, 10))



class TestFuncTest:
    """
    Making good unit tests that doesn't have side effects is sometimes
    not really trivial.  We'll allow the methods in this class to have
    side effects.
    """
    def __init__(self):
        if os.geteuid():
            raise nose.plugins.skip.SkipTest()

    @patch('thrash_protect.log_frozen')
    @patch('thrash_protect.log_unfrozen')
    def testSimpleFreezeUnfreeze(self, log_unfrozen, log_frozen):
        """This test should assert that suspension and resuming works.
        """
        prev=thrash_protect.SystemState()
        time.sleep(1)
        current=thrash_protect.SystemState()
        thrash_protect.global_process_selector.update(prev, current)
        ## Freezing something 6 times (to make sure we pass the default
        ## unfreeze_pop_ratio)
        my_frozen_pids = []
        for i in range(0,6):
            my_frozen_pids.append(thrash_protect.freeze_something())

        frozen_calls = log_frozen.call_args_list
        assert(len(frozen_calls) >= 6)

        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert(my_frozen_pids)

        for pids in my_frozen_pids:
            for pid in pids:
                assert_equal(thrash_protect.ProcessSelector().readStat(pid).state, 'T')

        ## Unfreeze
        for i in range(0,6):
            thrash_protect.unfreeze_something()

        assert_equal(log_frozen.call_args_list, frozen_calls)
        assert_equal(len(log_unfrozen.call_args_list), len(log_frozen.call_args_list))
        
        thrash_protect.global_process_selector.update(current, thrash_protect.SystemState())

        ## once again, to make sure the "unfreeze_last_frozen" also gets excersised
        for i in range(0,6):
            my_frozen_pids.append(thrash_protect.freeze_something())
        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert(my_frozen_pids)
        ## Unfreeze
        for i in range(0,6):
            thrash_protect.unfreeze_something()
