import struct
import zlib

import numpy as np
import pytest

from core import png


def chunk(ctype: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def make_png(width, height, rows, color_type=2, bit_depth=8, filters=None, palette=None):
    """rows: 每行的原始像素字节。filters 逐行指定 filter 类型。"""
    filters = filters or [0] * height
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = max(1, channels * bit_depth // 8)
    raw = bytearray()
    prev = bytes(len(rows[0]))
    for y, row in enumerate(rows):
        f = filters[y]
        out = bytearray(len(row))
        for i in range(len(row)):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 0:
                val = row[i]
            elif f == 1:
                val = row[i] - a
            elif f == 2:
                val = row[i] - b
            elif f == 3:
                val = row[i] - ((a + b) >> 1)
            else:
                val = row[i] - paeth(a, b, c)
            out[i] = val & 0xFF
        raw.append(f)
        raw += out
        prev = row

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    data = png.SIG + chunk(b"IHDR", ihdr)
    if palette is not None:
        data += chunk(b"PLTE", bytes(palette))
    data += chunk(b"IDAT", zlib.compress(bytes(raw)))
    data += chunk(b"IEND", b"")
    return data


def test_rgb_2x2():
    """手搓一张 2×2 纯色 PNG 字节流，验证解码结果。"""
    rows = [bytes([255, 0, 0] * 2), bytes([255, 0, 0] * 2)]
    data = make_png(2, 2, rows)
    w, h, rgba = png.decode(data)
    assert (w, h) == (2, 2)
    assert rgba.shape == (2, 2, 4)
    assert tuple(rgba[0, 0]) == (255, 0, 0, 255)
    assert png.average_color(rgba) == (255, 0, 0)


@pytest.mark.parametrize("filt", [0, 1, 2, 3, 4])
def test_all_filters(filt):
    rows = [
        bytes([10, 20, 30, 40, 50, 60]),
        bytes([70, 80, 90, 100, 110, 120]),
    ]
    data = make_png(2, 2, rows, filters=[0, filt])
    _, _, rgba = png.decode(data)
    assert tuple(rgba[0, 0]) == (10, 20, 30, 255)
    assert tuple(rgba[1, 1]) == (100, 110, 120, 255)


def test_rgba_alpha_average_skips_transparent():
    rows = [bytes([255, 0, 0, 255, 0, 0, 0, 0])]  # 一个不透明红，一个全透明
    data = make_png(2, 1, rows, color_type=6)
    _, _, rgba = png.decode(data)
    assert png.average_color(rgba) == (255, 0, 0)


def test_gray():
    data = make_png(1, 1, [bytes([128])], color_type=0)
    _, _, rgba = png.decode(data)
    assert tuple(rgba[0, 0]) == (128, 128, 128, 255)


def test_palette_bitdepth_4():
    """MC 的方块贴图经常是 4 bit 调色板。"""
    palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    # 4 像素一行，4 bit 打包：索引 0,1,2,3 -> 0x01, 0x23
    rows = [bytes([0x01, 0x23])]
    data = make_png(4, 1, rows, color_type=3, bit_depth=4, palette=palette)
    _, _, rgba = png.decode(data)
    assert tuple(rgba[0, 0]) == (255, 0, 0, 255)
    assert tuple(rgba[0, 1]) == (0, 255, 0, 255)
    assert tuple(rgba[0, 3]) == (255, 255, 255, 255)


def test_bad_signature():
    with pytest.raises(png.PNGError):
        png.decode(b"not a png at all")


def test_interlaced_rejected():
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 1)
    data = png.SIG + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    with pytest.raises(png.PNGError):
        png.decode(data)
