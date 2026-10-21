"""性能实测：对上方案 Sprint 4 的验收线。

    python tools/bench.py            # 100³ / 200³ / 512³ 体素化 + 百万方块导出
    python tools/bench.py 100        # 只跑指定边长

验收线（方案 Sprint 4）：
  - 100³ 体素化 < 5 秒
  - 512³ 不崩，峰值内存 < 2 GB
  - 100 万方块导出 < 10 秒
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from core.build import build_voxel_model  # noqa: E402
from core.ir import BlockState, VoxelModel  # noqa: E402
from core.litematic import write_litematic  # noqa: E402
from core.palette import BlockPalette, load_fallback_palette  # noqa: E402


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_mb() -> float:
    """当前进程峰值物理内存（MB）。Windows 限定，别的平台返回 0。"""
    if os.name != "nt":
        return 0.0
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
        psapi.GetProcessMemoryInfo.restype = wt.BOOL
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        info = _PMC()
        info.cb = ctypes.sizeof(_PMC)
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
            return 0.0
        return info.PeakWorkingSetSize / 2 ** 20
    except Exception:
        return 0.0


def uv_sphere(radius: float, segments: int = 64, rings: int = 32):
    """UV 球：2048 个四边形 → 4096 个三角形，接近真实模型的量级。"""
    verts = []
    for i in range(rings + 1):
        theta = math.pi * i / rings
        for j in range(segments + 1):
            phi = 2.0 * math.pi * j / segments
            verts.append(
                (
                    radius * math.sin(theta) * math.cos(phi),
                    radius * math.cos(theta),
                    radius * math.sin(theta) * math.sin(phi),
                )
            )
    tris = []
    for i in range(rings):
        for j in range(segments):
            a = i * (segments + 1) + j
            b = a + 1
            c = a + segments + 1
            d = c + 1
            tris.append((a, b, c))
            tris.append((b, d, c))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def bench_voxelize(edge: int, palette) -> None:
    verts, tris = uv_sphere(radius=edge / 2.0 - 1.0)
    colors = np.full((len(verts), 3), 180.0)
    t0 = time.perf_counter()
    model = build_voxel_model(
        verts, tris, colors, block_size=1.0, palette=palette, solid=True
    )
    dt = time.perf_counter() - t0
    print(
        f"{edge}³  {dt:6.2f}s  尺寸 {model.size}  方块 {model.total_blocks:>9,}  "
        f"峰值内存 {peak_mb():7.0f} MB  三角形 {len(tris):,}"
    )


def bench_export(palette) -> None:
    """100 万方块导出：100×100×100 实心。"""
    side = 100
    model = VoxelModel((side, side, side))
    idx = model.add_state(BlockState("minecraft:stone"))
    model.blocks[:] = idx
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist", "bench_1m.litematic")
    t0 = time.perf_counter()
    write_litematic(model, out, mc_data_version=5023, name="bench_1m")
    dt = time.perf_counter() - t0
    size = os.path.getsize(out) / 2 ** 20
    print(f"导出 100 万方块  {dt:6.2f}s  {size:.1f} MB  {out}")
    os.remove(out)


def main() -> int:
    edges = [int(a) for a in sys.argv[1:]] or [100, 200, 512]
    palette = BlockPalette(load_fallback_palette())
    print(f"色板 {len(palette.names)} 种，起始峰值内存 {peak_mb():.0f} MB")
    for edge in edges:
        try:
            bench_voxelize(edge, palette)
        except MemoryError:
            print(f"{edge}³  MemoryError —— 内存不够，这个尺度要调大方块尺寸")
    bench_export(palette)
    print(f"结束峰值内存 {peak_mb():.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
