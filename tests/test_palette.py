import numpy as np
import pytest

from core.color import delta_e76, srgb_to_lab
from core.palette import (
    TEXTURE_BIN_CENTERS,
    BlockPalette,
    load_fallback_palette,
    texture_bin,
)


def test_white_black():
    assert abs(srgb_to_lab((255, 255, 255))[0] - 100.0) < 0.2
    assert abs(srgb_to_lab((0, 0, 0))[0]) < 0.2


def test_red_positive_a():
    L, a, b = srgb_to_lab((255, 0, 0))
    assert a > 40
    assert L > 40


def test_batch_matches_single():
    rng = np.random.default_rng(1)
    rgbs = rng.integers(0, 256, size=(20, 3)).astype(float)
    batch = srgb_to_lab(rgbs)
    for i in range(20):
        assert np.allclose(batch[i], srgb_to_lab(rgbs[i]))


def test_delta_e_zero():
    lab = srgb_to_lab((10, 20, 30))
    assert delta_e76(lab, lab) == pytest.approx(0.0, abs=1e-9)


def test_fallback_palette_loads():
    colors = load_fallback_palette()
    assert len(colors) > 40
    assert "minecraft:stone" in colors


def test_gray_matches_stone_family():
    """灰度渐变 -> stone / andesite / deepslate 系列自然过渡（不能全是 stone）。"""
    p = BlockPalette(load_fallback_palette())
    picks = [p.nearest((v, v, v)) for v in (60, 110, 140, 200)]
    assert len(set(picks)) >= 2


def test_pure_red_not_terracotta():
    p = BlockPalette(load_fallback_palette())
    pick = p.nearest((255, 0, 0))
    assert "red" in pick


def test_batch_query():
    p = BlockPalette(load_fallback_palette())
    names = p.nearest_batch(np.array([[0, 0, 0], [255, 255, 255]], dtype=float))
    assert len(names) == 2
    assert all(isinstance(n, str) for n in names)


def test_deny_removes_block():
    colors = {"minecraft:stone": (128, 128, 128), "minecraft:black_wool": (20, 20, 20)}
    p = BlockPalette(colors, deny=["minecraft:stone"])
    assert p.nearest((128, 128, 128)) == "minecraft:black_wool"


def test_allow_restricts():
    colors = {"minecraft:stone": (128, 128, 128), "minecraft:sand": (219, 211, 160)}
    p = BlockPalette(colors, allow=["minecraft:sand"])
    assert p.nearest((128, 128, 128)) == "minecraft:sand"


def test_empty_after_filter_raises():
    with pytest.raises(ValueError):
        BlockPalette({"minecraft:stone": (1, 1, 1)}, allow=["minecraft:dirt"])


def test_lut_shape_and_filled():
    p = BlockPalette(load_fallback_palette())
    assert p.lut.shape == (32, 32, 32)
    assert (p.lut >= 0).all()


# ---------------- 禁用方块（需求⑦） ----------------

from core.palette import parse_block_filter


def test_parse_filter_splits_several_ways():
    got = parse_block_filter("minecraft:glass, minecraft:water; stone\nleafy，sand；")
    assert got == ["minecraft:glass", "minecraft:water", "minecraft:stone", "minecraft:leafy", "minecraft:sand"]


def test_parse_filter_adds_namespace():
    assert parse_block_filter("glass") == ["minecraft:glass"]
    # 已经带命名空间的不重复加
    assert parse_block_filter("minecraft:glass") == ["minecraft:glass"]


def test_parse_filter_empty():
    assert parse_block_filter("") == []
    assert parse_block_filter("   , ; \n ") == []


def test_parse_filter_globs_against_known():
    known = ["minecraft:red_wool", "minecraft:blue_wool", "minecraft:stone", "minecraft:glass"]
    got = parse_block_filter("*wool*", known=known)
    assert got == ["minecraft:red_wool", "minecraft:blue_wool"]
    # 通配吃不掉没匹配的
    assert parse_block_filter("nothing_*", known=known) == []


def test_parse_filter_dedupes():
    assert parse_block_filter("glass, glass, minecraft:glass") == ["minecraft:glass"]


def test_deny_falls_back_to_nearest_color():
    """被禁的方块不能留空洞：原本该给它的位置必须落到近似色。"""
    colors = {
        "minecraft:glass": (200, 220, 230),
        "minecraft:white_concrete": (207, 213, 214),
        "minecraft:red_wool": (160, 39, 34),
        "minecraft:red_concrete": (143, 33, 33),
    }
    free = BlockPalette(colors)
    banned = BlockPalette(colors, deny=["minecraft:glass", "minecraft:red_concrete"])

    assert banned.names == ["minecraft:white_concrete", "minecraft:red_wool"]
    # 查询原本指向玻璃的浅灰蓝 -> 换成白混凝土
    assert free.nearest((200, 220, 230)) == "minecraft:glass"
    assert banned.nearest((200, 220, 230)) == "minecraft:white_concrete"
    # 正红 -> 从红混凝土改成红羊毛
    assert free.nearest((150, 35, 34)) == "minecraft:red_concrete"
    assert banned.nearest((150, 35, 34)) == "minecraft:red_wool"


def test_deny_everything_raises():
    colors = {"minecraft:stone": (125, 125, 125)}
    with pytest.raises(ValueError):
        BlockPalette(colors, deny=["minecraft:stone"])


# ---------------- 面板输入框的增删（搜索添加 / 清空用） ----------------

from core.palette import drop_filter, filter_tokens, merge_filter


def test_filter_tokens_keeps_what_user_typed():
    """回读要原样：补 prefix、展开通配是匹配阶段的事，不能污染输入框。"""
    assert filter_tokens("glass，*wool*; oak_planks") == ["glass", "*wool*", "oak_planks"]
    assert filter_tokens("") == []


def test_merge_filter_appends_and_normalizes():
    assert merge_filter("", "minecraft:stone") == "minecraft:stone"
    assert merge_filter("glass", "oak_planks") == "glass, oak_planks"
    # 重复的不加，大小写不敏感
    assert merge_filter("minecraft:stone", "Minecraft:Stone") == "minecraft:stone"


def test_merge_filter_keeps_wildcards_and_rewrites_separators():
    assert merge_filter("glass;water\nsand", "*wool*") == "glass, water, sand, *wool*"


def test_drop_filter_removes_one_without_touching_others():
    assert drop_filter("glass, oak_planks, water", "oak_planks") == "glass, water"
    assert drop_filter("glass", "nothing") == "glass"
    assert drop_filter("glass", "") == "glass"
    # 删干净之后是空串，不能留个逗号
    assert drop_filter("glass", "glass") == ""


# ---------------- 纹理感知匹配（std 进成本） ----------------

def test_texture_bin_edges():
    assert texture_bin(0) == 0
    assert texture_bin(7.9) == 0
    assert texture_bin(8) == 1
    assert texture_bin(20) == 2
    assert texture_bin(34.9) == 2
    assert texture_bin(35) == 3
    assert texture_bin(255) == 3
    assert len(TEXTURE_BIN_CENTERS) == 4


def _std_test_palette():
    colors = {
        "minecraft:flat_block": (128, 128, 128),
        "minecraft:mid_block": (130, 128, 126),
        "minecraft:busy_block": (128, 128, 128),
    }
    stds = {
        "minecraft:flat_block": 2.0,
        "minecraft:mid_block": 14.0,
        "minecraft:busy_block": 30.0,
    }
    return BlockPalette(colors, stds=stds)


def test_texture_match_prefers_smooth_for_flat_target():
    """同平均色一平一花：平的目标选平的，粗的目标选花的。"""
    p = _std_test_palette()
    assert p.luts is not None
    assert p.nearest((128, 128, 128), std=1.0) == "minecraft:flat_block"
    # 中间档（bin 1，中心 14）：mid_block 的 std 正好 14，赢
    assert p.nearest((128, 128, 128), std=14.0) == "minecraft:mid_block"
    assert p.nearest((128, 128, 128), std=27.0) == "minecraft:busy_block"
    assert p.nearest((128, 128, 128), std=45.0) == "minecraft:busy_block"


def test_texture_batch_uses_per_row_bins():
    p = _std_test_palette()
    got = p.nearest_batch(
        np.full((3, 3), 128.0), stds=[1.0, 27.0, 45.0]
    )
    assert got == ["minecraft:flat_block", "minecraft:busy_block", "minecraft:busy_block"]


def test_texture_off_when_all_stds_equal():
    """全色板 std 一样时纹理项没意义，别白建 4 张 LUT。"""
    colors = {"minecraft:a": (1, 2, 3), "minecraft:b": (4, 5, 6)}
    p = BlockPalette(colors, stds={n: 10.0 for n in colors})
    assert p.luts is None


def test_no_stds_keeps_plain_behavior():
    p = BlockPalette(load_fallback_palette())
    assert p.luts is None
    # 带 std 参数也不炸，只是忽略
    assert p.nearest((128, 128, 128), std=30.0) == p.nearest((128, 128, 128))


def test_color_only_query_ignores_std_dimension():
    """重力替换这些内部查询不带 std，必须走纯颜色 LUT，行为不变。"""
    colors = {
        "minecraft:flat_block": (128, 128, 128),
        "minecraft:mid_block": (130, 128, 126),
        "minecraft:busy_block": (128, 128, 128),
    }
    with_std = BlockPalette(
        colors,
        stds={
            "minecraft:flat_block": 2.0,
            "minecraft:mid_block": 14.0,
            "minecraft:busy_block": 30.0,
        },
    )
    plain = BlockPalette(colors)
    # 同一组查询，带不带 std 的纯颜色路径必须给同样的答案
    for rgb in ((128, 128, 128), (130, 128, 126), (60, 60, 60)):
        assert with_std.nearest(rgb) == plain.nearest(rgb)


def test_without_carries_stds():
    p = _std_test_palette()
    q = p.without(["minecraft:flat_block"])
    assert q.names == ["minecraft:mid_block", "minecraft:busy_block"]
    assert q.luts is not None
    assert q.stds.tolist() == [14.0, 30.0]
    assert q.nearest((128, 128, 128), std=1.0) == "minecraft:mid_block"
