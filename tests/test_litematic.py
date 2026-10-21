import numpy as np
import pytest

from core import nbt
from core.ir import BlockState, VoxelModel
from core.litematic import (
    LITEMATIC_VERSION,
    build_litematic_tree,
    read_litematic,
    split_regions,
    write_litematic,
)

DV = 4325


def stone_cube(n=2):
    m = VoxelModel((n, n, n))
    mask = np.ones((n, n, n), dtype=bool)
    m.fill_from(mask, BlockState("minecraft:stone"))
    return m


def test_2x2x2_stone_fields():
    """方案里的硬断言：RegionCount=1, TotalBlocks=8, palette=2, BlockStates=1。"""
    tree = build_litematic_tree(stone_cube(2), mc_data_version=DV)
    assert tree["MinecraftDataVersion"] == DV
    assert tree["Version"] == LITEMATIC_VERSION == 7
    assert tree["Metadata"]["RegionCount"] == 1
    assert tree["Metadata"]["TotalVolume"] == 8
    assert tree["Metadata"]["TotalBlocks"] == 8

    region = tree["Regions"]["Region0"]
    assert len(region["BlockStatePalette"]) == 2
    assert region["BlockStatePalette"][0]["Name"] == "minecraft:air"
    assert region["BlockStatePalette"][1]["Name"] == "minecraft:stone"
    assert len(region["BlockStates"]) == 1  # 8 * 2 bit = 16 bit -> 1 long
    assert region["Size"]["x"] == 2 and region["Size"]["y"] == 2 and region["Size"]["z"] == 2
    assert region["Position"]["x"] == 0


def test_enclosing_size():
    tree = build_litematic_tree(stone_cube(3), mc_data_version=DV)
    assert (
        tree["Metadata"]["EnclosingSize"]["x"],
        tree["Metadata"]["EnclosingSize"]["y"],
        tree["Metadata"]["EnclosingSize"]["z"],
    ) == (3, 3, 3)


def test_palette_zero_is_air_when_model_full():
    """模型全是方块时，区域 palette 也要自己插一个 air 到 0 位。"""
    tree = build_litematic_tree(stone_cube(2), mc_data_version=DV)
    palette = tree["Regions"]["Region0"]["BlockStatePalette"]
    assert palette[0]["Name"] == "minecraft:air"


def test_file_roundtrip(tmp_path):
    model = stone_cube(3)
    path = tmp_path / "cube.litematic"
    write_litematic(model, str(path), mc_data_version=DV)
    assert path.read_bytes()[:2] == b"\x1f\x8b"

    back = read_litematic(str(path))
    assert back.size == (3, 3, 3)
    assert back.total_blocks == 27
    assert {s.name for s in back.palette} >= {"minecraft:stone"}


def test_multiple_regions():
    tree = build_litematic_tree(stone_cube(2), mc_data_version=DV, region_size=1)
    assert tree["Metadata"]["RegionCount"] == 8
    assert tree["Metadata"]["TotalBlocks"] == 8
    for name, region in tree["Regions"].items():
        assert region["Size"]["x"] == 1
    positions = {
        (r["Position"]["x"], r["Position"]["y"], r["Position"]["z"])
        for r in tree["Regions"].values()
    }
    assert len(positions) == 8


def test_split_regions_longest_axis():
    boxes = split_regions((10, 3, 3), 4)
    # x 方向 10 沿最长轴递归切到 <= 4，其他轴不动
    assert all(b[3] <= 4 for b in boxes)
    assert all(b[0] + b[3] <= 10 for b in boxes)
    assert sum(b[3] * b[4] * b[5] for b in boxes) == 10 * 3 * 3
    xs = sorted((b[0], b[3]) for b in boxes)
    assert xs[0][0] == 0
    # 无缝拼接
    for (s1, w1), (s2, _w2) in zip(xs, xs[1:]):
        assert s1 + w1 == s2


def test_block_properties_are_strings():
    m = VoxelModel((1, 1, 1))
    m.set(0, 0, 0, BlockState("minecraft:oak_stairs", {"facing": "north", "half": "bottom"}))
    tree = build_litematic_tree(m, mc_data_version=DV)
    palette = tree["Regions"]["Region0"]["BlockStatePalette"]
    entry = [p for p in palette if p["Name"] == "minecraft:oak_stairs"][0]
    assert entry["Properties"]["facing"] == "north"
    assert isinstance(entry["Properties"]["half"], nbt.TAGString)


def test_data_version_required():
    with pytest.raises(ValueError):
        build_litematic_tree(stone_cube(2), mc_data_version=None)


def test_air_voxels_not_counted():
    m = VoxelModel((2, 1, 1))
    m.set(0, 0, 0, BlockState("minecraft:stone"))
    tree = build_litematic_tree(m, mc_data_version=DV)
    assert tree["Metadata"]["TotalBlocks"] == 1
    assert tree["Metadata"]["TotalVolume"] == 2


def test_non_cubic_positions_preserved(tmp_path):
    """非立方体尺寸下，每块的位置必须原样还原（立方面掩盖过轴序 bug）。"""
    size = (5, 2, 3)
    m = VoxelModel(size)
    marks = {
        (0, 0, 0): "minecraft:stone",
        (4, 1, 2): "minecraft:gold_block",
        (2, 1, 0): "minecraft:red_wool",
        (0, 1, 2): "minecraft:blue_wool",
    }
    for pos, name in marks.items():
        m.set(pos[0], pos[1], pos[2], BlockState(name))

    path = tmp_path / "marks.litematic"
    write_litematic(m, str(path), mc_data_version=DV)
    back = read_litematic(str(path))
    assert back.size == size
    for pos, name in marks.items():
        assert back.get(*pos).name == name, f"{pos} 位置错了：{back.get(*pos).name}"
    assert back.total_blocks == len(marks)


def test_non_cubic_flat_order(tmp_path):
    """直接检查 BlockStates 里的 YZX 顺序，不依赖读回代码。"""
    from core.yzx import bits_for, unpack_bits, yzx_index

    size = (3, 2, 4)
    m = VoxelModel(size)
    m.set(2, 1, 3, BlockState("minecraft:diamond_block"))

    tree = build_litematic_tree(m, mc_data_version=DV)
    region = tree["Regions"]["Region0"]
    palette = region["BlockStatePalette"]
    bits = bits_for(len(palette))
    flat = unpack_bits(region["BlockStates"], 3 * 2 * 4, bits)
    want = yzx_index(2, 1, 3, *size)
    assert palette[int(flat[want])]["Name"] == "minecraft:diamond_block"
    non_air = [i for i, v in enumerate(flat) if palette[int(v)]["Name"] != "minecraft:air"]
    assert non_air == [want]
