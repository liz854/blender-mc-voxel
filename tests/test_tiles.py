"""分块导出（core/tiles.py）的单元测试。"""

import numpy as np
import pytest

from core import tiles


def test_plan_parts_no_split_when_small():
    assert tiles.plan_parts((100, 100, 100), max_voxels=10_000_000) == (0, 1)


def test_plan_parts_splits_longest_axis():
    axis, parts = tiles.plan_parts((200, 400, 200), max_voxels=4_000_000)
    assert axis == 1, "高 400 是最长轴，应该沿 Y 切"
    assert parts >= 2
    # 每段体积不超过上限
    assert 200 * 200 * (400 / parts) <= 4_000_000 * 1.01


def test_plan_parts_prefers_longest_axis():
    axis, parts = tiles.plan_parts((600, 100, 100), max_voxels=1_000_000)
    assert axis == 0
    assert parts >= 2


def test_plan_parts_never_returns_one_part_when_over_limit():
    axis, parts = tiles.plan_parts((512, 512, 512), max_voxels=24_000_000)
    assert parts >= 2


def test_part_ranges_covers_all_without_gaps():
    for length, parts in ((16, 3), (10, 4), (7, 7), (1, 1), (100, 6)):
        rs = tiles.part_ranges(length, parts)
        assert rs[0][0] == 0, rs
        assert rs[-1][1] == length, rs
        for (a, b), (c, d) in zip(rs, rs[1:]):
            assert b == c, f"段之间不能有缝也不能重叠：{(a, b)} -> {(c, d)}"
        assert all(b > a for a, b in rs)


def test_select_triangles_keeps_boundary_faces_on_both_sides():
    """跨分界的三角形两侧都要拿到，否则接缝处会缺一整层面。"""
    verts = np.array([[0, 3.5, 0], [1, 3.5, 0], [0, 4.5, 0]], dtype=float)
    tris = np.array([[0, 1, 2]])
    origin = np.zeros(3)
    lower = tiles.select_triangles(verts, tris, origin, 1.0, 1, 0, 4)
    upper = tiles.select_triangles(verts, tris, origin, 1.0, 1, 4, 8)
    assert len(lower) == 1 and len(upper) == 1


def test_select_triangles_drops_out_of_range():
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    tris = np.array([[0, 1, 2]])
    assert len(tiles.select_triangles(verts, tris, np.zeros(3), 1.0, 1, 20, 30)) == 0


def test_plan_segments_aligns_to_grid():
    """所有段必须与整体网格共用格子边界，否则拼起来错位。"""
    origin = np.array([1.5, 2.5, 3.5])
    block = 0.5
    segs = tiles.plan_segments((10, 10, 10), origin, block, 1, 3)
    assert len(segs) == 3
    covered = 0
    for seg_origin, seg_size, start, stop in segs:
        # 只在切分轴上偏移，且偏移量是整格
        assert seg_origin[0] == pytest.approx(origin[0])
        assert seg_origin[2] == pytest.approx(origin[2])
        shift = (seg_origin[1] - origin[1]) / block
        assert abs(shift - round(shift)) < 1e-9, f"偏移 {shift} 格，不是整格"
        assert seg_size == (10, stop - start, 10)
        covered += stop - start
    assert covered == 10


def test_part_suffix():
    assert tiles.part_suffix(1, 0, 3) == "_y1of3"
    assert tiles.part_suffix(0, 2, 4) == "_x3of4"
    assert tiles.part_suffix(2, 1, 2) == "_z2of2"


# ---------------- 端到端：分段体素化 vs 整体 ----------------


def _palette():
    from core.palette import BlockPalette, load_fallback_palette

    return BlockPalette(load_fallback_palette())


def test_segmented_build_keeps_solid_interior():
    """分段后中间段不能变成空心——切面要补封盖。

    整体建一个 8×24×8 的实心盒子应该得到 1536 个方块；切成 4 段后
    每段独立体素化，切面处各重叠一层封盖，总量应该略多于整体，
    但绝不能明显少（少了就说明内腔被 flood fill 判成外部了）。
    """
    from core import build as core_build
    from helpers import box_mesh

    pal = _palette()
    verts, tris = box_mesh((0, 0, 0), (8, 24, 8))
    colors = np.full((len(verts), 3), 200.0)

    whole = core_build.build_voxel_model(verts, tris, colors, 1.0, pal, solid=True)
    assert whole.total_blocks == 8 * 24 * 8

    origin, size = core_build.grid_from_bounds(verts, 1.0)
    axis, parts = tiles.plan_parts(size, max_voxels=500)
    assert axis == 1 and parts >= 2

    total = 0
    segs = tiles.plan_segments(size, origin, 1.0, axis, parts)
    for i, (seg_origin, seg_size, start, stop) in enumerate(segs):
        sub = tiles.select_triangles(verts, tris, origin, 1.0, axis, start, stop)
        m = core_build.build_voxel_model(
            verts,
            sub,
            colors,
            1.0,
            pal,
            solid=True,
            origin=seg_origin,
            size=seg_size,
            seal_axis=axis,
            seal_lo=i > 0,
            seal_hi=i < len(segs) - 1,
        )
        total += m.total_blocks

    # 每段都比原来多一层封盖，所以总量略多；但必须接近整体而不是空心
    assert total >= whole.total_blocks, f"分段后反而少了：{total} < {whole.total_blocks}"
    assert total <= whole.total_blocks + 8 * 8 * (parts - 1) * 2, (
        f"封盖层数过多：{total}"
    )


def test_segmented_build_without_seal_loses_interior():
    """反证：不补封盖时中间段会空心（这是必须补封盖的原因）。"""
    from core import build as core_build
    from helpers import box_mesh

    pal = _palette()
    verts, tris = box_mesh((0, 0, 0), (8, 24, 8))
    colors = np.full((len(verts), 3), 200.0)
    origin, size = core_build.grid_from_bounds(verts, 1.0)
    axis, parts = tiles.plan_parts(size, max_voxels=500)
    segs = tiles.plan_segments(size, origin, 1.0, axis, parts)

    total = 0
    for seg_origin, seg_size, start, stop in segs:
        sub = tiles.select_triangles(verts, tris, origin, 1.0, axis, start, stop)
        m = core_build.build_voxel_model(
            verts, sub, colors, 1.0, pal, solid=True,
            origin=seg_origin, size=seg_size,
        )
        total += m.total_blocks
    assert total < 8 * 24 * 8, "不封盖时中间段应该被掏空，总量必然少于整体"
