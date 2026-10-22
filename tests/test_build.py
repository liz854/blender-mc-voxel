import numpy as np
import pytest

from core import build as core_build
from core import mesh_export
from core.coords import blender_to_mc_array, mc_to_blender_array
from core.palette import BlockPalette, load_fallback_palette
from helpers import box_mesh, white_colors


def palette():
    return BlockPalette(load_fallback_palette())


def test_build_cube_10x10x10():
    """端到端：10×10×10 立方体 -> 1000 个方块。"""
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    colors = white_colors(len(verts))
    model = core_build.build_voxel_model(verts, tris, colors, 1.0, palette())
    assert model.size == (10, 10, 10)
    assert model.total_blocks == 1000
    assert len(model.palette) >= 2


def test_build_shell_only():
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    model = core_build.build_voxel_model(
        verts, tris, white_colors(len(verts)), 1.0, palette(), solid=False
    )
    assert model.total_blocks == 488


def test_build_hollow_when_open():
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10), skip_faces=("+y",))
    model = core_build.build_voxel_model(verts, tris, white_colors(len(verts)), 1.0, palette())
    assert model.total_blocks < 1000


def test_build_block_size_2():
    verts, tris = box_mesh((0, 0, 0), (10, 10, 10))
    model = core_build.build_voxel_model(verts, tris, white_colors(len(verts)), 2.0, palette())
    assert model.size == (5, 5, 5)
    assert model.total_blocks == 125


def test_grid_from_bounds():
    verts, _ = box_mesh((1, 2, 3), (4, 6, 9))
    origin, size = core_build.grid_from_bounds(verts, 1.0)
    assert tuple(origin) == (1, 2, 3)
    assert size == (3, 4, 6)


def test_voxel_model_colors_are_from_palette():
    verts, tris = box_mesh((0, 0, 0), (4, 4, 4))
    colors = np.tile(np.array([[200.0, 40.0, 40.0]]), (len(verts), 1))
    model = core_build.build_voxel_model(verts, tris, colors, 1.0, palette())
    used = {model.palette[int(i)].name for i in np.unique(model.blocks)}
    assert len(used) == 1
    assert "red" in used.pop()


def test_texture_match_flat_region_picks_smooth_block():
    """平涂的面必须选平纹理的方块，即使色板里有同平均色的花纹理方块。"""
    colors = {
        "minecraft:flat_block": (120, 120, 120),
        "minecraft:busy_block": (120, 120, 120),
    }
    stds = {"minecraft:flat_block": 2.0, "minecraft:busy_block": 30.0}
    pal = BlockPalette(colors, stds=stds)
    verts, tris = box_mesh((0, 0, 0), (8, 8, 8))
    vc = np.tile(np.array([[120.0, 120.0, 120.0]]), (len(verts), 1))
    model = core_build.build_voxel_model(verts, tris, vc, 1.0, pal)
    used = {model.palette[int(i)].name for i in np.unique(model.blocks)}
    assert used == {"minecraft:flat_block"}


def test_texture_targets_low_quantile_ignores_boundary():
    """颜色分界处的体素邻域横跨两色、std 虚高；同色区域取 25% 分位
    必须仍判"平"，否则墙色分界线会长出花纹理斑点。"""
    u8 = np.full((10, 1, 3), 200, dtype=np.uint8)
    u8[:, 0, 0] = 40  # 随便让每行同色
    std = np.array([0.0] * 8 + [40.0, 40.0])
    uniq, target = core_build.texture_targets_by_color(u8, std)
    assert len(uniq) == 1
    assert target[0] == pytest.approx(0.0)  # 25% 分位吃掉两个高值


def test_texture_targets_high_when_majority_noisy():
    """反过来：多数体素都粗糙时不能被误判成平的。"""
    u8 = np.full((10, 1, 3), 200, dtype=np.uint8)
    std = np.array([0.0, 35.0, 38.0, 40.0, 42.0, 36.0, 39.0, 41.0, 37.0, 40.0])
    _, target = core_build.texture_targets_by_color(u8, std)
    assert target[0] > 20.0  # 25% 分位仍落在高档


def test_mesh_export_normals_outward():
    verts, tris = box_mesh((0, 0, 0), (4, 4, 4))
    model = core_build.build_voxel_model(verts, tris, white_colors(len(verts)), 1.0, palette())
    v, q, _c = mesh_export.model_to_mesh(model)
    assert len(q) == 6 * 16  # 4×4 每个面 16 个面
    assert mesh_export.check_outward(v, q)


def test_mesh_export_blender_roundtrip_conversion():
    verts, _ = box_mesh()
    mc = blender_to_mc_array(verts)
    back = mc_to_blender_array(mc)
    assert np.allclose(back, verts)


def test_model_carries_palette_colors():
    """mesh_export / 预览图靠 model._colors 上色，不设就一路退回灰色。"""
    verts, tris = box_mesh((0, 0, 0), (4, 4, 4))
    colors = np.tile(np.array([[200.0, 40.0, 40.0]]), (len(verts), 1))
    pal = palette()
    m = core_build.build_voxel_model(verts, tris, colors, 1.0, pal)
    used = {m.palette[int(i)].name for i in np.unique(m.blocks)} - {"minecraft:air"}
    assert len(used) == 1
    name = used.pop()
    assert m._colors[name] == pal.color_of(name)
    _v, _q, vcols = mesh_export.model_to_mesh(m)
    assert not np.all(vcols == 200.0)
