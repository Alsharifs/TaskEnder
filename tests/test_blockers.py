import subprocess
import time

import psutil
import pytest

from blockers import (Blocker, _classify, _explorer_windows, _services_by_pid, end_blockers, find_blockers,
                      is_under)
from conftest import HOLD_FILE_OPEN, IS_ADMIN, SIT_IN_FOLDER


def test_is_under():
    assert is_under(r"C:\Data\a.txt", r"C:\data")
    assert is_under(r"C:\Data", "c:\\data\\")
    assert is_under(r"E:\x\y", "E:\\")
    assert not is_under(r"C:\Database", r"C:\Data")
    assert not is_under(r"D:\Data", r"C:\Data")


def test_finds_app_holding_a_file_open(tmp_path, spawn):
    locked = tmp_path / "locked.txt"
    child = spawn(HOLD_FILE_OPEN, locked, ready_file=locked)
    found = {b.pid: b for b in find_blockers(str(locked))}
    assert child.pid in found
    assert found[child.pid].kind == "App"


def test_finds_app_whose_working_folder_is_inside(tmp_path, spawn):
    folder = tmp_path / "masterkey-npm"
    folder.mkdir()
    child = spawn(SIT_IN_FOLDER, cwd=folder, ready_file=folder / "ready.txt")
    found = {b.pid: b for b in find_blockers(str(folder))}
    assert found[child.pid].kind == "App"   # seen via its folder handle or its working folder


def test_nothing_found_for_a_free_folder(tmp_path):
    (tmp_path / "free.txt").write_text("x")
    assert find_blockers(str(tmp_path)) == []


def test_end_blockers_closes_the_app(tmp_path, spawn):
    locked = tmp_path / "locked.txt"
    child = spawn(HOLD_FILE_OPEN, locked, ready_file=locked)
    blockers = find_blockers(str(tmp_path))
    assert end_blockers(blockers, str(tmp_path)) == []
    assert child.poll() is not None
    assert find_blockers(str(tmp_path)) == []


def test_protected_items_are_never_ended(tmp_path, spawn):
    child = spawn("import time; time.sleep(120)")
    end_blockers([Blocker("Protected", "python.exe", child.pid, "test")], str(tmp_path))
    assert child.poll() is None


def test_reused_pid_is_left_alone(tmp_path, spawn):
    child = spawn("import time; time.sleep(120)")
    end_blockers([Blocker("App", "some-other-program.exe", child.pid, "test")], str(tmp_path))
    assert child.poll() is None


def test_service_process_is_classified_as_service():
    pid, names = next(iter(_services_by_pid().items()))
    kinds = {b.kind for b in _classify(pid, "test", names)}
    assert kinds and kinds <= {"Service", "Protected"}


def test_critical_process_is_protected():
    lsass = next(p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "lsass.exe")
    assert {b.kind for b in _classify(lsass.pid, "test", [])} == {"Protected"}


@pytest.mark.skipif(not IS_ADMIN, reason="stopping a service needs administrator rights")
def test_stopping_a_stopped_service_is_not_an_error():
    from blockers import _stop_service
    stopped = next(s.name() for s in psutil.win_service_iter() if s.status() == "stopped")
    _stop_service(stopped)


def test_explorer_window_on_the_folder_is_found_and_closed(tmp_path):
    folder = tmp_path / "shown-in-explorer"
    folder.mkdir()
    subprocess.Popen(["explorer.exe", str(folder)])
    deadline = time.time() + 15
    while time.time() < deadline and not _explorer_windows(str(folder)):
        time.sleep(0.5)
    windows = [b for b in find_blockers(str(folder)) if b.kind == "Explorer window"]
    assert windows
    end_blockers(windows, str(folder))
    assert _explorer_windows(str(folder)) == []
