"""Litematica 投影（.litematic）写出 / 读回。P0 格式。

结构按 Litematica 源码（LitematicaSchematic / SchematicMetadata）实现：

root Compound:
    MinecraftDataVersion : Int      ← 从 jar 的 version.json 读，不要猜
    Version              : Int      ← 7（实测 Litematica 0.29.1 / MC 26.3）
    SubVersion           : Int      ← 1
    Metadata : Compound {
        Name / Author / Description : String
        TimeCreated / TimeModified  : Long（毫秒）
        EnclosingSize : Compound { x, y, z : Int }
        RegionCount   : Int
        TotalVolume / TotalBlocks : Long
    }
    Regions : Compound { "<RegionName>" : Compound }

每个 Region:
    Position : Compound { x, y, z : Int }   ← 区域最小角（负坐标合法）
    Size     : Compound { x, y, z : Int }   ← 全正数
    BlockStatePalette : List<Compound>      ← 索引 0 必须是 minecraft:air
    BlockStates       : LongArray           ← YZX 紧密 bit pack
    TileEntities / Entities / PendingBlockTicks / PendingFluidTicks : List
"""

from __future__ import annotations

import time
from typing import List, Tuple

import numpy as np

from . import nbt
from .ir import AIR, BlockState, VoxelModel
from .yzx import bits_for, pack_bits, unpack_bits

LITEMATIC_VERSION = 7  # 实测：Litematica 0.29.1（MC 26.3）写的是 7 + SubVersion 1
LITEMATIC_SUBVERSION = 1

# 兜底值 = Minecraft 26.3 的 DataVersion（见 mcver.KNOWN_DATA_VERSIONS）。
# 真实导出走 mcver.resolve_data_version()，不靠这个常量。
FALLBACK_DATA_VERSION = 5023


# --------------------------------------------------------------------------
# region 切分
# --------------------------------------------------------------------------
def split_regions(size: Tuple[int, int, int], region_size: int) -> List[Tuple[int, int, int, int, int, int]]:
    """递归沿最长轴切块，返回 [(ox, oy, oz, sx, sy, sz), ...]。"""
    out: List[Tuple[int, int, int, int, int, int]] = []
    region_size = max(1, int(region_size))

    def rec(ox, oy, oz, sx, sy, sz):
        dims = (sx, sy, sz)
        axis = int(np.argmax(dims))
        if dims[axis] <= region_size:
            out.append((ox, oy, oz, sx, sy, sz))
            return
        half = (dims[axis] + 1) // 2
        n = (dims[axis] + half - 1) // half
        for i in range(n):
            lo = i * half
            hi = min(dims[axis], lo + half)
            if hi <= lo:
                continue
            if axis == 0:
                rec(ox + lo, oy, oz, hi - lo, sy, sz)
            elif axis == 1:
                rec(ox, oy + lo, oz, sx, hi - lo, sz)
            else:
                rec(ox, oy, oz + lo, sx, sy, hi - lo)

    rec(0, 0, 0, *size)
    return out


def _region_flat(model: VoxelModel, ox, oy, oz, sx, sy, sz) -> np.ndarray:
    """取区域内方块下标（局部 palette），YZX 顺序展平。

    blocks 的展平顺序是 index = x + z*sx + y*sx*sz，
    所以先 reshape 成 [y][z][x]，再按 region 切块，切完直接 ravel 就是区域内的 YZX。
    """
    gx, gy, gz = model.size
    arr = model.blocks.reshape(gy, gz, gx)  # [y][z][x]
    sub = arr[oy : oy + sy, oz : oz + sz, ox : ox + sx]  # [ly][lz][lx]
    return sub.ravel().astype(np.int64)


def _palette_compound(state: BlockState) -> nbt.TAGCompound:
    c = nbt.TAGCompound({"Name": nbt.TAGString(state.name)})
    if state.props:
        c["Properties"] = nbt.TAGCompound(
            {k: nbt.TAGString(str(v)) for k, v in sorted(state.props.items())}
        )
    return c


# --------------------------------------------------------------------------
# 写出
# --------------------------------------------------------------------------
def build_litematic_tree(
    model: VoxelModel,
    mc_data_version: int = FALLBACK_DATA_VERSION,
    region_size: int = 256,
    name: str = "voxel",
    author: str = "mc_voxel_export",
    description: str = "",
    version: int = LITEMATIC_VERSION,
    timestamp_ms: int = None,
) -> nbt.TAGCompound:
    """构造 litematic 的 NBT 树（不落盘，方便测试）。"""
    if mc_data_version is None:
        raise ValueError(
            "mc_data_version 不能为空：从 jar 的 version.json 读，读不到让用户手填，不许猜"
        )
    m = model.trimmed_palette()
    sx, sy, sz = m.size
    ts = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)

    regions = nbt.TAGCompound()
    total_volume = 0
    total_blocks = 0

    for i, (ox, oy, oz, rx, ry, rz) in enumerate(split_regions(m.size, region_size)):
        local_idx = _region_flat(m, ox, oy, oz, rx, ry, rz)

        # 区域自己的 palette：用到的全局下标 -> 局部下标
        used = np.unique(local_idx)
        remap = {int(g): new for new, g in enumerate(used)}
        local_palette = [m.palette[int(g)] for g in used]
        # 索引 0 必须是 air：如果没有空气，插一个到 0 位
        if not local_palette[0].is_air:
            local_palette.insert(0, BlockState(AIR))
            remap = {g: new + 1 for g, new in remap.items()}

        local_flat = np.zeros_like(local_idx)
        for g, new in remap.items():
            local_flat[local_idx == g] = new

        bits = bits_for(len(local_palette))
        longs = pack_bits(local_flat, bits)

        region = nbt.TAGCompound()
        region["Position"] = nbt.TAGCompound(
            {"x": nbt.TAGInt(ox), "y": nbt.TAGInt(oy), "z": nbt.TAGInt(oz)}
        )
        region["Size"] = nbt.TAGCompound(
            {"x": nbt.TAGInt(rx), "y": nbt.TAGInt(ry), "z": nbt.TAGInt(rz)}
        )
        region["BlockStatePalette"] = nbt.TAGList(
            [_palette_compound(s) for s in local_palette], element_tag=nbt.TAG_COMPOUND
        )
        region["BlockStates"] = nbt.TAGLongArray(longs)
        region["TileEntities"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
        region["Entities"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
        region["PendingBlockTicks"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)
        region["PendingFluidTicks"] = nbt.TAGList([], element_tag=nbt.TAG_COMPOUND)

        regions[f"Region{i}"] = region
        total_volume += rx * ry * rz
        air_mask = np.array([s.is_air for s in local_palette], dtype=bool)
        total_blocks += int((~air_mask[local_flat]).sum())

    root = nbt.TAGCompound()
    root["MinecraftDataVersion"] = nbt.TAGInt(int(mc_data_version))
    root["Version"] = nbt.TAGInt(int(version))
    root["SubVersion"] = nbt.TAGInt(int(LITEMATIC_SUBVERSION))
    # 统计字段全部在 Metadata 里（SchematicMetadata.write），不放根节点。
    # TotalVolume / TotalBlocks 实测是 Int（不是 Long），按真文件来。
    root["Metadata"] = nbt.TAGCompound(
        {
            "Name": nbt.TAGString(name),
            "Author": nbt.TAGString(author),
            "Description": nbt.TAGString(description),
            "TimeCreated": nbt.TAGLong(ts),
            "TimeModified": nbt.TAGLong(ts),
            "EnclosingSize": nbt.TAGCompound(
                {"x": nbt.TAGInt(sx), "y": nbt.TAGInt(sy), "z": nbt.TAGInt(sz)}
            ),
            "RegionCount": nbt.TAGInt(len(regions)),
            "TotalVolume": nbt.TAGInt(total_volume),
            "TotalBlocks": nbt.TAGInt(total_blocks),
        }
    )
    root["Regions"] = regions
    return root


def write_litematic(
    model: VoxelModel,
    path: str,
    mc_data_version: int,
    region_size: int = 256,
    name: str = "voxel",
    author: str = "mc_voxel_export",
    description: str = "",
) -> None:
    """P0：写出 .litematic。"""
    root = build_litematic_tree(
        model,
        mc_data_version=mc_data_version,
        region_size=region_size,
        name=name,
        author=author,
        description=description,
    )
    nbt.write_nbt(path, root)


# --------------------------------------------------------------------------
# 读回（反向导入用）
# --------------------------------------------------------------------------
def _read_int_compound(c: dict) -> Tuple[int, int, int]:
    return int(c["x"]), int(c["y"]), int(c["z"])


def read_litematic_tree(root: dict) -> VoxelModel:
    """把 litematic 的 NBT 树还原成 VoxelModel（多 region 自动合并）。"""
    regions = root["Regions"]
    boxes = []
    for _, region in regions.items():
        pos = _read_int_compound(region["Position"])
        siz = _read_int_compound(region["Size"])
        lo = tuple(pos[i] + min(0, siz[i]) for i in range(3))
        hi = tuple(pos[i] + max(0, siz[i]) for i in range(3))
        boxes.append((lo, hi, region))

    gmin = [min(b[0][i] for b in boxes) for i in range(3)]
    gmax = [max(b[1][i] for b in boxes) for i in range(3)]
    size = tuple(gmax[i] - gmin[i] for i in range(3))

    model = VoxelModel(size)
    for lo, hi, region in boxes:
        sx, sy, sz = (hi[i] - lo[i] for i in range(3))
        palette = [
            BlockState(
                str(entry.get("Name", AIR)),
                {str(k): str(v) for k, v in entry.get("Properties", {}).items()},
            )
            for entry in region["BlockStatePalette"]
        ]
        bits = bits_for(len(palette))
        flat = unpack_bits(region["BlockStates"], sx * sy * sz, bits)
        local = flat.reshape(sy, sz, sx)  # [ly][lz][lx]
        ox = lo[0] - gmin[0]
        oy = lo[1] - gmin[1]
        oz = lo[2] - gmin[2]
        arr = model.blocks.reshape(model.size[1], model.size[2], model.size[0])  # [y][z][x]
        # 先把局部 palette 映射到全局 palette
        gidx = np.array([model.add_state(s) for s in palette], dtype=np.int64)
        arr[oy : oy + sy, oz : oz + sz, ox : ox + sx] = gidx[local]
    return model


def read_litematic(path: str) -> VoxelModel:
    return read_litematic_tree(nbt.read_nbt(path))
