"""Parse the Generalplus 'GP+' resource pack embedded in the Charmera firmware.

Layout (reverse engineered from the stock dump):
  pack header (0x18 bytes): b'GP+\\0', u16, u16, ... ; u32 count at +2? (see below)
  entries start at +0x18, 24 bytes each:
      char name[20]   -- base name immediately followed by extension, NUL padded
      u32  sector     -- offset from pack start in 512-byte sectors
"""
import struct
import sys
import zlib

PACK_BASE = 0x120000
SECTOR = 0x200


def parse_pack(fw: bytes, base: int = PACK_BASE):
    assert fw[base:base + 3] == b"GP+", "no GP+ pack at %#x" % base
    entries = []
    off = base + 0x18
    while True:
        name = fw[off:off + 20]
        if name[0] == 0:
            break
        sector = struct.unpack_from("<I", fw, off + 20)[0]
        entries.append([name.rstrip(b"\0").decode("ascii"), base + sector * SECTOR])
        off += 24
    # size = distance to the next entry (sector granular slot)
    for i, e in enumerate(entries):
        nxt = entries[i + 1][1] if i + 1 < len(entries) else None
        e.append(nxt - e[1] if nxt else None)
    return entries


def gpzp_inflate(blob: bytes):
    """Return (raw_bytes, header_info) for a GPZP blob."""
    assert blob[:4] == b"GPZP"
    for skip in (4, 8, 12):
        for wbits in (-15, 15):
            try:
                d = zlib.decompressobj(wbits)
                out = d.decompress(blob[skip:])
                if d.eof:
                    used = len(blob) - skip - len(d.unused_data)
                    return out, dict(skip=skip, wbits=wbits, compressed=used)
            except zlib.error:
                pass
    raise ValueError("could not inflate")


if __name__ == "__main__":
    fw = open(sys.argv[1], "rb").read()
    for name, off, size in parse_pack(fw):
        info = ""
        if name.endswith("GPZP") or name.endswith("GPZ"):
            try:
                raw, h = gpzp_inflate(fw[off:off + (size or 0x40000)])
                info = "inflated=%d %s" % (len(raw), h)
            except Exception as ex:  # noqa
                info = "inflate failed: %s ; head=%s" % (ex, fw[off:off + 16].hex())
        else:
            info = "head=%s" % fw[off:off + 12].hex()
        print("%-22s @%06X slot=%-7s %s" % (name, off, size, info))
