"""Shared core: find what is using a file, folder or whole drive, and end it.

Blockers are found three ways and merged by process ID:
  1. Windows Restart Manager: the OS's own list of processes and services that hold the files.
  2. One snapshot of every open file and folder handle in the system, plus each process's working
     folder and program file. This catches folder locks Restart Manager misses (a terminal or
     Explorer sitting in the folder) and takes about a second instead of psutil's ~15 s.
  3. File Explorer windows that are showing a folder inside the path.
"""
import ctypes
import logging
import msvcrt
import os
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass

import psutil
import pythoncom
import pywintypes
import win32com.client
import win32con
import win32gui
import win32process
import win32serviceutil

log = logging.getLogger("task_ender")

# Ending these would crash or destabilise Windows, so they are shown but never touched.
PROTECTED_PROCESSES = {
    "system", "idle", "registry", "memory compression", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "msmpeng.exe",
}
PROTECTED_SERVICES = {
    "rpcss", "rpceptmapper", "dcomlaunch", "lsm", "power", "brokerinfrastructure", "samss",
    "eventlog", "plugplay", "profsvc", "winmgmt", "windefend", "mpssvc", "bfe", "cryptsvc",
}
MAX_FILES_TO_CHECK = 5000   # Restart Manager check limit; psutil still checks every process
GRACE_SECONDS = 5           # time an app gets to close itself before it is forced

ACTIONS = {                 # what will be done to each kind, shown in the confirmation dialog
    "App": "Close app",
    "Service": "Stop service",
    "Explorer window": "Close Explorer window",
    "Explorer": "Restart Windows Explorer",
}


@dataclass(frozen=True)
class Blocker:
    kind: str       # "App", "Service", "Explorer window", "Explorer" or "Protected"
    name: str       # program file name, service name, or "File Explorer"
    pid: int
    holds: str      # what it is holding, for the user
    key: str = ""   # service name, or the window handle of an Explorer window

    @property
    def id(self):
        return self.kind, self.pid, self.key


def is_under(path, root):
    """True if `path` is `root` itself or anything inside it (case-insensitive, Windows rules)."""
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    return path == root or path.startswith(root + os.sep)


def find_blockers(target):
    """Return every Blocker holding `target` (a file, a folder, or a drive root like 'E:\\')."""
    target = os.path.abspath(target)
    holders = {}       # pid -> what it holds
    rm_services = {}   # pid -> service names Restart Manager pinpointed

    try:
        for pid, service in _restart_manager(_files_under(target)):
            holders.setdefault(pid, "open file(s)")
            if service:
                rm_services.setdefault(pid, []).append(service)
    except OSError as error:
        log.warning("Restart Manager check failed for %s: %s", target, error)

    holders.update(_process_holders(target))   # psutil's answer is more precise, so it wins

    blockers = [Blocker("Explorer window", "File Explorer", pid, path, str(hwnd))
                for hwnd, pid, path in _explorer_windows(target)]
    services_by_pid = _services_by_pid() if holders else {}
    for pid, holds in holders.items():
        blockers.extend(_classify(pid, holds, rm_services.get(pid) or services_by_pid.get(pid, [])))

    log.info("Found %d blocker(s) for %s: %s", len(blockers), target,
             ", ".join(f"{b.kind} {b.name} ({b.pid})" for b in blockers) or "none")
    return blockers


def end_blockers(blockers, target):
    """End the confirmed blockers, gentlest first. Returns a list of failure messages."""
    failures = []
    windows = [b for b in blockers if b.kind == "Explorer window"]
    if windows:
        failures += _close_explorer_windows({int(b.key) for b in windows})
        time.sleep(1)
        # Closing windows often frees Explorer's own handles, so only end what still blocks.
        still_blocking = {b.id for b in find_blockers(target)}
        blockers = [b for b in blockers if b.id in still_blocking]

    for blocker in blockers:
        try:
            if blocker.kind == "Service":
                _stop_service(blocker.key)
            elif blocker.kind == "App":
                _end_process(blocker.pid, blocker.name)
            elif blocker.kind == "Explorer":
                _restart_explorer(blocker.pid)
            else:
                continue   # protected items and still-open windows are never forced
            log.info("Ended %s %s (PID %d)", blocker.kind, blocker.name, blocker.pid)
        except (psutil.Error, pywintypes.error, OSError) as error:
            log.error("Could not end %s %s (PID %d): %s", blocker.kind, blocker.name, blocker.pid, error)
            failures.append(f"{blocker.name} (PID {blocker.pid}): {error}")
    return failures


# ---------- finding ----------

class _RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", _RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * 256),
                ("strServiceShortName", wintypes.WCHAR * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL)]


_rm = ctypes.WinDLL("rstrtmgr")
_rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
_rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
                                    wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
_rm.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                          ctypes.POINTER(_RM_PROCESS_INFO), ctypes.POINTER(wintypes.DWORD)]
_rm.RmEndSession.argtypes = [wintypes.DWORD]
_RM_SERVICE = 3
_ERROR_MORE_DATA = 234


def _restart_manager(files):
    """Ask Restart Manager who holds any of `files`. Returns [(pid, service_name or '')]."""
    if not files:
        return []
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    error = _rm.RmStartSession(ctypes.byref(session), 0, key)
    if error:
        raise OSError(error, "RmStartSession failed")
    try:
        for start in range(0, len(files), 500):
            chunk = files[start:start + 500]
            error = _rm.RmRegisterResources(session, len(chunk), (wintypes.LPCWSTR * len(chunk))(*chunk),
                                            0, None, 0, None)
            if error:
                raise OSError(error, "RmRegisterResources failed")

        needed, count, reasons, infos = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD(), None
        while True:
            error = _rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if error == _ERROR_MORE_DATA:   # first call, or the list grew: retry with room for all
                infos = (_RM_PROCESS_INFO * needed.value)()
                count.value = needed.value
                continue
            if error:
                raise OSError(error, "RmGetList failed")
            return [(info.Process.dwProcessId,
                     info.strServiceShortName if info.ApplicationType == _RM_SERVICE else "")
                    for info in (infos or [])[:count.value]]
    finally:
        _rm.RmEndSession(session)


def _files_under(target):
    if os.path.isfile(target):
        return [target]
    files = []
    for folder, _, names in os.walk(target):
        files.extend(os.path.join(folder, name) for name in names)
        if len(files) >= MAX_FILES_TO_CHECK:
            log.info("%s has over %d files; Restart Manager checks the first %d",
                     target, MAX_FILES_TO_CHECK, MAX_FILES_TO_CHECK)
            return files[:MAX_FILES_TO_CHECK]
    return files


def _process_holders(target):
    """{pid: what it holds} for processes whose working folder, program or open handles are in target."""
    found = {}
    for pid, path in _open_file_handles():
        if pid not in found and is_under(path, target):
            found[pid] = path
    skip = {0, 4, os.getpid()}
    for process in psutil.process_iter():
        if process.pid in skip or process.pid in found:
            continue
        if is_under(_safe(process.cwd, ""), target):
            found[process.pid] = "working folder is inside"
        elif is_under(_safe(process.exe, ""), target):
            found[process.pid] = "running from here"
    return found


class _HANDLE_ENTRY(ctypes.Structure):   # SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX
    _fields_ = [("Object", ctypes.c_void_p), ("UniqueProcessId", ctypes.c_size_t),
                ("HandleValue", ctypes.c_size_t), ("GrantedAccess", wintypes.ULONG),
                ("CreatorBackTraceIndex", wintypes.USHORT), ("ObjectTypeIndex", wintypes.USHORT),
                ("HandleAttributes", wintypes.ULONG), ("Reserved", wintypes.ULONG)]


_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtQuerySystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                                            ctypes.POINTER(wintypes.ULONG)]
_ntdll.NtQuerySystemInformation.restype = ctypes.c_long
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
                                 wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.GetFileType.argtypes = [wintypes.HANDLE]
_k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_PROCESS_DUP_HANDLE = 0x0040
_DUPLICATE_SAME_ACCESS = 0x2
_FILE_TYPE_DISK = 1


def _open_file_handles():
    """Yield (pid, path) for every file and folder any process has open, from one system snapshot."""
    with open(__file__, "rb") as own_file:   # our own known file handle reveals the "File" type number
        own_handle = msvcrt.get_osfhandle(own_file.fileno())
        size = 0x400000
        while True:
            buffer = ctypes.create_string_buffer(size)
            status = _ntdll.NtQuerySystemInformation(_SYSTEM_EXTENDED_HANDLE_INFORMATION, buffer, size, None)
            if status & 0xFFFFFFFF != _STATUS_INFO_LENGTH_MISMATCH:
                break
            size *= 2
        if status:
            log.warning("Handle snapshot failed (NTSTATUS 0x%08X)", status & 0xFFFFFFFF)
            return
        count = ctypes.c_size_t.from_buffer(buffer).value
        entries = (_HANDLE_ENTRY * count).from_buffer(buffer, 2 * ctypes.sizeof(ctypes.c_size_t))
        me = os.getpid()
        file_type = next(e.ObjectTypeIndex for e in entries
                         if e.UniqueProcessId == me and e.HandleValue == own_handle)

    this_process, processes = _k32.GetCurrentProcess(), {}
    path = ctypes.create_unicode_buffer(32768)
    try:
        for entry in entries:
            pid = entry.UniqueProcessId
            if entry.ObjectTypeIndex != file_type or pid in (0, 4, me):
                continue
            if pid not in processes:
                processes[pid] = _k32.OpenProcess(_PROCESS_DUP_HANDLE, False, pid)   # None if access denied
            if not processes[pid]:
                continue
            copy = wintypes.HANDLE()
            if not _k32.DuplicateHandle(processes[pid], entry.HandleValue, this_process, ctypes.byref(copy),
                                        0, False, _DUPLICATE_SAME_ACCESS):
                continue
            try:
                # Only disk files and folders: asking a pipe or device for its path can hang.
                if _k32.GetFileType(copy) == _FILE_TYPE_DISK and \
                        _k32.GetFinalPathNameByHandleW(copy, path, len(path), 0):
                    yield pid, path.value.removeprefix("\\\\?\\")
            finally:
                _k32.CloseHandle(copy)
    finally:
        for handle in processes.values():
            if handle:
                _k32.CloseHandle(handle)


def _safe(call, default):
    """psutil call that returns `default` when the process has exited or denies access."""
    try:
        return call() or default
    except (psutil.Error, OSError):
        return default


def _explorer_windows(target):
    """[(hwnd, pid, folder)] for File Explorer windows showing a folder inside target."""
    pythoncom.CoInitialize()
    found = []
    for window in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            folder, hwnd = window.Document.Folder.Self.Path, int(window.HWND)
        except (pywintypes.com_error, AttributeError):
            continue   # not a folder window (or it closed meanwhile)
        if is_under(folder, target):
            found.append((hwnd, win32process.GetWindowThreadProcessId(hwnd)[1], folder))
    return found


def _services_by_pid():
    result = {}
    for service in psutil.win_service_iter():
        try:
            pid = service.pid()
            if pid:
                result.setdefault(pid, []).append(service.name())
        except psutil.Error:
            continue
    return result


def _classify(pid, holds, services):
    name = _safe(lambda: psutil.Process(pid).name(), f"PID {pid}")
    if services:
        return [Blocker("Protected" if s.lower() in PROTECTED_SERVICES else "Service", s, pid, holds, s)
                for s in services]
    if name.lower() == "explorer.exe":
        return [Blocker("Explorer", name, pid, holds)]
    if name.lower() in PROTECTED_PROCESSES:
        return [Blocker("Protected", name, pid, holds)]
    return [Blocker("App", name, pid, holds)]


# ---------- ending ----------

def _close_explorer_windows(hwnds):
    pythoncom.CoInitialize()
    for window in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            if int(window.HWND) in hwnds:
                window.Quit()
                hwnds = hwnds - {int(window.HWND)}
        except pywintypes.com_error:
            continue
    log.info("Closed Explorer windows; %d could not be closed", len(hwnds))
    return [f"Explorer window {hwnd} could not be closed" for hwnd in hwnds]


def _stop_service(name):
    try:
        win32serviceutil.StopServiceWithDeps(name, waitSecs=15)
    except pywintypes.error as error:
        if error.winerror != 1062:   # 1062 = the service is not started, which is fine
            raise


def _end_process(pid, expected_name):
    """Ask the app's windows to close; force it only if it is still running after GRACE_SECONDS."""
    try:
        process = psutil.Process(pid)
        if process.name() != expected_name:   # the PID was reused by another program since the scan
            log.warning("PID %d is now %s, not %s; left alone", pid, process.name(), expected_name)
            return
    except psutil.NoSuchProcess:
        return

    windows = []
    win32gui.EnumWindows(lambda hwnd, _: windows.append(hwnd) or True, None)
    asked = False
    for hwnd in windows:
        if win32gui.IsWindowVisible(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            asked = True
    try:
        process.wait(GRACE_SECONDS if asked else 0)
    except psutil.TimeoutExpired:
        if asked:
            log.warning("%s (PID %d) did not close in %ds; forcing", expected_name, pid, GRACE_SECONDS)
        process.kill()
        process.wait(GRACE_SECONDS)


def _restart_explorer(pid):
    psutil.Process(pid).kill()
    time.sleep(2)
    if not any(_safe(p.name, "").lower() == "explorer.exe" for p in psutil.process_iter()):
        subprocess.Popen(["explorer.exe"])
