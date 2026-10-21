"""打包成可安装的 Blender 扩展 zip。

    python tools/pack.py

产物：dist/mc_voxel_export.zip（manifest 在 zip 根，可直接 Install from Disk）
"""

from __future__ import annotations

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "dist")
OUT_ZIP = os.path.join(OUT_DIR, "mc_voxel_export.zip")

INCLUDE_DIRS = ("core", "ui", "data")
INCLUDE_FILES = ("__init__.py", "blender_manifest.toml")
SKIP_EXT = (".pyc", ".pyo")


def collect():
    items = []
    for name in INCLUDE_FILES:
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            items.append((path, name))
    for d in INCLUDE_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x != "__pycache__"]
            for fn in filenames:
                if fn.endswith(SKIP_EXT):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace("\\", "/")
                items.append((full, rel))
    return items


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    items = collect()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in items:
            z.write(full, rel)
    size = os.path.getsize(OUT_ZIP)
    print(f"打包完成：{OUT_ZIP}")
    print(f"{len(items)} 个文件，{size/1024:.1f} KB")
    with zipfile.ZipFile(OUT_ZIP) as z:
        names = z.namelist()
    assert "blender_manifest.toml" in names, "manifest 必须在 zip 根目录"
    assert "__init__.py" in names, "__init__.py 必须在 zip 根目录"
    print("校验：manifest 与 __init__.py 都在根目录 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
