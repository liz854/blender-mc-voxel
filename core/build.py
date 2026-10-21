"""从网格到 VoxelModel 的完整流水线（不碰 bpy，可脱离 Blender 跑）。"""

from __future__ import annotations

import numpy as np

from .fill import fill_interior, flood_colors
from .gravity import enforce_gravity_support
from .ir import BlockState, VoxelModel
from .voxelize import voxelize_surface
from .yzx import mask_to_flat


def grid_from_bounds(verts, block_size: float):
    """由顶点包围盒算出 origin 和网格尺寸。"""
    v = np.asarray(verts, dtype=np.float64)
    lo = v.min(axis=0)
    hi = v.max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    size = np.maximum(1, np.ceil(span / float(block_size)).astype(np.int64))
    return lo, tuple(int(s) for s in size)


def build_voxel_model(
    verts,
    tris,
    colors,
    block_size: float,
    palette,
    solid: bool = True,
    origin=None,
    size=None,
    progress=None,
    no_floating_gravity: bool = True,
    max_voxels: int = 64_000_000,
) -> VoxelModel:
    """网格 -> VoxelModel。

    verts  : (N,3) Minecraft 坐标
    tris   : (M,3)
    colors : (N,3) 顶点色 0-255（可全 255）
    palette: core.palette.BlockPalette
    solid  : True 填充内部，False 只留外壳
    no_floating_gravity : 悬空的沙子/沙砾换成近似色，见 core/gravity.py
    max_voxels : 网格体积上限（默认 6400 万格 ≈ 400³），超了直接报错而不是 OOM
    """
    verts = np.asarray(verts, dtype=np.float64)
    if origin is None or size is None:
        auto_origin, auto_size = grid_from_bounds(verts, block_size)
        origin = auto_origin if origin is None else np.asarray(origin, dtype=np.float64)
        size = auto_size if size is None else tuple(int(s) for s in size)

    # 内存守卫：体素网格是稠密的，体积 × ~57 字节才是峰值占用。
    # 512³ 要 6 GB 以上，与其 OOM 崩掉不如提前说清楚。
    volume = int(size[0]) * int(size[1]) * int(size[2])
    if volume > max_voxels:
        need_gb = volume * 57 / 2 ** 30
        raise ValueError(
            f"网格 {size[0]}x{size[1]}x{size[2]} = {volume:,} 格，超过上限 {max_voxels:,}"
            f"（约需 {need_gb:.1f} GB 内存）。调大方块尺寸，或把模型拆成几段分别导出。"
        )

    if progress:
        progress("体素化")

    mask, rgb = voxelize_surface(verts, tris, size, origin, block_size, colors=colors)

    if progress:
        progress("内部填充" if solid else "整理外壳")

    if solid:
        final = fill_interior(mask)
        rgb = flood_colors(final, rgb)
    else:
        final = mask
        rgb = np.where(np.isnan(rgb), 127.0, rgb)

    flat = mask_to_flat(final)
    model = VoxelModel(size)
    hit = np.nonzero(flat)[0]
    if hit.size == 0:
        return model

    # 只取命中格的颜色。老写法先把整个颜色网格 transpose + reshape
    # 复制一份（300³ 时 648 MB 白复制），换成三维花式索引省掉这一份。
    ys, zs, xs = np.unravel_index(hit, (size[1], size[2], size[0]))
    vox_rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64)[xs, ys, zs], nan=127.0)

    if progress:
        progress("匹配方块")

    uniq, inverse = np.unique(
        np.clip(np.round(vox_rgb), 0, 255).astype(np.uint8), axis=0, return_inverse=True
    )
    name_cache = {}
    for rgb_key, name in zip(uniq, palette.nearest_batch(uniq.astype(np.float64))):
        name_cache[tuple(int(v) for v in rgb_key)] = name

    key_idx = {}
    out = np.zeros(hit.size, dtype=np.int64)
    for local_i, inv in enumerate(inverse):
        rgb_key = tuple(int(v) for v in uniq[inv])
        name = name_cache[rgb_key]
        if name not in key_idx:
            key_idx[name] = model.add_state(BlockState(name))
        out[local_i] = key_idx[name]

    model.blocks[hit] = out

    if no_floating_gravity:
        if progress:
            progress("重力方块支撑检查")
        n = enforce_gravity_support(
            model,
            palette,
            report=lambda cnt, olds, news: print(
                f"[gravity] {cnt} 个悬空的重力方块换成 {', '.join(news)}"
                f"（原为 {', '.join(olds)}）"
            ),
        )
        if n and progress:
            progress(f"{n} 个悬空重力方块已替换")

    # 每个方块的平均色：mesh_export / 预览图都靠这张表上色。
    # 以前这里不设，model_to_mesh 就一路退回 (200,200,200) 灰，渲染出来全灰。
    model._colors = {
        st.name: (palette.color_of(st.name) or (200, 200, 200)) for st in model.palette
    }
    return model
