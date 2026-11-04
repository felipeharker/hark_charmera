#!/usr/bin/env python3
"""
build_fx.py - Kodak Charmera custom firmware: extra colour looks + pixel text stamp,
packaged for the camera's built-in SD-card updater.

Target: original-series Charmera, firmware "JH5408 20250520" (built Oct 14 2025),
Generalplus GPCV1235F (ARM7TDMI, no MMU), 2 MiB SPI NOR. Addresses are RAM
addresses; flash/file offset = RAM + 0x200 (the boot header is not loaded).

Effect wheel (Up/Down in photo mode), effect byte at RAM 0x10FBC5:
  0 Standard  1 Warm  2 Cool  3 B&W  4-7 frames  8-11 Pixel
  12-14 custom colour looks from looks.json          (v1)
  15    Text: on-camera editor, live preview, stamped into the photo   (v2)

Every patch site is checked against the expected stock bytes; every region we
write new code/data into is checked against a pinned hash of the stock bytes.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile
from keystone import Ks, KS_ARCH_ARM, KS_MODE_ARM

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fx_tables  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR = 0x200
CODE_REGION_END = 0x170000
SETTINGS_BLOCK = (0x170000, 0x180000)
PACKAGE_SIZE = 0x1E0000
STOCK_STAMP = b"202505201029"
STOCK_CODE_SHA256 = "fe88e8de57e7b1ff1a0856127ee09a21fb3ce2dde2684662c533719b80adc5c8"
STOCK_CDSP_SHA256 = "fadbb7dff40e801b5d29c9af172718c2810505ff5a7d94b80cb940855151f701"

# Dead, unreferenced code regions (tools/free_spans.py) and their stock contents
FREE_REGIONS = {
    "CODE":  (0x036A60, 0x0372A8, "596536c2a86fda16232ecfa906fc819532dc7ab773acd0646496e54ae0f82cf0"),
    "CODE2": (0x0893A4, 0x089A00, "8cb1de19985bd182d67d75185b28f93eb9f3de5016c438ecd6b8fe5014be62c4"),
    "CODE3": (0x0DF790, 0x0DFC54, "dd4cb8e73963698270a3433c56f678807dc73985d81a7901903deae4423b5512"),
    "DATA":  (0x0753A4, 0x075910, "1eb503681642aa654f9eb7be34167fa02936047c484f27ed73b0f6c954cebeba"),
    "ICON0": (0x0ABB20, 0x0ABD38, "861469ea8de4b847dae45956f56879d18e5e46726ad69698b1663b75843a5edb"),
    "ICON1": (0x075F10, 0x076138, "29d98cb13f9751d6563d7a8881237e96ac51dd859fbc5d4b356ac71b2ef1406e"),
    "ICON2": (0x09ADC0, 0x09B004, "caa4b7b5ddf2173fa97a5a5f27c99b5ff6a603b374f864ccf38652a1b31e1747"),
    "ICON3": (0x08EC00, 0x08EE4C, "961247f27ba621a7ba52e441145c1a205059a3923f3e8ce90220a30de6144d1d"),
}

# --- stock firmware addresses (RAM) ------------------------------------------
OSD_HIDE = 0x21C14
CDSP_MODE = 0x2D368
CDSP_MODE_END = 0x2D424
CDSP_SET_A = 0x64958
CDSP_SET_B = 0x649B8
ICON_DESC = 0x1000D4             # u16 w, u16 h, u32 key, s16 x, s16 y
ICON_PTRS = 0x106F38
ICON_PLACEHOLDER_PTR = 0x0F563C
NEW_ICON_IDS = (0x2A, 0x2B, 0x2C, 0x2D)   # Sepia, Cyanotype, Faded, Text
STAMP_STR = 0x216C4
VERSION_STR = 0x18764
TRANSPARENT = 0x8C71
LAST_EFFECT = 15

STOCK_PARAMS = [
    ((-1, 2, 0x28, 0x28), (0, 0, 0x20, 0x20)),        # 0 Standard
    ((-20, 20, 5, 20), (-20, 20, 5, 20)),             # 1 Warm
    ((20, -20, 20, 5), (20, -20, 20, 5)),             # 2 Cool
    ((0, 0, 0, 0), (0, 0, 0, 0)),                     # 3 B&W
]

ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)


def asm(src, addr):
    enc, _ = ks.asm(src, addr)
    return bytes(enc)


class Image:
    def __init__(self, raw):
        self.b = bytearray(raw)
        self.log = []

    def get(self, ram, n):
        return bytes(self.b[ram + HDR: ram + HDR + n])

    def put(self, ram, data, expect=None, what=""):
        if expect is not None and self.get(ram, len(expect)) != expect:
            sys.exit("ABORT: unexpected bytes at RAM %06X (%s): %s != %s"
                     % (ram, what, self.get(ram, len(expect)).hex(), expect.hex()))
        self.b[ram + HDR: ram + HDR + len(data)] = data
        self.log.append((ram, len(data), what))

    def put_insn(self, ram, new_src, old_src, what):
        self.put(ram, asm(new_src, ram), expect=asm(old_src, ram), what=what)

    def check_region(self, name):
        lo, hi, sha = FREE_REGIONS[name]
        if hashlib.sha256(self.get(lo, hi - lo)).hexdigest() != sha:
            sys.exit("ABORT: region %s does not contain the expected dead code" % name)

    def put_region(self, name, ram, data, what):
        lo, hi, _ = FREE_REGIONS[name]
        if not (lo <= ram and ram + len(data) <= hi):
            sys.exit("ABORT: %s does not fit region %s" % (what, name))
        self.put(ram, data, what=what)


def rgb565(c):
    r, g, b = c
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def make_look_icon(img, look):
    """Recolour the stock B&W icon (id 0x62) mask with the look's colours."""
    src_ptr = struct.unpack("<I", img.get(ICON_PTRS + 0x62 * 4, 4))[0]
    px = struct.unpack("<256H", img.get(src_ptr, 512))
    fg, bg = rgb565(look["icon_fg"]), rgb565(look["icon_bg"])
    if TRANSPARENT in (fg, bg):
        sys.exit("icon colour collides with transparent key 0x8C71; nudge it")
    return struct.pack("<256H", *[fg if v == 0xFFFF else bg for v in px])


def validate_looks(looks):
    if len(looks) != 3:
        sys.exit("looks.json must define exactly 3 looks (effect slots 12, 13, 14)")
    for lk in looks:
        for k in ("u_offset", "v_offset"):
            if not -128 <= lk[k] <= 127:
                sys.exit("%s.%s out of range -128..127" % (lk["name"], k))
        for k in ("u_gain", "v_gain"):
            if not 0 <= lk[k] <= 64:
                sys.exit("%s.%s out of range 0..64" % (lk["name"], k))


# --- text feature: compile + link the C code ---------------------------------
def build_text_feature(outdir):
    src = os.path.join(ROOT, "fx_text")
    open(os.path.join(src, "fx_tables.h"), "w").write(fx_tables.render_header())
    zig = [sys.executable, "-m", "ziglang"]
    obj = os.path.join(outdir, "fx_text.o")
    elf = os.path.join(outdir, "fx_text.elf")
    cflags = ["-target", "arm-freestanding-eabi", "-mcpu=arm7tdmi", "-marm", "-Os", "-std=c99",
              "-Wall", "-Wextra", "-Werror", "-ffreestanding", "-fno-builtin", "-fno-pic",
              "-ffunction-sections", "-fdata-sections", "-fno-unwind-tables",
              "-fno-asynchronous-unwind-tables", "-fno-stack-protector"]
    subprocess.run(zig + ["cc"] + cflags + ["-c", os.path.join(src, "fx_text.c"), "-o", obj], check=True)
    subprocess.run(zig + ["ld.lld", "-T", os.path.join(src, "link.ld"), "--no-undefined", "-e", "0",
                          obj, "-o", elf, "-Map", os.path.join(outdir, "fx_text.map")], check=True)
    e = ELFFile(open(elf, "rb"))
    sections = [(s.name, s["sh_addr"], s.data()) for s in e.iter_sections()
                if s["sh_flags"] & 2 and s["sh_size"]]
    syms = {s.name: s["st_value"] for s in e.get_section_by_name(".symtab").iter_symbols()
            if s.name and s["st_shndx"] != "SHN_UNDEF"}
    for name, _, _ in sections:
        if name not in (".text", ".text2", ".text3", ".rodata", ".data"):
            sys.exit("ABORT: unexpected section %s in fx_text.elf" % name)
    return sections, syms


def region_of(addr, size):
    for name, (lo, hi, _) in FREE_REGIONS.items():
        if lo <= addr and addr + size <= hi:
            return name
    sys.exit("ABORT: %06X+%d is outside every free region" % (addr, size))


def patch(img, looks, stamp, outdir):
    for name in FREE_REGIONS:
        img.check_region(name)

    # ---- 1. effect wheel 0..15, raw effect passed to cdsp_mode. The key driver
    #      (RAM 0x7680..0x79F0) sends Up = msg 0x7003 -> handler 0x1DC24 (previous
    #      effect) and Down = msg 0x7002 -> handler 0x1DCB4 (next effect).
    img.put_insn(0x1DC70, "moveq r0, #%d" % LAST_EFFECT, "moveq r0, #0xb", "up: wrap 0 -> 15")
    img.put_insn(0x1DC78, "beq 0x1DC94", "beq 0x1DC90", "up: wrap path keeps effect in r0")
    img.put_insn(0x1DC8C, "b 0x1DC94", "bls 0x1DC94", "up: no clamp to 0..3")
    img.put_insn(0x1DCFC, "cmp r0, #%d" % LAST_EFFECT, "cmp r0, #0xb", "down: wrap after 15")
    img.put_insn(0x1DD18, "b 0x1DD28", "cmp r0, #3", "down: no clamp to 0..3")

    # ---- 2. cdsp_mode rewrite (table driven; stock modes 0..3 byte-identical)
    params = list(STOCK_PARAMS)
    for lk in looks:
        p = (lk["u_offset"], lk["v_offset"], lk["u_gain"], lk["v_gain"])
        params.append((p, p))
    effect_to_mode = bytes([0, 1, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 4, 5, 6, 0])   # 15 Text -> standard colour
    code = asm("""
        push  {r4, lr}
        cmp   r0, #15
        movhi r0, #0
        add   r4, pc, #MAPOFF
        ldrb  r0, [r4, r0]
        add   r4, r4, #16
        add   r4, r4, r0, lsl #3
        ldrsb r0, [r4]
        ldrsb r1, [r4, #1]
        ldrb  r2, [r4, #2]
        ldrb  r3, [r4, #3]
        bl    %d
        ldrsb r0, [r4, #4]
        ldrsb r1, [r4, #5]
        ldrb  r2, [r4, #6]
        ldrb  r3, [r4, #7]
        pop   {r4, lr}
        b     %d
    """.replace("MAPOFF", str(18 * 4 - (3 * 4 + 8))) % (CDSP_SET_A, CDSP_SET_B), CDSP_MODE)
    assert len(code) == 18 * 4
    blob = code + effect_to_mode + b"".join(struct.pack("<bbBBbbBB", *a, *b) for a, b in params)
    assert len(blob) <= CDSP_MODE_END - CDSP_MODE
    if hashlib.sha256(img.get(CDSP_MODE, CDSP_MODE_END - CDSP_MODE)).hexdigest() != STOCK_CDSP_SHA256:
        sys.exit("ABORT: cdsp_mode is not the stock function")
    img.put(CDSP_MODE, blob.ljust(CDSP_MODE_END - CDSP_MODE, b"\0"), what="cdsp_mode table-driven rewrite")

    # ---- 3. text feature code/data into dead regions
    sections, syms = build_text_feature(outdir)
    for name, addr, data in sections:
        img.put_region(region_of(addr, len(data)), addr, data, "fx_text %s" % name)

    # ---- 4. icons: three looks + Text, each in its own dead span
    icons = [make_look_icon(img, lk) for lk in looks] + [fx_tables.icon_rgb565("T")]
    icon_addrs = []
    for i, data in enumerate(icons):
        lo = FREE_REGIONS["ICON%d" % i][0]
        img.put_region("ICON%d" % i, lo, data, "icon %02X pixels" % NEW_ICON_IDS[i])
        icon_addrs.append(lo)
    for iid, addr in zip(NEW_ICON_IDS, icon_addrs):
        img.put(ICON_DESC + iid * 12, struct.pack("<HHIhh", 16, 16, TRANSPARENT, 0, 56),
                expect=struct.pack("<HHIhh", 32, 24, TRANSPARENT, 120, 25), what="icon %02X descriptor" % iid)
        img.put(ICON_PTRS + iid * 4, struct.pack("<I", addr),
                expect=struct.pack("<I", ICON_PLACEHOLDER_PTR), what="icon %02X pixel pointer" % iid)

    # ---- 5. hooks into the stock firmware
    s = syms
    img.put_insn(0x1DD38, "bl %d" % s["fx_icon_for_effect"], "add r0, r0, #0x5f", "show: icon id via table")
    img.put_insn(0x1DC60, "bl %d" % s["fx_hide_effect_icons"], "bl %d" % OSD_HIDE, "up: hide all effect icons")
    img.put_insn(0x1DCF0, "bl %d" % s["fx_hide_effect_icons"], "bl %d" % OSD_HIDE, "down: hide all effect icons")
    img.put_insn(0x1D9DC, "beq %d" % s["stub_down"], "beq 0x1DCB4", "msg 0x7002 Down -> text editor first")
    img.put_insn(0x1D9E8, "beq %d" % s["stub_up"], "beq 0x1DC24", "msg 0x7003 Up -> text editor first")
    img.put_insn(0x1DB50, "beq %d" % s["stub_play"], "beq 0x1DEC0", "msg 0x7061 Play/OK -> text editor first")
    img.put_insn(0x160E0, "bhi %d" % s["stub_disp"], "bhi 0x16120", "viewfinder: draw text")
    img.put_insn(0x6E458, "bl %d" % s["stub_cap"], "bl 0xECF0", "after capture: stamp text")

    # ---- 6. update stamp + version string
    img.put(STAMP_STR, stamp.encode(), expect=STOCK_STAMP, what="update stamp")
    ver = ("JH5408  FX" + stamp[2:8]).encode()
    img.put(VERSION_STR, ver, expect=b"JH5408  20250520", what="version string")
    return {"syms": {k: hex(v) for k, v in syms.items() if not k.startswith("$")},
            "sections": [(n, hex(a), len(d)) for n, a, d in sections],
            "icons": {hex(i): hex(a) for i, a in zip(NEW_ICON_IDS, icon_addrs)}, "version": ver.decode()}


def package(code_img, settings_src, stamp):
    out = bytearray(b"\xff" * PACKAGE_SIZE)
    out[:CODE_REGION_END] = code_img[:CODE_REGION_END]
    lo, hi = SETTINGS_BLOCK
    out[lo:hi] = settings_src[lo:hi]
    assert settings_src[0x17F000:0x17F080] == b"\0" * 0x80, "settings source has internal photos indexed"
    chk = sum(out) & 0xFFFFFFFF
    name = "JH_5408%s_%08X.bin" % (stamp, chk)
    assert len(name) == 32 and name[19] == "_"
    return bytes(out), name


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="third_party/keychain-camera-plus/dumps/charmera_stock_latest.bin")
    ap.add_argument("--crosscheck", default="third_party/Project-Sirius/Original_Firmware.bin")
    ap.add_argument("--looks", default="looks.json")
    ap.add_argument("--out", default="build")
    ap.add_argument("--stamp", default=None, help="12-digit YYYYMMDDhhmm (default: now)")
    a = ap.parse_args()

    base = open(a.base, "rb").read()
    assert len(base) == 0x200000 and base[:4] == b"SPII", "base is not a 2 MiB Charmera dump"
    if os.path.exists(a.crosscheck):
        assert open(a.crosscheck, "rb").read()[:CODE_REGION_END] == base[:CODE_REGION_END], "dumps disagree"
    if hashlib.sha256(base[:CODE_REGION_END]).hexdigest() != STOCK_CODE_SHA256:
        sys.exit("ABORT: base dump is not the supported stock firmware")

    stamp = a.stamp or dt.datetime.now().strftime("%Y%m%d%H%M")
    assert len(stamp) == 12 and stamp.isdigit() and stamp.encode() != STOCK_STAMP
    looks = json.load(open(a.looks))["looks"]
    validate_looks(looks)

    os.makedirs(a.out, exist_ok=True)
    img = Image(base)
    info = patch(img, looks, stamp, a.out)

    fx_pkg, fx_name = package(img.b, base, stamp)
    rs = (dt.datetime.strptime(stamp, "%Y%m%d%H%M") + dt.timedelta(minutes=1)).strftime("%Y%m%d%H%M")
    st_pkg, st_name = package(base, base, rs)
    for sub in ("fx", "stock_restore"):
        os.makedirs(os.path.join(a.out, sub), exist_ok=True)
    open(os.path.join(a.out, "fx", fx_name), "wb").write(fx_pkg)
    open(os.path.join(a.out, "stock_restore", st_name), "wb").write(st_pkg)
    open(os.path.join(a.out, "charmera_fx_full.bin"), "wb").write(bytes(img.b))

    print("patched regions:")
    for ram, n, what in img.log:
        print("   RAM %06X  file %06X  %5d B  %s" % (ram, ram + HDR, n, what))
    print("version string : %s" % info["version"])
    print("FX package     : %s/fx/%s" % (a.out, fx_name))
    print("stock restore  : %s/stock_restore/%s" % (a.out, st_name))
    json.dump({"stamp": stamp, "fx": fx_name, "restore": st_name, "looks": looks, **info},
              open(os.path.join(a.out, "build_info.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
