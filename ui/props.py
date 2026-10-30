"""面板属性。"""

from __future__ import annotations

import bpy

from ..core.mcver import DEFAULT_RELEASE, KNOWN_DATA_VERSIONS


def _release_items():
    """版本表 -> EnumProperty items。数字全部来自 core.mcver 的核对过的表。"""
    items = [
        (name, f"{name}（{dv}）", f"MinecraftDataVersion = {dv}")
        for name, dv in KNOWN_DATA_VERSIONS.items()
    ]
    items.append(("CUSTOM", "自定义（读 jar / 手填）", "从客户端 jar 读，或手填 DataVersion"))
    return items


class MCVoxelProps(bpy.types.PropertyGroup):
    mc_release: bpy.props.EnumProperty(
        name="游戏版本",
        description="决定 MinecraftDataVersion。装了客户端 jar 时以 jar 为准",
        items=_release_items(),
        default=DEFAULT_RELEASE,
    )
    block_size: bpy.props.FloatProperty(
        name="方块尺寸",
        description="1 体素 = N 米。越小越精细，体素数按立方增长",
        default=1.0,
        min=0.001,
        soft_max=8.0,
        precision=3,
    )
    solid_fill: bpy.props.BoolProperty(
        name="实心填充",
        description="默认只出外壳（空心）。打开后会填满内部：Litematica 要渲染的方块成倍增加，"
        "大模型容易卡；施工时也费材料。只有需要整体替换地形时才建议开",
        default=False,
    )
    disable_blocks: bpy.props.StringProperty(
        name="禁用方块",
        description=(
            "逗号或分号分隔，支持 * 通配（如 *wool*）。"
            "被禁用的方块不会留空洞，会自动换成色差最小的可用方块"
        ),
        default="",
    )
    allow_blocks: bpy.props.StringProperty(
        name="只用这些方块",
        description=(
            "白名单。留空表示不限制。语法同禁用方块，支持 * 通配。"
            "填了就只在这几种里选色，禁用方块仍然优先"
        ),
        default="",
    )
    gravity_support: bpy.props.BoolProperty(
        name="悬空禁用重力方块",
        description=(
            "沙子、沙砾、混凝土粉末下方没有方块时，换成色差最小的非重力方块。"
            "否则投影摆出去会塌成一地沙子"
        ),
        default=True,
    )
    size_axis: bpy.props.EnumProperty(
        name="按哪一轴",
        description="按某一轴的方块格数反算方块尺寸（倍率），而不是自己试数字",
        items=[
            ("Y", "高（Y）", "模型在游戏里高几格"),
            ("X", "长（X）", "模型在游戏里长几格（东西向）"),
            ("Z", "宽（Z）", "模型在游戏里宽几格（南北向）"),
        ],
        default="Y",
    )
    size_target: bpy.props.IntProperty(
        name="目标格数",
        description="填 0 表示不用。填了以后点「按长度定倍率」反算方块尺寸",
        default=0,
        min=0,
        soft_max=512,
    )
    auto_fit_height: bpy.props.BoolProperty(
        name="自动压到限高内",
        description=(
            "算出来会超出世界限高时，自动把方块尺寸放大到刚好塞得下，"
            "并写回面板。关掉就退回旧行为（严格限高直接报错）"
        ),
        default=True,
    )
    preview_path: bpy.props.StringProperty(
        name="预览图",
        description="等距预览图存哪。留空自动放在当前 blend 旁边（没存过就放临时目录）",
        subtype="FILE_PATH",
        default="",
    )
    preview_size: bpy.props.IntProperty(
        name="预览尺寸",
        description="预览图长边像素",
        default=1024,
        min=256,
        max=4096,
    )
    dimension: bpy.props.EnumProperty(
        name="维度",
        description=(
            "决定世界限高。数值从客户端 jar 的 dimension_type 读，读不到用内置兜底。"
            "超出限高的部分 Litematica 会静默跳过，所以导出前会拦"
        ),
        items=[
            ("overworld", "主世界", "y -64..319，384 格"),
            ("the_nether", "下界", "y 0..255，256 格（logical_height 128）"),
            ("the_end", "末地", "y 0..255，256 格"),
        ],
        default="overworld",
    )
    strict_height: bpy.props.BoolProperty(
        name="严格限高",
        description=(
            "超出限高直接中止导出。用改过世界高度的 mod / 数据包时关掉，"
            "关掉只告警不拦"
        ),
        default=True,
    )
    region_size: bpy.props.IntProperty(
        name="区域大小",
        description="超过这个尺寸就切 region，Litematica 按视锥加载",
        default=256,
        min=16,
        max=1024,
    )
    mc_data_version: bpy.props.IntProperty(
        name="DataVersion",
        description="从客户端 jar 的 version.json 读。留 0 表示自动读 jar",
        default=0,
        min=0,
    )
    jar_path: bpy.props.StringProperty(
        name="客户端 jar",
        description=".minecraft/versions/<版本>/<版本>.jar，用于读方块颜色和 DataVersion",
        subtype="FILE_PATH",
        default="",
    )
    output_path: bpy.props.StringProperty(
        name="输出",
        subtype="FILE_PATH",
        default="",
    )
    author: bpy.props.StringProperty(name="作者", default="")
    max_blocks: bpy.props.IntProperty(
        name="方块上限",
        description="超过就中止，防止内存爆掉",
        default=2_000_000,
        min=1000,
    )
    # ---- 导出结果回读（面板上看得见，不用翻状态栏） ----
    last_export_note: bpy.props.StringProperty(
        name="上次导出",
        description="最近一次成功导出的文件与统计，只读",
        default="",
    )
    last_export_dir: bpy.props.StringProperty(
        name="上次导出目录",
        description="用来打开所在文件夹，只读",
        default="",
    )
    # ---- 超上限时自动分块 ----
    auto_split: bpy.props.BoolProperty(
        name="超上限自动分块",
        description="模型太大时按最长轴切成几段分别导出，每段独立体素化，峰值内存只按单段算",
        default=True,
    )
    light_blocks: bpy.props.BoolProperty(
        name="允许照明方块",
        description="默认开启：允许用蛙鸣灯、菌光体、海晶灯这类「放下就亮」的方块。"
        "关掉的话这些方块不参与匹配，颜色相近的位置会换成不发光的方块",
        default=True,
    )
    max_kinds: bpy.props.IntProperty(
        name="方块种类上限",
        description="超过时把用得最少的方块并到颜色最接近的保留方块。"
        "Litematica 每种方块单独建模型缓存，种类越多加载越慢；0 = 不限",
        default=24,
        min=0,
        max=128,
    )
    noise: bpy.props.IntProperty(
        name="噪声",
        description="同一颜色区域里按这个比例混入次优方块，打散大面积墙面的单调感。0 = 永远选最接近的",
        default=0,
        min=0,
        max=100,
        subtype="PERCENTAGE",
    )


def register():
    bpy.utils.register_class(MCVoxelProps)
    bpy.types.Scene.mc_voxel = bpy.props.PointerProperty(type=MCVoxelProps)


def unregister():
    del bpy.types.Scene.mc_voxel
    bpy.utils.unregister_class(MCVoxelProps)
