"""Toon Studio's name and assets — and the old names that must not change.

Users see "Toon Studio". What stores their data keeps the ComicTranslate
names: renaming QSettings' organisation/application or the user data folder
would not migrate anything, it would silently start every user from empty
settings, glossaries and models. These tests make that rename a deliberate,
visible act instead of an accident of search-and-replace.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ICONS = REPO / "resources" / "icons"


def test_settings_keep_their_storage_identity():
    for path in (REPO / "app").rglob("*.py"):
        if "dayu_widgets" in path.parts:
            continue
        for call in re.findall(r"QSettings\(([^)]*)\)", path.read_text(encoding="utf-8")):
            if call.strip():
                assert re.sub(r"\s", "", call).replace("'", '"') == '"ComicLabs","ComicTranslate"', (path, call)


def test_the_user_data_folder_keeps_its_name():
    from modules.utils.paths import get_user_data_dir

    assert get_user_data_dir().rstrip("/\\").endswith("ComicTranslate")


def test_projects_keep_their_extension_and_instance_key():
    comic = (REPO / "comic.py").read_text(encoding="utf-8")
    assert 'f"ComicTranslate-{digest}"' in comic
    assert "ComicLabs.ComicTranslate" in comic
    assert ".ctpr" in (REPO / "app" / "controllers" / "projects.py").read_text(encoding="utf-8")


def test_the_window_is_titled_toon_studio(qapp):
    from core.theme_tokens import BRAND_NAME

    assert BRAND_NAME == "Toon Studio"
    window = (REPO / "app" / "ui" / "main_window" / "window.py").read_text(encoding="utf-8")
    assert 'self.setWindowTitle(f"{theme_tokens.BRAND_NAME}[*]")' in window
    assert "Comic Translate[*]" not in window


def test_every_build_names_the_executable_toon_studio():
    for name in ("build-linux.yml", "build-windows.yml", "build-windows-full.yml", "main full.yml", "build-macos-dmg.yml"):
        text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "--name ToonStudio" in text, name
        assert "ComicTranslate" not in text, name


def test_the_icon_carries_every_size_windows_asks_for():
    from PIL import Image

    sizes = Image.open(ICONS / "icon.ico").ico.sizes()
    for size in (16, 24, 32, 48, 256):
        assert (size, size) in sizes


def test_the_icns_and_splash_open():
    from PIL import Image

    assert Image.open(ICONS / "icon.icns").size[0] >= 512
    splash = Image.open(ICONS / "splash.png")
    assert splash.size == (1200, 675)


def test_the_mark_file_matches_the_generator():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import build_brand
    finally:
        sys.path.pop(0)
    assert (ICONS / "toon-mark.svg").read_text(encoding="utf-8") == build_brand.mark_svg()


def test_the_icon_is_drawn_in_the_accent(qapp):
    """A 16 px frame still shows the mark: plenty of accent-coloured pixels."""
    from PIL import Image

    from core.theme_tokens import DARK

    accent = tuple(int(DARK["accent"][i:i + 2], 16) for i in (1, 3, 5))
    ico = Image.open(ICONS / "icon.ico")
    ico.size = (16, 16)
    frame = ico.convert("RGB")
    raw = frame.tobytes()
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    near = [p for p in pixels if sum(abs(a - b) for a, b in zip(p, accent)) < 60]
    assert len(near) >= 40, len(near)


def test_the_display_font_ships_with_its_licence():
    folder = REPO / "resources" / "fonts" / "ui"
    assert (folder / "BricolageGrotesque[opsz,wdth,wght].ttf").is_file()
    assert "SIL Open Font License" in (folder / "OFL-BricolageGrotesque.txt").read_text(encoding="utf-8")
