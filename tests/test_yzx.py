import numpy as np
import pytest

from core.yzx import (
    bits_for,
    flat_to_mask,
    mask_to_flat,
    pack_bits,
    unpack_bits,
    yzx_index,
    yzx_to_xyz,
)


def test_index_formula():
    sx, sy, sz = 4, 5, 6
    assert yzx_index(0, 0, 0, sx, sy, sz) == 0
    assert yzx_index(1, 0, 0, sx, sy, sz) == 1
    assert yzx_index(0, 0, 1, sx, sy, sz) == sx
    assert yzx_index(0, 1, 0, sx, sy, sz) == sx * sz
    assert yzx_index(3, 4, 5, sx, sy, sz) == 3 + 5 * sx + 4 * sx * sz


def test_index_roundtrip():
    sx, sy, sz = 7, 5, 3
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                idx = yzx_index(x, y, z, sx, sy, sz)
                assert yzx_to_xyz(idx, sx, sy, sz) == (x, y, z)


def test_out_of_range():
    with pytest.raises(IndexError):
        yzx_index(4, 0, 0, 4, 4, 4)
    with pytest.raises(IndexError):
        yzx_index(0, -1, 0, 4, 4, 4)


def test_bits_for():
    assert bits_for(0) == 2
    assert bits_for(1) == 2
    assert bits_for(2) == 2
    assert bits_for(3) == 2
    assert bits_for(4) == 2
    assert bits_for(5) == 3
    assert bits_for(8) == 3
    assert bits_for(9) == 4
    assert bits_for(17) == 5


def test_bits_never_one():
    """bits 最小是 2，不是 1。"""
    assert bits_for(2) >= 2


@pytest.mark.parametrize("count,bits", [(1, 2), (7, 2), (8, 2), (33, 2), (100, 3), (1000, 5), (65, 7)])
def test_pack_roundtrip(count, bits):
    rng = np.random.default_rng(count + bits)
    idx = rng.integers(0, (1 << bits) - 1, size=count, dtype=np.int64)
    longs = pack_bits(idx, bits)
    assert len(longs) == (count * bits + 63) // 64
    back = unpack_bits(longs, count, bits)
    assert np.array_equal(back, idx)


def test_pack_across_long_boundary():
    """跨 long 紧密排布：7 bit × 10 个 = 70 bit -> 2 个 long。"""
    idx = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 0], dtype=np.int64)
    longs = pack_bits(idx, 7)
    assert len(longs) == 2
    assert np.array_equal(unpack_bits(longs, 10, 7), idx)


def test_pack_all_ones_no_negative_overflow():
    """移位按 64 位掩码处理，不能溢出成负数。"""
    idx = np.full(64, 3, dtype=np.int64)
    longs = pack_bits(idx, 2)
    back = unpack_bits(longs, 64, 2)
    assert np.array_equal(back, idx)
    assert all(isinstance(v, int) for v in longs)


def test_mask_flat_roundtrip():
    size = (4, 3, 5)
    mask = np.zeros(size, dtype=bool)
    mask[1, 2, 3] = True
    mask[0, 0, 0] = True
    flat = mask_to_flat(mask)
    assert flat.shape == (4 * 3 * 5,)
    # 检查映射符合 YZX
    x, y, z = 1, 2, 3
    assert flat[yzx_index(x, y, z, *size)]
    back = flat_to_mask(flat, size)
    assert np.array_equal(back, mask)
