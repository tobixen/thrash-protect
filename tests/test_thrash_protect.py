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


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset global state before each test."""
    thrash_protect.frozen_pids = []
    thrash_protect.frozen_cgroups = []
    thrash_protect.num_unfreezes = 0
    yield
    # Clean up after test
    thrash_protect.frozen_pids = []
    thrash_protect.frozen_cgroups = []
    thrash_protect.num_unfreezes = 0


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

    @patch("thrash_protect.kill")
    @patch("thrash_protect.unlink")
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
            thrash_protect.global_process_selector.update(prev, current)
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

        thrash_protect.global_process_selector.update(current, thrash_protect.SystemState())

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
                    final = thrash_protect.load_config(args)
                    assert final["interval"] == 2.0  # from file

            # Test: env should override file
            with patch.dict(os.environ, test_env, clear=False):
                with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
                    with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                        args = argparse.Namespace(config=config_file, interval=None)
                        final = thrash_protect.load_config(args)
                        assert final["interval"] == 1.0  # from env (overrides file)

            # Test: CLI should override env
            with patch.dict(os.environ, test_env, clear=False):
                with patch("thrash_protect.get_default_whitelist", return_value=["sshd"]):
                    with patch("thrash_protect.get_default_jobctrllist", return_value=["bash"]):
                        args = argparse.Namespace(config=config_file, interval=3.0)
                        final = thrash_protect.load_config(args)
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
        """Test that any .scope cgroup uses cgroup freezing."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/tmux-spawn-abc123.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result == "/sys/fs/cgroup/user.slice/tmux-spawn-abc123.scope"

    @patch("thrash_protect.get_cgroup_path")
    @patch("thrash_protect.is_cgroup_freezable")
    def test_should_use_cgroup_freeze_any_scope(self, mock_freezable, mock_cgroup):
        """Test that any .scope cgroup uses cgroup freezing (not just tmux/screen)."""
        mock_cgroup.return_value = "/sys/fs/cgroup/user.slice/run-12345.scope"
        mock_freezable.return_value = True
        result = thrash_protect.should_use_cgroup_freeze(12345)
        assert result == "/sys/fs/cgroup/user.slice/run-12345.scope"

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
        thrash_protect.frozen_pids = [(10,), (20, 30)]
        thrash_protect.frozen_cgroups = [("/cgroup/path", (40, 50))]
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
        thrash_protect.frozen_pids = [(10,), (20, 30)]
        thrash_protect.frozen_cgroups = [("/cgroup/path1", (40,)), ("/cgroup/path2", (50, 60))]
        thrash_protect.cleanup()
        # Should unfreeze both cgroups
        assert mock_unfreeze_cgroup.call_count == 2
        # Should SIGCONT regular pids (10, 30, 20 - in reverse order for tuples)
        assert kill.call_count == 3
