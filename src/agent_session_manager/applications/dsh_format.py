"""Independent bounded reader for standard Zstd frames and DSH physical JSONL.

No DSH/Node code is executed or imported. Older formats are inspectable, not migrated.
"""
from __future__ import annotations
import ctypes as C
import ctypes.util
import json
import re
from pathlib import Path
from functools import lru_cache

CANONICAL = re.compile(r"session(?:\.v([1-9][0-9]*))?\.jsonl(\.zstd)?$")
MAX_LINE = 16 * 1024 * 1024
CHUNK = 64 * 1024

class Buffer(C.Structure):
    _fields_ = [("data", C.c_void_p), ("size", C.c_size_t), ("pos", C.c_size_t)]

@lru_cache(maxsize=1)
def zstd_library():
    name = ctypes.util.find_library("zstd")
    if not name:
        raise ValueError("Zstandard runtime unavailable; install libzstd in your selected environment.")
    lib = C.CDLL(name)
    lib.ZSTD_createDStream.restype = C.c_void_p
    lib.ZSTD_initDStream.argtypes = [C.c_void_p]
    lib.ZSTD_initDStream.restype = C.c_size_t
    lib.ZSTD_decompressStream.argtypes = [C.c_void_p, C.POINTER(Buffer), C.POINTER(Buffer)]
    lib.ZSTD_decompressStream.restype = C.c_size_t
    lib.ZSTD_freeDStream.argtypes = [C.c_void_p]
    lib.ZSTD_isError.argtypes = [C.c_size_t]
    lib.ZSTD_isError.restype = C.c_uint
    lib.ZSTD_getErrorName.argtypes = [C.c_size_t]
    lib.ZSTD_getErrorName.restype = C.c_char_p
    return lib

def chunks(path: Path):
    if not path.name.endswith(".zstd"):
        with path.open("rb") as source:
            while data := source.read(CHUNK):
                yield data
        return
    lib = zstd_library()
    stream = lib.ZSTD_createDStream()
    if not stream:
        raise MemoryError("Cannot allocate Zstd reader")
    def check(code):
        if lib.ZSTD_isError(code):
            raise ValueError("Corrupt Zstd session: " + lib.ZSTD_getErrorName(code).decode())
    remaining = 1
    try:
        check(lib.ZSTD_initDStream(stream))
        with path.open("rb") as source:
            while data := source.read(CHUNK):
                incoming = C.create_string_buffer(data)
                input_buffer = Buffer(C.cast(incoming, C.c_void_p), len(data), 0)
                while True:
                    outgoing = C.create_string_buffer(CHUNK)
                    output_buffer = Buffer(C.cast(outgoing, C.c_void_p), CHUNK, 0)
                    before = input_buffer.pos
                    remaining = lib.ZSTD_decompressStream(stream, C.byref(output_buffer), C.byref(input_buffer))
                    check(remaining)
                    if output_buffer.pos:
                        yield outgoing.raw[:output_buffer.pos]
                    if input_buffer.pos == input_buffer.size and output_buffer.pos < CHUNK:
                        break
                    if input_buffer.pos == before and not output_buffer.pos:
                        raise ValueError("Zstd decoder made no progress")
        if remaining:
            raise ValueError("Truncated Zstd session; source remains unchanged")
    finally:
        lib.ZSTD_freeDStream(stream)

def rows(path: Path):
    pending = b""
    for chunk in chunks(path):
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if len(line) > MAX_LINE:
                raise ValueError("DSH event exceeds the bounded reader limit")
            if not line.strip():
                continue
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("DSH JSONL row must be an object")
            yield value
        if len(pending) > MAX_LINE:
            raise ValueError("DSH event exceeds the bounded reader limit")
    if pending.strip():
        raise ValueError("Incomplete DSH JSONL tail; source remains unchanged")

def selected_generation(directory: Path):
    found = []
    for path in directory.iterdir():
        match = CANONICAL.fullmatch(path.name)
        if match:
            found.append((int(match[1] or 0), bool(match[2]), path))
    if not found:
        return None
    if len({compressed for _, compressed, _ in found}) != 1:
        raise ValueError("Mixed raw/compressed DSH generations are unsupported")
    return max(found, key=lambda entry: entry[0])

def header(path: Path, version: int):
    iterator = rows(path)
    try:
        value = next(iterator)
    except StopIteration as exc:
        raise ValueError("Empty DSH log") from exc
    finally:
        iterator.close()
    if value.get("type") != "session" or value.get("version") != version:
        raise ValueError("DSH header version does not match filename")
    if not isinstance(value.get("id"), str) or not value["id"]:
        raise ValueError("Missing DSH session identity")
    if not isinstance(value.get("createdAt"), (int, float)):
        raise ValueError("Missing DSH creation time")
    if version == 4:
        allowed = {"type", "version", "id", "createdAt", "cwd", "parentSession", "isSeeded", "origin", "delegationDepth", "agentPreset"}
        required = {"type", "version", "id", "createdAt", "isSeeded"}
        if not required <= value.keys() or value.keys() - allowed:
            raise ValueError("Unsupported DSH v4 header fields")
        depth = value.get("delegationDepth", 0)
        if type(value["isSeeded"]) is not bool or type(depth) is not int or depth < 0:
            raise ValueError("Invalid DSH v4 header values")
        if value.get("origin") not in {None, "subagent"}:
            raise ValueError("Unknown DSH session origin")
    return value

def text_content(value, include_internal=False):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [text_content(part, include_internal) for part in value if isinstance(part, (dict, str))]
        return "\n".join(part for part in parts if part.strip())
    if isinstance(value, dict):
        if value.get("type") not in {None, "text", "input_text", "output_text"} and not include_internal:
            return ""
        if isinstance(value.get("text"), str):
            return value["text"]
        return text_content(value.get("content", ""), include_internal)
    return ""
