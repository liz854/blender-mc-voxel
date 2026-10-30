"""M1 验收：在真实 Blender 里跑一遍扩展。

    blender -b --python tools/blender_m1.py

检查项（对应方案文档 M1）：
  1. 扩展已注册
  2. 立方体三角化后 verts=8 tris=12
  3. 10x10x10 立方体 -> Filled: 1000
  4. 外壳模式 -> 488（1000 - 8^3）
  5. Preview 生成点云物体
  6. 导出 .litematic -> 读回逐块一致
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

import bpy

FAIL = []


def check(name, cond, detail=""):
    ok = "PASS" if cond else "FAIL"
    print(f"[{ok}] {name} {detail}")
    if not cond:
        FAIL.append(name)


def _expect_cancel(call):
    """预期返回 CANCELLED 的操作符。

    bpy.ops 在操作符 report ERROR 并返回 CANCELLED 时会抛 RuntimeError，
    这里把它当成正常的 CANCELLED 结果。
    """
    try:
        return call()
    except RuntimeError:
        return {"CANCELLED"}


def main():
    addon_key = None
    for key in bpy.context.preferences.addons.keys():
        if "mc_voxel_export" in key:
            addon_key = key
    check("扩展已注册", addon_key is not None, f"key={addon_key}")
    if addon_key is None:
        return 1

    from bl_ext.user_default.mc_voxel_export.core import build as core_build
    from bl_ext.user_default.mc_voxel_export.core import (
        litematic as core_litematic,
        mcver,
        mesh_data,
        nbt as core_nbt,
    )
    from bl_ext.user_default.mc_voxel_export.core.palette import (
        BlockPalette,
        load_fallback_palette,
    )

    # --- 1. 三角化检查 ---
    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.mesh.primitive_cube_add(size=10.0, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "M1Cube"
    bpy.context.view_layer.objects.active = obj

    verts, tris, colors = mesh_data.mesh_to_mc(obj)
    check(
        "三角化 verts=8 tris=12",
        len(verts) == 8 and len(tris) == 12,
        f"verts={len(verts)} tris={len(tris)}",
    )

    # --- 2. 10x10x10 -> 1000 ---
    props = bpy.context.scene.mc_voxel
    props.block_size = 1.0
    props.solid_fill = True
    props.max_blocks = 2_000_000

    palette = BlockPalette(load_fallback_palette())
    model = core_build.build_voxel_model(
        verts, tris, colors, block_size=1.0, palette=palette, solid=True
    )
    check("10x10x10 实心 = 1000", model.total_blocks == 1000, f"size={model.size} filled={model.total_blocks}")

    # --- 3. 外壳 -> 488 ---
    shell = core_build.build_voxel_model(
        verts, tris, colors, block_size=1.0, palette=palette, solid=False
    )
    check("外壳 = 488", shell.total_blocks == 488, f"filled={shell.total_blocks}")

    # --- 4. 操作符（走真实面板路径，含 report） ---
    r = bpy.ops.mc_voxel.voxelize()
    check("operator mc_voxel.voxelize", r == {"FINISHED"}, str(r))

    r = bpy.ops.mc_voxel.preview()
    preview = bpy.data.objects.get("MCVoxelPreview")
    check(
        "operator mc_voxel.preview 生成物体",
        r == {"FINISHED"} and preview is not None,
        f"{r} obj={preview.name if preview else None} faces={len(preview.data.polygons) if preview else 0}",
    )
    # 预览是只画外露面：10x10x10 立方体的外表面 = 6 * 100 = 600 个四边面
    if preview:
        n_faces = len(preview.data.polygons)
        check("预览只画外露面 = 600", n_faces == 600, f"faces={n_faces}")

    # --- 4.6 禁用方块（需求⑦）：禁掉的从候选池移除，方块数不变（不留空洞） ---
    tmp = tempfile.mkdtemp(prefix="mcvoxel_")
    out_deny = os.path.join(tmp, "m1_deny.litematic")
    props.disable_blocks = "stone"
    r = bpy.ops.mc_voxel.export(filepath=out_deny)
    check("禁用方块后导出仍 FINISHED", r == {"FINISHED"} and os.path.exists(out_deny), str(r))
    if os.path.exists(out_deny):
        back = core_litematic.read_litematic(out_deny)
        names = sorted({s.name for s in back.palette})
        check(
            "禁用 minecraft:stone 生效（palette 里没它）",
            "minecraft:stone" not in names,
            f"palette={names[:8]}",
        )
        check(
            "禁用后不留空洞（方块数仍是 1000）",
            back.total_blocks == 1000,
            f"blocks={back.total_blocks}",
        )
    props.disable_blocks = ""

    # --- 4b. 白名单/禁用框的输入辅助（侧栏太窄，单行输入框里 id 全是省略号） ---
    from bl_ext.user_default.mc_voxel_export.ui import picker

    props.allow_blocks = ""
    props.disable_blocks = ""

    r = bpy.ops.mc_voxel.pick_block(target="ALLOW", query="minecraft:stone")
    check(
        "搜索添加：写进白名单框",
        r == {"FINISHED"} and props.allow_blocks == "minecraft:stone",
        f"{r} text={props.allow_blocks!r}",
    )
    bpy.ops.mc_voxel.pick_block(target="ALLOW", query="Minecraft:Stone")
    check(
        "搜索添加：重复的不重复写（大小写不敏感）",
        props.allow_blocks == "minecraft:stone",
        repr(props.allow_blocks),
    )
    bpy.ops.mc_voxel.pick_block(target="ALLOW", query="oak_planks")
    check(
        "搜索添加：第二项用逗号接上，短名自动补 minecraft:",
        props.allow_blocks == "minecraft:stone, minecraft:oak_planks",
        repr(props.allow_blocks),
    )

    auto_jar = mcver.find_client_jar()
    if auto_jar:
        props.jar_path = auto_jar
    names = picker.palette_names(props)
    check("搜索候选非空（来自真实 jar 或兜底色板）", len(names) >= 60, f"{len(names)} 种")

    hit, missing, ntok = picker.resolve(props, "stone, *wool*")
    check(
        "输入框回读：通配展开成真实方块名",
        ntok == 2
        and "minecraft:stone" in hit
        and any(n.endswith("_wool") for n in hit)
        and not missing,
        f"写了 {ntok} 项 -> 命中 {len(hit)} 种，缺 {missing[:3]}",
    )

    hit, _, ntok = picker.resolve(props, ", ".join(names[:12]))
    check("输入框回读：12 项全命中（长文本也能看见生效结果）", len(hit) == 12, f"{len(hit)}/12")

    r = bpy.ops.mc_voxel.list_filter(target="ALLOW")
    check("明细弹窗能画（无头下返回 FINISHED）", r == {"FINISHED"}, str(r))

    r = bpy.ops.mc_voxel.clear_filter(target="ALLOW")
    check("清空白名单", r == {"FINISHED"} and props.allow_blocks == "", repr(props.allow_blocks))

    bpy.ops.mc_voxel.pick_block(target="DENY", query="snow_block")
    check(
        "禁用框走同一套（短名补前缀）",
        props.disable_blocks == "minecraft:snow_block",
        repr(props.disable_blocks),
    )
    bpy.ops.mc_voxel.pick_block(target="DENY", query="no_such_block")
    _, missing, _ = picker.resolve(props, props.disable_blocks)
    check(
        "不认识的方块名原样留着并标为缺失（不瞎补）",
        "minecraft:no_such_block" in missing and props.disable_blocks.endswith("no_such_block"),
        f"missing={missing} text={props.disable_blocks!r}",
    )
    bpy.ops.mc_voxel.clear_filter(target="DENY")

    props.jar_path = ""
    r = _expect_cancel(lambda: bpy.ops.mc_voxel.detect_jar())
    if auto_jar:
        check(
            "自动找 jar：填进面板",
            r == {"FINISHED"} and props.jar_path == auto_jar,
            f"{r} {props.jar_path!r}",
        )
    else:
        check(
            "自动找 jar：找不到就报错，不瞎填",
            r == {"CANCELLED"} and props.jar_path == "",
            f"{r} {props.jar_path!r}",
        )
    props.allow_blocks = ""
    props.disable_blocks = ""
    props.jar_path = ""

    # --- 4c. 布尔挖洞留下的纯黑顶点色要补回来（否则墙上出现深浅两片斜分界） ---
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    black_test = bpy.context.active_object
    black_test.name = "BlackVertTest"
    attr = black_test.data.color_attributes.new(
        name="Col", type="FLOAT_COLOR", domain="POINT"
    )
    for d in attr.data:
        d.color = (1.0, 1.0, 1.0, 1.0)
    for i in (0, 1, 2):  # 布尔产生的新顶点就是这个值
        attr.data[i].color = (0.0, 0.0, 0.0, 1.0)

    _, _, c_black = mesh_data.mesh_to_mc(black_test)
    check(
        "缺失顶点色（纯黑）按同面顶点补回，不留黑斑",
        bool((c_black > 1.0).all()),
        f"min={c_black.min(axis=0)}",
    )

    for d in attr.data:  # 整片都没涂：别猜，保持原样
        d.color = (0.0, 0.0, 0.0, 1.0)
    _, _, c_all_black = mesh_data.mesh_to_mc(black_test)
    check(
        "整片没涂色时原样返回（不瞎补成白色）",
        bool((c_all_black <= 1.0).all()),
        f"max={c_all_black.max(axis=0)}",
    )
    bpy.data.objects.remove(black_test, do_unlink=True)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # --- 5. 导出 -> 读回（不手填 DataVersion，走面板的「游戏版本」枚举） ---
    out = os.path.join(tmp, "m1_cube.litematic")
    bpy.context.view_layer.objects.active = obj
    props.mc_release = "26.3"
    props.mc_data_version = 0  # 0 = 自动（jar 优先，其次版本表）
    props.jar_path = ""
    props.export_format = "LITEMATIC"

    found_jar = mcver.find_client_jar()
    r = bpy.ops.mc_voxel.export(filepath=out)
    check("operator mc_voxel.export", r == {"FINISHED"} and os.path.exists(out), f"{r} exists={os.path.exists(out)}")

    if os.path.exists(out):
        raw = core_nbt.read_nbt(out)
        dv = int(raw["MinecraftDataVersion"])
        if found_jar is None:
            check("自动推断 DataVersion = 5023（26.3）", dv == 5023, f"dv={dv}")
        else:
            check("自动推断 DataVersion 走了 jar", dv > 0, f"dv={dv} jar={found_jar}")
        check("Version = 7 + SubVersion 1", int(raw["Version"]) == 7 and int(raw["SubVersion"]) == 1, f"Version={int(raw['Version'])} Sub={int(raw['SubVersion'])}")
        meta = raw["Metadata"]
        check(
            "Metadata 齐全（Litematica 从这读统计）",
            str(meta["Name"]) != "" and int(meta["RegionCount"]) >= 1 and int(meta["TotalBlocks"]) == 1000,
            f"Name={meta['Name']} RegionCount={meta['RegionCount']} TotalBlocks={meta['TotalBlocks']}",
        )
        back = core_litematic.read_litematic(out)
        same = back.size == model.size and back.total_blocks == model.total_blocks
        check(
            "读回尺寸与方块数一致",
            same,
            f"size={back.size} filled={back.total_blocks} vs orig {model.size}/{model.total_blocks}",
        )
        # 逐块比对
        diff = 0
        if back.size == model.size:
            for y in range(model.size[1]):
                for z in range(model.size[2]):
                    for x in range(model.size[0]):
                        a = back.get(x, y, z)
                        b = model.get(x, y, z)
                        if a.name != b.name or tuple(sorted(a.props.items())) != tuple(sorted(b.props.items())):
                            diff += 1
        check("逐块一致", diff == 0, f"diff={diff}")

    # --- 6. 预设（方案 Sprint 4） ---
    r = bpy.ops.mc_voxel.preset(preset="PIXEL")
    check(
        "预设 像素画 -> 0.2 / 只留外壳",
        r == {"FINISHED"} and abs(props.block_size - 0.2) < 1e-6 and not props.solid_fill,
        f"{r} size={props.block_size} solid={props.solid_fill}",
    )
    bpy.ops.mc_voxel.preset(preset="BUILDING")
    check(
        "预设 建筑 -> 1.0 / 实心",
        abs(props.block_size - 1.0) < 1e-6 and props.solid_fill,
        f"size={props.block_size} solid={props.solid_fill}",
    )

    # --- 7. 白名单（方案 Sprint 2 §5） ---
    props.allow_blocks = "stone"
    out_allow = os.path.join(tmp, "m1_allow.litematic")
    r = bpy.ops.mc_voxel.export(filepath=out_allow)
    if r == {"FINISHED"} and os.path.exists(out_allow):
        back_allow = core_litematic.read_litematic(out_allow)
        names = {s.name for s in back_allow.palette}
        check(
            "白名单只留 stone",
            names == {"minecraft:air", "minecraft:stone"},
            f"palette={sorted(names)}",
        )
        check(
            "白名单后方块数不变（不留空洞）",
            back_allow.total_blocks == 1000,
            f"blocks={back_allow.total_blocks}",
        )
    else:
        check("白名单导出", False, str(r))
    props.allow_blocks = ""

    # --- 8. 单位与物体缩放（方案 §3.3） ---
    import numpy as np

    bpy.context.view_layer.objects.active = obj
    v0, _, _ = mesh_data.mesh_to_mc(obj)
    span0 = v0.max(axis=0) - v0.min(axis=0)
    obj.scale = (2.0, 2.0, 2.0)
    v2, t2, c2 = mesh_data.mesh_to_mc(obj)
    span2 = v2.max(axis=0) - v2.min(axis=0)
    check(
        "物体放大 2 倍已计入尺寸",
        bool(np.allclose(span2, span0 * 2.0)),
        f"span {span0} -> {span2}",
    )
    scaled = core_build.build_voxel_model(
        v2, t2, c2, block_size=1.0, palette=palette, solid=True
    )
    check("放大 2 倍的方块 -> 8000", scaled.total_blocks == 8000, f"filled={scaled.total_blocks}")
    obj.scale = (1.0, 1.0, 1.0)

    unit = bpy.context.scene.unit_settings
    unit.scale_length = 2.0
    v3, _, _ = mesh_data.mesh_to_mc(obj)
    span3 = v3.max(axis=0) - v3.min(axis=0)
    check(
        "场景 scale_length 已换算",
        bool(np.allclose(span3, span0 * 2.0)),
        f"scale_length=2.0 -> span {span3}",
    )
    unit.scale_length = 1.0

    # --- 10. 世界限高 ---
    from bl_ext.user_default.mc_voxel_export.core import limits

    jar = bpy.path.abspath(props.jar_path) if props.jar_path else mcver.find_client_jar()
    real_dims = limits.read_dimensions(jar) if jar and os.path.exists(jar) else {}
    ow = (real_dims or limits.DIMENSION_DEFAULTS).get("overworld")
    check(
        "限高：主世界 384 格（y -64..319）",
        ow is not None and ow.height == 384 and ow.min_y == -64,
        f"{ow} jar={'读了' if real_dims else '兜底'}",
    )

    # 造一根 400 格高的方柱（Blender 是 Z 朝上，MC 的 y 来自 Blender 的 z）
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(40, 0, 0))
    bar = bpy.context.active_object
    bar.name = "M1Bar"
    bar.scale = (4.0, 4.0, 400.0)
    bpy.context.view_layer.objects.active = bar
    props.block_size = 1.0
    props.dimension = "overworld"
    props.strict_height = True
    props.auto_fit_height = False  # 这一节要验的是"拦住"，所以关掉自动压限高

    r = _expect_cancel(lambda: bpy.ops.mc_voxel.voxelize())
    check("400 格高：严格限高拦下", r == {"CANCELLED"}, str(r))

    props.strict_height = False
    r = bpy.ops.mc_voxel.voxelize()
    check("关掉严格限高后放行（只告警）", r == {"FINISHED"}, str(r))
    props.strict_height = True

    props.dimension = "the_nether"
    r = _expect_cancel(lambda: bpy.ops.mc_voxel.voxelize())
    check("下界限高 256：400 格同样拦下", r == {"CANCELLED"}, str(r))

    bar.scale = (4.0, 4.0, 55.0)
    props.dimension = "overworld"
    r = bpy.ops.mc_voxel.voxelize()
    check("55 格（猴头量级）正常通过", r == {"FINISHED"}, str(r))

    # --- 11. 按长度定倍率 / 自动压限高 / 等距预览图 ---
    bpy.context.view_layer.objects.active = obj  # 回到 10x10x10 立方体
    props.dimension = "overworld"
    props.strict_height = True
    props.auto_fit_height = True
    props.size_axis = "Y"
    props.size_target = 40
    r = bpy.ops.mc_voxel.fit_size()
    check(
        "按高度 40 格定倍率 -> 0.25",
        r == {"FINISHED"} and abs(props.block_size - 0.25) < 1e-6,
        f"bs={props.block_size}",
    )
    m40 = core_build.build_voxel_model(
        *mesh_data.mesh_to_mc(obj), block_size=props.block_size, palette=palette, solid=True
    )
    check("按 40 格反算出来真的是 40 格", m40.size[1] == 40, f"size={m40.size}")

    # 400 格高的柱子：倍率 1.0 会超限高，应自动放大到刚好塞得下而不是报错
    bpy.context.view_layer.objects.active = bar
    bar.scale = (4.0, 4.0, 400.0)
    props.block_size = 1.0
    r = bpy.ops.mc_voxel.voxelize()
    check(
        "自动压限高：不报错且倍率被放大",
        r == {"FINISHED"} and props.block_size > 1.0,
        f"bs={props.block_size:.4f}",
    )
    mfit = core_build.build_voxel_model(
        *mesh_data.mesh_to_mc(bar), block_size=props.block_size, palette=palette, solid=True
    )
    check("自动压完高度 <= 384", mfit.size[1] <= 384, f"h={mfit.size[1]}")

    # 等距预览图（Workbench，无头也能渲）
    bpy.context.view_layer.objects.active = obj
    obj.scale = (1.0, 1.0, 1.0)
    props.block_size = 1.0
    props.preview_size = 320
    png_path = os.path.join(tmp, "m1_preview.png")
    props.preview_path = png_path
    r = bpy.ops.mc_voxel.render_preview()
    size_ok = os.path.exists(png_path) and os.path.getsize(png_path) > 1000
    check("渲染等距预览图", r == {"FINISHED"} and size_ok, f"{r} {os.path.getsize(png_path) if size_ok else 0} B")
    if size_ok:
        from bl_ext.user_default.mc_voxel_export.core import png as core_png

        w, h, rgba = core_png.decode(open(png_path, "rb").read())
        n_colors = len(np.unique(rgba.reshape(-1, rgba.shape[2]), axis=0))
        check(
            "预览图长边 320 且不是纯色",
            max(w, h) == 320 and n_colors > 5,
            f"{w}x{h} 颜色数={n_colors}",
        )
    if os.path.exists(png_path):
        print(f"[preview] {png_path}")

    # 入口与文案：侧栏标签找不到时靠顶部菜单，标签必须全中文
    check(
        "面板标签是中文「MC 体素」",
        bpy.types.MCVOXEL_PT_panel.bl_label == "MC 体素"
        and bpy.types.MCVOXEL_PT_panel.bl_category == "MC 体素",
        f"{bpy.types.MCVOXEL_PT_panel.bl_label} / {bpy.types.MCVOXEL_PT_panel.bl_category}",
    )
    check(
        "顶部菜单已注册（主菜单栏全名 + 3D header 短名）",
        hasattr(bpy.types, "MCVOXEL_MT_menu")
        and hasattr(bpy.types, "MCVOXEL_MT_view3d_menu")
        and bpy.types.MCVOXEL_MT_menu.bl_label == "MC 体素"
        and bpy.types.MCVOXEL_MT_view3d_menu.bl_label == "体素",
        f"{bpy.types.MCVOXEL_MT_menu.bl_label} / {bpy.types.MCVOXEL_MT_view3d_menu.bl_label}",
    )
    from bl_ext.user_default.mc_voxel_export.ui import panel as ui_panel

    check(
        "菜单入口函数存在（顶部菜单栏 + 右键菜单都挂它）",
        callable(getattr(ui_panel, "_menu_entry", None)),
    )
    check(
        "操作符标签不再是英文",
        bpy.ops.mc_voxel.voxelize.get_rna_type().name == "体素化"
        and bpy.ops.mc_voxel.export.get_rna_type().name == "导出投影",
        f"{bpy.ops.mc_voxel.voxelize.get_rna_type().name} / {bpy.ops.mc_voxel.export.get_rna_type().name}",
    )
    check(
        "面板浮窗操作符可用",
        hasattr(bpy.ops.mc_voxel, "open_panel")
        and bpy.ops.mc_voxel.open_panel.get_rna_type().name == "打开 MC 体素面板",
    )

    # 浮窗面板里 context.active_object 常为空，用户明明选了却提示「先选一个网格物体」
    from bl_ext.user_default.mc_voxel_export.ui import operators as ui_ops

    bpy.context.view_layer.objects.active = None
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    r = bpy.ops.mc_voxel.voxelize()
    check("活动物体为空、只有一个选中网格时照样能体素化", r == {"FINISHED"}, str(r))

    cam = bpy.data.objects.new("TestCam", bpy.data.cameras.new("TestCam"))
    bpy.context.collection.objects.link(cam)
    bpy.context.view_layer.objects.active = cam
    try:
        ui_ops._active_mesh(bpy.context)
        cam_msg = ""
    except RuntimeError as exc:
        cam_msg = str(exc)
    check("选中摄像机时提示里点名「摄像机」", "摄像机" in cam_msg, cam_msg[:40])

    bpy.data.objects.remove(cam, do_unlink=True)
    bpy.context.view_layer.objects.active = obj

    print("\n=== 总结 ===")
    if FAIL:
        print(f"{len(FAIL)} 项失败：{FAIL}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 2
    sys.exit(code)
