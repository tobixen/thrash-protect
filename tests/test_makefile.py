import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_install_sway_extras_unit_is_not_tied_to_installers_home(tmp_path):
    """The target is run with sudo, where $HOME is /root - a user unit put
    there is invisible to the desktop user's `systemctl --user`."""
    root = tmp_path / "root"
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["make", "-C", str(REPO), "install-sway-extras", f"INSTALL_ROOT={root}"],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    unit = root / "usr/local/lib/systemd/user/thrash-protect-sway-indicator.service"
    assert unit.is_file()
    assert not any(home.rglob("*"))
