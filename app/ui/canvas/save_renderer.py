from PySide6 import QtCore, QtGui, QtWidgets
import imkit as imk
import numpy as np
from app.path_materialization import ensure_path_materialized
from .text_item import TextBlockItem
from .text.text_item_properties import TextItemProperties
from core.layers import DocumentLayers, LayerGroup, LayerProps


def _as_document_layers(value) -> DocumentLayers:
    if isinstance(value, DocumentLayers):
        return value
    return DocumentLayers.from_dict(value)


def build_text_item(text_block: dict) -> TextBlockItem:
    """A TextBlockItem configured from a saved text state, exactly as the
    canvas draws it. Layer props (visibility, opacity, order) are the caller's
    business: the flat render applies them, a PSD text layer carries them as
    the layer's own props instead."""
    text_props = TextItemProperties.from_dict(text_block)

    text_item = TextBlockItem(
        text=text_props.text,
        font_family=text_props.font_family,
        font_size=text_props.font_size,
        render_color=text_props.text_color,
        alignment=text_props.alignment,
        line_spacing=text_props.line_spacing,
        outline_color=text_props.outline_color,
        outline_width=text_props.outline_width,
        bold=text_props.bold,
        italic=text_props.italic,
        underline=text_props.underline,
        direction=text_props.direction,
    )

    text_item.set_text(text_props.text, text_props.width)
    if text_props.direction:
        text_item.set_direction(text_props.direction)
    if text_props.transform_origin:
        text_item.setTransformOriginPoint(QtCore.QPointF(*text_props.transform_origin))
    text_item.setPos(QtCore.QPointF(*text_props.position))
    text_item.setRotation(text_props.rotation)
    text_item.setScale(text_props.scale)
    text_item.set_vertical(bool(text_props.vertical))
    if text_props.text_color is not None:
        # A project saved before the colour was stored has none, and Qt
        # raises on a null brush rather than ignoring it — which would
        # abandon the whole render, not just this item's colour.
        text_item.set_color(text_props.text_color)
    text_item.selection_outlines = text_props.selection_outlines.copy()
    if text_props.letter_spacing:
        text_item.set_letter_spacing(text_props.letter_spacing)
    text_item.set_shadow(
        text_props.shadow_enabled,
        text_props.shadow_color,
        text_props.shadow_offset,
        text_props.shadow_blur,
    )
    text_item.set_gradient(
        text_props.gradient_enabled,
        text_props.gradient_color,
        text_props.gradient_angle,
    )
    text_item.set_curvature(text_props.curvature)
    return text_item


# Same supersampling as ImageSaveRenderer.render_to_image, so a text layer's
# pixels are the ones the flattened page carries.
_SUPERSAMPLE = 2


def render_text_item_rgba(text_block: dict, width: int, height: int):
    """One text item on its own, as straight-alpha RGBA cropped to its pixels.

    Returns ``(top, left, rgba)`` in page coordinates, or None when the item
    draws nothing on the page. This is what a PSD type layer carries as its
    cached raster: Photopea (and any reader that does not re-run Photoshop's
    text engine) shows exactly these pixels until the text is edited.

    Only the item's own neighbourhood is rendered, not the page: a webtoon
    strip is tens of thousands of pixels tall, and a supersampled full-page
    buffer per text item would be gigabytes.
    """
    scene = QtWidgets.QGraphicsScene()
    scene.setSceneRect(0, 0, width, height)
    item = build_text_item(text_block)
    scene.addItem(item)

    # Shadows and outlines can reach past the item's own rect; pad generously
    # and crop to the pixels actually drawn afterwards.
    pad = 8.0
    if getattr(item, "shadow_enabled", False):
        dx, dy = getattr(item, "shadow_offset", (0.0, 0.0))
        pad += float(getattr(item, "shadow_blur", 0) or 0) + abs(float(dx)) + abs(float(dy))
    region = item.sceneBoundingRect().adjusted(-pad, -pad, pad, pad)
    region = region.intersected(QtCore.QRectF(0, 0, width, height))
    left, top = int(region.left()), int(region.top())
    right = min(width, int(np.ceil(region.right())))
    bottom = min(height, int(np.ceil(region.bottom())))
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None

    image = QtGui.QImage(w * _SUPERSAMPLE, h * _SUPERSAMPLE, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
    scene.render(
        painter,
        QtCore.QRectF(0, 0, image.width(), image.height()),
        QtCore.QRectF(left, top, w, h),
    )
    painter.end()

    image = image.scaled(
        w, h, QtCore.Qt.AspectRatioMode.IgnoreAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation
    ).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    rgba = np.array(image.constBits(), dtype=np.uint8).reshape(h, image.bytesPerLine())[:, : w * 4]
    rgba = rgba.reshape(h, w, 4).copy()

    ys, xs = np.nonzero(rgba[:, :, 3])
    if ys.size == 0:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return top + int(y0), left + int(x0), np.ascontiguousarray(rgba[y0:y1, x0:x1])


class ImageSaveRenderer:
    """Renders a page to a flat image: the artwork, its patches and its text.

    Layer props decide what is drawn, the same way they do on the canvas: a
    hidden object (or an object in a hidden group) is not drawn, and opacity
    applies. ``document_layers`` is the document's group props; objects carry
    their own under their ``"layer"`` key.
    """

    def __init__(self, image: np.ndarray, document_layers=None):
        self.rgb_image = image
        self.layers = _as_document_layers(document_layers)
        self.scene = QtWidgets.QGraphicsScene()

        self.qimage = self.img_array_to_qimage(image)
        # Create a QGraphicsPixmapItem with the QPixmap
        self.pixmap = QtGui.QPixmap.fromImage(self.qimage)
        self.pixmap_item = QtWidgets.QGraphicsPixmapItem(self.pixmap)

        # Set scene size to match image
        self.scene.setSceneRect(0, 0, self.qimage.width(), self.qimage.height())

        # Add QGraphicsPixmapItem to the scene
        self.scene.addItem(self.pixmap_item)

        # Raw Image group. Patches are children of this item, so a hidden raw
        # image is faded to 0 rather than setVisible(False), which would hide
        # them too; they ignore their parent's opacity (see apply_patches).
        raw = self.layers.effective(LayerGroup.RAW)
        self.pixmap_item.setOpacity(raw.opacity if raw.visible else 0.0)


    def img_array_to_qimage(self, rgb_img: np.ndarray) -> QtGui.QImage:
        height, width, channel = rgb_img.shape
        bytes_per_line = channel * width
        return QtGui.QImage(rgb_img.data, width, height, bytes_per_line, QtGui.QImage.Format.Format_RGB888)

    def add_state_to_image(self, state, page_idx=None, main_page=None):
        # Add spanning text items if we have the context to do so
        if page_idx is not None and main_page is not None:
            self.add_spanning_text_items(state, page_idx, main_page)

        for text_block in state.get('text_items_state', []):
            eff = self.layers.effective(LayerGroup.TEXT, text_block.get('layer'))
            if not eff.visible:
                continue  # hidden on the canvas, so not in the flat export
            text_item = build_text_item(text_block)
            text_item.setOpacity(eff.opacity)
            order = LayerProps.from_dict(text_block.get('layer')).z
            if order:
                text_item.setZValue(text_item.zValue() + order * 1e-4)
            text_item.update()

            self.scene.addItem(text_item)

    def add_spanning_text_items(self, viewer_state, page_idx, main_page):
        """
        Add text items from spanning blocks that should appear on this page.
        This function uses 'positional clipping': it places the full text item
        on the render canvas but adjusts its position so that the parts outside
        the intended visible area are positioned off-canvas and are clipped by
        the renderer.
        """
        existing_text_items = viewer_state.get('text_items_state', [])

        current_image_path = main_page.image_files[page_idx]
        ensure_path_materialized(current_image_path)
        
        try:
            current_image = imk.read_image(current_image_path)
        except Exception as e:
            print(f"Warning: Could not read current image {current_image_path}: {e}")
            return
            
        current_page_height = current_image.shape[0]

        for other_page_idx, other_image_path in enumerate(main_page.image_files):
            if other_page_idx == page_idx:
                continue

            if not main_page.image_states.has_page(other_image_path):
                continue

            page_gap = page_idx - other_page_idx
            if abs(page_gap) != 1:  # Only check adjacent pages
                continue

            ensure_path_materialized(other_image_path)
            
            # SAFETY: Check if file exists and is readable before trying to load
            try:
                other_image = imk.read_image(other_image_path)
            except Exception as e:
                print(f"Warning: Could not read adjacent image {other_image_path}: {e}")
                print(f"  Skipping spanning text items from page {other_page_idx} to page {page_idx}")
                continue
                
            other_page_height = other_image.shape[0]
            other_viewer_state = main_page.image_states.viewer_state(other_image_path, {})
            other_text_items = other_viewer_state.get('text_items_state', [])

            if not other_text_items:
                continue

            for text_item in other_text_items:
                pos = text_item.get('position', (0, 0))
                item_x1, item_y1 = pos
                height = self._resolve_text_item_height(text_item)
                if height is None:
                    continue
                item_y2 = item_y1 + height

                new_pos = None

                if page_gap == 1:  # Current page is BELOW other page
                    # Check if text from the page above extends below its bottom boundary
                    if item_y2 > other_page_height:
                        # Position the item on the current page's canvas such that its top is
                        # shifted up by the height of the portion visible on the page above.
                        # This makes the renderer clip the top and show only the overflowing bottom part.
                        new_y = -(other_page_height - item_y1)
                        new_pos = (item_x1, new_y)

                elif page_gap == -1:  # Current page is ABOVE other page
                    # Check if text from the page below extends above its top boundary (i.e., has a negative y)
                    if item_y1 < 0:
                        # Position the item on the current page's canvas so its top aligns with where
                        # it should appear at the bottom of the current page. The renderer will clip the
                        # rest of the text block that falls below the page's bottom boundary.
                        new_y = current_page_height + item_y1
                        new_pos = (item_x1, new_y)

                if new_pos:
                    # Create a new text item state for the spanning portion.
                    # It's a full copy, but its position causes clipping.
                    spanning_text_item = text_item.copy()
                    spanning_text_item['position'] = new_pos
                    existing_text_items.append(spanning_text_item)

        viewer_state['text_items_state'] = existing_text_items

    def _resolve_text_item_height(self, text_item_state):
        height = text_item_state.get('height')
        if isinstance(height, (int, float)):
            return float(height)

        try:
            text_props = TextItemProperties.from_dict(text_item_state)
            text_item = TextBlockItem(
                text=text_props.text,
                font_family=text_props.font_family,
                font_size=text_props.font_size,
                render_color=text_props.text_color,
                alignment=text_props.alignment,
                line_spacing=text_props.line_spacing,
                outline_color=text_props.outline_color,
                outline_width=text_props.outline_width,
                bold=text_props.bold,
                italic=text_props.italic,
                underline=text_props.underline,
                direction=text_props.direction,
            )
            text_item.set_text(text_props.text, text_props.width)
            if text_props.direction:
                text_item.set_direction(text_props.direction)
            text_item.set_vertical(bool(text_props.vertical))
            if text_props.text_color is not None:
                # A project saved before the colour was stored has none, and Qt
                # raises on a null brush rather than ignoring it — which would
                # abandon the whole render, not just this item's colour.
                text_item.set_color(text_props.text_color)
            # Spacing changes how the text wraps, so it has to be applied before
            # the height is measured. The shadow does not affect layout.
            if text_props.letter_spacing:
                text_item.set_letter_spacing(text_props.letter_spacing)
            return float(text_item.boundingRect().height())
        except Exception:
            return None

    def render_to_image(self):
        # Create a high-resolution QImage
        scale_factor = 2  # Increase this for higher resolution
        original_size = self.pixmap.size()
        scaled_size = original_size * scale_factor
        
        qimage = QtGui.QImage(scaled_size, QtGui.QImage.Format.Format_ARGB32)
        # White, not transparent: the page is flattened to RGB, where
        # transparent becomes black. With the artwork shown it covers this
        # entirely; with the raw image hidden or faded, the page reads as paper.
        qimage.fill(QtCore.Qt.white)

        # Create a QPainter with antialiasing
        painter = QtGui.QPainter(qimage)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        # Render the scene
        self.scene.render(painter)
        painter.end()

        # Scale down the image to the original size
        qimage = qimage.scaled(original_size, 
                            QtCore.Qt.AspectRatioMode.KeepAspectRatio, 
                            QtCore.Qt.TransformationMode.SmoothTransformation)

        # Convert QImage to RGB numpy array
        qimage = qimage.convertToFormat(QtGui.QImage.Format.Format_RGB888)
        width = qimage.width()
        height = qimage.height()
        bytes_per_line = qimage.bytesPerLine()

        byte_count = qimage.sizeInBytes()
        expected_size = height * bytes_per_line  # bytes per line can include padding

        if byte_count != expected_size:
            print(f"QImage sizeInBytes: {byte_count}, Expected size: {expected_size}")
            print(f"Image dimensions: ({width}, {height}), Format: {qimage.format()}")
            raise ValueError(f"Byte count mismatch: got {byte_count} but expected {expected_size}")

        ptr = qimage.bits()

        # Convert memoryview to a numpy array considering the complete data with padding
        arr = np.array(ptr).reshape((height, bytes_per_line))
        # Exclude the padding bytes, keeping only the relevant image data
        arr = arr[:, :width * 3]
        # Reshape to the correct dimensions without the padding bytes
        arr = arr.reshape((height, width, 3))

        return arr

    def save_image(self, output_path: str):
        final_rgb = self.render_to_image()
        imk.write_image(output_path, final_rgb)

    def apply_patches(self, patches: list[dict]):
        """Apply inpainting patches to the image."""

        for patch in patches:
            eff = self.layers.effective(LayerGroup.PATCHES, patch.get('layer'))
            if not eff.visible:
                continue
            # Extract data from the patch dict
            x, y, w, h = patch['bbox']
            if 'png_path' in patch:
                patch_path = patch['png_path']
                ensure_path_materialized(patch_path)
                patch_image = imk.read_image(patch_path)
            else:
                # Handle direct image data (expected to be RGB format)
                patch_image = patch['image']
            
            # Convert patch to QImage
            patch_qimage = self.img_array_to_qimage(patch_image)
            patch_pixmap = QtGui.QPixmap.fromImage(patch_qimage)
            
            # Create a pixmap item for the patch
            patch_item = QtWidgets.QGraphicsPixmapItem(patch_pixmap, self.pixmap_item)
            
            # Position the patch relative to its parent (pixmap_item)
            patch_item.setPos(x, y)
            patch_item.setZValue(self.pixmap_item.zValue() + 0.5)
            # Its own opacity, not the raw image's (which may be faded out).
            patch_item.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresParentOpacity, True)
            patch_item.setOpacity(eff.opacity)



