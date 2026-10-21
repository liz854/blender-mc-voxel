"""中间表示（IR）。字段不得增删改名。

VoxelModel:
    size    : (sx, sy, sz) 体素尺寸
    palette : [BlockState]，索引 0 约定为空气
    blocks  : np.int64[(sx*sy*sz,)]，palette 下标，YZX 顺序

约定：1 体素 = 1 方块 = 1 米。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

AIR = "minecraft:air"


@dataclass
class BlockState:
    """一个方块状态。props 里所有值都必须是字符串（MC 的规矩）。"""

    name: str
    props: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.name.startswith("minecraft:"):
            self.name = "minecraft:" + self.name
        for k, v in list(self.props.items()):
            if not isinstance(v, str):
                self.props[k] = str(v).lower() if isinstance(v, bool) else str(v)

    @property
    def is_air(self) -> bool:
        return self.name == AIR

    def key(self) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
        """可哈希签名，用于 palette 去重。"""
        return (self.name, tuple(sorted(self.props.items())))


class VoxelModel:
    """体素模型。所有导出函数只吃这个对象。"""

    def __init__(self, size, palette=None):
        size = tuple(int(v) for v in size)
        if len(size) != 3 or min(size) <= 0:
            raise ValueError(f"size 必须是三个正整数，收到 {size}")
        self.size: Tuple[int, int, int] = size
        self.palette: List[BlockState] = list(palette) if palette else [BlockState(AIR)]
        self.blocks: np.ndarray = np.zeros(self.volume, dtype=np.int64)

    # ---- 尺寸 ----
    @property
    def volume(self) -> int:
        sx, sy, sz = self.size
        return sx * sy * sz

    # ---- palette ----
    def add_state(self, state: BlockState) -> int:
        k = state.key()
        for i, s in enumerate(self.palette):
            if s.key() == k:
                return i
        self.palette.append(state)
        return len(self.palette) - 1

    def index_of(self, name: str) -> int:
        for i, s in enumerate(self.palette):
            if s.name == name:
                return i
        raise KeyError(name)

    # ---- 读写 ----
    def _idx(self, x, y, z) -> int:
        from .yzx import yzx_index

        return yzx_index(x, y, z, *self.size)

    def get(self, x, y, z) -> BlockState:
        return self.palette[int(self.blocks[self._idx(x, y, z)])]

    def set(self, x, y, z, state) -> None:
        if isinstance(state, str):
            state = BlockState(state)
        self.blocks[self._idx(x, y, z)] = self.add_state(state)

    def fill_from(self, mask, state) -> None:
        """用一个 bool 掩码（同尺寸，YZX 展平）批量写入。"""
        m = np.asarray(mask, dtype=bool).reshape(-1)
        if m.size != self.volume:
            raise ValueError(f"掩码长度 {m.size} != 体积 {self.volume}")
        if not m.any():
            return
        i = self.add_state(state if isinstance(state, BlockState) else BlockState(state))
        self.blocks[m] = i

    # ---- 统计 ----
    @property
    def total_blocks(self) -> int:
        """非空气方块数。"""
        air = np.array([s.is_air for s in self.palette], dtype=bool)
        return int((~air[self.blocks]).sum())

    @property
    def non_air_indices(self) -> np.ndarray:
        air = np.array([s.is_air for s in self.palette], dtype=bool)
        return np.nonzero(~air[self.blocks])[0]

    def trimmed_palette(self) -> "VoxelModel":
        """去掉没用到的 palette 条目，重排下标。导出前调用。"""
        used = np.unique(self.blocks)
        # 空气（如果有）永远留在 0 位
        air_idx = [i for i in used if self.palette[int(i)].is_air]
        others = [int(i) for i in used if not self.palette[int(i)].is_air]
        order = air_idx[:1] + others
        remap = {int(old): new for new, old in enumerate(order)}
        out = VoxelModel(self.size, [self.palette[i] for i in order])
        flat = np.zeros(self.volume, dtype=np.int64)
        for old, new in remap.items():
            flat[self.blocks == old] = new
        out.blocks = flat
        return out
