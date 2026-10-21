"""操作符：预览体素 / 导出投影 / 反向导入。"""

from __future__ import annotations

import os
import tempfile

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..core import build as core_build
from ..core import limits
from ..core import litematic as core_litematic
from ..core import mcver, mesh_data, schem as core_schem, structure as core_structure
from ..core.ir import VoxelModel
from ..core.palette import BlockPalette, load_fallback_palette, parse_block_filter
from ..core.pack import ResourcePack


def _match_names(text, colors):
    """把用户填的方块过滤文本展开成色板里真实存在的名字。

    返回 (命中, 缺失)。通配、补 minecraft: 前缀、中英标点分隔都由
    core.palette.parse_block_filter 负责。
    """
    wanted = parse_block_filter(text, known=list(colors.keys()))
    hit = [n for n in wanted if n in colors]
    missing = [n for n in wanted if n not in colors]
    return hit, missing


def _get_palette(props) -> BlockPalette:
    """优先用 jar 的真实方块色，失败退回兜底 96 色。

    没填 jar 时自动去常见启动器目录里找（PCL / HMCL / 官方），这样开箱
    就是 323 种真实方块色，而不是一片偏差很大的兜底色。
    """
    colors = None
    jar = bpy.path.abspath(props.jar_path) if props.jar_path else (mcver.find_client_jar() or "")
    if jar and os.path.exists(jar):
        try:
            from ..core.pack import load_cached
            from .paths import cache_file

            colors = load_cached(jar, cache_file())
            print(f"[mc_voxel] 资源包方块 {len(colors or {})} 种（{os.path.basename(jar)}）")
        except Exception as exc:  # 资源包解析失败不致命，但要说清为什么
            print(f"[mc_voxel] 读资源包失败，用兜底色板：{exc}")
    elif jar:
        print(f"[mc_voxel] jar 路径不存在，用兜底色板：{jar}")
    else:
        print("[mc_voxel] 没找到客户端 jar，用兜底 96 色（颜色会偏）")
    if not colors:
        colors = load_fallback_palette()

    # 白名单：只用这些方块。留空表示不限制。
    allow = None
    allow_text = getattr(props, "allow_blocks", "") or ""
    if allow_text.strip():
        allow, missing = _match_names(allow_text, colors)
        if not allow:
            raise RuntimeError(
                f"白名单里的方块一个都不在色板里：{allow_text.strip()[:60]}"
            )
        print(
            f"[mc_voxel] 白名单命中 {len(allow)} 种"
            + (f"；这些不在色板里：{', '.join(missing[:5])}" if missing else "")
        )

    # 需求⑦：禁用方块。被禁的从候选池移除后重新建 LUT，
    # 所以原位置会落到色差最小的可用方块，不会留空洞。
    deny = []
    deny_text = getattr(props, "disable_blocks", "") or ""
    if deny_text.strip():
        deny, missing = _match_names(deny_text, colors)
        print(
            f"[mc_voxel] 禁用 {len(deny)} 种方块"
            + (f"；这些不在色板里：{', '.join(missing[:5])}" if missing else "")
        )

    if allow is None and not deny:
        return BlockPalette(colors)

    try:
        palette = BlockPalette(colors, allow=allow, deny=deny)
    except ValueError as exc:
        raise RuntimeError(f"过滤后方块一个不剩（{exc}），检查白名单/禁用方块") from exc

    print(f"[mc_voxel] 可用方块 {len(palette.names)} 种")
    return palette


class _Progress:
    """把 build_voxel_model 的阶段回调接到 Blender 的进度条上。

    无头模式（-b）或没有窗口时静默退化，只打印日志。
    """

    def __init__(self, window_manager, total=6):
        self.wm = window_manager
        self.total = total
        self.n = 0
        if self.wm is None:
            return
        try:
            self.wm.progress_begin(0, self.total)
        except Exception:
            self.wm = None

    def __call__(self, message):
        self.n += 1
        print(f"[mc_voxel] {message}")
        if self.wm is None:
            return
        try:
            self.wm.progress_update(min(self.n, self.total))
        except Exception:
            self.wm = None

    def end(self):
        if self.wm is None:
            return
        try:
            self.wm.progress_end()
        except Exception:
            pass
        self.wm = None


_TYPE_NAMES = {
    "MESH": "网格",
    "CAMERA": "摄像机",
    "LIGHT": "灯光",
    "EMPTY": "空物体",
    "CURVE": "曲线",
    "SURFACE": "曲面",
    "FONT": "文字",
    "ARMATURE": "骨架",
    "LATTICE": "晶格",
    "SPEAKER": "扬声器",
}


def _scene(context):
    """浮窗面板的上下文里 context.scene 可能拿不到，退到全局。"""
    return getattr(context, "scene", None) or bpy.context.scene


def _active_mesh(context):
    """取要体素化的网格物体。

    为什么不能直接用 context.active_object：
    用 `wm.call_panel` 弹出的浮窗面板，context 是 WM 区域而不是 3D 视图，
    `context.active_object` 在这些位置经常是 None —— 用户明明选了物体，
    却一直被告知「先选一个网格物体」。所以这里依次退：
    上下文里的活动物体 → 视图层的活动物体 → 唯一选中的网格。
    """
    tries = []
    for getter in (
        lambda: context.active_object,
        lambda: getattr(getattr(context, "view_layer", None), "objects", None).active,
        lambda: bpy.context.view_layer.objects.active,
    ):
        try:
            obj = getter()
        except Exception:
            obj = None
        if obj is not None and obj not in tries:
            tries.append(obj)

    for obj in tries:
        if obj.type == "MESH":
            return obj

    sel = [o for o in (getattr(context, "selected_objects", None) or []) if o is not None]
    mesh_sel = [o for o in sel if o.type == "MESH"]

    # 选中了东西但不是网格：说清楚是什么，别只说「先选一个网格物体」
    if tries:
        wrong = tries[0]
        kind = _TYPE_NAMES.get(wrong.type, wrong.type)
        raise RuntimeError(
            f"当前选中的是「{wrong.name}」（{kind}），不是网格模型。"
            "在 3D 视图里左键点一下要体素化的模型再试。"
        )
    if len(mesh_sel) == 1:
        return mesh_sel[0]
    if mesh_sel:
        raise RuntimeError(f"选了 {len(mesh_sel)} 个网格模型，请只留一个再体素化")
    if sel:
        kinds = "、".join(_TYPE_NAMES.get(o.type, o.type) for o in sel[:3])
        raise RuntimeError(f"选中的都是{kinds}，没有网格模型")
    raise RuntimeError("没有选中任何物体：先在 3D 视图里左键点一下模型")


def _build_from_active(context):
    """体素化当前物体。返回 (model, palette, 限高报告)。"""
    props = _scene(context).mc_voxel
    obj = _active_mesh(context)

    verts, tris, colors = mesh_data.mesh_to_mc(obj)
    jar = bpy.path.abspath(props.jar_path) if props.jar_path else None
    dimension = getattr(props, "dimension", "overworld")
    block_size = float(props.block_size)

    # 倍率不许超过世界限高。这里在体素化之前就能算准：
    # 格数 = ceil(原始高度 / 方块尺寸)，所以直接反解出最小可用尺寸，
    # 不用先体素化再回头缩放（那样会白白吃一遍内存）。
    span_y = float(verts[:, 1].max() - verts[:, 1].min())
    dim = limits.get_dimension(dimension, jar)
    autofit = ""
    if getattr(props, "auto_fit_height", True):
        fitted = limits.fit_block_size_to_height(block_size, span_y, dim.height)
        if fitted > block_size * (1.0 + 1e-9):
            autofit = (
                f"会超限高，方块尺寸 {block_size:g} → {fitted:g}"
                f"（{limits.span_cells(span_y, block_size)} 格高 → "
                f"{limits.span_cells(span_y, fitted)} 格高）"
            )
            print(f"[mc_voxel] {autofit}")
            props.block_size = fitted  # 写回，面板上能看见实际用的倍率
            block_size = fitted

    prog = _Progress(getattr(context, "window_manager", None))
    try:
        palette = _get_palette(props)
        model = core_build.build_voxel_model(
            verts,
            tris,
            colors,
            block_size=block_size,
            palette=palette,
            solid=props.solid_fill,
            no_floating_gravity=getattr(props, "gravity_support", True),
            progress=prog,
        )
    finally:
        prog.end()

    if model.total_blocks > props.max_blocks:
        raise RuntimeError(
            f"方块数 {model.total_blocks} 超过上限 {props.max_blocks}，调大方块尺寸再试"
        )

    # 限高：超出的方块 Litematica 会静默跳过（不报错、直接缺块），所以主动挡。
    report = limits.check_height(
        model.size[1],
        dimension=dimension,
        jar_path=jar,
        block_size=block_size,
    )
    report.autofit = autofit
    print(f"[mc_voxel] 限高：{report.message}")
    if report.level == "error":
        if getattr(props, "strict_height", True):
            raise RuntimeError(report.message)
        print("[mc_voxel] 已关闭严格限高，继续导出（超出部分摆不出来）")
        report.level = "warn"
    return model, palette, report


class MCVOXEL_OT_voxelize(bpy.types.Operator):
    """体素化选中物体并打印统计（预览用，不落盘）"""

    bl_idname = "mc_voxel.voxelize"
    bl_label = "体素化"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        try:
            model, _palette, hrep = _build_from_active(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        sx, sy, sz = model.size
        lines = [
            f"尺寸 {sx}x{sy}x{sz}",
            f"方块 {model.total_blocks}",
            f"色板 {len(model.palette)} 种",
            f"高度 {sy}/{hrep.limit}",
        ]
        for line in lines:
            print(f"[mc_voxel] {line}")
        if hrep.autofit:
            lines.append(f"已自动压到限高（倍率改为 {hrep.autofit}）")
        if hrep.level == "warn":
            self.report({"WARNING"}, " / ".join(lines) + "。" + hrep.message)
        else:
            self.report({"INFO"}, " / ".join(lines))
        return {"FINISHED"}


class MCVOXEL_OT_preview(bpy.types.Operator):
    """生成点云预览（用 from_pydata 建立方体点云，立刻可见）"""

    bl_idname = "mc_voxel.preview"
    bl_label = "预览方块"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        try:
            model, _palette, _hrep = _build_from_active(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        from ..core import mesh_export

        verts, quads, colors = mesh_export.model_to_mesh(model)
        if len(verts) == 0:
            self.report({"WARNING"}, "体素为空，检查方块尺寸")
            return {"CANCELLED"}

        from ..core.coords import mc_to_blender_array

        mesh = bpy.data.meshes.new("MCVoxelPreview")
        mesh.from_pydata(mc_to_blender_array(verts).tolist(), [], quads.tolist())
        mesh.update()
        obj = bpy.data.objects.new("MCVoxelPreview", mesh)
        context.collection.objects.link(obj)
        self.report({"INFO"}, f"{len(quads)} 个面")
        return {"FINISHED"}


class MCVOXEL_OT_export(bpy.types.Operator, ExportHelper):
    """导出 Minecraft 投影"""

    bl_idname = "mc_voxel.export"
    bl_label = "导出投影"
    bl_options = {"REGISTER"}

    filename_ext = ".litematic"
    filter_glob: bpy.props.StringProperty(default="*.litematic;*.schem;*.nbt", options={"HIDDEN"})

    def execute(self, context):
        props = _scene(context).mc_voxel
        try:
            model, _palette, hrep = _build_from_active(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        data_version = props.mc_data_version or 0
        if not data_version:
            try:
                jar = bpy.path.abspath(props.jar_path) if props.jar_path else None
                data_version = mcver.resolve_data_version(
                    jar or mcver.find_client_jar(), release=props.mc_release
                )
            except mcver.DataVersionUnknown as exc:
                self.report({"ERROR"}, f"{exc}（面板里选游戏版本或手填 DataVersion）")
                return {"CANCELLED"}

        path = bpy.path.abspath(self.filepath)
        fmt = props.export_format
        try:
            if fmt == "LITEMATIC":
                core_litematic.write_litematic(
                    model,
                    path,
                    mc_data_version=data_version,
                    region_size=props.region_size,
                    name=os.path.splitext(os.path.basename(path))[0],
                    author=props.author or "mc_voxel_export",
                )
            elif fmt == "SCHEM":
                core_schem.write_schem(model, path, data_version)
            else:
                core_structure.write_structure_nbt(model, path, data_version)
        except Exception as exc:
            self.report({"ERROR"}, f"写出失败：{exc}")
            return {"CANCELLED"}

        msg = f"已写出 {path}（{model.total_blocks} 方块）"
        if hrep.autofit:
            msg += f"；{hrep.autofit}"
        if fmt == "STRUCTURE" and max(model.size) > 48:
            print(
                "[mc_voxel] 结构方块 .nbt 文件本身没限高，但游戏里的结构方块界面"
                f"最大只能填 48，当前 {max(model.size)} 会填不进去"
            )
        if hrep.level == "warn":
            self.report({"WARNING"}, f"{msg}。{hrep.message}")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class MCVOXEL_OT_import(bpy.types.Operator, ImportHelper):
    """反向导入投影为网格"""

    bl_idname = "mc_voxel.import_litematic"
    bl_label = "导入投影"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".litematic"
    filter_glob: bpy.props.StringProperty(default="*.litematic;*.schem", options={"HIDDEN"})

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        try:
            if path.lower().endswith(".schem"):
                model = core_schem.read_schem(path)
            else:
                model = core_litematic.read_litematic(path)
        except Exception as exc:
            self.report({"ERROR"}, f"读取失败：{exc}")
            return {"CANCELLED"}

        from ..core import mesh_export
        from ..core.coords import mc_to_blender_array

        verts, quads, _colors = mesh_export.model_to_mesh(model)
        mesh = bpy.data.meshes.new("MCVoxelImport")
        mesh.from_pydata(mc_to_blender_array(verts).tolist(), [], quads.tolist())
        mesh.update()
        obj = bpy.data.objects.new("MCVoxelImport", mesh)
        context.collection.objects.link(obj)

        # 方案 §12.2：平滑走 Blender 修改器，不自研 marching cubes。
        # 两个开关分开：只改法线 vs 真磨圆。
        props = _scene(context).mc_voxel
        smooth = getattr(props, "smooth_shading", True)
        remesh = getattr(props, "remesh_smooth", False)
        if remesh:
            mod = obj.modifiers.new("Smooth", "REMESH")
            mod.mode = "VOXEL"
            mod.voxel_size = props.remesh_voxel_size
            mod.use_smooth_shade = smooth
        elif smooth:
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
            mesh.update()
        note = "磨圆" if remesh else ("平滑着色" if smooth else "保持硬边")
        self.report({"INFO"}, f"导入 {model.size}，{len(quads)} 个面（{note}）")
        return {"FINISHED"}


_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
_AXIS_NAME = {"X": "长", "Y": "高", "Z": "宽"}


class MCVOXEL_OT_fit_size(bpy.types.Operator):
    """按某一轴的方块格数反算方块尺寸（倍率）"""

    bl_idname = "mc_voxel.fit_size"
    bl_label = "按长度定倍率"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        try:
            obj = _active_mesh(context)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        target = int(props.size_target)
        if target <= 0:
            self.report({"ERROR"}, "先在「目标格数」里填一个大于 0 的数")
            return {"CANCELLED"}

        axis = getattr(props, "size_axis", "Y")
        try:
            verts, _tris, _colors = mesh_data.mesh_to_mc(obj)
            span = verts.max(axis=0) - verts.min(axis=0)
            units = float(span[_AXIS_INDEX[axis]])
            block_size = limits.block_size_for_length(units, target)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        # 反算出来的倍率也可能超限高（比如只限制"长 64 格"，但模型很瘦高）
        jar = bpy.path.abspath(props.jar_path) if props.jar_path else None
        dim = limits.get_dimension(getattr(props, "dimension", "overworld"), jar)
        note = ""
        if getattr(props, "auto_fit_height", True):
            fitted = limits.fit_block_size_to_height(
                block_size, float(span[1]), dim.height
            )
            if fitted > block_size * (1.0 + 1e-9):
                note = f"；高会超限高 {dim.height}，压到 {fitted:.4g}"
                block_size = fitted

        props.block_size = block_size
        cells = limits.span_cells(float(span[1]), block_size)
        self.report(
            {"INFO"},
            f"方块尺寸 = {block_size:.4g}"
            f"（{_AXIS_NAME[axis]} {target} 格 / {units:.3g} m，高 {cells} 格{note}）",
        )
        return {"FINISHED"}


class MCVOXEL_OT_render_preview(bpy.types.Operator):
    """渲染等距预览图（看着就像游戏里摆出来的样子）"""

    bl_idname = "mc_voxel.render_preview"
    bl_label = "渲染预览图"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        try:
            model, palette, hrep = _build_from_active(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        path = (props.preview_path or "").strip()
        if path:
            path = bpy.path.abspath(path)
            if not path.lower().endswith(".png"):
                path += ".png"
        elif bpy.data.filepath:
            path = os.path.splitext(bpy.data.filepath)[0] + ".preview.png"
        else:
            path = os.path.join(tempfile.gettempdir(), "mc_voxel_preview.png")

        from . import preview_render

        try:
            info = preview_render.render_iso(
                model, palette, path, size=int(getattr(props, "preview_size", 1024))
            )
        except Exception as exc:
            self.report({"ERROR"}, f"渲染失败：{exc}")
            return {"CANCELLED"}

        msg = (
            f"预览图 {info['width']}x{info['height']}，{info['faces']} 个面 → {info['path']}"
        )
        if hrep.level == "warn":
            self.report({"WARNING"}, f"{msg}。{hrep.message}")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


# 预设：名字 -> (方块尺寸, 实心, 重力检查)
PRESETS = {
    "BUILDING": ("建筑", 1.0, True, True),
    "PIXEL": ("像素画", 0.2, False, True),
    "TERRAIN": ("地形", 4.0, True, True),
}


class MCVOXEL_OT_preset(bpy.types.Operator):
    """套用一组预设参数"""

    bl_idname = "mc_voxel.preset"
    bl_label = "预设"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(
        items=[(k, v[0], f"方块尺寸 {v[1]}") for k, v in PRESETS.items()]
    )

    def execute(self, context):
        props = _scene(context).mc_voxel
        name, size, solid, gravity = PRESETS[self.preset]
        props.block_size = size
        props.solid_fill = solid
        props.gravity_support = gravity
        self.report({"INFO"}, f"{name}预设：方块尺寸 {size}，{'实心' if solid else '只留外壳'}")
        return {"FINISHED"}


class MCVOXEL_OT_detect_jar(bpy.types.Operator):
    """在常见启动器目录（PCL / HMCL / 官方）里找客户端 jar 并填入"""

    bl_idname = "mc_voxel.detect_jar"
    bl_label = "自动找客户端 jar"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        jar = mcver.find_client_jar()
        if not jar:
            self.report(
                {"ERROR"},
                "没在常见启动器目录里找到 versions/<版本>/<版本>.jar，请手动选",
            )
            return {"CANCELLED"}
        props.jar_path = jar
        self.report({"INFO"}, f"已填：{os.path.basename(jar)}")
        return {"FINISHED"}


class MCVOXEL_OT_open_panel(bpy.types.Operator):
    """把 MC 体素面板弹成浮窗（侧栏标签找不到时的快捷入口）"""

    bl_idname = "mc_voxel.open_panel"
    bl_label = "打开 MC 体素面板"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        bpy.ops.wm.call_panel(name="MCVOXEL_PT_panel", keep_open=True)
        return {"FINISHED"}

    def execute(self, context):
        bpy.ops.wm.call_panel(name="MCVOXEL_PT_panel", keep_open=True)
        return {"FINISHED"}


_CLASSES = (
    MCVOXEL_OT_voxelize,
    MCVOXEL_OT_preview,
    MCVOXEL_OT_export,
    MCVOXEL_OT_import,
    MCVOXEL_OT_preset,
    MCVOXEL_OT_fit_size,
    MCVOXEL_OT_render_preview,
    MCVOXEL_OT_detect_jar,
    MCVOXEL_OT_open_panel,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
