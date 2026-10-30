"""Golden sample 回归测试。

样本来源：真实游戏里存出来的
游戏内导出的真实投影（本机放在启动器的 `versions/<版本>/schematics/` 下）
（Minecraft 26.3 + Fabric，Litematica 0.29.1，2×2×2，8 种方块含楼梯）

它把之前靠猜的几件事全部钉死了：
  - Version = 7，SubVersion = 1
  - Metadata.TotalVolume / TotalBlocks 是 Int，不是 Long
  - BlockStates 是 tight 打包，bits = 4（palette 9 条）
  - 区域 Size 可以是负数（(2, -2, -2)）
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from core import nbt
from core.ir import BlockState
from core.litematic import (
    LITEMATIC_SUBVERSION,
    LITEMATIC_VERSION,
    build_litematic_tree,
    read_litematic,
    read_litematic_tree,
)
from core.yzx import bits_for, pack_bits, unpack_bits

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "golden_26_3.litematic")


@pytest.fixture(scope="module")
def golden_root():
    return nbt.read_nbt(GOLDEN)


def test_golden_exists():
    assert os.path.exists(GOLDEN), f"缺 golden sample：{GOLDEN}"


def test_golden_header(golden_root):
    assert int(golden_root["MinecraftDataVersion"]) == 5023
    assert int(golden_root["Version"]) == 7
    assert int(golden_root["SubVersion"]) == 1
    # 我们写出的版本号必须跟真文件一致
    assert LITEMATIC_VERSION == 7
    assert LITEMATIC_SUBVERSION == 1


def test_golden_metadata_types(golden_root):
    """统计字段是 Int 类型，写成 Long 虽然能读，但没必要跟真文件不一致。"""
    meta = golden_root["Metadata"]
    assert isinstance(meta["TotalVolume"], nbt.TAGInt)
    assert isinstance(meta["TotalBlocks"], nbt.TAGInt)
    assert isinstance(meta["RegionCount"], nbt.TAGInt)
    assert isinstance(meta["TimeCreated"], nbt.TAGLong)


def test_golden_region_shape(golden_root):
    region = golden_root["Regions"]["Unnamed"]
    assert (region["Position"]["x"], region["Position"]["y"], region["Position"]["z"]) == (0, 1, 1)
    assert (region["Size"]["x"], region["Size"]["y"], region["Size"]["z"]) == (2, -2, -2)
    # 全是空的也要写出来
    for key in ("TileEntities", "Entities", "PendingBlockTicks", "PendingFluidTicks"):
        assert key in region and len(region[key]) == 0


def test_golden_bits_and_packing(golden_root):
    region = golden_root["Regions"]["Unnamed"]
    palette = region["BlockStatePalette"]
    assert len(palette) == 9
    assert bits_for(len(palette)) == 4
    longs = [int(v) for v in region["BlockStates"]]
    assert len(longs) == 1
    # 真人摆的 2×2×2，8 个格子正好是 palette 1..8
    assert list(unpack_bits(longs, 8, 4)) == [1, 2, 3, 4, 5, 6, 7, 8]
    # 我们的编码器必须逐位复现
    assert pack_bits(list(unpack_bits(longs, 8, 4)), 4) == longs


def test_golden_reads_into_model():
    m = read_litematic(GOLDEN)
    assert m.size == (2, 2, 2)
    assert m.total_blocks == 8
    names = {s.name for s in m.palette}
    assert "minecraft:polished_granite" in names
    # 真样本里有带朝向/半砖等属性的方块，属性必须完整保留（不能被丢掉）
    with_props = [s for s in m.palette if s.props]
    assert with_props, "样本里应当有带属性的方块"
    assert all(len(s.props) >= 3 for s in with_props), \
        f"属性数对不上：{[(s.name, len(s.props)) for s in with_props]}"


def test_reencode_matches_golden_bytes(golden_root):
    """把 golden 读成模型再用我们的写出器重编码，BlockStates 与 palette 必须一模一样。"""
    m = read_litematic(GOLDEN)
    tree = build_litematic_tree(
        m, mc_data_version=5023, name="test", author="lizliz_666", timestamp_ms=1792585319012
    )
    ours = tree["Regions"]["Region0"]
    theirs = golden_root["Regions"]["Unnamed"]

    assert [e["Name"] for e in ours["BlockStatePalette"]] == [
        e["Name"] for e in theirs["BlockStatePalette"]
    ]
    assert [dict(e.get("Properties", {})) for e in ours["BlockStatePalette"]] == [
        dict(e.get("Properties", {})) for e in theirs["BlockStatePalette"]
    ]
    assert [int(v) for v in ours["BlockStates"]] == [int(v) for v in theirs["BlockStates"]]


def test_our_tree_version_fields():
    m = read_litematic(GOLDEN)
    tree = build_litematic_tree(m, mc_data_version=5023)
    assert int(tree["Version"]) == 7
    assert int(tree["SubVersion"]) == 1
    assert isinstance(tree["Metadata"]["TotalBlocks"], nbt.TAGInt)


def test_roundtrip_through_our_writer(tmp_path):
    """写出 -> 读回 -> 再写出，数据不能漂移。"""
    m = read_litematic(GOLDEN)
    out = tmp_path / "ours.litematic"
    from core.litematic import write_litematic

    write_litematic(m, str(out), mc_data_version=5023)
    back = read_litematic(str(out))
    assert back.size == m.size
    assert back.total_blocks == m.total_blocks
    for y in range(m.size[1]):
        for z in range(m.size[2]):
            for x in range(m.size[0]):
                assert back.get(x, y, z) == m.get(x, y, z)
