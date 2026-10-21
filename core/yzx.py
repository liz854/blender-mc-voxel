"""唯一的 YZX 索引函数 + bit packer。

Litematica / Sponge 都用 YZX 顺序存方块：
    index = x + z * sx + y * sx * sz

bit packing 是「跨 long 紧密排布」，不是按 long 边界对齐：
    bits = max(2, ceil(log2(palette_size)))
    p = i * bits ; word = p >> 6 ; off = p & 63
"""

from __future__ import annotations

import numpy as np


def yzx_index(x: int, y: int, z: int, sx: int, sy: int, sz: int) -> int:
    """YZX 索引。参数顺序固定为 x, y, z, sx, sy, sz。"""
    if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
        raise IndexError(f"({x},{y},{z}) 越界，尺寸 {(sx, sy, sz)}")
    return int(x) + int(z) * int(sx) + int(y) * int(sx) * int(sz)


def yzx_to_xyz(index: int, sx: int, sy: int, sz: int):
    """YZX 索引反解成 (x, y, z)。"""
    index = int(index)
    y, rem = divmod(index, sx * sz)
    z, x = divmod(rem, sx)
    if y >= sy:
        raise IndexError(f"索引 {index} 超出尺寸 {(sx, sy, sz)}")
    return x, y, z


def bits_for(palette_size: int) -> int:
    """palette 需要的最少 bit 数，最小 2。"""
    if palette_size <= 1:
        return 2
    return max(2, int((palette_size - 1).bit_length()))


def pack_bits(indices, bits: int):
    """把索引数组紧密打进 int64 long 数组。返回 np.uint64 视图安全的 int64 数组。

    返回的是 list[int]（有符号 64 位语义，写 NBT LongArray 用）。
    """
    idx = np.asarray(indices, dtype=np.uint64).reshape(-1)
    bits = int(bits)
    if bits < 2:
        raise ValueError("bits 最小是 2")
    n = idx.size
    if n == 0:
        return []
    n_words = (n * bits + 63) // 64
    words = np.zeros(n_words, dtype=np.uint64)

    pos = np.arange(n, dtype=np.uint64) * np.uint64(bits)
    word = pos >> np.uint64(6)
    off = pos & np.uint64(63)

    for b in range(bits):
        bit = (idx >> np.uint64(b)) & np.uint64(1)
        sh = off + np.uint64(b)
        low = sh < np.uint64(64)
        if low.any():
            sel = np.nonzero(low)[0]
            np.bitwise_or.at(words, word[sel], bit[sel] << sh[sel])
        high = ~low
        if high.any():
            sel = np.nonzero(high)[0]
            tgt = word[sel] + np.uint64(1)
            np.bitwise_or.at(words, tgt, bit[sel] << (sh[sel] - np.uint64(64)))

    # 转成有符号 64 位（NBT Long 是有符号的）
    return [int(v) for v in words.astype(np.int64)]


def unpack_bits(longs, count: int, bits: int):
    """pack_bits 的逆过程，返回 np.int64 索引数组。"""
    arr = np.asarray(longs, dtype=np.int64).astype(np.uint64).reshape(-1)
    count = int(count)
    bits = int(bits)
    if count == 0:
        return np.zeros(0, dtype=np.int64)

    pos = np.arange(count, dtype=np.uint64) * np.uint64(bits)
    word = pos >> np.uint64(6)
    off = pos & np.uint64(63)

    out = np.zeros(count, dtype=np.uint64)
    need_words = int(word.max()) + 1
    if arr.size < need_words:
        raise ValueError(f"LongArray 长度不足：需要 {need_words}，实际 {arr.size}")

    for b in range(bits):
        sh = off + np.uint64(b)
        vals = np.zeros(count, dtype=np.uint64)
        low = sh < np.uint64(64)
        if low.any():
            sel = np.nonzero(low)[0]
            vals[sel] = (arr[word[sel]] >> sh[sel]) & np.uint64(1)
        high = ~low
        if high.any():
            sel = np.nonzero(high)[0]
            vals[sel] = (arr[word[sel] + 1] >> (sh[sel] - np.uint64(64))) & np.uint64(1)
        out |= vals << np.uint64(b)

    return out.astype(np.int64)


def mask_to_flat(mask):
    """(sx,sy,sz)[x,y,z] 三维数组 -> YZX 展平（x 最快，其次 z，最后 y）。"""
    import numpy as np

    a = np.asarray(mask)
    sx, sy, sz = a.shape
    return a.transpose(1, 2, 0).reshape(-1)


def flat_to_mask(flat, size):
    """YZX 展平 -> (sx,sy,sz)[x,y,z] 三维数组。"""
    import numpy as np

    sx, sy, sz = (int(v) for v in size)
    a = np.asarray(flat).reshape(sy, sz, sx)
    return a.transpose(2, 0, 1)
