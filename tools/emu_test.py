#!/usr/bin/env python3
"""Execute stock and patched firmware code in a Unicorn ARM emulator.

  A. cdsp_mode(e): colour-processor parameters for every effect value
  B. Up/Down effect handlers: cycling, colour call, icons shown/hidden
  C. OSD renderer draws the new icon slots
  D. Post-capture pipeline: which effect paths run
  E. Button dispatch: Up/Down/OK go to the text editor only when it should take them
  F. Text editor walk-through driven through the real dispatch, viewfinder frames
     rendered after every step (build/text_editor_steps.png)
  G. Text stamped into a photo buffer (build/text_capture.png) + cost estimate
"""
import json
import os
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                               UC_ARM_REG_R5, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)
from PIL import Image, ImageDraw

HDR, LOADED, MEM = 0x200, 0x10AF30, 0x400000
STOP = 0x3FFFF0
EFFECT = 0x10FBC5
OSD_SHOW, OSD_HIDE, CDSP_MODE, SET_A, SET_B = 0x21BA0, 0x21C14, 0x2D368, 0x64958, 0x649B8
OSD_BITMAP, OSD_RENDER_RGB = 0x10FCD4, 0x13F78
CAPTURE = 0x14AB6C
# Key driver: Up = msg 0x7003 (stock: previous effect, 0x1DC24), Down = 0x7002 (next
# effect, 0x1DCB4), Play/OK = 0x7061 (stock: open playback via 0x1DEC0).
KEY_DONE, UP_HANDLER, DOWN_HANDLER, PLAY_STOCK = 0x1E0D4, 0x1DC24, 0x1DCB4, 0x1DEC0
LCD_W, LCD_H, FB = 160, 80, 0x1C0000
PHOTO = 0x200000
REGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
GLYPHS = None


class Emu:
    def __init__(self, image):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.mem_map(0, MEM)
        self.uc.mem_write(0, image[HDR:HDR + LOADED])
        self.calls = []
        self.count = 0
        self.stubs = {SET_A: "A", SET_B: "B", OSD_SHOW: "show", OSD_HIDE: "hide",
                      0xF0D98: "lcd_w", 0xF0DD4: "lcd_h", 0x9D9C: "nop", 0x60A04: "ok_first"}
        self.uc.hook_add(UC_HOOK_CODE, self._hook)

    def _hook(self, uc, addr, size, _):
        self.count += 1
        name = self.stubs.get(addr)
        if name is None:
            return
        self.calls.append((name, [uc.reg_read(r) for r in REGS]))
        ret = {"lcd_w": 320, "lcd_h": 240, "malloc": 0x1D0000}.get(name, 0)
        uc.reg_write(UC_ARM_REG_R0, ret)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def run(self, start, until=STOP, regs=None):
        self.calls, self.count = [], 0
        uc = self.uc
        uc.reg_write(UC_ARM_REG_SP, 0x1F0000)
        uc.reg_write(UC_ARM_REG_LR, STOP)
        for r, v in (regs or {}).items():
            uc.reg_write(r, v)
        uc.emu_start(start, until, count=50_000_000)
        return self.calls

    def run_until_any(self, start, stops, regs=None):
        """Run from start until PC hits one of `stops`; return the stop address."""
        hit = []

        def h(uc, addr, size, _):
            if addr in stops:
                hit.append(addr)
                uc.emu_stop()
        hh = self.uc.hook_add(UC_HOOK_CODE, h)
        try:
            self.run(start, regs=regs)
        finally:
            self.uc.hook_del(hh)
        return hit[0] if hit else None


def sb(v):
    v &= 0xFF
    return v - 256 if v > 127 else v


def cdsp_params(calls):
    out = {}
    for name, a in calls:
        if name in ("A", "B"):
            out[name] = (sb(a[0]), sb(a[1]), a[2] & 0xFF, a[3] & 0xFF)
    return out.get("A"), out.get("B")


def rgb565_img(raw, w, h):
    px = struct.unpack("<%dH" % (w * h), raw)
    im = Image.new("RGB", (w, h))
    im.putdata([((v >> 11) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31) for v in px])
    return im


def make_viewfinder():
    """Fake 160x80 RGB565 live view: black bars, gradient 'scene' in the 112x80 photo area."""
    px = []
    for y in range(LCD_H):
        for x in range(LCD_W):
            if 24 <= x < 136:
                r, g, b = 60 + (x - 24) * 120 // 112, 90 + y, 140 - y
            else:
                r = g = b = 0
            px.append(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
    return struct.pack("<%dH" % len(px), *px)


def make_photo(w, h):
    """GP420 photo buffer (Y line, UV line, Y line per row pair) with a smooth gradient."""
    out = bytearray()
    for k in range(h // 2):
        y0 = bytes((40 + (x * 150) // w + (2 * k * 40) // h) & 255 for x in range(w))
        y1 = bytes((40 + (x * 150) // w + ((2 * k + 1) * 40) // h) & 255 for x in range(w))
        uv = bytes((150 if i % 2 == 0 else 110) for i in range(w))
        out += y0 + uv + y1
    return bytes(out)


def gp420_to_img(buf, w, h):
    im = Image.new("RGB", (w, h))
    px = im.load()
    for k in range(h // 2):
        yl0 = buf[(3 * k) * w:(3 * k + 1) * w]
        uvl = buf[(3 * k + 1) * w:(3 * k + 2) * w]
        yl1 = buf[(3 * k + 2) * w:(3 * k + 3) * w]
        for x in range(w):
            u, v = uvl[x & ~1] - 128, uvl[(x & ~1) + 1] - 128
            for yy, Y in ((2 * k, yl0[x]), (2 * k + 1, yl1[x])):
                r, g, b = Y + 1.402 * v, Y - 0.344 * u - 0.714 * v, Y + 1.772 * u
                px[x, yy] = tuple(max(0, min(255, int(c))) for c in (r, g, b))
    return im


def main():
    stock = open(os.environ.get("CHARMERA_DUMP", "third_party/keychain-camera-plus/dumps/charmera_stock_latest.bin"), "rb").read()
    fx = open("build/charmera_fx_full.bin", "rb").read()
    info = json.load(open("build/build_info.json"))
    syms = {k: int(v, 16) for k, v in info["syms"].items()}
    looks = info["looks"]
    es, ef = Emu(stock), Emu(fx)
    ok = True
    FXSTATE = syms["fx"]
    FXINIT = bytes(fx[FXSTATE + HDR:FXSTATE + HDR + 24])   # initial editor state as stored in the image

    def reset_state():
        ef.uc.mem_write(FXSTATE, FXINIT)

    def check(cond, msg):
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        ok &= bool(cond)

    def state():
        b = ef.uc.mem_read(FXSTATE, 24)
        keys = ["editing", "step", "cur", "cand", "len", "color", "x", "y", "rot", "scale"]
        d = dict(zip(keys, b[:10]))
        d["text"] = list(b[12:12 + d["len"]])
        return d

    # ------------------------------------------------------------------ A
    print("A. cdsp_mode parameters")
    stock_modes = [cdsp_params(es.run(CDSP_MODE, regs={UC_ARM_REG_R0: m})) for m in range(4)]
    for e in range(17):
        got = cdsp_params(ef.run(CDSP_MODE, regs={UC_ARM_REG_R0: e}))
        if e <= 3:
            exp = stock_modes[e]
        elif 12 <= e <= 14:
            lk = looks[e - 12]
            p = (lk["u_offset"], lk["v_offset"], lk["u_gain"], lk["v_gain"])
            exp = (p, p)
        else:
            exp = stock_modes[0]
        check(got == exp, "effect %2d -> A=%s B=%s" % (e, got[0], got[1]))

    # ------------------------------------------------------------------ B
    print("\nB. Up/Down effect handlers (editor idle)")
    icon_map = [0] + list(range(0x60, 0x6B)) + [0x2A, 0x2B, 0x2C, 0x2D]
    all_icons = set(range(0x60, 0x6B)) | {0x2A, 0x2B, 0x2C, 0x2D}
    for side, start, step in (("up", UP_HANDLER, -1), ("down", DOWN_HANDLER, +1)):
        for s in range(16):
            ef.uc.mem_write(EFFECT, bytes([s]))
            calls = ef.run(start, until=0x1DCA4)
            new = ef.uc.mem_read(EFFECT, 1)[0]
            hidden = {a[i] & 0xFF for n, a in calls if n == "hide" for i in range(3)} - {0}
            shown = [a[0] & 0xFF for n, a in calls if n == "show"]
            exp_new = (s + step) % 16
            got_p = cdsp_params(calls)
            exp_p = cdsp_params(ef.run(CDSP_MODE, regs={UC_ARM_REG_R0: exp_new}))
            exp_show = [icon_map[exp_new]] if exp_new else []
            check(new == exp_new and got_p == exp_p and shown == exp_show and hidden == all_icons,
                  "%-4s %2d -> %2d  show=%s hid=%d" % (side, s, new, [hex(x) for x in shown], len(hidden)))

    # ------------------------------------------------------------------ C
    print("\nC. OSD renderer draws the new icon slots")
    tiles = []
    for iid in (0x62, 0x2A, 0x2B, 0x2C, 0x2D):
        bm = bytearray(16)
        bm[iid // 8] |= 1 << (iid % 8)
        ef.uc.mem_write(OSD_BITMAP, bytes(bm))
        ef.uc.mem_write(FB, b"\0" * (320 * 240 * 2))
        ef.run(OSD_RENDER_RGB, regs={UC_ARM_REG_R0: FB})
        fb = struct.unpack("<%dH" % (320 * 240), ef.uc.mem_read(FB, 320 * 240 * 2))
        xs = [i % 320 for i, v in enumerate(fb) if v]
        ys = [i // 320 for i, v in enumerate(fb) if v]
        box = (min(xs), min(ys), max(xs), max(ys)) if xs else None
        check(box is not None and box[1] >= 56 and box[2] <= 15 and box[3] <= 71, "icon %02X at %s" % (iid, box))
        tiles.append(rgb565_img(bytes(b for y in range(56, 72) for b in
                                      struct.pack("<16H", *fb[y * 320:y * 320 + 16])), 16, 16).resize((96, 96), Image.NEAREST))
    sheet = Image.new("RGB", (5 * 104 + 8, 104), (40, 40, 40))
    for i, t in enumerate(tiles):
        sheet.paste(t, (8 + i * 104, 4))
    sheet.save("build/emulated_icons.png")

    # ------------------------------------------------------------------ D
    print("\nD. Post-capture pipeline")
    for emu, label, rng in ((es, "stock", range(12)), (ef, "fx", range(16))):
        emu.stubs.update({0x6E3A0: "pixel_filter", 0x560: "free", 0x544: "malloc",
                          0xEC80: "frame_strip", 0x6C260: "tail"})
        for e in rng:
            emu.uc.mem_write(EFFECT, bytes([e]))
            emu.uc.mem_write(0x149430 + 0xC, b"\0\0\0\0")
            emu.uc.mem_write(CAPTURE, struct.pack("<6I", 0, 800, 600, 0, 0, 0))   # no buffer: stamp skipped
            calls = emu.run(0x6E414, until=0x6C260, regs={UC_ARM_REG_R0: 0})
            px = sum(1 for n, _ in calls if n == "pixel_filter")
            fr = sum(1 for n, _ in calls if n == "frame_strip")
            check(px == (1 if 8 <= e <= 11 else 0) and fr == (3 if 4 <= e <= 7 else 0),
                  "%-5s effect %2d: pixel x%d, frame strips x%d" % (label, e, px, fr))
        for k in ("6E3A0", "560", "544", "EC80", "6C260"):
            emu.stubs.pop(int(k, 16), None)

    # ------------------------------------------------------------------ E
    print("\nE. Button dispatch")
    starts = {"OK": (0x1DB44, 0x7061), "Up": (0x1D9D4, 0x7003), "Down": (0x1D9D4, 0x7002)}
    stops = {KEY_DONE: "editor", UP_HANDLER: "stock Up", DOWN_HANDLER: "stock Down", PLAY_STOCK: "stock OK"}

    def press(key, effect=None):
        if effect is not None:
            ef.uc.mem_write(EFFECT, bytes([effect]))
        start, msg = starts[key]
        hit = ef.run_until_any(start, set(stops), regs={UC_ARM_REG_R0: msg})
        return stops.get(hit, "none")

    reset_state()
    check(press("OK", 3) == "stock OK", "OK on B&W -> stock (playback)")
    check(bytes(ef.uc.mem_read(0x1D99C, 4)) == stock[HDR + 0x1D99C:HDR + 0x1D9A0], "message 0x7000 path is stock (not hooked)")
    check(press("Up", 15) == "stock Up", "Up on Text (editor closed) -> stock effect change")
    check(press("Down", 15) == "stock Down", "Down on Text (editor closed) -> stock effect change")
    check(press("OK", 15) == "editor" and state()["editing"] == 1, "OK on Text -> opens editor")
    check(press("Up", 15) == "editor" and state()["cand"] == 1, "Up in editor -> next letter (B)")
    check(press("Down", 15) == "editor" and state()["cand"] == 0, "Down in editor -> previous letter (A)")
    ef.uc.mem_write(FXSTATE, bytes(24))
    ef.uc.mem_write(FXSTATE + 6, bytes([10, 10, 0, 10]))
    ef.uc.mem_write(FXSTATE, b"\x01")             # editing flag left set, but effect changed
    check(press("Up", 5) == "stock Up" and state()["editing"] == 0, "stale editor flag cleared when effect is not Text")

    # ------------------------------------------------------------------ F
    print("\nF. Editor walk-through: type 'HI <3', yellow, move, rotate 45, size 200%")
    reset_state()
    check(state()["scale"] == 20 and state()["x"] == 10 and state()["len"] == 0,
          "power-on defaults: empty text, centred, size 200%")
    ef.uc.mem_write(EFFECT, bytes([15]))
    view = make_viewfinder()
    frames = []

    def snap(caption):
        ef.uc.mem_write(FB, view)
        ef.run(syms["fx_preview"], regs={UC_ARM_REG_R0: FB})
        frames.append((caption, rgb565_img(bytes(ef.uc.mem_read(FB, LCD_W * LCD_H * 2)), LCD_W, LCD_H)))
        return ef.count

    snap("Text selected (idle)")
    press("OK")
    snap("OK: editor, letter A")
    seq = [("H", 7), ("I", None), (" ", None), ("<3", None)]
    names = [g for g, _ in __import__("fx_tables").GLYPHS]
    for ch, _ in seq:
        target = names.index(ch)
        cur = state()["cand"]
        steps = (target - cur) % len(names)
        back = (cur - target) % len(names)
        if steps <= back:
            for _ in range(steps):
                press("Up")
        else:
            for _ in range(back):
                press("Down")
        snap("pick '%s'" % ch)
        press("OK")
    snap("next slot shows tick")
    press("OK")                                    # tick -> done typing
    st = state()
    check(st["len"] == 4 and st["step"] == 1 and [names[i] for i in st["text"]] == ["H", "I", " ", "<3"],
          "typed text = H I _ <3, now on colour step")
    press("Up")
    snap("colour: yellow")
    press("OK")
    for _ in range(4):
        press("Up")
    snap("X: 4 x Up")
    press("OK")
    for _ in range(3):
        press("Up")
    snap("Y: 3 x Up")
    press("OK")
    for _ in range(3):
        press("Up")
    snap("ROT: 3 x Up (45)")
    press("OK")
    for _ in range(5):
        press("Up")
    preview_cost = snap("SIZE: 5 x Up (250%)")
    press("OK")
    st = state()
    check(st["editing"] == 0 and st["color"] == 1 and st["x"] == 14 and st["y"] == 7 and st["rot"] == 3
          and st["scale"] == 25, "final setup: colour 1, X 70%%, Y 35%%, rot 45, size 250%% (%s)" % st)
    snap("done (editor closed)")
    check(press("Up", 15) == "stock Up", "after editing, Up changes effect again")
    ef.uc.mem_write(EFFECT, bytes([15]))
    check(preview_cost < 3_000_000, "viewfinder overlay cost ~%d instructions/frame" % preview_cost)

    cols = 4
    W, Hh = LCD_W * 2, LCD_H * 2
    sheet = Image.new("RGB", (cols * (W + 8) + 8, ((len(frames) + cols - 1) // cols) * (Hh + 22) + 8), (30, 30, 30))
    dr = ImageDraw.Draw(sheet)
    for i, (cap, im) in enumerate(frames):
        x, y = 8 + (i % cols) * (W + 8), 8 + (i // cols) * (Hh + 22)
        dr.text((x, y), cap, fill=(255, 255, 255))
        sheet.paste(im.resize((W, Hh), Image.NEAREST), (x, y + 14))
    sheet.save("build/text_editor_steps.png")
    os.makedirs("build/frames", exist_ok=True)
    for i, (cap, im) in enumerate(frames):
        im.save("build/frames/%02d.png" % i)          # native 160x80, for the site
    print("  (viewfinder frames: build/text_editor_steps.png)")

    # ------------------------------------------------------------------ G
    print("\nG. Stamp into an 800x600 photo")
    w, h = 800, 600
    photo = make_photo(w, h)
    ef.uc.mem_write(PHOTO, photo)
    ef.uc.mem_write(CAPTURE, struct.pack("<6I", 0, w, h, 0, 0, PHOTO))
    ef.run(syms["fx_capture"])
    cap_cost = ef.count
    out = bytes(ef.uc.mem_read(PHOTO, len(photo)))
    changed = sum(1 for a, b in zip(out, photo) if a != b)
    check(changed > 1000, "photo buffer modified (%d bytes changed)" % changed)
    check(out[len(photo):] == b"" and len(out) == len(photo), "writes stayed inside the buffer")
    check(cap_cost < 60_000_000, "capture stamp cost ~%d instructions" % cap_cost)
    gp420_to_img(out, w, h).save("build/text_capture.png")
    # guard band after buffer untouched
    ef.uc.mem_write(PHOTO + len(photo), b"\xAA" * 4096)
    ef.uc.mem_write(PHOTO, photo)
    for x, y, sc, r in ((20, 20, 40, 5), (0, 0, 40, 0), (20, 0, 1, 11)):
        ef.uc.mem_write(FXSTATE + 6, bytes([x, y, r, sc]))
        ef.run(syms["fx_capture"])
    check(bytes(ef.uc.mem_read(PHOTO + len(photo), 4096)) == b"\xAA" * 4096,
          "extreme position/size never writes past the buffer")
    print("  (stamped photo: build/text_capture.png)")

    print("\nRESULT: %s" % ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
