import time

import numpy as np
import pytest

from core.fill import fill_interior, outside_mask
from core.voxelize import voxelize_surface
from helpers import box_mesh, plane_mesh


def test_empty_input():
    verts, tris = box_mesh()
    mask = voxelize_surface(verts, tris, (5, 5, 5), origin=(0, 0, 0), block_size=1.0)
    assert mask.shape == (5, 5, 5)
    assert mask.any()


def test_cube_surface_then_filled_is_1000():
    """方案验收：默认立方体缩到 10×10×10 -> 1000 个体素，不是 600 个面。"""
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    mask = voxelize_surface(verts, tris, (10, 10, 10), origin=(0, 0, 0), block_size=1.0)
    assert mask.sum() == 488  # 外壳：1000 - 8³

    filled = fill_interior(mask)
    assert filled.sum() == 1000


def test_plane_is_one_layer():
    """单个 Plane -> 得到一层（不是零层，也不是填满包围盒）。"""
    verts, tris = plane_mesh(10)
    mask = voxelize_surface(verts, tris, (10, 1, 10), origin=(0, 0, 0), block_size=1.0)
    assert mask.shape == (10, 1, 10)
    assert mask.sum() == 100
    assert mask.all()


def test_open_box_stays_hollow():
    """六面体去掉一面 -> 内部是空的。"""
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10), skip_faces=("+y",))
    mask = voxelize_surface(verts, tris, (10, 10, 10), origin=(0, 0, 0), block_size=1.0)
    filled = fill_interior(mask)
    assert filled[5, 5, 5] is np.False_ or not filled[5, 5, 5]
    assert not filled[5, 5, 5]
    assert filled.sum() < 1000


def test_closed_box_center_filled():
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    filled = fill_interior(voxelize_surface(verts, tris, (10, 10, 10), (0, 0, 0), 1.0))
    assert filled[5, 5, 5]


def test_outside_mask_empty_grid():
    solid = np.zeros((3, 3, 3), dtype=bool)
    assert outside_mask(solid).all()
    assert not fill_interior(solid).any()


def test_offset_origin():
    """原点是网格最小角，模型整体平移到远处也必须落在同一个相对位置。"""
    verts, tris = box_mesh((100, 64, -50), (110, 74, -40))
    mask = voxelize_surface(verts, tris, (10, 10, 10), origin=(100, 64, -50), block_size=1.0)
    assert fill_interior(mask).sum() == 1000


def test_block_size_changes_resolution():
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    mask = voxelize_surface(verts, tris, (5, 5, 5), origin=(0, 0, 0), block_size=2.0)
    assert fill_interior(mask).sum() == 125


def test_colors_returned_and_propagated():
    from core.fill import flood_colors

    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    colors = np.tile(np.array([[255.0, 0.0, 0.0]]), (len(verts), 1))
    mask, rgb = voxelize_surface(verts, tris, (10, 10, 10), (0, 0, 0), 1.0, colors=colors)
    assert mask.shape == rgb.shape[:3]
    filled = fill_interior(mask)
    rgb = flood_colors(filled, rgb)
    assert not np.isnan(rgb).any()
    assert abs(rgb[5, 5, 5][0] - 255) < 1e-6


def test_100_cube_under_five_seconds():
    """100³ 耗时 < 5 秒（不含填充）。"""
    verts, tris = box_mesh((0, 0, 0), (100, 100, 100))
    t0 = time.perf_counter()
    mask = voxelize_surface(verts, tris, (100, 100, 100), (0, 0, 0), 1.0)
    cost = time.perf_counter() - t0
    assert mask.any()
    assert cost < 5.0, f"100³ 体素化用了 {cost:.2f}s"


def test_bad_input():
    verts, tris = box_mesh()
    with pytest.raises(IndexError):
        voxelize_surface(verts, np.array([[0, 1, 99]]), (4, 4, 4))
    with pytest.raises(ValueError):
        voxelize_surface(verts, tris, (4, 4, 4), block_size=0)
