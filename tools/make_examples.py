"""生成 examples/ 下的三个示例场景。

    blender.exe -b --python tools/make_examples.py

三个场景对应三种典型用法：
  01_cube_gradient.blend  灰阶渐变立方体 —— 看调色板是不是自然过渡（不能全是 stone）
  02_sphere.blend         球体 —— 曲面，测体素化精度与外壳模式
  03_suzanne.blend        Suzanne 猴头 —— 复杂网格 + 顶点色，真实模型的样子

顶点色就是配色依据：没有顶点色的模型会全白，所以每个场景都涂了。
"""

from __future__ import annotations

import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "examples")


def reset():
    bpy.ops.wm.read_homefile(use_empty=True)


def paint(obj, fn):
    """给物体刷顶点色。fn(co) -> (r,g,b)，取值 0..1。"""
    mesh = obj.data
    attr = mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="POINT")
    for i, vert in enumerate(mesh.vertices):
        r, g, b = fn(vert.co)
        attr.data[i].color = (r, g, b, 1.0)
    return attr


def setup_props(block_size=1.0, solid=True):
    props = bpy.context.scene.mc_voxel
    props.block_size = block_size
    props.solid_fill = solid
    props.gravity_support = True
    props.max_blocks = 2_000_000


def save(name):
    path = os.path.join(OUT, name)
    bpy.ops.wm.save_as_mainfile(filepath=path)
    print(f"[examples] {path}")


def make_cube():
    reset()
    bpy.ops.mesh.primitive_cube_add(size=10.0, location=(0, 0, 5))
    obj = bpy.context.active_object
    obj.name = "GradientCube"

    def shade(co):
        t = max(0.0, min(1.0, (co.z + 5.0) / 10.0))
        # 灰阶渐变：Sprint 2 的验收项，应该落到 stone / andesite / deepslate 系列
        v = 0.15 + 0.75 * t
        return (v, v, v)

    paint(obj, shade)
    setup_props(1.0, True)
    save("01_cube_gradient.blend")


def make_sphere():
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=5.0, segments=48, ring_count=24, location=(0, 0, 6))
    obj = bpy.context.active_object
    obj.name = "Sphere"

    def shade(co):
        # 冷色到暖色，用来检查色板在彩色区间的取舍
        t = max(0.0, min(1.0, (co.z + 5.0) / 10.0))
        return (0.25 + 0.6 * t, 0.45, 0.85 - 0.55 * t)

    paint(obj, shade)
    setup_props(1.0, True)
    save("02_sphere.blend")


def make_suzanne():
    reset()
    bpy.ops.mesh.primitive_monkey_add(size=6.0, location=(0, 0, 3.5))
    obj = bpy.context.active_object
    obj.name = "Suzanne"

    def shade(co):
        # 脸盘浅米，头顶和耳朵按高度渐变到棕
        if co.y < -0.6:
            return (0.86, 0.82, 0.72)
        t = max(0.0, min(1.0, (co.z + 3.0) / 6.0))
        return (0.45 + 0.25 * t, 0.33 + 0.18 * t, 0.22 + 0.12 * t)

    paint(obj, shade)
    setup_props(0.05, True)
    save("03_suzanne.blend")


def main():
    os.makedirs(OUT, exist_ok=True)
    make_cube()
    make_sphere()
    make_suzanne()
    print(f"[examples] 三个场景就绪：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
