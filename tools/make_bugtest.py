"""生成 examples/05_bugtest.blend —— 9 个专门用来"进游戏截图找 bug"的小方块群。

    blender.exe -b --python tools/make_bugtest.py

每个块针对一类历史上踩过、或者容易静默出错的点，涂成特定颜色。
用 `tools/bugtest_export.py` 可以一次性导出成 1 个总览投影 + 9 个单块投影，
Load 之后对着 `dist/bugtest/README.md` 里的对照表逐条核对。

布局（Blender 的 X/Y，MC 里 z = -y）：

    T1 平墙        T2 渐变        T3 挖洞(先挖后涂)
    T4 悬空沙柱    T5 薄板细柱    T3B 挖洞(先涂后挖)
    T6 灰阶        T7 金字塔      T8 四原色

每个块 8×8 米，间隔 6 米，默认方块尺寸 0.5 → 每块 16×16 格。
"""

from __future__ import annotations

import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples")

# 颜色（线性 0-1，写进 FLOAT_COLOR 后 x255 就是面板里看到的 sRGB 值）
WALL = (0.86, 0.83, 0.75)  # 米白
PALE = (0.80, 0.80, 0.82)  # 浅灰
SAND = (0.859, 0.812, 0.639)  # 沙黄，取自 jar 里 sand 的平均色 (219,207,163)，保证落到 sand
RED = (0.80, 0.12, 0.10)
BRICK = (0.55, 0.22, 0.18)
BLUE = (0.10, 0.25, 0.80)
YELLOW = (0.90, 0.80, 0.10)
WHITE = (0.95, 0.95, 0.95)
STONE = (0.36, 0.36, 0.36)

SIZE = 8.0  # 每块边长
STEP = 14.0  # 中心间距（8 米块 + 6 米间隔）

# 3×3 的中心坐标
CX = (-STEP, 0.0, STEP)
CY = (STEP, 0.0, -STEP)  # 从上到下（Blender +Y 是屏幕里"往里"，这里只是排布）


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob


def paint_flat(ob, rgb):
    attr = ob.data.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
    for d in attr.data:
        d.color = (rgb[0], rgb[1], rgb[2], 1.0)


def paint_by_x(ob, c0, c1):
    """沿 X 轴线性渐变：顶点色按 x 位置插值。"""
    attr = ob.data.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
    xs = [v.co.x for v in ob.data.vertices]
    lo, hi = min(xs), max(xs)
    span = (hi - lo) or 1.0
    for i, v in enumerate(ob.data.vertices):
        t = (v.co.x - lo) / span
        attr.data[i].color = tuple(c0[k] + (c1[k] - c0[k]) * t for k in range(3)) + (1.0,)


def cut(target, size, loc):
    """布尔差集。注意：必须在 paint 之前调用，否则新顶点的颜色是纯黑。"""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    cutter = bpy.context.active_object
    cutter.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    mod = target.modifiers.new("cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def vertical_wall(name, cx, cy, rgb, holes=(), paint_first=False):
    """一块 8×8、0.6 米厚的墙，可以在上面挖洞。

    paint_first=True 用来复现"先涂色后布尔"的错误顺序，验证
    core/mesh_data._fill_missing_colors 能把纯黑顶点补回来。
    """
    wall = box(name, (SIZE, 0.6, SIZE), (cx, cy, SIZE / 2))
    if paint_first:
        paint_flat(wall, rgb)
    for hsize, hloc in holes:
        cut(wall, hsize, hloc)
    if not paint_first:
        paint_flat(wall, rgb)
    return wall


def build():
    reset()
    parts = []

    # --- T1 平墙：最大的一片单色面，用来抓"同一面墙出现深浅两色"---
    parts.append(vertical_wall("T1_flat_wall", CX[0], CY[0], WALL))

    # --- T2 渐变：细分后按 x 涂色，红 → 蓝，检查色阶连续、不跳变 ---
    grad = box("T2_gradient", (SIZE, 0.6, SIZE), (CX[1], CY[0], SIZE / 2))
    bpy.context.view_layer.objects.active = grad
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.subdivide(number_cuts=7)
    bpy.ops.object.mode_set(mode="OBJECT")
    paint_by_x(grad, RED, BLUE)
    parts.append(grad)

    # --- T3 挖洞（正确顺序：先挖后涂）---
    win = (1.5, 2.0, 1.5)
    parts.append(
        vertical_wall(
            "T3_hole_ok",
            CX[2],
            CY[0],
            PALE,
            holes=(
                ((2.0, 2.0, 3.0), (CX[2], CY[0], 1.5)),  # 门
                (win, (CX[2] - 2.4, CY[0], 4.6)),
                (win, (CX[2] + 2.4, CY[0], 4.6)),
            ),
        )
    )

    # --- T4 悬空沙柱：底座托着的一段保留沙色，被挖断后悬空的一段必须换掉 ---
    base = box("T4_sand_base", (3.0, 3.0, 1.0), (CX[0], CY[1], 0.5))
    paint_flat(base, STONE)
    parts.append(base)
    pillar = box("T4_sand_pillar", (1.0, 1.0, 7.0), (CX[0], CY[1], 4.5))
    cut(pillar, (1.4, 1.4, 2.0), (CX[0], CY[1], 4.0))  # 中间挖断
    paint_flat(pillar, SAND)
    parts.append(pillar)

    # --- T5 薄板 + 细柱：0.5 米厚（正好 1 格），检查薄几何不漏块 ---
    plate = box("T5_plate", (SIZE, SIZE, 0.5), (CX[1], CY[1], 0.25))
    paint_flat(plate, RED)
    parts.append(plate)
    for i, dx in enumerate((-2.6, 0.0, 2.6)):
        col = box(f"T5_column{i + 1}", (0.5, 0.5, 6.0), (CX[1] + dx, CY[1], 3.5))
        paint_flat(col, RED)
        parts.append(col)

    # --- T3B 挖洞（错误顺序：先涂后挖）→ 靠补色兜底，结果应该和 T3 一样均匀 ---
    parts.append(
        vertical_wall(
            "T3B_hole_painted_first",
            CX[2],
            CY[1],
            PALE,
            holes=(( (2.0, 2.0, 3.0), (CX[2], CY[1], 1.5)),),
            paint_first=True,
        )
    )

    # --- T6 五级灰阶：同高竖条，检查色板分辨率（相邻两级不该落到同一方块）---
    # 条宽 1.2 + 间隔 0.5（正好一格），免得挨太近体素化后粘在一起、边缘混色
    for i, g in enumerate((0.10, 0.30, 0.50, 0.70, 0.90)):
        w, gap = 1.2, 0.5
        x = CX[0] - SIZE / 2 + w / 2 + i * (w + gap)
        bar = box(f"T6_gray{i + 1}", (w, 0.6, SIZE), (x, CY[2], SIZE / 2))
        paint_flat(bar, (g, g, g))
        parts.append(bar)

    # --- T7 金字塔：45° 斜面，检查阶梯连续、没有空洞 ---
    radius = SIZE / 2 * math.sqrt(2.0)
    bpy.ops.mesh.primitive_cone_add(
        vertices=4,
        radius1=radius,
        radius2=0.0,
        depth=4.0,
        location=(CX[1], CY[2], 2.0),
        rotation=(0.0, 0.0, math.radians(45.0)),
    )
    pyr = bpy.context.active_object
    pyr.name = "T7_pyramid"
    paint_flat(pyr, BRICK)
    parts.append(pyr)

    # --- T8 四原色：检查有没有系统性偏色（红变陶瓦、白变骨块这种）---
    # 块之间留 0.5 米（正好一格）缝隙，免得挨太近时边缘格颜色互相插值
    for i, (name, rgb) in enumerate(
        (("red", RED), ("yellow", YELLOW), ("blue", BLUE), ("white", WHITE))
    ):
        dx = -1.1 if i % 2 == 0 else 1.1
        dy = 1.1 if i < 2 else -1.1
        cube = box(f"T8_{name}", (1.7, 1.7, 1.7), (CX[2] + dx, CY[2] + dy, 0.85))
        paint_flat(cube, rgb)
        parts.append(cube)

    bpy.ops.object.select_all(action="DESELECT")
    return parts


def main():
    os.makedirs(OUT, exist_ok=True)
    parts = build()

    props = bpy.context.scene.mc_voxel
    props.block_size = 0.5
    props.solid_fill = True
    props.gravity_support = True
    props.auto_fit_height = True

    path = os.path.join(OUT, "05_bugtest.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)

    print(f"[bugtest] {path}")
    print(f"[bugtest] {len(parts)} 个物件：")
    for ob in parts:
        d = ob.dimensions
        print(f"          {ob.name:<26} {d.x:.1f} x {d.y:.1f} x {d.z:.1f} 米")
    return 0


if __name__ == "__main__":
    sys.exit(main())
