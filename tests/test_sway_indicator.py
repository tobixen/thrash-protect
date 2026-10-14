import importlib.util
import logging
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sway_frozen_indicator", REPO / "extras/sway-frozen-indicator.py")
indicator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(indicator)

TREE = {
    "id": 1,
    "nodes": [
        {
            "id": 2,
            "nodes": [
                {"id": 10, "pid": 100, "nodes": []},
                {"id": 11, "pid": 200, "nodes": [], "floating_nodes": []},
            ],
            "floating_nodes": [{"id": 12, "pid": 300, "nodes": []}],
        }
    ],
}

# 100 = foot, 101 = shell in it, 102 = claude in that shell
# 200 = electron main process, 201 = zygote, 202 = renderer
# 300 = foot, 301 = tmux client in it; 400 = tmux server, 401 = pane shell, 402 = job
PARENTS = {100: 1, 101: 100, 102: 101, 200: 1, 201: 200, 202: 201, 500: 1, 300: 1, 301: 300, 400: 1, 401: 400, 402: 401}


def parent(pid):
    return PARENTS.get(pid)


def test_window_pids_found_in_tiled_and_floating_nodes():
    assert indicator.window_pids(TREE) == {100: 1, 200: 1, 300: 1}


def test_window_pids_counts_windows_per_owner():
    tree = {"nodes": [{"pid": 100, "nodes": []}, {"pid": 100, "nodes": []}]}
    assert indicator.window_pids(tree) == {100: 2}


def test_frozen_child_maps_to_its_window_owner():
    """thrash-protect freezes renderers and terminal children, which own
    no window themselves - the [pid=...] criterion matched nothing."""
    assert indicator.owning_window_pid(102, {100, 200}, parent) == 100
    assert indicator.owning_window_pid(202, {100, 200}, parent) == 200


def test_window_owner_maps_to_itself():
    assert indicator.owning_window_pid(100, {100, 200}, parent) == 100


def test_process_without_window_maps_to_none():
    assert indicator.owning_window_pid(500, {100, 200}, parent) is None
    assert indicator.owning_window_pid(999, {100, 200}, parent) is None


def test_windows_for_frozen_pids():
    assert indicator.windows_for({102, 202, 500}, {100, 200, 300}, parent) == {100, 200}


def test_parent_pid_of_own_process():
    import os

    assert indicator.parent_pid(os.getpid()) == os.getppid()
    assert indicator.parent_pid(2**22 + 12345) is None


def test_process_in_tmux_maps_to_the_window_of_the_attached_client():
    """A tmux pane descends from the tmux server, not from the terminal
    showing it; the client process does."""
    panes = {401: {301}}
    assert indicator.windows_for({402}, {100, 300}, parent, panes) == {300}


def test_tmux_pane_without_client_maps_to_nothing():
    assert indicator.windows_for({402}, {100, 300}, parent, {401: set()}) == set()


def test_tmux_pane_clients_joins_panes_and_clients_on_window():
    outputs = {
        "list-panes": "401 @1\n405 @2\n",
        "list-clients": "301 @1\n302 @1\n303 @3\n",
    }
    assert indicator.tmux_pane_clients(lambda cmd: outputs[cmd]) == {401: {301, 302}, 405: set()}


def test_tmux_pane_clients_without_tmux_server():
    assert indicator.tmux_pane_clients(lambda cmd: "") == {}


def test_tmux_pane_clients_is_none_when_tmux_does_not_answer():
    asked = []

    def run(cmd):
        asked.append(cmd)

    assert indicator.tmux_pane_clients(run) is None
    assert asked == ["list-panes"]


def test_tmux_pane_clients_skips_unparsable_lines():
    outputs = {"list-panes": "401 @1\ngarbage\n", "list-clients": "301 @1\n\nx y z\n"}
    assert indicator.tmux_pane_clients(lambda cmd: outputs[cmd]) == {401: {301}}


def test_wanted_marks_flags_owners_of_several_windows_as_uncertain():
    """A browser or a foot server owns many windows; the process tree cannot
    tell which of them the frozen process belongs to."""
    windows = {100: 1, 200: 3}
    assert indicator.wanted_marks({102, 202}, windows, parent) == {
        100: indicator.FROZEN,
        200: indicator.MAYBE_FROZEN,
    }


class FakeSway:
    def __init__(self):
        self.marked = {}
        self.calls = []

    def mark(self, pid, label):
        self.calls.append(("mark", pid, label))
        self.marked[pid] = label

    def unmark(self, pid):
        self.calls.append(("unmark", pid))
        del self.marked[pid]


def test_sync_marks_applies_only_the_difference():
    sway = FakeSway()
    sway.marked = {100: indicator.FROZEN, 300: indicator.FROZEN}
    indicator.sync_marks({100: indicator.FROZEN, 200: indicator.MAYBE_FROZEN}, sway.marked, sway.mark, sway.unmark)
    assert sorted(sway.calls) == [("mark", 200, indicator.MAYBE_FROZEN), ("unmark", 300)]


def test_sync_marks_relabels_a_window_whose_certainty_changed():
    sway = FakeSway()
    sway.marked = {100: indicator.FROZEN}
    indicator.sync_marks({100: indicator.MAYBE_FROZEN}, sway.marked, sway.mark, sway.unmark)
    assert sway.calls == [("mark", 100, indicator.MAYBE_FROZEN)]


def test_poll_keeps_marks_when_the_sway_tree_cannot_be_read():
    sway = FakeSway()
    sway.marked = {100: indicator.FROZEN}
    indicator.poll({102}, lambda: None, dict, sway.marked, sway.mark, sway.unmark, parent)
    assert sway.calls == []


def test_poll_recomputes_while_the_frozen_set_is_unchanged():
    """A window opening or a tmux client switching window must show up
    without waiting for the frozen set to change."""
    sway = FakeSway()
    windows = {}
    for _ in range(2):
        indicator.poll({102}, lambda: dict(windows), dict, sway.marked, sway.mark, sway.unmark, parent)
        windows[100] = 1
    assert sway.marked == {100: indicator.FROZEN}


def test_poll_clears_marks_without_asking_sway_when_nothing_is_frozen():
    sway = FakeSway()
    sway.marked = {100: indicator.FROZEN}

    def tree_must_not_be_read():
        raise AssertionError("get_tree called")

    indicator.poll(set(), tree_must_not_be_read, dict, sway.marked, sway.mark, sway.unmark, parent)
    assert sway.marked == {}


def test_frozen_owner_of_several_windows_is_certainly_frozen():
    assert indicator.wanted_marks({200}, {200: 3}, parent) == {200: indicator.FROZEN}


def test_poll_keeps_marks_when_tmux_does_not_answer():
    """A swapped-out tmux server is likely while thrashing; dropping the
    marks it cannot confirm would make the titles flicker."""
    sway = FakeSway()
    sway.marked = {300: indicator.FROZEN}
    indicator.poll({402}, lambda: {300: 1}, lambda: None, sway.marked, sway.mark, sway.unmark, parent)
    assert sway.calls == []


def test_poll_asks_tmux_only_for_pids_without_a_window_owner():
    sway = FakeSway()

    def panes_must_not_be_read():
        raise AssertionError("tmux asked")

    indicator.poll({102}, lambda: {100: 1}, panes_must_not_be_read, sway.marked, sway.mark, sway.unmark, parent)
    assert sway.marked == {100: indicator.FROZEN}


class FakeSwaymsg:
    def __init__(self, returncode, stdout=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, "error text")

    def __call__(self, *args):
        return self.result


def test_failed_mark_is_not_recorded(monkeypatch):
    monkeypatch.setattr(indicator, "marked_pids", {})
    monkeypatch.setattr(indicator, "swaymsg", FakeSwaymsg(1))
    indicator.mark_frozen(100)
    assert indicator.marked_pids == {}


def test_failed_restore_is_kept_for_a_retry(monkeypatch):
    monkeypatch.setattr(indicator, "marked_pids", {100: indicator.FROZEN})
    monkeypatch.setattr(indicator, "swaymsg", FakeSwaymsg(1))
    indicator.mark_unfrozen(100)
    assert indicator.marked_pids == {100: indicator.FROZEN}


def test_restore_of_a_closed_window_is_done(monkeypatch):
    monkeypatch.setattr(indicator, "marked_pids", {100: indicator.FROZEN})
    monkeypatch.setattr(indicator, "swaymsg", FakeSwaymsg(2, '[{"success": false, "error": "No matching node."}]'))
    indicator.mark_unfrozen(100)
    assert indicator.marked_pids == {}


def test_unreachable_sway_is_logged_once_per_failure_streak(monkeypatch, caplog):
    monkeypatch.setattr(indicator, "_sway_failing", False)
    monkeypatch.setattr(indicator, "_run_swaymsg", FakeSwaymsg(1))
    with caplog.at_level(logging.WARNING):
        assert indicator.current_window_pids() is None
        assert indicator.current_window_pids() is None
    assert len([r for r in caplog.records if "error text" in r.getMessage()]) == 1


def test_ppid_from_truncated_stat_is_none():
    assert indicator.ppid_from_stat("1234 (foo) S 1 1234") == 1
    assert indicator.ppid_from_stat("1234 (foo") is None
    assert indicator.ppid_from_stat("1234 (a) b) S x") is None
