"""sRGB -> CIELAB(D65)，delta_e76。纯 numpy。"""

from __future__ import annotations

import numpy as np

# D65 白点
_XN, _YN, _ZN = 0.95047, 1.0, 1.08883
_KAPPA = 24389.0 / 27.0
_EPS = 216.0 / 24389.0

_M = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)


def srgb_to_linear(rgb):
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _f(t):
    return np.where(t > _EPS, np.cbrt(t), (_KAPPA * t + 16.0) / 116.0)


def srgb_to_lab(rgb):
    """(…,3) sRGB 0-255 -> (…,3) Lab。单点输入也接受。"""
    arr = np.asarray(rgb, dtype=np.float64)
    single = arr.ndim == 1
    lin = srgb_to_linear(arr)
    xyz = lin @ _M.T
    xyz[..., 0] /= _XN
    xyz[..., 1] /= _YN
    xyz[..., 2] /= _ZN
    fx, fy, fz = _f(xyz[..., 0]), _f(xyz[..., 1]), _f(xyz[..., 2])
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    lab = np.stack([L, a, b], axis=-1)
    return lab[()] if single else lab


def delta_e76(lab1, lab2):
    """(…,3) x (…,3) 欧氏距离。"""
    d = np.asarray(lab1, dtype=np.float64) - np.asarray(lab2, dtype=np.float64)
    return np.sqrt(np.einsum("...i,...i->...", d, d))
