"""把 examples/05_bugtest.blend 导出成游戏里能 Load 的投影 + 预览图 + 对照表。

    blender.exe -b examples/05_bugtest.blend --python tools/bugtest_export.py

产出：
  <游戏目录>/schematics/bugtest_all.litematic     9 个块按原样摆好，Load 一次看全
  <游戏目录>/schematics/bugtest_1_flat_wall.litematic  单块，近看细节
  ...（每个块一个）
  dist/bugtest/*.png                              每块的等距预览（不带游戏光照，只作参考）
  dist/bugtest/README.md                          对照表：每块应该长什么样

方块尺寸固定 0.5（= 每块 16×16 格），实心填充、重力规则都开着，
和你从面板点"导出投影"走的是同一条流水线。
"""

from __future__ import annotations

import os
import sys

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import _paths  # noqa: E402

from bl_ext.user_default.mc_voxel_export.core import build as core_build
from bl_ext.user_default.mc_voxel_export.core import litematic as core_litematic
from bl_ext.user_default.mc_voxel_export.core import mcver, mesh_data
from bl_ext.user_default.mc_voxel_export.core.palette import BlockPalette
from bl_ext.user_default.mc_voxel_export.ui import preview_render
from bl_ext.user_default.mc_voxel_export.ui.paths import cache_file

ROOT = _paths.ROOT
SHOTS = os.path.join(_paths.DIST, "bugtest")
GAME = _paths.schematics_dir()
BLOCK_SIZE = 0.5

# (文件名, 中文名, 物件列表, 期望看到什么)
GROUPS = (
    ("1_flat_wall", "T1 平墙", ["T1_flat_wall"], "整面米白，**一种颜色**，不能有深浅两色斜着拼起来"),
    ("2_gradient", "T2 渐变", ["T2_gradient"], "红→蓝平滑过渡，台阶均匀，没有突然跳色"),
    (
        "3_hole_ok",
        "T3 挖洞（先挖后涂）",
        ["T3_hole_ok"],
        "中间门 + 两侧窗，洞口边缘和墙面同色，不能出现黑边",
    ),
    (
        "4_sand",
        "T4 悬空沙柱",
        ["T4_sand_base", "T4_sand_pillar"],
        "底下石头托着的那截是**沙黄**，被挖断后**悬空的上半截应该变成砂岩色**（重力规则）",
    ),
    (
        "5_thin",
        "T5 薄板 + 细柱",
        ["T5_plate"] + [f"T5_column{i}" for i in (1, 2, 3)],
        "0.5 米厚（正好 1 格）的红板完整，三根细柱连续不断",
    ),
    (
        "3b_hole_painted_first",
        "T3B 挖洞（先涂后挖）",
        ["T3B_hole_painted_first"],
        "故意用错误顺序做的。**结果要跟 T3 一样均匀**，不能有黑斑或深浅分界",
    ),
    (
        "6_gray",
        "T6 五级灰阶",
        [f"T6_gray{i}" for i in range(1, 6)],
        "五条从深到浅能分出来，相邻两级不该撞成同一个方块",
    ),
    ("7_pyramid", "T7 金字塔", ["T7_pyramid"], "斜面是阶梯状但连续，不能有空洞或漏面"),
    (
        "8_colors",
        "T8 四原色",
        ["T8_red", "T8_yellow", "T8_blue", "T8_white"],
        "红黄蓝白各自明显，不能红变陶瓦、白变灰这种系统性偏色",
    ),
)


def load_palette() -> BlockPalette:
    jar = mcver.find_client_jar()
    if not jar:
        raise RuntimeError("找不到客户端 jar")
    from bl_ext.user_default.mc_voxel_export.core.pack import load_cached_stats

    colors, stds = load_cached_stats(jar, cache_file())
    print(f"[bugtest] 色板 {len(colors)} 种（{os.path.basename(jar)}），纹理匹配开")
    return BlockPalette(colors, stds=stds)


def merge(names) -> bpy.types.Object:
    """把一组物件复制一份再 join，不动原场景。"""
    objs = [bpy.data.objects[n] for n in names if n in bpy.data.objects]
    if not objs:
        raise RuntimeError(f"场景里没有这些物件：{names}")
    bpy.ops.object.select_all(action="DESELECT")
    for ob in objs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.duplicate()
    dup = list(bpy.context.selected_objects)
    bpy.context.view_layer.objects.active = dup[0]
    if len(dup) > 1:
        bpy.ops.object.join()
    return bpy.context.active_object


def voxelize(ob, palette):
    bpy.context.view_layer.objects.active = ob
    v, t, c = mesh_data.mesh_to_mc(ob)
    return core_build.build_voxel_model(
        v, t, c, block_size=BLOCK_SIZE, palette=palette, solid=True, no_floating_gravity=True
    )


def describe(model):
    """按方块数从多到少列前几种，方便对着截图看。"""
    from collections import Counter

    cnt = Counter()
    sx, sy, sz = model.size
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                st = model.get(x, y, z)
                if st.name != "minecraft:air":
                    cnt[st.name.split(":", 1)[-1]] += 1
    return ", ".join(f"{n}×{c}" for n, c in cnt.most_common(6))


def main():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(GAME, exist_ok=True)
    palette = load_palette()
    rows = []

    for slug, title, names, expect in GROUPS:
        ob = merge(names)
        model = voxelize(ob, palette)
        stem = f"bugtest_{slug}"
        out = os.path.join(GAME, stem + ".litematic")
        core_litematic.write_litematic(
            model,
            out,
            mc_data_version=5023,
            name=stem,
            description=f"{title}：{expect}",
        )
        png = os.path.join(SHOTS, stem + ".png")
        preview_render.render_iso(model, palette, png, size=640)
        summary = describe(model)
        print(f"[bugtest] {title:<22} {model.size} {model.total_blocks:>6} 块  {summary}")
        rows.append((title, stem, model.size, model.total_blocks, summary, expect))
        bpy.data.objects.remove(ob, do_unlink=True)

    # 总览：9 个块按原坐标一起体素化
    all_names = [n for _, _, names, _ in GROUPS for n in names]
    ob = merge(all_names)
    model = voxelize(ob, palette)
    out = os.path.join(GAME, "bugtest_all.litematic")
    core_litematic.write_litematic(
        model,
        out,
        mc_data_version=5023,
        name="bugtest_all",
        description="9 个测试块总览，逐块对照 dist/bugtest/README.md",
    )
    preview_render.render_iso(model, palette, os.path.join(SHOTS, "bugtest_all.png"), size=1100)
    print(f"[bugtest] 总览 {model.size} {model.total_blocks} 块 -> {out}")
    bpy.data.objects.remove(ob, do_unlink=True)

    # 对照表
    lines = [
        "# bugtest 对照表",
        "",
        "方块尺寸 0.5，实心填充，重力规则开。游戏里 Load 对应的 `.litematic`，"
        "对着下面「应该看到」一栏检查，不对就截图给我，注明是哪一块。",
        "",
        "| 块 | 投影文件 | 尺寸 | 方块数 | 实际用到的主要方块 | 应该看到 |",
        "|---|---|---|---|---|---|",
    ]
    for title, stem, size, total, summary, expect in rows:
        lines.append(
            f"| {title} | `{stem}.litematic` | {'×'.join(map(str, size))} | {total:,} | {summary} | {expect} |"
        )
    lines += [
        "",
        "## 怎么用",
        "",
        "1. 进游戏，`M` → **Load Schematic**（Litematica 的投影管理器里选 `bugtest_all`）",
        "2. 站远一点，把 9 个块都框进画面，**先拍一张总览**",
        "3. 哪块看着不对，再单独 Load 那一块的投影，走近拍一张",
        "4. 角度尽量正对，别让太阳直射或背光（MC 的朝向明暗不一样，会影响判断）",
        "",
        "## 这些**不是** bug",
        "",
        "- **同一面墙在游戏里亮度不均**：MC 按朝向打光，顶面最亮、东西面最暗，同一个方块换个面看就是深浅不同。"
        "判断颜色对不对看**纹理**（同一个方块纹理一致就对了），别看明暗",
        "- **斜面是阶梯状的**：体素化本来就是把斜面拆成一格一格，T7 金字塔必然有台阶，不是漏面",
        "- **渐变是一级一级跳的**：色板只有 300 种方块，红到蓝不可能连续过渡",
        "- **悬空的沙变成砂岩色**：这是重力规则，故意的（不换的话投影摆出去会塌成一地沙子）",
        "- **两个颜色落到同一个方块**：两种色在 Lab 空间离同一个方块最近时会撞色，属于色板分辨率的极限",
        "- **T4 里有零星几个相近色的方块**：沙子和末地石/可疑沙的平均色只差几个色阶，"
        "边界格抖动是正常的，只要贴地那截是沙、悬空那截是砂岩就没问题",
        "- **四个应该一样大的方块，看着不一样大**：纯色方块（混凝土）没有格子边界，"
        "带纹理的方块（红石块、金块）每一格都有边框，观感上会显得更大。"
        "实测 T8 四个象限各 64 格 = 4×4×4，完全一致",
        "- **渐变 / 灰阶里冒出带斑点的方块**：纹理感知匹配开着，平色区域会优先选平纹理的方块；"
        "但渐变区域本身颜色在变，邻域粗糙度不算低，仍可能选到纹理较花的方块。"
        "想要更平就用面板的「只用这些方块」限定",
        "",
        "对照表里「实际用到的主要方块」是这次导出真正选中的方块，"
        "**这一列和你截图里看到的纹理不一致，就是 bug**。",
        "",
    ]
    with open(os.path.join(SHOTS, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[bugtest] 对照表 -> {os.path.join(SHOTS, 'README.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
