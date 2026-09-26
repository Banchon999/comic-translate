"""Inpaint patches keep one logical identity (Slice 2C).

Before this, ``create_patch_item`` minted a fresh ``object_id`` every time it
drew a patch and nothing stored it, so the same patch had a different id after
each undo/redo, page switch, webtoon lazy load or project reload. The later
layer work gives every patch its own layer props keyed by that id, so it has to
be stable. The content ``hash`` stays what it was: the dedup key.
"""

import numpy as np
import pytest
from PIL import Image

from app.ui.commands.base import OBJECT_ID_KEY


@pytest.fixture
def controller(qapp, tmp_path):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    page = tmp_path / "001.png"
    Image.fromarray(np.full((40, 60, 3), 200, dtype=np.uint8)).save(page)
    win.image_files = [str(page)]
    win.image_data = {str(page): np.full((40, 60, 3), 200, dtype=np.uint8)}
    yield win
    win.close()


def _patch():
    return {"bbox": [2, 3, 8, 6], "image": np.full((6, 8, 3), 90, dtype=np.uint8)}


def _scene_patch_ids(viewer):
    from PySide6.QtWidgets import QGraphicsPixmapItem
    return [
        it.data(OBJECT_ID_KEY)
        for it in viewer._scene.items()
        if isinstance(it, QGraphicsPixmapItem) and it.data(0)
    ]


def test_insert_stores_one_id_and_draws_it(controller):
    from app.ui.commands.inpaint import PatchInsertCommand

    page = controller.image_files[0]
    cmd = PatchInsertCommand(controller, [_patch()], page)
    cmd.redo()

    stored = controller.image_patches[page][0]["object_id"]
    assert stored
    assert controller.in_memory_patches[page][0]["object_id"] == stored
    assert _scene_patch_ids(controller.image_viewer) == [stored]


def test_undo_redo_redraws_the_same_id(controller):
    from app.ui.commands.inpaint import PatchInsertCommand

    page = controller.image_files[0]
    cmd = PatchInsertCommand(controller, [_patch()], page)
    cmd.redo()
    first = controller.image_patches[page][0]["object_id"]

    cmd.undo()
    assert controller.image_patches[page] == []
    assert _scene_patch_ids(controller.image_viewer) == []

    cmd.redo()
    assert controller.image_patches[page][0]["object_id"] == first
    assert _scene_patch_ids(controller.image_viewer) == [first]


def test_v2_roundtrip_keeps_patch_id(controller, tmp_path):
    import controller as controller_mod
    from app.ui.commands.inpaint import PatchInsertCommand
    from app.projects.project_state_v2 import (
        save_state_to_proj_file_v2,
        load_state_from_proj_file_v2,
    )

    page = controller.image_files[0]
    PatchInsertCommand(controller, [_patch()], page).redo()
    saved_id = controller.image_patches[page][0]["object_id"]

    proj = tmp_path / "proj.ctpr"
    save_state_to_proj_file_v2(controller, str(proj))

    other = controller_mod.ComicTranslate()
    try:
        load_state_from_proj_file_v2(other, str(proj))
        (plist,) = other.image_patches.values()
        assert plist[0]["object_id"] == saved_id
        assert plist[0]["hash"] == controller.image_patches[page][0]["hash"]
    finally:
        other.close()


def test_v2_patch_saved_without_an_id_gets_one_that_persists(controller, tmp_path):
    """A project written before patches carried an id: load mints one, and the
    next save writes it, so a second reload sees the same id."""
    import controller as controller_mod
    from app.ui.commands.inpaint import PatchInsertCommand
    from app.projects.project_state_v2 import (
        save_state_to_proj_file_v2,
        load_state_from_proj_file_v2,
    )

    page = controller.image_files[0]
    PatchInsertCommand(controller, [_patch()], page).redo()
    del controller.image_patches[page][0]["object_id"]  # old-format entry

    first = tmp_path / "old.ctpr"
    save_state_to_proj_file_v2(controller, str(first))

    a = controller_mod.ComicTranslate()
    b = controller_mod.ComicTranslate()
    try:
        load_state_from_proj_file_v2(a, str(first))
        (plist,) = a.image_patches.values()
        minted = plist[0]["object_id"]
        assert minted

        second = tmp_path / "resaved.ctpr"
        save_state_to_proj_file_v2(a, str(second))
        load_state_from_proj_file_v2(b, str(second))
        (plist_b,) = b.image_patches.values()
        assert plist_b[0]["object_id"] == minted
    finally:
        a.close()
        b.close()
