import os
import shutil
import subprocess
import sys
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


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_install_version_stamp_wins_over_stale_dist_info(tmp_path):
    """A dist-info left behind by a removed package must not make a
    standalone install report that package's version."""
    root = tmp_path / "root"
    (root / "usr/sbin").mkdir(parents=True)
    (root / "usr/lib/systemd/system").mkdir(parents=True)
    result = subprocess.run(
        ["make", "-C", str(REPO), "install", f"DESTDIR={root}", "version=9.9.9"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    site = tmp_path / "site"
    dist_info = site / "thrash_protect-1.1.2.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: thrash-protect\nVersion: 1.1.2\n")
    result = subprocess.run(
        [sys.executable, str(root / "usr/sbin/thrash-protect"), "--version"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(site)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-1] == "9.9.9"
