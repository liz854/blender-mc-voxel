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
