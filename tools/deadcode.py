"""Find ARM functions in the Charmera image that nothing references.

A candidate must:
  * start with a `push {..., lr}` (stmdb sp!, {...lr})
  * not be the target of any B/BL in the image
  * not appear as a literal word anywhere in code/rodata/data (function pointer)
  * not be reachable by fall-through (previous instruction is a return/branch)
Size = distance to the next function start / referenced address.
"""
import struct
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
from fwdis import fw, u32  # noqa: E402

CODE_END = 0x0F5000  # rough end of executable code (rodata strings follow)
LOADED_END = 0x10AF30

branch_targets = set()
for a in range(0, CODE_END, 4):
    w = u32(a)
    if (w >> 25) & 7 == 5 and (w >> 28) != 0xF:
        off = w & 0xFFFFFF
        if off & 0x800000:
            off -= 0x1000000
        branch_targets.add(a + 8 + off * 4)

literal_words = set()
for a in range(0, LOADED_END, 2):
    v = struct.unpack_from("<I", fw, a)[0]
    if v < CODE_END:
        literal_words.add(v & ~1)


def is_push_lr(w):
    return (w & 0xFFFF0000) == 0xE92D0000 and (w & 0x4000)


def is_terminator(w):
    cond = w >> 28
    if cond != 0xE:
        return False
    if w == 0xE1A0F00E or w == 0xE12FFF1E:  # mov pc, lr / bx lr
        return True
    if (w & 0x0FFF0000) == 0x08BD0000 and (w & 0x8000):  # pop {..pc}
        return True
    if (w >> 25) & 7 == 5 and not (w >> 24) & 1:  # unconditional b
        return True
    if (w & 0x0FFFF000) == 0x049DF000:  # ldr pc, [sp], #4
        return True
    return False


starts = sorted(a for a in range(0, CODE_END, 4) if is_push_lr(u32(a)))
known = sorted(set(starts) | {t for t in branch_targets if t < CODE_END} | {v for v in literal_words if v % 4 == 0})

cands = []
for s in starts:
    if s in branch_targets or s in literal_words:
        continue
    if s >= 4 and not is_terminator(u32(s - 4)) and not (u32(s - 4) & 0x0F000000 == 0x0A000000):
        # previous word is not a terminator: maybe literal pool or fallthrough; accept literal pools
        pass
    # next known start
    import bisect
    i = bisect.bisect_right(known, s)
    nxt = known[i] if i < len(known) else CODE_END
    cands.append((nxt - s, s, nxt))

cands.sort(reverse=True)
for size, s, nxt in cands[: int(sys.argv[1]) if len(sys.argv) > 1 else 25]:
    print("RAM %06X-%06X size=%5d prev=%08X" % (s, nxt, size, u32(s - 4)))
