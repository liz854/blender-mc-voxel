"""从 jar / 版本表读 MinecraftDataVersion。

幻觉高发区：绝对不要凭记忆填 DataVersion。
优先级：客户端 jar 的 version.json > 用户手填 > 内置已验证版本表 > 抛异常。

内置表的来源（三处交叉核对，不是记忆）：
  - https://piston-meta.mojang.com/mc/game/version_manifest_v2.json
  - https://github.com/misode/mcmeta  versions/data.min.json（data_version 字段）
  - https://minecraft.wiki/w/Data_version
"""

from __future__ import annotations

import glob
import json
import os
import zipfile


# 已验证的正式版 -> DataVersion。只收当前主版本线 26.x，
# 更老的版本让扩展读 jar，别在这里堆没核对过的数字。
KNOWN_DATA_VERSIONS = {
    "26.3": 5023,
    "26.2": 4903,
    "26.1.2": 4790,
    "26.1.1": 4788,
    "26.1": 4786,
}

DEFAULT_RELEASE = "26.3"


class DataVersionUnknown(RuntimeError):
    pass


def data_version_for_release(name: str) -> int:
    """按正式版号查 DataVersion（只认表里核对过的）。"""
    key = str(name).strip()
    if key not in KNOWN_DATA_VERSIONS:
        raise DataVersionUnknown(
            f"版本表里没有 {name}，请指定客户端 jar 或直接手填 DataVersion"
        )
    return KNOWN_DATA_VERSIONS[key]


def read_data_version(jar_path: str):
    """从客户端 jar 的 version.json 读 world_version。"""
    try:
        with zipfile.ZipFile(jar_path) as z:
            raw = z.read("version.json")
        data = json.loads(raw.decode("utf-8"))
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise DataVersionUnknown(f"读不到 version.json：{jar_path}（{exc}）") from exc

    for key in ("world_version", "worldVersion", "data_version"):
        if key in data:
            return int(data[key])
    if isinstance(data.get("version"), dict) and "world_version" in data["version"]:
        return int(data["version"]["world_version"])
    raise DataVersionUnknown(f"version.json 里没有 world_version 字段：{data}")


def candidate_minecraft_dirs():
    """常见的 .minecraft 位置：官方启动器 + PCL / HMCL / BakaXL 的绿色版目录。

    只看固定候选，不做全盘遍历（那样每个盘都要扫一遍，面板一画就卡）。
    """
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, "AppData", "Roaming", ".minecraft"),
        os.path.join(home, "AppData", "Roaming", "BakaXL", ".minecraft"),
        os.path.join(home, "AppData", "Roaming", "com.modrinth.theseus", "meta", ".minecraft"),
    ]
    for drive in "CDEFGHIJ":
        for sub in ("", "PCL", "HMCL", "BakaXL", "Games", "Minecraft", "mc", "Minecraft Launcher"):
            roots.append(os.path.join(f"{drive}:\\", sub, ".minecraft"))

    out, seen = [], set()
    for path in roots:
        try:
            key = os.path.normcase(os.path.abspath(path))
        except OSError:
            continue
        if key in seen or not os.path.isdir(path):
            continue
        seen.add(key)
        out.append(path)
    return out


def find_client_jar(minecraft_dir: str = None):
    """猜一个客户端 jar：优先 `versions/<版本>/<版本>.jar` 这种标准布局。

    只认「jar 名 == 父目录名」的，整合包目录里成百上千个杂 jar 就不会被误选。
    都找不到时才退而求其次，取修改时间最新的那个。
    """
    dirs = [minecraft_dir] if minecraft_dir else candidate_minecraft_dirs()
    strict, loose = [], []
    for base_dir in dirs:
        pattern = os.path.join(base_dir, "versions", "*", "*.jar")
        for path in glob.glob(pattern):
            name = os.path.basename(path)
            if name.endswith("-natives.jar"):
                continue
            try:
                stamp = os.path.getmtime(path)
            except OSError:
                continue
            stem = os.path.splitext(name)[0]
            target = strict if stem == os.path.basename(os.path.dirname(path)) else loose
            target.append((stamp, path))

    pick = strict or loose
    if not pick:
        return None
    pick.sort(reverse=True)
    return pick[0][1]


def resolve_data_version(jar_path: str = None, fallback: int = None, release: str = None) -> int:
    """确定 DataVersion。

    顺序：客户端 jar（最权威） → 用户手填 fallback → release 版本表 → 报错。
    """
    if jar_path:
        try:
            return read_data_version(jar_path)
        except DataVersionUnknown:
            pass
    if fallback:
        return int(fallback)
    if release and str(release).upper() != "CUSTOM":
        return data_version_for_release(release)
    raise DataVersionUnknown(
        "无法确定 MinecraftDataVersion。请指定客户端 jar、选一个游戏版本，或手动填写（不许猜）。"
    )
