"""PageStateStore: the Slice 1 access boundary must behave exactly like the
plain dict it replaces (live references, nested in-place mutation, ordering)
while offering a semantic API that returns the same live objects.
"""

from app.projects.page_state_store import PageStateStore


# --- dict compatibility ------------------------------------------------------

def test_behaves_as_a_dict():
    s = PageStateStore()
    assert isinstance(s, dict)
    s["a"] = {"x": 1}
    assert s["a"] == {"x": 1}
    assert "a" in s
    assert s.get("a") == {"x": 1}
    assert s.get("missing") is None
    assert s.get("missing", 7) == 7
    assert list(s.keys()) == ["a"]
    assert list(s.values()) == [{"x": 1}]
    assert list(s.items()) == [("a", {"x": 1})]


def test_setdefault_pop_clear_and_order():
    s = PageStateStore()
    # insertion order preserved, like a normal dict
    for k in ("p3", "p1", "p2"):
        s[k] = {}
    assert list(s) == ["p3", "p1", "p2"]

    d = s.setdefault("p1", {"new": True})
    assert d == {}  # existing kept, default ignored
    assert s.pop("p3", None) == {}
    assert "p3" not in s
    s.clear()
    assert len(s) == 0


def test_construction_from_mapping_keeps_type():
    s = PageStateStore({"a": {"x": 1}})
    assert isinstance(s, PageStateStore)
    assert s["a"] == {"x": 1}


# --- live-reference semantics (non-negotiable) -------------------------------

def test_get_page_state_returns_live_reference():
    s = PageStateStore({"p": {"blk_list": []}})
    state = s.get_page_state("p")
    state["blk_list"].append("BLK")
    state["source_lang"] = "en"
    # mutations landed on the backing store, not a copy
    assert s["p"]["blk_list"] == ["BLK"]
    assert s["p"]["source_lang"] == "en"


def test_get_page_state_default():
    s = PageStateStore()
    assert s.get_page_state("missing") is None
    assert s.get_page_state("missing", {}) == {}


def test_ensure_page_is_idempotent_and_never_overwrites():
    s = PageStateStore()
    first = s.ensure_page("p")
    first["source_lang"] = "en"
    second = s.ensure_page("p")
    assert second is first  # same live object
    assert second["source_lang"] == "en"  # not overwritten


def test_set_and_remove_page():
    s = PageStateStore()
    s.set_page_state("p", {"skip": True})
    assert s["p"] == {"skip": True}
    assert s.remove_page("p") == {"skip": True}
    assert "p" not in s
    assert s.remove_page("gone") is None


def test_viewer_state_live_and_ensure():
    s = PageStateStore({"p": {"viewer_state": {"rectangles": []}}})
    vs = s.viewer_state("p")
    vs["rectangles"].append("R")
    assert s["p"]["viewer_state"]["rectangles"] == ["R"]
    assert s.viewer_state("missing") is None

    ensured = s.ensure_viewer_state("fresh")
    ensured["text_items_state"] = [1]
    assert s["fresh"]["viewer_state"]["text_items_state"] == [1]


def test_blk_list_live_read_and_set():
    s = PageStateStore({"p": {"blk_list": ["A"]}})
    live = s.blk_list("p")
    live.append("B")
    assert s["p"]["blk_list"] == ["A", "B"]
    # missing page -> throwaway empty, not stored
    assert s.blk_list("missing") == []
    assert "missing" not in s
    # set_blk_list persists and creates the page if needed
    s.set_blk_list("new", ["X"])
    assert s["new"]["blk_list"] == ["X"]


def test_is_skipped_and_set_skip():
    s = PageStateStore({"a": {"skip": True}, "b": {"skip": False}, "c": {}})
    assert s.is_skipped("a") is True
    assert s.is_skipped("b") is False
    assert s.is_skipped("c") is False
    assert s.is_skipped("missing") is False
    s.set_skip("b", True)
    assert s.is_skipped("b") is True
    s.set_skip("new", True)  # creates the page
    assert s.is_skipped("new") is True


def test_languages_get_and_set():
    s = PageStateStore({"p": {"source_lang": "Korean", "target_lang": "English"}})
    assert s.languages("p") == ("Korean", "English")
    assert s.languages("missing") == (None, None)
    s.set_languages("new", "Japanese", "English")
    assert s["new"]["source_lang"] == "Japanese"
    assert s["new"]["target_lang"] == "English"


# --- canonical creation ------------------------------------------------------

def test_build_page_state_defaults_and_shape():
    state = PageStateStore.build_page_state(
        viewer_state={"rectangles": []},
        source_lang="Korean",
        target_lang="English",
        brush_strokes=[],
        blk_list=[],
        skip=False,
        export_group_name="chapter1",
    )
    assert state == {
        "viewer_state": {"rectangles": []},
        "source_lang": "Korean",
        "target_lang": "English",
        "brush_strokes": [],
        "blk_list": [],
        "skip": False,
        "export_group_name": "chapter1",
    }


def test_build_page_state_preserves_existing_conditional_fields():
    # A batch-only field like skip_render must survive rebuild rather than being
    # dropped or made globally present.
    existing = {"skip_render": True, "export_group_name": "old"}
    state = PageStateStore.build_page_state(
        viewer_state={}, source_lang="", target_lang="",
        brush_strokes=[], blk_list=[], skip=False,
        export_group_name="new", existing=existing,
    )
    assert state["skip_render"] is True          # carried over
    assert state["export_group_name"] == "new"   # overwritten by explicit value
    # and a page built without it does not gain the field
    fresh = PageStateStore.build_page_state(
        viewer_state={}, source_lang="", target_lang="",
        brush_strokes=[], blk_list=[], skip=False, export_group_name="x",
    )
    assert "skip_render" not in fresh
