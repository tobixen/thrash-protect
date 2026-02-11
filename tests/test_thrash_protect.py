## TODO: I had to add a symlink from thrash-protect to thrash_protect to get this to work ...

import argparse
import json
import logging
import os
import signal
import tempfile
import time
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch

import pytest

import thrash_protect

# Save reference to real function before autouse fixture patches it
_real_detect_swap_storage_type = thrash_protect.detect_swap_storage_type


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test.

    Mocks detect_swap_storage_type to return None so SSD auto-detection
    doesn't change swap_page_threshold during tests (tests are calibrated
    for the default threshold=4).
    """
    thrash_protect._tp.reset()
    with patch("thrash_protect.detect_swap_storage_type", return_value=None):
        yield
    # Clean up after test
    thrash_protect._tp.reset()


class FileMockup:
    def __init__(self, files_override={}, write_failure=False):
        self.file_mocks = {}
        self.write_failure = write_failure
        self.files = {
            "/proc/10/stat": b"10 (cat) R 9 11054 16079 34823 11054 4202496 122 0 321 0 0 0 0 0 20 0 1 0 26355394 21721088 272 18446744073709551615 4194304 4239532 140734890833696 140734890833192 139859681691520 0 0 0 0 0 0 0 17 1 0 0 0 0 0 6340112 6341396 9216000 140734890837648 140734890837668 140734890837668 140734890840043 0\n",
            "/proc/9/stat": b"9 (bash) S 16077 16079 16079 34823 19840 4202496 5108 15681 0 1 22 3 64 17 20 0 1 0 20562390 31199232 1440 18446744073709551615 4194304 4959876 140736853723312 140736853721992 139725430523274 0 65536 3670020 1266777851 18446744071579314463 0 0 17 0 0 0 7 0 0 7060960 7076956 39927808 140736853724444 140736853724449 140736853724449 140736853725166 0\n",
            ## this stat file actually contains broken utf-8, ref https://github.com/tobixen/thrash-protect/issues/25#issuecomment-473707329
            "/proc/16077/stat": b"16077 (\xd0\x99\xd0\xa6\xd0\xa3\xd0\x9a\xd0\x95\xd0\x9d\xd0\x93\xd0) S 3451 16077 16077 0 -1 4202496 1915 87 0 0 49 21 0 0 20 0 1 0 20562367 89366528 2592 18446744073709551615 4194304 4674684 140733398864064 140733398863096 139952016082147 0 0 2097153 86022 18446744071580791049 0 0 17 0 0 0 0 0 0 6774128 6808520 20299776 140733398867312 140733398867318 140733398867318 140733398867945 0",
            "/tmp/thrash-protect-frozen-pid-list": b"1234 2345 3456",
        }
        self.files.update(files_override)

    def open(self, fn, mode="r"):
        if mode.startswith("r") and fn in self.files:
            if mode == "rb":
                return BytesIO(self.files[fn])
            else:
                return StringIO(self.files[fn].decode("utf-8", "ignore"))
        elif mode.startswith("r"):
            raise FileNotFoundError
        elif mode.startswith("w") or mode.startswith("a"):
            if self.write_failure:
                raise OSError
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

    @patch("thrash_protect.kill")
    @patch("thrash_protect.open")
    @patch("thrash_protect.unlink")
    def test_simple_freeze_unfreeze(self, unlink, open, kill):
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

    @patch("logging.critical")
    @patch("thrash_protect.kill")
    @patch("thrash_protect.unlink")
    def test_unfreeze_log_failure(self, unlink, kill, critical):
        """A logging failure during unfreeze should not cause the application
        to exit, but should log criticals. Note: log_frozen is important and
        will raise on failure, while log_unfrozen uses @ignore_failure.
        """
        with patch("thrash_protect.open", new=FileMockup().open):
            # Freeze works normally
            thrash_protect.freeze_something((10, 20, 30))

        # Now make open fail for unfreeze logging
        with patch("thrash_protect.open", new=FileMockup(write_failure=True).open):
            thrash_protect.unfreeze_something()

        # Should have logged critical for the failed unfreeze log
        assert critical.call_count >= 1

    @patch("logging.critical")
    @patch("thrash_protect.kill")
    @patch("thrash_protect.unlink")
    def test_tuple_freeze_unfreeze(self, unlink, kill, critical):
        with patch("thrash_protect.open", new=FileMockup().open) as open:
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
            ((10, signal.SIGSTOP),),
            ((20, signal.SIGSTOP),),
            ((30, signal.SIGSTOP),),
            ((30, signal.SIGCONT),),
            ((20, signal.SIGCONT),),
            ((10, signal.SIGCONT),),
        ]

    @patch("thrash_protect.open", new=FileMockup().open)
    @patch("thrash_protect.kill")
    def test_unfreeze_from_tmpfile(self, kill):
        thrash_protect.unfreeze_from_tmpfile()
        assert kill.call_args_list == [((1234, signal.SIGCONT),), ((2345, signal.SIGCONT),), ((3456, signal.SIGCONT),)]

    @patch("thrash_protect.open", new=FileMockup().open)
    def test_read_stat(self):
        stat_ret = thrash_protect.ProcessSelector().readStat("/proc/10/stat")
        assert stat_ret.cmd == "cat"
        assert stat_ret.state == "R"
        assert stat_ret.majflt == 321
        assert stat_ret.ppid == 9
        stat_ret2 = thrash_protect.ProcessSelector().readStat(10)
        assert stat_ret == stat_ret2

    @patch("thrash_protect.open", new=FileMockup().open)
    def test_check_parents(self):
        assert thrash_protect.ProcessSelector().checkParents(9) == (9,)
        assert thrash_protect.ProcessSelector().checkParents(9, 16077) == (9,)
        assert thrash_protect.ProcessSelector().checkParents(10) == (9, 10)
        assert thrash_protect.ProcessSelector().checkParents(10, 9) == (9, 10)

    def test_check_parents_login_shell(self):
        """Test that login shells with '-' prefix are detected correctly."""
        # Login shells have a '-' prefix (e.g., '-bash' instead of 'bash')
        login_shell_files = {
            "/proc/100/stat": b"100 (claude) S 99 100 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/99/stat": b"99 (-bash) S 1 99 99 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
        }
        with patch("thrash_protect.open", new=FileMockup(login_shell_files).open):
            # The parent shell is '-bash' (login shell), should still be detected
            result = thrash_protect.ProcessSelector().checkParents(100)
            assert result == (99, 100), f"Expected (99, 100), got {result}"

    def test_is_kernel_thread(self):
        """Test _is_kernel_thread() detects kthreadd and its children."""
        ps = thrash_protect.ProcessSelector()
        procstat = ps.procstat

        # pid 2 (kthreadd itself, ppid 0)
        assert ps._is_kernel_thread(2, procstat(cmd="kthreadd", state="S", majflt=0, ppid=0))
        # kernel worker thread (ppid 2)
        assert ps._is_kernel_thread(123, procstat(cmd="kworker/0:1", state="S", majflt=0, ppid=2))
        # normal userspace process
        assert not ps._is_kernel_thread(1000, procstat(cmd="cat", state="R", majflt=100, ppid=999))

    def test_is_frozen_sigstop(self):
        """Test _is_frozen detects SIGSTOP-frozen processes."""
        ps = thrash_protect.ProcessSelector()
        procstat = ps.procstat
        assert ps._is_frozen(100, procstat(cmd="cat", state="T", majflt=0, ppid=1))
        assert not ps._is_frozen(100, procstat(cmd="cat", state="R", majflt=0, ppid=1))

    def test_is_frozen_cgroup(self):
        """Test _is_frozen detects cgroup-frozen processes."""
        ps = thrash_protect.ProcessSelector()
        procstat = ps.procstat
        cgroup_path = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/tmux.scope"

        # Not frozen when frozen_cgroup_paths is empty
        with patch("thrash_protect.get_cgroup_path", return_value=cgroup_path):
            assert not ps._is_frozen(100, procstat(cmd="cat", state="S", majflt=0, ppid=1))

        # Frozen when cgroup is in frozen_cgroup_paths
        thrash_protect._tp.frozen_cgroup_paths.add(cgroup_path)
        with patch("thrash_protect.get_cgroup_path", return_value=cgroup_path):
            assert ps._is_frozen(100, procstat(cmd="cat", state="S", majflt=0, ppid=1))

    def test_oom_scan_skips_kernel_threads(self):
        """Test that OOMScoreProcessSelector.scan() skips kernel threads."""
        kernel_files = {
            # kthreadd (pid 2, ppid 0) with high oom_score
            "/proc/2/stat": b"2 (kthreadd) S 0 0 0 0 -1 2129984 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/2/oom_score": b"900\n",
            # kworker (pid 50, ppid 2) with high oom_score
            "/proc/50/stat": b"50 (kworker/0:1) S 2 0 0 0 -1 69238880 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/50/oom_score": b"800\n",
            # normal process (pid 500, ppid 1) with low oom_score
            "/proc/500/stat": b"500 (cat) R 1 500 500 0 -1 4202496 50 0 100 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/500/oom_score": b"100\n",
        }
        with patch("thrash_protect.open", new=FileMockup(kernel_files).open):
            with patch("glob.glob", return_value=["/proc/2/oom_score", "/proc/50/oom_score", "/proc/500/oom_score"]):
                result = thrash_protect.OOMScoreProcessSelector().scan()
                # Should select pid 500 (normal process), not 2 or 50 (kernel threads)
                assert result is not None
                assert 500 in result
                assert 2 not in result
                assert 50 not in result

    @patch("thrash_protect.kill")
    @patch("thrash_protect.unlink")
    def test_cleanup(self, unlink, kill):
        # Use the unified frozen_items format: ('sigstop', pids) tuples
        thrash_protect._tp.frozen_items = [
            ("sigstop", (10,)),
            ("sigstop", (20, 30)),
            ("sigstop", (40,)),
        ]
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
        with patch("glob.glob", return_value=["/proc/%i/oom_score" % self.unused_pid]):
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

    @patch("thrash_protect.log_frozen")
    @patch("thrash_protect.log_unfrozen")
    def test_simple_freeze_unfreeze(self, log_unfrozen, log_frozen):
        """This test should assert that suspension and resuming works."""
        ## Freezing something 6 times (to make sure we pass the default
        ## unfreeze_pop_ratio)
        my_frozen_pids = []
        prev = thrash_protect.SystemState()
        time.sleep(1)
        current = thrash_protect.SystemState()
        for i in range(0, 6):
            thrash_protect._tp.process_selector.update(prev, current)
            my_frozen_pids.append(thrash_protect.freeze_something())

        frozen_calls = log_frozen.call_args_list
        assert len(frozen_calls) >= 6

        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert my_frozen_pids

        for pids in my_frozen_pids:
            for pid in pids:
                assert thrash_protect.ProcessSelector().readStat(pid).state == "T"

        ## Unfreeze
        for i in range(0, 6):
            thrash_protect.unfreeze_something()

        assert log_frozen.call_args_list == frozen_calls
        assert len(log_unfrozen.call_args_list) == len(log_frozen.call_args_list)

        thrash_protect._tp.process_selector.update(current, thrash_protect.SystemState())

        ## once again, to make sure the "unfreeze_last_frozen" also gets excersised
        for i in range(0, 6):
            my_frozen_pids.append(thrash_protect.freeze_something())
        my_frozen_pids = [x for x in my_frozen_pids if x]
        assert my_frozen_pids
        ## Unfreeze
        for i in range(0, 6):
            thrash_protect.unfreeze_something()


class TestConfigurationSystem:
    """Tests for the new configuration system."""

    def test_parse_bool(self):
        """Test _parse_bool helper function."""
        # Boolean inputs
        assert thrash_protect._parse_bool(True) is True
        assert thrash_protect._parse_bool(False) is False

        # Integer inputs
        assert thrash_protect._parse_bool(1) is True
        assert thrash_protect._parse_bool(0) is False

        # String inputs (true values)
        assert thrash_protect._parse_bool("true") is True
        assert thrash_protect._parse_bool("True") is True
        assert thrash_protect._parse_bool("TRUE") is True
        assert thrash_protect._parse_bool("yes") is True
        assert thrash_protect._parse_bool("1") is True
        assert thrash_protect._parse_bool("on") is True

        # String inputs (false values)
        assert thrash_protect._parse_bool("false") is False
        assert thrash_protect._parse_bool("no") is False
        assert thrash_protect._parse_bool("0") is False
        assert thrash_protect._parse_bool("off") is False
        assert thrash_protect._parse_bool("anything_else") is False

    def test_parse_list(self):
        """Test _parse_list helper function."""
        # List input (pass through)
        assert thrash_protect._parse_list(["a", "b", "c"]) == ["a", "b", "c"]

        # String input (space-separated)
        assert thrash_protect._parse_list("sshd bash tmux") == ["sshd", "bash", "tmux"]

        # Empty/None values
        assert thrash_protect._parse_list("") == []
        assert thrash_protect._parse_list("   ") == []
        assert thrash_protect._parse_list(None) == []

    def test_get_shells_from_etc(self):
        """Test get_shells_from_etc() with mock /etc/shells."""
        mock_shells = """# /etc/shells
/bin/bash
/bin/sh
/bin/zsh
/usr/bin/fish
"""
        with patch("builtins.open", return_value=StringIO(mock_shells)):
            shells = thrash_protect.get_shells_from_etc()
            assert "bash" in shells
            assert "sh" in shells
            assert "zsh" in shells
            assert "fish" in shells

    def test_get_shells_from_etc_fallback(self):
        """Test fallback when /etc/shells is missing."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            shells = thrash_protect.get_shells_from_etc()
            assert shells == ["bash", "sh", "zsh", "fish"]

    def test_get_default_whitelist(self):
        """Test get_default_whitelist() includes static whitelist and shells."""
        with patch("thrash_protect.get_shells_from_etc", return_value=["bash", "zsh"]):
            whitelist = thrash_protect.get_default_whitelist()
            # Should include static whitelist entries
            assert "sshd" in whitelist
            assert "tmux" in whitelist
            assert "sway" in whitelist
            # Should include shells
            assert "bash" in whitelist
            assert "zsh" in whitelist

    def test_static_whitelist_includes_login_and_supervisord(self):
        """Test that login and supervisord are in STATIC_WHITELIST."""
        assert "login" in thrash_protect.STATIC_WHITELIST
        assert "supervisord" in thrash_protect.STATIC_WHITELIST

    def test_get_default_jobctrllist(self):
        """Test get_default_jobctrllist() includes shells and sudo."""
        with patch("thrash_protect.get_shells_from_etc", return_value=["bash", "zsh"]):
            jobctrllist = thrash_protect.get_default_jobctrllist()
            assert "bash" in jobctrllist
            assert "zsh" in jobctrllist
            assert "sudo" in jobctrllist

    def test_get_defaults(self):
        """Test get_defaults() returns expected structure."""
        with patch("thrash_protect.get_default_whitelist", return_value=["sshd", "bash"]):
            with patch("thrash_protect.get_default_jobctrllist", return_value=["bash", "sudo"]):
                defaults = thrash_protect.get_defaults()

                assert defaults["interval"] == 0.5
                assert defaults["swap_page_threshold"] == 4
                assert defaults["cmd_whitelist"] == ["sshd", "bash"]
                assert defaults["cmd_jobctrllist"] == ["bash", "sudo"]
                assert defaults["blacklist_score_multiplier"] == 16
                assert defaults["whitelist_score_divider"] == 64
                assert defaults["debug_logging"] is False
                assert defaults["log_user_data_on_unfreeze"] is True

    def test_load_from_env(self):
        """Test loading configuration from environment variables."""
        test_env = {
            "THRASH_PROTECT_INTERVAL": "1.5",
            "THRASH_PROTECT_DEBUG_LOGGING": "true",
            "THRASH_PROTECT_SWAP_PAGE_THRESHOLD": "8",
            "THRASH_PROTECT_CMD_WHITELIST": "sshd bash tmux",
        }
        with patch.dict(os.environ, test_env, clear=False):
            env_config = thrash_protect.load_from_env()

            assert env_config["interval"] == 1.5
            assert env_config["debug_logging"] is True
            assert env_config["swap_page_threshold"] == 8
            assert env_config["cmd_whitelist"] == ["sshd", "bash", "tmux"]

    def test_load_ini_config(self):
        """Test loading INI config file."""
        ini_content = """[thrash-protect]
interval = 2.0
swap_page_threshold = 10
debug_logging = true
cmd_whitelist = sshd bash
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(ini_content)
            f.flush()
            try:
                config = thrash_protect.load_from_file(f.name)
                assert config["interval"] == "2.0"  # INI returns strings
                assert config["swap_page_threshold"] == "10"
                assert config["debug_logging"] == "true"
            finally:
                os.unlink(f.name)

    def test_load_json_config(self):
        """Test loading JSON config file."""
        json_config = {
            "thrash-protect": {
                "interval": 2.0,
                "swap_page_threshold": 10,
                "debug_logging": True,
                "cmd_whitelist": ["sshd", "bash"],
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(json_config, f)
            f.flush()
            try:
                config = thrash_protect.load_from_file(f.name)
                assert config["interval"] == 2.0
                assert config["swap_page_threshold"] == 10
                assert config["debug_logging"] is True
                assert config["cmd_whitelist"] == ["sshd", "bash"]
            finally:
                os.unlink(f.name)

    def test_normalize_file_config(self):
        """Test normalize_file_config() handles key normalization."""
        file_config = {
            "swap-page-threshold": "10",
            "debug-logging": "true",
            "cmd-whitelist": "sshd bash",
        }
        normalized = thrash_protect.normalize_file_config(file_config)
        assert normalized["swap_page_threshold"] == 10
        assert normalized["debug_logging"] is True
        assert normalized["cmd_whitelist"] == ["sshd", "bash"]

    def test_load_config_priority(self):
        """Test configuration priority: CLI > env > file > defaults."""
        # Set up environment
        test_env = {"THRASH_PROTECT_INTERVAL": "1.0"}

        # Create a temporary JSON config file
        json_config = {"thrash-protect": {"interval": 2.0}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(json_config, f)
            f.flush()
            config_file = f.name

        try:
            # Test: file should override default (no env set)
            with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
                with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                    args = argparse.Namespace(config=config_file, interval=None)
                    final, explicitly_set = thrash_protect.load_config(args)
                    assert final["interval"] == 2.0  # from file
                    assert "interval" in explicitly_set

            # Test: env should override file
            with patch.dict(os.environ, test_env, clear=False):
                with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
                    with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                        args = argparse.Namespace(config=config_file, interval=None)
                        final, _ = thrash_protect.load_config(args)
                        assert final["interval"] == 1.0  # from env (overrides file)

            # Test: CLI should override env
            with patch.dict(os.environ, test_env, clear=False):
                with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
                    with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                        args = argparse.Namespace(config=config_file, interval=3.0)
                        final, _ = thrash_protect.load_config(args)
                        assert final["interval"] == 3.0  # from CLI
        finally:
            os.unlink(config_file)

    def test_init_config(self):
        """Test init_config() populates config namespace."""
        with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
            with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                args = argparse.Namespace(config=None, interval=1.5, debug_logging=True)
                # Add all other expected args as None
                for attr in [
                    "debug_checkstate",
                    "swap_page_threshold",
                    "pgmajfault_scan_threshold",
                    "cmd_whitelist",
                    "cmd_blacklist",
                    "cmd_jobctrllist",
                    "blacklist_score_multiplier",
                    "whitelist_score_divider",
                    "unfreeze_pop_ratio",
                    "test_mode",
                    "log_user_data_on_freeze",
                    "log_user_data_on_unfreeze",
                    "date_human_readable",
                    "diagnostic_logging",
                ]:
                    setattr(args, attr, None)

                thrash_protect.init_config(args)

                assert thrash_protect.config.interval == 1.5
                assert thrash_protect.config.debug_logging is True
                assert thrash_protect.config.max_acceptable_time_delta == 1.5 / 8.0

    def test_argument_parser(self):
        """Test create_argument_parser() creates valid parser."""
        parser = thrash_protect.create_argument_parser()

        # Test parsing valid arguments
        args = parser.parse_args(["--interval", "2.0", "--debug"])
        assert args.interval == 2.0
        assert args.debug_logging is True

        # Test list arguments
        args = parser.parse_args(["--cmd-whitelist", "sshd", "bash", "tmux"])
        assert args.cmd_whitelist == ["sshd", "bash", "tmux"]

        # Test config file argument
        args = parser.parse_args(["--config", "/etc/custom.yaml"])
        assert args.config == "/etc/custom.yaml"

    def test_load_from_file_missing(self):
        """Test load_from_file() returns empty dict for missing files."""
        config = thrash_protect.load_from_file("/nonexistent/path/config.yaml")
        assert config == {}

    @pytest.mark.skipif(not thrash_protect.HAS_YAML, reason="PyYAML not installed")
    def test_load_yaml_config(self):
        """Test loading YAML config file."""
        yaml_content = """thrash-protect:
  interval: 2.0
  swap_page_threshold: 10
  debug_logging: true
  cmd_whitelist:
    - sshd
    - bash
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            try:
                config = thrash_protect.load_from_file(f.name)
                assert config["interval"] == 2.0
                assert config["swap_page_threshold"] == 10
                assert config["debug_logging"] is True
                assert config["cmd_whitelist"] == ["sshd", "bash"]
            finally:
                os.unlink(f.name)

    @pytest.mark.skipif(not thrash_protect.HAS_TOML, reason="TOML support not available")
    def test_load_toml_config(self):
        """Test loading TOML config file."""
        toml_content = """[thrash-protect]
interval = 2.0
swap_page_threshold = 10
debug_logging = true
cmd_whitelist = ["sshd", "bash"]
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
            f.write(toml_content)
            f.flush()
            try:
                config = thrash_protect.load_from_file(f.name)
                assert config["interval"] == 2.0
                assert config["swap_page_threshold"] == 10
                assert config["debug_logging"] is True
                assert config["cmd_whitelist"] == ["sshd", "bash"]
            finally:
                os.unlink(f.name)


class TestDiagnosticLogging:
    """Tests for diagnostic logging functionality."""

    def test_diagnostic_disabled_by_default(self):
        """Test that diagnostic_log is None (no-op) by default."""
        thrash_protect.init_config(argparse.Namespace(config=None))
        assert thrash_protect.diagnostic_log is None

    def test_diagnostic_enabled(self):
        """Test that diagnostic_log is set to _diagnostic_log when enabled."""
        args = argparse.Namespace(config=None, diagnostic_logging=True)
        thrash_protect.init_config(args)
        assert thrash_protect.diagnostic_log is thrash_protect._diagnostic_log
        # Reset
        thrash_protect.init_config(argparse.Namespace(config=None))

    def test_diagnostic_logs_via_logging_info(self):
        """Test that diagnostic_log outputs via logging.info."""
        args = argparse.Namespace(config=None, diagnostic_logging=True)
        thrash_protect.init_config(args)
        try:
            with patch("logging.info") as mock_info:
                thrash_protect.diagnostic_log("test message")
                mock_info.assert_called_once_with("DIAGNOSTIC: %s" % "test message")
        finally:
            thrash_protect.init_config(argparse.Namespace(config=None))

    def test_diagnostic_argument_parser(self):
        """Test that --diagnostic flag is parsed correctly."""
        parser = thrash_protect.create_argument_parser()
        args = parser.parse_args(["--diagnostic"])
        assert args.diagnostic_logging is True

        args = parser.parse_args([])
        assert args.diagnostic_logging is None


class TestPSI:
    """Tests for PSI (Pressure Stall Information) functionality."""

    def test_is_psi_available(self):
        """Test PSI availability check."""
        # Reset the cache
        thrash_protect._psi_available = None
        result = thrash_protect.is_psi_available()
        # Should return True on modern Linux systems (4.20+)
        assert isinstance(result, bool)

    def test_get_memory_pressure(self):
        """Test reading memory pressure."""
        if not thrash_protect.is_psi_available():
            pytest.skip("PSI not available on this system")
        pressure = thrash_protect.get_memory_pressure()
        assert pressure is not None
        assert "some" in pressure
        assert "full" in pressure
        assert "avg10" in pressure["full"]
        assert "avg60" in pressure["full"]
        assert "avg300" in pressure["full"]
        assert "total" in pressure["full"]

    @patch("thrash_protect.is_psi_available")
    def test_get_memory_pressure_unavailable(self, mock_available):
        """Test get_memory_pressure when PSI not available."""
        mock_available.return_value = False
        result = thrash_protect.get_memory_pressure()
        assert result is None

    def test_hybrid_psi_amplifies_swap(self):
        """Test that PSI weight amplifies moderate swap to trigger detection."""
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (0, 0)
        prev.timer_alert = False

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        # Moderate swap: delta_in=3, delta_out=3 with threshold=4
        # swap_product = (3.1/4) * (3.1/4) = 0.775^2 ≈ 0.600 (below 1.0)
        current.swapcount = (3, 3)
        # High PSI: avg10=15%, psi_weight = 1 + 15/5 = 4.0
        # final = 0.600 * 4.0 = 2.4 > 1.0 → triggers
        current.psi = {"some": {"avg10": 15.0}}

        result = current.check_thrashing(prev)
        assert result is True
        assert current.cooldown_counter == 1

    def test_hybrid_psi_without_swap_does_not_trigger(self):
        """Test that high PSI + zero swap does NOT trigger."""
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (100, 100)
        prev.timer_alert = False
        prev.timestamp = time.time() - 1.0

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        # Zero swap delta
        current.swapcount = (100, 100)
        # Very high PSI
        current.psi = {"some": {"avg10": 50.0}}
        current.timestamp = time.time()
        # swap_product = (0.1/4) * (0.1/4) = 0.000625
        # psi_weight = 1 + 50/5 = 11.0
        # final = 0.000625 * 11.0 = 0.006875 < 1.0 → does NOT trigger

        result = current.check_thrashing(prev)
        assert result is False

    def test_hybrid_requires_swap_evidence(self):
        """Test that PSI alone without swap evidence does not trigger."""
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (50, 50)
        prev.timer_alert = False
        prev.timestamp = time.time() - 1.0

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        # Tiny swap delta (1 page each direction)
        current.swapcount = (51, 51)
        current.psi = {"some": {"avg10": 10.0}}
        current.timestamp = time.time()
        # swap_product = (1.1/4) * (1.1/4) = 0.275^2 ≈ 0.0756
        # psi_weight = 1 + 10/5 = 3.0
        # final = 0.0756 * 3.0 = 0.227 < 1.0 → does NOT trigger

        result = current.check_thrashing(prev)
        assert result is False

    def test_check_thrashing_no_psi(self):
        """Test that --no-psi uses pure swap counting."""
        args = argparse.Namespace(config=None, use_psi=False)
        thrash_protect.init_config(args)

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (0, 0)
        prev.timer_alert = False

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        current.psi = {"some": {"avg10": 50.0}}  # High PSI (should be ignored)
        current.swapcount = (100, 100)  # High swap activity
        current.cooldown_counter = 0

        # With PSI disabled, should use pure swap counting
        result = current.check_thrashing(prev)
        assert result is True  # Swap activity alone should trigger

    def test_hybrid_swap_only_still_works(self):
        """Test that swap-only triggers work when PSI is not available."""
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (0, 0)
        prev.timer_alert = False

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        # No PSI data at all
        current.psi = None
        # Large swap: delta_in=100, delta_out=100 with threshold=4
        # swap_product = (100.1/4) * (100.1/4) ≈ 626 > 1.0
        current.swapcount = (100, 100)

        result = current.check_thrashing(prev)
        assert result is True
        assert current.cooldown_counter == 1

    def test_hybrid_cooldown_uses_swap_not_psi(self):
        """Test that cooldown decrements when swap stops, even if PSI is still elevated."""
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 3
        prev.swapcount = (100, 100)
        prev.timer_alert = False

        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        # Swap stopped (same counts)
        current.swapcount = (100, 100)
        # PSI still elevated (stale avg10)
        current.psi = {"some": {"avg10": 20.0}}
        # Enough time has elapsed
        current.timestamp = time.time()
        prev.timestamp = current.timestamp - 1.0

        # Mock get_sleep_interval to return a small value so the time check passes
        with patch.object(current, "get_sleep_interval", return_value=0.5):
            result = current.check_thrashing(prev)
        assert result is False
        # Cooldown should have decremented despite PSI being elevated
        assert current.cooldown_counter == 2

    def test_psi_uses_some_not_full(self):
        """Test that PSI uses 'some' metric, not 'full'.

        'full' requires ALL CPUs to be stalled simultaneously, which can be
        near-zero even during heavy thrashing on multi-core systems.
        'some' triggers when at least one task is stalled.
        """
        thrash_protect.init_config(argparse.Namespace(config=None))

        prev = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev.cooldown_counter = 0
        prev.swapcount = (0, 0)
        prev.timer_alert = False

        # With 'some' PSI data + moderate swap → should trigger
        current = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        current.swapcount = (3, 3)
        current.psi = {"some": {"avg10": 15.0}}
        result = current.check_thrashing(prev)
        assert result is True

        # With only 'full' PSI data (no 'some') + moderate swap → should NOT amplify
        current2 = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        current2.swapcount = (3, 3)
        current2.psi = {"full": {"avg10": 15.0}}
        current2.cooldown_counter = 0
        current2.timestamp = time.time()
        prev2 = thrash_protect.SystemState.__new__(thrash_protect.SystemState)
        prev2.cooldown_counter = 0
        prev2.swapcount = (0, 0)
        prev2.timer_alert = False
        prev2.timestamp = current2.timestamp - 1.0
        result2 = current2.check_thrashing(prev2)
        # swap_product = (3.1/4)^2 ≈ 0.60 < 1.0, no amplification → False
        assert result2 is False


class TestCgroupPressureSelector:
    """Tests for CgroupPressureProcessSelector."""

    def test_get_cgroup_pressure(self):
        """Test reading cgroup pressure."""
        selector = thrash_protect.CgroupPressureProcessSelector()
        # Get our own cgroup
        pid = os.getpid()
        cgroup_path = thrash_protect.get_cgroup_path(pid)
        if cgroup_path:
            pressure = selector.get_cgroup_pressure(cgroup_path)
            # May be None if memory.pressure doesn't exist
            if pressure is not None:
                assert isinstance(pressure, float)
                assert pressure >= 0

    def test_cgroup_pressure_caching(self):
        """Test that cgroup pressure readings are cached."""
        selector = thrash_protect.CgroupPressureProcessSelector()
        pid = os.getpid()
        cgroup_path = thrash_protect.get_cgroup_path(pid)
        if not cgroup_path:
            pytest.skip("No cgroup path available")

        # First call
        pressure1 = selector.get_cgroup_pressure(cgroup_path)
        # Second call should use cache
        pressure2 = selector.get_cgroup_pressure(cgroup_path)

        # Both should be the same (cached)
        assert pressure1 == pressure2
        # Cache should have one entry
        assert cgroup_path in selector.cgroup_pressure_cache

    def test_selector_in_global_collection(self):
        """Test that CgroupPressureProcessSelector is in the global collection."""
        global_selector = thrash_protect.GlobalProcessSelector()
        selector_types = [type(s).__name__ for s in global_selector.collection]
        assert "CgroupPressureProcessSelector" in selector_types
        # Should be after LastFrozenProcessSelector
        last_frozen_idx = selector_types.index("LastFrozenProcessSelector")
        cgroup_idx = selector_types.index("CgroupPressureProcessSelector")
        assert cgroup_idx == last_frozen_idx + 1

    def test_oom_score_unbiases_large_cgroups(self):
        """Test that OOM score prevents bias toward large aggregate cgroups.

        A chromium renderer in a large session cgroup (high aggregate pressure=5%,
        low oom_score=100) should lose to a claude process in a small cgroup
        (low pressure=2%, high oom_score=800).
        """
        selector = thrash_protect.CgroupPressureProcessSelector()

        files = {
            # chromium renderer: low oom_score, high cgroup pressure
            "/proc/1000/stat": b"1000 (chromium) S 1 1000 1000 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/1000/oom_score": b"100\n",
            "/proc/1000/cgroup": b"0::/user.slice/user-1000.slice/session-1.scope\n",
            # claude: high oom_score, lower cgroup pressure
            "/proc/2000/stat": b"2000 (claude) S 1 2000 2000 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0",
            "/proc/2000/oom_score": b"800\n",
            "/proc/2000/cgroup": b"0::/user.slice/user-1000.slice/user@1000.service/tmux-spawn-abc.scope\n",
        }
        cgroup_pressures = {
            "/sys/fs/cgroup/user.slice/user-1000.slice/session-1.scope": 5.0,
            "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/tmux-spawn-abc.scope": 2.0,
        }

        def mock_get_pressure(cgroup_path):
            return cgroup_pressures.get(cgroup_path)

        with patch("thrash_protect.open", new=FileMockup(files).open):
            with patch("thrash_protect.is_psi_available", return_value=True):
                with patch("glob.glob", return_value=["/proc/1000/stat", "/proc/2000/stat"]):
                    with patch.object(selector, "get_cgroup_pressure", side_effect=mock_get_pressure):
                        result = selector.scan()

        # claude (score=2*800=1600) should win over chromium (score=5*100=500)
        assert result is not None
        assert 2000 in result


class TestCgroupFreezing:
    """Tests for cgroup-based freezing functionality."""

    def test_get_cgroup_path_self(self):
        """Test getting cgroup path for the current process."""
        pid = os.getpid()
        cgroup_path = thrash_protect.get_cgroup_path(pid)
        # Should return a path or None (depending on system configuration)
        if cgroup_path:
            assert cgroup_path.startswith("/sys/fs/cgroup/")
            assert os.path.exists(cgroup_path)

    def test_get_cgroup_path_nonexistent(self):
        """Test getting cgroup path for non-existent process."""
        cgroup_path = thrash_protect.get_cgroup_path(999999999)
        assert cgroup_path is None

    def test_is_cgroup_freezable(self):
        """Test cgroup freezable check."""
        # None path should not be freezable
        assert thrash_protect.is_cgroup_freezable(None) is False
        # Non-existent path should not be freezable
        assert thrash_protect.is_cgroup_freezable("/nonexistent/path") is False

    def test_should_use_cgroup_freeze_regular_process(self):
        """Test that regular processes don't use cgroup freezing."""
        # Current process is not in tmux/screen, should not use cgroup freeze
        pid = os.getpid()
        result = thrash_protect.should_use_cgroup_freeze(pid)
        # Unless we're actually running in tmux, this should be None
        # (The test might return a path if running inside tmux)
        if result:
            assert "tmux-spawn" in result or "screen-" in result

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_use_cgroup_freeze_scope(self, mock_freezable, mock_cgroup):
        """Test that .scope under user@service uses cgroup freezing."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/tmux-spawn-abc123.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result == "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/tmux-spawn-abc123.scope"

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_use_cgroup_freeze_any_scope(self, mock_freezable, mock_cgroup):
        """Test that any .scope under user@service uses cgroup freezing."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/run-12345.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result == "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/run-12345.scope"

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_not_freeze_session_scope(self, mock_freezable, mock_cgroup):
        """Test that session-N.scope (under user-N.slice, not user@) is rejected."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/user-1000.slice/session-1.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result is None

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_not_freeze_system_scope(self, mock_freezable, mock_cgroup):
        """Test that system .scope cgroups are rejected."""
        mock_cgroup.return_value = "/sys/fs/cgroup/system.slice/some-service.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result is None

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_not_use_cgroup_freeze_slice(self, mock_freezable, mock_cgroup):
        """Test that .slice cgroups don't use cgroup freezing (shared by many processes)."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/user-1000.slice"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result is None

    def test_get_all_frozen_pids_empty(self):
        """Test get_all_frozen_pids with no frozen processes."""
        result = thrash_protect.get_all_frozen_pids()
        assert result == []

    def test_get_all_frozen_pids_with_frozen(self):
        """Test get_all_frozen_pids with frozen processes."""
        # Use unified frozen_items format
        thrash_protect._tp.frozen_items = [
            ("sigstop", (10,)),
            ("sigstop", (20, 30)),
            ("cgroup", "/cgroup/path", (40, 50)),
        ]
        result = thrash_protect.get_all_frozen_pids()
        assert (10,) in result
        assert (20, 30) in result
        assert (40, 50) in result
        assert len(result) == 3

    @patch("thrash_protect.kill")
    @patch("thrash_protect.open")
    @patch("thrash_protect.unlink")
    @patch("thrash_protect.unfreeze_cgroup")
    def test_cleanup_with_cgroups(self, mock_unfreeze_cgroup, unlink, open, kill):
        """Test cleanup unfreezes both cgroups and regular pids."""
        # Use unified frozen_items format
        thrash_protect._tp.frozen_items = [
            ("sigstop", (10,)),
            ("sigstop", (20, 30)),
            ("cgroup", "/cgroup/path1", (40,)),
            ("cgroup", "/cgroup/path2", (50, 60)),
        ]
        thrash_protect.cleanup()
        # Should unfreeze both cgroups
        assert mock_unfreeze_cgroup.call_count == 2
        # Should SIGCONT regular pids (10, 30, 20 - in reverse order for tuples)
        assert kill.call_count == 3

    @patch("thrash_protect.freeze_cgroup", return_value=True)
    @patch("thrash_protect.should_use_cgroup_freeze")
    @patch("thrash_protect.open")
    @patch("thrash_protect.unlink")
    def test_no_duplicate_cgroup_frozen_items(self, unlink, open, mock_should_use, mock_freeze):
        """Test that freezing the same cgroup twice creates only one entry."""
        cgroup_path = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/tmux-spawn-abc.scope"
        mock_should_use.return_value = cgroup_path

        thrash_protect.freeze_something((100,))
        thrash_protect.freeze_something((200,))

        cgroup_entries = [item for item in thrash_protect._tp.frozen_items if item[0] == "cgroup"]
        assert len(cgroup_entries) == 1
        assert cgroup_entries[0][1] == cgroup_path


class TestSwapStorageDetection:
    """Tests for SSD/HDD auto-detection and swap threshold adjustment."""

    def test_detect_ssd(self):
        """Test detection of SSD swap device."""
        proc_swaps = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/sda2\tpartition\t8388604\t\t0\t\t-2\n"
        with patch("builtins.open", return_value=StringIO(proc_swaps)):
            with patch("thrash_protect._get_device_rotational", return_value=0):
                result = _real_detect_swap_storage_type()
                assert result == "ssd"

    def test_detect_hdd(self):
        """Test detection of HDD swap device."""
        proc_swaps = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/sda2\tpartition\t8388604\t\t0\t\t-2\n"
        with patch("builtins.open", return_value=StringIO(proc_swaps)):
            with patch("thrash_protect._get_device_rotational", return_value=1):
                result = _real_detect_swap_storage_type()
                assert result == "hdd"

    def test_detect_mixed_returns_hdd(self):
        """Test that mixed SSD+HDD returns 'hdd' (conservative)."""
        proc_swaps = (
            "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
            "/dev/sda2\tpartition\t8388604\t\t0\t\t-2\n"
            "/dev/sdb1\tpartition\t4194304\t\t0\t\t-1\n"
        )
        rotational_values = {"/dev/sda2": 0, "/dev/sdb1": 1}

        def mock_rotational(device):
            return rotational_values.get(device)

        with patch("builtins.open", return_value=StringIO(proc_swaps)):
            with patch("thrash_protect._get_device_rotational", side_effect=mock_rotational):
                result = _real_detect_swap_storage_type()
                assert result == "hdd"

    def test_detect_no_swap(self):
        """Test detection with no swap devices."""
        proc_swaps = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
        with patch("builtins.open", return_value=StringIO(proc_swaps)):
            result = _real_detect_swap_storage_type()
            assert result is None

    def test_detect_proc_swaps_missing(self):
        """Test detection when /proc/swaps is not available."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = _real_detect_swap_storage_type()
            assert result is None

    def test_detect_unresolvable_device(self):
        """Test detection when device rotational info is unavailable."""
        proc_swaps = "Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n/dev/zram0\tpartition\t8388604\t\t0\t\t-2\n"
        with patch("builtins.open", return_value=StringIO(proc_swaps)):
            with patch("thrash_protect._get_device_rotational", return_value=None):
                result = _real_detect_swap_storage_type()
                assert result is None

    def test_ssd_auto_adjusts_threshold(self):
        """Test that SSD detection auto-adjusts swap_page_threshold to 64."""
        with patch("thrash_protect.detect_swap_storage_type", return_value="ssd"):
            args = argparse.Namespace(config=None)
            thrash_protect.init_config(args)
            assert thrash_protect.config.swap_page_threshold == 64
            assert thrash_protect.config.pgmajfault_scan_threshold == 64 * 4

    def test_hdd_keeps_default_threshold(self):
        """Test that HDD detection keeps default swap_page_threshold."""
        with patch("thrash_protect.detect_swap_storage_type", return_value="hdd"):
            args = argparse.Namespace(config=None)
            thrash_protect.init_config(args)
            assert thrash_protect.config.swap_page_threshold == 4

    def test_explicit_threshold_not_overridden_by_ssd(self):
        """Test that explicitly set swap_page_threshold is not overridden by SSD detection."""
        with patch("thrash_protect.detect_swap_storage_type", return_value="ssd"):
            args = argparse.Namespace(config=None, swap_page_threshold=16)
            thrash_protect.init_config(args)
            assert thrash_protect.config.swap_page_threshold == 16

    def test_storage_type_cli_override(self):
        """Test --storage-type CLI flag overrides auto-detection."""
        parser = thrash_protect.create_argument_parser()
        args = parser.parse_args(["--storage-type", "hdd"])
        assert args.storage_type == "hdd"

    def test_load_config_tracks_explicitly_set_keys(self):
        """Test that load_config returns explicitly set keys."""
        args = argparse.Namespace(config=None, interval=2.0, swap_page_threshold=None)
        _, explicitly_set = thrash_protect.load_config(args)
        assert "interval" in explicitly_set
        assert "swap_page_threshold" not in explicitly_set


class TestOOMProtection:
    """Tests for OOM protection / memory exhaustion prediction."""

    def test_read_meminfo(self):
        """Test reading MemAvailable, SwapFree, MemTotal, SwapTotal from /proc/meminfo."""
        meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         1000000 kB\n"
            "MemAvailable:    4000000 kB\n"
            "Buffers:          500000 kB\n"
            "SwapTotal:       8000000 kB\n"
            "SwapFree:        6000000 kB\n"
        )
        with patch("builtins.open", return_value=StringIO(meminfo)):
            result = thrash_protect.read_meminfo()
            assert result == (4000000, 6000000, 16384000, 8000000)

    def test_read_meminfo_missing(self):
        """Test read_meminfo when /proc/meminfo is not available."""
        with patch("builtins.open", side_effect=FileNotFoundError):
            result = thrash_protect.read_meminfo()
            assert result is None

    def test_predictor_first_observation(self):
        """Test that first observation returns None (need two points)."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)
        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            eta = predictor.update_and_predict()
            assert eta is None

    def test_predictor_stable_memory(self):
        """Test that stable memory returns None (not declining)."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)

        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.update_and_predict()

        # Same values = not declining
        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta = predictor.update_and_predict()
                assert eta is None

    def test_predictor_increasing_memory(self):
        """Test that increasing memory returns None."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)

        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.update_and_predict()

        # Memory increased
        with patch("thrash_protect.read_meminfo", return_value=(5000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta = predictor.update_and_predict()
                assert eta is None

    def test_predictor_declining_memory(self):
        """Test ETA calculation with declining memory."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)

        # available = 4M + 6M * 2 = 16M
        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.update_and_predict()

        # 10 seconds later: available = 3M + 5M * 2 = 13M (declined by 3M in 10s)
        with patch("thrash_protect.read_meminfo", return_value=(3000000, 5000000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta = predictor.update_and_predict()
                # decline_rate = 3M / 10s = 300000 kB/s
                # eta = 13M / 300000 ≈ 43.3 seconds
                assert eta is not None
                assert abs(eta - 43.33) < 0.1

    def test_predictor_swap_weight_ssd_vs_hdd(self):
        """Test that swap_weight affects the prediction.

        Higher swap_weight (HDD) makes swap depletion more alarming.
        With swap nearly depleted, HDD should predict faster exhaustion.
        """
        predictor_ssd = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)
        predictor_hdd = thrash_protect.MemoryExhaustionPredictor(swap_weight=4.0, horizon=120, low_pct=100.0)

        # Scenario: swap is nearly depleted and declining
        for predictor in [predictor_ssd, predictor_hdd]:
            with patch("thrash_protect.read_meminfo", return_value=(1000000, 500000, 16000000, 8000000)):
                with patch("time.time", return_value=1000.0):
                    predictor.update_and_predict()

        # 10s later: swap dropped from 500k to 250k
        # SSD: 2M -> 1.5M (decline 500k), eta = 1.5M/50k = 30s
        # HDD: 3M -> 2M (decline 1M), eta = 2M/100k = 20s
        with patch("thrash_protect.read_meminfo", return_value=(1000000, 250000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta_ssd = predictor_ssd.update_and_predict()
                eta_hdd = predictor_hdd.update_and_predict()

        assert eta_ssd is not None
        assert eta_hdd is not None
        assert eta_hdd < eta_ssd

    def test_predictor_should_freeze_within_horizon(self):
        """Test should_freeze returns True when ETA < horizon."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=100.0)

        # First observation
        with patch("thrash_protect.read_meminfo", return_value=(2000000, 1000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.should_freeze()

        # Rapid decline: available goes from 4M to 1.5M in 10s
        # ETA = 1.5M / (250000 kB/s) = 6s -> well within horizon
        with patch("thrash_protect.read_meminfo", return_value=(500000, 500000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                result = predictor.should_freeze()
                assert result is True

    def test_predictor_should_not_freeze_beyond_horizon(self):
        """Test should_freeze returns False when ETA > horizon."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=60, low_pct=100.0)

        # First observation: large available
        with patch("thrash_protect.read_meminfo", return_value=(8000000, 8000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.should_freeze()

        # Tiny decline: 10kB in 10s -> ETA is huge
        with patch("thrash_protect.read_meminfo", return_value=(7999990, 8000000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                result = predictor.should_freeze()
                assert result is False

    def test_predictor_low_pct_filters_high_memory(self):
        """Test that low_pct prevents prediction when memory is plentiful.

        This is the key fix for false positives: when system has plenty of
        memory, normal allocation patterns should not trigger OOM prediction.
        """
        # low_pct=10 means only predict when available < 10% of total
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=10.0)

        # total = 16M + 8M*2 = 32M, 10% = 3.2M
        # available = 4M + 6M*2 = 16M -> 50% of total, well above 10%
        with patch("thrash_protect.read_meminfo", return_value=(4000000, 6000000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.update_and_predict()

        # Declining but still above threshold: available = 3M + 5M*2 = 13M -> 40.6%
        with patch("thrash_protect.read_meminfo", return_value=(3000000, 5000000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta = predictor.update_and_predict()
                # Should be None because 13M/32M = 40.6% > 10%
                assert eta is None

    def test_predictor_low_pct_allows_low_memory(self):
        """Test that prediction works when memory is actually low."""
        predictor = thrash_protect.MemoryExhaustionPredictor(swap_weight=2.0, horizon=120, low_pct=10.0)

        # total = 16M + 8M*2 = 32M, 10% = 3.2M
        # available = 500k + 500k*2 = 1.5M -> 4.7% of total, below 10%
        with patch("thrash_protect.read_meminfo", return_value=(500000, 500000, 16000000, 8000000)):
            with patch("time.time", return_value=1000.0):
                predictor.update_and_predict()

        # Still declining and still below threshold
        with patch("thrash_protect.read_meminfo", return_value=(300000, 400000, 16000000, 8000000)):
            with patch("time.time", return_value=1010.0):
                eta = predictor.update_and_predict()
                # available = 300k + 400k*2 = 1.1M, 3.4% < 10%, should predict
                assert eta is not None

    def test_oom_config_defaults(self):
        """Test OOM protection config defaults."""
        args = argparse.Namespace(config=None)
        thrash_protect.init_config(args)
        assert thrash_protect.config.oom_protection is True
        assert thrash_protect.config.oom_horizon == 120
        assert thrash_protect.config.oom_low_pct == 10.0
        assert thrash_protect.config.oom_swap_weight == 2.0  # default (no HDD detected)
        assert thrash_protect._tp.memory_predictor is not None

    def test_oom_disabled(self):
        """Test that --no-oom-protection disables the predictor."""
        args = argparse.Namespace(config=None, oom_protection=False)
        thrash_protect.init_config(args)
        assert thrash_protect._tp.memory_predictor is None

    def test_oom_swap_weight_hdd(self):
        """Test that HDD storage type sets swap_weight=4.0."""
        with patch("thrash_protect.detect_swap_storage_type", return_value="hdd"):
            args = argparse.Namespace(config=None)
            thrash_protect.init_config(args)
            assert thrash_protect.config.oom_swap_weight == 4.0
            assert thrash_protect._tp.memory_predictor.swap_weight == 4.0

    def test_oom_cli_arguments(self):
        """Test OOM-related CLI arguments."""
        parser = thrash_protect.create_argument_parser()

        args = parser.parse_args(["--no-oom-protection"])
        assert args.oom_protection is False

        args = parser.parse_args(["--oom-horizon", "1800"])
        assert args.oom_horizon == 1800

        args = parser.parse_args(["--oom-swap-weight", "3.0"])
        assert args.oom_swap_weight == 3.0

        args = parser.parse_args(["--oom-low-pct", "15.0"])
        assert args.oom_low_pct == 15.0
