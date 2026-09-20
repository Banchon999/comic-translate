"""PageStateStore survives the real controller and a project save/load round-trip.

These lean on the actual controller and the v2 project serializer rather than a
stub, because the two things they must prove — that a fresh controller and a
loaded project both back state with a PageStateStore, and that Slice 0 object_ids
survive the round-trip — are exactly the integration points a unit stub would
paper over.
"""

import numpy as np
import pytest

from app.projects.page_state_store import PageStateStore
from modules.utils.textblock import TextBlock


@pytest.fixture
def controller(qapp):
    import controller as controller_mod

    win = controller_mod.ComicTranslate()
    yield win
    win.close()


def test_fresh_controller_uses_page_state_store(controller):
    assert isinstance(controller.image_states, PageStateStore)


def test_project_roundtrip_reconstructs_store_and_preserves_object_ids(controller, tmp_path):
    from app.projects.project_state_v2 import (
        save_state_to_proj_file_v2,
        load_state_from_proj_file_v2,
    )

    # One real page on disk so the serializer can store its image blob.
    page = tmp_path / "001.png"
    from PIL import Image
    Image.fromarray(np.full((40, 60, 3), 200, dtype=np.uint8)).save(page)
    page_path = str(page)

    blk = TextBlock(text="hello")
    blk.xyxy = np.array([1, 2, 30, 20])
    controller.image_files = [page_path]
    controller.image_data = {page_path: np.full((40, 60, 3), 200, dtype=np.uint8)}
    controller.image_states = PageStateStore({
        page_path: PageStateStore.build_page_state(
            viewer_state={"text_items_state": [{"object_id": "TXT-1", "text": "<p>hi</p>",
                                                 "position": (1, 2)}]},
            source_lang="Korean", target_lang="English",
            brush_strokes=[], blk_list=[blk], skip=False,
            export_group_name="ch1",
        )
    })
    blk_id = blk.object_id

    proj = tmp_path / "proj.ctpr"
    save_state_to_proj_file_v2(controller, str(proj))

    # Load into a second controller instance.
    import controller as controller_mod
    other = controller_mod.ComicTranslate()
    try:
        load_state_from_proj_file_v2(other, str(proj))
        # The backing state is a PageStateStore, not a bare dict.
        assert isinstance(other.image_states, PageStateStore)

        loaded_paths = list(other.image_states.paths()) if hasattr(other.image_states, "paths") else list(other.image_states.keys())
        assert len(loaded_paths) == 1
        loaded = other.image_states.get_page_state(loaded_paths[0])

        # Slice 0 identities survived save -> load.
        assert loaded["blk_list"][0].object_id == blk_id
        assert loaded["viewer_state"]["text_items_state"][0]["object_id"] == "TXT-1"
        # And the languages came through the canonical shape intact.
        assert other.image_states.languages(loaded_paths[0]) == ("Korean", "English")
    finally:
        other.close()
