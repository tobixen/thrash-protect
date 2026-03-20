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
- Frozen windows get a `[FROZEN]` prefix
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
systemd user unit to `~/.config/systemd/user/`.

## Setup

### Sway window title indicator

Enable and start the systemd user service:

```sh
systemctl --user enable --now thrash-protect-sway-indicator.service
```

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
