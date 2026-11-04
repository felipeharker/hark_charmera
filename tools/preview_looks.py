"""Approximate preview of the colour-processor looks on a synthetic test chart.

Model (inferred from the stock Warm/Cool/B&W values, not measured on hardware):
    Cb' = (Cb-128) * u_gain/32 + u_offset + 128,   Cr' likewise,   Y unchanged.
"""
import json, sys
import numpy as np
from PIL import Image, ImageDraw

def chart(w=360, h=240):
    img = np.zeros((h, w, 3), np.float32)
    x = np.linspace(0, 1, w)
    img[:60] = x[None, :, None] * 255                                   # gray ramp
    hue = np.linspace(0, 6, w)
    rgb = np.clip(np.stack([np.abs(hue - 3) - 1, 2 - np.abs(hue - 2), 2 - np.abs(hue - 4)], -1), 0, 1)
    for i, v in enumerate(np.linspace(1.0, 0.35, 3)):                    # hue bands at 3 brightness levels
        img[60 + i * 40:100 + i * 40] = rgb[None] * 255 * v
    pal = [(115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67), (133, 128, 177), (103, 189, 170),
           (214, 126, 44), (80, 91, 166), (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46)]
    pw = w // 12
    for i, c in enumerate(pal):                                          # skin/sky/foliage-like patches
        img[180:240, i * pw:(i + 1) * pw] = c
    return img

def apply(img, uo, vo, ug, vg):
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = (b - y) * 0.564; cr = (r - y) * 0.713
    cb = cb * ug / 32 + uo; cr = cr * vg / 32 + vo
    out = np.stack([y + 1.403 * cr, y - 0.344 * cb - 0.714 * cr, y + 1.773 * cb], -1)
    return np.clip(out, 0, 255).astype(np.uint8)

looks = [("Standard", 0, 0, 32, 32), ("Warm", -20, 20, 5, 20), ("Cool", 20, -20, 20, 5), ("B&W", 0, 0, 0, 0)]
looks += [(l["name"], l["u_offset"], l["v_offset"], l["u_gain"], l["v_gain"]) for l in json.load(open(sys.argv[1]))["looks"]]
src = chart()
W, H = src.shape[1], src.shape[0]
sheet = Image.new("RGB", (4 * (W + 10) + 10, 2 * (H + 30) + 10), (30, 30, 30))
d = ImageDraw.Draw(sheet)
for i, (name, uo, vo, ug, vg) in enumerate(looks):
    x, y = 10 + (i % 4) * (W + 10), 10 + (i // 4) * (H + 30)
    sheet.paste(Image.fromarray(apply(src, uo, vo, ug, vg)), (x, y + 20))
    d.text((x, y + 4), "%s  (%d, %d, %d, %d)%s" % (name, uo, vo, ug, vg, "  NEW" if i >= 4 else ""), fill=(255, 255, 255))
sheet.save(sys.argv[2])
