"""DataVersion 解析的测试。

重点：不许猜。jar 优先，其次手填，再次内置已验证表，都没有必须报错。
"""

from __future__ import annotations

import json
import zipfile

import pytest

from core import mcver


def test_26_3_is_5023():
    """Minecraft 26.3 的 DataVersion，来源：Mojang manifest + misode/mcmeta + wiki 三方一致。"""
    assert mcver.data_version_for_release("26.3") == 5023
    assert mcver.DEFAULT_RELEASE == "26.3"
    assert mcver.KNOWN_DATA_VERSIONS[mcver.DEFAULT_RELEASE] == 5023


def test_known_table_monotonic():
    """表里的版本号必须单调递增，写反了说明抄错。"""
    items = sorted(
        mcver.KNOWN_DATA_VERSIONS.items(),
        key=lambda kv: tuple(int(p) for p in kv[0].split(".")),
    )
    values = [v for _, v in items]
    assert values == sorted(values), f"DataVersion 不单调：{items}"


def test_unknown_release_raises():
    with pytest.raises(mcver.DataVersionUnknown):
        mcver.data_version_for_release("1.7.10")


def test_resolve_prefers_jar(tmp_path):
    jar = tmp_path / "26.3.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("version.json", json.dumps({"world_version": 5023, "id": "26.3"}))
    # jar 说 5023，手填说 9999 -> jar 优先
    assert mcver.resolve_data_version(str(jar), fallback=9999, release="26.2") == 5023


def test_resolve_falls_back_to_manual(tmp_path):
    bad = tmp_path / "broken.jar"
    bad.write_bytes(b"not a zip")
    assert mcver.resolve_data_version(str(bad), fallback=4903) == 4903


def test_resolve_falls_back_to_release_table(tmp_path):
    assert mcver.resolve_data_version(None, release="26.3") == 5023


def test_custom_release_does_not_use_table():
    with pytest.raises(mcver.DataVersionUnknown):
        mcver.resolve_data_version(None, release="CUSTOM")


def test_nothing_usable_raises():
    with pytest.raises(mcver.DataVersionUnknown):
        mcver.resolve_data_version(None)


def test_read_data_version_from_jar(tmp_path):
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("version.json", json.dumps({"world_version": 5023}))
    assert mcver.read_data_version(str(jar)) == 5023


# ---------------- 自动找客户端 jar（PCL / HMCL 那种绿色版目录） ----------------


def _make_client(mc_dir, version, stamp=1000.0):
    """造一个启动器标准布局：versions/<版本>/<版本>.jar"""
    import os

    d = mc_dir / "versions" / version
    d.mkdir(parents=True, exist_ok=True)
    jar = d / f"{version}.jar"
    jar.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    os.utime(jar, (stamp, stamp))
    return jar


def test_find_client_jar_prefers_standard_layout(tmp_path):
    jar = _make_client(tmp_path, "26.3-Fabric 0.20.1")
    # 整合包里那种杂 jar 不能被选中
    junk = tmp_path / "versions" / "sodium-0.6" / "random-mod.jar"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"junk")

    assert mcver.find_client_jar(str(tmp_path)) == str(jar)


def test_find_client_jar_picks_newest(tmp_path):
    import os

    _make_client(tmp_path, "1.20.1", stamp=1000.0)
    new = _make_client(tmp_path, "26.3", stamp=2000.0)
    os.utime(new, (2000.0, 2000.0))
    assert mcver.find_client_jar(str(tmp_path)) == str(new)


def test_find_client_jar_skips_natives(tmp_path):
    d = tmp_path / "versions" / "26.3-natives"
    d.mkdir(parents=True)
    (d / "26.3-natives.jar").write_bytes(b"x")
    assert mcver.find_client_jar(str(tmp_path)) is None


def test_find_client_jar_missing_dir_is_none(tmp_path):
    assert mcver.find_client_jar(str(tmp_path / "nope")) is None


def test_candidate_dirs_only_lists_existing_unique():
    import os

    dirs = mcver.candidate_minecraft_dirs()
    assert all(os.path.isdir(d) for d in dirs)
    assert len({os.path.normcase(os.path.abspath(d)) for d in dirs}) == len(dirs)
