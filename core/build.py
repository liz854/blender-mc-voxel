"""从网格到 VoxelModel 的完整流水线（不碰 bpy，可脱离 Blender 跑）。"""

from __future__ import annotations

import numpy as np

from .fill import fill_interior, flood_colors, neighborhood_std
from .gravity import enforce_gravity_support
from .ir import BlockState, VoxelModel
from .palette import TEXTURE_BIN_CENTERS, texture_bin
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


def pack_rgb_keys(rgb_u8) -> np.ndarray:
    """(n,3) uint8 颜色 -> (n,) uint32 键（r<<16 | g<<8 | b）。

    用来替代 `np.unique(..., axis=0)`：三维唯一值要逐行比较/排序，
    400 万方块时要 3 秒；压成单整数后走一维 unique，只要几十毫秒。
    """
    a = np.asarray(rgb_u8, dtype=np.uint32)
    return (a[:, 0] << 16) | (a[:, 1] << 8) | a[:, 2]


def unpack_rgb_keys(keys) -> np.ndarray:
    """uint32 键 -> (n,3) uint8 颜色。"""
    k = np.asarray(keys, dtype=np.uint32)
    return np.stack(
        [((k >> 16) & 0xFF), ((k >> 8) & 0xFF), (k & 0xFF)], axis=1
    ).astype(np.uint8)


def texture_targets_by_color(uniq_keys, inverse, vox_std):
    """每种颜色一个目标粗糙度 = 该颜色所有体素 std 的 25% 分位。

    按体素直接取会把颜色分界处的体素判成"粗糙"（邻域横跨两种颜色，
    std 被抬高），墙色分界线就会长出花纹理斑点；同色区域内边界体素
    是少数，取低分位整体判"平"。

    uniq_keys : (K,) 唯一色键，只用来定返回长度
    inverse   : (n,) 每个体素所属色号
    vox_std   : (n,) 每个体素的邻域 std

    返回每色的目标 std，长度 K。
    """
    order = np.argsort(inverse, kind="stable")
    sorted_std = np.asarray(vox_std, dtype=np.float64)[order]
    counts = np.bincount(inverse, minlength=len(uniq_keys))
    edges = np.concatenate(([0], np.cumsum(counts)))
    target = np.empty(len(uniq_keys), dtype=np.float64)
    for ci in range(len(uniq_keys)):
        seg = sorted_std[edges[ci] : edges[ci + 1]]
        target[ci] = float(np.percentile(seg, 25)) if seg.size else 0.0
    return target


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
    texture_match: bool = True,
    max_voxels: int = 64_000_000,
) -> VoxelModel:
    """网格 -> VoxelModel。

    verts  : (N,3) Minecraft 坐标
    tris   : (M,3)
    colors : (N,3) 顶点色 0-255（可全 255）
    palette: core.palette.BlockPalette
    solid  : True 填充内部，False 只留外壳
    no_floating_gravity : 悬空的沙子/沙砾换成近似色，见 core/gravity.py
    texture_match : 色板带纹理 std 时，按局部颜色粗糙度匹配
                  （平的面配平纹理的方块）。色板没带 std 时自动不起作用。
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

    vox_u8 = np.clip(np.round(vox_rgb), 0, 255).astype(np.uint8)

    # 唯一色用 uint32 键做一维 unique（见 pack_rgb_keys 的注释）。
    uniq_keys, inverse = np.unique(pack_rgb_keys(vox_u8), return_inverse=True)
    uniq_rgb = unpack_rgb_keys(uniq_keys).astype(np.float64)

    # 匹配走精确 ΔE 而不是 32³ LUT：唯一色通常只有几十到几百个，
    # 精确算一遍是毫秒级，却完全消除了 LUT 把查询色吸附到格子中心
    # 带来的选色偏差（两个同色方块在 LUT 里可能投给第三个方块）。
    if texture_match and getattr(palette, "has_texture_info", False):
        vox_std = neighborhood_std(xs, ys, zs, rgb, size)
        target_std = texture_targets_by_color(uniq_keys, inverse, vox_std)
        bin_centers = np.asarray(TEXTURE_BIN_CENTERS)[
            [texture_bin(s) for s in target_std]
        ]
        matched = palette.nearest_batch(uniq_rgb, stds=bin_centers, exact=True)
    else:
        matched = palette.nearest_batch(uniq_rgb, exact=True)

    # 每个唯一色一个调色板下标，再靠 inverse 一次散射到全部体素。
    # 老写法逐体素做 Python 循环（400 万方块 = 400 万次字典查找 + tuple
    # 构造，实测 1.5-3 秒），是整条流水线里最大的一笔白开销。
    state_index = {}
    per_color = np.empty(len(uniq_keys), dtype=np.int64)
    for i, name in enumerate(matched):
        idx = state_index.get(name)
        if idx is None:
            idx = model.add_state(BlockState(name))
            state_index[name] = idx
        per_color[i] = idx
    out = per_color[inverse]

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
