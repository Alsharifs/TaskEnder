"""Feature B: list USB drives and safely eject one (the same request as "Safely Remove Hardware")."""
import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass

import pythoncom
import win32com.client

log = logging.getLogger("task_ender")

_cfg = ctypes.WinDLL("cfgmgr32")
_cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG]
_cfg.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
_cfg.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
                                       wintypes.DWORD, wintypes.ULONG]
_cfg.CM_Request_Device_EjectW.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_int), wintypes.LPWSTR,
                                          wintypes.ULONG, wintypes.ULONG]
_DN_REMOVABLE = 0x4000
_VETO_REASONS = {
    1: "a legacy device", 2: "a pending close", 3: "an application is using it",
    4: "a Windows service is using it", 5: "a file or folder on it is open", 6: "another device",
    7: "its driver", 8: "the request is not allowed", 9: "insufficient power",
    10: "the device cannot be disabled", 11: "a legacy driver", 12: "insufficient rights",
    13: "it was already removed",
}
EJECT_ATTEMPTS = 3


class EjectError(Exception):
    pass


@dataclass(frozen=True)
class UsbDrive:
    letter: str     # "E:"
    label: str
    size_gb: float
    model: str
    pnp_id: str     # Windows device ID of the disk, used to find the USB device to eject

    @property
    def root(self):
        return self.letter + "\\"

    def __str__(self):
        return f"{self.letter}  {self.label or 'USB Drive'}  ({self.size_gb:.1f} GB)  {self.model}"


def list_usb_drives():
    """USB and other removable disks that have a drive letter, sorted by letter."""
    pythoncom.CoInitialize()
    wmi = win32com.client.GetObject("winmgmts:")
    drives = []
    disks = wmi.ExecQuery("SELECT DeviceID, Model, PNPDeviceID FROM Win32_DiskDrive WHERE InterfaceType='USB'"
                          " OR MediaType LIKE 'Removable%' OR MediaType LIKE 'External%'")
    for disk in disks:
        disk_id = disk.DeviceID.replace("\\", "\\\\")
        for part in wmi.ExecQuery(f"ASSOCIATORS OF {{Win32_DiskDrive.DeviceID='{disk_id}'}} "
                                  "WHERE AssocClass=Win32_DiskDriveToDiskPartition"):
            for volume in wmi.ExecQuery(f"ASSOCIATORS OF {{Win32_DiskPartition.DeviceID='{part.DeviceID}'}} "
                                        "WHERE AssocClass=Win32_LogicalDiskToPartition"):
                drives.append(UsbDrive(volume.DeviceID, volume.VolumeName or "",
                                       int(volume.Size or 0) / 1e9, disk.Model, disk.PNPDeviceID))
    log.info("USB drives: %s", ", ".join(d.letter for d in drives) or "none")
    return sorted(drives, key=lambda d: d.letter)


def eject(drive):
    """Safely eject the USB device holding `drive`. Raises EjectError saying why Windows refused."""
    node = _removable_device(drive.pnp_id)
    veto_type, veto_name = ctypes.c_int(), ctypes.create_unicode_buffer(260)
    for attempt in range(1, EJECT_ATTEMPTS + 1):
        result = _cfg.CM_Request_Device_EjectW(node, ctypes.byref(veto_type), veto_name, 260, 0)
        if result == 0 and veto_type.value == 0:
            log.info("Ejected %s (%s)", drive.letter, drive.model)
            return
        reason = _VETO_REASONS.get(veto_type.value, f"error code {result}")
        if veto_name.value:
            reason += f" ({veto_name.value})"
        log.warning("Eject attempt %d of %s refused: %s", attempt, drive.letter, reason)
        time.sleep(1)
    raise EjectError(f"Windows would not eject {drive.letter} because {reason}.")


def _removable_device(pnp_id):
    """Walk up from the disk to the first device Windows marks as removable (the USB device itself)."""
    node = wintypes.DWORD()
    result = _cfg.CM_Locate_DevNodeW(ctypes.byref(node), pnp_id, 0)
    if result:
        raise EjectError(f"The device {pnp_id} was not found (it may already be removed).")
    for _ in range(8):
        status, problem = wintypes.ULONG(), wintypes.ULONG()
        if _cfg.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem), node, 0) == 0 \
                and status.value & _DN_REMOVABLE:
            return node.value
        parent = wintypes.DWORD()
        if _cfg.CM_Get_Parent(ctypes.byref(parent), node, 0):
            break
        node = parent
    raise EjectError(f"No removable device was found above {pnp_id}.")
