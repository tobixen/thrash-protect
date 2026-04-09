#!/usr/bin/env python3
"""Sway window title modifier for thrash-protect.

Monitors the thrash-protect frozen PID file and adds [FROZEN] prefix
to window titles of frozen processes in sway. Restores titles on
unfreeze or exit.

Requires: sway, swaymsg
No external Python dependencies (stdlib only).
"""

import logging
import signal
import subprocess
import sys
import time

FROZEN_PID_FILE = "/tmp/thrash-protect-frozen-pid-list"
POLL_INTERVAL = 1  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# Track PIDs we've marked as frozen so we can restore them
marked_pids: set[int] = set()


def swaymsg(criteria, command):
    """Run a swaymsg command targeting a window by criteria."""
    try:
        subprocess.run(
            ["swaymsg", criteria, command],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        logging.error("swaymsg not found - is sway installed?")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        logging.warning("swaymsg timed out for %s", criteria)


def mark_frozen(pid):
    """Add [FROZEN] prefix to a sway window's title."""
    logging.info("marking pid %d as frozen", pid)
    swaymsg(f"[pid={pid}]", "title_format '[FROZEN] %title'")
    marked_pids.add(pid)


def mark_unfrozen(pid):
    """Restore a sway window's original title."""
    logging.info("restoring pid %d title", pid)
    swaymsg(f"[pid={pid}]", "title_format '%title'")
    marked_pids.discard(pid)


def restore_all():
    """Restore all marked windows to their original titles."""
    for pid in list(marked_pids):
        mark_unfrozen(pid)


def read_frozen_pids():
    """Read the set of frozen PIDs from the state file."""
    try:
        with open(FROZEN_PID_FILE) as f:
            content = f.read().strip()
        if not content:
            return set()
        pids = set()
        for token in content.split():
            try:
                pids.add(int(token))
            except ValueError:
                pass
        return pids
    except FileNotFoundError:
        return set()
    except OSError as e:
        logging.warning("failed to read %s: %s", FROZEN_PID_FILE, e)
        return set()


def main():
    def handle_exit(signum, frame):
        logging.info("received signal %d, restoring titles and exiting", signum)
        restore_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    logging.info("started, monitoring %s", FROZEN_PID_FILE)

    previous_pids: set[int] = set()

    try:
        while True:
            current_pids = read_frozen_pids()

            newly_frozen = current_pids - previous_pids
            newly_unfrozen = previous_pids - current_pids

            for pid in newly_frozen:
                mark_frozen(pid)

            for pid in newly_unfrozen:
                mark_unfrozen(pid)

            previous_pids = current_pids
            time.sleep(POLL_INTERVAL)
    except Exception:
        logging.exception("unexpected error, restoring titles")
        restore_all()
        raise


if __name__ == "__main__":
    main()
