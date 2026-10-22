"""Blender MC Voxel — 只负责 register / unregister。

注意：顶层不 import bpy，这样 core/ 能被 pytest 直接跑，也方便打包工具静态扫描。
"""

from __future__ import annotations


def register():
    from .ui import operators, panel, picker, props

    for mod in (props, picker, operators, panel):
        mod.register()


def unregister():
    from .ui import operators, panel, picker, props

    for mod in (panel, operators, picker, props):
        mod.unregister()


if __name__ == "__main__":
    register()
