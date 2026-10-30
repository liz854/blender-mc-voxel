"""拿网上的模型试体素化效果：导入 glb → 贴图烘到顶点色 → 体素化 → 出对比图。

用法：
    blender.exe -b --python tools/try_model.py -- <模型路径> [名字]
"""

import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bl_ext.user_default.mc_voxel_export.core import build as core_build  # noqa: E402
from bl_ext.user_default.mc_voxel_export.core import litematic as core_litematic  # noqa: E402
from bl_ext.user_default.mc_voxel_export.core import mesh_data  # noqa: E402
from bl_ext.user_default.mc_voxel_export.core.pack import load_cached_stats  # noqa: E402
from bl_ext.user_default.mc_voxel_export.core.palette import BlockPalette  # noqa: E402
from bl_ext.user_default.mc_voxel_export.ui.preview_render import render_iso  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "cache_try.json")


def lin2srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1.0 / 2.4) - 0.055)


def find_image(mat):
    """找漫反射（base color）贴图。

    不能拿"第一个 TEX_IMAGE"：glTF 材质里还有法线、金属粗糙度、AO、发光贴图，
    抓到发光的（DamagedHelmet 那种只有面罩亮）会算出一片近乎全黑。
    正确做法是顺着材质连线找连到 Base Color 的那张贴图。
    """
    if not mat or not mat.use_nodes:
        return None
    cands = [n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.image]
    if not cands:
        return None

    # 1) 顺着 Principled BSDF 的 Base Color 往回找
    for node in mat.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        sock = node.inputs.get("Base Color")
        if sock is None:
            continue
        seen = set()
        stack = [l.from_node for l in mat.node_tree.links if l.to_socket == sock]
        while stack:
            n = stack.pop()
            if n is None or n.name in seen:
                continue
            seen.add(n.name)
            if n.type == "TEX_IMAGE" and n.image:
                print(f"[try] base color 贴图：{n.image.name}")
                return n.image
            for l in mat.node_tree.links:
                if l.to_node == n:
                    stack.append(l.from_node)

    # 2) 退回：挑 sRGB 且不是法线图的那张
    print("[try] 没找到 Base Color 连线，退回到启发式挑图：", [i.name for i in cands])
    for img in cands:
        cs = img.colorspace_settings.name
        if cs.startswith("sRGB"):
            cols = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
            img.pixels.foreach_get(cols)
            px = cols.reshape(img.size[1], img.size[0], 4)[::16, ::16, :3]
            is_normal = abs(px[..., 0].mean() - 0.5) < 0.08 and abs(px[..., 2].mean() - 1.0) < 0.08
            bright = float(px.mean())
            print(f"[try]   {img.name} 亮度 {bright:.3f} 法线图={is_normal}")
            if not is_normal and bright > 0.01:
                return img
    return cands[0]


def bake_uv_to_vcol(obj):
    """把材质贴图按 UV 采样到顶点色。没贴图返回 False。"""
    me = obj.data
    if not me.uv_layers.active:
        print("[try] 没有 UV，跳过烘焙")
        return False
    img = None
    for mat in me.materials:
        img = find_image(mat)
        if img:
            break
    if img is None:
        print("[try] 材质里没有贴图，跳过烘焙")
        return False

    w, h = img.size
    if w == 0 or h == 0:
        return False
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    px = buf.reshape(h, w, 4)

    nloop = len(me.loops)
    uv = np.empty(nloop * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(nloop, 2)
    xi = np.clip((uv[:, 0] % 1.0) * (w - 1), 0, w - 1).astype(np.int64)
    yi = np.clip((uv[:, 1] % 1.0) * (h - 1), 0, h - 1).astype(np.int64)
    loop_rgb = lin2srgb(px[yi, xi, :3])  # 贴图里是线性值，转成 sRGB 再喂给匹配

    vidx = np.empty(nloop, dtype=np.int64)
    me.loops.foreach_get("vertex_index", vidx)
    nv = len(me.vertices)
    acc = np.zeros((nv, 3), dtype=np.float64)
    cnt = np.zeros(nv, dtype=np.float64)
    np.add.at(acc, vidx, loop_rgb)
    np.add.at(cnt, vidx, 1.0)
    vcol = acc / np.maximum(cnt, 1.0)[:, None]

    # FLOAT_COLOR 的 .color 不做色彩空间转换，存什么读什么，
    # 正好对上 mesh_data 里「*255 当 sRGB 用」的假设。
    ca = me.color_attributes.get("VoxelColor")
    if ca is None:
        ca = me.color_attributes.new("VoxelColor", "FLOAT_COLOR", "POINT")
    rgba = np.ones((nv, 4), dtype=np.float32)
    rgba[:, :3] = vcol
    ca.data.foreach_set("color", rgba.ravel())
    me.color_attributes.active_color = ca
    print(
        f"[try] 贴图 {img.name} {w}×{h} -> 顶点色 "
        f"均值 {vcol.mean(axis=0).round(3).tolist()} "
        f"范围 {vcol.min():.3f}..{vcol.max():.3f}"
    )
    return True


def render_workbench(obj, path, size=900):
    """用 Workbench + 顶点色渲染一张原模型参照图。"""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "VERTEX"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = False
    if scene.world is None:
        scene.world = bpy.data.worlds.new("TryWorld")
    scene.world.color = (0.55, 0.68, 0.86)
    scene.render.filepath = path

    cam_data = bpy.data.cameras.new("TryCam")
    cam_data.type = "ORTHO"
    span = max(obj.dimensions)
    cam_data.ortho_scale = span * 1.35
    cam = bpy.data.objects.new("TryCam", cam_data)
    scene.collection.objects.link(cam)
    d = span * 3
    cam.location = (d, -d, d)
    cam.rotation_euler = (0.9553, 0, 0.7854)
    scene.camera = cam
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(cam, do_unlink=True)
    print(f"[try] 参照图 -> {path}")


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("需要模型路径")
    model_path = argv[0]
    name = argv[1] if len(argv) > 1 else os.path.splitext(os.path.basename(model_path))[0]
    cells = int(argv[2]) if len(argv) > 2 else 64

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=model_path)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    print(f"[try] 导入 {len(meshes)} 个网格：{name}")
    for o in meshes:
        bpy.context.view_layer.objects.active = o
        o.select_set(True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.active_object
    print(f"[try] 尺寸 {tuple(round(v, 3) for v in obj.dimensions)}，顶点 {len(obj.data.vertices)}")

    baked = bake_uv_to_vcol(obj)

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "try")
    os.makedirs(out_dir, exist_ok=True)
    if baked:
        render_workbench(obj, os.path.join(out_dir, f"{name}_source.png"))

    verts, tris, colors = mesh_data.mesh_to_mc(obj)
    span = float(np.max(verts.max(axis=0) - verts.min(axis=0)))
    block = span / cells

    jar = r"D:\PCL\.minecraft\versions\26.3-Fabric 0.20.1\26.3-Fabric 0.20.1.jar"
    try:
        palette_colors, palette_stds = load_cached_stats(jar, CACHE)
        palette = BlockPalette(palette_colors, stds=palette_stds)
        print(f"[try] 色板 {len(palette_colors)} 种")
    except Exception as exc:
        from bl_ext.user_default.mc_voxel_export.core.palette import load_fallback_palette

        palette = BlockPalette(load_fallback_palette())
        print(f"[try] 色板退回内置 96 色：{exc}")

    model = core_build.build_voxel_model(
        verts, tris, colors, block_size=block, palette=palette,
        solid=False, max_kinds=24,
    )
    print(f"[try] 体素化：网格 {model.size}，方块 {model.total_blocks:,}，"
          f"用到的方块 {len([s for s in model.palette if s.name != 'minecraft:air'])} 种")

    img = os.path.join(out_dir, f"{name}_voxel.png")
    render_iso(model, palette=palette, path=img, size=900)
    print(f"[try] 体素图 -> {img}")

    stf = os.path.join(out_dir, f"{name}.litematic")
    core_litematic.write_litematic(model, stf, mc_data_version=5023, name=name)
    print(f"[try] 投影 -> {stf}（{os.path.getsize(stf):,} 字节）")


if __name__ == "__main__":
    main()
