"""重力方块的下落检查。

沙子、沙砾、混凝土粉末、铁砧这些东西，下方没有实心方块时会掉下来。
投影是施工蓝图：Litematica 一块一块摆过去，悬空的沙子刚放就塌，
最后地上多一堆沙子、图纸上留个洞。

所以规则是——**底下没有方块托着的位置，不许用重力方块**，
换成色差最小的非重力方块。有支撑的地方照常用沙子，不搞一刀切。
"""

from __future__ import annotations

import numpy as np

from .ir import BlockState

# 会掉落的整方块。名单照 Minecraft 的重力方块集合，
# 只留会出现在色板里的（栅栏、门那些本来就进不了色板）。
GRAVITY_BLOCKS = {
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:suspicious_sand",
    "minecraft:gravel",
    "minecraft:suspicious_gravel",
    "minecraft:dragon_egg",
    "minecraft:anvil",
    "minecraft:chipped_anvil",
    "minecraft:damaged_anvil",
}

# 后缀匹配：16 种混凝土粉末
GRAVITY_SUFFIXES = ("_concrete_powder",)


def is_gravity_block(name: str) -> bool:
    if name in GRAVITY_BLOCKS:
        return True
    return name.endswith(GRAVITY_SUFFIXES)


def enforce_gravity_support(model, palette, report=None) -> int:
    """把悬空的重力方块换成近似色的非重力方块，返回替换数量。

    model   : VoxelModel（原地修改）
    palette : 生成这个 model 用的 BlockPalette（用来查原始颜色）

    判定"悬空"要往下追到底：沙托沙不算托。整根悬空的沙柱会被全部换掉，
    因为 Litematica 施工不保证严格从下往上摆，先放上面那格照样塌。
    模型最底层（y == 0）算有支撑——投影底面正常是贴地的。

    非重力方块不受影响：悬挑、桥、拱这些靠自身强度的结构照常留着。
    """
    sx, sy, sz = model.size
    if sy == 0 or not model.palette:
        return 0

    names = [st.name for st in model.palette]
    is_grav = np.array([is_gravity_block(n) for n in names], dtype=bool)
    if not is_grav.any():
        return 0

    arr = model.blocks.reshape(sy, sz, sx)  # [y][z][x]
    grav_mask = is_grav[arr]  # 索引 0 是 air，不会被算成重力方块

    # 自下而上传播"不稳"状态：下方是空气，或者下方是不稳的重力方块，
    # 这一格就托不住。y=0 贴地，永远算稳。
    unstable = np.zeros_like(grav_mask)
    for y in range(1, sy):
        unstable[y] = grav_mask[y] & ((arr[y - 1] == 0) | unstable[y - 1])

    need_fix = unstable
    count = int(need_fix.sum())
    if count == 0:
        return 0

    # 替代色板：原色板去掉所有重力方块
    spare = palette.without([n for n, g in zip(names, is_grav) if g])

    state_index = {n: i for i, n in enumerate(names)}

    def index_of(name: str) -> int:
        if name not in state_index:
            idx = model.add_state(BlockState(name))
            state_index[name] = idx
        return state_index[name]

    def replacement_for(name: str):
        """给一个重力方块挑替身：优先同色，其次同族（粉末 -> 混凝土）。"""
        rgb = palette.color_of(name)
        if rgb is not None:
            return spare.nearest(np.array(rgb, dtype=np.float64))
        # 色板里没有这个方块（比如导入的模型带了色板外的粉末），
        # 就按名字找它的"凝固版本"，颜色几乎一致
        if name.endswith("_concrete_powder"):
            solid = name[: -len("_concrete_powder")] + "_concrete"
            if solid in spare.names:
                return solid
        return spare.names[0] if spare.names else name

    fixed = 0
    old_names = set()
    used_names = set()
    for gidx in np.unique(arr[need_fix]):
        name = names[int(gidx)]
        new_name = replacement_for(name)
        if new_name == name:
            continue
        old_names.add(name)
        used_names.add(new_name)
        idx = index_of(new_name)
        hit = need_fix & (arr == int(gidx))
        fixed += int(hit.sum())
        arr[hit] = idx

    if report:
        report(fixed, sorted(old_names), sorted(used_names))
    return fixed
