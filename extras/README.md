# Sway/Waybar extras for thrash-protect

Optional companion scripts that provide visual feedback in
[sway](https://swaywm.org/) and [waybar](https://github.com/Alexays/Waybar)
when thrash-protect is throttling processes.

These scripts are **independent of thrash-protect itself** — they simply
monitor the state file (`/tmp/thrash-protect-frozen-pid-list`) that
thrash-protect already writes.  No changes to thrash-protect are required.

Both scripts use only the Python standard library (no external dependencies).

## Components

### sway-frozen-indicator.py

Polls the frozen PID file and modifies sway window titles:
- The window of a frozen process gets a `[FROZEN]` prefix.  A process that
  owns no window (a browser renderer, a program in a terminal) marks the
  window of its nearest ancestor that does; a process in a tmux pane marks
  the terminal of each tmux client showing that pane (default tmux socket
  only)
- When that ancestor owns several windows (a browser, `foot --server`,
  single-instance kitty), all of them get `[FROZEN?]`, since the process tree
  cannot tell which one is affected
- Freezes shorter than the one-second poll interval may go unnoticed
- Titles are restored when the process is unfrozen
- All titles are restored on exit (SIGTERM/SIGINT)

### waybar-thrash-protect.py

Outputs JSON for waybar's `custom` module:
- **Processes frozen**: shows warning icon + count + process names (CSS class: `frozen`)
- **Recent activity** (within 10s): shows warning icon (CSS class: `recent`)
- **Idle**: empty output (waybar hides the module)

### waybar-thrash-protect.css

Example CSS rules for waybar styling — blinking red when actively frozen,
yellow for recent activity.

## Installation

```sh
sudo make install-sway-extras
```

This installs the scripts to `/usr/local/lib/thrash-protect/` and the
systemd user unit to `/usr/local/lib/systemd/user/`, where every user's
`systemctl --user` finds it.

## Setup

### Sway window title indicator

Enable and start the systemd user service:

```sh
systemctl --user enable --now thrash-protect-sway-indicator.service
```

The unit is tied to `graphical-session.target`, which session managers such
as uwsm or sway-systemd activate.  Plain sway never does, so the service will
not come back at the next login; start it from the sway config instead,
after the environment it needs has been handed to systemd:

```
exec "systemctl --user import-environment SWAYSOCK WAYLAND_DISPLAY; systemctl --user start thrash-protect-sway-indicator.service"
```

If an earlier version of the unit was enabled, run `systemctl --user disable
thrash-protect-sway-indicator.service` before enabling it again, to drop the
dangling `sway-session.target.wants` link, and remove any copy of the unit in
`~/.config/systemd/user/`, which would override the installed one.

### Waybar module

Add to your waybar config (`~/.config/waybar/config`):

```json
"custom/thrash-protect": {
    "exec": "/usr/local/lib/thrash-protect/waybar-thrash-protect.py",
    "return-type": "json"
}
```

Then add `"custom/thrash-protect"` to one of your bar's module lists
(e.g. `"modules-right"`).

Add the CSS rules from `waybar-thrash-protect.css` to your waybar stylesheet
(`~/.config/waybar/style.css`), or import it:

```css
@import "/usr/local/lib/thrash-protect/waybar-thrash-protect.css";
```

Restart waybar to apply changes.

## Testing

The waybar module runs continuously (Ctrl-C to stop).  In one terminal:

```sh
python extras/waybar-thrash-protect.py
# Outputs one JSON line per second: {"text": "", "tooltip": "", "class": ""}
```

In another terminal, simulate frozen processes:

```sh
echo "1234 5678" > /tmp/thrash-protect-frozen-pid-list
# Waybar module should now show JSON with class "frozen"
rm /tmp/thrash-protect-frozen-pid-list
# Module should show class "recent" for ~10 seconds, then go quiet
```
