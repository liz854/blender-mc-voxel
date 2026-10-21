"""侧栏面板。"""

from __future__ import annotations

import bpy


class MCVOXEL_PT_panel(bpy.types.Panel):
    bl_label = "MC 体素"
    bl_idname = "MCVOXEL_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MC 体素"

    def draw(self, context):
        layout = self.layout
        props = context.scene.mc_voxel
        obj = context.active_object

        box = layout.box()
        box.label(text=f"选中：{obj.name if obj else '—'}", icon="MESH_DATA")

        # 预设：一键套一组参数
        row = box.row(align=True)
        row.label(text="预设")
        for key, label in (("BUILDING", "建筑"), ("PIXEL", "像素画"), ("TERRAIN", "地形")):
            op = row.operator("mc_voxel.preset", text=label)
            op.preset = key

        box.prop(props, "block_size")

        # 按某一轴的格数反算倍率：不用自己试 0.05 还是 0.08
        row = box.row(align=True)
        row.prop(props, "size_axis", text="")
        row.prop(props, "size_target", text="格")
        row.operator("mc_voxel.fit_size", text="定倍率", icon="DRIVER")
        if props.size_target <= 0:
            box.label(text="填目标格数后点「定倍率」反算方块尺寸", icon="INFO")

        box.prop(props, "solid_fill")
        box.prop(props, "gravity_support")
        box.prop(props, "auto_fit_height")

        box = layout.box()
        box.label(text="色板", icon="COLOR")
        # 先说清这两个框填什么，省得猜
        box.label(text="填方块 id，逗号分隔，可写 * 通配", icon="INFO")
        box.label(text="例：oak_planks, *concrete*, glass", icon="BLANK1")
        box.prop(props, "jar_path")
        if not props.jar_path.strip():
            row = box.row(align=True)
            row.label(text="留空 = 自动找启动器里的 jar", icon="INFO")
            row.operator("mc_voxel.detect_jar", text="自动找", icon="VIEWZOOM")
        box.prop(props, "mc_release")
        box.prop(props, "mc_data_version")

        # 白名单 / 禁用：输入框必须单独占一整行。和标签挤同一行时，中文标签
        # 会把输入框压成十几个像素，填进去的 id 全变成省略号，等于看不见。
        from .picker import resolve, short_name

        for attr, target, title, hint in (
            ("allow_blocks", "ALLOW", "只用这些方块", "留空不限"),
            ("disable_blocks", "DENY", "禁用方块", "自动换近色"),
        ):
            col = box.column(align=True)
            col.label(text=f"{title}（{hint}）", icon="FILTER")
            col.prop(props, attr, text="")

            row = col.row(align=True)
            op = row.operator("mc_voxel.pick_block", text="搜索添加", icon="VIEWZOOM")
            op.target = target
            op = row.operator("mc_voxel.clear_filter", text="清空", icon="X")
            op.target = target

            text = getattr(props, attr, "") or ""
            if not text.strip():
                continue
            # 输入框看不下长文本，所以在这里回读解析结果：填了 `*wool*`
            # 也能看见它到底展开成了哪几种
            hit, missing, ntok = resolve(props, text)
            if hit:
                shown = ", ".join(short_name(n) for n in hit[:4])
                more = "" if len(hit) <= 4 else f" 等 {len(hit)} 种"
                row2 = col.row(align=True)
                row2.label(text=f"生效 {shown}{more}", icon="CHECKMARK")
                op = row2.operator("mc_voxel.list_filter", text="", icon="EYEDROPPER")
                op.target = target
            else:
                col.label(text="一种都没匹配上，点「搜索添加」挑一个", icon="ERROR")
            if missing:
                col.label(
                    text=(
                        f"{len(missing)} 个不在色板里："
                        + ", ".join(short_name(m) for m in missing[:3])
                    ),
                    icon="QUESTION",
                )

        if props.mc_data_version == 0 and props.mc_release != "CUSTOM":
            from ..core.mcver import KNOWN_DATA_VERSIONS

            dv = KNOWN_DATA_VERSIONS.get(props.mc_release)
            if dv:
                box.label(text=f"将写入 DataVersion = {dv}", icon="INFO")

        box = layout.box()
        box.label(text="导出", icon="EXPORT")
        box.prop(props, "export_format")
        box.prop(props, "dimension")
        box.prop(props, "strict_height")

        # 限高：数值来自客户端 jar 的 dimension_type，读不到用内置兜底
        from ..core.limits import GROUND_LEVEL, get_dimension

        try:
            jar = bpy.path.abspath(props.jar_path) if props.jar_path else None
            dim = get_dimension(props.dimension, jar)
        except Exception:  # 面板不能因为读 jar 失败就整个崩掉
            box.label(text="限高数值读取失败", icon="ERROR")
        else:
            box.label(
                text=f"限高 {dim.height} 格（y {dim.min_y}..{dim.max_y}）",
                icon="INFO",
            )
            ground = GROUND_LEVEL.get(dim.name)
            if ground is not None:
                box.label(
                    text=f"地面 y={ground} 以上可用 {dim.max_y - ground + 1} 格",
                    icon="BLANK1",
                )
        if not props.strict_height:
            box.label(text="严格限高已关：超出部分摆不出来", icon="ERROR")

        box.prop(props, "region_size")
        box.prop(props, "author")
        box.prop(props, "max_blocks")

        box = layout.box()
        box.label(text="预览图", icon="IMAGE_DATA")
        box.prop(props, "preview_path")
        box.prop(props, "preview_size")
        box.operator("mc_voxel.render_preview", icon="RENDER_STILL")

        box = layout.box()
        box.label(text="反向导入后", icon="IMPORT")
        box.prop(props, "smooth_shading")
        box.prop(props, "remesh_smooth")
        if props.remesh_smooth:
            box.prop(props, "remesh_voxel_size")
            if props.smooth_shading:
                box.label(text="磨圆会丢锐利边缘和颜色细节", icon="ERROR")

        row = layout.row(align=True)
        row.operator("mc_voxel.voxelize", icon="MOD_BUILD")
        row.operator("mc_voxel.preview", icon="RESTRICT_VIEW_OFF")
        layout.operator("mc_voxel.export", icon="FILE_TICK")
        layout.operator("mc_voxel.import_litematic", icon="IMPORT")


class MCVOXEL_MT_menu(bpy.types.Menu):
    """3D 视图顶部菜单栏的入口。侧栏标签不好找，这里一眼能看到。"""

    bl_label = "MC 体素"
    bl_idname = "MCVOXEL_MT_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator("mc_voxel.open_panel", icon="WINDOW")
        layout.separator()
        layout.operator("mc_voxel.voxelize", icon="MOD_BUILD")
        layout.operator("mc_voxel.preview", icon="RESTRICT_VIEW_OFF")
        layout.operator("mc_voxel.render_preview", icon="RENDER_STILL")
        layout.separator()
        layout.operator("mc_voxel.export", icon="FILE_TICK")
        layout.operator("mc_voxel.import_litematic", icon="IMPORT")


def _menu_entry(self, context):
    self.layout.menu("MCVOXEL_MT_menu")


class MCVOXEL_MT_view3d_menu(bpy.types.Menu):
    """3D 视图 header 里的短名入口。

    header 那一行空间紧张，写「MC 体素」会被挤成「MC …」，
    所以这里只留两个字。
    """

    bl_label = "体素"
    bl_idname = "MCVOXEL_MT_view3d_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator("mc_voxel.open_panel", icon="WINDOW")
        layout.separator()
        layout.operator("mc_voxel.voxelize", icon="MOD_BUILD")
        layout.operator("mc_voxel.preview", icon="RESTRICT_VIEW_OFF")
        layout.operator("mc_voxel.render_preview", icon="RENDER_STILL")
        layout.separator()
        layout.operator("mc_voxel.export", icon="FILE_TICK")
        layout.operator("mc_voxel.import_litematic", icon="IMPORT")


def _view3d_menu_entry(self, context):
    self.layout.menu("MCVOXEL_MT_view3d_menu")


# 快捷键：Ctrl+Shift+M 直接把面板弹出来，不用去侧栏找标签
_addon_keymaps = []


def _register_keymap():
    try:
        kc = bpy.context.window_manager.keyconfigs.addon
    except AttributeError:  # 无头模式没有窗口管理器
        return
    if kc is None:
        return
    km = kc.keymaps.get("3D View")
    if km is None:
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new("mc_voxel.open_panel", "M", "PRESS", ctrl=True, shift=True)
    kmi.active = True
    _addon_keymaps.append((km, kmi))


def register():
    bpy.utils.register_class(MCVOXEL_PT_panel)
    bpy.utils.register_class(MCVOXEL_MT_menu)
    bpy.utils.register_class(MCVOXEL_MT_view3d_menu)
    # 顶部主菜单栏（文件/编辑/…/帮助 这一行）空间够，名字不会被截断；
    # 右键物体菜单里也放一份；3D 视图 header 用短名「体素」
    bpy.types.TOPBAR_MT_editor_menus.append(_menu_entry)
    bpy.types.VIEW3D_MT_object_context_menu.append(_menu_entry)
    bpy.types.VIEW3D_MT_editor_menus.append(_view3d_menu_entry)
    _register_keymap()


def unregister():
    for km, kmi in _addon_keymaps:
        km.keymap_items.remove(kmi)
    _addon_keymaps.clear()
    bpy.types.VIEW3D_MT_editor_menus.remove(_view3d_menu_entry)
    bpy.types.VIEW3D_MT_object_context_menu.remove(_menu_entry)
    bpy.types.TOPBAR_MT_editor_menus.remove(_menu_entry)
    bpy.utils.unregister_class(MCVOXEL_MT_view3d_menu)
    bpy.utils.unregister_class(MCVOXEL_MT_menu)
    bpy.utils.unregister_class(MCVOXEL_PT_panel)
