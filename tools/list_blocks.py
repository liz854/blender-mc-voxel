"""列出当前色板里的全部可用方块，输出 Markdown 供人工核对。

用途：筛选规则改过之后逐项检查「哪些进了、哪些被挡了」。
数据直接从客户端 jar 解析，看到的就是扩展实际会用的那一份。

用法：
    python tools/list_blocks.py                    # 输出 dist/available_blocks.md
    python tools/list_blocks.py out.md             # 指定输出路径
    MC_JAR=<jar> python tools/list_blocks.py       # 指定 jar

正文按方块 ID 的字母序排成一张表——想找某个方块，按名字定位最快。
后面附「已排除清单」，把黑名单和判定规则一起列出来，方便对照。
"""

import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pack import (  # noqa: E402
    LIGHT_BLOCKS,
    NON_BUILDING_BLOCKS,
    NON_BUILDING_PREFIXES,
    NON_BUILDING_SUFFIXES,
    _changes_on_place,
    load_cached_stats,
)


DEFAULT_JAR = r"D:\PCL\.minecraft\versions\26.3-Fabric 0.20.1\26.3-Fabric 0.20.1.jar"

COLORS = [
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red",
    "black",
]
COLOR_ZH = {
    "white": "白", "orange": "橙", "magenta": "品红", "light_blue": "淡蓝",
    "yellow": "黄", "lime": "黄绿", "pink": "粉红", "gray": "灰",
    "light_gray": "淡灰", "cyan": "青", "purple": "紫", "blue": "蓝",
    "brown": "棕", "green": "绿", "red": "红", "black": "黑",
}


def english_names(jar_path):
    """从 jar 的 en_us.json 读方块显示名。"""
    try:
        with zipfile.ZipFile(jar_path) as z:
            table = json.loads(z.read("assets/minecraft/lang/en_us.json").decode("utf-8"))
    except Exception:
        return {}
    prefix = "block.minecraft."
    return {k[len(prefix) :]: v for k, v in table.items() if k.startswith(prefix)}


def family(short):
    """粗分类，只用来在汇总里报个数。"""
    for c in COLORS:
        if short.startswith(c + "_"):
            short = short[len(c) + 1 :]
            break
    if short in ("wool",):
        return "羊毛"
    if short in ("concrete",):
        return "混凝土"
    if short in ("concrete_powder",):
        return "混凝土粉末"
    if short in ("terracotta",):
        return "陶瓦"
    if short in ("glazed_terracotta",):
        return "釉面陶瓦"
    if short in ("stained_glass", "glass"):
        return "玻璃"
    if short in ("carpet",):
        return "地毯"
    if short.endswith("_leaves") or short == "leaves":
        return "树叶"
    if short.endswith(("_log", "_wood", "_stem", "_hyphae")):
        return "原木"
    if short.endswith(("_planks", "_mosaic")):
        return "木板"
    if short.endswith("_ore"):
        return "矿石"
    if short.endswith("_block") and short.count("_") >= 1:
        return "材料/矿物块"
    return "其它"


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        root, "dist", "available_blocks.md"
    )
    jar = os.environ.get("MC_JAR") or DEFAULT_JAR

    colors, _stds = load_cached_stats(
        jar, os.path.join(os.path.dirname(out_path), "_list_cache.json")
    )
    names = english_names(jar)

    items = []
    for full in colors:
        short = full.split(":", 1)[1]
        rgb = colors[full]
        items.append((short, names.get(short, ""), rgb, family(short)))
    items.sort(key=lambda t: t[0])

    fam_count = {}
    for _s, _n, _c, fam in items:
        fam_count[fam] = fam_count.get(fam, 0) + 1

    L = []
    L.append("# 可用方块清单")
    L.append("")
    L.append(f"来源 `{os.path.basename(jar)}`，共 **{len(items)} 种**整方块。")
    L.append("按方块 ID 字母序排列，方便按名字找。色值是 jar 纹理解析出的平均色。")
    L.append("")
    L.append("## 汇总")
    L.append("")
    L.append("| 类别 | 数量 |")
    L.append("|---|---|")
    for fam, cnt in sorted(fam_count.items(), key=lambda kv: -kv[1]):
        L.append(f"| {fam} | {cnt} |")
    L.append(f"| **合计** | **{len(items)}** |")
    L.append("")

    L.append("## 全部可用方块")
    L.append("")
    L.append("| # | 方块 ID | 英文名 | 平均色 | 类别 |")
    L.append("|---|---|---|---|---|")
    for i, (short, label, rgb, fam) in enumerate(items, 1):
        L.append(
            f"| {i} | `{short}` | {label} | {rgb[0]},{rgb[1]},{rgb[2]} | {fam} |"
        )
    L.append("")

    # ---- 已排除 ----
    L.append("## 已排除清单")
    L.append("")
    L.append("### 1. 物理上放不进墙里")
    L.append("")
    L.append("| 方块 | 原因 |")
    L.append("|---|---|")
    L.append("| `cactus` 仙人掌 | 要求四面都不能挨着其他方块，埋进墙里游戏直接拒绝放置 |")
    L.append("")
    L.append("### 2. 放下去会变成另一种方块")
    L.append("")
    L.append("| 方块 | 原因 |")
    L.append("|---|---|")
    L.append("| `*_coral_block` / `*_coral` / `*_coral_fan` 活珊瑚 | 离开水立刻变死珊瑚，整块从粉/紫/黄/青变成灰绿 |")
    L.append("| `dead_*_coral*` 死珊瑚 | **没有排除**，本来就是死的，放下去不会再变 |")
    L.append("")
    L.append("### 3. 点名排除（会刷怪 / 执行命令 / 拆不掉 / 开界面）")
    L.append("")
    L.append("| 方块 ID | 英文名 |")
    L.append("|---|---|")
    for full in sorted(NON_BUILDING_BLOCKS):
        short = full.split(":", 1)[1]
        L.append(f"| `{short}` | {names.get(short, '')} |")
    L.append("")
    if NON_BUILDING_SUFFIXES or NON_BUILDING_PREFIXES:
        L.append("### 4. 按后缀/前缀排除")
        L.append("")
        L.append(f"- 后缀：`{', '.join(NON_BUILDING_SUFFIXES)}`")
        L.append(f"- 前缀：`{', '.join(NON_BUILDING_PREFIXES)}`")
        L.append("")
    else:
        L.append("### 4. 按后缀/前缀排除")
        L.append("")
        L.append("**清单为空**（树叶、混凝土粉末、釉面陶瓦、虫蚀方块都已放开）。")
        L.append("")
    L.append("### 5. 模型不是整方块")
    L.append("")
    L.append(
        "模型里没有「盖满 0..16 的方盒元素」的方块全部排除——台阶、楼梯、门、"
        "活板门、栅栏、墙、按钮、压力板、告示牌、铁轨、花、草、火把、床、"
        "箱子、铁砧、讲台、切石机、炼药锅这类。它们放下去不是一格完整方块，"
        "选它等于选错形状。**这一档有几百个，不逐个列出。**"
    )
    L.append("")
    L.append("### 6. 照明方块（默认允许，可关）")
    L.append("")
    L.append("这些不是排除，只是面板「允许照明方块」关掉时不参与匹配：")
    L.append("")
    for full in sorted(LIGHT_BLOCKS):
        short = full.split(":", 1)[1]
        mark = "✓ 在色板" if full in colors else "— 不在色板"
        L.append(f"- `{short}` {names.get(short, '')} — {mark}")
    L.append("")
    L.append("> 红石灯、铜灯泡系列**不算照明方块**——它们要红石信号才亮，不充能就是暗的。")
    L.append("")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))

    cache = os.path.join(os.path.dirname(out_path), "_list_cache.json")
    if os.path.exists(cache):
        os.remove(cache)

    print(f"已写出 {out_path}")
    print(f"可用 {len(items)} 种；排除名单 {len(NON_BUILDING_BLOCKS)} 项")
    print("分类统计：" + "、".join(f"{k} {v}" for k, v in
                              sorted(fam_count.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
