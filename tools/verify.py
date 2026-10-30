"""不装 Blender 也能跑的验收脚本。

    python tools/verify.py

跑完会打印每项 PASS/FAIL，并在 dist/ 下留一份示例投影。
"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import build as core_build
from core import litematic as core_litematic
from core import mesh_export
from core.ir import BlockState, VoxelModel
from core.palette import BlockPalette, load_fallback_palette

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist")
ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  PASS  {name}{('  ' + detail) if detail else ''}")
    else:
        fail_count += 1
        print(f"  FAIL  {name}{('  ' + detail) if detail else ''}")


def box_mesh(lo=(0, 0, 0), hi=(10, 10, 10)):
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    verts = np.array(
        [
            [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
            [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
        ]
    )
    tris = np.array(
        [
            [0, 3, 2], [0, 2, 1], [4, 5, 6], [4, 6, 7],
            [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
            [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
        ]
    )
    return verts, tris


def uv_sphere(radius=8.0, segments=32, rings=16, center=(8, 8, 8)):
    """UV 球，用来当 demo 场景。"""
    cx, cy, cz = center
    verts = [(cx, cy, cz + radius)]
    for r in range(1, rings):
        phi = np.pi * r / rings
        for s in range(segments):
            theta = 2 * np.pi * s / segments
            verts.append(
                (
                    cx + radius * np.sin(phi) * np.cos(theta),
                    cy + radius * np.sin(phi) * np.sin(theta),
                    cz + radius * np.cos(phi),
                )
            )
    verts.append((cx, cy, cz - radius))
    verts = np.asarray(verts)

    tris = []
    for s in range(segments):
        tris.append((0, 1 + (s + 1) % segments, 1 + s))
    for r in range(rings - 2):
        base = 1 + r * segments
        nxt = base + segments
        for s in range(segments):
            a = base + s
            b = base + (s + 1) % segments
            c = nxt + s
            d = nxt + (s + 1) % segments
            tris.append((a, b, d))
            tris.append((a, d, c))
    last = len(verts) - 1
    base = 1 + (rings - 2) * segments
    for s in range(segments):
        tris.append((last, base + s, base + (s + 1) % segments))
    return verts, np.asarray(tris, dtype=np.int64)


def colored(verts, tris, rgb):
    return verts, tris, np.tile(np.asarray(rgb, dtype=float), (len(verts), 1))


def compare_models(a: VoxelModel, b: VoxelModel, label):
    if a.size != b.size:
        check(label, False, f"尺寸不一致 {a.size} vs {b.size}")
        return
    sa = [a.palette[int(i)].name for i in a.blocks]
    sb = [b.palette[int(i)].name for i in b.blocks]
    diff = sum(1 for x, y in zip(sa, sb) if x != y)
    check(label, diff == 0, f"逐块差异 {diff}")


def main():
    os.makedirs(OUT, exist_ok=True)
    pal = BlockPalette(load_fallback_palette())
    print("== 1. 体素化 ==")

    verts, tris = box_mesh()
    colors = np.tile(np.array([140.0, 140.0, 140.0]), (len(verts), 1))
    model = core_build.build_voxel_model(verts, tris, colors, 1.0, pal)
    check("10×10×10 立方体 -> Filled: 1000", model.total_blocks == 1000, f"实际 {model.total_blocks}")

    sphere_v, sphere_t = uv_sphere()
    sphere_c = np.tile(np.array([60.0, 120.0, 200.0]), (len(sphere_v), 1))
    t0 = time.perf_counter()
    sphere = core_build.build_voxel_model(sphere_v, sphere_t, sphere_c, 1.0, pal)
    dt = time.perf_counter() - t0
    check("球体体素化非空", sphere.total_blocks > 100, f"{sphere.total_blocks} 方块 / {dt*1000:.0f} ms")
    check("球体耗时 < 5s", dt < 5.0, f"{dt:.2f}s")

    print("== 2. 导出 ==")
    dv = 4325
    path_lm = os.path.join(OUT, "demo_sphere.litematic")
    core_litematic.write_litematic(sphere, path_lm, mc_data_version=dv, name="demo_sphere")
    check("写出 .litematic", os.path.exists(path_lm), f"{os.path.getsize(path_lm)} 字节")

    print("== 3. 反向读回一致性 ==")
    back_lm = core_litematic.read_litematic(path_lm)
    compare_models(sphere, back_lm, ".litematic 读回逐块一致")

    print("== 4. 网格反向导出 ==")
    cube = VoxelModel((4, 4, 4))
    cube.fill_from(np.ones((4, 4, 4), dtype=bool), BlockState("minecraft:stone"))
    v, q, _ = mesh_export.model_to_mesh(cube)
    check("方块模型导出网格", len(q) == 6 * 16, f"{len(q)} 个面")
    check("法线朝外", mesh_export.check_outward(v, q))

    print("== 5. 结构字段（对应 golden sample 检查点） ==")
    from core import nbt

    root = nbt.read_nbt(path_lm)
    check("MinecraftDataVersion 写入", int(root["MinecraftDataVersion"]) == dv)
    check("Version = 7 + SubVersion 1（对齐真实样本）", int(root["Version"]) == 7 and int(root["SubVersion"]) == 1)
    check("Metadata.RegionCount >= 1", int(root["Metadata"]["RegionCount"]) >= 1)
    check("Metadata.TotalBlocks 一致", int(root["Metadata"]["TotalBlocks"]) == sphere.total_blocks,
          f"{int(root['Metadata']['TotalBlocks'])} vs {sphere.total_blocks}")
    region = root["Regions"]["Region0"]
    check("palette[0] 是 air", str(region["BlockStatePalette"][0]["Name"]) == "minecraft:air")

    print("== 6. 世界限高 ==")
    from core import limits, mcver

    jar = os.environ.get("MC_JAR") or mcver.find_client_jar()
    if not jar or not os.path.exists(jar):
        check(
            "没有客户端 jar，退回内置兜底（主世界 384）",
            limits.get_dimension("overworld").height == 384,
        )
    else:
        dims = limits.read_dimensions(jar)
        ow = dims.get("overworld")
        check("从 jar 的 dimension_type 读限高", bool(dims), f"{len(dims)} 个维度")
        check(
            "主世界 384 格（y -64..319）",
            bool(ow) and ow.height == 384 and ow.min_y == -64,
            str(ow),
        )
    check("55 格：放得下", limits.check_height(55).level == "ok")
    check("384 格整：只告警（要从地下起建）", limits.check_height(384).level == "warn")
    tall = limits.check_height(385)
    check("385 格：拦下", tall.level == "error", tall.message[:40] + "…")
    tall_model = VoxelModel((4, 400, 4))
    tall_model.set(0, 399, 0, BlockState("minecraft:stone"))
    check("check_model 按 y 跨度判", limits.check_model(tall_model).span == 400)
    check("下界限高 256", limits.check_height(257, "nether").level == "error")

    print(f"\n结果：{ok_count} 通过 / {fail_count} 失败")
    print(f"产物：{OUT}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
