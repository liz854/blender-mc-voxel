"""资源包 / .jar 解析 -> 方块平均色表。

输入 .jar 或资源包 .zip，输出 {block_name: (r,g,b)}。
流程：blockstates/*.json -> variants 第一个 model -> models/*.json（含 parent 链）
      -> textures 里取 all/top/side/particle -> 读 PNG -> 平均色（跳过 alpha<128）
"""

from __future__ import annotations

import json
import zipfile
from typing import Dict, Optional, Tuple

from . import png

TEXTURE_KEYS = ("all", "top", "side", "texture", "particle", "end", "bottom")

# 改了解析逻辑（过滤规则、tint、取纹理的优先级）就 +1，让旧缓存失效。
# 否则代码修好了、缓存还是旧结果，问题看起来"没修好"。
CACHE_VERSION = 6

# 这些方块的纹理是灰度图，游戏里按生物群系 tint 上色。
# 不乘上 tint，草方块和树叶会变成灰色，体素配色直接废掉。
# 值取平原群系的默认色（草 0x91BD59，树叶按树种）。
BIOME_TINTS: Dict[str, Tuple[int, int, int]] = {
    "minecraft:grass_block": (0x91, 0xBD, 0x59),
    "minecraft:short_grass": (0x91, 0xBD, 0x59),
    "minecraft:tall_grass": (0x91, 0xBD, 0x59),
    "minecraft:fern": (0x91, 0xBD, 0x59),
    "minecraft:large_fern": (0x91, 0xBD, 0x59),
    "minecraft:potted_fern": (0x91, 0xBD, 0x59),
    "minecraft:sugar_cane": (0x8B, 0xC8, 0x6B),
    "minecraft:oak_leaves": (0x6B, 0xA9, 0x41),
    "minecraft:jungle_leaves": (0x6B, 0xA9, 0x41),
    "minecraft:acacia_leaves": (0x87, 0xA9, 0x41),
    "minecraft:dark_oak_leaves": (0x50, 0x8A, 0x28),
    "minecraft:mangrove_leaves": (0x50, 0x8A, 0x28),
    "minecraft:spruce_leaves": (0x45, 0x6B, 0x42),
    "minecraft:birch_leaves": (0x6F, 0xA1, 0x49),
    "minecraft:azalea_leaves": (0x59, 0x7B, 0x44),
    "minecraft:flowering_azalea_leaves": (0x59, 0x7B, 0x44),
    "minecraft:vine": (0x6B, 0xA9, 0x41),
    "minecraft:lily_pad": (0x20, 0x80, 0x20),
    "minecraft:water": (0x3F, 0x76, 0xE4),
    "minecraft:redstone_wire": (0xB0, 0x15, 0x08),
}


def _norm_id(raw: str, default_ns: str = "minecraft") -> str:
    raw = str(raw)
    if ":" in raw:
        return raw
    return f"{default_ns}:{raw}"


def _split_id(ident: str) -> Tuple[str, str]:
    if ":" in ident:
        ns, path = ident.split(":", 1)
        return ns, path
    return "minecraft", ident


class ResourcePack:
    """只读 zip 包装，带 JSON / PNG 缓存。"""

    def __init__(self, path: str):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        self._json: Dict[str, dict] = {}
        self._tex: Dict[str, Optional[Tuple[int, int, int]]] = {}
        self._names = set(self.zip.namelist())

    def close(self):
        self.zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def json(self, ident: str):
        ns, path = _split_id(ident)
        key = f"assets/{ns}/{path}.json"
        if key not in self._names:
            return None
        if key not in self._json:
            self._json[key] = json.loads(self.zip.read(key).decode("utf-8"))
        return self._json[key]

    def texture_stats(self, ident: str):
        """返回 (平均色, 颜色标准差)。标准差大 = 纹理花，放大成方块后很显眼。"""
        ns, path = _split_id(ident)
        if not path.startswith("textures/"):
            path = "textures/" + path
        if not path.endswith(".png"):
            path += ".png"
        key = f"assets/{ns}/{path}"
        if key in self._tex:
            return self._tex[key]
        stats = None
        if key in self._names:
            try:
                _, _, rgba = png.decode(self.zip.read(key))
                stats = png.color_stats(rgba)
            except Exception:
                stats = None
        self._tex[key] = stats
        return stats

    def texture_color(self, ident: str):
        stats = self.texture_stats(ident)
        return stats[0] if stats else None

    def texture_rgba(self, ident: str):
        """原始像素 (h, w, 4)，0-255。拿不到返回 None。"""
        ns, path = _split_id(ident)
        if not path.startswith("textures/"):
            path = "textures/" + path
        if not path.endswith(".png"):
            path += ".png"
        key = f"assets/{ns}/{path}"
        if key not in self._names:
            return None
        try:
            _, _, rgba = png.decode(self.zip.read(key))
            return rgba
        except Exception:
            return None

    # ---- 模型解析 ----
    def _model_path(self, ident: str) -> str:
        """模型名 "minecraft:block/stone" -> "minecraft:models/block/stone"。"""
        ns, path = _split_id(ident)
        if not path.startswith("models/"):
            path = "models/" + path
        return f"{ns}:{path}"

    def model_textures(self, ident: str, depth: int = 0) -> dict:
        """递归解析 parent 链，子模型的 textures 覆盖父模型。

        注意：模型名不带 "models/" 前缀（如 minecraft:block/stone = "方块型"，
        不带前缀 = "模型型"）。漏了这层前缀会去读 assets/.../block/stone.json，
        文件不存在 -> 所有方块静默解析失败 -> 上层悄悄退回兜底色板。
        """
        if depth > 8:
            return {}
        data = self.json(self._model_path(ident))
        if not data:
            return {}
        merged = {}
        parent = data.get("parent")
        if parent:
            merged.update(self.model_textures(self._model_path(parent), depth + 1))
        merged.update(data.get("textures", {}) or {})
        return merged

    def block_model(self, block_name: str):
        """方块默认状态用到的模型标识（"minecraft:block/stone"）。"""
        ns, name = _split_id(block_name)
        states = self.json(f"{ns}:blockstates/{name}")
        if not states:
            return None
        variants = states.get("variants")
        if isinstance(variants, dict) and variants:
            first = next(iter(variants.values()))
            if isinstance(first, list) and first:
                first = first[0]
            ref = (first or {}).get("model")
            return _norm_id(ref, ns) if ref else None
        multipart = states.get("multipart")
        if isinstance(multipart, list) and multipart:
            apply = multipart[0].get("apply")
            if isinstance(apply, list) and apply:
                apply = apply[0]
            ref = (apply or {}).get("model")
            return _norm_id(ref, ns) if ref else None
        return None

    def _elements(self, ident: str, depth: int = 0):
        """沿 parent 链找到第一个带 elements 的模型，返回它的 elements。"""
        if depth > 8:
            return []
        data = self.json(self._model_path(ident))
        if not data:
            return []
        els = data.get("elements")
        if els:
            return els
        parent = data.get("parent")
        if parent:
            return self._elements(self._model_path(parent), depth + 1)
        return []

    def is_full_cube(self, model_ref: str) -> bool:
        """模型里有没有一个盖满 0..16 的方盒。

        楼梯、台阶、门、栅栏、按钮这些必须排除：它们不是整方块，
        体素匹配到它们会导出形状错乱的投影（而且我们也不写 Properties）。
        """
        for el in self._elements(model_ref):
            if not isinstance(el, dict):
                continue
            fr = el.get("from")
            to = el.get("to")
            if not fr or not to:
                continue
            if all(abs(float(a) - 0.0) < 1e-6 for a in fr) and all(
                abs(float(a) - 16.0) < 1e-6 for a in to
            ):
                return True
        return False

    def block_color(self, block_name: str, full_cube_only: bool = False, with_stats: bool = False):
        ns, name = _split_id(block_name)
        model_ref = self.block_model(block_name)
        if not model_ref:
            return None
        if full_cube_only and not self.is_full_cube(model_ref):
            return None

        textures = self.model_textures(model_ref)
        # 纹理引用可能是 "#all" 这种变量，往下追一层
        for key in TEXTURE_KEYS:
            if key not in textures:
                continue
            ref = textures[key]
            seen = set()
            while isinstance(ref, str) and ref.startswith("#") and ref[1:] not in seen:
                seen.add(ref[1:])
                ref = textures.get(ref[1:])
            if not isinstance(ref, str):
                continue
            stats = self.texture_stats(_norm_id(ref, ns))
            if stats:
                color, std = stats
                color = _apply_tint(block_name, color)
                return (color, std) if with_stats else color
        return None

    def block_names(self):
        """所有带 blockstates 的方块名。"""
        out = []
        for key in self._names:
            if key.startswith("assets/") and "/blockstates/" in key and key.endswith(".json"):
                _, ns, rest = key.split("/", 2)
                name = rest[len("blockstates/") : -len(".json")]
                out.append(f"{ns}:{name}")
        return sorted(out)


def _apply_tint(block_name: str, rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """灰度纹理乘上生物群系 tint。"""
    tint = BIOME_TINTS.get(block_name)
    if not tint:
        return rgb
    return tuple(
        int(max(0, min(255, round(c * t / 255.0)))) for c, t in zip(rgb, tint)
    )


# 功能方块：是整方块，但纹理是器物图案（工作台、书架、熔炉…），
# 拿来当体素会把整面墙变成杂乱图案。做纯色/材质色用，排除。
NON_BUILDING_BLOCKS = {
    "minecraft:bookshelf", "minecraft:chiseled_bookshelf", "minecraft:crafting_table",
    "minecraft:cartography_table", "minecraft:fletching_table", "minecraft:smithing_table",
    "minecraft:loom", "minecraft:stonecutter", "minecraft:grindstone", "minecraft:lectern",
    "minecraft:jukebox", "minecraft:note_block", "minecraft:chest", "minecraft:trapped_chest",
    "minecraft:barrel", "minecraft:furnace", "minecraft:blast_furnace", "minecraft:smoker",
    "minecraft:crafting_table", "minecraft:beehive", "minecraft:bee_nest",
    "minecraft:target", "minecraft:tnt", "minecraft:sponge", "minecraft:dried_kelp_block",
    "minecraft:mushroom_stem", "minecraft:red_mushroom_block", "minecraft:brown_mushroom_block",
    "minecraft:melon", "minecraft:pumpkin", "minecraft:carved_pumpkin", "minecraft:jack_o_lantern",
    "minecraft:magma_block", "minecraft:daylight_detector", "minecraft:observer",
    "minecraft:piston", "minecraft:sticky_piston", "minecraft:dispenser", "minecraft:dropper",
    "minecraft:hopper", "minecraft:composter", "minecraft:cauldron", "minecraft:brewing_stand",
    "minecraft:enchanting_table", "minecraft:anvil", "minecraft:chipped_anvil",
    "minecraft:damaged_anvil", "minecraft:respawn_anchor", "minecraft:lodestone",
    "minecraft:fletching_table", "minecraft:sculk_catalyst", "minecraft:sculk_sensor",
    "minecraft:calibrated_sculk_sensor", "minecraft:bedrock", "minecraft:spawner",
    # 机制方块：不是"一面墙"，而且有的会发光/刷怪/长东西
    "minecraft:beacon", "minecraft:budding_amethyst",
    "minecraft:trial_spawner", "minecraft:vault", "minecraft:creaking_heart",
    "minecraft:command_block", "minecraft:chain_command_block",
    "minecraft:repeating_command_block", "minecraft:jigsaw",
}

# 靠后缀/前缀排除的整方块——它们能通过"整方块"和"纹理够纯"两关，但拿来做建材会出事：
#   *_leaves            半透明 + 群系 tint，颜色算得漂亮，放下去是树叶不是石头
#   *_concrete_powder   重力方块，悬空放置会塌，而且正常人都用凝固后的混凝土
#   *_glazed_terracotta 釉面陶瓦，带朝一个方向的箭头图案，铺墙/渐变里会突然冒花纹
#   *_coral_block       珊瑚块**离开水会变成死珊瑚**（整块变灰），选它等于选了另一个颜色
#   *_coral             珊瑚 / 珊瑚扇，同上（保险）
#   infested_*          虫蚀方块，挖开冒蠹虫
#
# 注意：**矿石（*_ore）和粗金属块（raw_*）不剔**。它们虽然有斑点，但"平均色最接近"
# 就是本项目的匹配准则，没有理由因为"不够纯"就排除。真觉得花，用面板的白名单避开。
NON_BUILDING_SUFFIXES = (
    "_leaves",
    "_concrete_powder",
    "_glazed_terracotta",
    "_coral_block",
    "_coral",
)
NON_BUILDING_PREFIXES = ("minecraft:infested_",)


def _is_building_block(name: str) -> bool:
    if name in NON_BUILDING_BLOCKS:
        return False
    if name.endswith(NON_BUILDING_SUFFIXES):
        return False
    if name.startswith(NON_BUILDING_PREFIXES):
        return False
    return True


def load_block_colors(
    jar_path: str,
    names=None,
    skip=("minecraft:air", "minecraft:cave_air", "minecraft:void_air"),
    full_cube_only: bool = True,
    max_texture_std: float = 42.0,
) -> Dict[str, Tuple[int, int, int]]:
    """从 jar / 资源包读出方块平均色表。

    full_cube_only=True 时只保留"整方块"（模型里有盖满 0..16 的方盒），
    排除楼梯/台阶/门/栅栏/按钮等，否则体素可能被匹配成形状不对的方块。

    max_texture_std 按纹理颜色标准差过滤"花纹理"方块（书架、工作台那种），
    纯色方块 std 接近 0，石头这类有噪点的在 20-30 左右。
    """
    out: Dict[str, Tuple[int, int, int]] = {}
    with ResourcePack(jar_path) as rp:
        candidates = names if names is not None else rp.block_names()
        for name in candidates:
            if name in skip or not _is_building_block(name):
                continue
            stats = rp.block_color(name, full_cube_only=full_cube_only, with_stats=True)
            if not stats:
                continue
            rgb, std = stats
            if std > max_texture_std:
                continue
            out[name] = rgb
    return out


def load_cached(jar_path: str, cache_path: str):
    """带磁盘缓存（JSON）。缓存键里带 jar 的 mtime。

    空结果绝不写缓存：解析失败（jar 不完整、代码有 bug）时如果落了空表，
    之后即使问题修好也会一直命中空缓存，颜色永远是兜底色板而且没人察觉。
    """
    import json
    import os

    try:
        stamp = os.path.getmtime(jar_path)
    except OSError:
        stamp = 0
    key = {"jar": os.path.abspath(jar_path), "mtime": stamp, "v": CACHE_VERSION}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            colors = data.get("colors") or {}
            if data.get("key") == key and colors:
                return {k: tuple(v) for k, v in colors.items()}
        except Exception:
            pass
    colors = load_block_colors(jar_path)
    if not colors:
        return colors
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "colors": {k: list(v) for k, v in colors.items()}}, f)
    except Exception:
        pass
    return colors
