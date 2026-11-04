"""List code-cave candidates: dead functions with no reference from live code or data.

A byte range is usable when no live instruction branches/ADRs/pc-loads into it and no
word in the loaded image (outside dead code) points into it.
"""
import bisect
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(__file__))
from fwdis import fw, u32  # noqa: E402
import reach  # noqa: E402
from reach import branch_target, adr_target, CODE_END, LOADED_END  # noqa: E402


def main(min_len=256, top=30):
    dead, live = reach.main()
    live_starts = [s for s, e in live]
    dead_sorted = sorted(dead)
    dstarts = [s for s, e in dead_sorted]

    def is_dead(a):
        i = bisect.bisect_right(dstarts, a) - 1
        return i >= 0 and dead_sorted[i][0] <= a < dead_sorted[i][1]

    hits = set()
    for a in range(0, CODE_END, 4):
        if is_dead(a):
            continue
        w = u32(a)
        for t in (branch_target(a, w), adr_target(a, w)):
            if t is not None:
                hits.add(t & ~3)
        if (w & 0x0F7F0000) == 0x051F0000 and (w >> 28) != 0xF:
            imm = w & 0xFFF
            hits.add((a + 8 + (imm if (w >> 23) & 1 else -imm)) & ~3)
        if (w & 0x0E5F0000) == 0x004F0000 and (w >> 28) != 0xF:
            imm = ((w >> 4) & 0xF0) | (w & 0xF)
            hits.add((a + 8 + (imm if (w >> 23) & 1 else -imm)) & ~3)
    for a in range(0, LOADED_END, 2):
        if a < CODE_END and is_dead(a):
            continue
        v = struct.unpack_from("<I", fw, a)[0] & ~3
        if v < CODE_END:
            hits.add(v)

    # merge adjacent dead functions, then cut at referenced words
    merged = []
    for s, e in dead_sorted:
        if merged and merged[-1][1] == s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    spans = []
    for s, e in merged:
        cur = s
        for a in range(s, e, 4):
            if a in hits:
                if a - cur >= min_len:
                    spans.append((cur, a))
                cur = a + 4
        if e - cur >= min_len:
            spans.append((cur, e))
    spans.sort(key=lambda r: r[0] - r[1])
    return spans[:top]


if __name__ == "__main__":
    for s, e in main():
        print("free RAM %06X-%06X  %5d bytes" % (s, e, e - s))
