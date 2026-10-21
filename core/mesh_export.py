"""反向：VoxelModel -> 外表面网格。

6 个方向各做一次 solid & ~neighbor 得到暴露面，顶点去重。
法线朝外（每个方向 4 个角点顺序保证叉积朝外）。
"""

from __future__ import annotations

import numpy as np

from .ir import VoxelModel

# 每个方向：(法线, 4 个角的偏移，逆时针从外面看)
_FACES = {
    (1, 0, 0): [(1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)],
    (-1, 0, 0): [(0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)],
    (0, 1, 0): [(0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)],
    (0, -1, 0): [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)],
    (0, 0, 1): [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
    (0, 0, -1): [(0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)],
}


def _solid_mask(model: VoxelModel):
    air = np.array([s.is_air for s in model.palette], dtype=bool)
    flat = ~air[model.blocks]
    sx, sy, sz = model.size
    return flat.reshape(sy, sz, sx).transpose(2, 0, 1)  # [x][y][z]


def exposed_faces(model: VoxelModel):
    """返回 [(x, y, z, 方向, 4 个角点), ...]。"""
    solid = _solid_mask(model)
    out = []
    for direction, corners in _FACES.items():
        dx, dy, dz = direction
        shifted = np.zeros_like(solid)
        xs = slice(max(0, -dx), solid.shape[0] - max(0, dx))
        ys = slice(max(0, -dy), solid.shape[1] - max(0, dy))
        zs = slice(max(0, -dz), solid.shape[2] - max(0, dz))
        src = solid[
            slice(max(0, dx), solid.shape[0] + min(0, dx)),
            slice(max(0, dy), solid.shape[1] + min(0, dy)),
            slice(max(0, dz), solid.shape[2] + min(0, dz)),
        ]
        shifted[xs, ys, zs] = src
        exposed = solid & ~shifted
        for x, y, z in zip(*np.nonzero(exposed)):
            out.append((int(x), int(y), int(z), direction, corners))
    return out


def model_to_mesh(model: VoxelModel):
    """返回 (verts float (N,3) MC 坐标, quads (M,4) 顶点下标, colors (N,3))。"""
    faces = exposed_faces(model)
    if not faces:
        return np.zeros((0, 3)), np.zeros((0, 4), dtype=np.int64), np.zeros((0, 3))

    vmap = {}
    verts = []
    quads = []
    for x, y, z, _direction, corners in faces:
        quad = []
        for cx, cy, cz in corners:
            key = (x + cx, y + cy, z + cz)
            idx = vmap.get(key)
            if idx is None:
                idx = len(verts)
                vmap[key] = idx
                verts.append(key)
            quad.append(idx)
        quads.append(quad)

    # 每个顶点取一个相邻方块的颜色
    air = np.array([s.is_air for s in model.palette], dtype=bool)
    sx, sy, sz = model.size
    palette_colors = getattr(model, "_colors", None)
    colors = np.full((len(verts), 3), 200.0)
    if palette_colors is not None:
        for (x, y, z, _d, corners) in faces:
            state = model.get(x, y, z)
            rgb = palette_colors.get(state.name, (200, 200, 200))
            for cx, cy, cz in corners:
                colors[vmap[(x + cx, y + cy, z + cz)]] = rgb

    return np.asarray(verts, dtype=np.float64), np.asarray(quads, dtype=np.int64), colors


def check_outward(verts, quads) -> bool:
    """所有面法线朝外（相对模型质心点乘 > 0）。"""
    v = np.asarray(verts, dtype=np.float64)
    q = np.asarray(quads, dtype=np.int64)
    if len(q) == 0:
        return True
    center = v.mean(axis=0)
    ok = True
    for face in q:
        p = v[face]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        if np.linalg.norm(n) < 1e-12:
            ok = False
            continue
        if np.dot(n, p.mean(axis=0) - center) <= 0:
            ok = False
    return ok
