"""Measured image changes and technical warnings, never object visibility claims."""
import numpy as np


def inspect_sample(image, previous=None, delta_sec=None):
    gray=np.asarray(image,dtype=np.float32).mean(axis=2)/255.
    if gray.ndim!=2 or not np.isfinite(gray).all():raise ValueError('Invalid quality sample')
    dark=float(np.mean(gray<.04));bright=float(np.mean(gray>.96))
    gradient=float((np.mean(np.abs(np.diff(gray,axis=0)))+np.mean(np.abs(np.diff(gray,axis=1))))/2)
    flags=[]
    if dark>.95:flags.append('mostly_dark')
    if bright>.95:flags.append('mostly_bright')
    if gradient<.002:flags.append('low_detail')
    motion=None
    if previous is not None and delta_sec is not None and delta_sec>0:
        motion=float(np.mean(np.abs(gray-previous))/delta_sec)
    return gray,{'image_change_per_sec':motion,'dark_fraction':dark,'bright_fraction':bright,
                 'gradient_mean':gradient,'technical_flags':flags,'object_visibility':'not_assessed'}
