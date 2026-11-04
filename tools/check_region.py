"""Verify a RAM range is not referenced from outside itself."""
import sys, os, struct
sys.path.insert(0, os.path.dirname(__file__))
from fwdis import fw, u32
import bisect
import reach
from reach import branch_target, adr_target, CODE_END, LOADED_END
dead, live = reach.main()
live_starts=[s for s,e in live]
def is_live(a):
    if a >= CODE_END: return True   # data words count as live references
    i=bisect.bisect_right(live_starts,a)-1
    return i>=0 and live[i][0] <= a < live[i][1]
lo, hi = int(sys.argv[1], 0), int(sys.argv[2], 0)
bad = []
for a in range(0, CODE_END, 4):
    if lo <= a < hi or not is_live(a): continue
    w = u32(a)
    t = branch_target(a, w)
    if t is not None and lo <= t < hi: bad.append(('branch', a, t))
    t = adr_target(a, w)
    if t is not None and lo <= t < hi: bad.append(('adr', a, t))
    if (w & 0x0F7F0000) == 0x051F0000 and (w >> 28) != 0xF:   # ldr rX,[pc,#+/-imm]
        imm = w & 0xFFF; t = a + 8 + (imm if (w >> 23) & 1 else -imm)
        if lo <= t < hi: bad.append(('ldr-pc', a, t))
    if (w & 0x0E5F0000) == 0x004F0000 and (w >> 28) != 0xF:   # ldrh/ldrsb [pc,#imm]
        imm = ((w >> 4) & 0xF0) | (w & 0xF); t = a + 8 + (imm if (w >> 23) & 1 else -imm)
        if lo <= t < hi: bad.append(('ldrh-pc', a, t))
for a in range(0, LOADED_END, 2):
    if lo <= a < hi or not is_live(a): continue
    v = struct.unpack_from('<I', fw, a)[0]
    if lo <= (v & ~1) < hi: bad.append(('pointer', a, v))
print('refs into %06X-%06X from outside: %d' % (lo, hi, len(bad)))
for b in bad[:20]: print('  %-8s at %06X -> %06X' % b)
