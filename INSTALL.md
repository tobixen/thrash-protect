Installation and usage
======================

Requirements
------------

This will only work on linux, it depends on reading stats from the
/proc directory, it depends on python 3.9 or higher.

No required dependencies beyond the Python standard library.

### Optional Dependencies

For additional config file format support:

* `PyYAML` - For YAML config files (`.yaml`, `.yml`)
* `tomli` - For TOML config files on Python < 3.11 (Python 3.11+ has built-in TOML support)

Install with:
```
pip install thrash-protect[yaml]          # YAML support
pip install thrash-protect[toml]          # TOML support (Python < 3.11)
pip install thrash-protect[all-formats]   # All config formats
```

INI (`.conf`) and JSON (`.json`) config formats work without any extra dependencies.

The box or VM running thrash-protect needs to be set up with swap, or
trash-protect won't do anything useful (even if thrash-like situations
can happen without swap installed).  A reasonably large swap partition
is recommended, possibly twice as much swap as physical memory, though
YMMV, and even a very small swap partition is enough for
thrash-protect to do useful work.

My original idea was to make a rapid prototype in python, and then
port it over to C for a smaller memory- and CPU footprint; while
thrash-protect has successfully been running on single-CPU instances
with 512M RAM, it's probably best suited on systems with at least 1GB
RAM and multiple CPUs (or CPU cores) due to the overhead costs.

Compile and Install
-------------------

As it's in python, no compilation is needed.

"make install" will hopefully do the right thing and install the
script as a service.

Archlinux users may also install through AUR.  rpm and deb packages
will be made available on request.  There are some logic in the Makefile for creating such packages, but it's poorly tested.


Usage
-----

The service will need to be started and/or set up to start at boot.

If everything else fails, just run the script as root and do whatever
is necessary to ensure it will be started again after next reboot.

While it should be possible to adjust configuration through
environment variables, best practice is probably to run it without any
configuration.

The System V init file is so far quite redhat-specific and may need
tuning for usage with other distributions.

Configuration
-------------

thrash-protect can be configured through multiple methods. Configuration is
loaded in this priority order (highest to lowest):

1. **Command-line arguments** (`--interval=1.0`)
2. **Environment variables** (`THRASH_PROTECT_*`)
3. **Config file** (auto-detected or specified with `--config`)
4. **Built-in defaults** (including dynamic values from `/etc/shells`)

### Command-Line Options

Run `thrash-protect --help` for all available options:

```
thrash-protect --interval=1.0 --debug
thrash-protect --config=/etc/thrash-protect.yaml
thrash-protect --cmd-whitelist sshd bash tmux
```

Key options:
* `--config`, `-c PATH` - Configuration file path (format auto-detected by extension)
* `--debug`, `--debug-logging` - Enable debug logging to stderr
* `--interval SECONDS` - Sleep interval between checks (default: 0.5)
* `--swap-page-threshold N` - Number of swap pages to trigger action (default: 4)
* `--cmd-whitelist CMD [CMD ...]` - Processes to protect from suspension
* `--cmd-blacklist CMD [CMD ...]` - Processes to prioritize for suspension
* `--cmd-jobctrllist CMD [CMD ...]` - Processes with job control (suspend parent too)

### Config File

Config files are searched in this order (first found is used):
1. Path specified with `--config`
2. `/etc/thrash-protect.yaml`
3. `/etc/thrash-protect.yml`
4. `/etc/thrash-protect.toml`
5. `/etc/thrash-protect.json`
6. `/etc/thrash-protect.conf`

Supported formats:
* **INI** (`.conf`, `.ini`) - Standard library, no dependencies
* **JSON** (`.json`) - Standard library, no dependencies
* **YAML** (`.yaml`, `.yml`) - Requires PyYAML
* **TOML** (`.toml`) - Python 3.11+ built-in, or requires tomli

Example config files are included: `thrash-protect.conf.example` and `thrash-protect.yaml.example`.

### Environment Variables

All configuration options can also be set via environment variables. These are
still supported for backward compatibility:

* `THRASH_PROTECT_INTERVAL` - Sleep interval in seconds (default: 0.5)
* `THRASH_PROTECT_SWAP_PAGE_THRESHOLD` - Pages to trigger action (default: 4)
* `THRASH_PROTECT_CMD_WHITELIST` - Space-separated list of protected processes
* `THRASH_PROTECT_CMD_BLACKLIST` - Space-separated list of processes to prioritize for suspension
* `THRASH_PROTECT_CMD_JOBCTRLLIST` - Space-separated list of job control processes
* `THRASH_PROTECT_BLACKLIST_SCORE_MULTIPLIER` - Score multiplier for blacklisted processes (default: 16)
* `THRASH_PROTECT_WHITELIST_SCORE_MULTIPLIER` - Score divider for whitelisted processes (default: 64)
* `THRASH_PROTECT_UNFREEZE_POP_RATIO` - Ratio of stack vs queue pops when unfreezing (default: 5)
* `THRASH_PROTECT_LOG_USER_DATA_ON_FREEZE` - Log detailed info when freezing (default: false)
* `THRASH_PROTECT_LOG_USER_DATA_ON_UNFREEZE` - Log detailed info when unfreezing (default: true)
* `THRASH_PROTECT_DEBUG_LOGGING` - Enable debug logging (default: false)
* `THRASH_PROTECT_DEBUG_CHECKSTATE` - Log process state warnings (default: false)
* `THRASH_PROTECT_DATE_HUMAN_READABLE` - Use human-readable dates in logs (default: true)
* `THRASH_PROTECT_PGMAJFAULT_SCAN_THRESHOLD` - Page faults before process scan (default: swap_page_threshold * 4)
* `THRASH_PROTECT_TEST_MODE` - Pretend thrashing every 2^N iterations (default: 0 = disabled)

### Dynamic Defaults

The whitelist and jobctrllist now have dynamic defaults:

* **cmd_whitelist**: Includes all shells from `/etc/shells` plus a static list of common
  terminals, window managers, desktop environments, and system processes (sshd, tmux,
  sway, gnome-shell, etc.)

* **cmd_jobctrllist**: Includes all shells from `/etc/shells` plus `sudo`

If `/etc/shells` is not readable, falls back to `bash`, `sh`, `zsh`, `fish`.

### Configuration Options Reference

| Option | Default | Description |
|--------|---------|-------------|
| `interval` | 0.5 | Sleep interval between checks (seconds) |
| `swap_page_threshold` | 4 | Swap pages to trigger action |
| `pgmajfault_scan_threshold` | 16 | Page faults before process scan |
| `cmd_whitelist` | (dynamic) | Processes to protect |
| `cmd_blacklist` | (empty) | Processes to prioritize for suspension |
| `cmd_jobctrllist` | (dynamic) | Job control processes |
| `blacklist_score_multiplier` | 16 | Score multiplier for blacklisted |
| `whitelist_score_divider` | 64 | Score divider for whitelisted |
| `unfreeze_pop_ratio` | 5 | Stack/queue ratio when unfreezing |
| `debug_logging` | false | Enable debug logging |
| `debug_checkstate` | false | Log process state warnings |
| `log_user_data_on_freeze` | false | Log details when freezing |
| `log_user_data_on_unfreeze` | true | Log details when unfreezing |
| `date_human_readable` | true | Human-readable log timestamps |
| `test_mode` | 0 | Test mode (0 = disabled) |

Monitoring
----------

thrash-protect may relatively safely live it's own life, users will
only notice some delays and slowness, and bad situations will
autorecover (i.e. the resource-consuming process will stop by itself,
or the kernel will finally run out of swap and the OOM-killer will
kill the rogue process).

For production servers, thrash-protect should ideally only be latent,
only occationally stop something very briefly, if it becomes active a
system administrator should manually inspect the box and deal with the
situation, and eventually order more memory.

There are three useful ways to monitor:

* Monitoring the number of suspended processes.  This will possibly
  catch situations where thrash-protect itself has gone haywire,
  suspending processes but unable to reanimate them.  Unfortunately it
  may also cause false alarms on systems where processes are being
  suspended legitimately outside thrash-protect (i.e. due to some
  sysadmin pressing ^Z).

* Monitoring the /tmp/thrash-protect-frozen-pid-list file.  It should
  only exist briefly.

* Age of the /tmp/thrash-protect-frozen-pid-list file; if it exists
  and is old, most likely thrash-protect is not running anymore.

nrpe-scripts and icinga-configuration may be done available on request.

Subdirectories
--------------

The subdirectories contains various logic for deploying the script:

* archlinux - contains logic for submitting to AUR for Arch Linux
* systemv - contains a traditional init-script, though it may be rather RedHat-specific as for now
* systemd - contains a service config file for running the script under systemd
* upstart - contains the config file for starting up the script under the (Ubuntu) upstart system
* debian - contains files necessary for "debianization" and creating .deb-packages for ubuntu and debian
