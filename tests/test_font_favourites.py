"""The favourite-fonts store: the persistence and de-duplication behind the
toolbar star and the favourites menu.

The store is what the UI reads; the toolbar wiring itself was verified by
building the main window offscreen (see the PR), because nothing in the suite
constructs the whole `ComicTranslate` window. Here we pin the logic that would
silently corrupt the favourites list: QSettings handing a lone entry back as a
bare string, duplicates, blanks, and persistence across instances.

`conftest.py` repoints the config dir at a temp location, so these never touch
the real user's settings.
"""

import pytest
from PySide6 import QtCore

from app.ui.font_favourites import FontFavourites, _SETTINGS_KEY


@pytest.fixture(autouse=True)
def clear_favourites(qapp):
    """Each test starts with an empty store, isolated from the others."""
    QtCore.QSettings("ComicLabs", "ComicTranslate").remove(_SETTINGS_KEY)
    yield
    QtCore.QSettings("ComicLabs", "ComicTranslate").remove(_SETTINGS_KEY)


def test_toggle_adds_then_removes(qapp):
    store = FontFavourites()
    assert store.toggle("Arial") is True
    assert store.is_favourite("Arial")
    assert store.families() == ["Arial"]
    assert store.toggle("Arial") is False
    assert not store.is_favourite("Arial")
    assert store.families() == []


def test_favourites_persist_across_instances(qapp):
    FontFavourites().add("Comic Sans MS")
    # A brand-new store reads the same QSettings the app uses at next launch.
    assert FontFavourites().is_favourite("Comic Sans MS")


def test_a_single_favourite_survives_the_qsettings_string_quirk(qapp):
    """QSettings hands a one-element list back as a bare str, not a list.

    Loaded naively that turns "Arial" into ['A','r','i','a','l']. The store
    normalises it, so this is the guard for that.
    """
    FontFavourites().add("Arial")
    reloaded = FontFavourites()
    assert reloaded.families() == ["Arial"]


def test_no_duplicates(qapp):
    store = FontFavourites()
    store.add("Arial")
    store.add("Arial")
    assert store.families() == ["Arial"]


def test_blanks_are_ignored(qapp):
    store = FontFavourites()
    store.add("")
    store.add("   ")
    assert store.families() == []
    assert store.toggle("  ") is False


def test_order_is_preserved(qapp):
    store = FontFavourites()
    for family in ("Arial", "Tahoma", "Verdana"):
        store.add(family)
    assert store.families() == ["Arial", "Tahoma", "Verdana"]


def test_changed_fires_on_mutation_only(qapp):
    store = FontFavourites()
    seen = []
    store.changed.connect(lambda: seen.append(1))
    store.add("Arial")
    store.add("Arial")      # duplicate — no change, no signal
    store.remove("Nope")    # absent — no change, no signal
    store.remove("Arial")
    assert len(seen) == 2   # one add, one remove
