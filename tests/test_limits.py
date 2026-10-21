"""限高：数值必须来自真机 jar，判定边界要准。"""

import json
import zipfile

import numpy as np
import pytest

from core.limits import (
    DIMENSION_DEFAULTS,
    check_height,
    check_model,
    fit_block_size,
    get_dimension,
    load_dimensions,
    read_dimensions,
)
from core.ir import BlockState, VoxelModel


def _write_jar(path, dims):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in dims.items():
            z.writestr(f"data/minecraft/dimension_type/{name}.json", json.dumps(data))
        # 目录条目必须被跳过（read 它会抛 IsADirectory）
        z.writestr("data/minecraft/dimension_type/", "")
    return str(path)


def test_defaults_match_26_3_jar():
    """兜底值 = 26.3 真机 jar 的 dimension_type。"""
    ow = DIMENSION_DEFAULTS["overworld"]
    assert (ow.min_y, ow.height, ow.max_y) == (-64, 384, 319)
    nether = DIMENSION_DEFAULTS["the_nether"]
    assert (nether.min_y, nether.height, nether.logical_height) == (0, 256, 128)
    end = DIMENSION_DEFAULTS["the_end"]
    assert (end.min_y, end.height) == (0, 256)


def test_max_y_is_inclusive():
    """max_y 是最高一层能放方块的 y，不是排他上界。"""
    assert DIMENSION_DEFAULTS["overworld"].max_y == -64 + 384 - 1


def test_read_dimensions_from_jar(tmp_path):
    jar = _write_jar(
        tmp_path / "client.jar",
        {
            "overworld": {"min_y": -128, "height": 512, "logical_height": 512},
            "the_nether": {"min_y": 0, "height": 128},
        },
    )
    dims = read_dimensions(jar)
    assert set(dims) == {"overworld", "the_nether"}
    assert dims["overworld"].height == 512
    assert dims["overworld"].max_y == 383
    # 没写 logical_height 时退回 height
    assert dims["the_nether"].logical_height == 128


def test_read_dimensions_bad_jar_is_empty(tmp_path):
    bad = tmp_path / "broken.jar"
    bad.write_bytes(b"definitely not a zip")
    assert read_dimensions(str(bad)) == {}
    assert read_dimensions("") == {}
    assert read_dimensions(str(tmp_path / "missing.jar")) == {}


def test_load_dimensions_merges_jar_over_defaults(tmp_path):
    jar = _write_jar(
        tmp_path / "client.jar", {"overworld": {"min_y": -128, "height": 512}}
    )
    dims = load_dimensions(jar)
    assert dims["overworld"].height == 512  # jar 覆盖
    assert dims["the_end"].height == 256  # 兜底补上


def test_alias_and_unknown_name():
    assert get_dimension("nether").name == "the_nether"
    assert get_dimension("the_nether").name == "the_nether"
    assert get_dimension("end").name == "the_end"
    assert get_dimension("AmazingWorld").name == "overworld"  # 未知退回主世界


def test_ok_when_fits_above_ground():
    r = check_height(55)  # 猴头高度
    assert r.level == "ok" and r.ok
    assert r.limit == 384
    assert "放得下" in r.message


def test_boundary_exactly_ground_free_is_ok():
    """地面以上整 257 格，刚好放得下，不该告警。"""
    assert check_height(257).level == "ok"


def test_warn_when_taller_than_ground_free():
    r = check_height(258)
    assert r.level == "warn"
    assert r.ok  # 只是告警，不拦
    assert "地面" in r.message


def test_full_height_is_warn_not_error():
    """384 格整能塞进主世界（从 y=-64 起），但要从地下起建。"""
    r = check_height(384)
    assert r.level == "warn"


def test_error_over_limit():
    r = check_height(385)
    assert r.level == "error" and not r.ok
    assert "385" in r.message and "384" in r.message
    assert "2 段" in r.message


def test_error_message_suggests_block_size():
    r = check_height(400, block_size=1.0)
    assert "1.05" in r.message


def test_error_splits_into_three_segments():
    r = check_height(1000)
    assert "3 段" in r.message


def test_fit_block_size():
    assert fit_block_size(1.0, 400, 384) == 1.05
    assert fit_block_size(1.0, 100, 384) == 1.0  # 不用改
    assert fit_block_size(0.5, 800, 384) == 1.05


def test_nether_limit():
    assert check_height(300, "nether").level == "error"
    # 下界没定义地面高度，200 格只按 256 的硬限高判
    assert check_height(200, "nether").level == "ok"


def test_end_limit():
    assert check_height(256, "end").level == "ok"
    assert check_height(257, "end").level == "error"


def test_ground_level_can_be_disabled():
    assert check_height(384, ground_level=None).level == "ok"


def test_check_height_uses_jar_values(tmp_path):
    jar = _write_jar(
        tmp_path / "client.jar", {"overworld": {"min_y": -64, "height": 128}}
    )
    r = check_height(200, jar_path=jar)
    assert r.level == "error"
    assert r.limit == 128


def test_check_model_reads_y_span():
    m = VoxelModel((4, 40, 4))
    m.set(0, 39, 0, BlockState("minecraft:stone"))
    r = check_model(m)
    assert r.span == 40 and r.level == "ok"


# --------------------------------------------------------------------------
# 倍率换算（按长度定倍率 / 自动压到限高）
# --------------------------------------------------------------------------
from core.limits import block_size_for_length, fit_block_size_to_height, span_cells


def test_block_size_for_length():
    """200 m 长、要 64 格 -> 3.125 m 一格。"""
    assert block_size_for_length(200.0, 64) == pytest.approx(3.125)
    assert block_size_for_length(10.0, 40) == pytest.approx(0.25)


def test_block_size_for_length_rejects_bad_input():
    with pytest.raises(ValueError):
        block_size_for_length(10.0, 0)
    with pytest.raises(ValueError):
        block_size_for_length(0.0, 10)


def test_span_cells_matches_ceil():
    assert span_cells(400.0, 1.0) == 400
    assert span_cells(400.0, 4.0) == 100
    assert span_cells(10.0, 3.0) == 4  # ceil(3.33)


def test_fit_block_size_to_height_leaves_fitting_alone():
    assert fit_block_size_to_height(1.0, 100.0, 384) == 1.0
    assert fit_block_size_to_height(1.0, 384.0, 384) == 1.0


def test_fit_block_size_to_height_shrinks_to_limit():
    bs = fit_block_size_to_height(1.0, 400.0, 384)
    assert bs > 1.0
    assert span_cells(400.0, bs) <= 384
    assert bs == pytest.approx(400.0 / 384, rel=1e-3)


@pytest.mark.parametrize("span", [385.0, 400.0, 500.0, 777.7, 1234.5, 10000.0])
def test_fitted_block_size_never_exceeds_limit(span):
    """浮点边界上也不能多出 1 格——384 格整和 385 格是生与死。"""
    bs = fit_block_size_to_height(1.0, span, 384)
    assert span_cells(span, bs) <= 384


def test_fit_block_size_to_height_nether():
    bs = fit_block_size_to_height(0.5, 400.0, 256)
    assert span_cells(400.0, bs) <= 256


def test_fit_size_end_to_end_cells():
    """按长度定倍率后，真的正好是那个格数。"""
    from core.build import grid_from_bounds

    span_y = 55.0
    bs = block_size_for_length(span_y, 40)
    _origin, size = grid_from_bounds(np.array([[0, 0, 0], [10, span_y, 10]], float), bs)
    assert size[1] == 40


def test_fitted_size_survives_float32_roundtrip():
    """结果会写进 FloatProperty（float32），存回去再读也不能多出 1 格。

    400/384 = 1.0416666…，float32 变圆之后正好能算出 385 格——真机上踩过。
    """
    import struct

    for span in (385.0, 400.0, 777.7, 1000.0):
        bs = fit_block_size_to_height(1.0, span, 384)
        back = struct.unpack("f", struct.pack("f", bs))[0]
        assert span_cells(span, back) <= 384, (span, bs, back)
