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

# 改了解析逻辑（过滤规则、tint、取纹理的优先级、缓存里存 std）就 +1，
# 让旧缓存失效。否则代码修好了、缓存还是旧结果，问题看起来"没修好"。
CACHE_VERSION = 13

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

        台阶、门、栅栏、按钮这些必须排除：它们不是整方块，
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
# 黑名单**只装"物理上放不进墙里"的方块**（放置后被游戏拒绝的那种）。
#
# 曾经这里塞了 60 多个：书架、工作台、熔炉、南瓜、炼药锅、活塞、信标、床……
# 理由是"这些不是建材"。用户 2026-10-30 明确纠正：**只要能正常放置就不要挡**——
# 书架就是书架的纹理，工作台就是胶合板的颜色，玻璃熔炉是浅灰的，它们作为
# "平均色最接近的方块"完全正当，挡掉等于凭空少掉一批好素材。
# 真正该挡的只有一类：游戏根本不让放在那个位置的。
NON_BUILDING_BLOCKS = {
    # ---- 物理上放不下 ----
    # 仙人掌要求四面都不能挨着其他方块，埋进墙里连放置都被拒绝
    "minecraft:cactus",

    # ---- 用户点名排除（2026-10-30）----
    # 这些是"打开界面 / 触发机制 / 不可破坏"的方块：摆进作品里会刷怪、执行命令、
    # 需要权限、或者根本拆不掉。不是审美判断，是用户明确要求不要。
    "minecraft:spawner",                  # 刷怪笼：真的会刷怪
    "minecraft:trial_spawner",            # 试炼刷怪笼
    "minecraft:vault",                    # 宝库：要用试炼钥匙开、吐战利品
    "minecraft:command_block",            # 命令方块 / 链式 / 循环
    "minecraft:chain_command_block",
    "minecraft:repeating_command_block",
    "minecraft:structure_block",          # 结构方块：保存/加载局部结构
    "minecraft:jigsaw",                   # 拼图方块：生成结构用
    "minecraft:bedrock",                  # 基岩：放下去拆不掉
    "minecraft:test_block",               # 测试方块 / 测试实例方块
    "minecraft:test_instance_block",
    "minecraft:sculk_catalyst",           # 幽匿催发体：会扩散幽匿、召出监守者
    "minecraft:suspicious_sand",          # 可疑的沙子：里面有考古物品，刷掉就掉
    "minecraft:suspicious_gravel",        # 可疑的沙砾

    # ---- 容器与站台 ----
    # 其中砂轮/讲台/切石机/箱子/陷阱箱/堆肥桶/炼药锅/酿造台/附魔台/铁砧
    # 本来就被"整方块"那道筛子挡掉了（模型有空隙或斜面），列在这里是为了
    # 将来放宽整方块筛选时不会偷偷混回来。
    "minecraft:chest",
    "minecraft:trapped_chest",
    "minecraft:grindstone",
    "minecraft:lectern",
    "minecraft:stonecutter",
    "minecraft:composter",
    "minecraft:cauldron",
    "minecraft:brewing_stand",
    "minecraft:enchanting_table",
    "minecraft:anvil",
    "minecraft:chipped_anvil",
    "minecraft:damaged_anvil",
}

# 靠后缀/前缀排除的整方块——**现在是空的**。
#
# 以前这里挡了 _leaves（半透明+tint）、_concrete_powder（悬空会塌）、
# _glazed_terracotta（带方向箭头）、_coral_block（离水变死珊瑚）、infested_*（冒蠹虫）。
# 2026-10-30 用户要求全部放出来：这些方块**都能正常放置**，颜色就是它们的真实颜色，
# 放进色板只会让匹配多几个选项。担心哪一种就自己用面板的「禁用方块」排除。
# （如果确实出现了"放下去就变形"的方块再往这里加，但要有实测依据，不是凭想象。）
NON_BUILDING_SUFFIXES = ()
# 虫蚀方块：外观是石头/石砖，挖开刷蠹虫。用户 2026-10-30 要求全部排除。
NON_BUILDING_PREFIXES = ("minecraft:infested_",)


# 「自带光、放下就亮」的整方块。**不含需要充能的**（红石灯、铜灯泡要红石信号，
# 未充能时是暗的，选它等于选了一个颜色不对的方块）。
# 拿这些方块当建材的问题：会发光（晚上整面墙发亮）、颜色偏小清新的青/粉/白，
# 而且蛙鸣灯表面极平滑，特别容易被"浅色平滑面"的匹配选中——猴头脸部会被
# 一整片 verdant_froglight 填满。所以默认关掉，需要做灯效时再打开。
# 完整清单：Minecraft 里「自带光、放下就亮」的整方块。
# 有些当前进不了色板（标注了原因），但列在这里，将来放开筛选阈值或黑名单时自动生效。
LIGHT_BLOCKS = frozenset({
    "minecraft:ochre_froglight",        # 蛙鸣灯·赭黄  光 15
    "minecraft:pearlescent_froglight",  # 蛙鸣灯·珠光  光 15
    "minecraft:verdant_froglight",      # 蛙鸣灯·青翠  光 15
    "minecraft:shroomlight",            # 菌光体       光 15
    "minecraft:sea_lantern",            # 海晶灯       光 15（std 47.9，当前被花纹阈值筛掉）
    "minecraft:glowstone",              # 萤石         光 15（std 62.6，当前被花纹阈值筛掉）
    "minecraft:jack_o_lantern",         # 南瓜灯       光 15（在器物黑名单里）
    "minecraft:magma_block",            # 岩浆块       光 3 （在器物黑名单里）
    "minecraft:beacon"                  # 信标         光 15（在机制方块黑名单里）
    , "minecraft:crying_obsidian"       # 哭泣的黑曜石 光 10
})


# 「工作方块」：带 BlockEntity、会 tick、或者右键开界面的一类。
#
# 它们不像纯色方块那样"摆着不动"——每个都有方块实体在 tick（烧制、抓蜜蜂、
# 红石逻辑、存书……）。摆几万个，Litematica 加载和游戏 tick 都会吃力。
# 所以默认**不用**，面板「允许工作方块」打开后才参与匹配。
#
# 注意：普通书架 bookshelf 不在里面——它是纯装饰方块，没有 BlockEntity 也不 tick。
# 红石灯、铜灯也不在里面（只响应方块更新，不 tick）。
WORKSTATION_BLOCKS = frozenset({
    # 熔炉类：烧东西时持续 tick
    "minecraft:furnace",
    "minecraft:blast_furnace",
    "minecraft:smoker",
    # 村民工作站点 + 交互界面
    "minecraft:barrel",
    "minecraft:loom",
    "minecraft:cartography_table",
    "minecraft:fletching_table",
    "minecraft:smithing_table",
    "minecraft:crafting_table",
    "minecraft:crafter",
    "minecraft:chiseled_bookshelf",
    "minecraft:beehive",
    "minecraft:bee_nest",
    # 红石与机械：高频 tick
    "minecraft:dispenser",
    "minecraft:dropper",
    "minecraft:piston",
    "minecraft:sticky_piston",
    "minecraft:observer",
    "minecraft:note_block",
    "minecraft:target",
    "minecraft:hopper",
    "minecraft:daylight_detector",
    # 其它带方块实体的
    "minecraft:jukebox",
    "minecraft:beacon",
    "minecraft:respawn_anchor",
    "minecraft:lodestone",
    # 模型不是整方块（本来就被筛掉），列全是为了将来放宽时不会漏
    "minecraft:grindstone",
    "minecraft:lectern",
    "minecraft:stonecutter",
    "minecraft:composter",
    "minecraft:cauldron",
    "minecraft:brewing_stand",
    "minecraft:enchanting_table",
    "minecraft:sculk_sensor",
    "minecraft:calibrated_sculk_sensor",
})


def is_usable_block(name: str, work_blocks: bool = False) -> bool:
    """色板最终是否可用。work_blocks=False 时把工作方块也排除掉。

    面板、清单工具、测试都走这一个判定，避免各写一套。
    """
    if not _is_building_block(name):
        return False
    if not work_blocks and name in WORKSTATION_BLOCKS:
        return False
    return True


def _changes_on_place(name: str) -> bool:
    """放下去就**变成另一种方块**的，不能用。

    这类和"能不能放"是两件事：方块放得下，但放完颜色/外观就不是它了。
    目前三类：

    - **活珊瑚**：离开水立刻变死珊瑚，整块从粉/紫/黄/青变成灰绿。
      `dead_*` 系列本来就是死的，放下去不会再变，保留。
    - **会融化的冰**（`ice` / `frosted_ice`）：光照 12 以上化成水。
      `packed_ice` / `blue_ice` 不融化，保留。
    - **非涂蜡铜方块**：随时间氧化，亮铜 → 斑驳 → 锈蚀 → 氧化绿。
      `*_ore` 和 `raw_copper_block` 不氧化；`waxed_*` 锁住外观，保留。

    加新条目之前先确认「放置后确实会变」，不能凭想象。
    """
    short = name.split(":", 1)[-1]

    # 活珊瑚 -> 死珊瑚（离水整块变灰绿）
    if "coral" in short:
        return not short.startswith("dead_")

    # 会融化的冰：光照等级 12 以上化成水，霜冰踩过还会碎
    # 浮冰 packed_ice / 蓝冰 blue_ice 不融化，保留
    if short in ("ice", "frosted_ice"):
        return True

    # 非涂蜡铜方块：随时间氧化，颜色从亮铜 -> 斑驳 -> 锈蚀 -> 氧化绿
    # 矿石/粗矿块不氧化；waxed_* 涂过蜡，锁住当前外观，保留
    if "copper" in short and not short.startswith("waxed_"):
        return not (short.endswith("_ore") or short == "raw_copper_block")

    return False


def _is_building_block(name: str) -> bool:
    if name in NON_BUILDING_BLOCKS:
        return False
    if _changes_on_place(name):
        return False
    if name.endswith(NON_BUILDING_SUFFIXES):
        return False
    if name.startswith(NON_BUILDING_PREFIXES):
        return False
    return True


def _load_block_entries(
    jar_path: str,
    names=None,
    skip=("minecraft:air", "minecraft:cave_air", "minecraft:void_air"),
    full_cube_only: bool = True,
    max_texture_std=None,
) -> Dict[str, Tuple[Tuple[int, int, int], float]]:
    """从 jar / 资源包读出 {方块名: (平均色, 纹理标准差)}。

    full_cube_only=True 时只保留"整方块"（模型里有盖满 0..16 的方盒），
    排除台阶/门/栅栏/按钮等，否则体素可能被匹配成形状不对的方块。

    max_texture_std 默认不过滤（None）。**不要拿它当审美筛子**：
    实测 42 这个阈值会挡掉 deepslate_diamond_ore(42.7)、nether_quartz_ore(48.4)、
    birch_log(54.4)、sea_lantern(47.9)、glowstone(62.6)——矿石、白桦原木、照明方块
    全被误伤，而真正不该用的（合成器、结构方块、测试方块）要靠黑名单挡，
    它们的 std 并不高。std 的正确用途只有一个：纹理感知匹配时比"平不平"。
    """
    out: Dict[str, Tuple[Tuple[int, int, int], float]] = {}
    with ResourcePack(jar_path) as rp:
        candidates = names if names is not None else rp.block_names()
        for name in candidates:
            if name in skip or not _is_building_block(name):
                continue
            stats = rp.block_color(name, full_cube_only=full_cube_only, with_stats=True)
            if not stats:
                continue
            rgb, std = stats
            if max_texture_std is not None and std > max_texture_std:
                continue
            out[name] = (rgb, float(std))
    return out


def load_block_colors(
    jar_path: str,
    names=None,
    skip=("minecraft:air", "minecraft:cave_air", "minecraft:void_air"),
    full_cube_only: bool = True,
    max_texture_std=None,
) -> Dict[str, Tuple[int, int, int]]:
    """从 jar / 资源包读出方块平均色表（不要纹理 std 的旧接口）。"""
    return {
        name: rgb
        for name, (rgb, _std) in _load_block_entries(
            jar_path,
            names=names,
            skip=skip,
            full_cube_only=full_cube_only,
            max_texture_std=max_texture_std,
        ).items()
    }


def _cache_key(jar_path: str) -> dict:
    import os

    try:
        stamp = os.path.getmtime(jar_path)
    except OSError:
        stamp = 0
    return {"jar": os.path.abspath(jar_path), "mtime": stamp, "v": CACHE_VERSION}


def load_cached(jar_path: str, cache_path: str):
    """带磁盘缓存（JSON），只返回平均色表（兼容旧调用方）。"""
    return load_cached_stats(jar_path, cache_path)[0]


def load_cached_stats(jar_path: str, cache_path: str):
    """带磁盘缓存（JSON），返回 (色表, 纹理标准差表)。

    缓存键里带 jar 的 mtime。
    空结果绝不写缓存：解析失败（jar 不完整、代码有 bug）时如果落了空表，
    之后即使问题修好也会一直命中空缓存，颜色永远是兜底色板而且没人察觉。
    """
    import json
    import os

    key = _cache_key(jar_path)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            colors = data.get("colors") or {}
            stds = data.get("stds") or {}
            if data.get("key") == key and colors:
                return (
                    {k: tuple(v) for k, v in colors.items()},
                    {k: float(v) for k, v in stds.items()},
                )
        except Exception:
            pass
    entries = _load_block_entries(jar_path)
    if not entries:
        return {}, {}
    colors = {k: v[0] for k, v in entries.items()}
    stds = {k: v[1] for k, v in entries.items()}
    try:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "key": key,
                    "colors": {k: list(v) for k, v in colors.items()},
                    "stds": stds,
                },
                f,
            )
    except Exception:
        pass
    return colors, stds
