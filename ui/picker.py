"""白名单 / 禁用方块的输入辅助。

侧栏那一列很窄，StringProperty 的输入框被挤得只剩十几个像素，填进去的
方块 id 全是省略号。这里的几个操作符就是补这个短板：

- `mc_voxel.pick_block`   搜索弹窗，从当前色板里点选方块，自动追加到框里
- `mc_voxel.clear_filter` 清空某个框
- `mc_voxel.list_filter`  把某个框解析后的完整结果列出来（弹窗，宽度自己定）

搜索的候选来自真实 jar 的资源包解析结果（和体素化用的是同一份色板），
没配 jar 就用兜底的 60 色，绝不自己编方块名。
"""

from __future__ import annotations

import os

import bpy

from ..core.palette import (
    drop_filter,
    filter_tokens,
    load_fallback_palette,
    merge_filter,
    parse_block_filter,
)

# jar 路径 / mtime / 大小 -> 排序过的方块名列表。面板每帧都会调，不能每帧读 jar。
_NAME_CACHE = {}
_FALLBACK_NAMES = None

_TARGETS = (
    ("ALLOW", "只用这些方块", "白名单，填了就只在这些方块里选色"),
    ("DENY", "禁用方块", "被禁的方块换近色替代，不留洞"),
)
_TARGET_LABEL = {key: label for key, label, _ in _TARGETS}


def target_attr(target: str) -> str:
    """ALLOW/DENY -> 属性名。"""
    return "disable_blocks" if target == "DENY" else "allow_blocks"


def _props(context=None):
    context = context or bpy.context
    scene = getattr(context, "scene", None) or bpy.context.scene
    return scene.mc_voxel


def _cache_file() -> str:
    """和体素化用同一份磁盘缓存，别为了一份颜色解析两遍 jar。"""
    from .paths import cache_file

    return cache_file()


def _fallback_names() -> list:
    global _FALLBACK_NAMES
    if _FALLBACK_NAMES is None:
        try:
            _FALLBACK_NAMES = tuple(sorted(load_fallback_palette()))
        except Exception as exc:
            print(f"[mc_voxel] 读兜底色板失败：{exc}")
            _FALLBACK_NAMES = ()
    return list(_FALLBACK_NAMES)


def palette_names(props) -> list:
    """当前色板里所有可用方块名（排序后）。读不到 jar 时退回兜底色板。"""
    jar = ""
    try:
        jar = bpy.path.abspath(props.jar_path) if props.jar_path else ""
    except Exception:
        jar = ""

    key = None
    if jar and os.path.exists(jar):
        try:
            key = (os.path.abspath(jar), os.path.getmtime(jar), os.path.getsize(jar))
        except OSError:
            key = None
    if key is not None and key in _NAME_CACHE:
        return _NAME_CACHE[key]

    colors = None
    if key is not None:
        try:
            from ..core.pack import load_cached

            colors = load_cached(jar, _cache_file())
        except Exception as exc:
            print(f"[mc_voxel] 取方块列表失败，退回兜底：{exc}")

    if colors:
        names = sorted(colors)
        if len(_NAME_CACHE) > 4:  # 换了好几个 jar 才需要清，正常就一条
            _NAME_CACHE.clear()
        _NAME_CACHE[key] = names
        return names
    return _fallback_names()


def short_name(name: str) -> str:
    """`minecraft:oak_planks` -> `oak_planks`，面板里省地方。"""
    return name.split(":", 1)[-1]


def resolve(props, text: str):
    """把框里的文本解析成 (命中, 缺失, 原始 token 数)。

    命中/缺失都按色板里的真实名字算，用户写 `*wool*` 这种通配也能展开成具体方块。
    """
    text = text or ""
    tokens = filter_tokens(text)
    if not tokens:
        return [], [], 0
    try:
        names = palette_names(props)
    except Exception:
        names = []
    known = set(names) if names else None
    wanted = parse_block_filter(text, known=names or None)
    if known is None:  # 拿不到色板就别乱判缺失
        return wanted, [], len(tokens)
    hit = [n for n in wanted if n in known]
    return hit, [n for n in wanted if n not in known], len(tokens)


def _search_items(context, edit_text: str, limit: int = 80) -> list:
    """搜索弹窗的候选：短名匹配，返回带 `minecraft:` 的原名。"""
    try:
        names = palette_names(_props(context))
    except Exception:
        names = []
    key = (edit_text or "").strip().lower()
    if key.startswith("minecraft:"):
        key = key.split(":", 1)[1]
    if not key:
        return names[:limit]
    return [n for n in names if key in n.lower()][:limit]


def _search_query(self, context, edit_text):
    return _search_items(context, edit_text)


def _canonical(props, name: str) -> str:
    """短名补上 minecraft: 前缀，和色板对齐。

    通配（`*wool*`）和已经写了命名空间的原样放行——那种要留给匹配阶段展开。
    """
    text = (name or "").strip()
    if not text or ":" in text or "*" in text or "?" in text:
        return text
    try:
        names = set(palette_names(props))
    except Exception:
        names = set()
    full = f"minecraft:{text}"
    return full if full in names else text


def _add(props, target: str, name: str) -> str:
    attr = target_attr(target)
    merged = merge_filter(getattr(props, attr, "") or "", _canonical(props, name))
    setattr(props, attr, merged)
    return merged


class MCVOXEL_OT_pick_block(bpy.types.Operator):
    """搜索方块名并追加到白名单 / 禁用（省得记 id）"""

    bl_idname = "mc_voxel.pick_block"
    bl_label = "选方块"
    bl_property = "query"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(name="加到哪", items=_TARGETS, default="ALLOW")
    query: bpy.props.StringProperty(
        name="方块",
        description="输入名字片段搜索，如 wool / concrete / stone",
        search=_search_query,
    )

    def invoke(self, context, event):
        # 搜索弹窗在浮窗面板里也可能被拒，拒了就退化成"用输入框里已经填的内容"
        try:
            context.window_manager.invoke_search_popup(self)
            return {"RUNNING_MODAL"}
        except Exception as exc:
            print(f"[mc_voxel] 搜索弹窗打不开，改用输入框内容：{exc}")
            return self.execute(context)

    def execute(self, context):
        name = (self.query or "").strip()
        if not name:
            self.report({"WARNING"}, "没选方块")
            return {"CANCELLED"}
        props = _props(context)
        _add(props, self.target, name)
        self.report({"INFO"}, f"{_TARGET_LABEL[self.target]}已加入 {short_name(name)}")
        return {"FINISHED"}


class MCVOXEL_OT_clear_filter(bpy.types.Operator):
    """清空白名单 / 禁用框"""

    bl_idname = "mc_voxel.clear_filter"
    bl_label = "清空过滤"
    bl_options = {"REGISTER", "UNDO"}

    target: bpy.props.EnumProperty(name="清哪个", items=_TARGETS, default="ALLOW")

    def execute(self, context):
        props = _props(context)
        setattr(props, target_attr(self.target), "")
        self.report({"INFO"}, "已清空")
        return {"FINISHED"}


class MCVOXEL_OT_list_filter(bpy.types.Operator):
    """列出某个框解析后的完整结果（弹窗比侧栏宽，能看全）"""

    bl_idname = "mc_voxel.list_filter"
    bl_label = "查看生效的方块"
    bl_options = {"REGISTER"}

    target: bpy.props.EnumProperty(name="看哪个", items=_TARGETS, default="ALLOW")

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=460)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        props = _props(context)
        attr = target_attr(self.target)
        text = getattr(props, attr, "") or ""
        hit, missing, ntok = resolve(props, text)

        layout.label(text=_TARGET_LABEL[self.target], icon="FILTER")
        if not text.strip():
            layout.label(text="空着 = 不限制，当前用全部方块", icon="INFO")
            return
        layout.label(text=f"写了 {ntok} 项，展开成 {len(hit)} 种方块")

        col = layout.column(align=True)
        if hit:
            for name in hit[:120]:
                col.label(text=short_name(name), icon="BLOCK")
            if len(hit) > 120:
                col.label(text=f"…还有 {len(hit) - 120} 种没列（够用了，先干活）", icon="INFO")
        else:
            col.label(text="一种都没匹配上，检查拼写", icon="ERROR")

        if missing:
            box = layout.box()
            box.label(text=f"{len(missing)} 个不在色板里：", icon="ERROR")
            for name in missing[:20]:
                box.label(text=short_name(name))


_CLASSES = (
    MCVOXEL_OT_pick_block,
    MCVOXEL_OT_clear_filter,
    MCVOXEL_OT_list_filter,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _NAME_CACHE.clear()
