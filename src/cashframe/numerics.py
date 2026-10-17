"""Reject corrupt numerical inputs before they can become a negative verdict."""
import numpy as np


def finite_array(value, name, ndim=None):
    array = np.asarray(value, dtype=np.float64)
    if not array.size or not np.isfinite(array).all():
        raise ValueError(f'{name}: empty or non-finite numerical data')
    if ndim is not None and array.ndim != ndim:
        raise ValueError(f'{name}: expected {ndim} dimensions')
    return array


def finite_scalar(value, name, positive=False):
    array = finite_array(value, name)
    if array.size != 1:
        raise ValueError(f'{name}: expected a scalar')
    result = float(array.reshape(-1)[0])
    if positive and result <= 0:
        raise ValueError(f'{name}: must be positive')
    return result
