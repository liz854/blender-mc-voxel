"""重力方块支撑规则的实机验证样本。

造一个小场景，左边是悬空的沙柱（底下全空），右边是石头托着的沙柱。
跑完 enforce_gravity_support 后：左边三格必须不再是沙子，右边两格必须还是沙子。
导出的 .litematic 丢进游戏 Load 一下就能一眼看出来。

用法：
    python tools/gravity_demo.py [schematics目录]
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import _paths  # noqa: E402

from core.gravity import enforce_gravity_support, is_gravity_block  # noqa: E402
from core.ir import BlockState, VoxelModel  # noqa: E402
from core.litematic import read_litematic, write_litematic  # noqa: E402
from core.ir import AIR  # noqa: E402
from core.palette import BlockPalette  # noqa: E402

JAR = _paths.client_jar()
CACHE = _paths.palette_cache()
DEFAULT_OUT = _paths.schematics_dir()
DATA_VERSION = 5023


def load_palette() -> BlockPalette:
    """有客户端 jar 就用真实方块色，没有退回兜底 96 色。"""
    if JAR:
        from core.pack import load_cached

        colors = load_cached(JAR, CACHE)
        if colors:
            print(f"[gravity] 色板 {len(colors)} 种（{os.path.basename(JAR)}）")
            return BlockPalette(colors)
    from core.palette import load_fallback_palette

    print("[gravity] 没找到客户端 jar，用兜底 96 色")
    return BlockPalette(load_fallback_palette())


def build_scene() -> VoxelModel:
    sx, sy, sz = 9, 6, 5
    m = VoxelModel((sx, sy, sz))

    # 地基：整层石头
    for x in range(sx):
        for z in range(sz):
            m.set(x, 0, z, BlockState("minecraft:stone"))

    # 左侧：悬空沙柱（y=1/2 是空气，沙子从 y=3 起）
    for y in (3, 4, 5):
        m.set(2, y, 2, BlockState("minecraft:sand"))

    # 右侧：石头托着的沙柱（y=1 直接压在石头上）
    for y in (1, 2):
        m.set(6, y, 2, BlockState("minecraft:sand"))

    return m


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    palette = load_palette()
    model = build_scene()
    before = model.total_blocks

    print(f"色板 {len(palette.names)} 种，场景 {model.size}，方块 {before}")
    print("--- 替换前 ---")
    for y in range(model.size[1]):
        print(
            f"  y={y}  x=2/z=2: {model.get(2, y, 2).name:<32}"
            f"x=6/z=2: {model.get(6, y, 2).name}"
        )

    n = enforce_gravity_support(
        model,
        palette,
        report=lambda cnt, olds, news: print(
            f"[gravity] {cnt} 个悬空的重力方块换成 {', '.join(news)}（原为 {', '.join(olds)}）"
        ),
    )

    print("--- 替换后 ---")
    for y in range(model.size[1]):
        print(
            f"  y={y}  x=2/z=2: {model.get(2, y, 2).name:<32}"
            f"x=6/z=2: {model.get(6, y, 2).name}"
        )

    path = os.path.join(out_dir, "gravity_check.litematic")
    write_litematic(
        model,
        path,
        mc_data_version=DATA_VERSION,
        name="gravity_check",
        description="左边悬空沙柱已被替换，右边石头托着的沙子保留",
    )

    # 读回来对账
    back = read_litematic(path)
    ok = True
    for y in (3, 4, 5):
        name = back.get(2, y, 2).name
        if is_gravity_block(name) or name == AIR:
            print(f"✗ 悬空格 (2,{y},2) 还是 {name}")
            ok = False
    for y in (1, 2):
        name = back.get(6, y, 2).name
        if name != "minecraft:sand":
            print(f"✗ 有支撑的格 (6,{y},2) 被误改成 {name}")
            ok = False
    if back.total_blocks != before:
        print(f"✗ 方块总数变了：{before} -> {back.total_blocks}")
        ok = False

    print(f"替换 {n} 格；读回校验 {'通过' if ok else '失败'}；文件 {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
