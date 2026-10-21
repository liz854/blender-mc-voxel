"""生成 examples/04_house.blend —— 一栋小房子，手动练手用。

    blender.exe -b --python tools/make_house.py

尺寸是米：20 × 20 的底、墙高 8、屋顶高 4。默认方块尺寸 0.5，
体素化后大约 40 × 40 × 25 格，一万多块方块，体素化一两秒。
墙上挖了门和一扇窗，屋顶是四棱锥，右侧有烟囱。

色块是分开涂的（墙米白、屋顶砖红、地板木色、烟囱深灰），
体素化时调色板会分别落到羊毛/混凝土/陶瓦这类整方块上。
"""

from __future__ import annotations

import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples")

WALL = (0.86, 0.83, 0.75)  # 米白
ROOF = (0.55, 0.22, 0.18)  # 砖红
FLOOR = (0.52, 0.38, 0.26)  # 木色
STACK = (0.34, 0.32, 0.31)  # 深灰

HALF = 10.0  # 半边长
WALL_H = 8.0
WALL_T = 0.6


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def box(name, size, loc):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob


def paint(ob, rgb):
    attr = ob.data.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
    for d in attr.data:
        d.color = (rgb[0], rgb[1], rgb[2], 1.0)


def cut(target, cutter):
    """布尔差集，用来在墙上挖门窗。"""
    mod = target.modifiers.new("cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def build():
    reset()
    parts = []

    # 地板
    floor = box("Floor", (20.0, 20.0, 0.5), (0.0, 0.0, -0.25))
    paint(floor, FLOOR)
    parts.append(floor)

    # 四面墙（前墙 -Y、后墙 +Y、左 -X、右 +X）
    walls = []
    for name, size, loc in (
        ("WallFront", (20.0, WALL_T, WALL_H), (0.0, -HALF + WALL_T / 2, WALL_H / 2)),
        ("WallBack", (20.0, WALL_T, WALL_H), (0.0, HALF - WALL_T / 2, WALL_H / 2)),
        ("WallLeft", (WALL_T, 20.0, WALL_H), (-HALF + WALL_T / 2, 0.0, WALL_H / 2)),
        ("WallRight", (WALL_T, 20.0, WALL_H), (HALF - WALL_T / 2, 0.0, WALL_H / 2)),
    ):
        walls.append(box(name, size, loc))

    # 门 + 两扇窗（布尔差集）
    cut(walls[0], box("DoorCut", (3.0, 2.0, 4.5), (0.0, -HALF, 2.25)))
    cut(walls[2], box("WinLCut", (2.0, 2.5, 2.5), (-HALF, 0.0, 4.5)))
    cut(walls[3], box("WinRCut", (2.0, 2.5, 2.5), (HALF, 0.0, 4.5)))

    # 涂色必须在布尔**之后**。布尔产生的新顶点颜色是纯黑，而且不会继承原顶点色，
    # 先涂后挖的结果就是墙上一片黑斑，再顺着三角形插值晕成深浅两色的斜分界。
    for w in walls:
        paint(w, WALL)
    parts.extend(walls)

    # 屋顶：四棱锥，底边对齐 20×20
    radius = HALF * math.sqrt(2.0) * 1.06
    bpy.ops.mesh.primitive_cone_add(
        vertices=4,
        radius1=radius,
        radius2=0.0,
        depth=4.0,
        location=(0.0, 0.0, WALL_H + 2.0),
        rotation=(0.0, 0.0, math.radians(45.0)),
    )
    roof = bpy.context.active_object
    roof.name = "Roof"
    paint(roof, ROOF)
    parts.append(roof)

    # 烟囱
    stack = box("Chimney", (1.6, 1.6, 4.5), (5.0, 5.0, WALL_H + 1.75))
    paint(stack, STACK)
    parts.append(stack)

    # 合并成一个物体
    bpy.ops.object.select_all(action="DESELECT")
    for ob in parts:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    house = bpy.context.active_object
    house.name = "House"

    # 布尔挖洞会留下朝向不一致的面，法线乱了体素化时容易出黑斑
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    return house


def main():
    os.makedirs(OUT, exist_ok=True)
    house = build()

    props = bpy.context.scene.mc_voxel
    props.block_size = 0.5
    props.solid_fill = True
    props.gravity_support = True
    props.auto_fit_height = True

    path = os.path.join(OUT, "04_house.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)

    dims = house.dimensions
    print(f"[house] {path}")
    print(f"[house] 尺寸 {dims.x:.1f} x {dims.y:.1f} x {dims.z:.1f} 米，顶点 {len(house.data.vertices)}")
    print(f"[house] 方块尺寸 0.5 -> 约 {dims.x/0.5:.0f} x {dims.y/0.5:.0f} x {dims.z/0.5:.0f} 格")
    return 0


if __name__ == "__main__":
    sys.exit(main())
