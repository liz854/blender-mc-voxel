"""世界高度限制（限高）。

数值不靠记忆，从客户端 jar 里 `data/minecraft/dimension_type/*.json` 读：

    overworld     : min_y -64  height 384  logical_height 384
    the_nether    : min_y   0  height 256  logical_height 128
    the_end       : min_y   0  height 256  logical_height 256

（26.3 真机 jar 实测。1.18 起这套数值没变过，所以读不到 jar 时用内置兜底。）

为什么要拦：Litematica 摆投影时，超出世界高度的方块**不会报错**，直接跳过。
结果就是投影摆下去缺了头或少了半截，人还以为是自己没对准。所以导出前必须挡住。
"""

from __future__ import annotations

import json
import math
import os
import zipfile
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class Dimension:
    """一个维度的高度定义。"""

    name: str
    min_y: int
    height: int
    logical_height: int = 0

    @property
    def max_y(self) -> int:
        """最高一层能放方块的 y。"""
        return self.min_y + self.height - 1

    @property
    def span(self) -> int:
        """能放方块的层数。"""
        return self.height

    def __str__(self) -> str:
        return f"{self.name}（y {self.min_y}..{self.max_y}，共 {self.height} 格）"


# 读不到 jar 时的兜底：26.3 真机 jar 的数值
DIMENSION_DEFAULTS: Dict[str, Dimension] = {
    "overworld": Dimension("overworld", -64, 384, 384),
    "the_nether": Dimension("the_nether", 0, 256, 128),
    "the_end": Dimension("the_end", 0, 256, 256),
}

ALIASES = {
    "overworld": "overworld",
    "nether": "the_nether",
    "the_nether": "the_nether",
    "end": "the_end",
    "the_end": "the_end",
}

LABELS = {"overworld": "主世界", "the_nether": "下界", "the_end": "末地"}

# 海平面（1.18 起主世界是 y=63）。用来判断"从地面往上"还剩多少空间。
GROUND_LEVEL = {"overworld": 63}


def normalize(name: str) -> str:
    key = str(name or "").strip().lower()
    return ALIASES.get(key, key)


def _stamp(path: Optional[str]):
    """文件的 (修改时间, 大小)，用来判断缓存是否还能用。"""
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_CACHE: Dict[tuple, Dict[str, Dimension]] = {}


def read_dimensions(jar_path: str) -> Dict[str, Dimension]:
    """从客户端 jar 读所有 dimension_type。读不到返回空字典（不抛异常）。"""
    out: Dict[str, Dimension] = {}
    if not jar_path:
        return out
    prefix = "data/minecraft/dimension_type/"
    try:
        with zipfile.ZipFile(jar_path) as z:
            for entry in z.namelist():
                if not entry.startswith(prefix) or not entry.endswith(".json"):
                    continue
                try:
                    data = json.loads(z.read(entry).decode("utf-8"))
                except (ValueError, KeyError, OSError, UnicodeDecodeError):
                    continue
                if not isinstance(data, dict) or "min_y" not in data or "height" not in data:
                    continue
                name = entry[len(prefix) : -len(".json")]
                try:
                    out[name] = Dimension(
                        name,
                        int(data["min_y"]),
                        int(data["height"]),
                        int(data.get("logical_height", data["height"])),
                    )
                except (TypeError, ValueError):
                    continue
    except (zipfile.BadZipFile, OSError):
        return {}
    return out


def load_dimensions(jar_path: Optional[str] = None) -> Dict[str, Dimension]:
    """jar 优先，缺的用兜底补上（也允许 mod 加的自定义维度）。

    面板每次重绘都会调，所以按 (路径, 修改时间, 大小) 缓存一份，
    免得每帧都去打开 40 MB 的 jar。
    """
    key = (jar_path, _stamp(jar_path))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    merged = dict(DIMENSION_DEFAULTS)
    merged.update(read_dimensions(jar_path or ""))
    _CACHE.clear()  # 只用最近一份，jar 换了自然重建
    _CACHE[key] = merged
    return merged


def get_dimension(name: str = "overworld", jar_path: Optional[str] = None) -> Dimension:
    dims = load_dimensions(jar_path)
    key = normalize(name)
    if key in dims:
        return dims[key]
    return DIMENSION_DEFAULTS["overworld"]


@dataclass
class HeightReport:
    """限高检查结果。"""

    span: int
    dimension: Dimension
    level: str  # "ok" | "warn" | "error"
    message: str
    autofit: str = ""  # 自动放大倍率时的说明（没自动调就是空）

    @property
    def ok(self) -> bool:
        return self.level != "error"

    @property
    def limit(self) -> int:
        return self.dimension.height


def fit_block_size(block_size: float, span_y: int, limit: int) -> float:
    """把方块尺寸放大到能塞进限高，向上取到 0.01。"""
    if span_y <= limit or block_size <= 0:
        return float(block_size)
    needed = block_size * (span_y / float(limit))
    return round(needed + 0.004, 2)


# --------------------------------------------------------------------------
# 倍率（方块尺寸）与长度互相换算
# --------------------------------------------------------------------------
_EPS = 1e-9


def span_cells(span_units: float, block_size: float) -> int:
    """一段长度按方块尺寸离散后有几格。

    必须和 build.grid_from_bounds 用同一个算法（ceil），否则"刚好 384 格"
    会因为浮点误差变成 385。
    """
    if block_size <= 0:
        raise ValueError("方块尺寸必须大于 0")
    return int(math.ceil(float(span_units) / float(block_size) - _EPS))


def block_size_for_length(span_units: float, target_cells: int) -> float:
    """让某一轴正好 N 格时的方块尺寸。

    例：模型长 200 m，要它长 64 格 -> 200/64 = 3.125 m 一格。
    """
    target = int(target_cells)
    if target <= 0:
        raise ValueError("目标格数必须大于 0")
    if float(span_units) <= 0:
        raise ValueError("这一轴的长度是 0，没法按长度定倍率")
    return float(span_units) / float(target)


def fit_block_size_to_height(
    block_size: float, span_units_y: float, limit: int
) -> float:
    """把方块尺寸放大到高度不超过 limit。

    和 fit_block_size 的区别：那个是"已经体素化好了按格数缩"，
    这个是"还没体素化，按原始长度算"，所以结果是精确的——真的能让
    高度正好落在 limit 以内，而不是碰运气。

    已经放得下就原样返回（不会为了凑数把图变糙）。
    """
    bs = float(block_size)
    span = float(span_units_y)
    if bs <= 0 or span <= 0 or limit <= 0:
        return bs
    if span_cells(span, bs) <= int(limit):
        return bs

    # 理论值 = span / limit，浮点可能多出 1 格，往上顶一点点
    fitted = span / float(limit)
    for _ in range(16):
        if span_cells(span, fitted) <= int(limit):
            break
        fitted *= 1.0005
    # 结果会被写进 FloatProperty，那是 float32。400/384 = 1.0416666… 存成
    # float32 会略微变圆，第二次体素化就能算出 385 格（真机上踩过）。
    # 留 1e-6 的相对余量，比 float32 的 1.2e-7 大一个数量级，够用又不影响精度。
    return fitted * (1.0 + 1e-6)


def check_height(
    span_y: int,
    dimension: str = "overworld",
    jar_path: Optional[str] = None,
    block_size: Optional[float] = None,
    ground_level: object = "auto",
) -> HeightReport:
    """检查模型高度是否放得进维度。

    span_y     : 模型沿 y 的格数（= VoxelModel.size[1]）
    dimension  : overworld / the_nether / the_end（nether / end 也行）
    block_size : 只用于给"调到多大"的建议，可省
    ground_level: "auto" 用该维度的地面高度，None 关闭地面告警

    判定：
      span > height            → error（真的放不进去）
      span > 地面以上剩余空间  → warn（放得进，但得从地下起建）
      其余                     → ok
    """
    dim = get_dimension(dimension, jar_path)
    span = int(span_y)
    label = LABELS.get(dim.name, dim.name)
    limit = dim.height

    if span > limit:
        parts = [
            f"模型高 {span} 格，超过{label}的限高 {limit} 格"
            f"（y {dim.min_y}..{dim.max_y}）。",
            "Litematica 摆投影时超出限高的方块会被静默跳过，不报错但会缺块，所以这里直接拦。",
        ]
        if block_size:
            fit = fit_block_size(block_size, span, limit)
            parts.append(f"把方块尺寸从 {block_size:g} 调到 {fit:g} 以上，")
        segs = -(-span // limit)  # 向上取整
        parts.append(f"或者沿高度拆成 {segs} 段分别导出。")
        return HeightReport(span, dim, "error", "".join(parts))

    if ground_level == "auto":
        ground_level = GROUND_LEVEL.get(dim.name)
    if ground_level is not None:
        free = dim.max_y - int(ground_level) + 1
        if span > free:
            return HeightReport(
                span,
                dim,
                "warn",
                f"模型高 {span} 格，没超{label}限高 {limit} 格；"
                f"但从地面 y={ground_level} 往上只有 {free} 格，"
                f"投影得从地下 {span - free} 格处起建才放得下。",
            )

    return HeightReport(
        span,
        dim,
        "ok",
        f"模型高 {span} 格，{label}限高 {limit} 格（y {dim.min_y}..{dim.max_y}），放得下。",
    )


def check_model(model, dimension="overworld", jar_path=None, block_size=None) -> HeightReport:
    """对 VoxelModel 做限高检查（只看 y 跨度，不管原点）。"""
    return check_height(
        model.size[1], dimension=dimension, jar_path=jar_path, block_size=block_size
    )
