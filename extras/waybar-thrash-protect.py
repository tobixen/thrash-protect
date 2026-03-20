#!/usr/bin/env python3
"""Waybar custom module for thrash-protect status.

Runs as a long-lived process, printing one JSON line per second for
waybar's "custom" module.  Uses inotify to watch for the frozen PID
file so that even brief freeze/unfreeze events (sub-second) are detected.

Shows:
  - Nothing when idle (module hidden by waybar when text is empty)
  - Warning icon + count + names when processes are frozen
  - Brief warning after recent activity (last 10 seconds)

No external Python dependencies (stdlib + Linux inotify via ctypes).

Waybar config example:
    "custom/thrash-protect": {
        "exec": "/usr/local/lib/thrash-protect/waybar-thrash-protect.py",
        "return-type": "json"
    }
"""

import ctypes
import ctypes.util
import json
import os
import select
import signal
import struct
import sys
import time

FROZEN_PID_FILE = "/tmp/thrash-protect-frozen-pid-list"
WATCH_DIR = "/tmp"
WATCH_FILENAME = "thrash-protect-frozen-pid-list"
RECENT_ACTIVITY_WINDOW = 10  # seconds
OUTPUT_INTERVAL = 1  # seconds between waybar JSON lines

# inotify event masks
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_MODIFY = 0x00000002
IN_MOVED_TO = 0x00000080
IN_MOVED_FROM = 0x00000040

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _inotify_init():
    fd = _libc.inotify_init()
    if fd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return fd


def _inotify_add_watch(fd, path, mask):
    wd = _libc.inotify_add_watch(fd, path.encode(), ctypes.c_uint32(mask))
    if wd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return wd


def _drain_inotify_events(fd):
    """Read all pending inotify events and return filenames that changed."""
    names = set()
    try:
        buf = os.read(fd, 8192)
    except OSError:
        return names
    offset = 0
    header_size = struct.calcsize("iIII")
    while offset + header_size <= len(buf):
        _, mask, _, name_len = struct.unpack_from("iIII", buf, offset)
        offset += header_size
        name = buf[offset : offset + name_len].rstrip(b"\0").decode(errors="replace")
        offset += name_len
        names.add(name)
    return names


def get_process_name(pid):
    """Read short process name from /proc/<pid>/comm."""
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return None


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
    except (FileNotFoundError, OSError):
        return set()


def output(text="", tooltip="", css_class=""):
    """Print a single JSON line for waybar."""
    obj = {"text": text, "tooltip": tooltip, "class": css_class}
    print(json.dumps(obj), flush=True)


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    inotify_fd = _inotify_init()
    _inotify_add_watch(
        inotify_fd,
        WATCH_DIR,
        IN_CREATE | IN_DELETE | IN_MODIFY | IN_MOVED_TO | IN_MOVED_FROM,
    )

    last_active = 0.0  # timestamp of last time we saw activity
    next_output = 0.0

    while True:
        now = time.time()
        wait = max(0, next_output - now)

        # Wait for inotify events or until next output is due
        readable, _, _ = select.select([inotify_fd], [], [], wait)

        if readable:
            names = _drain_inotify_events(inotify_fd)
            if WATCH_FILENAME in names:
                # File was created, modified, or deleted — thrash-protect was active
                last_active = time.time()

        now = time.time()
        if now < next_output:
            continue

        next_output = now + OUTPUT_INTERVAL

        pids = read_frozen_pids()
        if pids:
            last_active = now
            names_list = []
            for pid in sorted(pids):
                name = get_process_name(pid)
                names_list.append(name if name else str(pid))
            name_str = ",".join(names_list)
            count = len(pids)
            text = f"\u26a0 {count}: {name_str}"
            tooltip = f"thrash-protect: {count} frozen process(es)\n{name_str}"
            output(text=text, tooltip=tooltip, css_class="blinking")
        elif last_active and (now - last_active) < RECENT_ACTIVITY_WINDOW:
            ago = int(now - last_active)
            text = "\u26a0"
            tooltip = f"thrash-protect was active {ago}s ago"
            output(text=text, tooltip=tooltip, css_class="recent")
        else:
            # Nothing to show - waybar hides the module when text is empty
            output()


if __name__ == "__main__":
    main()
