"""对着 Litematica 源码（LitematicaSchematic / ArrayBlockContainer /
TightLongBackedIntArray / SchematicMetadata）写死的格式一致性测试。

不是「读回自己写的东西」，而是独立按 Java 侧规则重新算一遍，验证：
  - Version <= CURRENT_SCHEMATIC_VERSION(4)
  - isValidData 要求的 Metadata / Regions 都在
  - Metadata 的统计字段齐全且类型是 Long
  - bits = max(2, ceil(log2(paletteSize)))（getRequiredBitWidth）
  - BlockStates 长度 = roundUp(volume * bits, 64) / 64（tight，不对齐 long）
  - 索引 = x + z*sx + y*sx*sz（ArrayBlockContainer.getIndex）
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.ir import BlockState, VoxelModel
from core.litematic import build_litematic_tree
import core.nbt as nbt

DV = 4315
CURRENT_SCHEMATIC_VERSION = 7  # 实测 Litematica 0.29.1 / MC 26.3 的 golden sample
MINIMUM_ENTRY_WIDTH = 2  # ArrayBlockContainer.MINIMUM_ENTRY_WIDTH


def get_required_bit_width(palette_size: int) -> int:
    """ArrayBlockContainer.getRequiredBitWidth 的直译。"""
    return max(MINIMUM_ENTRY_WIDTH, (palette_size - 1).bit_length())


def tight_long_count(volume: int, bits: int) -> int:
    """TightLongBackedIntArray.getRequiredArrayLength 的直译。"""
    return math.ceil(volume * bits / 64)


def litematica_index(x, y, z, sx, sy, sz) -> int:
    """ArrayBlockContainer.getIndex 的直译。"""
    return y * sx * sz + z * sx + x


def read_entry(longs, index, bits) -> int:
    """TightLongBackedIntArray.getAt 的直译（跨 long 时拼接）。"""
    start = index * bits
    a = start >> 6
    b = ((index + 1) * bits - 1) >> 6
    off = start & 63
    mask = (1 << bits) - 1
    lo = (longs[a] & 0xFFFFFFFFFFFFFFFF) >> off
    if a == b:
        return int(lo & mask)
    hi = (longs[b] & 0xFFFFFFFFFFFFFFFF) << (64 - off)
    return int((lo | hi) & mask)


def make_model(size):
    m = VoxelModel(size)
    n = 0
    for y in range(size[1]):
        for z in range(size[2]):
            for x in range(size[0]):
                if (x + y + z) % 3 == 0:
                    m.set(x, y, z, BlockState("minecraft:stone"))
                    n += 1
    return m, n


@pytest.mark.parametrize("size", [(5, 2, 3), (16, 16, 16), (1, 1, 1), (33, 7, 2)])
def test_version_within_supported(size):
    tree = build_litematic_tree(make_model(size)[0], mc_data_version=DV)
    v = int(tree["Version"])
    assert 0 < v <= CURRENT_SCHEMATIC_VERSION, f"Version={v} 会被 Litematica 当未知版本"


@pytest.mark.parametrize("size", [(5, 2, 3), (16, 16, 16)])
def test_is_valid_data_requirements(size):
    """isValidData: Metadata 和 Regions 都必须是 compound。"""
    tree = build_litematic_tree(make_model(size)[0], mc_data_version=DV)
    assert isinstance(tree.get("Metadata"), nbt.TAGCompound)
    assert isinstance(tree.get("Regions"), nbt.TAGCompound)
    assert "MinecraftDataVersion" in tree


@pytest.mark.parametrize("size", [(5, 2, 3), (16, 16, 16)])
def test_metadata_fields_and_types(size):
    """SchematicMetadata.read 用到的字段必须存在，且 Long 类型不能写成 Int。"""
    model, n_blocks = make_model(size)
    tree = build_litematic_tree(model, mc_data_version=DV, name="t", author="a")
    meta = tree["Metadata"]

    assert meta["Name"] == "t"
    assert meta["Author"] == "a"
    assert isinstance(meta["TimeCreated"], nbt.TAGLong)
    assert isinstance(meta["TimeModified"], nbt.TAGLong)
    assert isinstance(meta["TotalVolume"], nbt.TAGInt)  # 真实样本是 Int
    assert isinstance(meta["TotalBlocks"], nbt.TAGInt)
    assert isinstance(meta["RegionCount"], nbt.TAGInt)
    assert meta["TotalVolume"] == size[0] * size[1] * size[2]
    assert meta["TotalBlocks"] == n_blocks
    assert (meta["EnclosingSize"]["x"], meta["EnclosingSize"]["y"], meta["EnclosingSize"]["z"]) == size


@pytest.mark.parametrize("size", [(5, 2, 3), (16, 16, 16), (7, 1, 9)])
def test_bit_width_and_long_count_follow_litematica(size):
    """bits 和 LongArray 长度必须按 Java 侧公式算，不能按 64 对齐。"""
    tree = build_litematic_tree(make_model(size)[0], mc_data_version=DV)
    region = tree["Regions"]["Region0"]
    palette_size = len(region["BlockStatePalette"])
    bits = get_required_bit_width(palette_size)
    volume = size[0] * size[1] * size[2]

    longs = [int(v) for v in region["BlockStates"]]
    assert len(longs) == tight_long_count(volume, bits), (
        f"palette={palette_size} bits={bits} volume={volume} "
        f"应该是 {tight_long_count(volume, bits)} 个 long，实际 {len(longs)}"
    )


@pytest.mark.parametrize("size", [(5, 2, 3), (3, 4, 5)])
def test_index_order_matches_java(size):
    """用 Java 侧的 getAt 独立解码，确认 position 与 YZX 索引一致。"""
    model, _ = make_model(size)
    tree = build_litematic_tree(model, mc_data_version=DV)
    region = tree["Regions"]["Region0"]
    palette = [e["Name"] for e in region["BlockStatePalette"]]
    bits = get_required_bit_width(len(palette))
    longs = [int(v) for v in region["BlockStates"]]

    for y in range(size[1]):
        for z in range(size[2]):
            for x in range(size[0]):
                idx = litematica_index(x, y, z, *size)
                got = palette[read_entry(longs, idx, bits)]
                want = model.get(x, y, z).name
                assert got == want, f"({x},{y},{z}) 期望 {want} 实际 {got}"


def test_palette_zero_is_air_and_unique():
    """Litematica 的 palette 是 List<BlockState>，重复项会导致 id 混淆。"""
    tree = build_litematic_tree(make_model((6, 6, 6))[0], mc_data_version=DV)
    names = [e["Name"] for e in tree["Regions"]["Region0"]["BlockStatePalette"]]
    assert names[0] == "minecraft:air"
    assert len(names) == len(set(names)), f"palette 有重复：{names}"


def test_properties_written_as_string_compound():
    m = VoxelModel((1, 1, 1))
    m.set(0, 0, 0, BlockState("minecraft:oak_door", {"facing": "north", "half": "lower"}))
    tree = build_litematic_tree(m, mc_data_version=DV)
    entry = [e for e in tree["Regions"]["Region0"]["BlockStatePalette"] if e["Name"] == "minecraft:oak_door"][0]
    props = entry["Properties"]
    assert props["facing"] == "north" and props["half"] == "lower"
    assert all(isinstance(v, nbt.TAGString) for v in props.values())
