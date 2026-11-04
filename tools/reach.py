"""Conservative reachability analysis of the Charmera ARM image.

Roots: reset/exception vectors, every word in the loaded image that points at a
`push {..lr}` prologue or at a branch target (possible function pointer), and
every ADR (add/sub rX, pc, #imm) target. Edges: B/BL and fall-through.
A function is a range [push-prologue .. next prologue). Anything never reached
is reported as dead. Conservative = it may miss dead code, but should not
report live code as dead (unless code is reached through computed jumps).
"""
import bisect
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from fwdis import fw, u32  # noqa: E402

CODE_END = 0x0F33C0     # end of ARM code (rodata begins; verified by eye)
LOADED_END = 0x10AF30

def is_push_lr(w):
    return (w & 0xFFFF0000) == 0xE92D0000 and (w & 0x4000)

def branch_target(a, w):
    if (w >> 25) & 7 == 5 and (w >> 28) != 0xF:
        off = w & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        return a + 8 + off * 4
    return None

def adr_target(a, w):
    # add rd, pc, #imm  /  sub rd, pc, #imm  (data-processing immediate, Rn=pc)
    if (w & 0x0FEF0000) == 0x028F0000 or (w & 0x0FEF0000) == 0x024F0000:
        rot = ((w >> 8) & 0xF) * 2
        imm = w & 0xFF
        imm = ((imm >> rot) | (imm << (32 - rot))) & 0xFFFFFFFF
        return a + 8 + imm if (w & 0x00E00000) == 0x00800000 else a + 8 - imm
    return None

def main():
    global fstarts
    starts = sorted(a for a in range(0, CODE_END, 4) if is_push_lr(u32(a)))
    btargets = {}
    for a in range(0, CODE_END, 4):
        t = branch_target(a, u32(a))
        if t is not None and 0 <= t < CODE_END:
            btargets.setdefault(t, []).append(a)

    # function boundaries = prologues + BL targets
    bl_targets = {t for t, srcs in btargets.items() if any((u32(s) >> 24) & 1 for s in srcs)}
    fstarts = sorted(set(starts) | bl_targets)

    def func_of(a):
        i = bisect.bisect_right(fstarts, a) - 1
        return fstarts[i] if i >= 0 else 0

    def func_end(s):
        i = bisect.bisect_right(fstarts, s)
        return fstarts[i] if i < len(fstarts) else CODE_END

    roots = set(range(0, 0x40, 4))
    for a in range(0, LOADED_END, 2):
        v = struct.unpack_from("<I", fw, a)[0] & ~1
        if v < CODE_END and v % 4 == 0 and (v in fstarts or v in btargets):
            roots.add(v)
    for a in range(0, CODE_END, 4):
        t = adr_target(a, u32(a))
        if t is not None and t < CODE_END:
            roots.add(t & ~3)

    seen = set()
    work = [func_of(r) for r in roots]
    while work:
        f = work.pop()
        if f in seen:
            continue
        seen.add(f)
        e = func_end(f)
        for a in range(f, e, 4):
            t = branch_target(a, u32(a))
            if t is not None and t < CODE_END:
                work.append(func_of(t))
        # fall-through: if the word before the next start isn't an unconditional
        # return/branch (or a literal-pool word), treat the next function as reached
        if e < CODE_END:
            last = u32(e - 4)
            terminator = (
                last in (0xE1A0F00E, 0xE12FFF1E)
                or ((last & 0x0FFF0000) == 0x08BD0000 and (last & 0x8000) and last >> 28 == 0xE)
                or ((last >> 24) == 0xEA)
                or ((last & 0x0FFFF000) == 0x049DF000 and last >> 28 == 0xE)
                or (last >> 28) not in (0xE,) and branch_target(e - 4, last) is None
            )
            if not terminator:
                work.append(e)

    dead = [(f, func_end(f)) for f in fstarts if f not in seen]
    live_ranges = [(f, func_end(f)) for f in fstarts if f in seen]
    if not __name__ == "__main__":
        return dead, live_ranges
    # merge adjacent dead functions
    merged = []
    for s, e in dead:
        if merged and merged[-1][1] == s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    merged.sort(key=lambda r: r[0] - r[1])
    tot = sum(e - s for s, e in dead)
    print("functions: %d, reached: %d, dead: %d (%d bytes)" % (len(fstarts), len(seen), len(dead), tot))
    for s, e in merged[: int(sys.argv[1]) if len(sys.argv) > 1 else 20]:
        print("dead RAM %06X-%06X  %5d bytes" % (s, e, e - s))


if __name__ == "__main__":
    main()
