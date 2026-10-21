"""体素模型 -> 等距预览图（Blender Workbench 渲染）。

放在 ui/ 而不是 core/：`tests/test_constraints.py` 钉死了「core 除 mesh_data.py
外不许 import bpy」，这条边界值得守住。

为什么用 Blender 渲染而不是自己光栅化：
  - 方块面的明暗、遮蔽、抗锯齿都是现成的，比手写画家算法好看得多；
  - 颜色直接取调色板里方块的平均色，所以跟游戏里看到的配色一致；
  - 已经能无头跑（`blender -b --python ...`）。

渲染只画外露面（`mesh_export.model_to_mesh`），所以面数跟表面积走，不跟体积走。
"""

from __future__ import annotations

import os

import numpy as np


def _attach_colors(model, palette):
    """把调色板的方块平均色挂到模型上，供 mesh_export 取色。"""
    if not palette:
        return
    model._colors = {
        st.name: (palette.color_of(st.name) or (200, 200, 200)) for st in model.palette
    }


def _save_state(scene):
    """记下要改的渲染设置，渲染完原样还回去。"""
    shading = scene.display.shading
    return {
        "engine": scene.render.engine,
        "res_x": scene.render.resolution_x,
        "res_y": scene.render.resolution_y,
        "filepath": scene.render.filepath,
        "transparent": scene.render.film_transparent,
        "color_mode": scene.render.image_settings.color_mode,
        "camera": scene.camera,
        "world": scene.world,
        "aa": scene.display.render_aa,
        "shading": {
            "light": shading.light,
            "color_type": shading.color_type,
            "show_shadows": shading.show_shadows,
            "shadow_intensity": shading.shadow_intensity,
            "show_cavity": shading.show_cavity,
            "cavity_type": shading.cavity_type,
            "studiolight_intensity": shading.studiolight_intensity,
        },
    }


def _restore_state(scene, state):
    scene.render.engine = state["engine"]
    scene.render.resolution_x = state["res_x"]
    scene.render.resolution_y = state["res_y"]
    scene.render.filepath = state["filepath"]
    scene.render.film_transparent = state["transparent"]
    scene.render.image_settings.color_mode = state["color_mode"]
    scene.camera = state["camera"]
    scene.world = state["world"]
    scene.display.render_aa = state["aa"]
    shading = scene.display.shading
    for key, value in state["shading"].items():
        try:
            setattr(shading, key, value)
        except (AttributeError, TypeError):
            pass


def render_iso(
    model,
    palette=None,
    path=None,
    size: int = 1024,
    direction=(1.0, 1.0, 1.0),
    margin: float = 1.12,
    transparent: bool = False,
    background=(0.55, 0.68, 0.86),
    shadows: bool = True,
    cavity: bool = True,
    aa: str = "8",
):
    """把 VoxelModel 渲染成一张等距预览图，返回渲染信息。

    model       : VoxelModel
    palette     : BlockPalette（取方块真实平均色，不给就用模型自带/灰色）
    path        : 输出 png 路径
    size        : 长边像素
    direction   : MC 空间里的观察方向，(1,1,1) 是标准等距
    transparent : True 出透明背景的 RGBA
    """
    import bpy
    from mathutils import Vector

    from ..core.coords import mc_to_blender, mc_to_blender_array
    from ..core.mesh_export import model_to_mesh

    _attach_colors(model, palette)
    verts, quads, vcols = model_to_mesh(model)
    if len(verts) == 0:
        raise ValueError("体素为空，没有可预览的方块")

    created = []
    made_world = None
    scene = bpy.context.scene
    state = _save_state(scene)
    # 场景里原有的东西先藏起来：预览图只该出这一个模型，不然旁边的物件
    # 会一起进画面（bugtest 那种一块一个预览的场景特别明显）
    hidden_before = []
    try:
        for ob in scene.objects:
            if not ob.hide_render:
                ob.hide_render = True
                hidden_before.append(ob)

        mesh = bpy.data.meshes.new("MCVoxelPreviewMesh")
        mesh.from_pydata(mc_to_blender_array(verts).tolist(), [], quads.tolist())
        mesh.update()

        # 顶点色（CORNER 域，Workbench 的 VERTEX 着色直接吃）
        attr = mesh.color_attributes.new(name="Col", type="BYTE_COLOR", domain="CORNER")
        mesh.color_attributes.active_color = attr
        vidx = np.zeros(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", vidx)
        rgba = np.concatenate(
            [np.asarray(vcols, dtype=np.float64)[vidx] / 255.0, np.ones((len(vidx), 1))],
            axis=1,
        )
        attr.data.foreach_set("color", rgba.reshape(-1))
        mesh.update()

        obj = bpy.data.objects.new("MCVoxelPreviewMesh", mesh)
        scene.collection.objects.link(obj)
        created.append(obj)

        # --- 相机：正交 + 等距 ---
        co = np.asarray(verts, dtype=np.float64)
        lo, hi = co.min(axis=0), co.max(axis=0)
        center_mc = (lo + hi) / 2.0
        d = np.asarray(direction, dtype=np.float64)
        d = d / np.linalg.norm(d)
        dist = float(np.linalg.norm(hi - lo)) * 2.0 + 10.0

        cam_data = bpy.data.cameras.new("MCVoxelPreviewCam")
        cam_data.type = "ORTHO"
        cam = bpy.data.objects.new("MCVoxelPreviewCam", cam_data)
        scene.collection.objects.link(cam)
        created.append(cam)

        target = Vector(mc_to_blender(center_mc))
        cam.location = Vector(mc_to_blender(center_mc + d * dist))
        quat = (target - cam.location).to_track_quat("-Z", "Y")
        cam.rotation_euler = quat.to_euler()
        scene.camera = cam

        # --- 取景：按实际顶点投影定画幅，比包围盒的八个角紧得多 ---
        # 相机始终对着包围盒中心（画面中心就是它），所以这里算相对中心的半宽半高，
        # 两边取对称值——既能保证不裁掉，又不会因为外形偏一边而留大片空白。
        rot = quat.to_matrix()
        right = np.array(rot @ Vector((1.0, 0.0, 0.0)))
        up = np.array(rot @ Vector((0.0, 1.0, 0.0)))
        bl = mc_to_blender_array(co)
        if len(bl) > 200_000:  # 超大模型抽稀，取景不需要每个点
            bl = bl[:: max(1, len(bl) // 200_000)]
        center_bl = mc_to_blender_array(np.asarray([center_mc]))[0]
        u = bl @ right - float(center_bl @ right)
        v = bl @ up - float(center_bl @ up)
        w = max(float(np.abs(u).max()) * 2.0, 1e-6)
        h = max(float(np.abs(v).max()) * 2.0, 1e-6)

        if w >= h:
            res_x, res_y = int(size), max(1, int(round(size * h / w)))
            ortho = w * margin
        else:
            res_y, res_x = int(size), max(1, int(round(size * w / h)))
            ortho = h * margin
        cam_data.ortho_scale = float(ortho)

        # --- 渲染设置 ---
        scene.render.engine = "BLENDER_WORKBENCH"
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.color_type = "VERTEX"
        sh.show_shadows = bool(shadows)
        sh.shadow_intensity = 0.22
        sh.show_cavity = bool(cavity)
        sh.cavity_type = "BOTH"
        sh.studiolight_intensity = 1.4
        try:
            scene.display.render_aa = aa
        except (AttributeError, TypeError):
            pass

        scene.render.resolution_x = res_x
        scene.render.resolution_y = res_y
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = bool(transparent)
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"

        if scene.world is None:
            made_world = bpy.data.worlds.new("MCVoxelPreviewWorld")
            scene.world = made_world
        scene.world.color = tuple(float(c) for c in background)

        out = path or os.path.join(os.path.expanduser("~"), "mc_voxel_preview.png")
        out = bpy.path.abspath(out)
        parent = os.path.dirname(out)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
    finally:
        for ob in hidden_before:
            try:
                ob.hide_render = False
            except ReferenceError:
                pass
        for ob in created:
            data = ob.data
            bpy.data.objects.remove(ob, do_unlink=True)
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
        _restore_state(scene, state)
        if made_world is not None:
            bpy.data.worlds.remove(made_world)

    return {
        "path": out,
        "width": res_x,
        "height": res_y,
        "faces": int(len(quads)),
        "voxels": int((model.blocks != 0).sum()),
    }
