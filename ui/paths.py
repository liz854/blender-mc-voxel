"""扩展的用户数据目录（放色板缓存）。

踩过的坑：`bpy.utils.extension_path_user("bl_ext")` 会抛
`ValueError: The "package" does not name an extension` —— 它要的是**完整包名**，
不是 `__package__.split(".")[0]` 那一段。之前传错了，异常又被 `except` 吞掉，
结果真实 jar 的资源包解析永远失败、静默退回兜底 96 色，颜色一直不对还看不出来。
"""

from __future__ import annotations

import os
import tempfile

import bpy

# ui/paths.py 的 __package__ 是 bl_ext.user_default.mc_voxel_export.ui
_PKG = (__package__ or "").rsplit(".", 1)[0]


def user_dir(create: bool = True) -> str:
    """扩展自己的用户目录；取不到就退临时目录（缓存失效而已，不致命）。"""
    if _PKG:
        try:
            return bpy.utils.extension_path_user(_PKG, create=create)
        except Exception as exc:
            print(f"[mc_voxel] 取扩展用户目录失败（{exc}），改用临时目录")
    return tempfile.gettempdir()


def cache_file(name: str = "block_colors.json") -> str:
    return os.path.join(user_dir(), name)
