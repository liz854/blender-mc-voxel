"""结构方块 .nbt（structure block）。≤ 48³，超出明确提示。P2 格式。

root:
    DataVersion : Int
    size        : List<Int> [sx, sy, sz]
    palette     : List<Compound> { Name, Properties? }
    blocks      : List<Compound> { state: Int, pos: List<Int> [x,y,z] }
    entities    : List<Compound>
"""

from __future__ import annotations

from . import nbt
from .ir import VoxelModel
from .litematic import _palette_compound
from .yzx import yzx_to_xyz

LIMIT = 48


class StructureTooBig(ValueError):
    pass


def write_structure_nbt(model: VoxelModel, path: str, data_version: int) -> None:
    sx, sy, sz = model.size
    if max(model.size) > LIMIT:
        raise StructureTooBig(
            f"结构方块上限是 {LIMIT}³，当前 {model.size}。请改用 .litematic 或 .schem。"
        )
    m = model.trimmed_palette()

    blocks = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
    for flat in m.non_air_indices:
        x, y, z = yzx_to_xyz(int(flat), *m.size)
        entry = nbt.TAGCompound()
        entry["state"] = nbt.TAGInt(int(m.blocks[flat]))
        entry["pos"] = nbt.TAGList(
            [nbt.TAGInt(x), nbt.TAGInt(y), nbt.TAGInt(z)], element_tag=nbt.TAG_INT
        )
        blocks.append(entry)

    root = nbt.TAGCompound()
    root["DataVersion"] = nbt.TAGInt(int(data_version))
    root["size"] = nbt.TAGList(
        [nbt.TAGInt(sx), nbt.TAGInt(sy), nbt.TAGInt(sz)], element_tag=nbt.TAG_INT
    )
    root["palette"] = nbt.TAGList(
        [_palette_compound(s) for s in m.palette], element_tag=nbt.TAG_COMPOUND
    )
    root["blocks"] = blocks
    root["entities"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
    nbt.write_nbt(path, root)
