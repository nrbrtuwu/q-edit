"""Unpacks the python.org ARM64 installer (python-3.x.y-arm64.exe) into a plain Python folder
with Tkinter, without installing anything, so the ARM64 build can ship it.

The installer is a WiX bundle: the MSI packages sit in a cabinet appended to the .exe, named
a0, a1, ...; the bundle's manifest (file "0" in the first cabinet) maps those to core.msi etc.
Windows' own expand.exe unpacks the cabinets and msiexec /a ("administrative install") only
extracts an MSI's files. Usage: arm64_python.py <installer.exe> <out folder>
"""
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

WANT = ["core.msi", "exe.msi", "lib.msi", "tcltk.msi"]   # Python, pythonw.exe, stdlib, Tkinter


def cabinets(data):
    """(offset, size) of the cabinets ("MSCF" header, version 1.3) appended to the file; the
    search skips over each one found, as the MSIs inside carry cabinets of their own."""
    out, i = [], data.find(b"MSCF")
    while i >= 0:
        size = struct.unpack("<I", data[i + 8:i + 12])[0]
        if size and data[i + 24:i + 26] == b"\x03\x01" and i + size <= len(data):
            out.append((i, size))
            i = data.find(b"MSCF", i + size)
        else:
            i = data.find(b"MSCF", i + 4)
    return out


def main(exe, out):
    exe, out = Path(exe), Path(out)
    data = exe.read_bytes()
    tmp = Path(tempfile.mkdtemp(prefix="arm64py_"))
    try:
        dirs = []
        for n, (off, size) in enumerate(cabinets(data)):
            cab = tmp / f"cab{n}.cab"
            cab.write_bytes(data[off:off + size])
            d = tmp / f"c{n}"
            d.mkdir()
            subprocess.run(["expand", str(cab), "-F:*", str(d)], check=True,
                           stdout=subprocess.DEVNULL)
            dirs.append(d)
        manifest = (dirs[0] / "0").read_bytes().decode("utf-8", "replace")
        source = {}
        for m in re.finditer(r'<Payload [^>]*FilePath="([^"]+)"[^>]*SourcePath="(a\d+)"', manifest):
            source.setdefault(m.group(1), m.group(2))   # first of the all-users / per-user pair
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        for name in WANT:
            payload = next(d / source[name] for d in dirs if (d / source[name]).exists())
            msi = tmp / name
            shutil.copy2(payload, msi)
            r = subprocess.run(["msiexec", "/a", str(msi), "/qn", f"TARGETDIR={out}"])
            if r.returncode:
                sys.exit(f"msiexec failed on {name} ({r.returncode})")
        for f in out.glob("*.msi"):   # administrative installs leave a copy of the MSI behind
            f.unlink()
        if not (out / "pythonw.exe").exists() or not (out / "Lib" / "tkinter").is_dir():
            sys.exit("the unpacked Python is incomplete")
        print(f"ARM64 Python unpacked into {out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
