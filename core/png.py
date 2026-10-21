"""最小 PNG 解码器：只依赖 zlib + struct。

支持：color type 0/2/4/6，bit depth 8，非隔行。
反 filter：None / Sub / Up / Average / Paeth。
返回 (w, h, np.uint8[h, w, 4])。
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

SIG = b"\x89PNG\r\n\x1a\n"


class PNGError(ValueError):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw: bytes, width: int, height: int, bpp: int, stride: int) -> np.ndarray:
    out = np.zeros((height, stride), dtype=np.uint8)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise PNGError("IDAT 数据不足")
        ftype = raw[pos]
        pos += 1
        line = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=pos).astype(np.int32)
        pos += stride
        prev = out[y - 1].astype(np.int32) if y > 0 else np.zeros(stride, dtype=np.int32)

        if ftype == 0:
            cur = line
        elif ftype == 1:  # Sub
            cur = line.copy()
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            cur = (line + prev) & 0xFF
        elif ftype == 3:  # Average
            cur = line.copy()
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            cur = line.copy()
            for i in range(stride):
                left = cur[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                cur[i] = (cur[i] + _paeth(left, prev[i], upleft)) & 0xFF
        else:
            raise PNGError(f"未知 filter 类型 {ftype}")
        out[y] = cur.astype(np.uint8)
    return out


def _expand_bits(pixels: np.ndarray, width: int, bit_depth: int, stride: int) -> np.ndarray:
    """把 1/2/4 bit 的打包行展开成 (h, w) 的整数值。"""
    h = pixels.shape[0]
    per_byte = 8 // bit_depth
    out = np.zeros((h, width), dtype=np.uint8)
    mask = (1 << bit_depth) - 1
    for y in range(h):
        row = pixels[y]
        vals = []
        for b in range(stride):
            byte = int(row[b])
            for k in range(per_byte):
                shift = 8 - bit_depth * (k + 1)
                vals.append((byte >> shift) & mask)
        out[y] = np.array(vals[:width], dtype=np.uint8)
    return out


def decode(data: bytes):
    """解码 PNG 字节串 -> (w, h, rgba uint8[h,w,4])。"""
    if not data.startswith(SIG):
        raise PNGError("不是 PNG 文件")

    pos = len(SIG)
    width = height = None
    bit_depth = color_type = None
    idat = bytearray()
    palette = None
    trns = None

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length

        if ctype == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if comp != 0 or filt != 0:
                raise PNGError("不支持的压缩/过滤方法")
            if interlace != 0:
                raise PNGError("不支持隔行 PNG")
            if bit_depth not in (1, 2, 4, 8):
                raise PNGError(f"只支持 bit depth 1/2/4/8，收到 {bit_depth}")
        elif ctype == b"PLTE":
            palette = np.frombuffer(body, dtype=np.uint8).reshape(-1, 3)
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break

    if width is None:
        raise PNGError("缺少 IHDR")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PNGError(f"不支持 color type {color_type}")

    raw = zlib.decompress(bytes(idat))
    if bit_depth == 8:
        bpp = channels
        stride = width * channels
    elif bit_depth in (1, 2, 4):
        # 调色板 / 灰度才允许位深 < 8（MC 的方块贴图大量是 4 bit 调色板）
        if color_type not in (0, 3):
            raise PNGError(f"bit depth {bit_depth} 只支持灰度或调色板")
        bpp = 1
        stride = (width * bit_depth + 7) // 8
    else:
        raise PNGError(f"不支持 bit depth {bit_depth}")

    pixels = _unfilter(raw, width, height, bpp, stride)
    if bit_depth < 8:
        pixels = _expand_bits(pixels, width, bit_depth, stride)
    elif color_type in (0, 3):
        pixels = pixels.reshape(height, width)

    if bit_depth == 8 and color_type in (2, 4, 6):
        pixels = pixels.reshape(height, width, channels)

    if color_type == 0:
        if bit_depth < 8:
            scale = 255 // ((1 << bit_depth) - 1)
            pixels = (pixels * scale).astype(np.uint8)
        gray = pixels[:, :, None]
        rgba = np.concatenate([gray, gray, gray, np.full_like(gray, 255)], axis=2)
    elif color_type == 4:
        gray = pixels[:, :, 0:1]
        rgba = np.concatenate([gray, gray, gray, pixels[:, :, 1:2]], axis=2)
    elif color_type == 2:
        rgba = np.concatenate(
            [pixels, np.full((height, width, 1), 255, np.uint8)], axis=2
        )
    elif color_type == 6:
        rgba = pixels
    else:  # color type 3，调色板
        if palette is None:
            raise PNGError("调色板 PNG 缺少 PLTE")
        idx = pixels.astype(np.int64)
        if idx.max() >= len(palette):
            raise PNGError("调色板索引越界")
        rgb = palette[idx]
        alpha = np.full((height, width, 1), 255, np.uint8)
        if trns:
            alpha_map = np.frombuffer(trns, dtype=np.uint8)
            alpha = np.where(
                idx < len(alpha_map), alpha_map[np.clip(idx, 0, max(0, len(alpha_map) - 1))], 255
            )[..., None].astype(np.uint8)
        rgba = np.concatenate([rgb, alpha], axis=2)

    return width, height, rgba.astype(np.uint8)


def average_color(rgba, alpha_cut: int = 128):
    """平均色，跳过 alpha < 128 的像素。返回 (r,g,b) 或 None。"""
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim == 2:
        arr = arr.reshape(-1, 1, arr.shape[-1])
    flat = arr.reshape(-1, arr.shape[-1])
    mask = flat[:, 3] >= alpha_cut
    if not mask.any():
        return None
    rgb = flat[mask, :3].astype(np.float64).mean(axis=0)
    return tuple(int(round(v)) for v in rgb)


def color_stats(rgba, alpha_cut: int = 128):
    """(平均色, 颜色标准差) —— 标准差用来判断纹理有多花。

    纯色方块（白混凝土、石头）std 小；书架、工作台那种黑白相间的 std 很大。
    做体素配色时只想要前者。
    """
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim == 2:
        arr = arr.reshape(-1, 1, arr.shape[-1])
    flat = arr.reshape(-1, arr.shape[-1])
    mask = flat[:, 3] >= alpha_cut
    if not mask.any():
        return None
    px = flat[mask, :3].astype(np.float64)
    mean = px.mean(axis=0)
    std = float(px.std(axis=0).mean())
    return (tuple(int(round(v)) for v in mean), std)
