"""操作符：体素化 / 预览图 / 导出投影。"""

from __future__ import annotations

import os
import tempfile

import bpy
from bpy_extras.io_utils import ExportHelper

from ..core import build as core_build
from ..core import limits
from ..core import litematic as core_litematic
from ..core import mcver, mesh_data
from ..core import tiles as core_tiles
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
    stds = None
    jar = bpy.path.abspath(props.jar_path) if props.jar_path else (mcver.find_client_jar() or "")
    if jar and os.path.exists(jar):
        try:
            from ..core.pack import load_cached_stats
            from .paths import cache_file

            colors, stds = load_cached_stats(jar, cache_file())
            print(f"[mc_voxel] 资源包方块 {len(colors or {})} 种（{os.path.basename(jar)}）")
        except Exception as exc:  # 资源包解析失败不致命，但要说清为什么
            print(f"[mc_voxel] 读资源包失败，用兜底色板：{exc}")
    elif jar:
        print(f"[mc_voxel] jar 路径不存在，用兜底色板：{jar}")
    else:
        print("[mc_voxel] 没找到客户端 jar，用兜底 96 色（颜色会偏）")
    if not colors:
        colors = load_fallback_palette()
        stds = None

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
        return BlockPalette(colors, stds=stds)

    try:
        palette = BlockPalette(colors, allow=allow, deny=deny, stds=stds)
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


def _prepare(context):
    """读当前物体的网格数据、算好实际方块尺寸、建好色板。

    体素化前的公共准备。抽出来是因为分块导出要对同一份数据切好几刀，
    没必要每段都重读一遍网格、重算一遍限高、重建一次色板。
    """
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

    return {
        "props": props,
        "verts": verts,
        "tris": tris,
        "colors": colors,
        "jar": jar,
        "dimension": dimension,
        "block_size": block_size,
        "palette": _get_palette(props),
        "autofit": autofit,
        "noise": int(getattr(props, "noise", 0) or 0),
        "max_kinds": int(getattr(props, "max_kinds", 0) or 0),
    }


def _height_check(st, size_y):
    """世界限高检查。超出的方块 Litematica 会静默跳过（不报错、直接缺块）。"""
    report = limits.check_height(
        size_y,
        dimension=st["dimension"],
        jar_path=st["jar"],
        block_size=st["block_size"],
    )
    report.autofit = st["autofit"]
    print(f"[mc_voxel] 限高：{report.message}")
    if report.level == "error":
        if getattr(st["props"], "strict_height", True):
            raise RuntimeError(report.message)
        print("[mc_voxel] 已关闭严格限高，继续导出（超出部分摆不出来）")
        report.level = "warn"
    return report


def _build_from_st(st, context):
    """用已经准备好的数据体素化一次（单块）。返回 (model, 限高报告)。"""
    props = st["props"]
    prog = _Progress(getattr(context, "window_manager", None))
    try:
        model = core_build.build_voxel_model(
            st["verts"],
            st["tris"],
            st["colors"],
            block_size=st["block_size"],
            palette=st["palette"],
            solid=props.solid_fill,
            no_floating_gravity=getattr(props, "gravity_support", True),
            noise=st["noise"],
            max_kinds=st["max_kinds"],
            progress=prog,
        )
    finally:
        prog.end()

    if model.total_blocks > props.max_blocks:
        raise RuntimeError(
            f"方块数 {model.total_blocks} 超过上限 {props.max_blocks}，"
            "调大方块尺寸，或打开「超上限自动分块」"
        )
    return model, _height_check(st, model.size[1])


def _build_from_active(context):
    """体素化当前物体。返回 (model, palette, 限高报告)。"""
    st = _prepare(context)
    model, report = _build_from_st(st, context)
    return model, st["palette"], report


def _build_segments(st, origin, size, axis, parts, context):
    """按轴把整体网格切成 parts 段，逐段体素化。

    分块的意义是**峰值内存**：体素网格是稠密的，400³ 要 7 GB，切开只按
    单段算。所有段共用同一个 origin 和 block_size，格子边界严格对齐，
    拼回去不会错位。代价是每段独立做内部填充，切面上多一层壳——投影本来
    就分块摆放，拼起来看不出来。
    """
    props = st["props"]
    verts, tris, colors = st["verts"], st["tris"], st["colors"]
    bs = st["block_size"]

    # 高度按**整体**判：每段单独都矮，但拼起来照样可能超限高
    _height_check(st, size[1])

    segments = core_tiles.plan_segments(size, origin, bs, axis, parts)
    print(
        f"[mc_voxel] 网格 {'×'.join(map(str, size))} 超过单段上限，"
        f"按 {'XYZ'[axis]} 轴切成 {parts} 段"
    )

    models = []
    prog = _Progress(getattr(context, "window_manager", None), total=len(segments) * 4)
    try:
        for i, (seg_origin, seg_size, start, stop) in enumerate(segments):
            sub = core_tiles.select_triangles(verts, tris, origin, bs, axis, start, stop)
            model = core_build.build_voxel_model(
                verts,
                sub,
                colors,
                block_size=bs,
                palette=st["palette"],
                solid=props.solid_fill,
                origin=seg_origin,
                size=seg_size,
                no_floating_gravity=getattr(props, "gravity_support", True),
                # 内部切面补虚拟封盖，否则实心模型的中间段会被判成空心。
                # 最外两段贴的是模型真实的顶/底面，不用封。
                seal_axis=axis,
                seal_lo=i > 0,
                seal_hi=i < len(segments) - 1,
                noise=st["noise"],
                max_kinds=st["max_kinds"],
                progress=prog,
            )
            if model.total_blocks > props.max_blocks:
                raise RuntimeError(
                    f"第 {len(models) + 1} 段就有 {model.total_blocks} 个方块，"
                    f"超过上限 {props.max_blocks}，调大方块尺寸再试"
                )
            models.append(model)
    finally:
        prog.end()
    return models


def _write_model(path, model, props, data_version):
    """写出 .litematic（单段 / 分块 / 批量共用）。"""
    core_litematic.write_litematic(
        model,
        path,
        mc_data_version=data_version,
        region_size=props.region_size,
        name=os.path.splitext(os.path.basename(path))[0],
        author=props.author or "mc_voxel_export",
    )


def _notify(title, lines, icon="FILE_TICK"):
    """导出成功后的可见反馈。

    self.report 只在状态栏闪一下，浮窗面板里基本看不到——用户根本不知道
    到底写出来没有。这里弹一个带「打开所在文件夹」的提示框；无头模式
    （-b）没有窗口，静默跳过。
    """
    wm = getattr(bpy.context, "window_manager", None)
    # 后台模式（blender -b）下 window_manager 还在、context.window 也不是
    # None，但调 popup_menu 会让 Blender 直接段错误（C 层崩，try 拦不住）。
    # 只能用 bpy.app.background 判断。
    if bpy.app.background or wm is None or getattr(bpy.context, "window", None) is None:
        return

    def draw(menu, context):
        col = menu.layout.column(align=True)
        for ln in lines:
            col.label(text=ln)
        col.separator()
        col.operator("mc_voxel.open_folder", icon="FILE_FOLDER")

    try:
        wm.popup_menu(draw, title=title, icon=icon)
    except Exception:
        pass


class MCVOXEL_OT_open_folder(bpy.types.Operator):
    """打开上次导出文件所在的文件夹"""

    bl_idname = "mc_voxel.open_folder"
    bl_label = "打开所在文件夹"

    def execute(self, context):
        props = _scene(context).mc_voxel
        folder = props.last_export_dir
        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "还没有导出记录，或目录已经不存在")
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=folder)
        except Exception as exc:
            self.report({"ERROR"}, f"打不开文件夹：{exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


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
    filter_glob: bpy.props.StringProperty(default="*.litematic", options={"HIDDEN"})

    def execute(self, context):
        props = _scene(context).mc_voxel

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

        try:
            st = _prepare(context)
            origin, size = core_build.grid_from_bounds(st["verts"], st["block_size"])
            axis, parts = (0, 1)
            if getattr(props, "auto_split", True):
                axis, parts = core_tiles.plan_parts(size, core_tiles.DEFAULT_PART_VOXELS)

            if parts > 1:
                # 体积超过单段上限：切段分别体素化、分别落盘
                models = _build_segments(st, origin, size, axis, parts, context)
                stem, ext = os.path.splitext(path)
                names = []
                for i, model in enumerate(models):
                    seg_path = stem + core_tiles.part_suffix(axis, i, parts) + (ext or "")
                    _write_model(seg_path, model, props, data_version)
                    names.append(os.path.basename(seg_path))
                total = sum(m.total_blocks for m in models)
                note = f"分 {parts} 段 · 共 {total:,} 方块"
                lines = [
                    f"已写出 {parts} 个文件，共 {total:,} 方块",
                    f"按 {'XYZ'[axis]} 轴切分，文件名带后缀",
                ]
                lines += [f"· {n}" for n in names[:4]]
                if len(names) > 4:
                    lines.append(f"… 还有 {len(names) - 4} 个")
                warn = ""
            else:
                model, hrep = _build_from_st(st, context)
                _write_model(path, model, props, data_version)
                dims = "×".join(map(str, model.size))
                note = f"{os.path.basename(path)} · {model.total_blocks:,} 方块 · {dims} 格"
                lines = [f"已写出 {os.path.basename(path)}", f"{model.total_blocks:,} 方块 · {dims} 格"]
                if hrep.autofit:
                    lines.append(hrep.autofit)
                warn = hrep.message if hrep.level == "warn" else ""
                if warn:
                    lines.append(warn)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        props.last_export_dir = os.path.dirname(path)
        props.last_export_note = note
        print(f"[mc_voxel] 导出完成：{note}")
        _notify("导出完成", lines)
        if warn:
            self.report({"WARNING"}, f"{note}。{warn}")
        else:
            self.report({"INFO"}, note)
        return {"FINISHED"}


class MCVOXEL_OT_export_all(bpy.types.Operator):
    """把场景里每个网格物体各导出一个投影文件"""

    bl_idname = "mc_voxel.export_all"
    bl_label = "批量导出全部网格"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    filter_folder: bpy.props.BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = _scene(context).mc_voxel
        folder = bpy.path.abspath(self.directory)
        if not folder or not os.path.isdir(folder):
            self.report({"ERROR"}, "请选择一个存在的输出目录")
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

        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "场景里没有网格物体")
            return {"CANCELLED"}

        ext = ".litematic"
        prev_active = context.view_layer.objects.active if context.view_layer else None
        done, failed, total_blocks = [], [], 0
        for obj in meshes:
            try:
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                context.view_layer.objects.active = obj
                st = _prepare(context)
                origin, size = core_build.grid_from_bounds(st["verts"], st["block_size"])
                axis, parts = core_tiles.plan_parts(size, core_tiles.DEFAULT_PART_VOXELS)
                safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in obj.name)
                base = os.path.join(folder, safe + ext)
                if parts > 1:
                    models = _build_segments(st, origin, size, axis, parts, context)
                    stem = os.path.splitext(base)[0]
                    for i, m in enumerate(models):
                        seg = stem + core_tiles.part_suffix(axis, i, parts) + ext
                        _write_model(seg, m, props, data_version)
                        total_blocks += m.total_blocks
                    done.append(f"{obj.name}（{parts} 段）")
                else:
                    model, _hrep = _build_from_st(st, context)
                    _write_model(base, model, props, data_version)
                    total_blocks += model.total_blocks
                    done.append(f"{obj.name}（{model.total_blocks:,} 方块）")
            except Exception as exc:  # 单个物体失败不打断整批
                failed.append(f"{obj.name}: {exc}")
                print(f"[mc_voxel] 批量导出：{obj.name} 失败 —— {exc}")

        # 还原选择状态，别把用户的选择改乱了
        bpy.ops.object.select_all(action="DESELECT")
        if prev_active is not None:
            try:
                context.view_layer.objects.active = prev_active
                prev_active.select_set(True)
            except Exception:
                pass

        props.last_export_dir = folder
        props.last_export_note = f"批量导出 {len(done)} 个物体 · 共 {total_blocks:,} 方块"
        lines = [f"导出 {len(done)} 个物体，共 {total_blocks:,} 方块"]
        lines += [f"· {d}" for d in done[:5]]
        if len(done) > 5:
            lines.append(f"… 还有 {len(done) - 5} 个")
        if failed:
            lines.append(f"失败 {len(failed)} 个，详见 Console")
        _notify("批量导出完成", lines)
        print(f"[mc_voxel] 批量导出完成：{len(done)} 个成功，{len(failed)} 个失败")
        for f in failed:
            print(f"[mc_voxel]   ✗ {f}")
        if not done:
            self.report({"ERROR"}, "全部失败，看 Console")
            return {"CANCELLED"}
        self.report({"WARNING" if failed else "INFO"}, props.last_export_note)
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
# 三个预设都默认空心：实心会让 Litematica 要渲染的方块成倍增加，44k 方块就能卡住，
# 而且施工时内部方块白费材料。真需要实心在面板「高级设置 → 效果」里手动开。
PRESETS = {
    "BUILDING": ("建筑", 1.0, False, True),
    "PIXEL": ("像素画", 0.2, False, True),
    "TERRAIN": ("地形", 4.0, False, True),
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
    MCVOXEL_OT_preset,
    MCVOXEL_OT_fit_size,
    MCVOXEL_OT_render_preview,
    MCVOXEL_OT_detect_jar,
    MCVOXEL_OT_open_panel,
    MCVOXEL_OT_open_folder,
    MCVOXEL_OT_export_all,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
