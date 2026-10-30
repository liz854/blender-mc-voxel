"""超上限时的分块导出。

体素网格是稠密的：400³ 就要 7 GB 内存，所以大批模型只能分段体素化。
切分的关键约束是**格子必须对齐**——所有段共用同一个 origin 和 block_size，
否则段与段之间会错半格，拼起来出现错位缝隙。

做法：先按全局包围盒算出整体网格，再沿最长轴按格数切成 N 段；每段
只挑「包围盒与该段相交」的三角形去体素化（跨段的三角形两边都拿一份，
这样接缝处不会缺面），最后各自导出成一个文件。

已知取舍：每段独立做内部填充，所以段与段的切面上会多一层壳（相当于
把一整块切成几块独立的实心块）。投影本来就是分块摆放，拼起来看不出来；
换来的是峰值内存从「整块」降到「单段」。
"""

from __future__ import annotations

import numpy as np

# 每段允许的网格体积上限，跟 build_voxel_model 的默认守卫保持一个量级
DEFAULT_PART_VOXELS = 24_000_000


def plan_parts(size, max_voxels: int = DEFAULT_PART_VOXELS) -> tuple:
    """算出要切成几段、切哪个轴。

    size : (sx, sy, sz) 整体网格格数
    返回 (axis, parts)：axis 0=X / 1=Y / 2=Z（选取格数最多的那根），
    parts 为段数（1 表示不用切）。

    优先切最长轴：最长轴切下去每段体积下降最快，段数最少。
    """
    sx, sy, sz = (int(v) for v in size)
    total = sx * sy * sz
    if total <= max_voxels:
        return 0, 1

    axis = int(np.argmax([sx, sy, sz]))
    length = (sx, sy, sz)[axis]
    # 每段沿该轴的格数上限：其余两轴不变时，单段体积 = 该轴格数 × 其余两轴
    other = total // max(length, 1)
    per_part = max(1, max_voxels // max(other, 1))
    parts = int(np.ceil(length / per_part))
    return axis, max(2, parts)


def part_ranges(length: int, parts: int) -> list:
    """把 [0, length) 均分成 parts 段，返回 [(start, stop), …]（左闭右开）。"""
    length = int(length)
    parts = max(1, int(parts))
    if parts == 1:
        return [(0, length)]
    # 均分，最后一段吃掉余数
    step = length // parts
    out = []
    start = 0
    for i in range(parts):
        stop = length if i == parts - 1 else start + step
        if stop > start:
            out.append((start, stop))
        start = stop
    return out


def select_triangles(verts, tris, origin, block_size, axis, start, stop):
    """挑出包围盒与 [start, stop) 这段格子在 axis 上相交的三角形。

    跨段边界的三角形在相邻两段里都会出现——这是有意的：只要少给一边，
    接缝处就会缺一整层面。
    """
    V = np.asarray(verts, dtype=np.float64)
    T = np.asarray(tris, dtype=np.int64)
    if len(T) == 0:
        return T
    # 该段在 axis 方向覆盖的世界坐标区间
    lo_world = float(origin[axis]) + start * block_size
    hi_world = float(origin[axis]) + stop * block_size
    tri = V[T][:, :, axis]  # (M,3)
    tri_lo = tri.min(axis=1)
    tri_hi = tri.max(axis=1)
    # 相交判据与体素化一致：闭区间重叠（相切也算，否则边界上的面会漏）
    keep = (tri_hi >= lo_world) & (tri_lo <= hi_world)
    return T[keep]


def plan_segments(size, origin, block_size, axis, parts) -> list:
    """把整体网格按 axis 切段，返回每段的 (origin, size, start, stop)。

    所有段共用 block_size，origin 只在该轴方向上偏移整数格，
    因此格子边界严格对齐，拼回去不缺不重。
    """
    sx, sy, sz = (int(v) for v in size)
    dims = [sx, sy, sz]
    ranges = part_ranges(dims[axis], parts)
    out = []
    for start, stop in ranges:
        seg_size = list(dims)
        seg_size[axis] = stop - start
        seg_origin = np.array(origin, dtype=np.float64).copy()
        seg_origin[axis] += start * block_size
        out.append((seg_origin, tuple(seg_size), start, stop))
    return out


def part_suffix(axis: int, index: int, total: int) -> str:
    """给分块文件名加后缀，例如 _y1of3（轴用 x/y/z）。"""
    letter = "xyz"[axis]
    return f"_{letter}{index + 1}of{total}"
