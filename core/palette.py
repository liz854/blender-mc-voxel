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

# 目标纹理粗糙度量化档位：std_target 落进哪个区间就用对应中心的 LUT。
# 方块侧 std：纯色 ~0-5，石头类噪点 20-30，花纹理（上限剪到 42）35+。
# 模型侧 std：平涂的面 ~0，渐变/噪点区域中等，混色边界高。
TEXTURE_BIN_EDGES = (8.0, 20.0, 35.0)
TEXTURE_BIN_CENTERS = (4.0, 14.0, 27.5, 47.5)

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


def _fill_empty(lut: np.ndarray) -> None:
    """空格用 3×3×3 邻域填充（正常构建不会留空，兜底用）。原地修改。"""
    if (lut != NO_MATCH).all():
        return
    for _ in range(4):
        empty = lut == NO_MATCH
        if not empty.any():
            break
        best = np.full_like(lut, NO_MATCH)
        for axis in range(3):
            for shift in (-1, 1):
                cand = np.roll(lut, shift, axis=axis)
                best = np.where((best == NO_MATCH) & (cand != NO_MATCH), cand, best)
        lut[...] = np.where(empty, best, lut)


def texture_bin(std: float) -> int:
    """目标 std -> 档位序号（0..len(CENTERS)-1）。"""
    return int(np.searchsorted(TEXTURE_BIN_EDGES, float(std), side="right"))


class BlockPalette:
    """把颜色映射到方块名。

    colors : {block_name: (r,g,b)}
    allow  : 只允许这些方块（None 表示不限）
    deny   : 排除这些方块（如玻璃、水）
    stds   : {block_name: 纹理标准差}。给了它，查询时就可以带上目标粗糙度，
             成本变成 ΔE76 + texture_weight × |std_方块 - std_目标|，
             "平的面配平的方块、有纹理的面配有纹理的方块"。
             不带目标粗糙度的查询走纯颜色 LUT，行为和以前完全一样。
    texture_weight : 纹理项权重。1.0 = std 差满档（约 44）抵得上一个很大的色差。
    """

    def __init__(
        self,
        colors: dict,
        allow=None,
        deny=None,
        stds: dict = None,
        texture_weight: float = 1.0,
    ):
        if not colors:
            raise ValueError("色板不能为空")
        allow_set = set(allow) if allow is not None else None
        deny_set = set(deny) if deny is not None else set()

        names, rgbs, std_vals = [], [], []
        for name, rgb in colors.items():
            if allow_set is not None and name not in allow_set:
                continue
            if name in deny_set:
                continue
            names.append(name)
            rgbs.append(tuple(float(v) for v in rgb))
            std_vals.append(float(stds.get(name, 0.0)) if stds else 0.0)
        if not names:
            raise ValueError("过滤后色板为空，检查 allow / deny")

        self.names = names
        self.rgbs = np.asarray(rgbs, dtype=np.float64)  # (N,3)
        self.labs = srgb_to_lab(self.rgbs)  # (N,3)
        self.texture_weight = float(texture_weight)
        self.stds = np.asarray(std_vals, dtype=np.float64)  # (N,)
        # 色板里至少要有一平一花才值得开纹理匹配
        self.has_texture_info = bool(self.stds.size) and (
            float(self.stds.max()) - float(self.stds.min()) > 1.0
        )
        # LUT 按需构建：build_voxel_model 走精确 ΔE 根本不用 LUT，
        # 而建一张 32³ LUT 要 138 ms（带纹理 4 张共 725 ms）。UI 每次
        # 导出/预览都会新建色板，提前建就是每次白花 0.7 秒。
        self._lut = None
        self._luts = None

    # ---- LUT（按需构建） ----
    @property
    def lut(self) -> np.ndarray:
        if self._lut is None:
            lut = self._build_lut()
            _fill_empty(lut)
            self._lut = lut
        return self._lut

    @property
    def luts(self):
        """每档目标粗糙度一张 LUT；色板没有纹理信息时返回 None。"""
        if not self.has_texture_info:
            return None
        if self._luts is None:
            luts = self._build_std_luts()
            for lut in luts:
                _fill_empty(lut)
            self._luts = luts
        return self._luts

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

    def _build_std_luts(self) -> list:
        """每档目标粗糙度一张 LUT。ΔE 矩阵只算一次，各档加纹理项后取 argmin。"""
        centers = self._bin_centers()
        luts = []
        step = 2048
        w = self.texture_weight
        for target in TEXTURE_BIN_CENTERS:
            out = np.full(len(centers), NO_MATCH, dtype=np.int16)
            penalty = w * np.abs(self.stds[None, :] - float(target))  # (1,N)
            for start in range(0, len(centers), step):
                chunk = centers[start : start + step]
                d = delta_e76(chunk[:, None, :], self.labs[None, :, :]) + penalty
                out[start : start + step] = np.argmin(d, axis=1).astype(np.int16)
            luts.append(out.reshape(LUT_N, LUT_N, LUT_N))
        return luts

    # ---- 查询 ----
    @staticmethod
    def _indices(lab) -> tuple:
        L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
        i = np.clip((L / 100.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        j = np.clip(((a + 128.0) / 256.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        k = np.clip(((b + 128.0) / 256.0 * LUT_N).astype(np.int64), 0, LUT_N - 1)
        return i, j, k

    def nearest_index(self, rgb, std=None):
        """最近方块的下标。单次查询，直接算精确 ΔE。

        以前走 32³ LUT，但 LUT 把查询色吸附到格子中心，两个平均色相同的
        方块可能一起输给第三个；单次查询本来就只要几十微秒，没必要为它
        牺牲准确性。
        """
        lab = srgb_to_lab(np.asarray(rgb, dtype=np.float64).reshape(1, 3))
        d = delta_e76(lab, self.labs[None, :, :])
        if std is not None and self.has_texture_info:
            d = d + self.texture_weight * np.abs(self.stds[None, :] - float(std))
        return int(np.argmin(d[0]))

    def nearest(self, rgb, std=None) -> str:
        return self.names[self.nearest_index(rgb, std)]

    def nearest_batch(self, rgbs, stds=None, exact=False) -> list:
        """(K,3) 颜色 -> [block_name, …]

        stds 给了且色板带纹理信息时，成本里加上纹理项。
        exact=True 时不算 LUT、直接对所有方块算一遍 ΔE。用途：
        批量匹配（唯一色数量有限）时避免 LUT 把查询色吸附到 32³ 格子
        中心带来的选色偏差——两个平均色相同的方块在 LUT 里可能一起
        输给第三个方块。单次查询仍然走 LUT，快得多。
        """
        arr = np.asarray(rgbs, dtype=np.float64)
        flat = arr.reshape(-1, 3)
        labs = srgb_to_lab(flat)
        use_texture = stds is not None and self.has_texture_info

        if exact:
            penalty = None
            if use_texture:
                penalty = self.texture_weight * np.abs(
                    self.stds[None, :] - np.asarray(stds, dtype=np.float64)[:, None]
                )
            idx = np.empty(len(labs), dtype=np.int64)
            step = 1024
            for start in range(0, len(labs), step):
                chunk = labs[start : start + step]
                d = delta_e76(chunk[:, None, :], self.labs[None, :, :])
                if penalty is not None:
                    d = d + penalty[start : start + step]
                idx[start : start + step] = np.argmin(d, axis=1)
            return [self.names[int(v)] for v in idx]

        i, j, k = self._indices(labs)
        if use_texture:
            bins = [texture_bin(s) for s in stds]
            idx = np.asarray(
                [self.luts[b][i[n], j[n], k[n]] for n, b in enumerate(bins)],
                dtype=np.int64,
            )
        else:
            idx = self.lut[i, j, k]
        return [self.names[int(v)] for v in idx]

    def nearest_topk(self, rgbs, stds=None, k: int = 2) -> np.ndarray:
        """返回 (k, n) 的方块下标：第 0 行最接近，后面依次变差。

        给"噪声"用：同色区域里按比例混入次优方块，打散大面积墙面的单调。
        成本函数与 nearest_batch(exact=True) 完全一致，只是多留下几个候选。
        """
        arr = np.asarray(rgbs, dtype=np.float64)
        labs = srgb_to_lab(arr.reshape(-1, 3))
        n = len(labs)
        k = max(1, min(int(k), len(self.names)))
        out = np.zeros((k, n), dtype=np.int64)
        penalty = None
        if stds is not None and self.has_texture_info:
            penalty = self.texture_weight * np.abs(
                self.stds[None, :] - np.asarray(stds, dtype=np.float64)[:, None]
            )
        step = 1024
        for start in range(0, n, step):
            chunk = labs[start : start + step]
            d = delta_e76(chunk[:, None, :], self.labs[None, :, :])
            if penalty is not None:
                d = d + penalty[start : start + step]
            m = chunk.shape[0]
            part = np.argpartition(d, k - 1, axis=1)[:, :k]
            rows = np.arange(m)[:, None]
            order = np.argsort(d[rows, part], axis=1)
            out[:, start : start + m] = part[rows, order].T
        return out

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
        stds = None
        if self.has_texture_info:
            stds = {
                n: float(s) for n, s in zip(self.names, self.stds) if n not in ban
            }
        return BlockPalette(
            colors, stds=stds, texture_weight=self.texture_weight
        )


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
