"""Windows desktop integration. Imported only by the Windows selector."""
from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

NAME = "windows"
PREFERRED_FONTS = ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
PREFERRED_THEME = "vista"
TK_SCALING = None
CODEX_COMMAND_NAMES = ("codex.exe", "codex.cmd", "codex")
CONFIG_DIR_NAME = "AgentSessionManager"
LEGACY_CONFIG_DIR_NAMES = ("CodexLocalSessionCleaner",)


def settings_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / CONFIG_DIR_NAME / "settings.json"


def legacy_settings_paths() -> tuple[Path, ...]:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return tuple(root / name / "settings.json" for name in LEGACY_CONFIG_DIR_NAMES)


def directory_command(path: Path, *, reveal: bool = False) -> list[str]:
    return ["explorer.exe", f"/select,{path}" if reveal else str(path)]


def _toolhelp_process_names() -> list[str]:
    """Enumerate processes through Toolhelp32 without elevated privileges."""
    max_path = 260
    th32cs_snapprocess = 0x00000002
    invalid_handle = ctypes.c_void_p(-1).value

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * max_path),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(th32cs_snapprocess, 0)
    if snapshot == invalid_handle:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    names: list[str] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            raise OSError(ctypes.get_last_error(), "Process32FirstW failed")
        while True:
            names.append(entry.szExeFile)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)
    return names


def process_names() -> list[str]:
    try:
        return _toolhelp_process_names()
    except OSError:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=5, check=False,
        )
        if result.returncode:
            raise OSError("Windows process enumeration failed")
        return [row[0] for row in csv.reader(io.StringIO(result.stdout or "")) if row]


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return path.is_symlink() or bool(attributes & 0x400)


def prepare_gui_runtime() -> None:
    """Windows uses the Tcl/Tk shipped with its Python runtime."""
    return None
