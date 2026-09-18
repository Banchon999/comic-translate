"""A small, shared store of the user's favourite fonts.

The font picker lists every font on the system, and scrolling that to reach the
three or four a translator actually uses is the friction this removes. A
favourite is just a family name; the store persists them in the same
``QSettings("ComicLabs", "ComicTranslate")`` the rest of the app uses and
notifies every widget when the set changes, so the toolbar star and the
favourites menu never disagree.

Pinning favourites *into* the ``QFontComboBox`` model was tried and rejected:
``QFontComboBox.setCurrentFont`` rebuilds its model from the font database, and
``tools.set_font`` calls it on every selection and page load, so inserted rows
vanish the moment the font changes. A separate control that reads this store is
the robust equivalent.
"""

from __future__ import annotations

from PySide6 import QtCore

_SETTINGS_KEY = "text_rendering/favourite_fonts"


class FontFavourites(QtCore.QObject):
    """The favourite font families, backed by QSettings.

    ``changed`` fires on every mutation so all pickers refresh together. Order
    is preserved (most-recently-added last), which is the order the menu shows.
    """

    changed = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._families: list[str] = self._load()

    # -- persistence -------------------------------------------------------
    def _settings(self) -> QtCore.QSettings:
        return QtCore.QSettings("ComicLabs", "ComicTranslate")

    def _load(self) -> list[str]:
        raw = self._settings().value(_SETTINGS_KEY, [])
        # QSettings hands back a str for a one-element list, None when unset,
        # and a list otherwise — normalise all three, dropping blanks/dupes.
        if raw is None:
            items = []
        elif isinstance(raw, str):
            items = [raw]
        else:
            items = list(raw)
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            name = str(item).strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _save(self) -> None:
        self._settings().setValue(_SETTINGS_KEY, list(self._families))

    # -- queries -----------------------------------------------------------
    def families(self) -> list[str]:
        return list(self._families)

    def is_favourite(self, family: str) -> bool:
        return bool(family) and family.strip() in self._families

    # -- mutations ---------------------------------------------------------
    def add(self, family: str) -> None:
        name = (family or "").strip()
        if not name or name in self._families:
            return
        self._families.append(name)
        self._save()
        self.changed.emit()

    def remove(self, family: str) -> None:
        name = (family or "").strip()
        if name not in self._families:
            return
        self._families.remove(name)
        self._save()
        self.changed.emit()

    def toggle(self, family: str) -> bool:
        """Add the family if absent, remove it if present. Returns the new state."""
        name = (family or "").strip()
        if not name:
            return False
        if name in self._families:
            self.remove(name)
            return False
        self.add(name)
        return True


_instance: FontFavourites | None = None


def favourites() -> FontFavourites:
    """The process-wide store. Needs a QApplication (it is a QObject)."""
    global _instance
    if _instance is None:
        _instance = FontFavourites()
    return _instance
