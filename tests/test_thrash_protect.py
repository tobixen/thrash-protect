## TODO: I had to add a symlink from thrash-protect to thrash_protect to get this to work ...

import sys

from unittest.mock import patch
import importlib
import signal

#thrash_protect = importlib.import_module('thrash-protect')
import thrash_protect
from nose.tools import assert_equal
thrash_protect.open=True

class TestFreezeUnfreeze:
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
        thrash_protect.freeze_something(10)
        thrash_protect.freeze_something(20)
        thrash_protect.freeze_something(30)

        assert_equal(kill.call_args_list, [((10, signal.SIGSTOP),), ((20, signal.SIGSTOP),), ((30, signal.SIGSTOP),)])
        
        thrash_protect.unfreeze_something()
        thrash_protect.unfreeze_something()
        thrash_protect.unfreeze_something()
        
        ## this is a bit wrong - currently unfreeze_something will also
        ## unfreeze parent processes.  This is going to change, but as
        ## for now we do no asserts on the num_calls.

        ## we also do no asserts of in which order the pids were reanimated.
        call_list = kill.call_args_list
        assert ((10, signal.SIGSTOP),) in call_list
        assert ((20, signal.SIGSTOP),) in call_list
        assert ((30, signal.SIGSTOP),) in call_list

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

        
