"""用真实 jar 色板把小房子体素化，出一张预览图（验证示例场景 + 色板）。

    blender.exe -b examples/04_house.blend --python tools/house_preview.py

需要客户端 jar：自动探测，或用 `MC_JAR` 环境变量指定。
"""

import os
import sys

import bpy

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import _paths  # noqa: E402

from bl_ext.user_default.mc_voxel_export.core import build as core_build  # noqa: E402
from bl_ext.user_default.mc_voxel_export.core import (  # noqa: E402
    mesh_data,
    pack as core_pack,
    palette as core_palette,
)
from bl_ext.user_default.mc_voxel_export.ui import preview_render  # noqa: E402

JAR = _paths.client_jar()
CACHE = _paths.palette_cache()
OUT = os.path.join(_paths.DIST, "house_preview.png")

if not JAR:
    raise SystemExit("[house] 找不到客户端 jar：设 MC_JAR 环境变量，或放到常见启动器目录")
print("[house] jar:", JAR)

obj = bpy.data.objects.get("House")
if obj is None:
    raise SystemExit("[house] 场景里没有 House，先打开 examples/04_house.blend")

verts, tris, colors = mesh_data.mesh_to_mc(obj)
raw = core_pack.load_cached(JAR, CACHE)
print("[house] 色板", len(raw) if raw else 0, "种")
pal = core_palette.BlockPalette(raw)
model = core_build.build_voxel_model(
    verts, tris, colors, block_size=0.5, palette=pal, solid=True, no_floating_gravity=True
)
print("[house] 体素", model.size, model.total_blocks, "方块")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
info = preview_render.render_iso(model, pal, OUT, size=900)
print("[house] PNG", info)
