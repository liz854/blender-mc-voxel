"""体素化：三角形 vs 体素 AABB 的 16 轴 SAT（3 盒轴 + 1 法线 + 3 边法线 + 9 盒轴×边）。

唯一的技术核心。纯 numpy，禁止 scipy。

掩码约定：三维 bool 数组，形状 (sx, sy, sz)，下标顺序 [x, y, z]。
落盘前用 yzx.mask_to_flat() 转成 YZX 展平。

性能做法：三角形按"覆盖的体素块尺寸"分组，同组三角形堆成一个
(T, V) 张量一次算完。球体那种几万个三角形尺寸都差不多，分组后
Python 层的循环从"每个三角形一次"降到"每批一次"，实测快 6 倍以上。
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-9
_BATCH_CELLS = 200_000  # 单批 (三角形数 × 每三角形候选体素数) 上限


def _norm_size(size):
    if np.isscalar(size):
        s = int(size)
        return (s, s, s)
    s = tuple(int(v) for v in size)
    if len(s) != 3:
        raise ValueError(f"size 必须是 int 或三元组，收到 {size}")
    return s


def _separated(local, axis, radius):
    """local (T,V,3,3)，axis (T,V,3)。返回 (T,V) bool：沿该轴投影后是否分离。"""
    proj = np.einsum("tvij,tvj->tvi", local, axis)
    return (proj.min(axis=2) > radius) | (proj.max(axis=2) < -radius)


def _separated_all(local, radius_base):
    """16 轴 SAT，一次算完一整批。返回 (T,V) bool：是否被某根轴分离。

    标准三角形-AABB 的 SAT 一共 13 根轴：3 根盒轴、1 根三角形法线、
    9 根「盒轴 × 三角形边」。少测那 9 根的话，三角形的一条边从体素角落
    旁边斜着掠过时所有轴都投影重叠，会被误判成"相交"——结果就是尖顶和
    斜棱附近大面积膨胀（四棱锥的顶变成三角形、棱角糊成一片）。
    """
    p0 = local[:, :, 0, :]
    p1 = local[:, :, 1, :]
    p2 = local[:, :, 2, :]
    e0 = p1 - p0
    e1 = p2 - p1
    e2 = p0 - p2
    normal = np.cross(e0, e1)

    shape = local[:, :, 0, :].shape
    box_axes = [
        np.broadcast_to(np.array([1.0, 0.0, 0.0]), shape),
        np.broadcast_to(np.array([0.0, 1.0, 0.0]), shape),
        np.broadcast_to(np.array([0.0, 0.0, 1.0]), shape),
    ]
    axes = list(box_axes) + [
        normal,
        np.cross(e0, normal),
        np.cross(e1, normal),
        np.cross(e2, normal),
    ]
    # 盒轴 × 三角形边：9 根，缺了就出上面说的"棱角糊成一片"
    for box_axis in box_axes:
        for edge in (e0, e1, e2):
            axes.append(np.cross(box_axis, edge))

    separated = np.zeros(local.shape[:2], dtype=bool)
    for axis in axes:
        # axis 可能是 broadcast 出来的只读视图，先取具体数组再算模长
        ax = np.asarray(axis, dtype=np.float64)
        norm2 = np.einsum("tvi,tvi->tv", ax, ax)
        valid = norm2 > 1e-20
        if not valid.any():
            continue
        unit = ax / np.sqrt(np.where(valid, norm2, 1.0))[:, :, None]
        # 体素方盒在任意轴上的投影半径
        radius = radius_base * (
            np.abs(unit[:, :, 0]) + np.abs(unit[:, :, 1]) + np.abs(unit[:, :, 2])
        )
        separated |= valid & _separated(local, unit, radius + _EPS * radius_base)
    return separated


def voxelize_surface(verts, tris, size, origin=None, block_size=1.0, colors=None):
    """三角形表面体素化。

    参数
    ----
    verts  : (N,3) 顶点（Minecraft 坐标）
    tris   : (M,3) 顶点下标
    size   : int 或 (sx,sy,sz)，体素网格尺寸
    origin : 网格最小角（MC 坐标），默认取 verts 最小值
    block_size : 1 体素边长（米）
    colors : 可选 (N,3) 顶点颜色（0-255），给了就返回 (mask, rgb)

    返回
    ----
    mask : (sx,sy,sz) bool，[x,y,z]
    rgb  : 给了 colors 时返回 (sx,sy,sz,3) float，未命中处为 NaN
    """
    V = np.asarray(verts, dtype=np.float64)
    if V.ndim != 2 or V.shape[1] != 3:
        raise ValueError(f"verts 必须是 (N,3)，收到 {V.shape}")
    T = np.asarray(tris, dtype=np.int64)
    if T.ndim != 2 or T.shape[1] != 3:
        raise ValueError(f"tris 必须是 (M,3)，收到 {T.shape}")
    if T.size and (T.max() >= len(V) or T.min() < 0):
        raise IndexError("tris 里有越界顶点下标")

    size = _norm_size(size)
    block = float(block_size)
    if block <= 0:
        raise ValueError("block_size 必须为正")
    if origin is None:
        origin = V.min(axis=0) if len(V) else np.zeros(3)
    origin = np.asarray(origin, dtype=np.float64)

    mask = np.zeros(size, dtype=bool)
    want_color = colors is not None
    if want_color:
        C = np.asarray(colors, dtype=np.float64)
        if C.shape != V.shape:
            raise ValueError("colors 形状必须与 verts 一致")
        acc = np.zeros(size + (3,), dtype=np.float64)
        cnt = np.zeros(size, dtype=np.float64)

    if len(T) == 0:
        empty_rgb = np.full(size + (3,), np.nan)
        return (mask, empty_rgb) if want_color else mask

    P = V[T]  # (M,3,3)
    tri_min = P.min(axis=1)
    tri_max = P.max(axis=1)

    # 三角形覆盖的体素索引区间，外扩 1 格；SAT 会再剔除多余部分
    lo = np.floor((tri_min - origin) / block).astype(np.int64) - 1
    hi = np.floor((tri_max - origin) / block).astype(np.int64) + 1
    for a in range(3):
        lo[:, a] = np.clip(lo[:, a], 0, size[a] - 1)
        hi[:, a] = np.clip(hi[:, a], 0, size[a] - 1)

    extent = hi - lo + 1
    alive = np.all(extent > 0, axis=1)
    if not alive.any():
        if want_color:
            rgb = np.full(size + (3,), np.nan)
            return mask, rgb
        return mask

    radius_base = 0.5 * block

    # ---- 按覆盖尺寸分组：同尺寸的三角形能拼成一个张量 ----
    tsel_all = np.nonzero(alive)[0]
    ext_alive = extent[tsel_all]
    keys = (
        ext_alive[:, 0].astype(np.int64) * 10 ** 10
        + ext_alive[:, 1].astype(np.int64) * 10 ** 5
        + ext_alive[:, 2].astype(np.int64)
    )
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    group_edges = np.nonzero(np.diff(sorted_keys))[0] + 1
    groups = np.split(order, group_edges)

    for group in groups:
        tidx = tsel_all[group]
        ex, ey, ez = (int(v) for v in extent[tidx[0]])
        cells = ex * ey * ez

        # 同组三角形共用的体素偏移表
        dx, dy, dz = np.meshgrid(
            np.arange(ex), np.arange(ey), np.arange(ez), indexing="ij"
        )
        off = np.stack([dx.reshape(-1), dy.reshape(-1), dz.reshape(-1)], axis=1)
        off_i = off.astype(np.int64)
        off = off.astype(np.float64)

        # 一次丢多少三角形进去：控制 (T, V) 张量的大小
        per_batch = max(1, _BATCH_CELLS // max(cells, 1))
        for start in range(0, len(tidx), per_batch):
            batch = tidx[start : start + per_batch]
            base = origin + (lo[batch].astype(np.float64) + 0.5) * block  # (T,3)
            centers = base[:, None, :] + off[None, :, :] * block  # (T,V,3)
            local = P[batch][:, None, :, :] - centers[:, :, None, :]  # (T,V,3,3)

            separated = _separated_all(local, radius_base)
            hit = ~separated
            if not hit.any():
                continue

            bi, vi = np.nonzero(hit)
            vx = lo[batch[bi], 0] + off_i[vi, 0]
            vy = lo[batch[bi], 1] + off_i[vi, 1]
            vz = lo[batch[bi], 2] + off_i[vi, 2]

            mask[vx, vy, vz] = True
            if want_color:
                tri_color = C[T[batch[bi]]].mean(axis=1)  # (H,3)
                np.add.at(acc, (vx, vy, vz), tri_color)
                np.add.at(cnt, (vx, vy, vz), 1.0)

    if want_color:
        rgb = np.full(size + (3,), np.nan)
        nz = cnt > 0
        rgb[nz] = acc[nz] / cnt[nz][:, None]
        return mask, rgb
    return mask
