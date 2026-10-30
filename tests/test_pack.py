"""资源包解析（core/pack.py）测试。

背景：这条路径在真机上第一次跑就暴露了"模型路径漏 models/ 前缀"的 bug——
1288 个方块全部静默返回 None，上层悄悄退回兜底色板，测试里完全看不出来。
所以这里必须用「假 zip 资源包」把整条链钉住，不能只测 fallback。
"""

from __future__ import annotations

import json
import os
import struct
import zipfile
import zlib

import pytest

from core import pack


# --------------------------------------------------------------------------
# 最小 PNG 编码器（png.py 只有 decode，测试里需要造纹理）
# --------------------------------------------------------------------------
def _png_rgb(w: int, h: int, rgb) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8bit truecolor
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _png_noisy(w: int, h: int) -> bytes:
    rows = []
    for y in range(h):
        row = bytearray(b"\x00")
        for x in range(w):
            v = 255 if (x + y) % 2 == 0 else 0
            row += bytes((v, v, v))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _full_cube_elements():
    return [{"from": [0, 0, 0], "to": [16, 16, 16]}]


def _make_pack(path, blocks):
    """blocks: {name: dict(elements=..., texture_rgb=..., noisy=bool, tint)?}"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "assets/minecraft/models/block/cube_all.json",
            json.dumps({"elements": _full_cube_elements()}),
        )
        for name, spec in blocks.items():
            short = name.split(":", 1)[1]
            z.writestr(
                f"assets/minecraft/blockstates/{short}.json",
                json.dumps({"variants": {"": {"model": f"minecraft:block/{short}_model"}}}),
            )
            z.writestr(
                f"assets/minecraft/models/block/{short}_model.json",
                json.dumps(
                    {
                        "parent": "minecraft:block/cube_all",
                        "textures": {"all": f"minecraft:block/{short}_tex"},
                        "elements": spec.get("elements", _full_cube_elements()),
                    }
                ),
            )
            tex = (
                _png_noisy(8, 8)
                if spec.get("noisy")
                else _png_rgb(8, 8, spec.get("texture_rgb", (120, 120, 120)))
            )
            z.writestr(f"assets/minecraft/textures/block/{short}_tex.png", tex)
    return str(path)


# --------------------------------------------------------------------------
# 核心：模型路径必须带 models/ 前缀
# --------------------------------------------------------------------------
def test_block_color_resolves_model_path(tmp_path):
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:stone": {"texture_rgb": (126, 126, 126)}})
    with pack.ResourcePack(jar) as rp:
        assert rp.block_model("minecraft:stone") == "minecraft:block/stone_model"
        # 这条断言就是当时真机翻车的地方
        assert rp.block_color("minecraft:stone") == (126, 126, 126)
        assert rp.model_textures("minecraft:block/stone_model")


def test_model_path_helper_adds_prefix():
    rp = pack.ResourcePack.__new__(pack.ResourcePack)  # 只用方法，不打开 zip
    assert rp._model_path("minecraft:block/stone") == "minecraft:models/block/stone"
    # 已经带前缀的不重复加
    assert rp._model_path("minecraft:models/block/stone") == "minecraft:models/block/stone"
    assert rp._model_path("block/stone") == "minecraft:models/block/stone"


def test_parent_chain_reaches_elements(tmp_path):
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:stone": {}})
    with pack.ResourcePack(jar) as rp:
        assert rp.is_full_cube("minecraft:block/stone_model") is True


# --------------------------------------------------------------------------
# 整方块过滤：楼梯/台阶不能进调色板
# --------------------------------------------------------------------------
def test_partial_model_is_not_full_cube(tmp_path):
    slab = [{"from": [0, 0, 0], "to": [16, 8, 16]}]
    jar = _make_pack(
        tmp_path / "p.zip",
        {"minecraft:stone": {}, "minecraft:oak_slab": {"elements": slab}},
    )
    with pack.ResourcePack(jar) as rp:
        assert rp.is_full_cube("minecraft:block/stone_model") is True
        assert rp.is_full_cube("minecraft:block/oak_slab_model") is False
        assert rp.block_color("minecraft:oak_slab", full_cube_only=True) is None

    colors = pack.load_block_colors(jar)
    assert "minecraft:oak_slab" not in colors
    assert "minecraft:stone" in colors


# --------------------------------------------------------------------------
# 花纹理过滤
# --------------------------------------------------------------------------
def test_noisy_texture_still_kept(tmp_path):
    """纹理花的方块也不能剔。

    曾经有一道 std>42 的筛子，理由是"过滤花纹理"。实测它挡掉的 12 种里只有 4 种该挡，
    其余全是误伤（钻石矿 42.7、白桦原木 54.4、海晶灯 47.9、萤石 62.6）。
    书架、工作台、熔炉这类功能方块更不该挡——它们都能正常放置，颜色就是它们的真实颜色。
    """
    jar = _make_pack(
        tmp_path / "p.zip",
        {
            "minecraft:white_concrete": {"texture_rgb": (240, 240, 240)},
            "minecraft:bookshelf": {"noisy": True},
            "minecraft:crafting_table": {"texture_rgb": (150, 108, 74)},
            "minecraft:furnace": {"texture_rgb": (110, 110, 110)},
        },
    )
    colors = pack.load_block_colors(jar)
    for name in (
        "minecraft:white_concrete",
        "minecraft:bookshelf",
        "minecraft:crafting_table",
        "minecraft:furnace",
    ):
        assert name in colors, f"{name} 不该被剔掉"


def test_only_unplaceable_blocks_filtered(tmp_path):
    """黑名单只装「物理上放不进墙里」的方块。

    仙人掌要求四面都不能挨着其他方块，埋进墙里连放置都被拒绝——这类必须挡。
    其余能正常放置的（含书架、工作台、熔炉、刷怪笼、命令方块）一律放出来。
    """
    jar = _make_pack(
        tmp_path / "p.zip",
        {
            "minecraft:stone": {},
            "minecraft:cactus": {},
            "minecraft:crafting_table": {},
            "minecraft:furnace": {},
            "minecraft:bookshelf": {},
        },
    )
    colors = pack.load_block_colors(jar)
    assert "minecraft:stone" in colors
    assert "minecraft:cactus" not in colors, "仙人掌在墙里放不下去，必须挡"
    for ok in ("minecraft:crafting_table", "minecraft:furnace", "minecraft:bookshelf"):
        assert ok in colors, f"{ok} 能正常放置，不该被挡"


def test_grass_block_gets_green_tint(tmp_path):
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:grass_block": {"texture_rgb": (147, 147, 147)}})
    colors = pack.load_block_colors(jar)
    r, g, b = colors["minecraft:grass_block"]
    assert g > r and g > b, f"草方块必须是绿的，拿到 {(r, g, b)}"


def test_tint_unknown_block_untouched():
    assert pack._apply_tint("minecraft:stone", (10, 20, 30)) == (10, 20, 30)


# --------------------------------------------------------------------------
# 缓存：空结果绝不能落盘
# --------------------------------------------------------------------------
def test_cache_does_not_persist_empty_result(tmp_path):
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:stone": {}})
    cache = tmp_path / "colors.json"

    empty_jar = str(tmp_path / "empty.zip")
    with zipfile.ZipFile(empty_jar, "w") as z:
        z.writestr("readme.txt", "no assets here")

    assert pack.load_cached(empty_jar, str(cache)) == {}
    assert not cache.exists(), "空结果不该写缓存"

    # 同一个缓存文件，换成有内容的 jar 必须能拿到真颜色
    colors = pack.load_cached(jar, str(cache))
    assert colors.get("minecraft:stone") == (120, 120, 120)
    assert cache.exists()


def test_cache_roundtrip(tmp_path):
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:stone": {"texture_rgb": (126, 126, 126)}})
    cache = tmp_path / "c.json"
    first = pack.load_cached(jar, str(cache))
    second = pack.load_cached(jar, str(cache))
    assert first == second == {"minecraft:stone": (126, 126, 126)}


def test_cache_roundtrip_with_stds(tmp_path):
    """缓存必须把纹理 std 一起存，否则重启后纹理匹配静默退化成纯颜色。"""
    jar = _make_pack(tmp_path / "p.zip", {"minecraft:stone": {"texture_rgb": (126, 126, 126)}})
    cache = tmp_path / "c.json"
    colors1, stds1 = pack.load_cached_stats(jar, str(cache))
    colors2, stds2 = pack.load_cached_stats(jar, str(cache))  # 命中缓存
    assert colors1 == colors2 == {"minecraft:stone": (126, 126, 126)}
    assert stds1 == stds2
    assert "minecraft:stone" in stds1 and stds1["minecraft:stone"] >= 0.0
    # 旧接口读同一个缓存文件也正常
    assert pack.load_cached(jar, str(cache)) == colors1


# --------------------------------------------------------------------------
# 真实客户端 jar（有就跑，没有跳过）
# --------------------------------------------------------------------------
from core.mcver import find_client_jar  # noqa: E402

# 优先环境变量，其次自动探测常见启动器目录；都没有就跳过这组测试
REAL_JAR = os.environ.get("MC_JAR") or (find_client_jar() or "")


@pytest.mark.skipif(not os.path.exists(REAL_JAR), reason="本机没有 26.3 客户端 jar")
def test_real_jar_parses():
    colors = pack.load_block_colors(REAL_JAR)
    # 数量会随剔除规则变化。2026-10-30 放开功能方块与外观类剔除后是 417 种
    assert len(colors) > 380, f"整方块数量太少：{len(colors)}"

    # 非整方块的形状不对，仍然不能混进来（台阶/门/栅栏放下去不是一格方块）
    for bad in ("minecraft:oak_fence", "minecraft:stone_slab", "minecraft:oak_door"):
        assert bad not in colors, f"{bad} 不是整方块，不该出现在调色板里"

    # 普通功能方块能正常放置，必须保留
    for ok in ("minecraft:bookshelf", "minecraft:crafting_table", "minecraft:furnace",
               "minecraft:beacon", "minecraft:barrel"):
        assert ok in colors, f"{ok} 能正常放置，不该被剔掉"

    # 用户点名不要的（会刷怪/执行命令/拆不掉）必须挡
    for bad in ("minecraft:spawner", "minecraft:trial_spawner", "minecraft:vault",
                "minecraft:command_block", "minecraft:chain_command_block",
                "minecraft:repeating_command_block", "minecraft:structure_block",
                "minecraft:jigsaw", "minecraft:bedrock",
                "minecraft:test_block", "minecraft:test_instance_block"):
        assert bad not in colors, f"{bad} 按用户要求应排除"

    # 树叶/釉面陶瓦保留：颜色就是它们的真实颜色
    for ok in ("minecraft:oak_leaves", "minecraft:red_glazed_terracotta",
               "minecraft:sea_lantern", "minecraft:dead_tube_coral_block"):
        assert ok in colors, f"{ok} 不该被剔掉"

    # 活珊瑚必须挡：离开水立刻变死珊瑚，整块从粉/紫/黄变成灰绿
    for bad in ("minecraft:tube_coral_block", "minecraft:brain_coral_block",
                "minecraft:fire_coral_block", "minecraft:horn_coral_block"):
        assert bad not in colors, f"{bad} 放下去会变成死珊瑚，颜色就不对了"

    # 矿石/粗矿块保留：匹配准则就是"平均色最接近"
    for ok in ("minecraft:copper_ore", "minecraft:redstone_ore",
               "minecraft:raw_iron_block", "minecraft:birch_log"):
        assert ok in colors, f"{ok} 不该被剔掉"

    # 唯一该挡的：仙人掌在墙里放不下去
    assert "minecraft:cactus" not in colors

    # 判定颜色合理性
    assert colors["minecraft:stone"][0] == colors["minecraft:stone"][1]  # 灰色，R≈G
    r, g, b = colors["minecraft:grass_block"]
    assert g > r and g > b
    assert colors["minecraft:sand"][0] > 190  # 沙是浅色


def test_live_coral_excluded_dead_kept():
    """活珊瑚必须排除，死珊瑚必须保留。

    这是"放得下但放完就不是它"的典型：活珊瑚离开水立刻变死珊瑚，
    整块从粉/紫/黄/青变成灰绿——色差算得再准，摆出来也不是那个颜色。
    dead_* 系列本来就是死的，放下去不会再变，颜色是真实的，保留。
    """
    for name in (
        "minecraft:tube_coral_block",
        "minecraft:brain_coral_block",
        "minecraft:bubble_coral_block",
        "minecraft:fire_coral_block",
        "minecraft:horn_coral_block",
        "minecraft:tube_coral",
        "minecraft:tube_coral_fan",
        "minecraft:tube_coral_wall_fan",
    ):
        assert not pack._is_building_block(name), f"{name} 离开水会变成死珊瑚"
    for name in (
        "minecraft:dead_tube_coral_block",
        "minecraft:dead_brain_coral",
        "minecraft:dead_tube_coral_fan",
    ):
        assert pack._is_building_block(name), f"{name} 本来就是死的，不该挡"


def test_suffix_blacklist_is_empty():
    """后缀黑名单必须是空的。

    以前靠后缀挡了 _leaves（半透明+tint）、_concrete_powder（悬空会塌）、
    _glazed_terracotta（带方向箭头）、_coral_block（离水变灰）、infested_*（冒蠹虫）。
    用户 2026-10-30 要求全部放出来——这些方块都能正常放置，颜色就是真实颜色。
    再往这里加条目要先有"实测放下去必然出错"的证据，不能凭想象。
    """
    assert pack.NON_BUILDING_SUFFIXES == ()
    assert pack.NON_BUILDING_PREFIXES == ()
    for name in (
        "minecraft:oak_leaves",
        "minecraft:black_concrete_powder",
        "minecraft:infested_stone",
        "minecraft:red_glazed_terracotta",
        "minecraft:dead_brain_coral_block",  # 死的本来就不变
    ):
        assert pack._is_building_block(name), f"{name} 能正常放置，不该被挡"
    # 正常建材当然还在
    for name in ("minecraft:stone", "minecraft:oak_planks", "minecraft:grass_block"):
        assert pack._is_building_block(name), name


def test_leaves_and_powder_kept(tmp_path):
    """树叶、混凝土粉末都要保留——它们能正常放置，颜色就是真实颜色。"""
    jar = _make_pack(
        tmp_path / "p.zip",
        {
            "minecraft:stone": {"texture_rgb": (126, 126, 126)},
            "minecraft:oak_leaves": {"texture_rgb": (120, 130, 110)},
            "minecraft:red_concrete_powder": {"texture_rgb": (150, 40, 38)},
        },
    )
    colors = pack.load_block_colors(jar)
    assert "minecraft:stone" in colors
    assert "minecraft:oak_leaves" in colors
    assert "minecraft:red_concrete_powder" in colors


def test_cactus_is_not_a_building_block():
    """仙人掌要求四周不能挨着别的方块，当体素放下去根本不成立。"""
    from core.pack import _is_building_block

    assert not _is_building_block("minecraft:cactus")


def test_light_blocks_exclude_powered_ones():
    """「自带光」的清单里不能混进需要充能的方块。

    红石灯、铜灯泡要红石信号才亮，未充能时是暗的——把它们算作照明方块，
    等于给用户一个颜色不对的方块。
    """
    from core.pack import LIGHT_BLOCKS

    for name in ("minecraft:redstone_lamp", "minecraft:copper_bulb",
                 "minecraft:waxed_copper_bulb", "minecraft:exposed_copper_bulb"):
        assert name not in LIGHT_BLOCKS, f"{name} 需要充能，不该算照明方块"
    # 真正自带光的必须在里面
    for name in ("minecraft:ochre_froglight", "minecraft:verdant_froglight",
                 "minecraft:pearlescent_froglight", "minecraft:shroomlight"):
        assert name in LIGHT_BLOCKS


def test_texture_std_filter_is_off_by_default():
    """纹理标准差只用来比"平不平"，不能当筛子。

    42 这个阈值曾经误伤 deepslate_diamond_ore(42.7)、nether_quartz_ore(48.4)、
    birch_log(54.4)、sea_lantern(47.9)、glowstone(62.6)——矿石、常用建材、照明方块
    全被挡；而真正该挡的机制方块 std 并不高，得靠黑名单。
    """
    import inspect

    from core import pack

    for fn in (pack._load_block_entries, pack.load_block_colors):
        default = inspect.signature(fn).parameters["max_texture_std"].default
        assert default is None, f"{fn.__name__} 的 max_texture_std 默认应该是 None"


def test_ordinary_functional_blocks_kept():
    """普通功能方块（书架、工作台、熔炉、信标、木桶）都要保留。

    它们能正常放置，作为"平均色最接近的方块"完全正当。
    """
    for name in (
        "minecraft:bookshelf",
        "minecraft:crafting_table",
        "minecraft:furnace",
        "minecraft:beacon",
        "minecraft:barrel",
        "minecraft:loom",
        "minecraft:crafter",
    ):
        assert pack._is_building_block(name), f"{name} 能正常放置，不该被挡"


def test_user_excluded_blocks():
    """用户 2026-10-30 点名排除的方块：会刷怪 / 执行命令 / 需要权限 / 拆不掉。

    这批不是审美判断，是用户明确要求。其中砂轮、讲台、切石机、箱子、铁砧等
    本来就被"整方块"筛子挡住了（模型有空隙或斜面），列进黑名单是为了将来
    放宽整方块筛选时不会偷偷混回来。
    """
    for name in (
        "minecraft:spawner",
        "minecraft:trial_spawner",
        "minecraft:vault",
        "minecraft:command_block",
        "minecraft:chain_command_block",
        "minecraft:repeating_command_block",
        "minecraft:structure_block",
        "minecraft:jigsaw",
        "minecraft:bedrock",
        "minecraft:test_block",
        "minecraft:test_instance_block",
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
    ):
        assert not pack._is_building_block(name), f"{name} 按用户要求应排除"

