"""Turning Skia on for profiles that predate it being the default.

Changing the fallback passed to `QSettings.value` does not, on its own, reach
anybody who already uses the app. `skia_text_engine` is part of
`get_all_settings()`, which autosaves about a second after any settings change,
so every existing user has an explicit `False` stored and the new fallback is
never consulted for them — the flip would silently apply to new installs only.

`SettingsPage._migrate_skia_default` closes that gap exactly once, and these
tests pin both halves: it must reach an existing profile, and it must not
override a user who has since turned Skia off.

`QSettings` is pointed at a temp file rather than the real store; `conftest.py`
already redirects the XDG variables, but being explicit keeps the test honest
about what it writes.
"""

import pytest

from PySide6 import QtCore

from app.ui.settings.settings_page import SettingsPage

KEY = SettingsPage.SKIA_DEFAULT_MIGRATION_KEY


@pytest.fixture
def settings(tmp_path):
    store = QtCore.QSettings(
        str(tmp_path / "ct.ini"), QtCore.QSettings.Format.IniFormat
    )
    yield store
    store.sync()


def _migrate(settings):
    """The migration alone, without constructing the whole settings dialog."""
    SettingsPage._migrate_skia_default(settings)


def test_an_existing_profile_with_skia_off_is_flipped_on(settings):
    """The case the whole migration exists for: an upgrading user."""
    settings.setValue('skia_text_engine', False)

    _migrate(settings)

    assert settings.value('skia_text_engine', type=bool) is True
    assert settings.value(KEY, type=bool) is True


def test_a_fresh_profile_is_marked_so_it_is_not_migrated_twice(settings):
    _migrate(settings)
    assert settings.value('skia_text_engine', type=bool) is True
    assert settings.value(KEY, type=bool) is True


def test_a_user_who_turned_skia_off_afterwards_stays_off(settings):
    """The migration is one-shot, not a preference that keeps reasserting itself."""
    _migrate(settings)
    settings.setValue('skia_text_engine', False)      # the user opts out

    _migrate(settings)                                 # next launch

    assert settings.value('skia_text_engine', type=bool) is False, (
        "the migration overrode a user who had explicitly turned Skia off"
    )
