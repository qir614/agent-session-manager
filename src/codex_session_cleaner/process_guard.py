from __future__ import annotations

import csv
import ctypes
import io
import os
import subprocess
from ctypes import wintypes


CODEX_PROCESS_NAMES = {
    "chatgpt.exe",
    "codex.exe",
    "codex-code-mode-host.exe",
    "chatgpt",
    "codex",
    "codex-code-mode-host",
}


def _windows_process_names() -> list[str]:
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


def running_codex_processes() -> list[str]:
    """Return relevant process names without requiring third-party packages."""
    if os.name == "nt":
        try:
            return sorted({
                name for name in _windows_process_names()
                if name.casefold() in CODEX_PROCESS_NAMES
            }, key=str.casefold)
        except OSError:
            pass
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ["process-check-unavailable"]
        if result.returncode != 0:
            return ["process-check-unavailable"]
        names = []
        for row in csv.reader(io.StringIO(result.stdout or "")):
            if row and row[0].casefold() in CODEX_PROCESS_NAMES:
                names.append(row[0])
        return sorted(set(names), key=str.casefold)
    try:
        result = subprocess.run(
            ["ps", "-A", "-o", "comm="], capture_output=True, text=True,
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["process-check-unavailable"]
    return sorted({
        line.strip() for line in result.stdout.splitlines()
        if line.strip().casefold() in CODEX_PROCESS_NAMES
    })


def require_codex_stopped() -> None:
    running = running_codex_processes()
    if running:
        names = ", ".join(running)
        raise RuntimeError(
            "Codex must be completely closed before cleanup or restore. "
            f"Detected: {names}"
        )
