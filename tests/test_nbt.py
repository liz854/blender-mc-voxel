import gzip
import io

import pytest

from core import nbt


def test_roundtrip_all_tags():
    root = nbt.TAGCompound(
        {
            "b": nbt.TAGByte(-3),
            "s": nbt.TAGShort(-300),
            "i": nbt.TAGInt(70000),
            "l": nbt.TAGLong(1 << 40),
            "f": nbt.TAGFloat(0.5),
            "d": nbt.TAGDouble(1.25),
            "str": nbt.TAGString("石头"),
            "ba": nbt.TAGByteArray(b"\x01\x02\x03"),
            "ia": nbt.TAGIntArray([1, -2, 3]),
            "la": nbt.TAGLongArray([1, -1, 1 << 62]),
            "list": nbt.TAGList([nbt.TAGInt(1), nbt.TAGInt(2)], element_tag=nbt.TAG_INT),
            "empty": nbt.TAGList([], element_tag=nbt.TAG_COMPOUND),
            "nested": nbt.TAGCompound({"k": nbt.TAGString("v")}),
        }
    )
    raw = nbt.serialize(root)
    back = nbt.parse(raw)
    assert back["b"] == -3
    assert isinstance(back["b"], nbt.TAGByte)
    assert back["s"] == -300
    assert isinstance(back["s"], nbt.TAGShort)
    assert isinstance(back["i"], nbt.TAGInt)
    assert isinstance(back["l"], nbt.TAGLong)
    assert back["str"] == "石头"
    assert isinstance(back["ba"], nbt.TAGByteArray)
    assert list(back["ia"]) == [1, -2, 3]
    assert list(back["la"]) == [1, -1, 1 << 62]
    assert len(back["list"]) == 2
    assert back["list"].element_tag == nbt.TAG_INT
    assert back["empty"].element_tag == nbt.TAG_COMPOUND
    assert back["nested"]["k"] == "v"


def test_modified_utf8_emoji():
    root = nbt.TAGCompound({"s": nbt.TAGString("方块😁🚀")})
    back = nbt.parse(nbt.serialize(root))
    assert back["s"] == "方块😁🚀"


def test_modified_utf8_nul():
    root = nbt.TAGCompound({"s": nbt.TAGString("a\x00b")})
    back = nbt.parse(nbt.serialize(root))
    assert back["s"] == "a\x00b"


def test_bare_python_types_inferred():
    root = nbt.TAGCompound({"n": 5, "f": 1.5, "s": "x", "raw": b"\x00", "sub": {"a": 1}})
    back = nbt.parse(nbt.serialize(root))
    assert isinstance(back["n"], nbt.TAGInt)
    assert isinstance(back["f"], nbt.TAGDouble)
    assert isinstance(back["raw"], nbt.TAGByteArray)
    assert isinstance(back["sub"], nbt.TAGCompound)


def test_gzip_file_roundtrip(tmp_path):
    path = tmp_path / "x.nbt"
    root = nbt.TAGCompound({"v": nbt.TAGInt(7)})
    nbt.write_nbt(str(path), root)
    assert path.read_bytes()[:2] == b"\x1f\x8b"
    assert nbt.read_nbt(str(path))["v"] == 7


def test_truncated_raises():
    raw = nbt.serialize(nbt.TAGCompound({"v": nbt.TAGInt(7)}))
    with pytest.raises(ValueError):
        nbt.parse(raw[:-3])


def test_root_must_be_compound():
    with pytest.raises(ValueError):
        nbt.parse(b"\x03\x00\x00\x00\x00\x00\x07")
