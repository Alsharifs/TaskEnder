import os

import pytest

from blockers import end_blockers, find_blockers
from conftest import HOLD_FILE_OPEN
from usb import EjectError, UsbDrive, _removable_device, eject, list_usb_drives

# Ejecting needs a real USB stick, so this test runs only when you name one:  set TASK_ENDER_TEST_USB=E:
TEST_DRIVE = os.environ.get("TASK_ENDER_TEST_USB", "").upper()


def test_lists_drives_with_letters():
    for drive in list_usb_drives():
        assert isinstance(drive, UsbDrive)
        assert len(drive.letter) == 2 and drive.letter.endswith(":")
        assert drive.pnp_id


def test_unknown_device_gives_clear_error():
    with pytest.raises(EjectError, match="not found"):
        _removable_device(r"USBSTOR\DISK&VEN_NOPE&PROD_NOPE\0")


@pytest.mark.skipif(not TEST_DRIVE, reason="set TASK_ENDER_TEST_USB=E: to run the real eject test")
def test_free_and_eject_real_drive(spawn):
    """M5: a file on the stick is held open, eject is refused, Task Ender frees it, eject succeeds."""
    drive = next(d for d in list_usb_drives() if d.letter == TEST_DRIVE)
    held = os.path.join(drive.root, "task_ender_test.txt")
    spawn(HOLD_FILE_OPEN, held, ready_file=held)

    with pytest.raises(EjectError):
        eject(drive)
    assert end_blockers(find_blockers(drive.root), drive.root) == []
    os.remove(held)
    eject(drive)
    assert TEST_DRIVE not in {d.letter for d in list_usb_drives()}
