#!/usr/bin/env python3
"""Sway window title modifier for thrash-protect.

Monitors the thrash-protect frozen PID file and adds [FROZEN] prefix
to the sway window titles of frozen processes.  A frozen process seldom
owns a window itself (browser renderers, programs in a terminal), so it
is mapped to the nearest ancestor that does.  Restores titles on
unfreeze or exit.

Requires: sway, swaymsg (tmux is used when present)
No external Python dependencies (stdlib only).
"""

import json
import logging
import signal
import subprocess
import sys
import time

FROZEN_PID_FILE = "/tmp/thrash-protect-frozen-pid-list"
POLL_INTERVAL = 1  # seconds

FROZEN = "[FROZEN]"
# The owner has several windows (a browser, a foot server) and the process
# tree cannot tell which of them the frozen process belongs to
MAYBE_FROZEN = "[FROZEN?]"

# Window-owning PIDs we've marked, and with which label, so we can restore them
marked_pids: dict[int, str] = {}

# Whether sway failed last time, so an outage is logged once, not every poll
_sway_failing = False


def _run_swaymsg(*args):
    try:
        return subprocess.run(
            ["swaymsg", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logging.error("swaymsg not found - is sway installed?")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        return None


def _no_matching_window(result):
    return result.returncode == 2 and "No matching node" in result.stdout


def swaymsg(*args):
    """Run swaymsg; the CompletedProcess, or None on timeout.

    A failure (sway unreachable, typically SWAYSOCK missing from the
    service's environment) is logged once until sway answers again.
    """
    global _sway_failing
    result = _run_swaymsg(*args)
    failed = result is None or (result.returncode != 0 and not _no_matching_window(result))
    if failed and not _sway_failing:
        reason = "timed out" if result is None else f"exited {result.returncode}: {result.stderr.strip()}"
        logging.warning("swaymsg %s %s", " ".join(args), reason)
    _sway_failing = failed
    return result


def _settled(result):
    """Whether a window command took effect, or had no window left to act on."""
    return result is not None and (result.returncode == 0 or _no_matching_window(result))


def window_pids(tree):
    """The PIDs owning a window in a sway get_tree JSON tree, with the
    number of windows each of them owns."""
    pids = {}
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.get("pid"):
            pids[node["pid"]] = pids.get(node["pid"], 0) + 1
        stack.extend(node.get("nodes", []))
        stack.extend(node.get("floating_nodes", []))
    return pids


def ppid_from_stat(stat):
    """The parent PID in a /proc/<pid>/stat line, or None if it is malformed."""
    # The command name may contain spaces and parentheses; ppid follows the last ')'
    try:
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return None


def parent_pid(pid):
    """Parent PID from /proc, or None if the process is gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return ppid_from_stat(f.read())
    except OSError:
        return None


def ancestors(pid, parent=parent_pid):
    """pid followed by its ancestors, up to but excluding init."""
    while pid and pid > 1:
        yield pid
        pid = parent(pid)


def owning_window_pid(pid, wpids, parent=parent_pid):
    """The nearest ancestor of pid (or pid itself) that owns a window."""
    return next((a for a in ancestors(pid, parent) if a in wpids), None)


def windows_for(frozen_pids, wpids, parent=parent_pid, panes=None):
    """The window-owning PIDs affected by a set of frozen PIDs.

    A process in a tmux pane descends from the tmux server rather than
    from the terminal showing it, so it is mapped through panes (pane
    PID -> PIDs of the tmux clients showing it) to those clients' windows.
    """
    panes = panes or {}
    owners = set()
    for pid in frozen_pids:
        owner = owning_window_pid(pid, wpids, parent)
        if owner:
            owners.add(owner)
            continue
        pane = next((a for a in ancestors(pid, parent) if a in panes), None)
        if pane:
            owners.update(owning_window_pid(c, wpids, parent) for c in panes[pane])
    owners.discard(None)
    return owners


_TMUX_FORMATS = {
    "list-panes": ["list-panes", "-a", "-F", "#{pane_pid} #{window_id}"],
    "list-clients": ["list-clients", "-F", "#{client_pid} #{window_id}"],
}


def _tmux(cmd):
    """tmux's output; "" when there is no tmux or no server, None on timeout."""
    try:
        # Short timeout: a tmux server frozen by thrash-protect does not answer
        result = subprocess.run(["tmux", *_TMUX_FORMATS[cmd]], capture_output=True, text=True, timeout=1)
    except OSError:
        return ""
    except subprocess.TimeoutExpired:
        return None
    return result.stdout if result.returncode == 0 else ""


def _pid_window_pairs(output):
    for line in output.splitlines():
        try:
            pid, window = line.split()
            yield int(pid), window
        except ValueError:
            continue


def tmux_pane_clients(run=_tmux):
    """Map each tmux pane PID to the PIDs of the clients showing its window.

    Only the default tmux server is asked; servers on other sockets
    (tmux -L/-S) are not seen.  None if tmux did not answer.
    """
    panes = run("list-panes")
    clients = None if panes is None else run("list-clients")
    if clients is None:
        return None
    shown_by = {}
    for client_pid, window in _pid_window_pairs(clients):
        shown_by.setdefault(window, set()).add(client_pid)
    return {pane_pid: shown_by.get(window, set()) for pane_pid, window in _pid_window_pairs(panes)}


def current_window_pids():
    """Window-owning PIDs with their window counts, or None if sway did not answer."""
    result = swaymsg("-t", "get_tree")
    if result is None or result.returncode != 0:
        return None
    try:
        return window_pids(json.loads(result.stdout))
    except ValueError:
        logging.warning("could not parse sway tree")
        return None


def wanted_marks(frozen_pids, windows, parent=parent_pid, panes=None):
    """The label each affected window owner should carry."""
    return {
        owner: FROZEN if windows[owner] == 1 or owner in frozen_pids else MAYBE_FROZEN
        for owner in windows_for(frozen_pids, windows, parent, panes)
    }


def sync_marks(wanted, marked, mark, unmark):
    """Apply the difference between the wanted and the current marks."""
    for pid, label in wanted.items():
        if marked.get(pid) != label:
            mark(pid, label)
    for pid in set(marked) - set(wanted):
        unmark(pid)


def poll(frozen_pids, read_windows, read_panes, marked, mark, unmark, parent=parent_pid):
    """One iteration: bring the marks in line with the frozen PIDs.

    Recomputed on every poll while anything is frozen, so windows opening
    and tmux clients switching windows are picked up.  If sway or a needed
    tmux cannot be asked, the marks are left as they are until the next
    poll.  tmux is only asked about PIDs without a window-owning ancestor.
    """
    if not frozen_pids:
        sync_marks({}, marked, mark, unmark)
        return
    windows = read_windows()
    if windows is None:
        return
    panes = {}
    if any(owning_window_pid(pid, windows, parent) is None for pid in frozen_pids):
        panes = read_panes()
        if panes is None:
            return
    sync_marks(wanted_marks(frozen_pids, windows, parent, panes), marked, mark, unmark)


def mark_frozen(pid, label=FROZEN):
    """Prefix the titles of a PID's sway windows with label."""
    logging.info("marking pid %d as %s", pid, label)
    previous = marked_pids.get(pid)
    # Recorded first, so a signal arriving mid-command still restores it
    marked_pids[pid] = label
    if not _settled(swaymsg(f"[pid={pid}]", f"title_format '{label} %title'")):
        if previous is None:
            del marked_pids[pid]
        else:
            marked_pids[pid] = previous


def mark_unfrozen(pid):
    """Restore a sway window's original title."""
    logging.info("restoring pid %d title", pid)
    if _settled(swaymsg(f"[pid={pid}]", "title_format '%title'")):
        marked_pids.pop(pid, None)


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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    def handle_exit(signum, frame):
        logging.info("received signal %d, restoring titles and exiting", signum)
        restore_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    logging.info("started, monitoring %s", FROZEN_PID_FILE)

    try:
        while True:
            poll(
                read_frozen_pids(),
                current_window_pids,
                tmux_pane_clients,
                marked_pids,
                mark_frozen,
                mark_unfrozen,
            )
            time.sleep(POLL_INTERVAL)
    except Exception:
        logging.exception("unexpected error, restoring titles")
        restore_all()
        raise


if __name__ == "__main__":
    main()
