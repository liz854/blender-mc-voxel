import numpy as np

from core.coords import blender_to_mc, blender_to_mc_array, mc_to_blender, mc_to_blender_array


def test_three_asserts():
    assert blender_to_mc((1, 0, 0)) == (1, 0, 0)
    assert blender_to_mc((0, 0, 1)) == (0, 1, 0)
    assert blender_to_mc((0, 1, 0)) == (0, 0, -1)


def test_scalar_roundtrip():
    for p in [(1, 2, 3), (-4.5, 0, 7), (0, 0, 0)]:
        assert mc_to_blender(blender_to_mc(p)) == p


def test_array_matches_scalar():
    rng = np.random.default_rng(0)
    verts = rng.normal(size=(50, 3))
    arr = blender_to_mc_array(verts)
    for i, v in enumerate(verts):
        assert tuple(arr[i]) == blender_to_mc(v)
    back = mc_to_blender_array(arr)
    assert np.allclose(back, verts)


def test_bad_shape():
    import pytest

    with pytest.raises(ValueError):
        blender_to_mc_array(np.zeros((4, 2)))
