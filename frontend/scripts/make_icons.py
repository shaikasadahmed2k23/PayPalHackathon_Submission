"""Generates the PWA PNG icons (no dependencies): python scripts/make_icons.py  (run from frontend/).
Mark: a blue dot joined by a curved leash to a blue ring, on the app light grey."""
import math
import struct
import zlib
from pathlib import Path

BG, LIME = (245, 247, 250), (0, 112, 224)  # app background, accent blue
OUT = Path(__file__).resolve().parent.parent / "public"


def bezier(p0, p1, p2, n=60):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(n + 1))]


def shape(scale: float):
    """Return coverage(x, y) in 0..1 over the unit square; scale shrinks the mark toward the centre."""
    s = lambda v: 0.5 + (v - 0.5) * scale  # noqa: E731
    dot, ring = (s(0.30), s(0.70)), (s(0.70), s(0.30))
    curve = bezier((dot[0], dot[1]), (s(0.28), s(0.30)), (ring[0], ring[1]))
    rd, rr, rw, cw = 0.115 * scale, 0.095 * scale, 0.045 * scale, 0.03 * scale

    def covered(x, y):
        if math.hypot(x - dot[0], y - dot[1]) <= rd:
            return True
        if abs(math.hypot(x - ring[0], y - ring[1]) - rr) <= rw / 2:
            return True
        return any(math.hypot(x - cx, y - cy) <= cw for cx, cy in curve)

    return covered


def png(size: int, scale: float) -> bytes:
    covered, ss = shape(scale), 3
    rows = bytearray()
    for py in range(size):
        rows.append(0)
        for px in range(size):
            hits = sum(covered((px + (i + .5) / ss) / size, (py + (j + .5) / ss) / size)
                       for i in range(ss) for j in range(ss))
            a = hits / (ss * ss)
            rows += bytes(round(BG[c] * (1 - a) + LIME[c] * a) for c in range(3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


for name, size, scale in [("icon-192.png", 192, 1.0), ("icon-512.png", 512, 1.0),
                          ("icon-maskable-192.png", 192, 0.72), ("icon-maskable-512.png", 512, 0.72),
                          ("apple-touch-icon.png", 180, 0.9)]:
    (OUT / name).write_bytes(png(size, scale))
    print("wrote", name)
