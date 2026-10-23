"""Stand-in for REDkit's wcc_lite in tests (cook / pack / metadatastore / unbundle, same arguments as
build.yaml).

FAKE_WCC_BREAK=<mode> makes it misbehave, to test that the build catches it:
  cook_keeps_swf, cook_alters_code, pack_wrong_file, no_metadata, fail

cook:          for each file under -mod=, loads it from the project given by "-workspaceDir <dir>"
               (<dir>\\workspace\\<path>, as the real one does) and writes it to -outdir= without the
               embedded source SWF (cooked form). Without -workspaceDir the real one cooks REDkit's
               vanilla copy; this one fails.
pack:          writes -outdir=/blob0.bundle (REDkit's 0x130-entry layout, stored) from the files in -dir=
metadatastore: writes -path=/metadata.store
unbundle:      writes every file of every .bundle under -dir= to -outdir=/<depot path>, read with
               redbundle's reader (tests check the build's wcc_lite plumbing, not wcc_lite)
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from redbundle import Bundle                                       # noqa: E402
from redswf_patch import RedSwf                                    # noqa: E402
from swf_patch.swf import read_abc, replace_abc                    # noqa: E402

BREAK = os.environ.get("FAKE_WCC_BREAK", "")


def option(name):
    """The value of a -name=value argument."""
    return next(a.split("=", 1)[1] for a in sys.argv[2:] if a.startswith(f"-{name}="))


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def cook():
    if BREAK == "fail":
        print("wcc_lite: fatal error (simulated)")
        sys.exit(3)
    listed, out_dir = option("mod"), option("outdir")
    if "-workspaceDir" not in sys.argv:
        print("wcc_lite (fake): no -workspaceDir <project>; the real one would cook the depot's copy")
        sys.exit(4)
    workspace = os.path.join(sys.argv[sys.argv.index("-workspaceDir") + 1], "workspace")
    for directory, _, files in os.walk(listed):
        for name in files:
            rel = os.path.relpath(os.path.join(directory, name), listed)
            data = read_bytes(os.path.join(workspace, rel))
            out = os.path.join(out_dir, rel)
            if BREAK == "cook_keeps_swf":
                write_bytes(out, data)
                continue
            resource = RedSwf(data)
            resource.cooked = True                # drop the source SWF, keep everything else
            gfx = resource.gfx
            if BREAK == "cook_alters_code":       # e.g. a cooker that re-exports the GFX from the source SWF
                abc = bytearray(read_abc(gfx))
                abc[-1] ^= 1
                gfx = replace_abc(gfx, bytes(abc))
            write_bytes(out, resource.build(gfx))


def pack():
    source, out_dir = option("dir"), option("outdir")
    files = sorted(os.path.relpath(os.path.join(d, f), source) for d, _, fs in os.walk(source) for f in fs)
    table_size = 0x130 * len(files)
    blobs, offset, table = [], 0x20 + table_size, b""
    for rel in files:
        data = read_bytes(os.path.join(source, rel))
        if BREAK == "pack_wrong_file":
            data = data[:-1] + bytes([data[-1] ^ 1])
        name = rel.replace(os.sep, "\\").encode().ljust(0x100, b"\0")
        # name, hash (zeros), then offset, ?, size, stored size, crc32, compression (0 = stored), padding
        table += name + b"\0" * 16 + struct.pack("<IIIIII", offset, 0, len(data), len(data), zlib.crc32(data), 0) + b"\0" * 8
        blobs.append(data)
        offset += len(data)
    body = b"".join(blobs)
    total = 0x20 + table_size + len(body)
    header = b"POTATO70" + struct.pack("<III", (total + 15) // 16 * 16, 0, table_size) + b"\0" * 12
    write_bytes(os.path.join(out_dir, "blob0.bundle"), header + table + body)


def metadatastore():
    if BREAK == "no_metadata":
        return
    write_bytes(os.path.join(option("path"), "metadata.store"), b"fake metadata store\n")


def unbundle():
    source, out_dir = option("dir"), option("outdir")
    for directory, _, files in os.walk(source):
        for name in files:
            if name.lower().endswith(".bundle"):
                bundle = Bundle(read_bytes(os.path.join(directory, name)))
                for entry in bundle.entries:
                    write_bytes(os.path.join(out_dir, *entry.name.split("\\")), bundle.read(entry))


{"cook": cook, "pack": pack, "metadatastore": metadatastore, "unbundle": unbundle}[sys.argv[1]]()
