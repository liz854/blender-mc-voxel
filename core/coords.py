"""唯一的坐标转换入口。

Blender: Z-up 右手系 (x, y, z)
Minecraft: Y-up，X 东 / Z 南

    mc_x =  bl_x
    mc_y =  bl_z
    mc_z = -bl_y

全项目只允许通过本模块做坐标转换。
"""

from __future__ import annotations

import numpy as np


def blender_to_mc(v):
    """单个点：Blender -> Minecraft。"""
    x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    return (x, z, -y)


def mc_to_blender(v):
    """单个点：Minecraft -> Blender。"""
    x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    return (x, -z, y)


def blender_to_mc_array(verts):
    """(N, 3) 顶点数组：Blender -> Minecraft，返回新数组（float64）。"""
    v = np.asarray(verts, dtype=np.float64)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError(f"期望 (N,3) 顶点数组，收到 {v.shape}")
    out = np.empty_like(v)
    out[:, 0] = v[:, 0]
    out[:, 1] = v[:, 2]
    out[:, 2] = -v[:, 1]
    return out


def mc_to_blender_array(verts):
    """(N, 3) 顶点数组：Minecraft -> Blender。"""
    v = np.asarray(verts, dtype=np.float64)
    if v.ndim != 2 or v.shape[1] != 3:
        raise ValueError(f"期望 (N,3) 顶点数组，收到 {v.shape}")
    out = np.empty_like(v)
    out[:, 0] = v[:, 0]
    out[:, 1] = -v[:, 2]
    out[:, 2] = v[:, 1]
    return out
