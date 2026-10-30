"""从网格到 VoxelModel 的完整流水线（不碰 bpy，可脱离 Blender 跑）。"""

from __future__ import annotations

import numpy as np

from .fill import fill_interior, fill_interior_2d, flood_colors, neighborhood_std
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


def _apply_seal(mask, axis, seal_lo: bool, seal_hi: bool):
    """给分块后的截面补一层虚拟封盖，返回新的 mask。

    分段独立体素化时，切面上没有三角形，实心模型的内腔会被 flood fill
    判成"连通外界"从而留空。封盖只在**该截面上模型真占用的投影区域**
    （沿 axis 是否有任何体素）上补，不会凭空填满整面。
    """
    out = np.array(mask, dtype=bool, copy=True)
    # 截面投影 = 该截面上模型占用的区域。注意侧墙的投影是个"方框"，
    # 中间是空的——直接用方框封盖，内腔照样能从中间漏出去。所以要把
    # 投影里的孔洞填实（二维内部填充）。
    proj = fill_interior_2d(mask.any(axis=axis))
    for flag, idx in ((seal_lo, 0), (seal_hi, -1)):
        if not flag:
            continue
        sl = [slice(None)] * 3
        sl[axis] = idx
        out[tuple(sl)] |= proj
    return out


def _compact_palette(model, out):
    """去掉调色板里没被用到的项（air 固定在索引 0），返回重映射后的 out。

    只归并颜色还不够：那几种方块仍然留在 BlockStatePalette 里，Litematica 照样
    要给每一项建模型缓存。必须把没用的项从调色板里删掉才算真省。
    """
    used = np.unique(out)
    old = list(model.palette)
    air_i = next((i for i, st in enumerate(old) if st.name == "minecraft:air"), None)
    keep = [int(i) for i in used if i != air_i]
    order = ([air_i] if air_i is not None else []) + keep
    lut = np.zeros(len(old), dtype=np.int64)
    for new_i, old_i in enumerate(order):
        lut[old_i] = new_i
    model.palette = [old[i] for i in order]
    return lut[out]


def _limit_kinds(model, palette, per_color, inverse, names_of, state_index, max_kinds):
    """方块种类超限时，把用得最少的几种并到颜色最接近的保留方块上。

    Litematica 给每种 BlockState 单独建模型缓存（日志里的
    "BlockModelCacheSchematic: refreshing model cache"），种类越多加载越慢。
    一个灰头盔选出 69 种灰阶方块毫无意义——压到 20 多种视觉上几乎看不出，
    加载时间能差好几倍。

    按体素数量排序保留，不是按唯一色数量：大面积的颜色才是主角。
    """
    idxs = np.array([state_index[n] for n in state_index], dtype=np.int64)
    names = list(state_index.keys())
    weights = np.bincount(inverse, minlength=len(per_color))
    state_cnt = np.zeros(len(model.palette), dtype=np.int64)
    np.add.at(state_cnt, per_color, weights)

    order = np.argsort(-state_cnt[idxs], kind="stable")
    keep_names = [names[i] for i in order[:max_kinds]]
    drop_names = [names[i] for i in order[max_kinds:]]

    lab_of = {n: palette.labs[i] for i, n in enumerate(palette.names)}
    keep_labs = np.array([lab_of[n] for n in keep_names])
    remap = np.arange(len(model.palette), dtype=np.int64)
    for name in drop_names:
        lab = lab_of[name]
        j = int(np.argmin(np.linalg.norm(keep_labs - lab, axis=1)))
        remap[state_index[name]] = state_index[keep_names[j]]

    print(
        f"[mc_voxel] 方块种类 {len(state_index)} 超过上限 {max_kinds}，"
        f"并把 {len(drop_names)} 种归到最近的保留方块"
    )
    return remap[per_color]


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
    seal_axis=None,
    seal_lo: bool = False,
    seal_hi: bool = False,
    noise: int = 0,
    max_kinds: int = 0,
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
    noise : 0-100。同一颜色区域里按这个百分比改用「次优」方块，用来打散
            大面积墙面的单调感。选谁由体素坐标哈希决定，同一模型每次结果
            一致，不会引入随机闪烁。
    max_kinds : 方块种类上限（0 = 不限）。超过时把用得最少的方块并到颜色最接近的
            保留方块上。Litematica 要为每种 BlockState 单独建模型缓存，种类越多
            加载越慢——69 种灰阶方块能把一个头盔渲染到卡死，压到 20 多种几乎
            看不出差别。
    seal_axis / seal_lo / seal_hi : 分块导出专用。沿 seal_axis 的 low / high
                  端面补一层"虚拟封盖"再做内部填充——分段后切面上没有表面，
                  实心模型会被 flood fill 判成空心。封盖只盖"该截面上模型
                  真正占用的投影区域"，不会凭空填满整面。封盖层会保留在
                  结果里，因此相邻两段在切面处重叠一层（摆放时叠加无害）。
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
        if seal_axis is not None and (seal_lo or seal_hi):
            mask = _apply_seal(mask, seal_axis, seal_lo, seal_hi)
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
    bin_centers = None
    if texture_match and getattr(palette, "has_texture_info", False):
        vox_std = neighborhood_std(xs, ys, zs, rgb, size)
        target_std = texture_targets_by_color(uniq_keys, inverse, vox_std)
        bin_centers = np.asarray(TEXTURE_BIN_CENTERS)[
            [texture_bin(s) for s in target_std]
        ]
        matched = palette.nearest_batch(uniq_rgb, stds=bin_centers, exact=True)
    else:
        matched = palette.nearest_batch(uniq_rgb, exact=True)

    if noise > 0 and len(uniq_keys) and hasattr(palette, "nearest_topk"):
        # 噪声：同色区域里按比例换成绩次优的方块，打散大面墙的单调感。
        # 用体素坐标哈希而不是随机数，保证同一模型每次导出一致。
        top = palette.nearest_topk(uniq_rgb, stds=bin_centers, k=2)
        h = (
            (np.asarray(xs, dtype=np.int64) * 73856093)
            ^ (np.asarray(ys, dtype=np.int64) * 19349663)
            ^ (np.asarray(zs, dtype=np.int64) * 83492791)
        ) % 100
        use_alt = h < int(noise)
        chosen = np.where(use_alt, top[1][inverse], top[0][inverse])
        uniq_block, inv_block = np.unique(chosen, return_inverse=True)
        state_of = np.empty(len(uniq_block), dtype=np.int64)
        for i, bi in enumerate(uniq_block):
            state_of[i] = model.add_state(BlockState(palette.names[int(bi)]))
        out = state_of[inv_block]
    else:
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

        if max_kinds and max_kinds > 0 and len(state_index) > max_kinds:
            per_color = _limit_kinds(
                model, palette, per_color, inverse,
                names_of=matched, state_index=state_index, max_kinds=int(max_kinds),
            )
        out = per_color[inverse]
        if max_kinds and max_kinds > 0 and len(state_index) > max_kinds:
            out = _compact_palette(model, out)

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
