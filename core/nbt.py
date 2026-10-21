"""NBT 读写（tag 0-12，大端序，modified UTF-8，gzip）。

类型映射（全部是 Python 内建类型的子类，方便 round-trip 保真）:

    TAGByte(int) TAGShort(int) TAGInt(int) TAGLong(int)
    TAGFloat(float) TAGDouble(float) TAGString(str)
    TAGByteArray(bytes) TAGIntArray(list) TAGLongArray(list)
    TAGList(list) TAGCompound(dict)

裸 int / float / str / bytes / dict / list 也能写，按下面的规则推断：
    bool -> Byte, int -> Int, float -> Double, str -> String,
    bytes -> ByteArray, dict -> Compound, list -> List, np.int64 数组 -> LongArray
要写 Long/Float/Short 这类有歧义的类型，必须显式用 TAGLong 等包装。
"""

from __future__ import annotations

import gzip
import io
import struct

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


# --------------------------------------------------------------------------
# tagged value types
# --------------------------------------------------------------------------
class TAGByte(int):
    tag_id = TAG_BYTE


class TAGShort(int):
    tag_id = TAG_SHORT


class TAGInt(int):
    tag_id = TAG_INT


class TAGLong(int):
    tag_id = TAG_LONG


class TAGFloat(float):
    tag_id = TAG_FLOAT


class TAGDouble(float):
    tag_id = TAG_DOUBLE


class TAGString(str):
    tag_id = TAG_STRING


class TAGByteArray(bytes):
    tag_id = TAG_BYTE_ARRAY


class TAGIntArray(list):
    tag_id = TAG_INT_ARRAY


class TAGLongArray(list):
    tag_id = TAG_LONG_ARRAY


class TAGList(list):
    tag_id = TAG_LIST

    def __init__(self, items=None, element_tag=TAG_END):
        super().__init__(items or [])
        # 元素类型：显式给，或从第一个元素推断；空列表按 0（End）
        if element_tag == TAG_END and len(self) > 0:
            element_tag = tag_id_of(self[0])
        self.element_tag = element_tag


class TAGCompound(dict):
    tag_id = TAG_COMPOUND


def tag_id_of(value) -> int:
    """推断一个 Python 值的 NBT tag id。"""
    tid = getattr(value, "tag_id", None)
    if tid is not None:
        return tid
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, (bytes, bytearray)):
        return TAG_BYTE_ARRAY
    if isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, (list, tuple)):
        return TAG_LIST
    raise TypeError(f"无法推断 NBT 类型：{type(value)!r}")


# --------------------------------------------------------------------------
# modified UTF-8
# --------------------------------------------------------------------------
def _enc_modified_utf8(s: str) -> bytes:
    out = bytearray()
    for ch in s:
        cp = ord(ch)
        if cp == 0:
            out += b"\xc0\x80"
        elif cp < 0x80:
            out.append(cp)
        elif cp < 0x800:
            out.append(0xC0 | (cp >> 6))
            out.append(0x80 | (cp & 0x3F))
        elif cp < 0x10000:
            out.append(0xE0 | (cp >> 12))
            out.append(0x80 | ((cp >> 6) & 0x3F))
            out.append(0x80 | (cp & 0x3F))
        else:  # 增补平面 -> CESU-8 代理对
            cp -= 0x10000
            for surrogate in (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)):
                out.append(0xE0 | (surrogate >> 12))
                out.append(0x80 | ((surrogate >> 6) & 0x3F))
                out.append(0x80 | (surrogate & 0x3F))
    return bytes(out)


def _dec_modified_utf8(raw: bytes) -> str:
    units = []
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b < 0x80:
            units.append(b)
            i += 1
        elif b >> 5 == 0b110:
            units.append(((b & 0x1F) << 6) | (raw[i + 1] & 0x3F))
            i += 2
        elif b >> 4 == 0b1110:
            units.append(((b & 0x0F) << 12) | ((raw[i + 1] & 0x3F) << 6) | (raw[i + 2] & 0x3F))
            i += 3
        else:
            raise ValueError(f"非法 modified UTF-8 头字节 0x{b:02x}")
    # 合并代理对
    out = []
    j = 0
    while j < len(units):
        u = units[j]
        if 0xD800 <= u <= 0xDBFF and j + 1 < len(units) and 0xDC00 <= units[j + 1] <= 0xDFFF:
            cp = 0x10000 + ((u - 0xD800) << 10) + (units[j + 1] - 0xDC00)
            out.append(chr(cp))
            j += 2
        else:
            out.append(chr(u))
            j += 1
    return "".join(out)


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.d):
            raise ValueError("NBT 数据被截断")
        chunk = self.d[self.i : self.i + n]
        self.i += n
        return chunk

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        length = self.unpack(">H")
        return _dec_modified_utf8(self.take(length))

    def payload(self, tid: int):
        if tid == TAG_BYTE:
            return TAGByte(self.unpack(">b"))
        if tid == TAG_SHORT:
            return TAGShort(self.unpack(">h"))
        if tid == TAG_INT:
            return TAGInt(self.unpack(">i"))
        if tid == TAG_LONG:
            return TAGLong(self.unpack(">q"))
        if tid == TAG_FLOAT:
            return TAGFloat(self.unpack(">f"))
        if tid == TAG_DOUBLE:
            return TAGDouble(self.unpack(">d"))
        if tid == TAG_BYTE_ARRAY:
            return TAGByteArray(self.take(self.unpack(">i")))
        if tid == TAG_STRING:
            return TAGString(self.string())
        if tid == TAG_LIST:
            elem = self.unpack(">b")
            count = self.unpack(">i")
            return TAGList([self.payload(elem) for _ in range(count)], element_tag=elem)
        if tid == TAG_COMPOUND:
            out = TAGCompound()
            while True:
                child = self.unpack(">b")
                if child == TAG_END:
                    return out
                name = self.string()
                out[name] = self.payload(child)
        if tid == TAG_INT_ARRAY:
            count = self.unpack(">i")
            return TAGIntArray([self.unpack(">i") for _ in range(count)])
        if tid == TAG_LONG_ARRAY:
            count = self.unpack(">i")
            return TAGLongArray([self.unpack(">q") for _ in range(count)])
        raise ValueError(f"未知或非法的 tag id: {tid}")


def parse(data: bytes) -> TAGCompound:
    """解析未压缩的 NBT 字节串，返回根 Compound。"""
    r = _Reader(data)
    tid = r.unpack(">b")
    if tid != TAG_COMPOUND:
        raise ValueError(f"根 tag 必须是 Compound，收到 {tid}")
    r.string()  # 根名字，通常为空
    return r.payload(TAG_COMPOUND)


# --------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------
def _write_string(out: bytearray, s: str) -> None:
    raw = _enc_modified_utf8(s)
    if len(raw) > 0xFFFF:
        raise ValueError("字符串超过 65535 字节")
    out += struct.pack(">H", len(raw)) + raw


def _write_payload(out: bytearray, value, tid: int) -> None:
    if tid == TAG_BYTE:
        out += struct.pack(">b", int(value))
    elif tid == TAG_SHORT:
        out += struct.pack(">h", int(value))
    elif tid == TAG_INT:
        out += struct.pack(">i", int(value))
    elif tid == TAG_LONG:
        out += struct.pack(">q", int(value))
    elif tid == TAG_FLOAT:
        out += struct.pack(">f", float(value))
    elif tid == TAG_DOUBLE:
        out += struct.pack(">d", float(value))
    elif tid == TAG_BYTE_ARRAY:
        raw = bytes(value)
        out += struct.pack(">i", len(raw)) + raw
    elif tid == TAG_STRING:
        _write_string(out, str(value))
    elif tid == TAG_LIST:
        elem = getattr(value, "element_tag", TAG_END)
        if elem == TAG_END and len(value) > 0:
            elem = tag_id_of(value[0])
        out += struct.pack(">bi", elem, len(value))
        for item in value:
            _write_payload(out, item, elem)
    elif tid == TAG_COMPOUND:
        for name, child in value.items():
            cid = tag_id_of(child)
            out += struct.pack(">b", cid)
            _write_string(out, name)
            _write_payload(out, child, cid)
        out += struct.pack(">b", TAG_END)
    elif tid == TAG_INT_ARRAY:
        out += struct.pack(">i", len(value))
        for v in value:
            out += struct.pack(">i", int(v))
    elif tid == TAG_LONG_ARRAY:
        out += struct.pack(">i", len(value))
        for v in value:
            out += struct.pack(">q", int(v))
    else:
        raise ValueError(f"无法写入的 tag id: {tid}")


def serialize(root: dict, root_name: str = "") -> bytes:
    """序列化成未压缩 NBT 字节串。"""
    out = bytearray()
    out += struct.pack(">b", TAG_COMPOUND)
    _write_string(out, root_name)
    _write_payload(out, root, TAG_COMPOUND)
    return bytes(out)


# --------------------------------------------------------------------------
# gzip 便捷入口
# --------------------------------------------------------------------------
def write_nbt(path: str, root: dict, root_name: str = "") -> None:
    """写 gzip 包裹的 NBT 文件。"""
    raw = serialize(root, root_name)
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as gz:
            gz.write(raw)


def read_nbt(path: str) -> TAGCompound:
    """读 gzip 包裹（或裸 NBT）文件。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return parse(data)


def read_nbt_bytes(data: bytes) -> TAGCompound:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return parse(data)


def loads(data: bytes):
    """兼容别名。"""
    return read_nbt_bytes(data)


def dumps(root: dict, root_name: str = "") -> bytes:
    return serialize(root, root_name)


def to_plain(value):
    """把 tagged 结构转成纯 Python（调试打印用）。"""
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, float):
        return float(value)
    if isinstance(value, int):
        return int(value)
    return value


def dumps_hex(data: bytes, n: int = 64) -> str:
    return data[:n].hex()


__all__ = [
    "TAGByte", "TAGShort", "TAGInt", "TAGLong", "TAGFloat", "TAGDouble",
    "TAGString", "TAGByteArray", "TAGIntArray", "TAGLongArray",
    "TAGList", "TAGCompound",
    "parse", "serialize", "write_nbt", "read_nbt", "read_nbt_bytes",
    "loads", "dumps", "to_plain", "tag_id_of",
]
