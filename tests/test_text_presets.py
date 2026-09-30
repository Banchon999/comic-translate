"""Text style presets: the store, the pipeline half, the item half, the UI.

A preset sets only the fields it holds. Applied by hand it sets all of them;
as a per-text-class default for newly rendered text it sets only the look
(LOOK_FIELDS), because the renderer already fitted the text in its own font
and size.
"""

import json

import numpy as np
import pytest
from PySide6.QtGui import QColor, QUndoStack

from modules.utils.text_presets import (
    BUILTIN_PRESETS,
    LOOK_FIELDS,
    TextPreset,
    TextPresetStore,
    apply_to_state,
)


@pytest.fixture
def store(tmp_path):
    return TextPresetStore(str(tmp_path / "presets.json"))


class TestStore:
    def test_the_built_ins_come_first(self, store):
        assert store.names()[:5] == ["Speech", "Shout", "Thought", "Narration", "SFX"]
        assert all(p.builtin for p in BUILTIN_PRESETS)

    def test_a_user_preset_persists(self, store, tmp_path):
        store.put(TextPreset("Mine", color="#ff00ff00", bold=True))
        again = TextPresetStore(str(tmp_path / "presets.json"))
        mine = again.get("Mine")
        assert mine is not None and mine.color == "#ff00ff00" and mine.bold is True and not mine.builtin

    def test_built_in_names_are_refused_and_empty_names_too(self, store):
        with pytest.raises(ValueError):
            store.put(TextPreset("SFX"))
        with pytest.raises(ValueError):
            store.put(TextPreset("  "))

    def test_rename_and_delete(self, store):
        store.put(TextPreset("A", color="#000"))
        store.rename("A", "B")
        assert store.get("A") is None and store.get("B") is not None
        assert store.delete("B") is True
        assert store.delete("SFX") is False  # built-ins stay

    def test_a_damaged_file_leaves_the_built_ins(self, tmp_path):
        path = tmp_path / "presets.json"
        path.write_text("{not json", encoding="utf-8")
        assert TextPresetStore(str(path)).names() == [p.name for p in BUILTIN_PRESETS]

    def test_unknown_keys_survive_a_save(self, store, tmp_path):
        path = tmp_path / "presets.json"
        path.write_text(json.dumps({"presets": [{"name": "Future", "sparkle": 3}]}), encoding="utf-8")
        store.load()
        store.put(TextPreset("Other"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert {"name": "Future", "sparkle": 3} in saved["presets"]


class TestState:
    def state(self):
        return {"text_color": "#000000", "font_family": "Arial", "font_size": 20, "bold": False,
                "outline": True, "outline_color": "#ffffff", "outline_width": 1.0}

    def test_a_default_preset_sets_the_look_only(self):
        sfx = next(p for p in BUILTIN_PRESETS if p.name == "SFX")
        out = apply_to_state(self.state(), sfx)
        assert out["text_color"] == "#ff3b6b"
        assert out["stroke_layers"] and out["shadow_enabled"] is True
        assert (out["warp_style"], out["warp_bend"]) == ("arch", 0.4)
        # The fit is the renderer's: font, size and weight are untouched.
        assert out["font_family"] == "Arial" and out["font_size"] == 20 and out["bold"] is False

    def test_no_outline_clears_its_colour(self):
        narration = next(p for p in BUILTIN_PRESETS if p.name == "Narration")
        out = apply_to_state(self.state(), narration)
        assert out["outline"] is False and out["outline_color"] is None

    def test_no_preset_changes_nothing(self):
        assert apply_to_state(self.state(), None) == self.state()

    def test_look_fields_leave_out_anything_that_changes_size(self):
        assert not {"font_family", "font_size", "bold", "italic", "letter_spacing", "line_spacing"} & set(LOOK_FIELDS)


@pytest.fixture
def item(qapp):
    from app.ui.canvas.text_item import TextBlockItem

    block = TextBlockItem(text="STYLE", font_size=24, render_color=QColor(0, 0, 0))
    block.set_font("DejaVu Sans", 24)
    block.setTextWidth(200)
    yield block


class TestItem:
    def test_applying_sets_every_field_it_holds(self, item):
        from app.ui.canvas.text.presets import apply_preset_to_item

        sfx = next(p for p in BUILTIN_PRESETS if p.name == "SFX")
        apply_preset_to_item(item, sfx)
        assert item.text_color.name() == "#ff3b6b"
        assert item.bold is True
        assert [layer.width for layer in item.stroke_layers] == [5.0, 4.0]
        assert item.shadow_enabled and item.shadow_color.alpha() == 0x6E
        assert (item.warp_style, item.warp_bend) == ("arch", 0.4)
        assert item.outline and item.outline_width == 3.0

    def test_only_look_fields_keeps_the_font(self, item):
        from app.ui.canvas.text.presets import apply_preset_to_item

        shout = next(p for p in BUILTIN_PRESETS if p.name == "Shout")
        apply_preset_to_item(item, shout, LOOK_FIELDS)
        assert item.bold is False  # weight is not a look field
        assert item.warp_style == "bulge"

    def test_capture_then_apply_gives_the_same_look(self, qapp, item):
        from app.ui.canvas.text.presets import apply_preset_to_item, preset_from_item
        from app.ui.canvas.text_item import TextBlockItem

        item.set_color(QColor(20, 120, 220))
        item.set_outline(QColor(255, 255, 0), 2.5)
        item.set_stroke_layers([("#ff000000", 4)])
        item.set_shadow(True, QColor(0, 0, 0, 90), (3, 5), 2)
        item.set_warp("wave", 0.5)
        preset = preset_from_item(item, "Captured")
        assert preset.font_size is None  # sizes are the fitter's business

        other = TextBlockItem(text="OTHER", font_size=18, render_color=QColor(0, 0, 0))
        apply_preset_to_item(other, preset)
        for attribute in ("bold", "italic", "outline", "outline_width", "stroke_layers",
                          "shadow_enabled", "shadow_offset", "shadow_blur", "warp_style", "warp_bend"):
            assert getattr(other, attribute) == getattr(item, attribute), attribute
        assert other.text_color.name() == item.text_color.name()
        assert other.shadow_color.alpha() == item.shadow_color.alpha()


class TestInTheWindow:
    @pytest.fixture
    def window(self, qapp, monkeypatch, tmp_path):
        import controller as controller_mod

        monkeypatch.setattr(
            "modules.utils.text_presets.get_user_data_dir", lambda *a, **k: str(tmp_path)
        )
        win = controller_mod.ComicTranslate()
        win.undo_group.setActiveStack(QUndoStack(win.undo_group))
        yield win
        win._skip_close_prompt = True
        win.close()

    def test_the_style_list_and_both_default_combos_list_the_built_ins(self, window):
        combo = window.style_preset_combo
        names = [combo.itemData(i) for i in range(combo.count())]
        assert names[1:6] == ["Speech", "Shout", "Thought", "Narration", "SFX"]
        ui = window.settings_page.ui
        assert ui.default_bubble_preset_combo.findData("SFX") > 0
        assert ui.default_free_preset_combo.itemData(0) == ""

    def test_applying_a_style_to_two_items_is_one_undo_step(self, window):
        from app.ui.canvas.text_item import TextBlockItem

        items = []
        for x in (0, 300):
            block = TextBlockItem(text="HI", font_size=20, render_color=QColor(0, 0, 0))
            window.image_viewer._scene.addItem(block)
            block.setPos(x, 0)
            block.setSelected(True)
            items.append(block)
        window.curr_tblock_item = items[0]
        combo = window.style_preset_combo
        combo.setCurrentIndex(combo.findData("SFX"))
        window.text_ctrl.apply_style_preset()
        assert all(b.stroke_layers and b.warp_style == "arch" for b in items)
        window.undo_group.activeStack().undo()
        assert all(not b.stroke_layers and b.warp_style == "" for b in items)

    def test_save_and_delete_a_style(self, window, monkeypatch):
        from PySide6 import QtWidgets
        from app.ui.canvas.text_item import TextBlockItem

        block = TextBlockItem(text="MINE", font_size=20, render_color=QColor(10, 200, 30))
        window.image_viewer._scene.addItem(block)
        window.curr_tblock_item = block
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("Green", True))
        answers = []
        monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                            lambda *a, **k: answers.pop(0) if answers else QtWidgets.QMessageBox.StandardButton.No)
        window.text_ctrl.save_style_preset()
        combo = window.style_preset_combo
        assert combo.currentData() == "Green"
        assert window.settings_page.ui.default_free_preset_combo.findData("Green") > 0

        # Saving over it asks first; "No" keeps the old look.
        block.set_color(QColor(200, 0, 0))
        window.text_ctrl.save_style_preset()
        assert window.text_ctrl.preset_store.get("Green").color == "#ff0ac81e"

        window.text_ctrl.delete_style_preset()  # declined
        assert combo.findData("Green") > 0
        answers.append(QtWidgets.QMessageBox.StandardButton.Yes)
        window.text_ctrl.delete_style_preset()
        assert combo.findData("Green") == -1

    def test_a_built_in_name_is_refused_with_a_message(self, window, monkeypatch):
        from PySide6 import QtWidgets
        from app.ui.canvas.text_item import TextBlockItem

        block = TextBlockItem(text="X", font_size=20, render_color=QColor(0, 0, 0))
        window.image_viewer._scene.addItem(block)
        window.curr_tblock_item = block
        warned = []
        monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **k: ("SFX", True))
        monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
        window.text_ctrl.save_style_preset()
        assert warned and "SFX" in warned[0]
        assert [p.name for p in window.text_ctrl.preset_store.presets() if not p.builtin] == []

    def test_the_default_style_reaches_newly_rendered_text(self, window):
        from modules.utils.textblock import TextBlock

        ui = window.settings_page.ui
        ui.default_free_preset_combo.setCurrentIndex(ui.default_free_preset_combo.findData("SFX"))
        settings = window.render_settings()
        assert settings.default_presets["text_free"].name == "SFX"
        assert "text_bubble" not in settings.default_presets

        page = window.image_viewer
        page.display_image_array(np.full((300, 400, 3), 255, np.uint8))
        window.image_files = ["page.png"]
        window.curr_img_idx = 0
        blk = TextBlock(text_bbox=np.array([20, 20, 220, 90]))
        blk.text_class = "text_free"
        blk.translation = "BOOM"
        window.text_ctrl.on_blk_rendered("BOOM", 24, blk, "page.png")
        rendered = page.text_items[-1]
        assert rendered.stroke_layers and rendered.warp_style == "arch"
        assert rendered.text_color.name() == "#ff3b6b"
