"""The Stitch page: sources in, pages out, and how it is reached from the window.

Without a window the page runs its jobs inline, so these tests exercise the
real load → plan → write path synchronously; the window test checks the task
runner path and the nav wiring.
"""

import os
import types

import numpy as np
import pytest
from PIL import Image


def slice_image(height, width=300, panels=(), seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((height, width, 3), 255, np.uint8)
    for top, bottom in panels:
        img[top:bottom, 15:width - 15] = rng.integers(0, 255, (bottom - top, width - 30, 3), dtype=np.uint8)
    return img


@pytest.fixture
def slices_dir(tmp_path):
    folder = tmp_path / "raw"
    folder.mkdir()
    # Named so that plain string order is wrong (10 before 2).
    specs = {"1.png": (600, [(50, 550)]), "2.png": (600, [(50, 600)]), "10.png": (600, [(0, 400)])}
    for i, (name, (h, panels)) in enumerate(specs.items()):
        Image.fromarray(slice_image(h, panels=panels, seed=i)).save(folder / name)
    return folder


@pytest.fixture
def page(qapp):
    from app.ui.stitch_page import StitchPage

    widget = StitchPage()
    widget.apply_theme(True)
    widget.height_spin.setValue(700)
    yield widget
    widget.deleteLater()


def names(page):
    return [os.path.basename(s.path) for s in page.sources()]


def test_a_folder_is_added_in_natural_order(page, slices_dir):
    assert page.add_folder(str(slices_dir)) == 3
    assert names(page) == ["1.png", "2.png", "10.png"]
    assert page.output_edit.text() == os.path.join(str(slices_dir), "stitched")


def test_non_images_are_skipped(page, slices_dir):
    (slices_dir / "notes.txt").write_text("x")
    page.add_files([str(slices_dir / "notes.txt"), str(slices_dir / "1.png")])
    assert names(page) == ["1.png"]


def test_sort_remove_and_clear(page, slices_dir):
    page.add_files([str(slices_dir / n) for n in ("10.png", "1.png", "2.png")])
    page.sort_by_name()
    assert names(page) == ["1.png", "2.png", "10.png"]
    page.source_list.item(1).setSelected(True)
    page.remove_selected()
    assert names(page) == ["1.png", "10.png"]
    page.clear_sources()
    assert page.sources() == [] and not page.preview_button.isEnabled()


def test_preview_shows_the_cuts_without_writing(page, slices_dir, tmp_path):
    page.add_folder(str(slices_dir))
    page.output_edit.setText(str(tmp_path / "out"))
    page.run_preview()
    assert page._plan is not None and page._plan.total_height == 1800
    assert page.preview_canvas.has_pages()
    assert "page" in page.summary_label.text()
    assert not (tmp_path / "out").exists()


def test_stitch_writes_pages_that_rebuild_the_strip(page, slices_dir, tmp_path):
    page.add_folder(str(slices_dir))
    out = tmp_path / "out"
    page.output_edit.setText(str(out))
    page.prefix_edit.setText("ch1_")
    page.run_stitch()
    written = sorted(os.listdir(out))
    assert written and all(n.startswith("ch1_") and n.endswith(".png") for n in written)
    strip = np.concatenate([np.asarray(Image.open(slices_dir / n).convert("RGB")) for n in ("1.png", "2.png", "10.png")])
    back = np.concatenate([np.asarray(Image.open(out / n).convert("RGB")) for n in written])
    assert np.array_equal(back, strip)
    assert page.open_project_button.isEnabled()
    opened = []
    page.open_pages.connect(opened.append)
    page.open_project_button.click()
    assert [os.path.basename(p) for p in opened[0]] == written


def test_changing_a_setting_drops_the_stale_preview(page, slices_dir):
    page.add_folder(str(slices_dir))
    page.run_preview()
    page.height_spin.setValue(900)
    assert page._plan is None and not page.preview_canvas.has_pages()


def test_project_pages_count_as_they_stand_in_the_editor(qapp, slices_dir, tmp_path):
    from app.ui.stitch_page import StitchPage

    edited = np.zeros((600, 300, 3), np.uint8)  # an unsaved edit of 2.png
    paths = [str(slices_dir / n) for n in ("1.png", "2.png")]
    main = types.SimpleNamespace(image_files=paths, image_data={paths[1]: edited}, image_history={})
    widget = StitchPage(main=main)
    try:
        widget.height_spin.setValue(0)
        assert widget.from_project_button.isEnabled()
        widget.use_project_pages()
        widget.output_edit.setText(str(tmp_path / "out"))
        widget.run_stitch()
        (only,) = os.listdir(tmp_path / "out")
        back = np.asarray(Image.open(tmp_path / "out" / only).convert("RGB"))
        assert back.shape[0] == 1200
        assert (back[600:] == 0).all()  # the edit, not the file on disk
    finally:
        widget.deleteLater()


def test_settings_persist(qapp):
    from app.ui.stitch_page import StitchPage

    first = StitchPage()
    first.height_spin.setValue(3200)
    first.format_combo.setCurrentIndex(first.format_combo.findData("jpg"))
    first.deleteLater()
    second = StitchPage()
    try:
        assert second.height_spin.value() == 3200
        assert second.format_combo.currentData() == "jpg"
        assert second.quality_spin.isEnabled()
    finally:
        second.format_combo.setCurrentIndex(second.format_combo.findData("png"))
        second.height_spin.setValue(5000)
        second.deleteLater()


def test_the_theme_colours_the_cut_legend(page):
    from core import theme_tokens

    page.apply_theme(False)
    assert page.preview_canvas._colours["edge"] == theme_tokens.LIGHT["accent"]
    page.apply_theme(True)
    assert theme_tokens.DARK["warn"].lower() in page.legend_forced.styleSheet().lower()


def test_the_nav_rail_opens_the_stitch_page(qapp, slices_dir, tmp_path):
    from PySide6 import QtCore

    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    try:
        win.stitch_nav_button.click()
        assert win._center_stack.currentWidget() is win.stitch_page
        assert win.stitch_nav_button.isChecked() and not win.settings_nav_button.isChecked()
        win.settings_nav_button.click()
        assert win._center_stack.currentWidget() is win.settings_page

        # The task-runner path: the job runs on a worker and reports back.
        win.show_stitch_page()
        sp = win.stitch_page
        sp.height_spin.setValue(700)
        sp.add_folder(str(slices_dir))
        sp.output_edit.setText(str(tmp_path / "out"))
        sp.run_stitch()
        timer = QtCore.QElapsedTimer()
        timer.start()
        while not sp._written and timer.elapsed() < 20000:
            qapp.processEvents()
        assert sp._written and all(os.path.isfile(p) for p in sp._written)
        assert not sp._busy

        # "Open as New Project" loads the pages and lands in the editor.
        sp.open_project_button.click()
        timer.start()
        while not win.image_viewer.hasPhoto() and timer.elapsed() < 20000:
            qapp.processEvents()
        assert win.image_files and [os.path.basename(p) for p in win.image_files] == [
            os.path.basename(p) for p in sp._written
        ]
        assert win._center_stack.currentWidget() is win.main_content_widget
    finally:
        win._skip_close_prompt = True
        win.close()
