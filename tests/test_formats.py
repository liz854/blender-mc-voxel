import numpy as np
import pytest

from core.ir import BlockState, VoxelModel
from core.schem import read_schem, read_varint, write_schem, write_varint


def test_varint_roundtrip():
    for n in (0, 1, 127, 128, 300, 16383, 16384, 1 << 20):
        raw = write_varint(n)
        val, pos = read_varint(raw)
        assert val == n
        assert pos == len(raw)


def test_varint_negative_rejected():
    with pytest.raises(ValueError):
        write_varint(-1)


def test_schem_roundtrip(tmp_path):
    m = VoxelModel((3, 2, 2))
    m.set(0, 0, 0, BlockState("minecraft:stone"))
    m.set(2, 1, 1, BlockState("minecraft:oak_log", {"axis": "y"}))
    path = tmp_path / "x.schem"
    write_schem(m, str(path), 4325)

    back = read_schem(str(path))
    assert back.size == (3, 2, 2)
    assert back.total_blocks == 2
    names = {(s.name, tuple(sorted(s.props.items()))) for s in back.palette}
    assert ("minecraft:stone", ()) in names
    assert ("minecraft:oak_log", (("axis", "y"),)) in names


def test_schem_has_required_fields(tmp_path):
    from core import nbt

    m = VoxelModel((1, 1, 1))
    m.set(0, 0, 0, BlockState("minecraft:stone"))
    path = tmp_path / "y.schem"
    write_schem(m, str(path), 4325)
    root = nbt.read_nbt(str(path))
    for key in (
        "Version", "DataVersion", "Width", "Height", "Length",
        "Offset", "PaletteMax", "Palette", "BlockData", "BlockEntities",
    ):
        assert key in root
    assert root["Version"] == 2
    assert root["Width"] == 1
    # 1×1×1 全 stone：trimmed 后没有空气，PaletteMax 就是 1（schem 允许省略空气）
    assert root["PaletteMax"] == 1
    assert "minecraft:stone" in root["Palette"]


