import numpy as np
import pytest

from core.color import delta_e76, srgb_to_lab
from core.palette import BlockPalette, load_fallback_palette


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
