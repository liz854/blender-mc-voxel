"""测试公共工具：几何体构造。"""

from __future__ import annotations

import numpy as np


def box_mesh(lo=(0, 0, 0), hi=(10, 10, 10), skip_faces=()):
    """轴对齐盒子，返回 (verts (8,3), tris (12,3))。skip_faces 里填 +x/-x/+y/-y/+z/-z。"""
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    verts = np.array(
        [
            [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
        ],
        dtype=np.float64,
    )
    faces = {
        "-z": (0, 3, 2, 1),
        "+z": (4, 5, 6, 7),
        "-y": (0, 1, 5, 4),
        "+y": (3, 7, 6, 2),
        "-x": (0, 4, 7, 3),
        "+x": (1, 2, 6, 5),
    }
    tris = []
    for name, (a, b, c, d) in faces.items():
        if name in skip_faces:
            continue
        tris.append((a, b, c))
        tris.append((a, c, d))
    return verts, np.asarray(tris, dtype=np.int64)


def plane_mesh(size=10.0):
    """y=0 的一张平面（法线 -y 方向无所谓）。"""
    verts = np.array(
        [[0, 0, 0], [size, 0, 0], [size, 0, size], [0, 0, size]], dtype=np.float64
    )
    tris = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    return verts, tris


def white_colors(n):
    return np.full((n, 3), 255.0)
