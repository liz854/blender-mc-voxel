"""Sponge Schematic v2（.schem，WorldEdit 用）。P2 格式。

root:
    Version     : Int      = 2
    DataVersion : Int
    Width / Height / Length : Short
    Offset      : IntArray [0, 0, 0]
    PaletteMax  : Int
    Palette     : Compound { "minecraft:stone": Int }
    BlockData   : ByteArray（VarInt，YZX 顺序）
    BlockEntities : List<Compound>
    Metadata    : Compound（可选）
"""

from __future__ import annotations

import numpy as np

from . import nbt
from .ir import AIR, BlockState, VoxelModel
from .litematic import _palette_compound
from .yzx import yzx_index


def write_varint(n: int) -> bytes:
    """标准 VarInt（schem 用）。"""
    n = int(n)
    if n < 0:
        raise ValueError("VarInt 不接受负数")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def read_varint(buf: bytes, pos: int = 0):
    """返回 (value, new_pos)。"""
    value = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("VarInt 数据被截断")
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7
        if shift > 35:
            raise ValueError("VarInt 过长")


def _block_key(state: BlockState) -> str:
    if not state.props:
        return state.name
    inner = ",".join(f"{k}={v}" for k, v in sorted(state.props.items()))
    return f"{state.name}[{inner}]"


def build_schem_tree(model: VoxelModel, data_version: int) -> nbt.TAGCompound:
    m = model.trimmed_palette()
    sx, sy, sz = m.size

    # palette：schem 的 key 是带属性的完整状态串
    keys = [_block_key(s) for s in m.palette]
    palette = {}
    for i, k in enumerate(keys):
        palette.setdefault(k, i)
    # 重排成连续的 0..n-1
    ordered = sorted(palette.items(), key=lambda kv: kv[1])
    remap = {old: new for new, (_, old) in enumerate(ordered)}
    global_to_new = np.array([remap[i] for i in range(len(m.palette))], dtype=np.int64)

    data = bytearray()
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                idx = int(m.blocks[yzx_index(x, y, z, sx, sy, sz)])
                data += write_varint(int(global_to_new[idx]))

    root = nbt.TAGCompound()
    root["Version"] = nbt.TAGInt(2)
    root["DataVersion"] = nbt.TAGInt(int(data_version))
    root["Width"] = nbt.TAGShort(sx)
    root["Height"] = nbt.TAGShort(sy)
    root["Length"] = nbt.TAGShort(sz)
    root["Offset"] = nbt.TAGIntArray([0, 0, 0])
    root["PaletteMax"] = nbt.TAGInt(len(ordered))
    root["Palette"] = nbt.TAGCompound({k: nbt.TAGInt(v) for k, v in ordered})
    root["BlockData"] = nbt.TAGByteArray(bytes(data))
    root["BlockEntities"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
    return root


def write_schem(model: VoxelModel, path: str, data_version: int) -> None:
    nbt.write_nbt(path, build_schem_tree(model, data_version))


def read_schem(path: str) -> VoxelModel:
    root = nbt.read_nbt(path)
    sx, sy, sz = int(root["Width"]), int(root["Height"]), int(root["Length"])
    palette = [None] * int(root["PaletteMax"])
    for key, idx in root["Palette"].items():
        name, _, rest = str(key).partition("[")
        props = {}
        if rest:
            for pair in rest.rstrip("]").split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    props[k] = v
        palette[int(idx)] = BlockState(name, props)
    model = VoxelModel((sx, sy, sz))
    for state in palette:
        model.add_state(state if state is not None else BlockState(AIR))

    raw = bytes(root["BlockData"])
    pos = 0
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                val, pos = read_varint(raw, pos)
                model.blocks[yzx_index(x, y, z, sx, sy, sz)] = val
    return model
