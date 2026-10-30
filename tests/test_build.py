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
    u8 = np.full((10, 3), 200, dtype=np.uint8)
    u8[:, 0] = 40
    std = np.array([0.0] * 8 + [40.0, 40.0])
    uniq, inverse = np.unique(core_build.pack_rgb_keys(u8), return_inverse=True)
    target = core_build.texture_targets_by_color(uniq, inverse, std)
    assert len(uniq) == 1
    assert target[0] == pytest.approx(0.0)  # 25% 分位吃掉两个高值


def test_texture_targets_high_when_majority_noisy():
    """反过来：多数体素都粗糙时不能被误判成平的。"""
    u8 = np.full((10, 3), 200, dtype=np.uint8)
    u8[:, 0] = 40
    std = np.array([0.0, 35.0, 38.0, 40.0, 42.0, 36.0, 39.0, 41.0, 37.0, 40.0])
    uniq, inverse = np.unique(core_build.pack_rgb_keys(u8), return_inverse=True)
    target = core_build.texture_targets_by_color(uniq, inverse, std)
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


def test_noise_mixes_second_best_but_is_reproducible():
    """噪声打开后同色区域会出现第二种方块，但同样输入每次结果一致。"""
    colors = {
        "minecraft:flat_block": (120, 120, 120),
        "minecraft:busy_block": (122, 120, 118),
    }
    stds = {"minecraft:flat_block": 2.0, "minecraft:busy_block": 30.0}
    pal = BlockPalette(colors, stds=stds)
    verts, tris = box_mesh((0, 0, 0), (8, 8, 8))
    vc = np.tile(np.array([[120.0, 120.0, 120.0]]), (len(verts), 1))

    def used(noise):
        m = core_build.build_voxel_model(verts, tris, vc, 1.0, pal, noise=noise)
        return {m.palette[int(i)].name for i in np.unique(m.blocks)}

    assert len(used(0)) == 1, "噪声 0 时只该用最优方块"
    noisy = used(50)
    assert len(noisy) >= 2, f"噪声 50% 应该混入第二种方块，实际只有 {noisy}"
    assert used(50) == noisy, "同样输入两次结果必须一致（哈希而非随机数）"


def test_noise_keeps_block_count():
    """噪声只换方块种类，不能改变方块数量。"""
    colors = {
        "minecraft:flat_block": (120, 120, 120),
        "minecraft:busy_block": (122, 120, 118),
    }
    pal = BlockPalette(colors, stds={"minecraft:flat_block": 2.0, "minecraft:busy_block": 30.0})
    verts, tris = box_mesh((0, 0, 0), (6, 6, 6))
    vc = np.tile(np.array([[120.0, 120.0, 120.0]]), (len(verts), 1))
    a = core_build.build_voxel_model(verts, tris, vc, 1.0, pal, noise=0)
    b = core_build.build_voxel_model(verts, tris, vc, 1.0, pal, noise=80)
    assert a.total_blocks == b.total_blocks == 216


def test_max_kinds_limits_variety_without_losing_blocks():
    """方块种类上限：种类被压下来，但方块总数一格不少。"""
    # 六种灰阶色，配六个平均色各差一点的方块
    colors = {
        f"minecraft:g{i}": (100 + i * 8, 100 + i * 8, 100 + i * 8) for i in range(6)
    }
    pal = BlockPalette(colors)
    verts, tris = box_mesh((0, 0, 0), (6, 6, 6))
    # 顶点色按 x 分六档，保证六种色都会出现
    vc = np.zeros((len(verts), 3))
    for i, v in enumerate(verts):
        band = int(min(max((v[0] + 0.0) // 1, 0), 5))
        vc[i] = 100 + band * 8
    plain = core_build.build_voxel_model(verts, tris, vc, 1.0, pal)
    assert len({s.name for s in plain.palette if s.name != "minecraft:air"}) > 2

    limited = core_build.build_voxel_model(verts, tris, vc, 1.0, pal, max_kinds=2)
    kinds = {s.name for s in limited.palette if s.name != "minecraft:air"}
    assert len(kinds) <= 2, f"应该压到 2 种以内，实际 {kinds}"
    assert limited.total_blocks == plain.total_blocks, "种类变了但方块数不能变"
