# Blender MC Voxel

**把 Blender 模型一键体素化成 Minecraft 建筑。** 主力导出 `.litematic`（Litematica 投影），顺带支持 WorldEdit 的 `.schem` 和结构方块 `.nbt`。配色不是拍脑袋的色表，而是直接读你客户端 jar 里的真实方块平均色。

![Blender 里的小房子体素化后的等距预览](docs/images/house.png)

> 状态：在 **Blender 5.2.2 LTS** 实机验收通过（57 项），`pytest` **207 项**全绿，导出结果在 **MC 26.3 + Fabric + Litematica 0.29.1** 游戏内 Load 成功。

## 功能一览

| 能力 | 说明 |
|---|---|
| **体素化** | 三角形-体素 16 轴 SAT，斜面和尖角不糊；按覆盖尺寸分组批量算，4000+ 三角形的模型秒级出结果 |
| **三种导出格式** | `.litematic`（Litematica，主力）/ `.schem`（WorldEdit、FAWE）/ `.nbt`（结构方块，≤48³） |
| **真实方块配色** | 直接读客户端 jar：1288 个 blockstate → 筛出 **300 种**能当建材的整方块，按 CIE Lab ΔE76 最近邻匹配 |
| **纹理感知匹配** | 平涂的面配平纹理方块、有噪点的面配有噪点的方块，光面上不会突然冒出矿石斑点 |
| **尺寸控制** | 直接给方块尺寸（1 体素 = N 米），或反过来「我要它高 200 格」自动反算倍率 |
| **世界限高** | 主世界 / 下界 / 末地的高度上限从 jar 的 `dimension_type` 读，超限自动压倍率或拦下并给出改法 |
| **重力方块保护** | 悬空的沙子 / 沙砾 / 混凝土粉末自动换成近似色，投影摆出去不会塌成一地 |
| **白名单 / 禁用** | 只用某几种方块，或剔掉不想要的（支持 `*` 通配、面板内搜索添加，不用背 id） |
| **等距预览图** | 不出 Blender 就能看游戏里大概什么样：正交相机 + 真实方块色 + 接触阴影 |
| **反向导入** | `.litematic` / `.schem` 读回成 Blender 网格，可选平滑着色或 Remesh 磨圆 |
| **可脱离 Blender** | `core/` 除 `mesh_data.py` 外不 import bpy，整套测试和导出都能在纯 Python 里跑 |

## 安装

### 需要什么

| 项 | 要求 |
|---|---|
| Blender | **5.0 或更高**（`blender_manifest.toml` 里 `blender_version_min = 5.0.0`），推荐 **5.2.2 LTS** |
| Python | 不用自己装，Blender 自带；核心算法只依赖 numpy（Blender 内置） |
| 客户端 jar | 可选，但**强烈建议**：有它才有真实 300 色配色；没有会退到内置 96 色，颜色明显偏 |
| 游戏侧 | Minecraft + [Litematica](https://modrinth.com/mod/litematica)（Load `.litematic` 用）；推荐 MC 26.3 + Litematica 0.29.1 |

### 推荐版本（只在这个组合上验证过）

| 组件 | 版本 | 验证到什么程度 |
|---|---|---|
| **Blender** | **5.2.2 LTS**（Steam 版，build 2026-09-15） | 面板操作、`tools/blender_m1.py` 真机 57 项全部在这个版本跑过 |
| **Minecraft** | **26.3** | 导出的 `.litematic` 在游戏里 Load 成功，形状 / 空心 / 开口 / 分层都核对过 |
| **模组加载器** | **Fabric**（本机 0.20.1） | 只影响 Litematica 能不能装，**与导出的文件无关**；Forge / NeoForge 一样能用 |
| **Litematica** | **0.29.1** | Modrinth 上 **0.29.x 才是 26.3 的构建**，0.28 及以下不支持 26.3 |
| **客户端 jar** | 26.3 官方 jar | 300 种方块色和世界限高数值都从这里读 |

### 换版本会怎样（更高 / 更低不一样）

**版本更高基本没事，版本更低才容易出事。** 不是猜的：Litematica 读取时的判断是
`version >= 1 && version <= SCHEMATIC_VERSION`，当前源码里 `SCHEMATIC_VERSION = 7`，
`SCHEMATIC_VERSION_SUB = 1`——我们写出的正是 `7 / 1`。所以对更新版的 Litematica 来说，这个文件永远是"旧格式但合法"。

| 组件 | 换成**更高**版本 | 换成**更低**版本 |
|---|---|---|
| **Litematica** | ✅ 能读。新版的 `SCHEMATIC_VERSION` 只会 ≥ 7，向后兼容；跨版本的方块名/状态由 `convertBlockStatePalette_to_1_20_5` + `postProcessContainerIfNeeded` 自动修正 | ❌ **读不了**。`SCHEMATIC_VERSION` 低于 7 的旧版会判定"文件版本太新" |
| **Minecraft** | ✅ 一般能读。文件里的 `MinecraftDataVersion` 交给 DataFixer 升级，被删除或改名的方块可能丢几个 | ⚠️ 不一定。老版本没见过的方块会变成未知方块 |
| **Blender** | ✅ 能装。manifest 只写了 `blender_version_min = 5.0.0`，**没有上限**；扩展 API 向后兼容 | ❌ 装不上（低于 5.0 直接被 manifest 拦掉） |
| **Fabric / Forge / NeoForge** | ✅ **完全无关**。导出的是磁盘上的文件，跟模组加载器版本没有任何关系 | 同左 |

**唯一需要小心的是 MC 跨大版本的方块改名**：`MinecraftDataVersion` 填错，DataFixer 就会走错修正表。换 MC
版本时面板「游戏版本」要选对，或者干脆把新版客户端 jar 填上——DataVersion 直接从 jar 的 `version.json` 读，不用手填。

要试别的版本，建议先导一遍 `examples/05_bugtest.blend` 再进游戏 Load：那 9 个块能最快暴露格式问题。

上面「推荐版本」那张表是实际完整验收过的组合，其他组合没跑全量测试。

### 方式一：装发布包（普通用户）

1. 下载 `mc_voxel_export.zip`（[Releases](https://github.com/liz854/blender-mc-voxel/releases) 页）
2. Blender → `Edit` → `Preferences` → `Add-ons` → 右上角 `▾` → **Install from Disk…**
3. 选那个 zip，确认列表里 **Blender MC Voxel** 已勾选

> Release 里还没传包的话，用下面的方式二自己打一个，两步就完。

### 方式二：从源码打包（开发者）

```bash
git clone https://github.com/liz854/blender-mc-voxel.git
cd blender-mc-voxel
python tools/pack.py                         # 产出 dist/mc_voxel_export.zip
blender.exe --command extension install-file -r user_default -e dist/mc_voxel_export.zip
```

改完代码重跑这两条就是覆盖安装，不用手动删旧版。

### 确认装好了

3D 视图按 `N`，侧栏**最右边那一列竖排标签**里出现 **MC 体素**（`N` 弹出的 X/Y/Z 是 Blender 自带的物体变换面板，不是本扩展）。
嫌不好找就用顶部菜单栏的 **MC 体素**，或右键物体 → **MC 体素** → **打开 MC 体素面板**，面板会弹成浮窗。

装好后第一次体素化，Console 里应该能看到一行：

```
[mc_voxel] 资源包方块 300 种（26.3-Fabric 0.20.1.jar）
```

数字不对或显示「用兜底 96 色」，就是没找到客户端 jar，见下面「快速上手」第 3 条。

### 更新 / 卸载

- **更新**：重新 Install from Disk 同一个包会直接覆盖（Blender 输出 `Reinstalled "mc_voxel_export"`）
- **卸载**：Add-ons 列表里取消勾选，或右上角 `▾` → Uninstall
- 色板缓存在扩展的用户目录，卸载不会删；想强制重建就删掉 `block_colors.json`，或换一个 jar

## 快速上手

1. 选中网格物体（没有顶点色会全白，先涂色或看 `examples/`）
2. 点 **建筑 / 像素画 / 地形** 套预设，或自己调 **方块尺寸**（1 体素 = N 米）。10×10×10 的立方体配 1.0 → 1000 个方块
   也可以反过来：选 **高 / 长 / 宽** 某一轴，填 **目标格数**，点 **定倍率** 让扩展自己算（见下）
3. 选 **游戏版本**（默认 26.3），DataVersion 自动取表里的值；填了 **客户端 jar** 路径就以 jar 为准（同时用来取方块真实颜色）。jar 留空时会自动去常见启动器目录里找（官方 / PCL / HMCL / BakaXL，认 `versions/<版本>/<版本>.jar` 这种标准布局，整合包里的杂 jar 不会被误选），实在找不到才用兜底 96 色
4. 需要排除某些方块填 **禁用方块**，只想用某几种填 **只用这些方块**（见下）
5. 点 **体素化** 看统计，点 **预览方块** 生成方块预览
6. 点 **导出投影** 选格式落盘

导出后把 `.litematic` 丢进 `.minecraft/schematics/`，游戏里按 `M` → Load Schematic。

### 预设

三个按钮一键套参数：

| 预设 | 方块尺寸 | 实心 | 适合 |
|---|---|---|---|
| 建筑 | 1.0 m | 是 | 一比一的房子、雕塑 |
| 像素画 | 0.2 m | 否（只留外壳） | 平面画、贴面 |
| 地形 | 4.0 m | 是 | 大范围地貌，方块少、拖得动 |

### 倍率：从长度反算

不想手工试 0.05 还是 0.08，就直接说"我要它高 200 格"：

1. 下拉选 **高（Y）/ 长（X）/ 宽（Z）**
2. **目标格数** 填 200
3. 点 **定倍率**

方块尺寸 = 这一轴的实际长度 ÷ 目标格数，写回面板，后面的体素化和导出都用它。例如 55 m 高的猴头要 40 格高 → 55 ÷ 40 = 1.375 m 一格，体素化出来正好 40 格。

反算出来的倍率如果会让高度超限高（比如只限制了"长 64 格"，但模型又瘦又高），会按下面的规则自动压到限高内。

### 白名单与禁用

面板上是两个文本框，填的就是**方块 id**（`stone`、`oak_planks`、`glass` 这种），语法一样：逗号、分号、空格、换行都能分隔，中英文标点都认。

```
只用这些：minecraft:stone, deepslate, *wool*
禁用：glass, water; sand
```

- 不带 `minecraft:` 前缀会自动补（`glass` → `minecraft:glass`）
- 支持 `*` / `?` 通配，按当前色板展开
- **白名单**只在这几种里选色；**禁用**从候选池里剔掉，两者可以同时用，禁用优先
- **都不会留空洞**：过滤后重建 Lab LUT，原位置落到色差最小的可用方块。禁掉 `stone` 之后那块变成 `snow_block`，方块总数一个不少
- 全禁光会直接报错，不会静默输出一堆空气

侧栏那一列很窄，单行输入框里长一点的内容就只剩省略号，看不清自己填了什么。所以每个框下面都有：

| 按钮 | 干什么 |
|---|---|
| **搜索添加** | 弹搜索框，从当前色板里输入片段（`wool` / `concrete` / `stone`）挑一个，自动补全 `minecraft:` 前缀并追加到框里，不用背 id |
| **清空** | 清掉这个框 |
| 输入框下面那行 「生效 …」 | 直接回读解析结果，`*wool*` 展开成哪几种都列出来，前 4 个显示名字、后面给总数 |
| 右侧放大镜 | 弹窗列出**全部**生效方块（比侧栏宽），以及哪些名字在当前色板里找不到 |

### 纹理感知匹配

只按平均色匹配会有个毛病：一面纯色的墙，色差最小的方块可能带着斑点纹理（矿石、粗金属块），放大到 1 米一格后整面墙都在闪。所以匹配成本加了一项：

```
cost = ΔE76(色差) + 1.0 × |std_方块 − std_目标|
```

`std` 是纹理像素的颜色标准差（纯色方块 ≈ 0-5，石头类噪点 20-30，花纹理 35+，超过 42 的不进色板）。目标 std 取模型那一块区域的局部颜色方差，量化成 4 档（中心 4 / 14 / 27.5 / 47.5），每档预先建一张 32³ LUT——ΔE 矩阵只算一次，四档共用，构建开销可以忽略。

**关键设计：目标 std 必须按颜色分组取 25% 分位，不能按体素直接取。** 颜色分界处的体素，3³ 邻域横跨两种颜色，std 会被自然抬高，按体素取就会把整条分界线判成"粗糙"，结果墙色交界处冒出一圈斑点方块（T6 灰阶、T8 四原色实测都踩到了）。同色区域内边界体素是少数，取低分位就能整体判"平"。

对导出的实际影响（`tools/bugtest_export.py` 重导前后对比）：

| 测试块 | 改前 | 改后 |
|---|---|---|
| T8 四原色 | 黄色被拆成羊毛 + 金块 + 紫水晶三种 | **四象限各 64 块，各一种方块** |
| T3 / T3B 挖洞墙 | 白混凝土混萤光石、闪长岩 | **476 / 492 块全是一种白混凝土** |
| T7 金字塔 | 红陶瓦 + 两种朱砂混拼 | **935 块全是一种红陶瓦** |
| T6 五级灰阶 | 每级混入矿石 | 每级一种，无矿石 |

色板没有纹理信息时（兜底 96 色、工具脚本直接传 `colors`）这一项自动关闭，行为与改前完全一致，性能零开销（200³ 实测 16.49s，改前 16.5s）。想手动关掉：`build_voxel_model(..., texture_match=False)`。

### 重力方块支撑

沙子、沙砾、混凝土粉末这些放下会掉的方块，**下方没有实心方块托着就不许用**，换成色差最小的非重力方块（沙 → 砂岩那种）。不这么做的话，投影摆出去会塌成一地沙子。

判定要往下追到底：**沙托沙不算托**。整根悬空沙柱会被全部换掉——Litematica 施工不保证严格从下往上摆，只换最底下那格的话，先放上面那格照样塌。非重力方块不受影响，悬挑、桥、拱照留。

面板上「悬空禁用重力方块」默认开。验证样本：`python tools/gravity_demo.py`（左侧悬空沙柱三格全换、右侧石头托着的两格保留）。

### 单位与缩放

约定 **1 体素 = 1 方块 = 1 米**。导出时顶点会依次乘上：

1. 物体自身的 `matrix_world`（位移 / 旋转 / 缩放）
2. 场景的 `unit_settings.scale_length`（1 BU 等于多少米）

所以把 Suzanne 放大两倍再体素化，方块数会真的变成 8 倍；场景单位设成厘米也不会导出一个缩水 100 倍的投影。两者非默认值时 Console 会打印换算信息。

### 限高（世界高度）

MC 有世界高度上限，**超出的方块 Litematica 摆投影时会静默跳过**——不报错、不提示，就是缺了头或少半截。所以导出前会拦：

| 维度 | y 范围 | 限高 |
|---|---|---|
| 主世界 | -64 … 319 | **384 格** |
| 下界 | 0 … 255 | 256 格（`logical_height` 只有 128） |
| 末地 | 0 … 255 | 256 格 |

数值不是写死的：给了客户端 jar 就从 `data/minecraft/dimension_type/*.json` 读（缓存按 jar 的 mtime 失效），读不到才用内置兜底。也就是说改过世界高度的 mod / 数据包，只要 jar 里是对的就跟着对。

**倍率会自动压到限高内**：面板「自动压到限高内」默认开。算出来会超限高时，先把方块尺寸放大到刚好塞得下（`格数 = ceil(原始高度 ÷ 方块尺寸)` 可以直接反解，不用先体素化一遍），再把实际用的值写回面板——所以你看到的就是真实生效的倍率，不会出现"我明明设了 1.0，导出却是 1.0417"的困惑。关掉它就退回旧行为：严格限高直接报错。

判定分三档：

- **放得下**：直接过
- **放得下但得从地下起建**：主世界地面在 y=63，往上只有 257 格。模型高过 257 就告警，告诉你得往下埋 `高度 − 257` 格
- **超限高**：直接中止，并给出两条出路——把方块尺寸调到多少（例：400 格高、当前 1.0 → 调到 1.05），或者沿高度拆成几段分别导出

面板「维度」下面实时显示当前维度的 y 范围和地面以上可用格数。用改高度的整合包时可以把「严格限高」关掉，关掉只告警不拦。

> 结构方块 `.nbt` 的文件格式本身不限高，但游戏里结构方块界面最大只能填 48，超了会填不进去（Console 会提示）。

### 预览图（看着像游戏里摆出来的样子）

**渲染预览图** 出一张等距（isometric）PNG：正交相机、真实方块平均色、Workbench 的接触阴影和 cavity。不用自己光栅化，也不用手动摆相机。

- 默认 `(1,1,1)` 观察方向、透明关掉、浅蓝灰背景，长边 1024 像素（面板可调 256–4096）
- 画幅按**实际顶点投影**算，不是包围盒八个角——否则尖耳、细尾巴四周会留一大圈空白
- 只画外露面，面数跟表面积走：猴头 55×40×35 是 13400 个面，1 秒出图
- 输出路径留空就放在当前 `.blend` 旁边（没存过则放临时目录），面板填了就用填的
- 渲染完把引擎、分辨率、相机、世界全部还原，不会污染你的工程

### 反向导入（MC → Blender）

**导入投影** 读 `.litematic` / `.schem` 成网格，多 region 自动合并，负 `Size` 会归一化。导入后两个开关分开管：

| 开关 | 效果 | 代价 |
|---|---|---|
| 平滑着色 | 只改法线，看起来圆润 | 零，轮廓一个不差 |
| 磨圆（Remesh） | 真的重建成平滑曲面 | 丢锐利边缘和颜色细节，只适合展示 |

磨圆可以调 **磨圆精度**（Voxel Size，默认 0.5）。不用自研 marching cubes：体素模型的方块感是特征不是缺陷，强行平滑会毁掉建筑轮廓。

## 验收

不装 Blender 就能跑：

```bash
python -m pytest -q          # 207 项，全绿
python tools/verify.py       # 端到端 22 项检查，产物写到 dist/
```

装了 Blender 再加一条真机：

```bash
blender.exe -b --python tools/blender_m1.py    # 57 项，全绿
```

`verify.py` 想连真机 jar 一起验限高，可以指定路径（不指定就用 `mcver.find_client_jar()` 猜）：

```bash
MC_JAR="<客户端 jar 路径>" python tools/verify.py
```

关键几条：

- 10×10×10 立方体 → `Filled: 1000`
- 外壳 488 个（1000 − 8³），填充后 1000
- 单个 Plane → 一层 100 个，不漏成 0 也不吞掉整个包围盒
- 去掉一面的盒子 → 内部空心，填充不进去
- 2×2×2 全 stone → `RegionCount=1, TotalBlocks=8, palette 长度=2, BlockStates 长度=1`
- 非立方体尺寸（5×2×3）每块位置原位还原
- `.litematic` 与 `.schem` 读回逐块一致
- 禁用 / 白名单后 `TotalBlocks` 不变，只是颜色换人
- 悬空沙柱整根替换，托着的沙子一格不动
- 物体放大 2 倍 → 8000 方块；`scale_length=2.0` → 同样翻倍
- 400 格高的方柱：主世界被限高拦下、关掉严格限高能过、切到下界照样拦；55 格正常
- 按高度 40 格定倍率 → 方块尺寸 0.25，体素化出来正好 40 格
- 自动压限高：400 格高的柱子自动放大到 1.0417，压成 384 格，不报错
- 等距预览图能渲染出来、长边 320、不是纯色
- 活动物体为空但只有一个选中的网格 → 照样能体素化（浮窗面板里常这样）
- 选中摄像机时提示点名「摄像机」，不再笼统地说「先选一个网格物体」
- 预设、平滑两开关、导入后不加多余修改器

## 性能（实测）

Windows / Ryzen 机器，球体 4096 个三角形，`solid=True`：

| 网格 | 耗时 | 峰值内存 |
|---|---|---|
| 100³（51 万方块） | **2.5 s** | 110 MB |
| 200³（414 万方块） | 16.5 s | 646 MB |
| 300³（1400 万方块） | 约 63 s | 2.1 GB |
| 导出 100 万方块 | **0.08 s** | — |

复跑：`python tools/bench.py 100 200 300`。

三个提速点值得记着：

- **三角形按覆盖尺寸分组批量算 SAT**。同尺寸的三角形拼成 `(T, V)` 张量，Python 循环从"每个三角形一次"降到"每批一次"。小球体快 10 倍。
- **颜色扩散只更新边界环**。老写法每轮 `np.roll` 整个颜色网格（200³ 时 186 MB × 6 次 × 100 轮），光是这一步就 45 秒；现在只对"贴着已定色区域"的一薄层取值，降到 2 秒级。
- **SAT 是 16 轴不是 13 轴**。除了 3 根盒轴、三角形法线、3 根边法线，还必须测 9 根「盒轴 × 三角形边」。少了这 9 根，三角形的一条边从体素角落斜着掠过时所有轴都投影重叠，会被误判成"相交"——四棱锥的尖顶会糊成一个三角形、斜棱附近整体膨胀。补上以后 200³ 从 13.5 s 涨到 16.5 s（+22%），换来的是尖角和斜面终于干净了。

**512³ 跑不动**：稠密网格下 134M 格 × 约 57 字节 ≈ 7 GB，会吃爆内存。`build_voxel_model(max_voxels=64_000_000)` 会在分配前拦住并告诉你需要多大内存——调大方块尺寸或分段导出。实际使用（Suzanne 55×40×35）只要 200 MB。

## 示例场景

`examples/` 下五个 `.blend`。前三个由 `tools/make_examples.py` 生成，房子由 `tools/make_house.py` 生成，bugtest 由 `tools/make_bugtest.py` 生成（都能 `blender -b --python tools/xxx.py` 重新生成）：

| 文件 | 内容 | 用法 |
|---|---|---|
| `01_cube_gradient.blend` | 灰阶渐变立方体，10 m | 方块尺寸 1.0，看调色板过渡（应落到 stone / andesite / deepslate 系列，不是一片 stone） |
| `02_sphere.blend` | 彩色球，半径 5 m | 方块尺寸 1.0，看曲面精度；关掉实心看外壳 |
| `03_suzanne.blend` | Suzanne 猴头，顶点色已涂 | 方块尺寸 0.05，真实模型的量级 |
| `04_house.blend` | 小房子：20×20 底、墙高 8、四棱锥屋顶、门+两扇窗+烟囱 | 方块尺寸 0.5 → 43×25×43 格 / 16618 方块，**手动练手推荐从这个开始** |
| `05_bugtest.blend` | 9 个测试块，每块针对一类容易出错的点 | 见下 |

### 进游戏找 bug（bugtest）

`05_bugtest.blend` 是给"导出之后进游戏看效果"准备的一套对照样本，一次 Load 就能把常见的坑扫一遍：

```
blender.exe -b examples/05_bugtest.blend --python tools/bugtest_export.py
```

会往游戏 `schematics/` 写 10 个投影（1 个总览 `bugtest_all` + 9 个单块），并在 `dist/bugtest/` 生成每块的等距预览图和一张 `README.md` 对照表。目标目录自动探测（客户端 jar 同级的 `schematics/`），也可以用 `MC_SCHEMATICS` 环境变量指定。

![9 个测试块总览](docs/images/bugtest.png)

| 块 | 测什么 |
|---|---|
| T1 平墙 | 同一面墙必须**只有一种颜色**（曾经因为布尔把顶点色弄成纯黑，墙面变成深浅两色斜着拼） |
| T2 渐变 | 红→蓝的色阶连续性，会不会突然跳色 |
| T3 挖洞（先挖后涂） | 门/窗洞口边缘和墙面同色，没有黑边 |
| T3B 挖洞（先涂后挖） | **故意做错顺序**，靠 `mesh_data._fill_missing_colors` 兜底，结果应该和 T3 一样 |
| T4 悬空沙柱 | 重力规则：贴地的沙保留、被挖断后悬空的沙换成砂岩 |
| T5 薄板 + 细柱 | 0.5 米厚（正好 1 格）的薄几何不漏块 |
| T6 五级灰阶 | 色板分辨率，相邻灰度不该撞成同一个方块 |
| T7 金字塔 | 45° 斜面的阶梯是否连续、有没有空洞 |
| T8 四原色 | 红黄蓝白有没有系统性偏色 |

对照表里「实际用到的主要方块」是导出时真正选中的方块——**这一列和你截图里看到的纹理不一致，就是 bug**。

## 结构

```
blender_manifest.toml
__init__.py              只做 register/unregister，顶层不 import bpy
core/                    除 mesh_data.py 外不 import bpy，可脱离 Blender 跑
  coords.py              唯一的坐标转换入口（bl_x, bl_z, -bl_y）
  ir.py                  VoxelModel / BlockState
  yzx.py                 唯一的 YZX 索引 + bit packer
  voxelize.py            三角形-体素 AABB 的 16 轴 SAT（按尺寸分组批量算）
  fill.py                6-连通 flood fill 内部填充 + 颜色扩散 + 邻域颜色 std
  gravity.py             重力方块支撑检查
  png.py                 最小 PNG 解码（zlib + struct，支持 4bit 调色板）
  pack.py                资源包解析 → 方块平均色 + 纹理标准差
  color.py               sRGB → Lab(D65)、ΔE76
  palette.py             32³ Lab LUT（纹理 4 档各一张）+ 白名单/禁用 + 纹理感知匹配
  nbt.py                 NBT 读写（大端、modified UTF-8、gzip）
  litematic.py           ★ P0 导出 / 读回
  schem.py               Sponge v2
  structure.py           结构方块 .nbt（≤48³）
  build.py               网格 → VoxelModel 流水线（含内存守卫）
  limits.py              世界限高：维度 y 范围（从 jar 的 dimension_type 读）
  mcver.py               版本 → DataVersion，从 jar 的 version.json 读
  mesh_data.py           bpy mesh → 顶点/三角形/顶点色（唯一碰 bpy 的 core 文件）
  mesh_export.py         反向：VoxelModel → 外表面网格
ui/                      面板、操作符、属性
  picker.py              白名单/禁用框的输入辅助（搜索选方块、清空、明细）
  paths.py               扩展用户目录（色板缓存放这，注意要传完整包名）
  preview_render.py      等距预览图（Workbench 渲染，唯一在 ui 里碰渲染的）
data/fallback_palette.json
examples/                5 个示例场景
tests/                   207 项
docs/images/             README 用的图
tools/
  _paths.py              工具脚本共用的路径解析（自动找 jar / schematics，别写死本机路径）
  verify.py              端到端验收
  blender_m1.py          Blender 真机验收（57 项）
  blender_suzanne.py     猴头演示：体素化 + 渲染预览 + 导出
  gravity_demo.py        重力规则验证样本
  bench.py               性能实测
  make_examples.py       生成 examples/ 的立方体/球/猴头
  make_house.py          生成 examples/04_house.blend（小房子）
  make_bugtest.py        生成 examples/05_bugtest.blend（9 个测试块）
  bugtest_export.py      把 bugtest 导出成游戏投影 + 预览图 + 对照表
  house_preview.py       给房子出一张等距预览图
  voxelize_compare.py    新旧体素化实现对拍
  pack.py                打包成可安装 zip
```

## 调色板怎么来的（core/pack.py）

给了客户端 jar 就以 jar 为准，从资源包算出每个方块的平均色；没填就自动去常见启动器目录里找一个，都没有才用内置的 96 色兜底表。

从 26.3 的客户端 jar 里能解析出 **300 种可用方块**（0.2 秒，结果按 jar 的 mtime 缓存在扩展的用户目录）。这个数字是层层筛出来的：

| 阶段 | 数量 | 筛掉什么 |
|---|---|---|
| `blockstates/*.json` | 1288 | — |
| 是"整方块" | 418 | 楼梯、台阶、门、栅栏、按钮、栏杆——模型里没有盖满 0..16 的方盒 |
| 纹理够纯 | 358 | 书架、工作台、织布机这类器物图案（纹理颜色标准差 > 42） |
| 能当建材 | **300** | 树叶（`*_leaves`，半透明+群系 tint）、混凝土粉末（`*_concrete_powder`，放下就塌）、釉面陶瓦（`*_glazed_terracotta`，箭头图案有方向，铺墙会乱）、**珊瑚块（`*_coral_block`，离水就变死珊瑚、整块变灰）**、虫蚀方块（`infested_*`） |

最后这一档是补上"禁用方块"功能时才发现的：禁掉 `stone` 之后，色差最接近的替代居然是 `pale_oak_leaves`（树叶）和 `black_concrete_powder`（放下就塌的粉末）。色差算得没错，材质是错的，所以按名字规则先剔除。

另外两类特殊处理：

- **灰度纹理要乘生物群系 tint**。草方块和树叶的纹理本身是灰的，游戏里按群系上色；不乘的话草地会变成灰地。表在 `pack.BIOME_TINTS`。
- **功能方块黑名单**。工作台、熔炉、箱子、书架这类虽然也是整方块，但纹理是器物图案，拿来铺墙会变成杂乱马赛克。表在 `pack.NON_BUILDING_BLOCKS`，按后缀/前缀排除的在 `NON_BUILDING_SUFFIXES` / `NON_BUILDING_PREFIXES`。

这四处（`models/` 前缀、整方块过滤、空缓存、缓存目录参数）都是**只有在真实 jar 上跑才会暴露**的问题——单测用兜底色板永远测不出来，所以补了 `tests/test_pack.py`，用最小假资源包把整条链钉住。

最后那个最长记性：`bpy.utils.extension_path_user()` 要的是**完整包名**（`bl_ext.user_default.mc_voxel_export`），传 `__package__.split(".")[0]` 得到的 `bl_ext` 会抛 `ValueError: The "package" does not name an extension`。异常被 `except` 吞掉后，表现和「jar 解析失败」一模一样——静默退回兜底 96 色，颜色一直偏，日志里只有一行「用兜底色板」，而且照样能导出、能在游戏里 Load，谁也不会怀疑。现在统一走 `ui/paths.py`，并且解析成功时把「资源包方块 300 种」打进日志：数字不对，一眼就能看出来。

## 已知限制

- 不烘焙光照，只取材质基色。MC 引擎自己会打光
- v1 输出整方块，不出楼梯/半砖（半砖/楼梯的 `BlockState.props` 已经能带，缺的是正向输出）
- TileEntities 为空，箱子和告示牌内容不保留
- 结构方块 `.nbt` 有 48 边限制，超出请用 `.litematic`
- 反向导入不还原楼梯形状，楼梯按整方块处理
- remesh smooth 会丢颜色精度
- 预览图是等距单视角，不给多角度/动画
- 模型没有顶点色也没贴图时会全白（Suzanne 这种要先涂顶点色，见 `tools/blender_suzanne.py`）
- 512³ 受稠密网格内存限制，会被守卫拦下（见「性能」）
- 限高只按「y 跨度」判，不管摆放位置：384 格高的模型能通过，但得从 y=-64 起建才放得下
- 纹理只取平均色 + 标准差两个统计量，不做逐像素结构匹配（砖纹不会对齐，也不会只因为图案好看就选某个方块）

## 格式对齐（按 Litematica 源码核对过）

这几个值不是猜的，是照 `maruohon/litematica` 的 Java 源码逐条对出来的：

| 项 | 值 | 来源 |
|---|---|---|
| `Version` / `SubVersion` | **7 / 1** | 游戏里真实存出来的样本（Litematica 0.29.1 / MC 26.3） |
| `BlockStates` 打包 | **tight**（值可跨 long 边界） | `getAsTightLongBackedArray` / `TightLongBackedIntArray` |
| `bits` | `max(2, ⌈log2(paletteSize)⌉)` | `ArrayBlockContainer.getRequiredBitWidth` |
| LongArray 长度 | `⌈volume × bits / 64⌉` | `TightLongBackedIntArray.getRequiredArrayLength` |
| 索引 | `x + z·sx + y·sx·sz` | `ArrayBlockContainer.getIndex` |
| Metadata | `Name / Author / Description / TimeCreated / TimeModified / EnclosingSize / RegionCount / TotalVolume / TotalBlocks` | `SchematicMetadata.write` |

版本号这块走过两次弯路：方案文档写 6，GitHub master 分支写 4，**真实文件是 7 + SubVersion 1**。以真文件为准——master 那条分支不是 26.3 的构建。

`tests/test_golden_sample.py` 直接拿真样本做回归：把它读进模型、用写出器重编码，要求 `BlockStates` 与 palette **逐位一致**（实测都是 `0x87654321`）。`tests/test_litematica_conformance.py` 另外按 Java 规则独立重算 bits / long 数量 / 索引解码。两套一起防跑偏。

## 已知的一处不一致

真样本里区域 `Size` 是 `(2, -2, -2)`（负数表示往负方向延伸），我们写出的永远是正数。Litematica 两种都吃，读的时候按 min/max 归一化，不影响加载。

## 验证状态

- `pytest` 207 项全绿（含 9 项 golden 回归、13 项资源包解析、10 项重力规则、8 项禁用方块、33 项限高与倍率换算、9 项白名单框增删与 jar 自动查找、11 项纹理感知匹配）
- `tools/verify.py` 22 项端到端
- Blender 5.2.2 真机 57 项（`tools/blender_m1.py`：含禁用/白名单生效且不留空洞、预设、单位换算、限高拦截、倍率反算、等距预览图、平滑两开关、搜索添加/清空/回读明细、jar 自动查找）
- 真样本双向对拍：读得进去，重编码逐位相同
- **游戏内实装已确认**（MC 26.3 + Fabric + Litematica 0.29.1）：16×16×16 空心盒装进 `.minecraft/schematics/`，游戏里 Load 出来形状、空心、开口位置、三色分层都对
- 真实模型跑通：Suzanne 猴头 55×40×35 / 7509 方块 / 16 种方块，用 26.3 客户端 jar 的真实色板配色（`tools/blender_suzanne.py`）
- 性能：100³ 2.5 s / 110 MB、200³ 16.5 s / 646 MB、100 万方块导出 0.08 s

整条链闭合：Blender 场景 → 体素化 → `.litematic` → 游戏内可见。

## 常见问题

**按 `N` 找不到「MC 体素」标签**
侧栏标签是竖排的，在最右边那一列，容易被窗口边缘裁掉；把侧栏拉宽一点。`N` 直接弹出的 X/Y/Z 是 Blender 自己的物体变换面板。也可以用顶部菜单栏 **MC 体素**，或右键物体 → **MC 体素**。

**颜色明显偏 / Console 里说「用兜底 96 色」**
没找到客户端 jar。面板「客户端 jar」填绝对路径（`.../versions/<版本>/<版本>.jar`），或点「自动找」。填对了 Console 会打印 `资源包方块 300 种`。

**体素化出来全是白的**
模型没有顶点色，也没有材质颜色。本扩展只读顶点色，不读贴图——先在 Blender 里涂顶点色（`examples/03_suzanne.blend` 是涂好的例子）。

**导出后在游戏里 Load 不到**
`.litematic` 要放进 `<游戏目录>/schematics/`；Litematica 版本必须支持你的游戏版本（26.3 对应 0.29.x）。

**投影摆出来缺块 / 少一截头**
两种情况：模型高过世界限高（Litematica 会静默跳过超出 y 范围的方块），或者用了会掉的方块。前者看面板「维度」下面提示的可用格数，后者看是否关掉了「悬空禁用重力方块」。

**报错「网格 X×Y×Z 格，超过上限」**
体素网格是稠密的，512³ 要 7 GB 内存。把方块尺寸调大（一格代表更多米），或把模型沿高度拆成几段分别导出。

**体素化结果是 0 个方块**
模型是单面片（Plane）且法线朝内，或者物体被隐藏/在别的集合里。检查 `Alt+H` 取消隐藏、法线方向（`Shift+N` 重算外侧）。

**用整合包，自动找到的 jar 不对**
整合包目录下常有一堆杂 jar。自动查找只认 `versions/<版本>/<版本>.jar` 这种标准布局；还不对就手动填路径，面板上填的优先级最高。

## 许可证

GPL-3.0-or-later，见 [LICENSE](LICENSE)。核心原因是 Blender 扩展体系本身要求 GPL 兼容，另外这个项目也是从 Litematica 的源码里核对格式细节才写对的。

代码可以随意改、随意分发，但分发时要同样开源。有问题直接开 Issue。
