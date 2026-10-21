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
    stair = [{"from": [0, 0, 8], "to": [16, 8, 16]}]
    jar = _make_pack(
        tmp_path / "p.zip",
        {"minecraft:stone": {}, "minecraft:oak_stairs": {"elements": stair}},
    )
    with pack.ResourcePack(jar) as rp:
        assert rp.is_full_cube("minecraft:block/stone_model") is True
        assert rp.is_full_cube("minecraft:block/oak_stairs_model") is False
        assert rp.block_color("minecraft:oak_stairs", full_cube_only=True) is None

    colors = pack.load_block_colors(jar)
    assert "minecraft:oak_stairs" not in colors
    assert "minecraft:stone" in colors


# --------------------------------------------------------------------------
# 花纹理过滤
# --------------------------------------------------------------------------
def test_noisy_texture_filtered(tmp_path):
    jar = _make_pack(
        tmp_path / "p.zip",
        {
            "minecraft:white_concrete": {"texture_rgb": (240, 240, 240)},
            "minecraft:bookshelf": {"noisy": True},
        },
    )
    colors = pack.load_block_colors(jar)
    assert "minecraft:white_concrete" in colors
    assert "minecraft:bookshelf" not in colors


def test_blacklist_blocks_filtered(tmp_path):
    jar = _make_pack(
        tmp_path / "p.zip",
        {"minecraft:stone": {}, "minecraft:crafting_table": {}, "minecraft:chest": {}},
    )
    colors = pack.load_block_colors(jar)
    assert "minecraft:stone" in colors
    assert "minecraft:crafting_table" not in colors
    assert "minecraft:chest" not in colors


# --------------------------------------------------------------------------
# 灰度纹理 tint
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# 真实客户端 jar（有就跑，没有跳过）
# --------------------------------------------------------------------------
from core.mcver import find_client_jar  # noqa: E402

# 优先环境变量，其次自动探测常见启动器目录；都没有就跳过这组测试
REAL_JAR = os.environ.get("MC_JAR") or (find_client_jar() or "")


@pytest.mark.skipif(not os.path.exists(REAL_JAR), reason="本机没有 26.3 客户端 jar")
def test_real_jar_parses():
    colors = pack.load_block_colors(REAL_JAR)
    # 数量会随剔除规则变化（316 → 剔釉面陶瓦 → 剔珊瑚/矿石/粗矿块 → 277），
    # 只要没掉到"色板不够用"的量级就行
    assert len(colors) > 250, f"整方块数量太少：{len(colors)}"

    # 非整方块和功能方块不能混进来
    for bad in ("minecraft:oak_stairs", "minecraft:stone_slab", "minecraft:oak_door",
                "minecraft:bookshelf", "minecraft:crafting_table"):
        assert bad not in colors, f"{bad} 不该出现在调色板里"

    # 珊瑚离水会变死珊瑚（整块变灰），选它等于选错颜色，必须剔
    for bad in ("minecraft:tube_coral_block", "minecraft:brain_coral_block",
                "minecraft:dead_tube_coral_block"):
        assert bad not in colors, f"{bad} 不该出现在调色板里"

    # 矿石/粗矿块**保留**：有斑点但有可能是平均色最近的那个，匹配准则就是"平均色最接近"
    for ok in ("minecraft:copper_ore", "minecraft:redstone_ore",
               "minecraft:raw_iron_block"):
        assert ok in colors, f"{ok} 不该被剔掉"

    # 判定颜色合理性
    assert colors["minecraft:stone"][0] == colors["minecraft:stone"][1]  # 灰色，R≈G
    r, g, b = colors["minecraft:grass_block"]
    assert g > r and g > b
    assert colors["minecraft:sand"][0] > 190  # 沙是浅色


# ---------------- 建材资格过滤 ----------------

def test_non_building_suffixes_excluded():
    """树叶/混凝土粉末能过"整方块+纯色"两关，但做替代方块会出事。"""
    assert not pack._is_building_block("minecraft:oak_leaves")
    assert not pack._is_building_block("minecraft:pale_oak_leaves")
    assert not pack._is_building_block("minecraft:black_concrete_powder")
    assert not pack._is_building_block("minecraft:infested_stone")
    # 釉面陶瓦有方向箭头图案；珊瑚离水变色
    assert not pack._is_building_block("minecraft:red_glazed_terracotta")
    assert not pack._is_building_block("minecraft:tube_coral_block")
    assert not pack._is_building_block("minecraft:dead_brain_coral_block")
    # 矿石和粗矿块保留：有斑点不影响"平均色最接近"这个准则
    assert pack._is_building_block("minecraft:diamond_ore")
    assert pack._is_building_block("minecraft:raw_gold_block")
    # 正常建材不能被误杀
    for name in (
        "minecraft:stone",
        "minecraft:black_concrete",
        "minecraft:oak_planks",
        "minecraft:grass_block",
        "minecraft:deepslate_bricks",
    ):
        assert pack._is_building_block(name), name


def test_leaves_filtered_from_real_palette(tmp_path):
    """假资源包里的树叶必须被 load_block_colors 剔掉。"""
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
    assert "minecraft:oak_leaves" not in colors
    assert "minecraft:red_concrete_powder" not in colors
