"""真机演示：Suzanne 猴头 -> .litematic + 预览渲染。

    blender -b --python tools/blender_suzanne.py -- [block_size] [--solid]

产出：
  <schematics>/suzanne.litematic   直接进游戏 Load
  dist/suzanne_preview.png         Workbench 渲染的体素预览

为什么要给顶点色：Suzanne 没有贴图也没有顶点色，不涂的话体素全是白色，
出来是一坨纯白方块，看不出五官。这里按脸的朝向分区涂色。
"""

from __future__ import annotations

import math
import os
import sys
import traceback

import bpy
import numpy as np

# ---- 路径（自动探测客户端 jar / schematics 目录，别写死本机路径）----
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _paths  # noqa: E402

ROOT = _paths.ROOT
SCHEMATICS = _paths.schematics_dir()
JAR = _paths.client_jar()
DIST = _paths.DIST

# ---- 配色（sRGB 0-255，会经 Lab LUT 映射到最近的方块）----
FUR = (0x60, 0x3C, 0x20)      # 深棕（brown_concrete 一档）
FUR_LIT = (0x7A, 0x56, 0x38)  # 亮棕（coarse_dirt 一档，别太黄）
FACE = (0xDB, 0xD3, 0xA0)     # 面部浅米（sand）
FACE_DARK = (0x98, 0x5E, 0x43)  # 面部阴影（terracotta）

# Suzanne 眼窝中心，MC 坐标 (x, y=高度)。从正视正交渲染量出来的：
# size=2 的 Suzanne 眼窝在 Blender (±0.506, z=+0.459)，脸朝 -Y → MC 的 +Z。
# 左右对称，验算 (17 + 38) / 2 ≈ 27.5 = 模型 x 中心。
EYE_CENTERS = [(-0.506, 0.459), (0.506, 0.459)]


def colorize(obj):
    """按 Suzanne 的几何分区涂顶点色。

    Blender 里 Suzanne 脸朝 -Y，头顶 +Z。规则：
      - 脸盘（-Y 侧、|x| 不太宽、z 在中部偏下）-> 浅米色
      - 眼窝、脸盘边缘 -> 深一点的陶土色
      - 其余（头顶、后脑、耳朵外侧）-> 棕色，按高度提亮
    """
    mesh = obj.data
    attr = mesh.color_attributes.get("Col")
    if attr is None:
        attr = mesh.color_attributes.new(name="Col", type="BYTE_COLOR", domain="CORNER")
    mesh.color_attributes.active_color = attr

    co = np.array([v.co[:] for v in mesh.vertices], dtype=np.float64)
    lo, hi = co.min(axis=0), co.max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    n = (co - lo) / span  # 0..1 归一化

    # 脸盘用球面圈：Suzanne size=2 时脸中心在 (0, -0.85, 0.0) 附近，半径约 0.78
    # （比按归一化比例切更贴合几何，归一化切出来会把整张前脸都涂白）
    face_center = np.array([0.0, -0.85, 0.0])
    d_face = np.linalg.norm(co - face_center, axis=1)
    face_zone = d_face < 0.80

    vertical = n[:, 2]
    # 头顶提亮：平滑插值，别硬切（硬切+插值会蹦出浅木色木板，显脏）
    lit = np.clip((vertical - 0.45) / 0.45, 0.0, 1.0)[:, None]
    rgb = np.zeros((len(co), 3), dtype=np.float64)
    rgb[:] = np.asarray(FUR, dtype=np.float64) * (1 - lit) + np.asarray(FUR_LIT, dtype=np.float64) * lit
    rgb[face_zone] = FACE

    # 面与面之间插值：写 CORNER 域，每个 loop 取它顶点的颜色
    vidx = np.zeros(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", vidx)
    col = rgb[vidx] / 255.0
    rgba = np.concatenate([col, np.ones((len(col), 1))], axis=1)
    attr.data.foreach_set("color", rgba.reshape(-1))
    mesh.update()

    print(f"[color] 脸盘顶点 {int(face_zone.sum())} / 总 {len(co)}")


def paint_eyes(model, origin, bs, centers, radius=2, name="minecraft:black_concrete"):
    """在体素模型上点眼珠。

    为什么要手工点：Suzanne 本身只有两个浅浅的眼窝（正视渲染能看到，
    深度约 0.1 单位 = 2 体素），0.05 精度下被体素化直接抹平了，
    导出来就是一张平脸。所以不改几何，直接在脸正面涂两块深色。

    centers : MC 坐标 [(x, y), …]，y 是高度（= Blender 的 z）
    只涂每列最靠脸的一格（脸朝 +z），不会往内部糊。
    """
    from bl_ext.user_default.mc_voxel_export.core.ir import BlockState

    sx, sy, sz = model.size
    arr = model.blocks.reshape(sy, sz, sx)  # [y][z][x]
    idx = model.add_state(BlockState(name))
    painted = 0
    for ex, ey in centers:
        ix = int(round((ex - origin[0]) / bs))
        iy = int(round((ey - origin[1]) / bs))
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                x, y = ix + dx, iy + dy
                if not (0 <= x < sx and 0 <= y < sy):
                    continue
                col = arr[y, :, x]
                nz = np.nonzero(col)[0]
                if nz.size == 0:  # 这一列没有方块（脸轮廓外），跳过
                    continue
                col[nz.max()] = idx
                painted += 1
    return painted


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    block_size = 0.08
    solid = False
    for a in argv:
        if a == "--solid":
            solid = True
        else:
            try:
                block_size = float(a)
            except ValueError:
                pass

    from bl_ext.user_default.mc_voxel_export.core import build as core_build
    from bl_ext.user_default.mc_voxel_export.core import litematic as core_litematic
    from bl_ext.user_default.mc_voxel_export.core import mesh_data, mcver
    from bl_ext.user_default.mc_voxel_export.core.palette import (
        BlockPalette,
        load_fallback_palette,
    )

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "Suzanne"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.shade_smooth()

    colorize(obj)

    verts, tris, colors = mesh_data.mesh_to_mc(obj)
    print(f"[mesh] verts={len(verts)} tris={len(tris)}")

    colors_dict = None
    if os.path.exists(JAR):
        try:
            from bl_ext.user_default.mc_voxel_export.core import pack as core_pack

            colors_dict = core_pack.load_cached(JAR, os.path.join(DIST, "block_colors.json"))
            print(f"[palette] 用客户端资源包：{len(colors_dict)} 种整方块")
        except Exception as exc:
            print(f"[palette] 读 jar 失败（{exc}），退回兜底")
            colors_dict = None
    if not colors_dict:
        colors_dict = load_fallback_palette()
        print(f"[palette] 用兜底色板：{len(colors_dict)} 色")
    palette = BlockPalette(colors_dict)

    origin, size = core_build.grid_from_bounds(verts, block_size)
    model = core_build.build_voxel_model(
        verts, tris, colors,
        block_size=block_size, palette=palette,
        solid=solid,
        origin=origin, size=size,
        progress=lambda s: print(f"[step] {s}"),
    )
    print(f"[voxel] size={model.size} blocks={model.total_blocks} palette={len(model.palette)} solid={solid}")

    eyes = paint_eyes(model, origin, block_size, EYE_CENTERS, radius=2)
    print(f"[eyes] 点了 {eyes} 格黑眼珠（radius=2）")
    print("[voxel] 用到的方块:", sorted({s.name for s in model.palette if not s.is_air}))

    if model.total_blocks == 0:
        raise RuntimeError("体素为空，block_size 可能过大")

    # 预览要按方块真实颜色着色
    model._colors = {k: tuple(int(c) for c in v) for k, v in colors_dict.items()}
    model._colors.setdefault("minecraft:black_concrete", (8, 10, 15))  # 眼珠，兜底色板里可能没有

    os.makedirs(DIST, exist_ok=True)
    os.makedirs(SCHEMATICS, exist_ok=True)
    out = os.path.join(SCHEMATICS, "suzanne.litematic")
    dv = mcver.resolve_data_version(None, mcver.data_version_for_release("26.3"))
    core_litematic.write_litematic(
        model, out, mc_data_version=dv, name="suzanne", author="mc_voxel_export"
    )
    print(f"[export] {out}  ({os.path.getsize(out)/1024:.1f} KB) DataVersion={dv}")

    # 等距预览图：跟游戏里摆出来的样子一致（Workbench，无头可渲）
    png = os.path.join(DIST, "suzanne_preview.png")
    try:
        from bl_ext.user_default.mc_voxel_export.ui import preview_render

        info = preview_render.render_iso(model, palette, png, size=1000)
        print(f"[render] {info['path']} {info['width']}x{info['height']} {info['faces']} 个面")
    except Exception:
        traceback.print_exc()
        print("[render] 渲染失败，但投影已导出")

    print("=== DONE ===")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 2
    sys.exit(code)
