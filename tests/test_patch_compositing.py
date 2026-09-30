"""Overlapping patches compose in the order the scene draws them.

`get_image_array(include_patches=True)` (and its webtoon twin) is what OCR,
inpainting and every pixel tool read. It walked `scene.items()`, which lists
the topmost item first, so wherever two patches overlapped the *older* one
was composited last: the screen showed the newer patch while every consumer
read the older one. A second clean over the same area, or the restore eraser
over a cleaned area, then worked from stale pixels.
"""

import numpy as np
from PySide6.QtGui import QImage, QPixmap


def _patch(viewer, x, y, w, h, colour, tag):
    image = QImage(w, h, QImage.Format.Format_RGB888)
    image.fill(0)
    pixmap = QPixmap.fromImage(image)
    pixmap.fill(colour)
    item = viewer._scene.addPixmap(pixmap)
    item.setPos(x, y)
    item.setZValue(0.5)
    item.setData(0, tag)   # the patch hash slot: marks it as a patch
    return item


def test_the_newer_of_two_overlapping_patches_wins(qapp):
    from PySide6.QtGui import QColor

    from app.ui.canvas.image_viewer import ImageViewer

    viewer = ImageViewer(None)
    try:
        viewer.display_image_array(np.full((60, 80, 3), 128, np.uint8))
        _patch(viewer, 10, 10, 40, 30, QColor(255, 0, 0), "older")
        _patch(viewer, 30, 20, 40, 30, QColor(0, 0, 255), "newer")
        image = viewer.get_image_array(include_patches=True)
        assert tuple(image[25, 40]) == (0, 0, 255), "the overlap shows the newer patch"
        assert tuple(image[15, 15]) == (255, 0, 0)
        assert tuple(image[55, 5]) == (128, 128, 128)
    finally:
        viewer.close()
