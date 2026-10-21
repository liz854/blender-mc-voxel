"""新旧体素化实现对拍：结果必须逐格一致。

    python tools/voxelize_compare.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from core.voxelize import voxelize_surface as new_impl  # noqa: E402

_legacy_path = os.path.join(os.path.dirname(ROOT), "voxelize_legacy.py")
_spec = importlib.util.spec_from_file_location("voxelize_legacy", _legacy_path)
_legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_legacy)
old_impl = _legacy.voxelize_surface


def cube(lo, hi):
    v = np.array(
        [
            [lo[0], lo[1], lo[2]],
            [hi[0], lo[1], lo[2]],
            [hi[0], hi[1], lo[2]],
            [lo[0], hi[1], lo[2]],
            [lo[0], lo[1], hi[2]],
            [hi[0], lo[1], hi[2]],
            [hi[0], hi[1], hi[2]],
            [lo[0], hi[1], hi[2]],
        ],
        dtype=np.float64,
    )
    t = np.array(
        [
            [0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6],
            [0, 4, 5], [0, 5, 1], [1, 5, 6], [1, 6, 2],
            [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0],
        ],
        dtype=np.int64,
    )
    return v, t


def sphere(radius, seg=48, rings=24):
    verts = []
    for i in range(rings + 1):
        theta = np.pi * i / rings
        for j in range(seg + 1):
            phi = 2 * np.pi * j / seg
            verts.append(
                (
                    radius * np.sin(theta) * np.cos(phi),
                    radius * np.cos(theta),
                    radius * np.sin(theta) * np.sin(phi),
                )
            )
    tris = []
    for i in range(rings):
        for j in range(seg):
            a = i * (seg + 1) + j
            tris += [(a, a + 1, a + seg + 1), (a + 1, a + seg + 2, a + seg + 1)]
    return np.asarray(verts), np.asarray(tris, dtype=np.int64)


def run(label, verts, tris, size, block, with_color=True):
    colors = np.random.default_rng(7).integers(0, 256, (len(verts), 3)).astype(np.float64)
    t0 = time.perf_counter()
    m_old, c_old = old_impl(verts, tris, size, block_size=block, colors=colors)
    t_old = time.perf_counter() - t0
    t0 = time.perf_counter()
    m_new, c_new = new_impl(verts, tris, size, block_size=block, colors=colors)
    t_new = time.perf_counter() - t0
    same_mask = np.array_equal(m_old, m_new)
    nan_old = np.isnan(c_old)
    nan_new = np.isnan(c_new)
    same_nan = np.array_equal(nan_old, nan_new)
    diff = np.nanmax(np.abs(c_old - c_new)) if not nan_old.all() else 0.0
    ok = same_mask and same_nan and diff < 1e-6
    print(
        f"[{'OK' if ok else 'XX'}] {label:<28} 体素 {int(m_new.sum()):>8}  "
        f"旧 {t_old:6.2f}s  新 {t_new:6.2f}s  色差 {diff:.2e}"
        f"{'' if same_mask else '  mask 不一致'}{'' if same_nan else '  NaN 位置不一致'}"
    )
    return ok


def main():
    ok = True
    v, t = cube((0, 0, 0), (10, 10, 10))
    ok &= run("立方体 10³", v, t, (12, 12, 12), 1.0)

    v, t = cube((1, 2, 3), (6, 4, 6))
    ok &= run("非立方面包盒 5x2x3", v, t, (7, 4, 4), 1.0)

    v, t = cube((0, 0, 0), (10, 0.2, 10))
    ok &= run("薄板 plane", v, t, (11, 2, 11), 1.0)

    v, t = sphere(20)
    ok &= run("球 r=20", v, t, (42, 42, 42), 1.0)

    v, t = sphere(3)
    ok &= run("小球 r=3（细分）", v, t, (7, 7, 7), 1.0)

    v, t = cube((0, 0, 0), (4, 4, 4))
    ok &= run("小方块 0.1 精度", v, t, (41, 41, 41), 0.1)

    print("\n对比结果：", "全部一致" if ok else "有不一致，别用新实现")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
