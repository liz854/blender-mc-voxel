"""bpy mesh -> (verts, tris, colors)。这是 core/ 里唯一允许 import bpy 的文件。"""

from __future__ import annotations

from .coords import blender_to_mc_array


def evaluated_mesh(obj):
    """必须走 depsgraph 拿 evaluated object（否则修改器不生效），并强制三角化。"""
    import bmesh
    import bpy

    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.calc_loop_triangles()

    verts_bl = [tuple(v.co) for v in mesh.vertices]
    tris = [tuple(p.vertices) for p in mesh.polygons]

    colors = _vertex_colors(mesh, len(verts_bl))
    colors, filled = _fill_missing_colors(colors, tris)
    if filled:
        print(
            f"[mc_voxel] {filled} 个顶点没有颜色（布尔挖洞/打孔常见），已按同面顶点补上"
        )
    eval_obj.to_mesh_clear()
    return verts_bl, tris, colors


def _fill_missing_colors(colors, tris):
    """纯黑 (0,0,0) 的顶点按所在面的其他顶点补色，返回 (colors, 补了几个)。

    布尔运算（挖门窗、打孔、切斜面）产生的新顶点，颜色属性是**纯黑 0**。
    不补的话墙面会出现一片黑斑——而且因为颜色按顶点插值，黑会顺着一圈三角形
    晕染开，最后整面墙变成深浅两色斜着拼起来，看着完全不像涂的那个颜色。

    判据用"恰好接近 0"：Minecraft 最黑的方块（black_concrete）也有 8 左右，
    用户手动涂纯黑 (0,0,0) 的可能性极低，而布尔留下的缺失是精确 0。
    """
    import numpy as np

    colors = np.asarray(colors, dtype=np.float64)
    if colors.size == 0:
        return colors, 0

    missing = np.all(np.abs(colors) <= 1.0, axis=1)
    if not missing.any():
        return colors, 0
    if missing.all():  # 整个模型都没涂色，别瞎补，保持"没有顶点色就是白"的语义
        return colors, 0

    colors = colors.copy()
    total = int(missing.sum())
    tri = [[int(i) for i in face] for face in tris]

    # 两轮：第一轮处理有正常邻居的黑顶点，第二轮让夹在两片黑里的顶点也能吸收到色
    for _ in range(2):
        changed = 0
        for face in tri:
            if len(face) < 3:
                continue
            good = [i for i in face if not missing[i]]
            bad = [i for i in face if missing[i]]
            if not good or not bad:
                continue
            mean = colors[good].mean(axis=0)
            for i in bad:
                colors[i] = mean
                missing[i] = False
                changed += 1
        if not changed:
            break

    if missing.any():  # 整块区域都没色（整面都是新几何），用全局平均兜一下
        rest = colors[~missing]
        colors[missing] = rest.mean(axis=0) if len(rest) else 255.0
    return colors, total


def _vertex_colors(mesh, vert_count):
    """取顶点色，转成 (N,3) 的 0-255。没有就全白。"""
    import numpy as np

    default = np.full((vert_count, 3), 255.0)
    attr = getattr(mesh.color_attributes, "active_color", None)
    if attr is None:
        return default

    out = np.full((vert_count, 3), 255.0)
    try:
        n = len(attr.data)
        if attr.domain == "POINT" and n == vert_count:
            for i in range(vert_count):
                out[i] = attr.data[i].color[:3]
        else:  # CORNER
            for poly in mesh.polygons:
                for li in poly.loop_indices:
                    vi = mesh.loops[li].vertex_index
                    out[vi] = attr.data[li].color[:3]
    except Exception:
        return default
    return np.clip(out * 255.0, 0, 255)


def scene_unit_scale() -> float:
    """1 Blender unit 等于多少米。

    Edit → Scene Properties → Units → Length 里的 scale_length。
    默认 1.0（1 BU = 1 m）。设成 0.01 就是"1 BU = 1 厘米"，
    同一个模型体素数要乘 100，不换算会导出一个缩水 100 倍的投影。
    """
    import bpy

    try:
        scale = float(bpy.context.scene.unit_settings.scale_length or 1.0)
    except Exception:
        return 1.0
    return scale if scale > 0 else 1.0


def mesh_to_mc(obj):
    """返回 Minecraft 坐标下的 (verts (N,3) ndarray, tris, colors)。

    两个坑，都在这里堵住：

    1. ``to_mesh()`` 给的是 **object space**，物体自身的位移/旋转/缩放
       一个都没进去。不乘 ``matrix_world`` 的话，把 Suzanne 放大两倍再体素化，
       出来的方块数跟没放大一样。
    2. 场景单位不一定是米。方案约定 1 体素 = 1 方块 = 1 米，
       所以顶点要乘 ``unit_settings.scale_length``。
    """
    import numpy as np

    verts_bl, tris, colors = evaluated_mesh(obj)
    if not verts_bl:
        raise ValueError("选中物体没有几何数据")

    verts = np.asarray(verts_bl, dtype=np.float64)
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    if matrix.shape == (4, 4):
        verts = verts @ matrix[:3, :3].T + matrix[:3, 3]

    scale = scene_unit_scale()
    if scale != 1.0:
        verts = verts * scale
        print(f"[mc_voxel] 场景单位 scale_length={scale}，已换算：1 BU = {scale} m")

    scale_xyz = obj.matrix_world.to_scale() if hasattr(obj.matrix_world, "to_scale") else None
    if scale_xyz is not None and any(abs(s - 1.0) > 1e-6 for s in scale_xyz):
        print(
            "[mc_voxel] 物体自身缩放 "
            f"({scale_xyz.x:.3f}, {scale_xyz.y:.3f}, {scale_xyz.z:.3f}) 已计入尺寸"
        )

    # 轴序最后转：上面全在 Blender 坐标系（Z-up）里算，这里才翻成 MC 的 Y-up
    return blender_to_mc_array(verts), np.asarray(tris, dtype=np.int64), colors
