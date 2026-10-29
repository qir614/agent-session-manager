"""Prepare a project-local Xft Tk library; never install into conda or /tmp."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import struct
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PACKAGE = "tk-8.6.13-xft_h2e05e87_1.conda"
URL = "https://conda.anaconda.org/conda-forge/linux-64/" + PACKAGE
SHA256 = "1b8f9db863562aaf342a874a61e346a3b88d672b621df3df7b5aa40ddfeca6e1"



def set_library_identity(library: Path) -> None:
    """Replace the package's stale build RPATH with its missing ELF SONAME.

    This lets the loader reuse the preloaded Tk for _tkinter, avoiding a second
    incompatible Tk from conda. Only the project-local, verified copy is edited.
    """
    data = bytearray(library.read_bytes())
    if data[:6] != b"\x7fELF\x02\x01":
        raise RuntimeError("Expected a 64-bit little-endian Linux Tk library.")
    section_offset = struct.unpack_from("<Q", data, 40)[0]
    section_size, section_count = struct.unpack_from("<HH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, section_offset + i * section_size)
                for i in range(section_count)]
    dynamic = next(section for section in sections if section[1] == 6)
    entries = range(dynamic[4], dynamic[4] + dynamic[5], 16)
    if any(struct.unpack_from("<Q", data, offset)[0] == 14 for offset in entries):
        return
    strings = sections[dynamic[6]]
    for offset in entries:
        tag, value = struct.unpack_from("<QQ", data, offset)
        if tag != 15:
            continue
        start = strings[4] + value
        end = data.index(0, start)
        identity = b"libtk8.6.so"
        if end - start < len(identity):
            raise RuntimeError("Tk build RPATH cannot hold its library identity.")
        data[start:end] = identity + b"\0" * (end - start - len(identity))
        struct.pack_into("<Q", data, offset, 14)
        library.write_bytes(data)
        return
    raise RuntimeError("Verified Tk package has neither SONAME nor build RPATH.")


def main() -> None:
    if not sys.platform.startswith("linux") or platform.machine() != "x86_64":
        raise SystemExit("This optional font runtime supports Linux x86_64 only.")
    import tkinter
    if tkinter.TkVersion != 8.6:
        raise SystemExit("This font runtime requires Tcl/Tk 8.6.")
    base = Path(__file__).resolve().parent / ".runtime"
    destination = base / "tk-xft"
    if (destination / "lib/libtk8.6.so").is_file():
        set_library_identity(destination / "lib/libtk8.6.so")
        print("Project-local Xft runtime is already prepared:", destination)
        return
    zstd = shutil.which("zstd")
    if not zstd:
        candidate = Path(sys.prefix).parent.parent / "bin/zstd"
        zstd = str(candidate) if candidate.is_file() else None
    if not zstd:
        raise SystemExit("A zstd decompressor is required; no environment was changed.")
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prepare-", dir=base) as temporary:
        work = Path(temporary)
        archive = work / PACKAGE
        digest = hashlib.sha256()
        total = 0
        with urllib.request.urlopen(URL, timeout=30) as response, archive.open("wb") as output:
            while chunk := response.read(128 * 1024):
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise RuntimeError("Font package exceeded the download size limit.")
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != SHA256:
            raise RuntimeError("Font package SHA-256 verification failed.")
        staging = work / "runtime"
        staging.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            for compressed in bundle.namelist():
                if not compressed.endswith(".tar.zst"):
                    continue
                packed = work / "part.tar.zst"
                unpacked = work / "part.tar"
                with bundle.open(compressed) as source, packed.open("wb") as output:
                    shutil.copyfileobj(source, output)
                with unpacked.open("wb") as output:
                    subprocess.run([zstd, "-d", "--stdout", str(packed)], stdout=output, check=True)
                if unpacked.stat().st_size > 50 * 1024 * 1024:
                    raise RuntimeError("Font package exceeded the unpacked size limit.")
                with tarfile.open(unpacked) as tar:
                    for member in tar:
                        name = member.name
                        if not member.isfile() or ".." in Path(name).parts:
                            continue
                        if name == "lib/libtk8.6.so" or name.startswith("lib/tk8.6/"):
                            target = staging / name
                        elif name.startswith("info/licenses/"):
                            target = staging / "licenses" / Path(name).name
                        else:
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, target.open("wb") as output:
                            shutil.copyfileobj(source, output)
        library = staging / "lib/libtk8.6.so"
        if not library.is_file() or not (staging / "lib/tk8.6/tk.tcl").is_file():
            raise RuntimeError("Font package does not contain the required Tk files.")
        set_library_identity(library)
        dependency_check = subprocess.run(["ldd", str(library)], capture_output=True, text=True, check=True)
        if "not found" in dependency_check.stdout:
            raise RuntimeError("Missing system libraries: " + dependency_check.stdout)
        (staging / "source.json").write_text(json.dumps({"url": URL, "sha256": SHA256, "bytes": total}, indent=2))
        staging.rename(destination)
    print("Prepared project-local Xft fonts:", destination)
    print("The existing conda environment was not changed. Start with bash linux/start.sh.")


if __name__ == "__main__":
    main()
