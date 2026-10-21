"""工具脚本共用的路径解析。

**别在这里写死本机路径**——仓库要发出去，别人的 `.minecraft` 不在 D 盘。

优先级：
  `MC_JAR` / `MC_SCHEMATICS` 环境变量  >  自动探测（从客户端 jar 推导）

自动探测用 `core.mcver.find_client_jar()`，它会扫官方 / PCL / HMCL / BakaXL
的 `.minecraft/versions/<版本>/<版本>.jar`，取修改时间最新的那个。
投影目录按启动器惯例取 jar 同级的 `schematics/`。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
EXAMPLES = os.path.join(ROOT, "examples")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def client_jar() -> str:
    """客户端 jar 路径。找不到返回空串。"""
    env = os.environ.get("MC_JAR")
    if env:
        return env if os.path.exists(env) else ""
    from core.mcver import find_client_jar

    return find_client_jar() or ""


def schematics_dir() -> str:
    """游戏读投影的目录。

    没配 `MC_SCHEMATICS` 也没找到客户端时，退回仓库里的 `dist/schematics/`，
    这样脚本在哪都能跑，只是不会自动进游戏。
    """
    env = os.environ.get("MC_SCHEMATICS")
    if env:
        return env
    jar = client_jar()
    if jar:
        return os.path.join(os.path.dirname(jar), "schematics")
    return os.path.join(DIST, "schematics")


def palette_cache() -> str:
    """色板缓存文件（和扩展在 Blender 里用的是同一份格式）。"""
    return os.path.join(DIST, "block_colors.json")
