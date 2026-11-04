"""Decode the Charmera frame overlays (F_<n>_<strip>.GPZP) to RGBA PNGs.

Strip format (from the compositor at RAM 0xEBA4 / 0x140C8): 800x200 'GP420':
for each pair of rows k: line 3k = Y of even row (800 B), line 3k+1 = chroma
(800 B, interleaved per 2 px), line 3k+2 = Y of odd row. Transparent where
both lumas of a 2-px group are 0x8C (140).
"""
import sys, os
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
from gppack import parse_pack, gpzp_inflate

W, SH = 800, 200

def decode_strip(raw, uv_order="VU"):
    a = np.frombuffer(raw, np.uint8).reshape(SH // 2, 3, W)
    y = np.empty((SH, W), np.uint8)
    y[0::2] = a[:, 0]; y[1::2] = a[:, 2]
    c = a[:, 1].reshape(SH // 2, W // 2, 2)
    first, second = c[..., 0], c[..., 1]
    v, u = (first, second) if uv_order == "VU" else (second, first)
    u = np.repeat(np.repeat(u, 2, 1), 2, 0).astype(np.float32) - 128
    v = np.repeat(np.repeat(v, 2, 1), 2, 0).astype(np.float32) - 128
    yf = y.astype(np.float32)
    rgb = np.stack([yf + 1.402 * v, yf - 0.344 * u - 0.714 * v, yf + 1.772 * u], -1)
    key = (y.reshape(SH, W // 2, 2) == 0x8C).all(-1)
    alpha = np.where(np.repeat(key, 2, 1), 0, 255).astype(np.uint8)
    return np.clip(rgb, 0, 255).astype(np.uint8), alpha

def decode_frames(fw, uv_order="VU"):
    ents = {n: (o, s) for n, o, s in parse_pack(fw)}
    out = []
    for f in range(4):
        rgb, al = [], []
        for s in range(3):
            o, sz = ents["F_%d_%03dGPZP" % (f, s)]
            raw, _ = gpzp_inflate(fw[o:o + sz])
            r, a = decode_strip(raw, uv_order)
            rgb.append(r); al.append(a)
        out.append((np.vstack(rgb), np.vstack(al)))
    return out

if __name__ == "__main__":
    fw = open(sys.argv[1], "rb").read()
    od = sys.argv[2]; os.makedirs(od, exist_ok=True)
    for order in ("VU", "UV"):
        for i, (rgb, a) in enumerate(decode_frames(fw, order)):
            Image.fromarray(np.dstack([rgb, a]), "RGBA").save(os.path.join(od, "frame%d_%s.png" % (i, order)))
    print("ok")
