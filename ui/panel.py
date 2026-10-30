"""侧栏面板。

分成两块：
- 主面板：新手动一次就能出结果的东西（选物体、预设、方块尺寸、导出）
- 高级设置：色板、限高、分块、噪声、作者这类调参，默认折叠

原则是"台面上只放常用的"。所有功能都还在，只是不挤在第一屏。
"""

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

        # ---- 1. 现在选中了什么（说不清就会一直被"先选物体"卡住） ----
        box = layout.box()
        if obj is None:
            box.label(text="还没选中物体", icon="ERROR")
            box.label(text="左键点一下要体素化的模型", icon="BLANK1")
        elif obj.type != "MESH":
            from .operators import _TYPE_NAMES

            kind = _TYPE_NAMES.get(obj.type, obj.type)
            box.label(text=f"选中的是{kind}：{obj.name}", icon="ERROR")
            box.label(text="左键点一下要体素化的模型", icon="BLANK1")
        else:
            box.label(text=f"选中：{obj.name}", icon="MESH_DATA")

        # ---- 2. 预设：一键套一组参数，新手直接点这个就行 ----
        row = box.row(align=True)
        row.label(text="一键预设")
        for key, label in (("BUILDING", "建筑"), ("PIXEL", "像素画"), ("TERRAIN", "地形")):
            op = row.operator("mc_voxel.preset", text=label)
            op.preset = key

        # ---- 3. 方块尺寸（唯一必须理解的参数） ----
        box.prop(props, "block_size")
        if props.block_size >= 0.9:
            box.label(text="一格 1 米左右：适合房子、雕塑", icon="INFO")
        elif props.block_size >= 0.5:
            box.label(text="一格半米左右：适合小物件", icon="INFO")
        else:
            box.label(text="格子很小：方块会非常多", icon="INFO")

        # 按目标格数反算，省得自己试 0.05 还是 0.08
        row = box.row(align=True)
        row.prop(props, "size_axis", text="")
        row.prop(props, "size_target", text="格")
        row.operator("mc_voxel.fit_size", text="定倍率", icon="DRIVER")

        # ---- 4. 动作：把导出放最前，因为大多数人只需要这个 ----
        layout.operator("mc_voxel.export", icon="FILE_TICK", text="导出投影（.litematic）")
        row = layout.row(align=True)
        row.operator("mc_voxel.voxelize", icon="MOD_BUILD", text="只看统计")
        row.operator("mc_voxel.preview", icon="RESTRICT_VIEW_OFF", text="方块预览")

        # ---- 5. 上次导出（report 只在状态栏闪一下，看不见） ----
        if props.last_export_note:
            res = layout.box()
            res.label(text="上次导出", icon="CHECKMARK")
            res.label(text=props.last_export_note)
            res.operator("mc_voxel.open_folder", icon="FILE_FOLDER")


class MCVOXEL_PT_advanced(bpy.types.Panel):
    """调参都塞这里，默认折叠。"""

    bl_label = "高级设置"
    bl_idname = "MCVOXEL_PT_advanced"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MC 体素"
    bl_parent_id = "MCVOXEL_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.mc_voxel

        # ---- 效果 ----
        col = layout.column(align=True)
        col.label(text="效果", icon="MODIFIER")
        col.prop(props, "solid_fill")
        col.prop(props, "gravity_support")
        col.prop(props, "work_blocks")
        col.prop(props, "light_blocks")
        col.prop(props, "max_kinds")
        col.prop(props, "noise")
        col.label(text="噪声：同色墙面混入次近方块，0 就是每次结果都一样", icon="BLANK1")

        # ---- 世界与限高 ----
        box = layout.box()
        box.label(text="世界与限高", icon="WORLD")
        box.prop(props, "dimension")
        box.prop(props, "auto_fit_height")
        box.prop(props, "strict_height")

        from ..core.limits import GROUND_LEVEL, get_dimension

        try:
            jar = bpy.path.abspath(props.jar_path) if props.jar_path else None
            dim = get_dimension(props.dimension, jar)
        except Exception:  # 读 jar 失败不能让整个面板崩掉
            box.label(text="限高数值读取失败", icon="ERROR")
        else:
            box.label(text=f"限高 {dim.height} 格（y {dim.min_y}..{dim.max_y}）", icon="INFO")
            ground = GROUND_LEVEL.get(dim.name)
            if ground is not None:
                box.label(
                    text=f"地面 y={ground} 以上可用 {dim.max_y - ground + 1} 格",
                    icon="BLANK1",
                )
        if not props.strict_height:
            box.label(text="严格限高已关：超出部分摆不出来", icon="ERROR")
        # 坐标转换是 bl(x,y,z) -> mc(x, z, -y)，所以 Blender 的 +Y 是 MC 的北
        box.label(text="MC 朝向：+X 东，+Y 北，+Z 上", icon="INFO")

        # ---- 色板 ----
        box = layout.box()
        box.label(text="色板（决定用哪些方块）", icon="COLOR")
        box.prop(props, "jar_path")
        if not props.jar_path.strip():
            row = box.row(align=True)
            row.label(text="留空 = 自动找启动器里的 jar", icon="INFO")
            row.operator("mc_voxel.detect_jar", text="自动找", icon="VIEWZOOM")
        box.prop(props, "mc_release")
        box.prop(props, "mc_data_version")
        if props.mc_data_version == 0 and props.mc_release != "CUSTOM":
            from ..core.mcver import KNOWN_DATA_VERSIONS

            dv = KNOWN_DATA_VERSIONS.get(props.mc_release)
            if dv:
                box.label(text=f"将写入 DataVersion = {dv}", icon="INFO")

        # 白名单 / 禁用：输入框必须单独占一整行，和中文标签挤一行会只剩省略号
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

        # ---- 大模型与文件 ----
        box = layout.box()
        box.label(text="大模型与文件", icon="FILE_TICK")
        box.prop(props, "auto_split")
        box.prop(props, "max_blocks")
        box.prop(props, "region_size")
        box.prop(props, "author")
        box.operator("mc_voxel.export_all", icon="FILE_FOLDER")

        # ---- 预览图 ----
        box = layout.box()
        box.label(text="预览图", icon="IMAGE_DATA")
        box.prop(props, "preview_path")
        box.prop(props, "preview_size")
        box.operator("mc_voxel.render_preview", icon="RENDER_STILL")


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
        layout.operator("mc_voxel.export_all", icon="FILE_FOLDER")


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
        layout.operator("mc_voxel.export_all", icon="FILE_FOLDER")


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
    bpy.utils.register_class(MCVOXEL_PT_advanced)
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
    bpy.utils.unregister_class(MCVOXEL_PT_advanced)
    bpy.utils.unregister_class(MCVOXEL_PT_panel)
