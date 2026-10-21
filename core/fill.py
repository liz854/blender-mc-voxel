"""内部填充：从边界 6-连通 flood fill 标记「外部空气」，solid = ~outside。

用 numpy 迭代膨胀，不用 Python deque。
"""

from __future__ import annotations

import numpy as np


def _spread(outside: np.ndarray, air: np.ndarray) -> np.ndarray:
    """把 outside 沿 6 个方向扩散一格（只能进空气）。"""
    nxt = outside.copy()
    nxt[1:, :, :] |= outside[:-1, :, :] & air[1:, :, :]
    nxt[:-1, :, :] |= outside[1:, :, :] & air[:-1, :, :]
    nxt[:, 1:, :] |= outside[:, :-1, :] & air[:, 1:, :]
    nxt[:, :-1, :] |= outside[:, 1:, :] & air[:, :-1, :]
    nxt[:, :, 1:] |= outside[:, :, :-1] & air[:, :, 1:]
    nxt[:, :, :-1] |= outside[:, :, 1:] & air[:, :, :-1]
    return nxt


def outside_mask(solid: np.ndarray, max_iter: int = None) -> np.ndarray:
    """可达外界的空气掩码。"""
    solid = np.asarray(solid, dtype=bool)
    air = ~solid
    outside = np.zeros_like(solid)

    # 种子：6 个外表面上的空气
    outside[0, :, :] |= air[0, :, :]
    outside[-1, :, :] |= air[-1, :, :]
    outside[:, 0, :] |= air[:, 0, :]
    outside[:, -1, :] |= air[:, -1, :]
    outside[:, :, 0] |= air[:, :, 0]
    outside[:, :, -1] |= air[:, :, -1]

    limit = max_iter if max_iter is not None else int(sum(solid.shape)) + 8
    for _ in range(limit):
        nxt = _spread(outside, air)
        if np.array_equal(nxt, outside):
            break
        outside = nxt
    return outside


def fill_interior(solid: np.ndarray, max_iter: int = None) -> np.ndarray:
    """实心化：表面 + 被包住的内部空气。开口的壳保持空心。"""
    solid = np.asarray(solid, dtype=bool)
    if not solid.any():
        return np.zeros_like(solid)
    return ~outside_mask(solid, max_iter=max_iter)


_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def flood_colors(mask: np.ndarray, rgb: np.ndarray, max_iter: int = None):
    """把表面颜色扩散到被填充的内部体素。

    mask : (sx,sy,sz) bool，最终实心区域
    rgb  : (sx,sy,sz,3)，未命中处为 NaN
    返回补全颜色后的 rgb（仍未取到色的地方用 (127,127,127)）。

    只更新"贴着已定色区域"的那一薄层：每轮用 bool 膨胀找边界环，
    再只对环上的格子取 6 邻居平均。老写法每轮 np.roll 整个颜色网格
    （200³ 时 186 MB × 6 次 × 100 轮），实测 45 秒；现在是 2 秒级。
    """
    mask = np.asarray(mask, dtype=bool)
    out = np.array(rgb, dtype=np.float64, copy=True)
    if not mask.any():
        out[:] = 127.0
        return out

    known = ~np.isnan(out[..., 0]) & mask
    if not known.any():
        out[:] = 127.0
        return out

    sx, sy, sz = mask.shape
    limit = max_iter if max_iter is not None else int(sum(mask.shape)) + 8

    for _ in range(limit):
        target = mask & ~known
        if not target.any():
            break

        # 边界环 = 还没定色、但六个邻居里至少有一个已定色
        nb = np.zeros_like(known)
        nb[1:, :, :] |= known[:-1, :, :]
        nb[:-1, :, :] |= known[1:, :, :]
        nb[:, 1:, :] |= known[:, :-1, :]
        nb[:, :-1, :] |= known[:, 1:, :]
        nb[:, :, 1:] |= known[:, :, :-1]
        nb[:, :, :-1] |= known[:, :, 1:]
        ring = target & nb
        if not ring.any():
            break

        ix, iy, iz = np.nonzero(ring)
        acc = np.zeros((ix.size, 3), dtype=np.float64)
        cnt = np.zeros(ix.size, dtype=np.float64)

        for dx, dy, dz in _DIRS:
            nx = ix + dx
            ny = iy + dy
            nz = iz + dz
            inside = (
                (nx >= 0) & (nx < sx) & (ny >= 0) & (ny < sy) & (nz >= 0) & (nz < sz)
            )
            if not inside.any():
                continue
            nx = np.clip(nx, 0, sx - 1)
            ny = np.clip(ny, 0, sy - 1)
            nz = np.clip(nz, 0, sz - 1)
            take = inside & known[nx, ny, nz]
            if not take.any():
                continue
            acc[take] += out[nx[take], ny[take], nz[take]]
            cnt[take] += 1.0

        got = cnt > 0
        if not got.any():
            break
        gx, gy, gz = ix[got], iy[got], iz[got]
        out[gx, gy, gz] = acc[got] / cnt[got][:, None]
        known[gx, gy, gz] = True

    out[np.isnan(out[..., 0])] = 127.0
    return out
