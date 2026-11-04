"""Checks that every program file (.exe .dll .pyd) under the given folders is built for one CPU,
by reading the machine field of its PE header. Usage: check_arch.py arm64|x64 <folder>..."""
import struct
import sys
from pathlib import Path

MACHINES = {0xAA64: "arm64", 0x8664: "x64", 0x014C: "x86", 0xA641: "arm64ec"}


def machine(path):
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:2] != b"MZ":
        return None
    pe = struct.unpack("<I", head[0x3C:0x40])[0]
    if head[pe:pe + 4] != b"PE\0\0":
        return None
    return MACHINES.get(struct.unpack("<H", head[pe + 4:pe + 6])[0], "unknown")


def main(want, *folders):
    bad, count = [], 0
    for folder in folders:
        for p in Path(folder).rglob("*"):
            if p.suffix.lower() in (".exe", ".dll", ".pyd") and p.is_file():
                m = machine(p)
                count += 1
                if m != want:
                    bad.append(f"{m}: {p}")
    print(f"{count} program files checked, {len(bad)} not {want}")
    for b in bad[:40]:
        print("  ", b)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(*sys.argv[1:])
