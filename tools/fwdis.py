"""Tiny disassembly helpers for the Charmera image, indexed by RAM address (= flash offset - 0x200)."""
import os
import struct
import sys

import capstone

# The stock 2 MiB flash dump (not in this repository; see README.md, "Firmware Dump").
FW_PATH = os.environ.get("CHARMERA_DUMP", "third_party/keychain-camera-plus/dumps/charmera_stock_latest.bin")
_raw = open(FW_PATH, "rb").read()
HDR = 0x200  # SPI boot header is not loaded: RAM address = file offset - 0x200
fw = _raw[HDR:]  # index by RAM address
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
md.detail = False
mdt = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)


def u32(a):
    return struct.unpack_from("<I", fw, a)[0]


def dis(addr, n=40, thumb=False, out=sys.stdout):
    m = mdt if thumb else md
    end = addr + (n * (2 if thumb else 4))
    a = addr
    while a < end:
        got = False
        for insn in m.disasm(fw[a:end], a):
            extra = ""
            if insn.mnemonic.startswith("ldr") and "[pc" in insn.op_str:
                try:
                    imm = int(insn.op_str.split("#")[-1].rstrip("]!"), 0)
                    lit = ((insn.address + (4 if thumb else 8)) & ~3) + imm
                    extra = "    ; =0x%08X" % u32(lit)
                except Exception:
                    pass
            print("%08X: %-10s %-8s %s%s" % (insn.address, insn.bytes.hex(), insn.mnemonic, insn.op_str, extra), file=out)
            a = insn.address + insn.size
            got = True
        if not got or a < end:
            print("%08X: %-10s .word" % (a, fw[a:a + 4].hex()), file=out)
            a += 4


def xrefs_bl(target, lo=0, hi=0x10B000):
    """Find ARM BL/B instructions targeting `target`."""
    res = []
    for a in range(lo, hi, 4):
        w = u32(a)
        if (w >> 25) & 7 == 5 and (w >> 28) != 0xF:
            off = w & 0xFFFFFF
            if off & 0x800000:
                off -= 0x1000000
            if a + 8 + off * 4 == target:
                res.append((a, "bl" if w & (1 << 24) else "b", w >> 28))
    return res


def literal_refs(value, lo=0, hi=0x10B000):
    return [a for a in range(lo, hi, 4) if u32(a) == value]


if __name__ == "__main__":
    a = int(sys.argv[1], 0)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    dis(a, n, thumb=len(sys.argv) > 3 and sys.argv[3] == "t")


def pcrel_users(lit, span=0x1000):
    """ARM 'ldr rX, [pc, #+/-imm]' instructions that load the word at `lit`."""
    users = []
    for a in range(max(0, lit - span) & ~3, lit + span, 4):
        w = u32(a)
        if (w & 0x0F7F0000) == 0x051F0000 and (w >> 28) != 0xF:
            imm = w & 0xFFF
            t = a + 8 + (imm if (w >> 23) & 1 else -imm)
            if t == lit:
                users.append(a)
    return users


def users_of_value(value, lo=0, hi=0x10B000):
    """All instructions that load a literal-pool word equal to `value`."""
    out = []
    for lit in literal_refs(value, lo, hi):
        out += pcrel_users(lit)
    return sorted(out)
