"""32³ Lab 查找表 + 最近方块映射。

不做 KD-tree，不依赖 scipy。构建一次，查询 O(LUT 大小)。
"""

from __future__ import annotations

import fnmatch
import re

import numpy as np

from .color import delta_e76, srgb_to_lab

LUT_N = 32
NO_MATCH = -1

DEFAULT_NAMESPACE = "minecraft"

_SPLIT = re.compile(r"[,;，；\s]+")


def _qualify(name: str, default_ns: str = DEFAULT_NAMESPACE) -> str:
    """补全命名空间：glass -> minecraft:glass；已带前缀的不动。"""
    name = name.strip()
    if not name:
        return ""
    if ":" in name or "*" in name:
        return name
    return f"{default_ns}:{name}"


def parse_block_filter(text: str, known=None, default_ns: str = DEFAULT_NAMESPACE) -> list:
    """把面板里的文本框解析成方块名列表。

    分隔符：逗号 / 分号 / 空白 / 换行（中英文标点都认）。
    不带 `minecraft:` 前缀自动补；支持 `*` 通配，给了 known（全部候选方块名）
    就把通配展开成实际名字，没给就原样返回给调用方。

    例：``"glass, water; *wool*"`` -> ``["minecraft:glass", "minecraft:water",
    "minecraft:white_wool", ...]``
    """
    out = []
    seen = set()
    for raw in _SPLIT.split(text or ""):
        token = _qualify(raw, default_ns)
        if not token:
            continue
        if "*" in token or "?" in token:
            if known is None:
                names = [token]
            else:
                names = [n for n in known if fnmatch.fnmatchcase(n, token)]
        else:
            names = [token]
        for n in names:
            if n not in seen:
                seen.add(n)
                out.append(n)
    return out


def filter_tokens(text: str) -> list:
    """把过滤文本切回「用户自己写的 token」（不补前缀、不展开通配）。

    和 parse_block_filter 的区别：那个是给匹配用的，返回一堆 `minecraft:xxx`；
    这个是给面板显示和增删用的，原样保留用户敲的内容。面板里 200 多个方块名
    塞进单行输入框看不清，只能靠这个回读。
    """
    return [t for t in (raw.strip() for raw in _SPLIT.split(text or "")) if t]


def merge_filter(text: str, name: str) -> str:
    """往过滤文本里追加一个方块名（重复的不加，顺带把分隔符规整成 ", "）。"""
    name = (name or "").strip()
    tokens = filter_tokens(text)
    if not name:
        return ", ".join(tokens)
    low = name.lower()
    if all(t.lower() != low for t in tokens):
        tokens.append(name)
    return ", ".join(tokens)


def drop_filter(text: str, name: str) -> str:
    """从过滤文本里去掉一个方块名（大小写不敏感）。"""
    low = (name or "").strip().lower()
    if not low:
        return ", ".join(filter_tokens(text))
    return ", ".join(t for t in filter_tokens(text) if t.lower() != low)


class BlockPalette:
    """把颜色映射到方块名。

    colors : {block_name: (r,g,b)}
    allow  : 只允许这些方块（None 表示不限）
    deny   : 排除这些方块（如玻璃、水）
    """

    def __init__(self, colors: dict, allow=None, deny=None):
        if not colors:
            raise ValueError("色板不能为空")
        allow_set = set(allow) if allow is not None else None
        deny_set = set(deny) if deny is not None else set()

        names, rgbs = [], []
        for name, rgb in colors.items():
            if allow_set is not None and name not in allow_set:
                continue
            if name in deny_set:
                continue
            names.append(name)
            rgbs.append(tuple(float(v) for v in rgb))
        if not names:
            raise ValueError("过滤后色板为空，检查 allow / deny")

        self.names = names
        self.rgbs = np.asarray(rgbs, dtype=np.float64)  # (N,3)
        self.labs = srgb_to_lab(self.rgbs)  # (N,3)
        self.lut = self._build_lut()
        self._fill_empty()

    # ---- LUT ----
    def _bin_centers(self) -> np.ndarray:
        """32³ 个格子中心的 Lab。"""
        l = (np.arange(LUT_N) + 0.5) / LUT_N * 100.0
        ab = (np.arange(LUT_N) + 0.5) / LUT_N * 256.0 - 128.0
        gL, ga, gb = np.meshgrid(l, ab, ab, indexing="ij")
        return np.stack([gL, ga, gb], axis=-1).reshape(-1, 3)

    def _build_lut(self) -> np.ndarray:
        centers = self._bin_centers()
        out = np.full(len(centers), NO_MATCH, dtype=np.int16)
        step = 2048
        for start in range(0, len(centers), step):
            chunk = centers[start : start + step]
            d = delta_e76(chunk[:, None, :], self.labs[None, :, :])  # (chunk,N)
            out[start : start + step] = np.argmin(d, axis=1).astype(np.int16)
        return out.reshape(LUT_N, LUT_N, LUT_N)

    def _fill_empty(self) -> None:
        """空格用 3×3×3 邻域填充（正常构建不会留空，兜底用）。"""
        if (self.lut != NO_MATCH).all():
            return
        lut = self.lut.copy()
        for _ in range(4):
            empty = lut == NO_MATCH
            if not empty.any():
                break
            best = np.full_like(lut, NO_MATCH)
            for axis in range(3):
                for shift in (-1, 1):
                    cand = np.roll(lut, shift, axis=axis)
                    best = np.where((best == NO_MATCH) & (cand != NO_MATCH), cand, best)
            lut = np.where(empty, best, lut)
        self.lut = lut

    # ---- 查询 ----
    @staticmethod
    def _indices(lab) -> tuple:
        L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
        i = np.clip((L / 100.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        j = np.clip(((a + 128.0) / 256.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        k = np.clip(((b + 128.0) / 256.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        return i, j, k

    def nearest_index(self, rgb):
        lab = srgb_to_lab(np.asarray(rgb, dtype=np.float64))
        i, j, k = self._indices(lab)
        return int(self.lut[i, j, k])

    def nearest(self, rgb) -> str:
        return self.names[self.nearest_index(rgb)]

    def nearest_batch(self, rgbs) -> list:
        """(K,3) 颜色 -> [block_name, …]"""
        arr = np.asarray(rgbs, dtype=np.float64)
        flat = arr.reshape(-1, 3)
        labs = srgb_to_lab(flat)
        i, j, k = self._indices(labs)
        idx = self.lut[i, j, k]
        return [self.names[int(v)] for v in idx]

    # ---- 兜底 ----
    def fallback(self, start: int = 0) -> str:
        return self.names[start % len(self.names)]

    # ---- 派生 ----
    def colors_dict(self) -> dict:
        """{name: (r,g,b)}，方便构造派生色板。"""
        return {n: tuple(float(v) for v in rgb) for n, rgb in zip(self.names, self.rgbs)}

    def color_of(self, name):
        """名字 -> 平均色；不认识返回 None。"""
        try:
            i = self.names.index(name)
        except ValueError:
            return None
        return tuple(int(round(v)) for v in self.rgbs[i])

    def without(self, exclude) -> "BlockPalette":
        """去掉若干方块后的新色板。用来做"重力方块不许用"的替代查询。"""
        ban = set(exclude)
        colors = {n: rgb for n, rgb in self.colors_dict().items() if n not in ban}
        if not colors:
            return self
        return BlockPalette(colors)


def load_fallback_palette(path: str = None) -> dict:
    """读 data/fallback_palette.json。"""
    import json
    from pathlib import Path

    if path is None:
        path = Path(__file__).resolve().parent.parent / "data" / "fallback_palette.json"
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for name, hexval in raw.items():
        h = hexval.lstrip("#")
        out[name] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return out
