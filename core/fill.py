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

    做法是"前沿扩散"：每轮只从**上一轮刚定色的那些体素**往外找一层，
    不再对整个网格做 6 次移位。老版本每轮都操作整张网格
    （200³ 时 8M 格 × 6 次移位 × 98 轮 ≈ 47 亿次操作，光这一步 3 秒），
    而前沿扩散的总工作量跟"体素数量"成正比，与体积无关。
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

    # 前沿：当前已定色的体素坐标。第一轮是全部表面体素。
    fx, fy, fz = np.nonzero(known)

    for _ in range(limit):
        if fx.size == 0:
            break

        # 1) 前沿的 6 邻居里，挑出"在实心区内、还没定色"的格子
        nx_parts, ny_parts, nz_parts = [], [], []
        for dx, dy, dz in _DIRS:
            nx = fx + dx
            ny = fy + dy
            nz = fz + dz
            ok = (
                (nx >= 0) & (nx < sx) & (ny >= 0) & (ny < sy) & (nz >= 0) & (nz < sz)
            )
            if not ok.any():
                continue
            nx_parts.append(nx[ok])
            ny_parts.append(ny[ok])
            nz_parts.append(nz[ok])
        if not nx_parts:
            break
        nx = np.concatenate(nx_parts)
        ny = np.concatenate(ny_parts)
        nz = np.concatenate(nz_parts)

        keep = mask[nx, ny, nz] & ~known[nx, ny, nz]
        if not keep.any():
            break
        nx, ny, nz = nx[keep], ny[keep], nz[keep]

        # 2) 同一个格子可能被多个前沿体素指向，去重
        flat = np.ravel_multi_index((nx, ny, nz), (sx, sy, sz))
        flat, first = np.unique(flat, return_index=True)
        nx, ny, nz = nx[first], ny[first], nz[first]

        # 3) 用这一圈格子的 6 邻居里"已定色"的平均色
        acc = np.zeros((nx.size, 3), dtype=np.float64)
        cnt = np.zeros(nx.size, dtype=np.float64)
        for dx, dy, dz in _DIRS:
            ax = nx + dx
            ay = ny + dy
            az = nz + dz
            ok = (
                (ax >= 0) & (ax < sx) & (ay >= 0) & (ay < sy) & (az >= 0) & (az < sz)
            )
            if not ok.any():
                continue
            ax = np.clip(ax, 0, sx - 1)
            ay = np.clip(ay, 0, sy - 1)
            az = np.clip(az, 0, sz - 1)
            take = ok & known[ax, ay, az]
            if not take.any():
                continue
            acc[take] += out[ax[take], ay[take], az[take]]
            cnt[take] += 1.0

        got = cnt > 0
        if not got.any():
            break
        gx, gy, gz = nx[got], ny[got], nz[got]
        out[gx, gy, gz] = acc[got] / cnt[got][:, None]
        known[gx, gy, gz] = True
        fx, fy, fz = gx, gy, gz

    out[np.isnan(out[..., 0])] = 127.0
    return out


def neighborhood_std(xs, ys, zs, rgb, size) -> np.ndarray:
    """每个命中体素在 3×3×3 邻域内的颜色标准差（各通道平均）。

    用来当"目标纹理粗糙度"：平涂的面颜色几乎不变 std≈0，
    渐变 / 噪点 / 花纹区域 std 明显更大。匹配时让平的面配平纹理的方块。

    xs/ys/zs : 命中体素的网格坐标（已由调用方 unravel 好的三个数组）
    rgb      : (sx,sy,sz,3) 颜色网格（flood_colors 之后的，全有效）
    size     : 网格尺寸 (sx,sy,sz)
    """
    sx, sy, sz = size
    xs = np.asarray(xs)
    ys = np.asarray(ys)
    zs = np.asarray(zs)
    n = xs.size
    acc = np.zeros((n, 3), dtype=np.float64)
    acc2 = np.zeros((n, 3), dtype=np.float64)
    cnt = np.zeros(n, dtype=np.float64)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                nx = np.clip(xs + dx, 0, sx - 1)
                ny = np.clip(ys + dy, 0, sy - 1)
                nz = np.clip(zs + dz, 0, sz - 1)
                v = rgb[nx, ny, nz]
                ok = np.isfinite(v[:, 0])
                if not ok.any():
                    continue
                vv = v[ok]
                acc[ok] += vv
                acc2[ok] += vv * vv
                cnt[ok] += 1.0
    std = np.zeros(n, dtype=np.float64)
    good = cnt > 0
    if good.any():
        mean = acc[good] / cnt[good][:, None]
        var = acc2[good] / cnt[good][:, None] - mean * mean
        std[good] = np.sqrt(np.clip(var, 0.0, None)).mean(axis=1)
    return std
