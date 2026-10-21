"""防跑偏清单第 1、2 条：检查依赖边界。

1. 只用 bpy / bmesh / numpy / 标准库。禁止 scipy、PIL、trimesh、nbtlib、amulet-nbt、requests。
2. core/ 下除 mesh_data.py 外，一律不许 import bpy。
"""

import ast
import pathlib

FORBIDDEN = {"scipy", "PIL", "Pillow", "trimesh", "nbtlib", "amulet", "requests", "cv2", "sklearn"}

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / "core"


def _imports(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module.split(".")[0]


def test_no_forbidden_third_party():
    for path in list(CORE.glob("*.py")) + list((ROOT / "ui").glob("*.py")):
        for mod in _imports(path):
            assert mod not in FORBIDDEN, f"{path.name} 引入了禁用依赖 {mod}"


def test_core_has_no_bpy_except_mesh_data():
    for path in CORE.glob("*.py"):
        if path.name == "mesh_data.py":
            continue
        mods = set(_imports(path))
        assert "bpy" not in mods, f"{path.name} 不该 import bpy"
        assert "bmesh" not in mods, f"{path.name} 不该 import bmesh"


def test_coordinate_conversion_only_in_coords():
    """所有坐标转换必须走 core/coords.py：别处不许出现 -bl_y 这类手写公式。"""
    for path in CORE.glob("*.py"):
        if path.name in ("coords.py",):
            continue
        text = path.read_text(encoding="utf-8")
        assert "-v[:, 1]" not in text, f"{path.name} 里手写了坐标转换"
