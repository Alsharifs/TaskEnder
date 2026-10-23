import os
import stat

import pytest

from blockers import end_blockers, find_blockers
from conftest import HOLD_FILE_OPEN, SIT_IN_FOLDER
from delete import delete_path


def _make_tree(root):
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a")
    (root / "sub" / "b.txt").write_text("b")
    for item in (root / "a.txt", root / "sub" / "b.txt"):
        os.chmod(item, stat.S_IREAD)   # read-only, like many files copied from CDs or git packs
    return root


def test_deletes_read_only_folder_tree(tmp_path):
    folder = _make_tree(tmp_path / "tree")
    delete_path(str(folder))
    assert not folder.exists()


def test_deletes_read_only_file(tmp_path):
    item = tmp_path / "ro.txt"
    item.write_text("x")
    os.chmod(item, stat.S_IREAD)
    delete_path(str(item))
    assert not item.exists()


def test_missing_path_is_not_an_error(tmp_path):
    delete_path(str(tmp_path / "never-existed"))


def test_locked_file_raises_with_reason(tmp_path, spawn):
    locked = tmp_path / "locked.txt"
    spawn(HOLD_FILE_OPEN, locked, ready_file=locked)
    with pytest.raises(OSError):
        delete_path(str(locked))
    assert locked.exists()


def test_unlock_and_delete_end_to_end(tmp_path, spawn):
    """M4: the 'Folder In Use' case. One app holds a file open, another sits inside the folder."""
    folder = _make_tree(tmp_path / "masterkey-npm")
    spawn(HOLD_FILE_OPEN, folder / "sub" / "held.log", ready_file=folder / "sub" / "held.log")
    spawn(SIT_IN_FOLDER, cwd=folder, ready_file=folder / "ready.txt")

    blockers = find_blockers(str(folder))
    assert len(blockers) >= 2
    assert end_blockers(blockers, str(folder)) == []
    delete_path(str(folder))
    assert not folder.exists()
