"""Morphological operations for the imkit module."""

from __future__ import annotations
import numpy as np
import mahotas as mh


MORPH_RECT = 0
MORPH_CROSS = 1
MORPH_ELLIPSE = 2

MORPH_OPEN = 'open'
MORPH_CLOSE = 'close'
MORPH_GRADIENT = 'gradient'
MORPH_TOPHAT = 'tophat'
MORPH_BLACKHAT = 'blackhat'


def dilate(mask: np.ndarray, kernel: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Apply dilation morphological operation to a mask.
    
    Args:
        mask: Input mask
        kernel: a 2D numpy array kernel
        iterations: Number of iterations
    """
    se = (kernel > 0).astype(bool)
    out = mask.copy()
    for _ in range(iterations):
        out = mh.dilate(out, se)
    return (out > 0).astype(np.uint8)*255


def erode(mask: np.ndarray, kernel: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Apply erosion morphological operation to a mask.
    
    Args:
        mask: Input mask
        kernel: a 2D numpy array kernel
        iterations: Number of iterations
    """
    se = (kernel > 0).astype(bool)
    out = mask.copy()
    for _ in range(iterations):
        out = mh.erode(out, se)
    return (out > 0).astype(np.uint8)*255


def morphology_ex(image: np.ndarray, op: str, kernel: np.ndarray) -> np.ndarray:
    # Convert OpenCV kernel to Mahotas structuring element
    Bc = kernel.astype(bool)
    
    if op == MORPH_OPEN:  # cv2.MORPH_OPEN
        return mh.open(image, Bc)
    elif op == MORPH_CLOSE:  # cv2.MORPH_CLOSE
        return mh.close(image, Bc)
    elif op == MORPH_GRADIENT:  # cv2.MORPH_GRADIENT
        return mh.dilate(image, Bc) - mh.erode(image, Bc)
    elif op == MORPH_TOPHAT:  # cv2.MORPH_TOPHAT
        return image - mh.open(image, Bc)
    elif op == MORPH_BLACKHAT:  # cv2.MORPH_BLACKHAT
        return mh.close(image, Bc) - image
    else:
        raise ValueError(f"Unsupported operation: {op}")
    

def get_structuring_element(shape: int, ksize: tuple) -> np.ndarray:
    """
    OpenCV-like getStructuringElement using Mahotas.

    Parameters
    ----------
    shape : int
        One of MORPH_RECT, MORPH_CROSS, MORPH_ELLIPSE
    ksize : (h, w) tuple
        Size of the structuring element
    """
    h, w = ksize

    if shape == MORPH_RECT:
        # full ones
        elem = np.ones((h, w), dtype=bool)

    elif shape == MORPH_CROSS:
        elem = np.zeros((h, w), dtype=bool)
        elem[h//2, :] = True
        elem[:, w//2] = True

    elif shape == MORPH_ELLIPSE:
        # Rasterised the way OpenCV does it, one row at a time. mh.disk(r) was
        # used here before, but it draws a disk of radius r-1 inside an r*2+1
        # box: a (3, 3) element came out as a single pixel, i.e. an identity
        # kernel that dilated nothing, and every larger one grew a pixel short.
        elem = np.zeros((h, w), dtype=bool)
        r, c = h // 2, w // 2
        inv_r2 = 1.0 / (r * r) if r else 0.0
        for i in range(h):
            dy = i - r
            if abs(dy) > r:
                continue
            dx = int(c * np.sqrt((r * r - dy * dy) * inv_r2) + 0.5) if r else c
            elem[i, max(c - dx, 0):min(c + dx + 1, w)] = True

    else:
        raise ValueError("Unknown shape flag")

    return elem


def close_holes(mask: np.ndarray) -> np.ndarray:
    """Fill holes in a binary mask using Mahotas.
    
    Args:
        mask: Input binary mask
        
    Returns:
        Hole-filled boolean mask
    """
    return mh.close_holes(mask > 0)