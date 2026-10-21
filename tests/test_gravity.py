"""重力方块支撑规则：底下没有方块托着，就不许用沙子/沙砾。"""

import numpy as np
import pytest

from core.gravity import GRAVITY_BLOCKS, enforce_gravity_support, is_gravity_block
from core.ir import BlockState, VoxelModel
from core.palette import BlockPalette, load_fallback_palette


@pytest.fixture(scope="module")
def palette():
    return BlockPalette(load_fallback_palette())


def test_gravity_block_names():
    for name in (
        "minecraft:sand",
        "minecraft:red_sand",
        "minecraft:gravel",
        "minecraft:suspicious_sand",
        "minecraft:white_concrete_powder",
        "minecraft:red_concrete_powder",
        "minecraft:anvil",
    ):
        assert is_gravity_block(name), name
    for name in (
        "minecraft:stone",
        "minecraft:sandstone",
        "minecraft:white_concrete",
        "minecraft:gravel_wall",  # 不是整方块，但不是重力方块
    ):
        assert not is_gravity_block(name), name


def test_floating_sand_replaced(palette):
    """悬空的沙子必须被换掉。"""
    m = VoxelModel((3, 5, 3))
    m.set(1, 3, 1, BlockState("minecraft:sand"))
    n = enforce_gravity_support(m, palette)
    assert n == 1
    assert not is_gravity_block(m.get(1, 3, 1).name)
    # 换成砂岩/石头这类近似色，不能是空气
    assert not m.get(1, 3, 1).is_air


def test_grounded_sand_kept(palette):
    """贴地（y=0）的沙子不动。"""
    m = VoxelModel((3, 3, 3))
    m.set(1, 0, 1, BlockState("minecraft:sand"))
    assert enforce_gravity_support(m, palette) == 0
    assert m.get(1, 0, 1).name == "minecraft:sand"


def test_sand_on_stone_kept(palette):
    """石头托着的沙子不动，哪怕石头自己悬空——托住了就是托住了。"""
    m = VoxelModel((3, 4, 3))
    m.set(1, 1, 1, BlockState("minecraft:stone"))
    m.set(1, 2, 1, BlockState("minecraft:sand"))
    assert enforce_gravity_support(m, palette) == 0
    assert m.get(1, 2, 1).name == "minecraft:sand"


def test_sand_stack_all_kept(palette):
    """落地沙柱整根保留，不能因为判顺序把上面几格误伤。"""
    m = VoxelModel((3, 6, 3))
    for y in range(6):
        m.set(1, y, 1, BlockState("minecraft:sand"))
    assert enforce_gravity_support(m, palette) == 0
    for y in range(6):
        assert m.get(1, y, 1).name == "minecraft:sand"


def test_mixed_column_only_floating_replaced(palette):
    """空气/沙子/空气/沙子：悬空那格换掉，落地那格留着。"""
    m = VoxelModel((3, 5, 3))
    m.set(1, 0, 1, BlockState("minecraft:sand"))
    m.set(1, 3, 1, BlockState("minecraft:sand"))
    n = enforce_gravity_support(m, palette)
    assert n == 1
    assert m.get(1, 0, 1).name == "minecraft:sand"
    assert not is_gravity_block(m.get(1, 3, 1).name)


def test_gravel_and_powder_also_checked(palette):
    m = VoxelModel((5, 4, 5))
    m.set(0, 2, 0, BlockState("minecraft:gravel"))
    m.set(4, 2, 4, BlockState("minecraft:white_concrete_powder"))
    n = enforce_gravity_support(m, palette)
    assert n == 2
    assert not is_gravity_block(m.get(0, 2, 0).name)
    assert not is_gravity_block(m.get(4, 2, 4).name)


def test_replacement_ignores_other_gravity_blocks(palette):
    """替代结果不能再是重力方块，否则塌得更彻底。"""
    m = VoxelModel((3, 5, 3))
    m.set(1, 4, 1, BlockState("minecraft:sand"))
    enforce_gravity_support(m, palette)
    assert not is_gravity_block(m.get(1, 4, 1).name)
    # 剩下的所有方块里也不该新长出重力方块
    used = {m.get(1, 4, 1).name}
    assert not any(is_gravity_block(n) for n in used)


def test_floating_sand_column_replaced_entirely(palette):
    """悬空沙柱要整根换：沙托沙不算托，施工顺序不保证从下往上。"""
    m = VoxelModel((3, 8, 3))
    m.set(1, 0, 1, BlockState("minecraft:stone"))  # 只贴地一块石头
    for y in (3, 4, 5):
        m.set(1, y, 1, BlockState("minecraft:sand"))  # y=1/2 是空气
    n = enforce_gravity_support(m, palette)
    assert n == 3
    for y in (3, 4, 5):
        assert not is_gravity_block(m.get(1, y, 1).name), y


def test_non_gravity_block_above_floating_sand_untouched(palette):
    """沙子上面压的石头不受影响：石头不落地也不掉，悬挑照留。"""
    m = VoxelModel((3, 8, 3))
    m.set(1, 0, 1, BlockState("minecraft:stone"))
    m.set(1, 3, 1, BlockState("minecraft:sand"))
    m.set(1, 4, 1, BlockState("minecraft:stone"))
    assert enforce_gravity_support(m, palette) == 1
    assert not is_gravity_block(m.get(1, 3, 1).name)
    assert m.get(1, 4, 1).name == "minecraft:stone"


def test_no_gravity_blocks_is_noop(palette):
    """纯石头模型不动，也不能报错。"""
    m = VoxelModel((3, 3, 3))
    m.set(1, 1, 1, BlockState("minecraft:stone"))
    assert enforce_gravity_support(m, palette) == 0


def test_volume_preserved(palette):
    """替换只改方块种类，不能改变方块总数（不许出现洞）。"""
    m = VoxelModel((6, 6, 6))
    for y in range(6):
        for x in range(6):
            m.set(x, y, 0, BlockState("minecraft:sand"))
    before = m.total_blocks
    enforce_gravity_support(m, palette)
    assert m.total_blocks == before
